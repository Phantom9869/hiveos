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

# Groq retires model names periodically. If this one starts returning a 404
# with `model_not_found`, it is a one-variable change and nothing else moves.
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# Per-call output cap — the same spend guard the Bedrock plan specified.
MAX_TOKENS = int(os.environ.get("MAX_TOKENS_PER_CALL", "1024"))

# Shorter than the Lambda's 60s timeout and the queue's visibility window, so a
# hung provider surfaces as a fallback rather than as a redelivered task.
TIMEOUT_SECONDS = 20

# Low but not zero: the demo asks the same question across takes and a wildly
# different answer each time reads as instability on camera.
TEMPERATURE = 0.3

SYSTEM_PROMPT = (
    "You are a shared team agent running inside HiveOS, a workspace where an "
    "entire team draws on one pooled AI token budget. Answer in at most three "
    "short sentences. Every token you spend comes out of the team's shared "
    "quota, so be brief and concrete."
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
