"""Task history — who ran what, what it cost, and whether it ran at all.

This is the entity a governance product is actually for. Until now HiveOS
could tell you the team had spent 2,214 tokens and absolutely nothing about
*where they went*: no per-person spend, no record of a refusal, no way to
answer "who used the budget up". A meter without a ledger is a gauge, which is
the exact criticism the product levels at everything else.

It is also why `get_task_context` was never built (CONTRACT.md): there was no
task history to return.

Rows are `TASK#<microsecond timestamp>#<short uuid>`, so they sort newest-last
lexicographically by SK for free. **Microsecond precision, not `now_iso()`** —
at second granularity two tasks finishing in the same second tie and fall back
to UUID order, i.e. random. The queue learned this the hard way in Phase 2.
"""

import uuid

from . import state

# How much of the prompt to keep. Enough to recognise a task in a list, not so
# much that the history rows become a second copy of everything ever asked.
MAX_PROMPT = 120

# What a snapshot carries. The frame has to stay small — every client parses it
# on connect — and nobody reads the 40th most recent task off a board.
SNAPSHOT_LIMIT = 12

DONE = "done"
FAILED = "failed"
REFUSED = "refused"


def record(user_id, agent_type, tokens, estimated, status, prompt=""):
    """Write one task to the ledger. Never raises into the caller.

    Deliberately swallowing failures: this is a record *about* work that has
    already happened, and losing a history row is a great deal better than
    failing a task that already ran and already charged the team for it.
    """
    try:
        state.table().put_item(
            Item={
                "PK": state.TEAM_PK,
                "SK": f"TASK#{state.now_iso_micros()}#{uuid.uuid4().hex[:8]}",
                "user_id": user_id or "unknown",
                "agent_type": agent_type or "",
                "tokens": int(tokens or 0),
                "estimated": bool(estimated),
                "status": status,
                "prompt": (prompt or "")[:MAX_PROMPT],
                "created_at": state.now_iso(),
            }
        )
    except Exception as exc:  # pragma: no cover - telemetry, not control flow
        print(f"[history] could not record task ({type(exc).__name__}: {exc})")


def view(rows):
    """Shape `TASK#` items for the wire, newest first."""
    ordered = sorted(rows, key=lambda item: item["SK"], reverse=True)
    return [
        {
            "user_id": item.get("user_id"),
            "agent_type": item.get("agent_type"),
            "tokens": int(item.get("tokens", 0)),
            "estimated": bool(item.get("estimated", False)),
            "status": item.get("status"),
            "prompt": item.get("prompt", ""),
            "at": item.get("created_at"),
        }
        for item in ordered[:SNAPSHOT_LIMIT]
    ]


def as_context(limit=8):
    """Recent tasks as a block a model can read. "" when nothing has run.

    This is what `get_task_context` returns, and the reason CONTRACT.md could
    finally stop calling that tool unbuildable: it was specified to return
    prior task history, and until the ledger existed there was none.

    Reads the table itself rather than taking rows, because its caller is a
    tool invocation deep inside a model round trip and has none to hand.
    """
    rows = sorted(state.query_team("TASK#"), key=lambda item: item["SK"], reverse=True)
    if not rows:
        return "The team has not run any agent tasks yet."

    lines = [
        f"- {item.get('user_id')}: {item.get('prompt') or '(no prompt)'}"
        f" [{item.get('status')}, {int(item.get('tokens', 0))} tokens]"
        for item in rows[:limit]
    ]
    return "Recent agent tasks, newest first:\n" + "\n".join(lines)


def spend(rows):
    """Per-person spend, biggest first.

    Aggregated over *every* task row rather than the snapshot slice — the whole
    point is the total, and a breakdown of only the last twelve tasks would be
    a different and much less useful number.

    Counts refusals and failures as tasks but not as spend, because that is
    what they are: a refused task costs nothing, and saying so is the clearest
    possible demonstration that the ceiling is a control rather than a gauge.
    """
    totals = {}
    for item in rows:
        user = item.get("user_id") or "unknown"
        bucket = totals.setdefault(user, {"user_id": user, "tokens": 0, "tasks": 0})
        bucket["tokens"] += int(item.get("tokens", 0))
        bucket["tasks"] += 1

    return sorted(totals.values(), key=lambda row: (-row["tokens"], row["user_id"]))
