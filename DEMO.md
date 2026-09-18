# DEMO.md — the recording run sheet

Everything needed to record the 3-minute video in one take. **No new features from here.**

The sequence below is rehearsed automatically and passes 12/12 against deployed AWS:

```bash
python scripts/rehearse.py --takes 2
```

Run that first. If it fails, do not record — fix what it names.

---

## Before you hit record

```bash
./scripts/reset-demo.sh          # clean board, SQS drained, Lambdas warm, verified
```

It prints `snapshot clean — both slots IDLE, 0/5000 tokens, queue and memory empty`. If it
prints `DIRTY`, or warns that connection rows were live, **close every browser tab pointed at
the deployed URL and run it again.**

Then:

- [ ] `rehearse.py --takes 2` passed within the last hour
- [ ] Three browser windows, **640×950 each**, side by side. The HUD is built for this; the
      board is 933 px tall with the memory panel showing, so it fits without scrolling. (It was
      640×880 before the workspace floor landed — a shorter window now cuts off the activity
      log, which still scrolls but is no longer fully visible.)
- [ ] Each window is a **separate browser or profile**. The entry gate persists to
      `localStorage['hiveos.identity']`, so two tabs of the same origin share one identity and
      you will end up with three "Alice"s.
- [ ] Identities entered: **alice 🐝**, **bob 🦊**, **charlie 🦉**
- [ ] All three show the same meter — `0 / 5000` — and both slots IDLE
- [ ] **Browser extensions disabled, or record in a clean profile.** Grammarly injects a
      floating icon *into the prompt textarea* and it is clearly visible on camera. Found while
      verifying the deployed page — it was the only thing in the console, and the only thing on
      screen that is not HiveOS.
- [ ] Beat 4 of `rehearse.py` printed **`REAL — provider-reported usage`**. If it printed
      `ESTIMATED`, the model was unreachable: the board still works but the counts are
      heuristics, and the narration has to say so. Fix it before recording rather than
      explaining it on camera.
- [ ] Screen recorder capturing all three windows
- [ ] Notifications silenced

The reset takes ~8 seconds. Between takes, run it again — it is idempotent.

---

## The take

Product time is ~15 seconds; the rest is narration over a live board. Rehearsed timings are in
brackets.

### 0:00–0:25 · The problem

Talking over the three windows, before touching anything.

> Uber burned through its 2026 AI budget in four months. 79% of enterprises had overruns last
> year; only 36% have any real-time control. Teams share AI agents with no visibility, no
> fairness, and no way to stop a single heavy task draining the month. Operating systems solved
> this for CPU fifty years ago — scheduling, quotas, fair queueing. HiveOS applies that to a
> team's shared AI compute.

If asked how this differs from a local agent harness: **that governs one developer's own CLI
agents on their own machine; this is a cloud governance layer for a team sharing one budget.**

### 0:25–0:45 · Three browsers, one workspace

Click once on the **workspace floor** in each window, so each marker visibly moves on the other
two screens. This is the cheapest possible proof that the three windows are one live board —
much stronger than pointing at three identical numbers, which a viewer could assume were
screenshots.

> Three people, one workspace, one budget. Same meter, same slots, same queue — on every
> screen, live. When I move here, it moves there.

Keep it to one click each — the floor is the opening handshake, not the point of the demo.

### 0:45–1:30 · The queue moment `[~1s of product time]`

1. **Alice** requests an agent with the prompt:
   ```
   remember: deploy window = Friday 16:00 UTC
   ```
   → `coder` goes BUSY on **all three** screens *(rehearsed: 282–310 ms)*

2. **Bob** requests an agent — any prompt, e.g. `summarise yesterday's incident review`
   → `researcher` goes BUSY. Both slots now full.

3. **Charlie** requests an agent with:
   ```
   when is our next deploy?
   ```
   → Charlie gets **queue position 1**, and it is visible on Alice's and Bob's screens too.

> Both slots are busy, so Charlie doesn't get a failure and he doesn't get a spinner — he gets
> a real position in line, and the whole team can see he's waiting.

**Say this, it is the accurate wording:** *every agent task runs through a real SQS queue, and
waiting tasks auto-dispatch the moment a slot frees.*

**Do not say** that Charlie is "parked inside SQS" — he is not. The queue is gated in DynamoDB;
SQS is the durable at-least-once handoff for tasks that are actually running.
(`ARCHITECTURE.md` decision 1.)

### 1:30–2:15 · The memory moment `[~10s of product time]`

This runs itself. Alice's task finishes, and three things happen in sequence — let them land.

1. A **toast** fires on all three screens and Alice's fact appears in **team memory**,
   attributed to her — this lands as her task *starts*, so give it a beat before the rest
2. Her slot frees → **Charlie is auto-dispatched into it** *(rehearsed: 64–84 ms)*, and
   Charlie's marker on the floor picks up its working indicator
3. ~6 seconds later Charlie's response arrives — **already carrying Alice's fact**

> Alice saved one fact for the team. Her slot frees, Charlie is dispatched automatically — he
> never asked twice — and his agent already knows the deploy window. Nobody told it. That's
> shared memory across a team's agents.

The meter has moved to roughly **980 / 5000** — about 20%, so it is unmistakably visible.

### 2:15–2:45 · Where AWS fits

> API Gateway WebSocket for the live board. Lambda for the router and the agent runner. SQS as
> the durable task handoff, with a dead-letter queue. DynamoDB as a single-table store, with
> atomic conditional writes doing the slot claiming and atomic counters doing the token
> accounting. Amplify hosts the frontend. All serverless, scaling to zero.

**Then say the inference sentence — once, plainly, do not bury it:**

> One honest note: Bedrock is quota-blocked on this AWS account, so model inference calls out
> to Groq. Everything else you just saw is AWS. And the token counts are real — they're the
> usage the provider reports, not an estimate.

**Do not say the agent is stubbed. It is not — that changed, and the counts are now real.**
The old caveat undersold a working system; saying it now would be false. If the model is ever
unreachable mid-take the workspace still answers, but from composed text, and every count it
produces is flagged `estimated` on screen. `rehearse.py` prints which mode the board is in —
`REAL` or `ESTIMATED` — in Beat 4. Check it before you record.

### 2:45–3:00 · What was learned

> The hard part wasn't the AI. It was making three browsers agree on one number, ordering the
> frames so a slot never looks stuck, and making a budget ceiling that actually refuses instead
> of just turning red.

---

## Optional: the ceiling beat

Worth 15 seconds if the edit has room. It needs its own near-spent board, so it is a **separate
take** — do not try to reach the ceiling during the main sequence.

```bash
TOKEN_BUDGET=500 ./scripts/reset-demo.sh
```

Run two tasks. The second tips the meter over; the next request is **refused before the agent
is invoked** and `budget_exhausted` goes out team-wide.

> At the ceiling the agent is not called at all. Not throttled, not queued — not invoked. Zero
> tokens spent. That's the difference between a quota and a gauge.

The meter displays **100.0%** and **0 remaining**, even though the underlying count overshoots
(rehearsed at 675 of 500) — a task's cost is only known once it has run, so it cannot be
charged in advance, and the UI clamps. Rehearse it with `python scripts/rehearse.py --ceiling`.

**500, not 60.** 60 was sized for the stub's ~58-token tasks. A real task costs 200–400, so at
60 the very first one blows straight past the ceiling and the climb never appears on camera.

---

## If something breaks mid-take

From `BUILD_PLAN.md`'s fallback ladder. **Fix only what is broken — never add a feature to
rescue a demo.**

| Symptom | Do this |
|---|---|
| A slot looks stuck BUSY | `./scripts/reset-demo.sh`, start the take over |
| A browser shows a stale board | Reload it — the client re-syncs from `state_snapshot` |
| Member count looks wrong | A stray tab is connected. Close it, reset, re-record |
| WebSocket won't connect | Record the components separately rather than abandoning |
| Everything is slow on the first click | Lambdas went cold — rerun `reset-demo.sh` to warm them |

---

## After recording

1. Upload to YouTube, **public or unlisted**
2. **Open the link in a signed-out browser** — an accidentally-private video is a zero
3. Confirm the video is **under 3:00**
4. Confirm the public URL still opens cold: <https://main.dbavt8jr66qxx.amplifyapp.com>
5. Submit with the writeup in `SUBMISSION.md`
