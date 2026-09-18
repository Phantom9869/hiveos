"""Slot scheduling — the OS mechanic that HiveOS actually is.

Two agent slots are the CPU cores. A claim is a conditional DynamoDB update, so
two people clicking at the same instant can never both win the same slot. If
every slot is taken the task becomes a QUEUE# row and gets a real position,
because a queue you cannot see your place in is not a queue.

Both Lambdas use this module: the Router claims on demand, the Agent Runner
releases and dispatches the next waiting task. Keeping one implementation is
what stops the two paths from drifting into different state machines.

Ordering rules that matter (ARCHITECTURE.md decision 1):
  - SQS carries tasks that are RUNNING. Waiting tasks live in DynamoDB.
  - The QUEUE# delete is the exactly-once gate. Whoever wins the delete owns
    the task, so two runners finishing together cannot dispatch it twice.
"""

import json
import os
import uuid

import boto3
from botocore.exceptions import ClientError

from . import broadcast, state

# Slot order is also the fallback order for a claim. CONTRACT.md.
SLOTS = ("coder", "researcher")

# Rough per-task duration used for the queue's estimated wait. A heuristic,
# shown as such — the stub agent takes ~2.5s, a Bedrock call will take longer.
# Phase 3 should retune this once real latency is known.
ESTIMATED_TASK_SECONDS = 8

_sqs = None


def sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def _is_conditional_failure(error):
    return (
        error.response.get("Error", {}).get("Code")
        == "ConditionalCheckFailedException"
    )


# --- Slots -----------------------------------------------------------------


def try_claim(slot_id, user_id):
    """Atomically take one slot. False means somebody else already had it.

    The ConditionExpression is the whole point: this is the only correct way
    to claim, and a read-then-write here would hand both users the same slot
    under exactly the load the demo creates.
    """
    try:
        state.table().update_item(
            Key={"PK": state.TEAM_PK, "SK": f"AGENT#{slot_id}"},
            UpdateExpression="SET #s = :busy, #u = :user, claimed_at = :now",
            ConditionExpression="#s = :idle",
            ExpressionAttributeNames={"#s": "status", "#u": "current_user"},
            ExpressionAttributeValues={
                ":busy": "BUSY",
                ":idle": "IDLE",
                ":user": user_id,
                ":now": state.now_iso(),
            },
        )
        return True
    except ClientError as error:
        if _is_conditional_failure(error):
            return False
        raise


def claim_any(preferred, user_id):
    """Claim the requested slot, else any other. None if all are busy."""
    order = list(SLOTS)
    if preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)

    for slot_id in order:
        if try_claim(slot_id, user_id):
            print(f"[scheduler] claimed slot={slot_id} user={user_id}")
            return slot_id

    print(f"[scheduler] all slots busy — user={user_id} must queue")
    return None


def set_idle(slot_id):
    state.table().update_item(
        Key={"PK": state.TEAM_PK, "SK": f"AGENT#{slot_id}"},
        UpdateExpression="SET #s = :idle, #u = :null REMOVE claimed_at",
        ExpressionAttributeNames={"#s": "status", "#u": "current_user"},
        ExpressionAttributeValues={":idle": "IDLE", ":null": None},
    )
    print(f"[scheduler] released slot={slot_id}")


# --- Queue -----------------------------------------------------------------


def enqueue(user_id, agent_type, prompt, connection_id):
    """Park a task in DynamoDB and return its SK."""
    sk = f"QUEUE#{state.now_iso_micros()}#{uuid.uuid4().hex[:8]}"
    state.table().put_item(
        Item={
            "PK": state.TEAM_PK,
            "SK": sk,
            "user_id": user_id,
            "agent_type": agent_type,
            "prompt": prompt,
            "connection_id": connection_id,
            "enqueued_at": state.now_iso(),
        }
    )
    print(f"[scheduler] enqueued user={user_id} sk={sk}")
    return sk


def take_next_task():
    """Claim ownership of the next waiting task, or None if none wait.

    "Next" is `state.fair_order` — least-recently-served first, arrival only as
    the tie-break — not simply the oldest. The same function orders the queue
    the clients are shown, so position 1 on the board is genuinely whoever this
    will pick.

    The conditional delete is the exactly-once gate. Two Agent Runners
    finishing at the same moment both see the same head-of-queue item; only
    one can delete it, and the loser simply moves to the next one.
    """
    for item in state.queue_items():
        try:
            state.table().delete_item(
                Key={"PK": state.TEAM_PK, "SK": item["SK"]},
                ConditionExpression="attribute_exists(SK)",
            )
            return item
        except ClientError as error:
            if _is_conditional_failure(error):
                print(f"[scheduler] lost race for {item['SK']} — trying next")
                continue
            raise
    return None


def requeue(item):
    """Put a task back at its original position.

    Only used when we won a task but then lost the slot race. Re-writing the
    same SK preserves FIFO — the user does not get punished by going to the
    back of the line for losing a race they never saw.
    """
    state.table().put_item(Item=item)
    print(f"[scheduler] requeued {item['SK']} — no slot was free after all")


# --- Dispatch --------------------------------------------------------------


def dispatch(slot_id, user_id, agent_type, prompt, connection_id):
    """Hand a running task to SQS. Format is CONTRACT.md's."""
    sqs().send_message(
        QueueUrl=os.environ["QUEUE_URL"],
        MessageBody=json.dumps(
            {
                "team_id": state.TEAM_ID,
                "slot_id": slot_id,
                "user_id": user_id,
                "agent_type": agent_type,
                "prompt": prompt,
                "connection_id": connection_id,
                "enqueued_at": state.now_iso(),
            }
        ),
    )
    print(f"[scheduler] dispatched slot={slot_id} user={user_id}")


# --- Broadcasts ------------------------------------------------------------


def broadcast_slot(slot_id, status, current_user):
    broadcast.broadcast_to_team(
        {
            "event": "agent_state_update",
            "agent_type": slot_id,
            "slot_id": slot_id,
            "status": status,
            "current_user": current_user,
        }
    )


def broadcast_queue():
    """Tell every waiting user where they now stand.

    Sent to the whole team rather than directed at one connection: a user can
    have several tabs open, and the frontend filters on user_id anyway.
    """
    for entry in state.queue_view():
        broadcast.broadcast_to_team(
            {
                "event": "queue_update",
                "user_id": entry["user_id"],
                "queue_position": entry["queue_position"],
                "estimated_wait_seconds": entry["queue_position"]
                * ESTIMATED_TASK_SECONDS,
            }
        )


# --- The release path ------------------------------------------------------


def release_and_dispatch(slot_id):
    """Free a slot, then start the next waiting task. Returns the slot used.

    This runs in the Agent Runner's finally block, so it must work even when
    the task it follows blew up. A slot that leaks here deadlocks the demo.
    """
    set_idle(slot_id)
    broadcast_slot(slot_id, "IDLE", None)

    task = take_next_task()
    if task is None:
        return None

    claimed = claim_any(task.get("agent_type"), task["user_id"])
    if claimed is None:
        # Someone claimed the slot we just freed in the gap between the two
        # operations. Put the task back where it was and let whoever finishes
        # next pick it up.
        requeue(task)
        broadcast_queue()
        return None

    # Same ordering rule as claim_agent: the BUSY frame must reach clients
    # before the task that could complete and release the slot.
    broadcast_slot(claimed, "BUSY", task["user_id"])
    dispatch(
        claimed,
        task["user_id"],
        task.get("agent_type"),
        task.get("prompt", ""),
        task.get("connection_id"),
    )
    broadcast_queue()
    return claimed
