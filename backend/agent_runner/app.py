"""HiveOS Agent Runner — one SQS message is one agent task on one slot.

The agent is **real**: `shared/llm.py` calls a model and reports the token
usage the provider actually counted. Amazon Bedrock is blocked account-wide on
this AWS account (three regions, both first-party and Marketplace models — see
PROGRESS.md), so inference calls out to Groq. Every other component — the
queue, the scheduler, the state, the real-time layer, the hosting — is AWS.

Everything around the model is what the product actually claims:

  - team memory is loaded before the task runs, and saved facts broadcast
  - `tokens_used` is incremented atomically and broadcast to every client
  - the budget ceiling is **enforced** — the agent is not invoked at 100%

Three invariants live here:

1. **The slot is released even when the task fails.** A leaked slot deadlocks
   the workspace, and on a recording that is indistinguishable from the
   product being broken. Hence try/finally, always.
2. **A token count is only reported as real when a provider counted it.** If
   the model call fails we still answer, from `_stub_agent`, and that result
   sets `estimated` — which is sticky on the total (`state.add_tokens`), so a
   degraded call can never be laundered into a billed-looking meter.
3. **A task holds its slot for a visible minimum.** The scheduler is the
   product; a 400 ms call would make BUSY and a queue position illegible.
"""

import json
import time
import traceback
from collections import namedtuple

from shared import broadcast, llm, memory, scheduler, state

# The floor on how long a task occupies its slot. This pads the *slot*, not the
# model: the queue mechanic is the thing being demonstrated, and a sub-second
# response would flash BUSY and free again before either was readable. Sized so
# a three-deep queue still drains well inside a 3-minute demo.
MIN_SLOT_SECONDS = 4.0

# Fault injection for the smoke test's no-slot-leak check. The release
# invariant is the one thing that must never silently regress, so it stays
# verifiable against deployed AWS rather than only in a local test.
FAIL_SENTINEL = "__hiveos_fail__"

# The standard rough heuristic for English. Used *only* on the fallback path,
# where no provider counted anything; a real response reports usage directly
# and `estimated` becomes False.
CHARS_PER_TOKEN = 4

AgentResult = namedtuple("AgentResult", "text tokens estimated")


def lambda_handler(event, context):
    for record in event.get("Records", []):
        # A body that will not parse cannot be acted on at all — let it raise
        # and land in the DLQ rather than pretending it succeeded.
        _handle(json.loads(record["body"]))
    return {"ok": True}


def _handle(task):
    slot_id = task["slot_id"]
    user_id = task.get("user_id", "unknown")
    print(
        f"[runner] start slot={slot_id} user={user_id} "
        f"conn={task.get('connection_id')}"
    )

    try:
        # Returning here still runs `finally`, so a refused task releases its
        # slot exactly like a completed one.
        if _refuse_over_budget(task):
            return
        _reply(task, _run_agent(task))
    except Exception:
        # The task failed, not the infrastructure. Tell the user, then fall
        # through to finally — swallowing it here is what stops SQS from
        # redelivering a task we have already accounted for.
        traceback.print_exc()
        _reply_error(task, "agent task failed")
    finally:
        # Deliberately NOT wrapped: if the release itself fails, the slot is
        # leaked, and an SQS redelivery is the only thing that can still fix
        # it. Better a duplicate response than a deadlocked workspace.
        scheduler.release_and_dispatch(slot_id)

    print(f"[runner] done slot={slot_id} user={user_id}")


# --- The budget ceiling ----------------------------------------------------


def _refuse_over_budget(task):
    """True if the quota is spent and the agent must not be invoked.

    This is the control the product is built around: a real refusal, not a
    gauge (PRD.md, ARCHITECTURE.md decision 4). It is also the spend guard —
    never disable it to make a demo work.

    Checked *here*, not at claim time, on purpose. A task can sit in the queue
    while the tasks ahead of it burn what was left, so the only honest moment
    to decide is immediately before the model would be called.
    """
    used, budget = state.budget_state()
    if not budget or used < budget:
        return False

    print(f"[runner] REFUSED — over budget used={used} budget={budget}")
    broadcast.broadcast_to_team(
        {
            "event": "budget_exhausted",
            "tokens_used": used,
            "token_budget": budget,
        }
    )
    _reply_error(task, "team token quota reached — the agent was not invoked")
    return True


# --- The agent -------------------------------------------------------------


def _estimate_tokens(*texts):
    """A truthful estimate of what these strings would cost a model.

    **Not** a Bedrock usage figure — there is no model call. It is
    `len(text) / 4` over the real prompt, the real memory context and the real
    response, which is the standard rough heuristic. Every frame that carries
    it sets `estimated: true`.
    """
    total = sum(len(text or "") for text in texts)
    return max(1, total // CHARS_PER_TOKEN)


def _run_agent(task):
    """Run one task: load the team's memory, call the model, hold the slot.

    The model call is the only part that can fail in a way the user should
    still get an answer from, so it is the only part wrapped. Everything
    around it — the memory write, the broadcast, the accounting — is the real
    mechanism and runs whether or not the provider is reachable.
    """
    prompt = task.get("prompt", "")

    if FAIL_SENTINEL in prompt:
        raise RuntimeError(f"fault injection via {FAIL_SENTINEL}")

    agent_type = task.get("agent_type") or task["slot_id"]
    requester = task.get("user_id", "unknown")
    started = time.monotonic()

    # Before the work, not during it: a queued user's agent must already know
    # the team's facts the moment its turn starts (CONTRACT.md).
    context = memory.as_context()

    # The save is deterministic and happens first, so the fact is on every
    # board — and in this call's own context — regardless of what the model
    # then says about it. `remember()` owns the row and the broadcast.
    saving = memory.directive(prompt)
    fact = memory.remember(saving[0], saving[1], requester) if saving else None

    try:
        text, tokens = llm.complete(prompt, llm.build_system_prompt(context, fact))
        result = AgentResult(text=text, tokens=tokens, estimated=False)
    except Exception as exc:
        # Answer anyway. A demo that shows an error because a third-party API
        # blipped is worse than one that answers from composed text and says
        # so — and `estimated=True` keeps the meter honest about it.
        print(f"[runner] model call failed ({type(exc).__name__}: {exc}) — composing fallback")
        result = _stub_agent(prompt, context, agent_type, fact)

    _hold_slot(started)
    return result


def _hold_slot(started):
    """Keep the slot BUSY long enough to be legible on a recording.

    Padding the slot rather than the model: the queue mechanic is what is being
    demonstrated, and the scheduler's behaviour is identical either way.
    """
    remaining = MIN_SLOT_SECONDS - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)


def _stub_agent(prompt, context, agent_type, fact):
    """Composed answer for when the model is unreachable. Always `estimated`.

    This is the fallback ladder's bottom rung (PRD.md) kept live in code rather
    than as a plan: the workspace degrades to text it composes itself instead
    of surfacing a provider outage as a broken product.
    """
    if fact:
        text = (
            f"[{agent_type} · offline] Saved for the team: "
            f"{fact['key']} — {fact['val']}. Every agent task from now on "
            "loads this before it starts."
        )
    elif context:
        text = (
            f"[{agent_type} · offline] Task accepted: {prompt!r}.\n{context}\n"
            "Those facts were loaded before this task began — nobody had to "
            "repeat them. The model itself is unreachable right now, so this "
            "reply is composed and its token count is an estimate."
        )
    else:
        text = (
            f"[{agent_type} · offline] Task accepted: {prompt!r}. The model is "
            "unreachable right now, so this reply is composed; the scheduler, "
            "queue, quota and shared memory around it are live either way."
        )

    return AgentResult(
        text=text,
        tokens=_estimate_tokens(prompt, context, text),
        estimated=True,
    )


# --- Replies ---------------------------------------------------------------


def _reply(task, result):
    """Account for the spend, then tell the room.

    The `ADD` happens before either broadcast so no client is ever told about
    a response whose cost was not recorded. `token_update` goes first because
    it describes already-committed state and the meter is the headline number.
    """
    usage = state.add_tokens(result.tokens, estimated=result.estimated)

    # `usage` already carries `estimated`, read back from the row, so the
    # broadcast reports the provenance of the whole total rather than of this
    # one call.
    broadcast.broadcast_to_team({"event": "token_update", **usage})
    broadcast.broadcast_to_team(
        {
            "event": "agent_response",
            "user_id": task.get("user_id"),
            "agent_type": task.get("agent_type") or task["slot_id"],
            "text": result.text,
            "tokens_used_this_call": result.tokens,
            "estimated": result.estimated,
        }
    )


def _reply_error(task, message):
    """Directed at the requester — a failed task is their problem, not the room's.

    The connection may already be gone; send_to_connection handles that and
    cleans up the row, so no special case is needed here.
    """
    connection_id = task.get("connection_id")
    if not connection_id:
        print("[runner] no connection_id on the task — cannot report the failure")
        return
    delivered = broadcast.send_to_connection(
        connection_id,
        {"event": "error", "message": message},
    )
    print(f"[runner] error reply to {connection_id} delivered={delivered}")
