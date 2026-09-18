"""Team memory — the MEMORY# rows behind the agent's memory tools.

What this buys the product: a fact one member's agent saves is loaded into
every later agent task, so the next person's agent already knows it without
being told. That is the "shared memory" claim in PRD.md, and it is what makes
the workspace one team rather than N private chat windows.

Memory is loaded **before** the agent runs, never fetched on demand
(CONTRACT.md). A queued user waits while other tasks execute, and their agent
has to already know the team's facts the moment its turn finally starts.
"""

import re

from . import broadcast, state

MAX_KEY = 60
MAX_VAL = 300

# Enough for a demo team and small enough that the context block can never
# grow into the thing that blows the token budget it is displayed next to.
MAX_FACTS_IN_CONTEXT = 20

# `remember: deploy window = Friday 4pm` — see `directive()`.
_DIRECTIVE = re.compile(
    r"^\s*remember\s*:?\s+(?P<key>[^=]{1,80}?)\s*=\s*(?P<val>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def _slug(key):
    """Stable SK fragment derived from the fact's key.

    CONTRACT.md originally specified `MEMORY#<uuid>`. That made
    `set_team_memory` non-idempotent: saving the same key twice left two rows
    carrying the same `key`, which `state_snapshot` would then hand a client
    as two separate facts. Deriving the SK from the key makes a write an
    upsert, which is what a key/value store should do.

    Two keys differing only in case or punctuation collapse to one fact.
    That is a deliberate trade and arguably the correct reading — "Deploy
    Window" and "deploy_window" are the same fact to a team.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
    return slug[:MAX_KEY] or "fact"


# --- get_team_memory -------------------------------------------------------


def facts():
    """Every fact the team knows, most recently set last."""
    rows = state.query_team("MEMORY#")
    rows.sort(key=lambda item: item.get("created_at", ""))
    return [
        {
            "key": row.get("key"),
            "val": row.get("val"),
            "updated_by": row.get("updated_by"),
        }
        for row in rows
    ]


def as_context():
    """The team's facts as a block for an agent's system prompt.

    Returns "" when the team knows nothing, so a caller can treat emptiness
    as falsey rather than having to special-case a header with no body.
    """
    known = facts()[-MAX_FACTS_IN_CONTEXT:]
    if not known:
        return ""
    lines = "\n".join(f"- {fact['key']}: {fact['val']}" for fact in known)
    return f"What this team already knows:\n{lines}"


# --- set_team_memory -------------------------------------------------------


def remember(key, val, updated_by):
    """Upsert a fact and tell the whole team. Returns the fact, or None.

    The broadcast is the point as much as the write: a fact appearing on
    everyone's board the instant it is saved is what makes the memory shared
    rather than merely persistent.
    """
    key = (key or "").strip()[:MAX_KEY]
    val = (val or "").strip()[:MAX_VAL]
    if not key or not val:
        return None

    fact = {"key": key, "val": val, "updated_by": updated_by}
    state.table().put_item(
        Item={
            "PK": state.TEAM_PK,
            "SK": f"MEMORY#{_slug(key)}",
            # Write time. On an upsert this refreshes, so `facts()` orders by
            # most-recently-set — which is the useful order for display.
            "created_at": state.now_iso(),
            **fact,
        }
    )
    print(f"[memory] remembered {key!r} from {updated_by}")

    broadcast.broadcast_to_team({"event": "memory_updated", **fact})
    return fact


def directive(prompt):
    """Parse `remember <key> = <val>` out of a prompt. None if absent.

    A real model decides to call `set_team_memory` itself. The stub agent has
    no model, so the trigger is an explicit prompt convention instead.

    This is the one place the stub differs from a real agent in *kind* rather
    than degree, and it is the reason the demo narration has to say the agent
    is stubbed. Everything the directive then touches — the DynamoDB row, the
    broadcast, the context load on the next task — is the real mechanism.
    """
    match = _DIRECTIVE.match(prompt or "")
    if not match:
        return None
    return match.group("key"), match.group("val")
