#!/usr/bin/env python3
"""Rehearse the recorded demo, end to end, against deployed AWS.

`ws_smoke.py` asks "is the system correct?" — 49 checks over every path,
including ones no viewer will ever see. This asks a different and, three days
before a deadline, more urgent question: **does the sequence I am about to
record actually work, in that order, in the time I have?**

So every assertion here is something a viewer can see on screen, and every beat
is wall-clock timed. A beat that passes but takes 40 seconds fails the demo just
as surely as one that breaks, because the video has 180 seconds total.

    python scripts/rehearse.py            # one run
    python scripts/rehearse.py --takes 2  # BUILD_PLAN Phase 6 task 4
    python scripts/rehearse.py --ceiling  # the budget-refusal beat instead

The beat order is BUILD_PLAN's, not PROGRESS.md's earlier run sheet, and the
difference is deliberate — see `pick a slot` below.

Close stray browser tabs first: this asserts on the member list.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time

import websockets

STACK = "hiveos"
REGION = "us-east-1"
TEAM_PK = "TEAM#alpha"
RECV_TIMEOUT = 25

# The fact Alice saves on camera. Short enough to read on a 640px window.
FACT_KEY = "deploy window"
FACT_VAL = "Friday 16:00 UTC"

# What the video has to fit inside. The beats below are the 0:25-2:15 stretch
# of BUILD_PLAN's script — problem framing and the AWS/learnings outro are
# talking over a static board and cost no product time.
DEMO_BUDGET_SECONDS = 110

# The board every other tool expects to find. `--ceiling` seeds a tiny budget
# on purpose, and leaving it behind is a trap: ws_smoke.py resets `tokens_used`
# but not `token_budget`, so the next smoke run silently inherits a 60-token
# ceiling and fails two checks for reasons that have nothing to do with the
# code. Restored unconditionally when this script exits.
STANDARD_BUDGET = 5000

results = []
timings = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def knows_fact(text, value):
    """Does this answer demonstrate knowledge of `value`, however phrased?

    Every token must appear, but not contiguously — a real model writes
    "Friday **at** 16:00 UTC" and a substring match fails on the preposition
    while the memory load was in fact correct. Kept identical to the copy in
    `ws_smoke.py`; if one changes, change both.
    """
    haystack = text.lower()
    return all(token in haystack for token in value.lower().split())


class Beat:
    """Times a demo beat and reports it against the recording budget."""

    def __init__(self, label):
        self.label = label

    def __enter__(self):
        print(f"\n{self.label}")
        self.started = time.monotonic()
        return self

    def __exit__(self, *exc):
        elapsed = time.monotonic() - self.started
        timings.append((self.label, elapsed))
        print(f"  ({elapsed:.1f}s)")
        return False


def aws(*args):
    proc = subprocess.run(
        ["aws", *args, "--region", REGION, "--output", "json"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args)} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def stack_output(key):
    outputs = aws("cloudformation", "describe-stacks", "--stack-name", STACK)
    outputs = outputs["Stacks"][0]["Outputs"]
    return next(o["OutputValue"] for o in outputs if o["OutputKey"] == key)


def metadata_row():
    page = aws(
        "dynamodb", "get-item", "--table-name", "hiveos-state",
        "--key", json.dumps({"PK": {"S": TEAM_PK}, "SK": {"S": "METADATA"}}),
    )
    item = page.get("Item") or {}
    return (
        int(item.get("tokens_used", {}).get("N", 0)),
        int(item.get("token_budget", {}).get("N", 0)),
    )


def reset(budget):
    """Re-run the operator's own reset script — not a second implementation.

    If reset-demo.sh cannot produce a clean board, the rehearsal must fail here
    rather than quietly cleaning up after it and reporting a demo that only
    works when this file is driving.
    """
    print(f"\n-- reset-demo.sh (budget {budget}) --")
    # Deliberately *not* SKIP_WARM: the operator pre-warms before recording, so
    # a rehearsal that skipped it would report cold-start timings the real take
    # will never see, and hide warm-path regressions behind the noise.
    proc = subprocess.run(
        ["./scripts/reset-demo.sh"],
        env={**os.environ, "TOKEN_BUDGET": str(budget)},
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"reset-demo.sh failed:\n{proc.stdout}\n{proc.stderr}")
    print("   board clean")


def restore_standard_budget(used_budget):
    """Put the board back to the budget every other tool assumes.

    Only does work if this run moved it. Runs on the failure path too — a
    rehearsal that aborts mid-ceiling is exactly when the small budget is most
    likely to be forgotten.
    """
    if used_budget == STANDARD_BUDGET:
        return
    print(f"\n-- restoring the standard board (budget {STANDARD_BUDGET}) --")
    subprocess.run(
        ["./scripts/seed.sh"],
        env={**os.environ, "TOKEN_BUDGET": str(STANDARD_BUDGET)},
        capture_output=True, text=True, check=True,
    )
    print(f"   budget back to {STANDARD_BUDGET}; ws_smoke.py will behave")


# Frames that arrived while `expect` was waiting for a *different* one. They
# used to be dropped on the floor, which made the harness order-sensitive in a
# way the product is not: `memory_updated` now broadcasts at the start of a
# task rather than after it, so it overtakes the `agent_state_update` being
# awaited, and the next `expect` looked for a frame that had already been read
# and thrown away — failing 25s later with a message pointing nowhere near the
# cause. Buffering per socket makes arrival order irrelevant, which is the only
# assumption a broadcast harness is entitled to make.
_pending = {}


def _buffer(ws):
    return _pending.setdefault(ws, [])


async def expect(ws, event, who, timeout=RECV_TIMEOUT, where=None):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    buffered = _buffer(ws)

    # Anything that arrived early is still here, in arrival order.
    for i, frame in enumerate(buffered):
        if frame.get("event") == event and (where is None or where(frame)):
            return buffered.pop(i)

    seen = list(buffered)
    while True:
        remaining = deadline - loop.time()
        try:
            if remaining <= 0:
                raise TimeoutError
            raw = await asyncio.wait_for(ws.recv(), remaining)
        except (asyncio.TimeoutError, TimeoutError):
            raise AssertionError(
                f"{who}: timed out waiting for {event!r}; saw {[f.get('event') for f in seen]}"
            ) from None
        frame = json.loads(raw)
        if frame.get("event") == event and (where is None or where(frame)):
            return frame
        buffered.append(frame)
        seen.append(frame)


async def drain(ws):
    _buffer(ws).clear()
    while True:
        try:
            await asyncio.wait_for(ws.recv(), 0.25)
        except (asyncio.TimeoutError, TimeoutError):
            return


async def snapshot(ws, who):
    await ws.send(json.dumps({"action": "hello"}))
    return await expect(ws, "state_snapshot", who)


async def run_demo(url):
    """The 0:25-2:15 stretch of the recorded script, in recording order."""
    alice = await websockets.connect(f"{url}?user_id=alice&avatar=%F0%9F%90%9D")
    bob = await websockets.connect(f"{url}?user_id=bob&avatar=%F0%9F%A6%8A")
    charlie = await websockets.connect(f"{url}?user_id=charlie&avatar=%F0%9F%A6%89")

    try:
        with Beat("BEAT 1 (0:25) — three browsers, one workspace"):
            snaps = {
                "alice": await snapshot(alice, "alice"),
                "bob": await snapshot(bob, "bob"),
                "charlie": await snapshot(charlie, "charlie"),
            }
            meters = {
                who: (s["tokens_used"], s["token_budget"]) for who, s in snaps.items()
            }
            check(
                "the token meter reads identically on all three screens",
                len(set(meters.values())) == 1,
                str(meters),
            )
            # The claim is "one workspace", so each screen must show the other
            # two people — not just its own connection.
            seen_by = {
                who: sorted({m["user_id"] for m in s["members"]})
                for who, s in snaps.items()
            }
            check(
                "every screen lists all three members",
                all(v == ["alice", "bob", "charlie"] for v in seen_by.values()),
                str(seen_by),
            )
            check(
                "the board opens with both slots IDLE",
                all(a["status"] == "IDLE" for a in snaps["alice"]["agents"]),
                str([(a["slot_id"], a["status"]) for a in snaps["alice"]["agents"]]),
            )

        for ws in (alice, bob, charlie):
            await drain(ws)

        with Beat("BEAT 2 (0:45) — the queue moment"):
            # Alice's task IS the memory save. This is the one place the
            # rehearsal departs from PROGRESS.md's run sheet, and it is not a
            # style preference: a user holding a slot cannot claim a second one
            # (scheduler refuses it), so "Alice claims, then Alice saves a fact"
            # is two separate rounds and ~15 extra seconds. Folding the save
            # into her claim makes the fact land at the exact moment her slot
            # frees and Charlie is dispatched into it — which is BUILD_PLAN's
            # beat, and one continuous shot instead of two.
            claimed_at = time.monotonic()
            await alice.send(json.dumps({
                "action": "claim_agent", "agent_type": "coder",
                "prompt": f"remember: {FACT_KEY} = {FACT_VAL}",
            }))
            busy = await expect(alice, "agent_state_update", "alice",
                                where=lambda f: f.get("current_user") == "alice")
            # Measured on Bob's socket: the product claim is that the board is
            # identical on every screen, so the number that matters is how long
            # a bystander waits, not the actor.
            bob_saw = await expect(bob, "agent_state_update", "bob",
                                   where=lambda f: f.get("current_user") == "alice")
            propagation = (time.monotonic() - claimed_at) * 1000
            check(
                "alice's claim takes a slot and lands on a bystander's screen",
                busy["status"] == "BUSY" and bob_saw["status"] == "BUSY",
                f"{busy['slot_id']} BUSY, visible to bob in {propagation:.0f} ms",
            )

            await bob.send(json.dumps({
                "action": "claim_agent", "agent_type": "researcher",
                "prompt": "summarise yesterday's incident review",
            }))
            await expect(bob, "agent_state_update", "bob",
                         where=lambda f: f.get("current_user") == "bob")

            await charlie.send(json.dumps({
                "action": "claim_agent", "agent_type": "coder",
                "prompt": "when is our next deploy?",
            }))
            queued = await expect(charlie, "queue_update", "charlie",
                                  where=lambda f: f.get("user_id") == "charlie")
            check(
                "the third request gets a real queue position, not a failure",
                queued.get("queue_position") == 1,
                f"position {queued.get('queue_position')}",
            )
            # Position 1 on Charlie's own screen could be a client-side guess.
            # Seeing it on Alice's proves the server assigned it.
            on_alice = await expect(alice, "queue_update", "alice",
                                    where=lambda f: f.get("user_id") == "charlie")
            check(
                "the whole team sees charlie waiting — the queue is shared state",
                on_alice.get("queue_position") == 1,
                str(on_alice.get("queue_position")),
            )

        with Beat("BEAT 3 (1:30) — the memory moment and auto-dispatch"):
            saved = await expect(bob, "memory_updated", "bob")
            check(
                "alice's fact reaches a teammate's screen, attributed to her",
                (saved.get("key"), saved.get("val"), saved.get("updated_by"))
                == (FACT_KEY, FACT_VAL, "alice"),
                f"{saved.get('key')} = {saved.get('val')} (by {saved.get('updated_by')})",
            )

            freed_at = time.monotonic()
            dispatched = await expect(charlie, "agent_state_update", "charlie",
                                      where=lambda f: f.get("current_user") == "charlie")
            dispatch_ms = (time.monotonic() - freed_at) * 1000
            check(
                "charlie is auto-dispatched into the freed slot — he never re-asks",
                dispatched.get("status") == "BUSY",
                f"{dispatched.get('slot_id')} in {dispatch_ms:.0f} ms after the slot freed",
            )

            answered = await expect(charlie, "agent_response", "charlie",
                                    where=lambda f: f.get("user_id") == "charlie")
            check(
                "**charlie's agent already knows alice's fact — nobody told it**",
                knows_fact(answered.get("text", ""), FACT_VAL),
                answered.get("text", "").replace("\n", " ")[:120],
            )

        with Beat("BEAT 4 (2:05) — the meter moved, and says what it is"):
            used, budget = metadata_row()
            check(
                "the meter actually moved during the demo",
                0 < used < budget,
                f"{used}/{budget} tokens",
            )
            # The honesty guard. A judge opening the URL cold is the most
            # likely viewer, and they must not see an estimate presented as
            # billed usage. Which way this flag falls decides a line of
            # narration, so it is reported loudly rather than merely asserted
            # — see DEMO.md, "what to say about the token counts".
            cold = await websockets.connect(f"{url}?user_id=judge")
            cold_snap = await snapshot(cold, "judge")
            await cold.close()
            estimated = cold_snap.get("usage_estimated")
            check(
                "a browser opening the URL cold is told where the counts came from",
                isinstance(estimated, bool),
                "ESTIMATED — the model was unreachable; say so on camera"
                if estimated
                else "REAL — provider-reported usage; drop the 'estimates' caveat",
            )
            check(
                "that cold browser renders the whole board from one frame",
                cold_snap["tokens_used"] == used
                and len(cold_snap["memory"]) == 1
                and len(cold_snap["agents"]) == 2,
                f"{cold_snap['tokens_used']} tokens, "
                f"{len(cold_snap['memory'])} fact, {len(cold_snap['agents'])} slots",
            )

        # Let bob's in-flight task finish so the board is quiet at the outro.
        await expect(bob, "agent_response", "bob",
                     where=lambda f: f.get("user_id") == "bob")
    finally:
        for ws in (alice, bob, charlie):
            await ws.close()


async def run_ceiling(url):
    """The budget-refusal beat. Needs its own near-spent board, so it is a
    separate take rather than part of the main sequence."""
    alice = await websockets.connect(f"{url}?user_id=alice")
    bob = await websockets.connect(f"{url}?user_id=bob")
    try:
        await snapshot(alice, "alice")
        await drain(alice)
        await drain(bob)

        with Beat("CEILING BEAT — the quota is a control, not a gauge"):
            # Spend the budget with real tasks, the way it happens on camera:
            # nothing is forced, the meter simply runs out. Looped rather than
            # assuming one task covers it — the cost of a task depends on what
            # the model actually returns, so it must not be hardcoded here.
            used, budget = metadata_row()
            for attempt in range(4):
                if used >= budget:
                    break
                await alice.send(json.dumps({
                    "action": "claim_agent", "agent_type": "coder",
                    "prompt": "draft the release notes for this sprint",
                }))
                await expect(alice, "agent_response", "alice",
                             where=lambda f: f.get("user_id") == "alice")
                await asyncio.sleep(2)
                used, budget = metadata_row()
            check(
                "real tasks drive the meter to the ceiling — nothing is forced",
                used >= budget,
                f"{used}/{budget} after {attempt + 1} task(s)",
            )

            before, _ = metadata_row()
            await bob.send(json.dumps({
                "action": "claim_agent", "agent_type": "coder",
                "prompt": "one more task",
            }))
            exhausted = await expect(bob, "budget_exhausted", "bob")
            # `>=`, not `==`. The last task to run before the ceiling always
            # overshoots it, because a task's cost is only known once it has
            # produced its response — there is no way to charge it in advance.
            # ws_smoke.py sees an exact `==` only because it forces the counter
            # to the ceiling; spending the budget for real is what surfaces
            # this, and the meter is on camera during the beat.
            check(
                "budget_exhausted is broadcast team-wide with the causing numbers",
                exhausted.get("tokens_used") >= exhausted.get("token_budget") > 0,
                f"{exhausted.get('tokens_used')}/{exhausted.get('token_budget')}",
            )
            check(
                "the overspent meter reads 100%, not 103% — the UI clamps it",
                exhausted.get("pct_used") is None
                or min(100.0, exhausted.get("pct_used", 0)) == 100.0,
                f"pct_used={exhausted.get('pct_used')} -> displays "
                f"{min(100.0, exhausted.get('pct_used', 100)):.1f}%",
            )
            refused = await expect(bob, "error", "bob")
            check(
                "the requester is told the agent was not invoked",
                "quota" in refused.get("message", "").lower(),
                refused.get("message", ""),
            )

            await asyncio.sleep(3)
            after, _ = metadata_row()
            check(
                "**not one token was spent on the refused task**",
                after == before,
                f"{before} -> {after}",
            )
    finally:
        for ws in (alice, bob):
            await ws.close()


def report():
    failed = [name for name, ok, _ in results if not ok]
    total = sum(t for _, t in timings)

    print("\n" + "=" * 68)
    print("BEAT TIMINGS")
    for label, elapsed in timings:
        print(f"  {elapsed:6.1f}s  {label}")
    print(f"  {total:6.1f}s  TOTAL product time")
    if total > DEMO_BUDGET_SECONDS:
        print(f"  ⚠  over the {DEMO_BUDGET_SECONDS}s allowance by {total - DEMO_BUDGET_SECONDS:.0f}s")
    else:
        print(f"     fits the {DEMO_BUDGET_SECONDS}s allowance "
              f"with {DEMO_BUDGET_SECONDS - total:.0f}s of headroom")

    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="wss:// endpoint (default: from stack output)")
    parser.add_argument("--takes", type=int, default=1,
                        help="rehearse this many times back to back")
    parser.add_argument("--ceiling", action="store_true",
                        help="rehearse the budget-refusal beat instead")
    parser.add_argument("--budget", type=int,
                        help="seeded token budget (default 5000, or 60 with --ceiling)")
    args = parser.parse_args()

    url = args.url or stack_output("WebSocketURL")
    # 500 for the ceiling take. A real task costs roughly 200-400 tokens, so
    # this lets the meter visibly climb across two tasks and then refuse the
    # third — the refusal reads as a control rather than an instant wall. Was
    # 60 while the agent was stubbed, at which a single real task now blows
    # straight past the ceiling and the climb is invisible.
    budget = args.budget or (500 if args.ceiling else STANDARD_BUDGET)
    beat = run_ceiling if args.ceiling else run_demo

    print(f"Endpoint: {url}")
    status = 0
    try:
        for take in range(1, args.takes + 1):
            print(f"\n{'=' * 68}\nTAKE {take} of {args.takes}\n{'=' * 68}")
            results.clear()
            timings.clear()
            try:
                reset(budget)
                asyncio.run(beat(url))
            except Exception as exc:
                check(f"rehearsal aborted: {type(exc).__name__}", False, str(exc))
            status = report()
            if status:
                print(f"\nTAKE {take} FAILED — fix this before recording.")
                break
            print(f"\nTAKE {take} clean.")
        else:
            print(f"\nAll {args.takes} take(s) ran clean, unattended.")
    finally:
        restore_standard_budget(budget)

    sys.exit(status)


if __name__ == "__main__":
    main()
