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
    "You have tools. Use set_team_memory whenever someone asks you to "
    "remember, note or record something for the team — it is shared, so every "
    "teammate's later tasks will know it. Use get_task_context for questions "
    "about what the team has been doing or where the budget went.\n"
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


def build_system_prompt(context):
    """The system prompt: who the agent is, plus what the team already knows.

    `context` is the team's shared memory. Loading it *before* the call is the
    product claim — a queued user's agent knows the team's facts the moment its
    turn starts, without anyone repeating them.

    It no longer takes a saved fact. That argument existed to tell the model
    about a save the *runner* had already performed by regex; the model now
    makes that decision itself and hears the outcome as a tool result, which is
    where it belongs.
    """
    parts = [SYSTEM_PROMPT]
    if context:
        parts.append(context)
    return "\n\n".join(parts)


# --- Tools -----------------------------------------------------------------

# What the agent may decide to do, as opposed to what is done *for* it.
#
# `get_team_memory` is deliberately absent. The team's facts are loaded into
# the system prompt before the call, because "a queued user's agent already
# knows the team's facts the moment its turn starts" is a product claim
# (CONTRACT.md) — making it a tool would make it conditional on the model
# choosing to ask, and a model that forgot to ask would silently break the
# demo's strongest beat.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_team_memory",
            "description": (
                "Save a fact for the whole team. Everyone's future agent tasks "
                "will load it automatically. Use this whenever the user asks "
                "you to remember, note or record something for the team."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Short name for the fact, e.g. 'deploy window'.",
                    },
                    "value": {
                        "type": "string",
                        "description": "The fact itself, e.g. 'Friday 16:00 UTC'.",
                    },
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_context",
            "description": (
                "Look at what the team has recently asked agents to do, and "
                "what each task cost. Use this for questions about what the "
                "team has been working on or where the token budget went."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# One round of tools, not a loop until the model is satisfied. Each round is a
# full round trip inside a Lambda that holds an agent slot the whole time, and
# an unbounded loop is an unbounded bill. One round is enough for "save this
# and tell me you did".
MAX_TOOL_ROUNDS = 1


def _post(payload):
    """One POST to the provider. Returns the decoded body."""
    request = urllib.request.Request(
        GROQ_URL,
        data=json.dumps(payload).encode("utf-8"),
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
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # The body carries the actual reason (bad model name, revoked key,
        # rate limit). Surfacing it is the difference between a five-minute fix
        # and an hour of guessing — and it never contains the key, which rides
        # in the request headers only.
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"groq HTTP {exc.code}: {detail}") from exc


def _usage(payload):
    return int((payload.get("usage") or {}).get("total_tokens") or 0)


def complete(prompt, system, run_tool=None):
    """Call the model, letting it use tools. Returns (text, tokens, called).

    `run_tool(name, args) -> str` executes one tool and returns what the model
    should be told about it. `called` is the list of tool names the model
    actually chose, which the caller needs because a fact saved by the *model*
    and a fact saved by a regex are different claims and only one of them is
    "the agent decided to".

    **Tokens are the sum across every round.** A tool call costs two requests,
    and charging the team for one of them would under-report spend on the one
    product whose entire subject is spend.

    Raises on any failure — transport, HTTP status, or a response missing the
    fields we need. The caller owns the fallback, because only the caller knows
    what a degraded answer should say.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    request = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "messages": messages,
    }
    if run_tool:
        request["tools"] = TOOLS
        request["tool_choice"] = "auto"

    tokens = 0
    called = []

    for _ in range(MAX_TOOL_ROUNDS + 1):
        payload = _post({**request, "messages": messages})
        tokens += _usage(payload)
        choice = payload["choices"][0]["message"]
        calls = choice.get("tool_calls") or []

        if not calls or not run_tool:
            text = (choice.get("content") or "").strip()
            if not text or tokens <= 0:
                raise RuntimeError(f"groq returned no usable completion: {payload.get('usage')}")
            return text, tokens, called

        # The assistant turn that *requested* the tools has to go back verbatim,
        # or the tool results below have nothing to attach to.
        messages.append(choice)
        for call in calls:
            name = call.get("function", {}).get("name", "")
            try:
                args = json.loads(call.get("function", {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            print(f"[llm] model called {name}({args})")
            called.append(name)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": run_tool(name, args),
                }
            )

        # Second round answers in prose; offering the tools again would invite
        # it to call them forever.
        request.pop("tools", None)
        request.pop("tool_choice", None)

    raise RuntimeError("groq kept asking for tools past the round limit")
