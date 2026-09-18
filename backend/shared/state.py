"""DynamoDB access for the HiveOS single table.

Schema authority is CONTRACT.md. Everything for a team lives under one
partition key and is separated by SK prefix, so the whole world state is one
Query away — which is exactly what `state_snapshot` needs.
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

TEAM_ID = os.environ.get("TEAM_ID", "alpha")
TEAM_PK = f"TEAM#{TEAM_ID}"

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


def query_team(sk_prefix=None):
    """Every item for the team, optionally narrowed to one SK prefix."""
    condition = Key("PK").eq(TEAM_PK)
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


def connection_ids():
    return [item["SK"].split("#", 1)[1] for item in query_team("CONN#")]


def add_connection(connection_id, user_id, avatar):
    member = {
        "user_id": user_id,
        "avatar": avatar,
        "x": 0,
        "y": 0,
    }
    table().put_item(
        Item={
            "PK": TEAM_PK,
            "SK": f"CONN#{connection_id}",
            "connected_at": now_iso(),
            **member,
        }
    )
    return member


def remove_connection(connection_id):
    """Delete a CONN# row and return what was there, or None if it was gone."""
    response = table().delete_item(
        Key={"PK": TEAM_PK, "SK": f"CONN#{connection_id}"},
        ReturnValues="ALL_OLD",
    )
    return response.get("Attributes")


def connection_user(connection_id):
    response = table().get_item(
        Key={"PK": TEAM_PK, "SK": f"CONN#{connection_id}"},
        ProjectionExpression="user_id",
    )
    return (response.get("Item") or {}).get("user_id")


# --- Queue -----------------------------------------------------------------


def queue_items():
    """Waiting tasks in FIFO order.

    The SK is QUEUE#<timestamp>#<uuid> and the timestamp leads, so sorting
    lexicographically by SK *is* sorting by arrival time. No extra index.
    """
    return sorted(query_team("QUEUE#"), key=lambda item: item["SK"])


def queue_view(items=None):
    """The queue as clients see it: 1-based positions, oldest first."""
    items = queue_items() if items is None else items
    return [
        {
            "user_id": item.get("user_id"),
            "agent_type": item.get("agent_type"),
            "queue_position": position,
        }
        for position, item in enumerate(items, start=1)
    ]


# --- Snapshot --------------------------------------------------------------


def state_snapshot():
    """One frame a cold client can render the entire workspace from.

    Load-bearing per CONTRACT.md: a browser joining mid-demo must not have to
    wait for the next incremental event to show correct state.
    """
    metadata, agents, members, memory, waiting = {}, [], [], [], []

    for item in query_team():
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
        "team": metadata.get("name", TEAM_ID),
        "agents": sorted(agents, key=lambda a: a["slot_id"] or ""),
        "tokens_used": used,
        "token_budget": budget,
        "pct_used": round(float(used) / float(budget) * 100, 1) if budget else 0,
        "members": members,
        "memory": memory,
        # Without this a client that joins or reconnects while queued cannot
        # render its own position until someone else's action happens to move
        # the queue. The snapshot has to stand alone (CONTRACT.md).
        "queue": queue_view(sorted(waiting, key=lambda item: item["SK"])),
    }
