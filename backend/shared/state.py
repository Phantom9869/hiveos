"""DynamoDB access for the HiveOS single table.

Schema authority is CONTRACT.md. Everything for a team lives under one
partition key and is separated by SK prefix, so the whole world state is one
Query away — which is exactly what `state_snapshot` needs.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# The team a client lands in when it does not name one. Teams are no longer
# hardcoded — every row is partitioned by team and every function takes one —
# but a bare connection still has to go somewhere, and that somewhere is the
# team the demo uses.
DEFAULT_TEAM = os.environ.get("TEAM_ID", "alpha")

# Team names arrive from a query string, so they are untrusted input that ends
# up inside a partition key. Anything outside this set could collide two teams
# into one partition, or forge a `TEAM#` prefix of its own.
TEAM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")

# What a team starts with the first time anyone joins it. Teams bootstrap
# themselves — requiring a seeding script before a name works would make
# isolation a deployment step rather than a property of the product.
DEFAULT_TEAM_BUDGET = int(os.environ.get("TOKEN_BUDGET", "1000000"))
SLOT_IDS = ("coder", "researcher")


def clean_team(name):
    """Normalise an untrusted team name, falling back to the default.

    Lowercased so `Alpha` and `alpha` are the same room rather than two rooms
    that look identical in the UI — a team whose members silently cannot see
    each other is worse than one that rejects the name outright.
    """
    candidate = (name or "").strip().lower()
    return candidate if TEAM_PATTERN.match(candidate) else DEFAULT_TEAM


def team_pk(team):
    return f"TEAM#{clean_team(team)}"

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso_micros():
    """Microsecond-precision timestamp, used only for QUEUE# sort keys.

    QUEUE# items are ordered by SK, so the timestamp is what makes the queue
    FIFO. At second granularity two people clicking in the same second tie and
    fall back to UUID order — i.e. random. Microseconds keep the order the one
    thing the queue must never get wrong: actual arrival.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- JSON ------------------------------------------------------------------
# DynamoDB hands back Decimal for every number and json.dumps refuses it.
# Every frame that leaves this backend goes through dumps().


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def dumps(payload):
    return json.dumps(_plain(payload))


# --- Queries ---------------------------------------------------------------


def query_team(team, sk_prefix=None):
    """Every item for the team, optionally narrowed to one SK prefix."""
    condition = Key("PK").eq(team_pk(team))
    if sk_prefix:
        condition = condition & Key("SK").begins_with(sk_prefix)

    items = []
    kwargs = {"KeyConditionExpression": condition}
    while True:
        response = table().query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


# --- Connections -----------------------------------------------------------


def connection_ids(team):
    return [item["SK"].split("#", 1)[1] for item in query_team(team, "CONN#")]


def spawn_point(connection_id):
    """A scattered starting position, derived from the connection ID.

    Everyone used to spawn at (0, 0), which stacked every avatar in one corner
    of the canvas — on a three-browser demo that reads as "the workspace is
    broken", not "nobody has moved yet". Derived from the ID rather than
    randomised so a client and the server agree without another round trip, and
    so a reconnect does not teleport someone across the room.

    Kept to the middle 60% of each axis: the corners are where the avatar
    collides with the canvas caption and the edge labels.
    """
    digest = hashlib.sha1(connection_id.encode()).digest()
    return {
        "x": Decimal(20 + digest[0] * 60 // 255),
        "y": Decimal(20 + digest[1] * 60 // 255),
    }


def add_connection(team, connection_id, user_id, avatar):
    member = {
        "user_id": user_id,
        "avatar": avatar,
        **spawn_point(connection_id),
    }
    table().put_item(
        Item={
            "PK": team_pk(team),
            "SK": f"CONN#{connection_id}",
            "connected_at": now_iso(),
            **member,
        }
    )
    # Written together: a member row without its index is a connection nothing
    # can route a later frame to.
    bind_connection(connection_id, team)
    return member


def move_connection(team, connection_id, x, y):
    """Move one avatar. Returns the mover's `user_id`, or None if the row is gone.

    Conditional on the row existing so a frame that races `$disconnect` cannot
    resurrect a dead connection as a half-populated row — `state_snapshot`
    reads every CONN# row as a live member, so that ghost would show up in the
    member count on every screen.
    """
    try:
        response = table().update_item(
            Key={"PK": team_pk(team), "SK": f"CONN#{connection_id}"},
            UpdateExpression="SET x = :x, y = :y",
            ConditionExpression="attribute_exists(SK)",
            # `Decimal(str(x))`, never `Decimal(x)`. Building a Decimal from a
            # float carries the full binary expansion — Decimal(24.92) is
            # 24.9200000000000017053..., and boto3's DYNAMODB_CONTEXT raises
            # decimal.Inexact rather than silently rounding it.
            ExpressionAttributeValues={":x": Decimal(str(x)), ":y": Decimal(str(y))},
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return (response.get("Attributes") or {}).get("user_id")


def remove_connection(team, connection_id):
    """Delete a CONN# row and return what was there, or None if it was gone."""
    response = table().delete_item(
        Key={"PK": team_pk(team), "SK": f"CONN#{connection_id}"},
        ReturnValues="ALL_OLD",
    )
    unbind_connection(connection_id)
    return response.get("Attributes")


def connection_user(team, connection_id):
    response = table().get_item(
        Key={"PK": team_pk(team), "SK": f"CONN#{connection_id}"},
        ProjectionExpression="user_id",
    )
    return (response.get("Item") or {}).get("user_id")


# --- Connection ownership --------------------------------------------------


def ensure_team(team):
    """Create a team's METADATA and slot rows if this is its first member.

    Teams bootstrap themselves. Requiring `seed.sh` before a name worked would
    make isolation a deployment step rather than a property of the product —
    and the whole point is that two people typing different team names get
    different workspaces without anyone provisioning anything.

    Every write is conditional on the row being absent, so several people
    joining a brand-new team in the same second cannot each reset it to empty.
    """
    pk = team_pk(team)
    try:
        table().put_item(
            Item={
                "PK": pk,
                "SK": "METADATA",
                "name": clean_team(team),
                "token_budget": DEFAULT_TEAM_BUDGET,
                "tokens_used": 0,
                "created_at": now_iso(),
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
        print(f"[state] bootstrapped new team {clean_team(team)!r}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise

    for slot_id in SLOT_IDS:
        try:
            table().put_item(
                Item={
                    "PK": pk,
                    "SK": f"AGENT#{slot_id}",
                    "slot_id": slot_id,
                    "status": "IDLE",
                    "current_user": None,
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise


def bind_connection(connection_id, team):
    """Record which team a connection belongs to.

    This row lives *outside* every team partition, keyed `CONN#<id>` / `TEAM`,
    and it exists because of an awkward asymmetry in API Gateway: the team
    arrives in the query string, which is only readable on `$connect`. Every
    frame after that carries a connection ID and nothing else — so without an
    index, finding a connection's team would mean scanning every team.

    The alternative was to have the client send its team on each frame. That is
    rejected for the same reason `CONTRACT.md` resolves the *sender* from the
    stored row rather than the frame: a value the client supplies is a value
    the client can forge, and here forging it would mean reading another
    team's board.
    """
    table().put_item(
        Item={
            "PK": f"CONN#{connection_id}",
            "SK": "TEAM",
            "team": clean_team(team),
            "connected_at": now_iso(),
        }
    )


def connection_team(connection_id):
    """Which team this connection is in. `DEFAULT_TEAM` if the row is gone.

    Falling back rather than raising: a missing index row means the connection
    is already being torn down, and answering into the default team is
    harmless, where an exception in the router is a dropped frame.
    """
    response = table().get_item(
        Key={"PK": f"CONN#{connection_id}", "SK": "TEAM"},
        ProjectionExpression="team",
    )
    return (response.get("Item") or {}).get("team") or DEFAULT_TEAM


def unbind_connection(connection_id):
    table().delete_item(Key={"PK": f"CONN#{connection_id}", "SK": "TEAM"})


# --- Queue -----------------------------------------------------------------


def _served_from(task_rows):
    """When each person's most recent task finished, from rows already in hand.

    The SK leads with a microsecond timestamp, so the largest SK for a user
    *is* their most recent task. People with no tasks are simply absent, and
    `fair_order` reads absent as "has waited forever" — which is what it means.
    """
    served = {}
    for item in task_rows:
        user = item.get("user_id")
        if not user:
            continue
        if item["SK"] > served.get(user, ""):
            served[user] = item["SK"]
    return served


def last_served(team):
    """`_served_from` over a fresh read of the `TASK#` ledger.

    Read off the ledger rather than tracked separately: it already records who
    ran what and when, and a second counter maintained alongside it is one more
    thing that can disagree with it.

    Accurate at dispatch time because the runner records a task before its
    `finally` releases the slot — the job that just finished is already in the
    ledger when the next one is chosen.
    """
    return _served_from(query_team(team, "TASK#"))


def fair_order(team, items, served=None):
    """The queue order: fair queueing, not first-come-first-served.

    Sorted by how long each person has gone without a turn, and only then by
    arrival. Someone who has never run outranks someone who just did, so one
    person queueing three tasks cannot drain the whole queue while another
    waits — they interleave.

    **This is the single definition of queue order.** Display and dispatch both
    go through it. If the board said "position 1" by arrival while the runner
    picked by fairness, the number on screen would simply be wrong about who
    goes next — and this repository has been bitten twice by two orderings that
    had to agree and eventually did not.

    FIFO remains the tie-break, so among people who have waited equally long
    the earlier request still wins. With one task each — the common case, and
    the demo case — this is indistinguishable from FIFO.
    """
    served = last_served(team) if served is None else served
    return sorted(items, key=lambda item: (served.get(item.get("user_id"), ""), item["SK"]))


def queue_items(team, served=None):
    """Waiting tasks in the order they will actually be dispatched."""
    return fair_order(team, query_team(team, "QUEUE#"), served)


def queue_view(team, items=None):
    """The queue as clients see it: 1-based positions, next up first."""
    items = queue_items(team) if items is None else items
    return [
        {
            "user_id": item.get("user_id"),
            "agent_type": item.get("agent_type"),
            "queue_position": position,
        }
        for position, item in enumerate(items, start=1)
    ]


# --- Token accounting ------------------------------------------------------


def pct_used(used, budget):
    """One definition of the percentage, shared by the snapshot and every
    `token_update`. Two copies would eventually disagree by a rounding step
    and the meter would flicker between them."""
    if not budget:
        return 0
    return round(float(used) / float(budget) * 100, 1)


def budget_state(team):
    """`(tokens_used, token_budget)` straight from METADATA.

    METADATA is the single source of truth for the ceiling — deliberately not
    the `TOKEN_BUDGET` env var. It is the same row the counter increments and
    the same number `state_snapshot` hands a client, so the guard can never
    disagree with the meter a user is looking at. `TOKEN_BUDGET` only supplies
    the default `seed.sh` writes.
    """
    item = table().get_item(Key={"PK": team_pk(team), "SK": "METADATA"}).get("Item") or {}
    return int(item.get("tokens_used", 0)), int(item.get("token_budget", 0))


def add_tokens(team, count, estimated=False):
    """Atomically add to `tokens_used`; returns a ready `token_update` payload.

    `ADD` rather than read-then-write because two agent runs finishing
    together would otherwise lose one of the two increments — and an
    undercounted meter is exactly the failure the product claims to prevent.

    `ALL_NEW` so the budget comes back in the same round trip that moved the
    counter. Reading it separately would let the broadcast describe a state
    that no longer matches the row it came from.

    `estimated` sets a sticky `usage_estimated` flag on the row. It is sticky
    and never cleared here on purpose: once any estimated spend is folded into
    the total, the *total* is partly estimated for as long as it stands, and a
    client loading cold has no other way to learn that. Only `seed.sh` clears
    it, by rewriting METADATA from scratch.
    """
    expression = "ADD tokens_used :n"
    values = {":n": count}
    if estimated:
        expression += " SET usage_estimated = :e"
        values[":e"] = True

    item = table().update_item(
        Key={"PK": team_pk(team), "SK": "METADATA"},
        UpdateExpression=expression,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )["Attributes"]

    used = int(item.get("tokens_used", 0))
    budget = int(item.get("token_budget", 0))
    return {
        "tokens_used": used,
        "token_budget": budget,
        "pct_used": pct_used(used, budget),
        "estimated": bool(item.get("usage_estimated", False)),
    }


# --- Snapshot --------------------------------------------------------------


def state_snapshot(team):
    """One frame a cold client can render the entire workspace from.

    Load-bearing per CONTRACT.md: a browser joining mid-demo must not have to
    wait for the next incremental event to show correct state.
    """
    # Imported here, not at module scope: `history` needs `state` for its
    # writes, so a top-level import either way is a cycle. Only the two pure
    # shaping functions are used below, and this is their one caller.
    from . import history

    metadata, agents, members, memory, waiting, tasks = {}, [], [], [], [], []

    for item in query_team(team):
        sk = item["SK"]
        if sk == "METADATA":
            metadata = item
        elif sk.startswith("QUEUE#"):
            waiting.append(item)
        elif sk.startswith("AGENT#"):
            agents.append(
                {
                    "slot_id": item.get("slot_id"),
                    "agent_type": item.get("slot_id"),
                    "status": item.get("status"),
                    "current_user": item.get("current_user"),
                }
            )
        elif sk.startswith("CONN#"):
            members.append(
                {
                    "user_id": item.get("user_id"),
                    "avatar": item.get("avatar"),
                    "x": item.get("x", 0),
                    "y": item.get("y", 0),
                }
            )
        elif sk.startswith("TASK#"):
            tasks.append(item)
        elif sk.startswith("MEMORY#"):
            memory.append(
                {
                    "key": item.get("key"),
                    "val": item.get("val"),
                    "updated_by": item.get("updated_by"),
                }
            )

    budget = metadata.get("token_budget", 0)
    used = metadata.get("tokens_used", 0)

    return {
        "event": "state_snapshot",
        "team": clean_team(team),
        "agents": sorted(agents, key=lambda a: a["slot_id"] or ""),
        "tokens_used": used,
        "token_budget": budget,
        "pct_used": pct_used(used, budget),
        # Whether any of that total is an estimate rather than billed model
        # usage. Carried on the snapshot so a client loading cold — the most
        # likely way anyone sees this board — is told too, not just clients
        # that happened to be watching when the spend happened.
        "usage_estimated": bool(metadata.get("usage_estimated", False)),
        "members": members,
        "memory": memory,
        # Without this a client that joins or reconnects while queued cannot
        # render its own position until someone else's action happens to move
        # the queue. The snapshot has to stand alone (CONTRACT.md).
        # Ordered by the same function the runner dispatches with, and from
        # the task rows already in hand rather than a second query. Sorting by
        # SK here — as this did — would have the snapshot disagree with the
        # live queue about who is next the moment fairness reorders anything.
        "queue": queue_view(team, fair_order(team, waiting, _served_from(tasks))),
        # The ledger. On the snapshot rather than only on a live event for the
        # same reason as everything else here: the client most likely to want
        # "who spent what" is the one that just opened the URL.
        "history": history.view(tasks),
        "spend": history.spend(tasks),
    }
