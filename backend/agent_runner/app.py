"""HiveOS Agent Runner — one SQS message is one agent task on one slot.

The agent itself is still a **stub**: it sleeps, returns composed text, and
reports an *estimated* token count. Amazon Bedrock is blocked account-wide on
this AWS account (see PROGRESS.md), not by anything here. `_run_agent` is the
single seam a real model call drops into; nothing else in this file changes.

Everything around the stub is real and is what the product actually claims:

  - team memory is loaded before the task runs, and saved facts broadcast
  - `tokens_used` is incremented atomically and broadcast to every client
  - the budget ceiling is **enforced** — the agent is not invoked at 100%

Two invariants live here:

1. **The slot is released even when the task fails.** A leaked slot deadlocks
   the workspace, and on a recording that is indistinguishable from the
   product being broken. Hence try/finally, always.
2. **Every frame carrying a token count sets `estimated`.** While the agent is
   stubbed the number is a heuristic over real text, not billed model usage,
   and no client should be able to present it as the latter.
"""

import json
import time
import traceback
from collections import namedtuple

from shared import broadcast, memory, scheduler, state

# The stub's "work". Long enough that a BUSY slot and a queue position are
# legible on a recording, short enough that the queue still visibly drains
# inside a 3-minute demo.
STUB_DELAY_SECONDS = 5.0

# Fault injection for the smoke test's no-slot-leak check. The release
# invariant is the one thing that must never silently regress, so it stays
# verifiable against deployed AWS rather than only in a local test.
FAIL_SENTINEL = "__hiveos_fail__"

# The standard rough heuristic for English. Only used while the agent is
# stubbed; a real Bedrock response reports usage directly and `estimated`
# becomes False.
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
    """Stub agent. A real Bedrock call replaces this body and nothing else.

    When it does, `tokens` comes from the response's usage block and
    `estimated` becomes False.
    """
    prompt = task.get("prompt", "")

    if FAIL_SENTINEL in prompt:
        raise RuntimeError(f"fault injection via {FAIL_SENTINEL}")

    # Before the work, not during it: a queued user's agent must already know
    # the team's facts the moment its turn starts (CONTRACT.md).
    context = memory.as_context()

    time.sleep(STUB_DELAY_SECONDS)

    agent_type = task.get("agent_type") or task["slot_id"]
    requester = task.get("user_id", "unknown")
    saving = memory.directive(prompt)

    if saving:
        fact = memory.remember(saving[0], saving[1], requester)
        text = (
            f"[{agent_type} · stub] Saved for the team: "
            f"{fact['key']} — {fact['val']}. Every agent task from now on "
            "loads this before it starts."
            if fact
            else f"[{agent_type} · stub] I could not read a fact out of that."
        )
    elif context:
        text = (
            f"[{agent_type} · stub] Task accepted: {prompt!r}.\n{context}\n"
            "Those facts were loaded before this task began — nobody had to "
            "repeat them."
        )
    else:
        text = (
            f"[{agent_type} · stub] Task accepted: {prompt!r}. Real model "
            "output arrives once Bedrock is unblocked; the scheduler, queue, "
            "quota and shared memory around this response are already live."
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
