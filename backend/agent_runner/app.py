"""HiveOS Agent Runner — one SQS message is one agent task on one slot.

Phase 2 runs a stub agent: it sleeps, returns canned text, and spends no
tokens. That is deliberate. The queue mechanic is the product, and proving it
without an LLM in the loop keeps iteration fast and means a scheduling bug can
never be mistaken for model latency. Phase 3 swaps `_run_agent` for Bedrock and
nothing else in this file has to change.

The single invariant here: **the slot is released even when the task fails.**
A leaked slot deadlocks the workspace, and on a recorded demo that is
indistinguishable from the product being broken. Hence try/finally, always.
"""

import json
import time
import traceback

from shared import broadcast, scheduler

# The stub's "work". Long enough that a BUSY slot and a queue position are
# legible on a recording, short enough that the queue still visibly drains
# inside a 3-minute demo. Also roughly the latency a real Bedrock call will
# have in Phase 3, so the demo's feel does not change when the stub is
# replaced.
STUB_DELAY_SECONDS = 5.0

# Fault injection for the smoke test's no-slot-leak check. The release
# invariant is the one thing that must never silently regress, so it stays
# verifiable against deployed AWS rather than only in a local test.
FAIL_SENTINEL = "__hiveos_fail__"


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


# --- The agent -------------------------------------------------------------


def _run_agent(task):
    """Stub agent. Phase 3 replaces this body with a real Bedrock call."""
    prompt = task.get("prompt", "")

    if FAIL_SENTINEL in prompt:
        raise RuntimeError(f"fault injection via {FAIL_SENTINEL}")

    time.sleep(STUB_DELAY_SECONDS)

    agent_type = task.get("agent_type") or task["slot_id"]
    return (
        f"[{agent_type} · stub] Task accepted: {prompt!r}. "
        "Real model output arrives in Phase 3 — the scheduler, queue and "
        "slot lifecycle around this response are already live."
    )


# --- Replies ---------------------------------------------------------------


def _reply(task, text):
    broadcast.broadcast_to_team(
        {
            "event": "agent_response",
            "user_id": task.get("user_id"),
            "agent_type": task.get("agent_type") or task["slot_id"],
            "text": text,
            # The stub spends nothing. The meter stays honest until there is a
            # real Bedrock usage figure to add.
            "tokens_used_this_call": 0,
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
