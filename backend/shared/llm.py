"""The model call. One provider, one function, no SDK.

Amazon Bedrock is blocked account-wide on this AWS account — not by anything
here, and not by a fixable setting. Three regions (`us-east-1`, `us-west-2`,
`ap-south-1`), both first-party and Marketplace models, all refuse:
`INVALID_PAYMENT_INSTRUMENT` for Anthropic/AI21, and a hard zero per-day token
quota (`adjustable=False`) for Amazon's own Nova. See PROGRESS.md. Inference
therefore calls out to Groq; **every other component stays on AWS.**

That split is worth stating plainly rather than hiding: a governance layer that
only works with one vendor's models is a worse governance layer. The scheduler
does not care where a token was spent, only that it was counted.

Deliberately built on `urllib` from the standard library:

  - no new dependency, so `sam build` has nothing extra to package
  - no wheel to resolve, which matters because local Python is 3.14 and any
    compiled dependency built off-container would be the wrong platform
  - the whole client is one POST and one JSON parse

The API key lives in SSM Parameter Store as a SecureString, never in
`template.yaml`, `samconfig.toml`, an environment variable, or this repository.
It is fetched once per cold start and cached for the life of the container.
"""

import json
import os
import urllib.error
import urllib.request

import boto3

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Name of the SSM SecureString holding the key. The *name* is not a secret.
KEY_PARAM_NAME = os.environ.get("GROQ_KEY_PARAM", "/hiveos/groq-api-key")

# Groq retires model names periodically — the Llama 3.3 name this was first
# written against was already gone by the time it was deployed. If this starts
# returning 404 `model_not_found`, list the current ids with
# `GET https://api.groq.com/openai/v1/models`; it is a one-variable change.
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# Per-call output cap — the same spend guard the Bedrock plan specified.
MAX_TOKENS = int(os.environ.get("MAX_TOKENS_PER_CALL", "1024"))

# Shorter than the Lambda's 60s timeout and the queue's visibility window, so a
# hung provider surfaces as a fallback rather than as a redelivered task.
TIMEOUT_SECONDS = 20

# Identifies this client to the provider's edge. See the note in `complete`:
# the stdlib default is blocked by Cloudflare, so this is load-bearing.
USER_AGENT = "HiveOS/1.0 (+https://github.com/arunishrajput/hiveos)"

# Low but not zero: the demo asks the same question across takes and a wildly
# different answer each time reads as instability on camera.
TEMPERATURE = 0.3

# Brevity is a product constraint, not a style preference: every token spent
# here comes out of a quota the whole team shares, and the response renders in
# a narrow panel beside three other people's. Stated in the imperative and
# repeated, because a single polite "be brief" is reliably ignored the moment a
# prompt looks like it wants code.
SYSTEM_PROMPT = (
    "You are a shared team agent running inside HiveOS, a workspace where an "
    "entire team draws on one pooled AI token budget.\n"
    "Rules, in order of importance:\n"
    "1. Answer in at most three short sentences. Never exceed this.\n"
    "2. Do not include code blocks, bullet lists, or headings. Prose only.\n"
    "3. If a question invites a long answer, give the shortest useful one and "
    "stop.\n"
    "Every token you spend is drawn from the team's shared quota, so brevity "
    "is the job, not a preference."
)

_key_cache = None


def _api_key():
    """The Groq key from SSM, fetched once per container.

    Cached at module scope: a warm Lambda serves many tasks and re-reading the
    parameter on every one would add a round trip to each task for nothing.
    """
    global _key_cache
    if _key_cache is None:
        _key_cache = boto3.client("ssm").get_parameter(
            Name=KEY_PARAM_NAME,
            WithDecryption=True,
        )["Parameter"]["Value"]
    return _key_cache


def build_system_prompt(context, fact=None):
    """The system prompt: who the agent is, plus what the team already knows.

    `context` is the team's shared memory. Loading it *before* the call is the
    product claim — a queued user's agent knows the team's facts the moment its
    turn starts, without anyone repeating them.
    """
    parts = [SYSTEM_PROMPT]
    if context:
        parts.append(context)
    if fact:
        parts.append(
            f"You have just saved this fact for the team: "
            f"{fact['key']} = {fact['val']}. Confirm it in one sentence and "
            "say that every future task will load it automatically."
        )
    return "\n\n".join(parts)


def complete(prompt, system):
    """Call the model. Returns (text, tokens) with **real** reported usage.

    Raises on any failure — transport, HTTP status, or a response missing the
    fields we need. The caller owns the fallback, because only the caller knows
    what a degraded answer should say.
    """
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        GROQ_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            # Not decoration. The endpoint sits behind Cloudflare, which bans
            # urllib's default `Python-urllib/3.13` signature outright and
            # answers HTTP 403 `error code: 1010` — which looks exactly like a
            # bad API key and is not one. Any honest UA gets through.
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        # The body carries the actual reason (bad model name, revoked key,
        # rate limit). Surfacing it is the difference between a five-minute
        # fix and an hour of guessing — and it never contains the key, which
        # rides in the request headers only.
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"groq HTTP {exc.code}: {detail}") from exc

    text = (payload["choices"][0]["message"]["content"] or "").strip()

    # Groq reports usage the same way OpenAI does. This is the whole reason the
    # swap is worth doing: `total_tokens` is what the provider actually
    # counted, so the meter stops being a heuristic.
    usage = payload.get("usage") or {}
    tokens = int(usage.get("total_tokens") or 0)
    if not text or tokens <= 0:
        raise RuntimeError(f"groq returned no usable completion: usage={usage}")

    return text, tokens
