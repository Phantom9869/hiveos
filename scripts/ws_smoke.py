#!/usr/bin/env python3
"""End-to-end WebSocket smoke test against the DEPLOYED HiveOS stack.

This is the Phase 1 and Phase 2 gate, and the regression check for every phase
after them. It talks to real AWS — nothing here is mocked. A zero exit code
means the deployed system actually behaved, not that a command succeeded.

Sections 1-6 cover the WebSocket backbone; 7-12 cover the scheduler: both
slots claimed, a third claim queued with a real position, auto-dispatch when a
slot frees, and no slot leak when a task fails. Sections 13-18 cover shared
memory, token accounting, and the enforced budget ceiling. 19 covers avatar
presence and movement; 21 covers team isolation; 22 covers workspace passphrases; 23 covers administration; 20 covers fair queueing — that dispatch order follows
who has waited longest rather than who arrived first, and that the position
shown on the board is the one actually dispatched.

The harness resets `tokens_used` and clears MEMORY# rows before and after, so
it is re-runnable — tasks now genuinely spend (estimated) tokens and write
facts.

    pip install websockets
    python scripts/ws_smoke.py

Resolves the wss:// URL from the CloudFormation stack output, so it never
needs a hardcoded endpoint. Requires the AWS CLI on PATH for the DynamoDB
assertions.

**Close every browser tab pointed at the deployed URL before running this.**
The CONN#-leak checks assert the table holds *no* connection rows, so a live
browser anywhere in the world counts as a leak and fails four checks that have
nothing to do with the code. If those four are the only failures, that is
almost certainly what happened.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import urllib.parse

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


def knows_fact(text, value):
    """Does this answer demonstrate knowledge of `value`, however phrased?

    Every token must appear, but not contiguously. A substring match was fine
    against the stub, which echoed the fact verbatim — a real model writes
    "Friday **at** 16:00 UTC" and the gate failed on an inserted preposition
    while the agent had in fact loaded the memory correctly.

    Still deliberately strict: it demands every token, because this is the
    Phase 3 gate and a loose match here would let a genuinely broken memory
    load pass on a coincidental word.
    """
    haystack = text.lower()
    return all(token in haystack for token in value.lower().split())


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


def scheduler_rows():
    """Slots and queue from ONE DynamoDB query: ({slot: (status, user)}, [users]).

    Deliberately a single call. A queued task only exists for as long as the
    task ahead of it runs, so two sequential AWS CLI round trips can easily
    outlast the window and read an empty queue that really was there.
    """
    page = aws(
        "dynamodb", "query",
        "--table-name", "hiveos-state",
        "--key-condition-expression", "PK = :p",
        "--expression-attribute-values", json.dumps({":p": {"S": TEAM_PK}}),
    )
    slots, waiting = {}, []
    for item in page["Items"]:
        sk = item["SK"]["S"]
        if sk.startswith("AGENT#"):
            slots[sk.split("#", 1)[1]] = (
                item["status"]["S"],
                item.get("current_user", {}).get("S"),
            )
        elif sk.startswith("QUEUE#"):
            waiting.append((sk, item.get("user_id", {}).get("S")))
    return slots, sorted(waiting)


def agent_rows():
    return scheduler_rows()[0]


def queue_rows():
    """Waiting users in FIFO order."""
    return [user for _, user in scheduler_rows()[1]]


def metadata_row():
    """`(tokens_used, token_budget)` from the METADATA row."""
    page = aws(
        "dynamodb", "get-item",
        "--table-name", "hiveos-state",
        "--key", json.dumps({"PK": {"S": TEAM_PK}, "SK": {"S": "METADATA"}}),
    )
    item = page.get("Item") or {}
    return (
        int(item.get("tokens_used", {}).get("N", 0)),
        int(item.get("token_budget", {}).get("N", 0)),
    )


def set_tokens_used(value):
    """Drive `tokens_used` directly.

    The only way to reach the budget ceiling without actually spending a
    budget, which is what makes the refusal path testable at all.
    """
    aws(
        "dynamodb", "update-item",
        "--table-name", "hiveos-state",
        "--key", json.dumps({"PK": {"S": TEAM_PK}, "SK": {"S": "METADATA"}}),
        "--update-expression", "SET tokens_used = :n",
        "--expression-attribute-values", json.dumps({":n": {"N": str(value)}}),
    )


def memory_rows():
    """Every MEMORY# row: {SK: (key, val, updated_by)}."""
    page = aws(
        "dynamodb", "query",
        "--table-name", "hiveos-state",
        "--key-condition-expression", "PK = :p AND begins_with(SK, :s)",
        "--expression-attribute-values",
        json.dumps({":p": {"S": TEAM_PK}, ":s": {"S": "MEMORY#"}}),
    )
    return {
        i["SK"]["S"]: (
            i.get("key", {}).get("S"),
            i.get("val", {}).get("S"),
            i.get("updated_by", {}).get("S"),
        )
        for i in page["Items"]
    }


def task_rows():
    """Every TASK# row's SK."""
    page = aws(
        "dynamodb", "query",
        "--table-name", "hiveos-state",
        "--key-condition-expression", "PK = :p AND begins_with(SK, :s)",
        "--expression-attribute-values",
        json.dumps({":p": {"S": TEAM_PK}, ":s": {"S": "TASK#"}}),
    )
    return [i["SK"]["S"] for i in page["Items"]]


def reset_demo_state():
    """Slots IDLE, queue empty, memory and history cleared, `tokens_used` 0.

    Tasks spend tokens, write MEMORY# rows and now write TASK# rows, so without
    all three this harness would only pass the first time it was ever run.

    TASK# matters for two reasons beyond tidiness: `state_snapshot` walks every
    row in the partition, so leaving them behind slows every frame the harness
    waits on; and dispatch order is derived from them, so a stale ledger would
    decide who goes next in the *following* run.
    """
    set_tokens_used(0)
    for sk in task_rows():
        aws(
            "dynamodb", "delete-item",
            "--table-name", "hiveos-state",
            "--key", json.dumps({"PK": {"S": TEAM_PK}, "SK": {"S": sk}}),
        )
    for sk in memory_rows():
        aws(
            "dynamodb", "delete-item",
            "--table-name", "hiveos-state",
            "--key", json.dumps({"PK": {"S": TEAM_PK}, "SK": {"S": sk}}),
        )
    for slot in ("coder", "researcher"):
        aws(
            "dynamodb", "put-item",
            "--table-name", "hiveos-state",
            "--item",
            json.dumps({
                "PK": {"S": TEAM_PK},
                "SK": {"S": f"AGENT#{slot}"},
                "status": {"S": "IDLE"},
                "current_user": {"NULL": True},
                "slot_id": {"S": slot},
            }),
        )
    for sk, _ in scheduler_rows()[1]:
        aws(
            "dynamodb", "delete-item",
            "--table-name", "hiveos-state",
            "--key", json.dumps({"PK": {"S": TEAM_PK}, "SK": {"S": sk}}),
        )


async def drain(ws):
    """Discard frames already buffered on this socket.

    Every client sees every broadcast, so by mid-run each socket holds a
    backlog. Without draining, a later `expect` can match an old frame and
    report a pass for something that never happened.
    """
    while True:
        try:
            await asyncio.wait_for(ws.recv(), 0.25)
        except (asyncio.TimeoutError, TimeoutError):
            return


async def expect(ws, event, who, timeout=RECV_TIMEOUT, where=None):
    """Drain frames until `event` arrives. Other events in between are fine.

    `where` narrows to a specific frame — several agent_state_update events
    fly around during a claim, and the test needs a named one.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    seen = []
    while True:
        remaining = deadline - loop.time()
        try:
            if remaining <= 0:
                raise TimeoutError
            raw = await asyncio.wait_for(ws.recv(), remaining)
        except (asyncio.TimeoutError, TimeoutError):
            # Report what did arrive. A bare TimeoutError here says only
            # "something hung", which is useless against deployed AWS.
            raise AssertionError(
                f"{who}: timed out waiting for {event!r}; saw {seen}"
            ) from None
        frame = json.loads(raw)
        if frame.get("event") == event and (where is None or where(frame)):
            return frame
        seen.append(frame)


async def run(url):
    print(f"\nEndpoint: {url}\n")
    reset_demo_state()

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
    # Not hardcoded to 1,000,000: the demo is seeded with a smaller budget so
    # real token counts are visible on the meter (TOKEN_BUDGET=… ./scripts/seed.sh),
    # and this harness has to pass at whatever budget is actually seeded.
    check(
        "snapshot carries a real token budget and a reset counter",
        snapshot["token_budget"] > 0 and snapshot["tokens_used"] == 0,
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

    await run_scheduler(url)


async def run_scheduler(url):
    """Phase 2 gate: two slots, a real queue, and no slot leaks."""
    print("\n7. Scheduler — claiming both slots")
    alice = await websockets.connect(f"{url}?user_id=alice")
    bob = await websockets.connect(f"{url}?user_id=bob")
    carol = await websockets.connect(f"{url}?user_id=carol")

    await alice.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder", "prompt": "write a parser",
    }))
    first = await expect(alice, "agent_state_update", "alice",
                         where=lambda f: f.get("current_user") == "alice")
    check(
        "first claim takes the coder slot",
        (first.get("slot_id"), first.get("current_user")) == ("coder", "alice"),
        str(first),
    )

    await bob.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder", "prompt": "summarise the RFC",
    }))
    # Filter on the holder, not just BUSY: bob's socket also carries alice's
    # claim, and matching that would pass this check for the wrong reason.
    second = await expect(bob, "agent_state_update", "bob",
                          where=lambda f: f.get("current_user") == "bob")
    check(
        "second claim falls back to researcher — a preference is not a reservation",
        (second.get("slot_id"), second.get("current_user")) == ("researcher", "bob"),
        str(second),
    )

    print("\n8. The third claim queues — the Phase 2 gate")
    await carol.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder", "prompt": "check the budget",
    }))
    queued = await expect(carol, "queue_update", "carol",
                          where=lambda f: f.get("user_id") == "carol")
    check(
        "third claim returns a real queue position, not a failure",
        queued.get("queue_position") == 1,
        str(queued),
    )
    check(
        "queue position is broadcast to the whole team, not just the queued user",
        (await expect(alice, "queue_update", "alice",
                      where=lambda f: f.get("user_id") == "carol")) is not None,
    )

    # One query, both assertions: this is the only moment where two slots are
    # BUSY and a third task is genuinely waiting in DynamoDB.
    slots, waiting = scheduler_rows()
    check(
        "DynamoDB confirms both slots BUSY with the right holders",
        slots == {"coder": ("BUSY", "alice"), "researcher": ("BUSY", "bob")},
        str(slots),
    )
    check(
        "QUEUE# item written to DynamoDB for carol",
        [user for _, user in waiting] == ["carol"],
        str(waiting),
    )

    print("\n9. Auto-dispatch when a slot frees — the Phase 2 gate")
    done = await expect(alice, "agent_response", "alice",
                        where=lambda f: f.get("user_id") == "alice")
    check("alice's agent responded", bool(done.get("text")), str(done)[:120])
    # Provenance, not a fixed expectation. With a model reachable the count is
    # what the provider reported (estimated=False); on the fallback path it is
    # a heuristic (estimated=True). What must hold either way is that the frame
    # *says which*, so no client can present one as the other.
    check(
        "the response reports a token cost and declares its provenance",
        done.get("tokens_used_this_call", 0) > 0
        and isinstance(done.get("estimated"), bool),
        f"tokens={done.get('tokens_used_this_call')} estimated={done.get('estimated')}",
    )

    dispatched = await expect(carol, "agent_state_update", "carol",
                              where=lambda f: f.get("current_user") == "carol")
    check(
        "carol is auto-dispatched into the freed slot without re-asking",
        dispatched.get("status") == "BUSY",
        str(dispatched),
    )
    drained = queue_rows()
    check("queue is now empty in DynamoDB", drained == [], str(drained))

    # Let bob and carol finish so the slots return to IDLE.
    await expect(carol, "agent_response", "carol",
                 where=lambda f: f.get("user_id") == "carol")
    await asyncio.sleep(2)
    slots = agent_rows()
    check(
        "every slot returns to IDLE once the work drains",
        sorted(slots.values()) == [("IDLE", None), ("IDLE", None)],
        str(slots),
    )

    print("\n10. A failing task must never leak a slot — the Phase 2 gate")
    await drain(alice)
    await alice.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder",
        "prompt": "__hiveos_fail__ deliberate fault injection",
    }))
    await expect(alice, "agent_state_update", "alice",
                 where=lambda f: f.get("current_user") == "alice")
    failed = await expect(alice, "error", "alice")
    check("the failing task reports an error to its requester", "message" in failed, str(failed))

    await expect(alice, "agent_state_update", "alice",
                 where=lambda f: f.get("slot_id") == "coder" and f.get("status") == "IDLE")
    slots = agent_rows()
    check(
        "the slot is released even though the agent raised",
        slots.get("coder") == ("IDLE", None),
        str(slots),
    )

    print("\n11. Guard against one user monopolising both slots")
    await drain(bob)
    await bob.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder", "prompt": "first task",
    }))
    await expect(bob, "agent_state_update", "bob",
                 where=lambda f: f.get("current_user") == "bob")
    await bob.send(json.dumps({
        "action": "claim_agent", "agent_type": "researcher", "prompt": "second task",
    }))
    refused = await expect(bob, "error", "bob")
    check(
        "a second concurrent claim by the same user is refused",
        "already" in refused.get("message", ""),
        str(refused),
    )

    print("\n12. Cleanup")
    for ws in (alice, bob, carol):
        await ws.close()
    await asyncio.sleep(8)  # let bob's in-flight task finish and release
    reset_demo_state()
    leaked = connection_rows()
    check("no CONN# rows leak after the scheduler run", leaked == {}, str(leaked))

    await run_memory_and_budget(url)


async def run_memory_and_budget(url):
    """Phase 3 (Bedrock-free) gate: shared memory, token accounting, ceiling."""
    print("\n13. Shared team memory")
    alice = await websockets.connect(f"{url}?user_id=alice")
    bob = await websockets.connect(f"{url}?user_id=bob")
    await drain(alice)
    await drain(bob)

    await alice.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder",
        "prompt": "remember: deploy window = Friday 16:00 UTC",
    }))

    saved = await expect(alice, "memory_updated", "alice")
    check(
        "saving a fact broadcasts memory_updated",
        (saved.get("key"), saved.get("val")) == ("deploy window", "Friday 16:00 UTC"),
        str(saved),
    )
    check(
        "the fact reaches the whole team, not just the author",
        (await expect(bob, "memory_updated", "bob")).get("key") == "deploy window",
    )
    check(
        "the fact is attributed to whoever saved it",
        saved.get("updated_by") == "alice",
        str(saved.get("updated_by")),
    )

    rows = memory_rows()
    check(
        "MEMORY# row written to DynamoDB",
        list(rows.values()) == [("deploy window", "Friday 16:00 UTC", "alice")],
        str(rows),
    )
    check(
        "the SK is derived from the key, so a re-save is an upsert",
        list(rows) == ["MEMORY#deploy_window"],
        str(list(rows)),
    )

    await expect(alice, "agent_response", "alice",
                 where=lambda f: f.get("user_id") == "alice")
    await asyncio.sleep(2)

    print("\n14. Memory crosses to another user — the Phase 3 gate")
    await drain(bob)
    await bob.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder",
        "prompt": "when can I ship?",
    }))
    answered = await expect(bob, "agent_response", "bob",
                            where=lambda f: f.get("user_id") == "bob")
    check(
        "bob's agent already knows alice's fact without being told",
        knows_fact(answered.get("text", ""), "Friday 16:00 UTC"),
        answered.get("text", "")[:160],
    )

    print("\n15. Re-saving a key upserts rather than duplicating")
    await asyncio.sleep(2)
    await drain(alice)
    await alice.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder",
        "prompt": "remember: Deploy Window = Monday 09:00 UTC",
    }))
    await expect(alice, "memory_updated", "alice")
    await expect(alice, "agent_response", "alice",
                 where=lambda f: f.get("user_id") == "alice")
    rows = memory_rows()
    check(
        "the team still knows exactly one deploy window, now updated",
        len(rows) == 1 and list(rows.values())[0][1] == "Monday 09:00 UTC",
        str(rows),
    )

    print("\n16. Token accounting")
    await asyncio.sleep(2)
    before, budget = metadata_row()
    await drain(bob)
    await bob.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder", "prompt": "estimate this task",
    }))
    meter = await expect(bob, "token_update", "bob")
    spent = await expect(bob, "agent_response", "bob",
                         where=lambda f: f.get("user_id") == "bob")

    check(
        "token_update is broadcast with the new total and the budget",
        meter.get("tokens_used", 0) > before and meter.get("token_budget") == budget,
        f"{meter.get('tokens_used')}/{meter.get('token_budget')} (was {before})",
    )
    # `usage_estimated` is sticky (state.add_tokens): once any estimated spend
    # is folded in, the *total* is partly estimated for as long as it stands.
    # So a real call need not clear it — but an estimated one must set it.
    call_estimated = spent.get("estimated")
    total_estimated = meter.get("estimated")
    check(
        "an estimated call flags the running total — the flag is sticky",
        total_estimated is True if call_estimated else isinstance(total_estimated, bool),
        f"call={call_estimated} total={total_estimated}",
    )

    # The cold-load case, and the reason it is a check at all: the flag once
    # rode only on live token_update frames, so a browser opening the URL for
    # the first time — every judge — saw an unlabelled number. Cold must agree
    # with live exactly, whichever way the flag is set.
    cold = await websockets.connect(f"{url}?user_id=cold")
    await cold.send(json.dumps({"action": "hello"}))
    snap = await expect(cold, "state_snapshot", "cold")
    await cold.close()
    check(
        "a cold client's snapshot reports the same provenance as the live frame",
        snap.get("usage_estimated") == total_estimated and snap.get("tokens_used", 0) > 0,
        f"cold={snap.get('usage_estimated')} live={total_estimated} used={snap.get('tokens_used')}",
    )
    check(
        "pct_used matches tokens_used/token_budget",
        abs(meter.get("pct_used", -1)
            - round(meter["tokens_used"] / meter["token_budget"] * 100, 1)) < 0.05,
        f"pct={meter.get('pct_used')}",
    )

    await asyncio.sleep(2)
    persisted, _ = metadata_row()
    check(
        "DynamoDB agrees with what was broadcast — no lost increment",
        persisted == meter["tokens_used"],
        f"dynamo={persisted} broadcast={meter['tokens_used']}",
    )
    check(
        "the increment equals the cost the response reported",
        persisted - before == spent.get("tokens_used_this_call"),
        f"delta={persisted - before} reported={spent.get('tokens_used_this_call')}",
    )

    print("\n17. The budget ceiling is enforced — the Phase 3 gate")
    # Drive the counter to the ceiling. The only way to test the refusal
    # without actually spending a budget.
    set_tokens_used(budget)
    at_ceiling, _ = metadata_row()
    check("counter parked at the ceiling", at_ceiling == budget, f"{at_ceiling}/{budget}")

    await drain(alice)
    await alice.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder", "prompt": "one more task",
    }))
    exhausted = await expect(alice, "budget_exhausted", "alice")
    check(
        "budget_exhausted is broadcast with the numbers that caused it",
        exhausted.get("tokens_used") == budget
        and exhausted.get("token_budget") == budget,
        str(exhausted),
    )
    refused = await expect(alice, "error", "alice")
    check(
        "the requester is told the agent was not invoked",
        "quota" in refused.get("message", "").lower(),
        str(refused),
    )

    await asyncio.sleep(3)
    after_refusal, _ = metadata_row()
    check(
        "**the agent was genuinely not invoked — not one token was spent**",
        after_refusal == budget,
        f"{after_refusal} (ceiling {budget})",
    )
    slots = agent_rows()
    check(
        "a refused task still releases its slot — no leak on the refusal path",
        sorted(slots.values()) == [("IDLE", None), ("IDLE", None)],
        str(slots),
    )

    print("\n18. Cleanup")
    for ws in (alice, bob):
        await ws.close()
    await asyncio.sleep(3)
    reset_demo_state()
    used_after, _ = metadata_row()
    check(
        "demo state reset — counter zeroed and memory cleared",
        used_after == 0 and memory_rows() == {},
        f"tokens_used={used_after} facts={len(memory_rows())}",
    )
    leaked = connection_rows()
    check("no CONN# rows leak after the memory run", leaked == {}, str(leaked))

    await run_avatars(url)
    await run_fairness(url)
    await run_isolation(url)
    await run_passphrase(url)
    await run_admin(url)





# A fixed name and passphrase, so this is idempotent: the first run creates the
# workspace, every later run verifies against what it created.
VAULT_TEAM = "smoke-vault"
VAULT_PASS = "correct horse battery staple"


async def _join(url, user, team, passphrase=None):
    """Try to open a socket and read a snapshot. Returns (snapshot | None)."""
    query = f"user_id={user}&team={team}"
    if passphrase is not None:
        query += f"&passphrase={urllib.parse.quote(passphrase)}"
    try:
        ws = await websockets.connect(f"{url}?{query}")
    except Exception:
        # API Gateway answers a refused $connect with 403, which the client
        # library raises during the handshake. There is no status code a
        # browser could read here either — see useHive's `refused` state.
        return None
    try:
        await ws.send(json.dumps({"action": "hello"}))
        return await expect(ws, "state_snapshot", user)
    finally:
        await ws.close()



# Created and deleted within the section, so it leaves nothing behind.
ADMIN_TEAM = "smoke-admin"
ADMIN_TOKEN = "smoke-owner-token-do-not-reuse"


async def run_admin(url):
    """Section 23: administration is enforced on the server.

    The point of these checks is not that the buttons work — it is that hiding
    them is *not* the control. A workspace with no accounts has no "who", so
    rights hang off a secret the creator holds, and the server has to refuse a
    frame from anyone who does not hold it. Every negative case below is a
    client that asked politely and was told no.
    """
    print("\n23. Workspace administration — the Phase 11 gate")

    owner = await websockets.connect(
        f"{url}?user_id=owner&team={ADMIN_TEAM}"
        f"&admin_token={urllib.parse.quote(ADMIN_TOKEN)}"
    )
    await owner.send(json.dumps({"action": "hello"}))
    created = await expect(owner, "state_snapshot", "owner")
    check(
        "whoever creates a workspace administers it",
        created.get("is_admin") is True and created.get("owned") is True,
        f"is_admin={created.get('is_admin')} owned={created.get('owned')}",
    )
    check(
        "**the admin salt and hash never reach a client**",
        "admin_hash" not in json.dumps(created)
        and "admin_salt" not in json.dumps(created),
        "neither field present in state_snapshot",
    )

    member = await websockets.connect(f"{url}?user_id=member&team={ADMIN_TEAM}")
    await member.send(json.dumps({"action": "hello"}))
    plain = await expect(member, "state_snapshot", "member")
    check(
        "an ordinary member is not an administrator",
        plain.get("is_admin") is False,
        f"is_admin={plain.get('is_admin')}",
    )

    forger = await websockets.connect(
        f"{url}?user_id=forger&team={ADMIN_TEAM}&admin_token=not-the-real-token"
    )
    await forger.send(json.dumps({"action": "hello"}))
    forged = await expect(forger, "state_snapshot", "forger")
    check(
        "**a wrong admin token grants nothing**",
        forged.get("is_admin") is False,
        f"is_admin={forged.get('is_admin')}",
    )

    await drain(member)
    await member.send(json.dumps({
        "action": "admin_set_budget", "token_budget": 999999,
    }))
    refused = await expect(member, "error", "member")
    check(
        "**the server refuses a privileged action from a member — not the UI**",
        "admin token" in (refused.get("message") or ""),
        refused.get("message"),
    )

    await drain(member)
    await owner.send(json.dumps({"action": "admin_set_budget", "token_budget": 4242}))
    changed = await expect(member, "token_update", "member",
                           where=lambda f: f.get("token_budget") == 4242)
    check(
        "the owner sets the budget and the whole workspace sees it",
        changed.get("token_budget") == 4242,
        f"budget={changed.get('token_budget')} seen by a member",
    )

    await owner.send(json.dumps({"action": "admin_delete_workspace"}))
    gone = await expect(member, "workspace_deleted", "member")
    check(
        "deleting a workspace tells the people standing in it",
        gone.get("team") == ADMIN_TEAM,
        f"team={gone.get('team')}",
    )

    for ws in (owner, member, forger):
        await ws.close()
    await asyncio.sleep(3)


async def run_passphrase(url):
    """Section 22: a protected workspace admits only the passphrase.

    The open case is asserted as hard as the closed one. Zero-login on the
    public URL is a deliberate property — a stranger has to be able to open the
    board cold — and an over-eager auth change would take it away silently.
    """
    print("\n22. Workspace passphrases — the Phase 10 gate")

    created = await _join(url, "founder", VAULT_TEAM, VAULT_PASS)
    check(
        "a workspace created with a passphrase reports itself protected",
        created is not None and created.get("protected") is True,
        f"protected={created and created.get('protected')}",
    )
    check(
        "**the salt and hash never reach a client**",
        created is not None
        and "pass_hash" not in json.dumps(created)
        and "pass_salt" not in json.dumps(created),
        "neither field present in state_snapshot",
    )

    await asyncio.sleep(1)
    check(
        "**a wrong passphrase is refused at the handshake**",
        await _join(url, "stranger", VAULT_TEAM, "not the passphrase") is None,
        "no socket opened",
    )
    check(
        "**no passphrase at all is refused**",
        await _join(url, "stranger", VAULT_TEAM) is None,
        "no socket opened",
    )
    check(
        "the right passphrase gets in",
        await _join(url, "teammate", VAULT_TEAM, VAULT_PASS) is not None,
        "joined",
    )

    open_board = await _join(url, "judge", "alpha")
    check(
        "**an open workspace still opens cold — zero-login survives**",
        open_board is not None and open_board.get("protected") is False,
        f"joined, protected={open_board and open_board.get('protected')}",
    )
    await asyncio.sleep(3)


async def run_isolation(url):
    """Section 21: two teams cannot see each other.

    Isolation is the one property that cannot be demonstrated from inside a
    single team, and the one where a regression is silent — everything would
    keep working, just for everybody at once. A leak here is a data breach
    rather than a bug, so it is asserted from both directions: nothing of
    acme's reaches alpha, and nothing of alpha's reaches acme.
    """
    print("\n21. Two teams cannot see each other — the Phase 9 gate")
    alice = await websockets.connect(f"{url}?user_id=alice&team=alpha")
    zara = await websockets.connect(f"{url}?user_id=zara&team=acme")
    await asyncio.sleep(1.5)
    for ws in (alice, zara):
        await drain(ws)
        await ws.send(json.dumps({"action": "hello"}))

    alpha = await expect(alice, "state_snapshot", "alice")
    acme = await expect(zara, "state_snapshot", "zara")

    check(
        "each side is told which workspace it is in",
        (alpha.get("team"), acme.get("team")) == ("alpha", "acme"),
        f"{alpha.get('team')} / {acme.get('team')}",
    )
    check(
        "**neither team's member list contains the other's people**",
        "zara" not in {m["user_id"] for m in alpha.get("members", [])}
        and "alice" not in {m["user_id"] for m in acme.get("members", [])},
        f"alpha={sorted(m['user_id'] for m in alpha.get('members', []))} "
        f"acme={sorted(m['user_id'] for m in acme.get('members', []))}",
    )
    check(
        "a brand-new team bootstrapped its own budget and slots",
        acme.get("token_budget", 0) > 0 and len(acme.get("agents", [])) == 2,
        f"budget={acme.get('token_budget')} slots={len(acme.get('agents', []))}",
    )

    # Real work in acme, watched from alpha.
    await drain(alice)
    await zara.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder",
        "prompt": "remember: office = Berlin",
    }))
    await expect(zara, "agent_response", "zara",
                 where=lambda f: f.get("user_id") == "zara", timeout=60)

    leaked = []
    try:
        while True:
            raw = await asyncio.wait_for(alice.recv(), 2)
            event = json.loads(raw).get("event")
            if event in {"agent_state_update", "token_update", "agent_response",
                         "memory_updated", "queue_update"}:
                leaked.append(event)
    except (asyncio.TimeoutError, TimeoutError):
        pass
    check(
        "**acme's agent activity never reaches alpha**",
        not leaked,
        f"leaked {sorted(set(leaked))}" if leaked else "nothing crossed",
    )

    await alice.send(json.dumps({"action": "hello"}))
    after = await expect(alice, "state_snapshot", "alice")
    check(
        "**acme's spend and memory stay out of alpha's board**",
        after.get("tokens_used") == 0 and not after.get("memory"),
        f"alpha tokens={after.get('tokens_used')} facts={len(after.get('memory') or [])}",
    )

    for ws in (alice, zara):
        await ws.close()
    await asyncio.sleep(3)


async def run_fairness(url):
    """Section 20: dispatch order is fairness, not arrival.

    The product claims *fair queueing* and OS-style scheduling. Without this
    check that claim could regress to plain FIFO silently — nothing else here
    would notice, because with one task each and nobody having run yet the two
    orders are identical.

    The setup is fiddly for a real reason: a task completes in about 4.5s
    (MIN_SLOT_SECONDS plus the model), so both contenders have to be queued
    inside that window. An earlier version of this let alice be dispatched
    before bob had even queued, and read a misleading "position 1" twice.
    """
    print("\n20. Dispatch order is fair, not first-come — the Phase 8 gate")
    alice = await websockets.connect(f"{url}?user_id=alice")
    bob = await websockets.connect(f"{url}?user_id=bob")
    carol = await websockets.connect(f"{url}?user_id=carol")
    dave = await websockets.connect(f"{url}?user_id=dave")
    watcher = await websockets.connect(f"{url}?user_id=watcher")
    for ws in (alice, bob, carol, dave, watcher):
        await drain(ws)

    # alice takes a turn, so the ledger knows she was served most recently.
    await alice.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder", "prompt": "one deploy risk",
    }))
    await expect(alice, "agent_response", "alice",
                 where=lambda f: f.get("user_id") == "alice")
    await asyncio.sleep(2)
    for ws in (alice, bob, carol, dave, watcher):
        await drain(ws)

    # Both slots taken by other people.
    for who, ws in (("carol", carol), ("dave", dave)):
        await ws.send(json.dumps({
            "action": "claim_agent", "agent_type": "coder", "prompt": f"{who} holds a slot",
        }))
        await expect(watcher, "agent_state_update", "watcher",
                     where=lambda f, w=who: f.get("current_user") == w)

    # alice asks FIRST, bob SECOND — and bob has never run.
    await alice.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder", "prompt": "alice again",
    }))
    await asyncio.sleep(0.35)
    await bob.send(json.dumps({
        "action": "claim_agent", "agent_type": "coder", "prompt": "bob first turn",
    }))
    await asyncio.sleep(1.0)

    await watcher.send(json.dumps({"action": "hello"}))
    snap = await expect(watcher, "state_snapshot", "watcher")
    order = [(q["user_id"], q["queue_position"]) for q in snap.get("queue", [])]
    check(
        "both contenders are waiting at once — the test window held",
        len(order) == 2,
        str(order),
    )
    check(
        "**the board ranks the person who has waited longer first, not the earlier arrival**",
        order and order[0][0] == "bob",
        str(order),
    )

    dispatched = await expect(watcher, "agent_state_update", "watcher",
                              where=lambda f: f.get("status") == "BUSY"
                              and f.get("current_user") in ("alice", "bob"),
                              timeout=90)
    check(
        "**and dispatches that same person — display and dispatch cannot disagree**",
        dispatched.get("current_user") == "bob",
        f"dispatched {dispatched.get('current_user')}, board said {order and order[0][0]}",
    )

    for ws in (alice, bob, carol, dave, watcher):
        await ws.close()
    await asyncio.sleep(10)
    reset_demo_state()


async def run_avatars(url):
    """Phase 5: the shared workspace floor."""
    print("\n19. Avatar presence and movement")
    alice = await websockets.connect(f"{url}?user_id=alice&avatar=%F0%9F%90%9D")
    bob = await websockets.connect(f"{url}?user_id=bob&avatar=%F0%9F%A6%8A")
    await alice.send(json.dumps({"action": "hello"}))
    snapshot = await expect(alice, "state_snapshot", "alice")

    spots = {m["user_id"]: (m["x"], m["y"]) for m in snapshot["members"]}
    check(
        "everyone spawns somewhere different — no pile-up in one corner",
        len(set(spots.values())) == len(spots) and len(spots) >= 2,
        str(spots),
    )

    await drain(alice)
    await bob.send(json.dumps({"action": "move_avatar", "x": 24.92, "y": 69.74}))
    moved = await expect(alice, "avatar_moved", "alice")
    check(
        "a move is broadcast to the rest of the team, not just the mover",
        moved.get("user_id") == "bob",
        str(moved),
    )
    # The regression that matters. 24.92 is not representable in binary
    # floating point, so `Decimal(x)` from a float raises decimal.Inexact
    # inside boto3 and the move is silently lost — while the mover's own
    # optimistic UI still shows them somewhere nobody else sees. Coordinates
    # like 73.5 or 0.25 *are* representable and pass either way, which is
    # exactly why this check uses one that is not.
    check(
        "**an awkward float survives the round trip — Decimal(str(x)), not Decimal(x)**",
        (float(moved.get("x", -1)), float(moved.get("y", -1))) == (24.92, 69.74),
        f"{moved.get('x')},{moved.get('y')}",
    )

    await bob.send(json.dumps({"action": "move_avatar", "x": 999, "y": -50}))
    clamped = await expect(alice, "avatar_moved", "alice")
    check(
        "out-of-range coordinates are clamped to the board, not rejected",
        (float(clamped["x"]), float(clamped["y"])) == (100.0, 0.0),
        f"{clamped['x']},{clamped['y']}",
    )

    for bad in ("over there", True, None):
        await bob.send(json.dumps({"action": "move_avatar", "x": bad, "y": 3}))
        refused = await expect(bob, "error", "bob")
        check(
            f"a non-numeric coordinate ({bad!r}) is refused",
            "numeric" in refused.get("message", ""),
            refused.get("message", ""),
        )

    await alice.send(json.dumps({"action": "hello"}))
    resnap = await expect(alice, "state_snapshot", "alice")
    where = [(m["x"], m["y"]) for m in resnap["members"] if m["user_id"] == "bob"]
    check(
        "a client loading cold sees where everyone is standing",
        where and (float(where[0][0]), float(where[0][1])) == (100.0, 0.0),
        str(where),
    )

    for ws in (alice, bob):
        await ws.close()
    await asyncio.sleep(3)
    leaked = connection_rows()
    check("no CONN# rows leak after the avatar run", leaked == {}, str(leaked))


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
