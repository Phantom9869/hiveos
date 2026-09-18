#!/usr/bin/env python3
"""End-to-end WebSocket smoke test against the DEPLOYED HiveOS stack.

This is the Phase 1 gate, and the regression check for every phase after it.
It talks to real AWS — nothing here is mocked. A zero exit code means the
deployed system actually behaved, not that a command succeeded.

    pip install websockets
    python scripts/ws_smoke.py

Resolves the wss:// URL from the CloudFormation stack output, so it never
needs a hardcoded endpoint. Requires the AWS CLI on PATH for the DynamoDB
assertions.
"""

import argparse
import asyncio
import json
import subprocess
import sys

import websockets

STACK = "hiveos"
REGION = "us-east-1"
TEAM_PK = "TEAM#alpha"
RECV_TIMEOUT = 20  # generous: the first frame pays a Lambda cold start

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def aws(*args):
    proc = subprocess.run(
        ["aws", *args, "--region", REGION, "--output", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args)} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def websocket_url():
    stacks = aws("cloudformation", "describe-stacks", "--stack-name", STACK)
    outputs = stacks["Stacks"][0]["Outputs"]
    return next(o["OutputValue"] for o in outputs if o["OutputKey"] == "WebSocketURL")


def connection_rows():
    """Every CONN# row currently in DynamoDB."""
    page = aws(
        "dynamodb", "query",
        "--table-name", "hiveos-state",
        "--key-condition-expression", "PK = :p AND begins_with(SK, :s)",
        "--expression-attribute-values",
        json.dumps({":p": {"S": TEAM_PK}, ":s": {"S": "CONN#"}}),
    )
    return {i["SK"]["S"]: i.get("user_id", {}).get("S") for i in page["Items"]}


def put_connection_row(sk, user_id):
    aws(
        "dynamodb", "put-item",
        "--table-name", "hiveos-state",
        "--item",
        json.dumps({
            "PK": {"S": TEAM_PK},
            "SK": {"S": sk},
            "user_id": {"S": user_id},
            "avatar": {"S": "\U0001f480"},
            "x": {"N": "0"},
            "y": {"N": "0"},
            "connected_at": {"S": "1970-01-01T00:00:00Z"},
        }),
    )


async def expect(ws, event, who, timeout=RECV_TIMEOUT):
    """Drain frames until `event` arrives. Other events in between are fine."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(
                f"{who}: timed out waiting for {event!r}; saw {seen}"
            )
        raw = await asyncio.wait_for(ws.recv(), remaining)
        frame = json.loads(raw)
        if frame.get("event") == event:
            return frame
        seen.append(frame.get("event"))


async def run(url):
    print(f"\nEndpoint: {url}\n")

    print("1. Connect + opening snapshot")
    alice = await websockets.connect(f"{url}?user_id=alice&avatar=%F0%9F%90%9D")
    await alice.send(json.dumps({"action": "hello"}))
    snapshot = await expect(alice, "state_snapshot", "alice")

    check(
        "state_snapshot carries every field a cold client renders from",
        all(k in snapshot for k in
            ("team", "agents", "tokens_used", "token_budget", "members", "memory")),
        f"keys={sorted(snapshot)}",
    )
    check(
        "snapshot reports both agent slots IDLE",
        sorted((a["slot_id"], a["status"]) for a in snapshot["agents"])
        == [("coder", "IDLE"), ("researcher", "IDLE")],
        str(snapshot["agents"]),
    )
    check(
        "snapshot carries the real token budget",
        snapshot["token_budget"] == 1000000 and snapshot["tokens_used"] == 0,
        f"{snapshot['tokens_used']}/{snapshot['token_budget']}",
    )

    print("\n2. Presence")
    bob = await websockets.connect(f"{url}?user_id=bob&avatar=%F0%9F%A6%8A")
    joined = await expect(alice, "user_joined", "alice")
    check("alice is told bob arrived", joined.get("user_id") == "bob", str(joined))

    rows = connection_rows()
    check(
        "both CONN# rows written to DynamoDB",
        sorted(v for v in rows.values() if v in ("alice", "bob")) == ["alice", "bob"],
        str(rows),
    )

    print("\n3. Fan-out — the Phase 1 gate")
    await alice.send(json.dumps({"action": "send_message", "text": "hive online"}))
    to_alice = await expect(alice, "chat_message", "alice")
    to_bob = await expect(bob, "chat_message", "bob")
    check(
        "one client's message reaches BOTH clients",
        to_alice["text"] == to_bob["text"] == "hive online",
        f"alice={to_alice['text']!r} bob={to_bob['text']!r}",
    )
    check(
        "sender is resolved from the CONN# row, not the frame",
        to_bob["user_id"] == "alice",
        str(to_bob),
    )

    print("\n4. GoneException — a dead connection must not break delivery")
    # Connect a third client, learn its real connection ID, close it, then put
    # the row back. The Router now holds a genuine-but-dead ID, which is what
    # a closed laptop lid looks like from the server's side.
    before = set(connection_rows())
    ghost = await websockets.connect(f"{url}?user_id=ghost")
    await expect(alice, "user_joined", "alice")
    ghost_sk = next(iter(set(connection_rows()) - before))
    await ghost.close()
    await expect(alice, "user_left", "alice")
    put_connection_row(ghost_sk, "ghost")
    check("stale CONN# row is in place", ghost_sk in connection_rows(), ghost_sk)

    await bob.send(json.dumps({"action": "send_message", "text": "after the ghost"}))
    survived_a = await expect(alice, "chat_message", "alice")
    survived_b = await expect(bob, "chat_message", "bob")
    check(
        "live clients still receive the broadcast past the dead connection",
        survived_a["text"] == survived_b["text"] == "after the ghost",
    )
    check(
        "GoneException branch deleted the stale CONN# row",
        ghost_sk not in connection_rows(),
        ghost_sk,
    )

    print("\n5. Error handling")
    await bob.send(json.dumps({"action": "not_a_real_action"}))
    err = await expect(bob, "error", "bob")
    check("unknown action returns an error frame, not a dropped socket", "message" in err, str(err))
    await bob.send("{not json")
    err = await expect(bob, "error", "bob")
    check("malformed JSON returns an error frame", "message" in err, str(err))

    print("\n6. Disconnect cleanup")
    await bob.close()
    left = await expect(alice, "user_left", "alice")
    check("alice is told bob left", left.get("user_id") == "bob", str(left))
    await alice.close()
    await asyncio.sleep(3)  # $disconnect is fire-and-forget
    remaining = connection_rows()
    check("no CONN# rows leak after everyone leaves", remaining == {}, str(remaining))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="wss:// endpoint (default: from stack output)")
    args = parser.parse_args()

    url = args.url or websocket_url()
    try:
        asyncio.run(run(url))
    except Exception as exc:
        check(f"harness aborted: {type(exc).__name__}", False, str(exc))

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
