# PROGRESS.md

> Current execution state. A fresh Claude Code session reads this to know exactly where things stand.
> Keep it operational and short. Not a diary — history lives in git.

**Last updated:** 2026-09-18

---

## Project status

| | |
|---|---|
| **Project** | HiveOS — OS-style scheduler for a team's shared AI agent budget |
| **Track** | Ship It (deployed, public URL) |
| **Deadline** | 2026-09-20 |
| **Current phase** | **Phase 6 — demo readiness** (Phase 3 closed out 2026-09-18: the model call is live) |
| **Phase status** | `BLOCKED — WAITING FOR MANUAL ACTION`. Everything buildable is done and rehearsed, Phase 5 included; **recording, upload and submission are the user's** |
| **Deployment state** | Stack `hiveos` live in `us-east-1`. DynamoDB + WebSocket API + Router + SQS/DLQ + Agent Runner. Frontend live on Amplify. |
| **🌐 Public URL** | **https://main.dbavt8jr66qxx.amplifyapp.com** — verified cold, zero setup |
| **WebSocket endpoint** | `wss://mel2gpat9c.execute-api.us-east-1.amazonaws.com/prod` |
| **Amplify app** | `dbavt8jr66qxx`, branch `main` — **not in CloudFormation** (see below) |
| **Repository** | https://github.com/arunishrajput/hiveos (public, `main`) |
| **AWS account** | `890608337320` · `us-east-1` · IAM user `hiveos-dev` (AdministratorAccess) |

> **There is a submittable deliverable as of now.** The Phase 4 gate — a deployed public URL
> showing live shared state — is met. Everything remaining is enhancement.

---

## Phase board

| # | Phase | Status |
|---|---|---|
| 0 | Pre-project setup | `COMPLETE` (Bedrock abandoned — see below) |
| 1 | WebSocket backbone | `COMPLETE` |
| 2 | Scheduler + queue (no LLM) | `COMPLETE` |
| 3 | Model + agent + memory | `COMPLETE` — real model, real provider-reported token counts, enforced ceiling |
| 4 | Frontend HUD + public URL | `COMPLETE` |
| 5 | Agent chat + 2D canvas (cuttable) | `COMPLETE` — **un-cut** by user decision, 2026-09-18 |
| 6 | Demo readiness | `BLOCKED — WAITING FOR MANUAL ACTION` — tasks 1–5 and 8 done; 6, 7, 9 are the user's ← **here** |

**Phase 3 is complete as of 2026-09-18**, but not as planned — Bedrock was abandoned, not
integrated. See *Blocked* below for the evidence, and `ARCHITECTURE.md` decision 7 for the
reasoning.

| Phase 3 task | State |
|---|---|
| 1. Bedrock IAM permissions | **not done, and correctly so.** Bedrock is never called, so granting it would be an unused permission. `template.yaml` records where it would go if Bedrock is ever unblocked. |
| 2. Strands SDK vs boto3 `converse` | **neither.** Both assumed Bedrock. `shared/llm.py` calls Groq with one stdlib `urllib` POST — no SDK, nothing to package. |
| 3. The memory tools | `get_team_memory` / `set_team_memory` **done**. `get_task_context` deliberately not built — no task history exists to return; see `CONTRACT.md`. |
| 4. Load memory before the call | **done** — and verified crossing between users against the deployed URL |
| 5. Token accounting + `token_update` | **done, and the numbers are now real** — `total_tokens` as the provider reports it, `estimated=False` |
| 6. Budget ceiling + `budget_exhausted` | **done, enforced, verified** — rehearsed at 675/500, refused, not one token spent |
| 7. Cap `max_tokens` per call | **done** — `MAX_TOKENS_PER_CALL=400`, a backstop rather than a shaper |

**What is still not a real agent:** a fact is saved by the `remember: k = v` prompt convention,
not by the model deciding to call `set_team_memory`. Tool calling was judged not worth a second
round trip and a new failure surface against the deadline, with everything else green. This is
the only place the agent differs in *kind* from the Phase 3 design.

---

## Completed

**Phase 3 closed — 2026-09-18 — real model inference on Groq, Bedrock abandoned**

User decision, after one last verification probe: *"if bedrock is not doable then drop it, we
will use anything else."* Bedrock was re-tested first, per this file's own standing instruction
— `us-east-1`, `us-west-2` and `ap-south-1`, both vendor classes, all still refusing. Provider
chosen for zero payment friction, which is what had already cost this project a phase.

- **`backend/shared/llm.py`** — one `urllib` POST. No SDK, so `sam build` has nothing extra to
  package and there is no compiled wheel to resolve against a local Python 3.14 that does not
  match the runtime.
- **The key is an SSM SecureString read at runtime**, cached per container, IAM-scoped to that
  one parameter. Never in `template.yaml`, the stack, an env var, or git. A redeploy cannot wipe
  it, and it goes live without one — the runner retries the read on every task until it succeeds.
- **The stub is retained as an automatic fallback**, flagged `estimated`. A provider blip
  degrades the answer instead of breaking the workspace mid-take.
- `MIN_SLOT_SECONDS` (4s) replaces `STUB_DELAY_SECONDS` (5s) — it pads the **slot**, not the
  model, because a 1.5s call would leave BUSY and a queue position illegible on camera.

**Verified against deployed AWS and the public URL, not exit codes:**

| Check | Result |
|---|---|
| `python scripts/ws_smoke.py` | ✅ **58/58** |
| `python scripts/rehearse.py --takes 2` | ✅ 12/12, twice, unattended |
| `python scripts/rehearse.py --ceiling` | ✅ 5/5 — 675/500, clamped to 100%, **nothing spent** |
| Token provenance | ✅ `estimated=False`, provider-reported `total_tokens` |
| **Memory crosses users with a real model** | ✅ *"Your next deploy is scheduled for Friday at 16:00 UTC"* |
| Real task driven from the public URL in a browser | ✅ 378 tokens, no `~` prefix, answer used the team fact |
| Zero app console errors on the deployed page | ✅ (three warnings, all from the Grammarly extension) |
| Auto-dispatch latency, correctly anchored | ✅ **64–84 ms** |

**Four real failures, each found by running it rather than reading it:**

1. **Cloudflare answers urllib's default User-Agent with HTTP 403 `error code: 1010`.** It looks
   exactly like a rejected API key and is not one. Only reading the response *body* separated
   them. Any honest `User-Agent` gets through.
2. **`llama-3.3-70b-versatile` no longer exists on Groq.** The name this was first written
   against was already retired, and a wrong model fails at **runtime**, not at deploy. Now
   listed from `GET /v1/models` rather than guessed twice.
3. **Editing a template `Default:` does not change a deployed stack.** CloudFormation keeps
   existing parameter values on update, so a clean `sam deploy` left the Lambda still calling the
   dead model while the template said otherwise. Pinned in `samconfig.toml`'s
   `parameter_overrides`.
4. **Three test assertions encoded the stub's behaviour rather than the system's guarantee.**
   Two asserted `estimated is True`; the Phase 3 memory gate asserted the literal substring
   `Friday 16:00 UTC` and failed when the model wrote *"Friday **at** 16:00 UTC"* — while the
   memory load was perfectly correct. Rewritten as the real invariants, which hold in both modes
   and are strictly stronger.

**One ordering change with a consequence worth recording.** Saving a fact now happens *before*
the model call (so it persists even when the model is unreachable, and lands in its own call's
context) rather than after a stub's sleep. That made `memory_updated` overtake the frame
`rehearse.py` was waiting on — and its `expect()` **discarded** what it read, so the frame was
gone by the time the next assertion looked for it. It now buffers per socket. Arrival order is
the only thing a broadcast harness is not entitled to assume, and this is the second time that
exact trap has cost time.

**A measurement broke silently in the same change.** Dispatch latency was anchored on
`memory_updated`, which now fires at task *start* — so it reported 3,454 ms for what is actually
a 64–84 ms dispatch. Re-anchored on the slot's IDLE broadcast. A number that merely gets worse,
rather than failing, is the kind a green test will happily carry onto a slide.

**Phase 5 — 2026-09-18 — workspace floor, team chat, toasts**

**Phase 5 was previously marked "cut" and that call was wrong.** It was cut on a time-pressure
argument, but the deadline is 2026-09-20 and every MVP-Critical item was already built and
verified — which is exactly the condition `CLAUDE.md` says MVP-Supporting work should be built
under. `BUILD_PLAN.md` says to cut Phase 5 *"if Phase 3 or 4 overran"*; Phase 3 is blocked on an
AWS account restriction, which is not the same thing as overrunning. Un-cut on user decision.

- **`move_avatar` end to end.** Client action → `CONN#` row update → `avatar_moved` broadcast.
  The action was in `CONTRACT.md` since Phase 0 but had no handler and, like `send_message`
  before it, **no matching server event** — `avatar_moved` is now specified.
- `state.spawn_point()` — scatters people on join, derived from a hash of the connection ID.
  Everyone previously spawned at `(0, 0)`, which stacked every marker in one corner.
- `state.move_connection()` — conditional on the row existing, so a move racing `$disconnect`
  cannot resurrect a dead connection as a ghost member.
- **`CanvasPanel`** — absolutely positioned markers in a fixed-ratio box, moved with a CSS
  transition. No canvas element, no animation loop, no game engine. Click or arrow keys.
  Coordinates are percentages, so every window agrees regardless of width.
- **`ChatComposer`** — `send_message` has been broadcasting since Phase 1, but nothing in the UI
  could send one, so the activity log could render a `chat_message` no client could produce.
- **`ToastStack`** — memory-saved and quota-reached toasts, auto-expiring after 4.5 s.
- Per-task token cost and the "thinking" pulse already existed from Phase 4; no work needed.

**Verified against the deployed public URL, not exit codes:**

| Check | Result |
|---|---|
| `python scripts/ws_smoke.py` — now includes an avatar section | ✅ **58/58** |
| `python scripts/rehearse.py --takes 2` — no demo regression | ✅ 12/12, twice |
| A move painted on a **second, observing browser** | ✅ **270–294 ms** |
| Three markers render, positioned, one per person | ✅ |
| A marker shows `busy` exactly while its owner holds a slot | ✅ |
| Memory toast fires on a browser that did not save the fact | ✅ |
| Toast auto-expires (~4.5 s) | ✅ |
| Zero console errors or warnings on the deployed page | ✅ |
| Out-of-range coordinates clamp; non-numeric ones are refused | ✅ |

**Two real bugs found by running it rather than reading it:**

1. **`Decimal(24.92)` raises `decimal.Inexact` inside boto3 — moves were silently lost.**
   Building a `Decimal` from a float carries the full binary expansion
   (`24.920000000000001705…`) and boto3's `DYNAMODB_CONTEXT` refuses it rather than rounding.
   The mover's own optimistic UI still moved, so they appeared somewhere **nobody else saw
   them** — the exact divergence the board is supposed to be incapable of.
   Fixed with `Decimal(str(x))`.

   **The verification that missed it is the instructive part.** The first test used `73.5` and
   `21.25` — deliberately fractional, and both *exactly* representable in binary floating point,
   so they passed. Only a real mouse click produced a coordinate that was not.
   `ws_smoke.py` section 19 now asserts on `24.92` specifically.

2. **`overflow: hidden` clipped the chat and activity log off the board entirely.** Adding the
   floor pushed the panel stack to 1055 px against a 880 px window. Pinning the board to
   `height: 100dvh; overflow: hidden` made `scrollHeight === innerHeight` report success — while
   silently making two panels **unreachable**, which is strictly worse than scrolling to them.
   Caught by looking at a screenshot, not by the measurement that said it was fine.

   Fixed properly: the board grows naturally, the run queue and team memory sit **side by side**
   (two short lists, one row instead of two), and the floor is 100 px. The board is now **933 px**
   and fits a **640×950** window with nothing clipped.

**The demo window size changed: 640×950, not 640×880.** `DEMO.md` and the gotchas below are
updated. On a 1080p screen three browsers at that size still sit side by side.

**Phase 6 — 2026-09-18 — demo readiness (everything except the recording itself)**

No new features, per `BUILD_PLAN.md`. Two scripts and two documents:

- `scripts/reset-demo.sh` — the one command to run between takes. Wraps `seed.sh` (which still
  owns the DynamoDB demo state — duplicating it would let the two definitions drift) and adds
  the four things that are invisible until they are on camera: **stale `CONN#` rows** from a
  crashed tab, **in-flight SQS messages** from the previous take, **cold Lambdas**, and
  **verification**. It defaults to `TOKEN_BUDGET=5000`.
- `scripts/rehearse.py` — drives the recorded sequence as three WebSocket clients and
  wall-clock times every beat. Distinct from `ws_smoke.py` on purpose: `ws_smoke` asks *is the
  system correct*, this asks *does the sequence I am about to record work, in that order, in
  the time I have*. Every assertion is something a viewer can see on screen.
- `DEMO.md` — the run sheet: pre-flight checklist, beat-by-beat narration, the exact wording
  for the SQS claim, the stub sentence, and the mid-take fallback table.
- `SUBMISSION.md` — the writeup (`BUILD_PLAN.md` task 8), with the video link left blank.

**Rehearsed against deployed AWS — `python scripts/rehearse.py --takes 2`, 12/12 twice:**

| Check | Result |
|---|---|
| The token meter reads identically on all three screens | ✅ |
| Every screen lists all three members | ✅ |
| Alice's claim lands on a bystander's screen | ✅ 282–310 ms |
| The third request gets a real queue position, not a failure | ✅ position 1 |
| The whole team sees Charlie waiting — the queue is shared state | ✅ |
| Alice's fact reaches a teammate's screen, attributed to her | ✅ |
| **Charlie is auto-dispatched into the freed slot** | ✅ 187–234 ms |
| **Charlie's agent already knows Alice's fact — nobody told it** | ✅ |
| A browser opening the URL cold is told the counts are estimates | ✅ |
| That cold browser renders the whole board from one frame | ✅ |

Ceiling beat, separately (`--ceiling`, 5/5): real tasks drive the meter to the ceiling, the
next request is refused before the agent is invoked, and **not one token is spent on it**.

**Total product time is ~14 seconds** across all four beats — 96 s of headroom inside the
110 s allowance. The demo is not time-constrained; the narration is.

**The beat order changed, and the change matters.** `PROGRESS.md`'s earlier run sheet had Alice
claim a slot and *then* save a fact. She cannot — the scheduler refuses a second concurrent
claim from the same user, so that is two rounds and ~15 extra seconds. Folding the save into
her claim prompt makes the fact land at the exact moment her slot frees and Charlie is
dispatched into it, which is `BUILD_PLAN.md`'s beat and one continuous shot. `DEMO.md` has the
corrected order.

**Two traps found by rehearsing rather than by reading:**

1. **`--ceiling` left a 60-token budget behind and broke the next `ws_smoke.py` run.**
   `ws_smoke` resets `tokens_used` but never `token_budget`, so it inherited the tiny ceiling
   and failed two checks for reasons unrelated to the code — one of them a confusing
   *"a second concurrent claim is refused"* failure that was really a quota refusal. Fixed:
   `rehearse.py` restores the standard budget in a `finally`, including on the failure path.
2. **Bare `reset-demo.sh` seeded 1,000,000.** It inherited `seed.sh`'s production-shaped
   default, at which a ~58-token task moves the meter 0.006% — invisible, which is the one
   thing the recording cannot afford, and `DEMO.md` tells the operator to run it bare. The
   demo-facing script now defaults to 5,000; `seed.sh` keeps its generic default.

Neither was a product bug, and `python scripts/ws_smoke.py` is **49/49** with the board
correctly seeded. But both would have cost real time at 2 a.m. the night before a deadline.

**Not done — these are the user's, and Phase 6 is not complete until they are:**
recording the take, uploading to YouTube and verifying it signed-out, and submitting.
See *Manual actions pending*.

**Phase 3 without Bedrock — 2026-09-18 — memory, token accounting, enforced ceiling**

- `backend/shared/memory.py` — `facts()`, `as_context()`, `remember()`, `directive()`.
- `MEMORY#` rows keyed by a **slug of the fact's key**, not a UUID, so a re-save is an upsert.
  The UUID the contract originally specified made `set_team_memory` non-idempotent and produced
  duplicate facts in the snapshot. `CONTRACT.md` corrected.
- `state.budget_state()` / `state.add_tokens()` / `state.pct_used()` — atomic `ADD` with
  `ALL_NEW` so the broadcast and the row can never disagree.
- Agent Runner: ceiling check before the agent runs, memory loaded before the agent runs,
  token accounting after, `token_update` + `agent_response` + `memory_updated` broadcasts.
- Token provenance (`estimated` / `usage_estimated`) so no client can present an estimate as
  billed model usage — including a client that loaded cold.
- Demo budget seeded at **5,000** (`TOKEN_BUDGET=5000 ./scripts/seed.sh`), see below.

**Verified against deployed AWS — `python scripts/ws_smoke.py`, 49/49:**

| Check | Result |
|---|---|
| Saving a fact broadcasts `memory_updated` team-wide, attributed to its author | ✅ |
| `MEMORY#` row written; SK derived from the key | ✅ |
| **Another user's later agent already knows the fact without being told** | ✅ Phase 3 gate |
| Re-saving a key upserts — the team still knows exactly one deploy window | ✅ |
| `token_update` carries the new total and budget; DynamoDB agrees | ✅ |
| The increment equals the cost the response reported — no lost increment | ✅ |
| `pct_used` matches `tokens_used / token_budget` | ✅ |
| A cold client's snapshot also reports the usage as estimated | ✅ |
| `budget_exhausted` broadcast with the numbers that caused it | ✅ |
| **At the ceiling the agent is genuinely not invoked — not one token spent** | ✅ Phase 3 gate |
| A refused task still releases its slot — no leak on the refusal path | ✅ |

Confirmed live on the public URL too: the meter ticks (0 → 42 → 109 tokens), the memory panel
appears with the fact, and a later task's response contains the loaded context.

**The honesty problem, and what was done about it.** The stub spends no real tokens, so a
meter that moved would be reporting invented numbers — on a product whose entire pitch is
token governance. Three things keep it straight:

1. The count is `len(prompt + context + response) / 4` over the **real** strings — the standard
   heuristic, not a fabricated figure.
2. Every frame carrying a count sets `estimated`, and the snapshot carries `usage_estimated`
   for clients that loaded cold. The UI says *"estimated, the agent is stubbed"* next to the
   number and prefixes per-task costs with `~`.
3. `README.md` and this file say so in plain text.

**A hole in that safeguard was found and fixed during verification.** The `estimated` flag
originally rode only on live `token_update` frames, so a browser loading the public URL cold —
which is *every judge* — saw an unlabelled total that was in fact estimated. Fixed by
persisting `usage_estimated` on the METADATA row and returning it on `state_snapshot`. There is
now a smoke-test check for exactly that case.

**Why the demo budget is 5,000.** At 1,000,000 a ~68-token stub task moves the meter 0.007% —
invisible. The strip is ~68 segments, so one segment is 1.48%; a 5,000 budget puts one task at
1.36%, i.e. almost exactly one segment per task. This is not a trick to inflate the numbers:
5,000 with 68-token tasks is the same *fraction of the meter* that 1,000,000 would be with
13,600-token tasks, which is the order `PRD.md` cites for agentic workloads. Change it with
`TOKEN_BUDGET=<n> ./scripts/seed.sh`; nothing in code hardcodes it.

**Phase 4 — 2026-09-18 — Frontend HUD and public URL**

- Vite + React app in `frontend/`. No Tailwind, no component library, no router, no state
  library — React plus one CSS file. 74 KB gzipped JS, 444 KB total.
- `src/useHive.js` — the WebSocket client. Owns all board state, applies every server event in
  `CONTRACT.md`, reconnects with 1/2/4/8s backoff, re-sends `hello` on reconnect.
- `src/components.jsx` — quota strip, slot cards, run queue, activity log, team memory.
- `src/App.jsx` — entry gate (name + marker, persisted to localStorage), request form, layout.
- `scripts/deploy-frontend.sh` — idempotent Amplify manual-mode deploy. Resolves the WebSocket
  URL from the stack output, builds, **greps the bundle to prove the URL actually got baked in**,
  zips, finds-or-creates app and branch, uploads, starts, and polls to `SUCCEED`.
- Amplify app `dbavt8jr66qxx` created with an SPA rewrite (`/<*>` → `/index.html`, 404-200).

**Verified against the deployed public URL, not exit codes:**

| Check | Result |
|---|---|
| Public HTTPS URL opens cold with zero setup | ✅ |
| Board renders entirely from one `state_snapshot` | ✅ |
| Zero console errors or warnings on the deployed page | ✅ |
| **Claim propagates to a second browser in 282 ms** (gate allows ~2 s) | ✅ Phase 4 gate |
| Same, measured localhost → public Amplify origin | ✅ 282 ms |
| Both slots BUSY + a real server-assigned queue position rendered | ✅ |
| **Auto-dispatch visible in the UI 160 ms after a slot freed** | ✅ Phase 4 gate |
| Meter thresholds at 49.9/50.0/80.0/80.1/100 % → jade/amber/amber/coral/coral | ✅ |
| Quota strip fill stays tick-aligned with the track at every width | ✅ |
| `python scripts/ws_smoke.py` — backend regression | ✅ 29/29 |

Measured by installing a 20 ms DOM sampler in the *observing* browser and comparing its
absolute timestamps against the acting browser's click — so the figures are click-to-paint
across two clients, not a server-side round trip.

**Two real frontend bugs found and fixed:**

1. **A user dropped themselves from their own member list.** Membership is per-connection
   server-side (one `CONN#` row each), but `user_left` carries only `user_id`. Two connections
   for one person collapsed into a single member entry, so closing one tab removed them
   entirely while their own socket was still live. The member count is on screen for the whole
   recording, so this would have shown. Fixed by deduping members by `user_id` and adding
   `user_joined`/`user_left` to the re-sync set so the authoritative snapshot corrects any
   collapse.

2. **The queue ETA was erased ~500 ms after appearing.** `queue_update` carries
   `estimated_wait_seconds`; `state_snapshot.queue[]` does not (`state.py` `queue_view`,
   `CONTRACT.md`). The debounced re-sync therefore overwrote a correct `~8s` with `—`. Caught
   on the timestamped trace at exactly the 460 ms mark. Fixed by deriving the ETA from
   `queue_position` client-side — the server's figure is exactly
   `position * ESTIMATED_TASK_SECONDS`, so this is equivalent rather than an approximation, and
   it additionally gives a user who *reconnects while queued* an ETA the snapshot alone could
   not supply.

**Phase 2 — 2026-09-18**

- SQS `hiveos-agent-tasks` + `hiveos-agent-tasks-dlq` (maxReceiveCount 5, visibility 360s)
- Agent Runner Lambda `hiveos-agent-runner`, SQS event source, `BatchSize: 1`
- `backend/shared/scheduler.py` — one slot state machine used by both Lambdas
- `claim_agent`: atomic conditional claim, falls back across slots, else writes `QUEUE#`
- `release_agent` + automatic release in the runner's `finally`
- Auto-dispatch of the oldest queued task on every release
- Stub agent — 5s, canned text, **zero tokens**; `__hiveos_fail__` injects a failure
- `scripts/ws_smoke.py` extended to 29 checks (sections 7–12 are the Phase 2 gate)

**Verified against deployed AWS, not exit codes** — `python scripts/ws_smoke.py`, 29/29,
run 3 consecutive times clean:

| Check | Result |
|---|---|
| First claim takes `coder`; second falls back to `researcher` | ✅ |
| DynamoDB confirms both slots BUSY with the right holders | ✅ |
| **Third claim writes a `QUEUE#` row and broadcasts position 1** | ✅ Phase 2 gate |
| Queue position is broadcast team-wide, not just to the queued user | ✅ |
| **Queued task auto-dispatches into the freed slot** | ✅ Phase 2 gate |
| Queue empties in DynamoDB; every slot returns to IDLE | ✅ |
| **A raising task still releases its slot — no leak** | ✅ Phase 2 gate |
| Failing task reports `error` to its requester | ✅ |
| A user's second concurrent claim is refused | ✅ |
| DLQ empty, main queue empty, no `CONN#`/`QUEUE#` rows leaked | ✅ |

**Real bug found and fixed — frame ordering.** `claim_agent` dispatched to SQS *before*
broadcasting `agent_state_update` BUSY, so a fast-failing task posted its reply ahead of
the BUSY frame. A client would then apply BUSY *after* the IDLE release and show a slot
stuck BUSY for the rest of the demo. Failed ~50% of runs; found from the delivered=True
log proving the backend sent it, which ruled out delivery and left ordering. Both
`_claim_agent` and `release_and_dispatch` now broadcast before dispatching.
`CONTRACT.md` records the guaranteed sequence.

**Phase 1 — 2026-09-18**

- WebSocket API `hiveos-ws` + Router Lambda `hiveos-router` in `template.yaml`
- `$connect` writes a `CONN#` row and broadcasts `user_joined`; `$disconnect` deletes it and broadcasts `user_left`
- `backend/shared/broadcast.py` — fan-out with the mandatory GoneException branch
- `backend/shared/state.py` — single-table access, Decimal-safe JSON, `state_snapshot()`
- `$default` handles `hello` (→ `state_snapshot`) and `send_message` (→ `chat_message`)
- `execute-api:ManageConnections` granted on the Router role
- `scripts/ws_smoke.py` — 14-check end-to-end harness against deployed AWS
- `samconfig.toml` created and committed (was missing; see below)

**Verified against deployed AWS, not exit codes** — `python scripts/ws_smoke.py`, 14/14:

| Check | Result |
|---|---|
| `state_snapshot` has every field a cold client renders from | ✅ |
| Snapshot reports both slots `IDLE`, budget `0/1000000` | ✅ |
| Two clients connected; `user_joined` delivered to the other | ✅ |
| **One client's message reached both clients** | ✅ Phase 1 gate |
| Sender resolved from the `CONN#` row, not the frame | ✅ |
| **Broadcast past a dead connection still delivered to live clients** | ✅ Phase 1 gate |
| **GoneException branch deleted the stale `CONN#` row** | ✅ Phase 1 gate |
| Unknown action / malformed JSON return `error`, socket survives | ✅ |
| No `CONN#` rows leak after everyone disconnects | ✅ |

CloudWatch confirms the branch fired rather than the test merely passing:

```
[broadcast] gone connection=gaylUe9avQAYKEixkA== — deleting CONN# row
[broadcast] event=chat_message delivered=2 stale=1
```

No traceback anywhere in the run.

**Phase 0 — 2026-09-18** (commits `32fd214`, `f303cba`)

- Repository scaffold: 8 docs + SAM template
- AWS credentials configured; root keys retired in favour of IAM user `hiveos-dev`
- Anthropic use-case form submitted and confirmed cleared
- SAM CLI 1.166.2 installed; Docker 29.7.2 running
- Stack `hiveos` deployed to `us-east-1` — **deployment pipeline proven**
- `scripts/seed.sh` — idempotent seed/reset for demo state
- Seeded `TEAM#alpha` metadata + both agent slots `IDLE`
- AWS Budget `hiveos-guardrail` ($20, 80% alert)
- Public GitHub repo created and pushed

**Verified against deployed AWS, not exit codes:**

| Check | Result |
|---|---|
| `sts get-caller-identity` | `user/hiveos-dev` |
| `sam validate --lint` | valid |
| CloudFormation stack status | `CREATE_COMPLETE` |
| `put-item` / `get-item` round trip | item returned correctly |
| Atomic `ADD tokens_used` | returned new total `1234` — `CONTRACT.md` pattern works |
| `seed.sh` re-run | idempotent, table state correct |

---

## Blocked

| Blocker | Blocks | Status |
|---|---|---|
| **Bedrock unusable — account-level zero quotas; card added and did NOT fix it** | *Nothing any more* | **Closed 2026-09-18 — abandoned, not fixed.** Re-verified in three regions, then inference moved to Groq. Post-hackathon AWS Support ticket at most. |

### What the Bedrock blocker cost, and what closed it

**Closed 2026-09-18.** It cost the Phase 3 ordering and roughly a day of diagnosis. It no longer
costs the demo anything: the agent is real, the token counts are the provider's own, and every
beat in the demo script is demonstrable with nothing caveated away.

The one honest line the video must still carry: **inference runs on Groq because Bedrock is
quota-blocked on this account; everything else is AWS.** `DEMO.md` has the wording. Do **not**
say the agent is stubbed — that was true until 2026-09-18 and is now false.

The diagnosis below is kept because it is the evidence for that sentence, and because a future
session must not waste hours re-testing Bedrock hoping for a different answer. One `converse`
call is enough to detect if it ever unlocks.

### ⛔ Card added 2026-09-18 — did not unblock Bedrock

The user added a card. Verified in the console: **Visa •••• 3306, set as Default**; UPI AutoPay
removed; billing address and contact email updated. The payment instrument is genuinely fixed.

**Bedrock did not change.** Re-tested ~25 minutes after the card landed, well past the
"try again after 2 minutes" window AWS's own error suggests:

| Test | Result |
|---|---|
| Anthropic Haiku 4.5, us-east-1 | `INVALID_PAYMENT_INSTRUMENT` — unchanged |
| Anthropic Haiku 4.5, us-west-2 | `INVALID_PAYMENT_INSTRUMENT` — identical |
| Amazon Nova Lite / Micro, us-east-1 | `ThrottlingException: Too many tokens per day` |
| Nova Lite without inference profile | `ThrottlingException` |
| Per-day token quotas | **still 42 of 43 at zero, 0 adjustable** |
| Marketplace active subscriptions | still 0 |
| Retry poll, 10 attempts over 7 min (03:15–03:22Z) | `AccessDeniedException` every single time |

**So the missing card was real but was not the root cause.** The deeper problem is the one
that was visible all along and is not payment-related: **Amazon Nova is first-party, needs no
Marketplace subscription and no payment instrument, and still hits a hard zero per-day quota.**
A zero, non-adjustable per-day quota across 42 models is an **account-level Bedrock
restriction** — most likely because the account is new (created 2026-04-27) and AISPL. No
console setting changes it; `adjustable=False` means it cannot even be raised by request.

**This is an AWS Support ticket, and support will not turn around before the 2026-09-20
deadline. Plan Phase 3 on the fallback (`ARCHITECTURE.md` decision 7). Do not spend more
session time re-testing Bedrock** — one quick converse call at the start of Phase 3 is enough
to detect if it ever unlocks.

### ✅ CORRECTED DIAGNOSIS (2026-09-18, verified in the AWS Console)

**The "Free Plan" theory below was wrong. There is no Paid Plan to upgrade to on this
account, and chasing one is a dead end. Do not re-open it.**

Verified directly in the console (Console Home, Billing Home, Account, Free Tier, Getting
Started, notifications) — **no Free Plan / Paid Plan UI exists anywhere on this account**:

| Evidence | Finding |
|---|---|
| Service provider | **Amazon Web Services India Private Limited (AISPL)** — not AWS Inc. |
| Account created | 2026-04-27, verified (`CUSTOMER VERIFICATION SUCCESS`), currency INR |
| Console Home | **No Free Plan banner** — Free Plan accounts always show one |
| Free Tier page | Legacy model (`AWS Free Usage Tier`, "Always Free"), not credits-based Free Plan |
| Account page | No plan section, no upgrade CTA |
| Credits | **$254.62 active** (incl. $100 WeMakeDevs, expires 2027-07-31) |
| Real spend | $0.46 MTD / $0.84 last month — the account bills normally |

**The actual blocker is the payment instrument:**

| Evidence | Finding |
|---|---|
| Payment methods | **1 of 1 — UPI AutoPay (GooglePay). No credit or debit card.** |
| Backup payment method | Disabled |
| AWS Marketplace active subscriptions | **0 — "You have no subscriptions"** |
| Anthropic / AI21 invoke | `AccessDeniedException: INVALID_PAYMENT_INSTRUMENT` |
| Bedrock Model access page | "For models served from **AWS Marketplace**, a user … must invoke the model once to enable it" |

Anthropic, AI21 and Mistral on Bedrock are served **through AWS Marketplace**. Marketplace
will not complete a subscription with UPI as the only instrument — it requires a card. That
is precisely what `INVALID_PAYMENT_INSTRUMENT` reports, and why the subscription list is empty.

**Unresolved residue — do not claim the card fixes everything.** `us.amazon.nova-lite-v1:0`
is first-party, needs no Marketplace subscription, and *still* fails with
`ThrottlingException: Too many tokens per day` against a zero quota. Plausibly the same root
(no payment-verified Bedrock entitlement), but that is inference, not proof. If Nova still
throttles after a card verifies, it is an AWS Support ticket, not a config fix.

**Action required (user only — Claude must not enter card details):** add a credit/debit card
at `https://console.aws.amazon.com/billing/home#/paymentpreferences`, then re-run the
verification command in *Manual actions pending*.

---

### ⛔ SUPERSEDED — original Bedrock quota investigation (2026-09-18)

> Kept for the quota data, which is still accurate and still reproduces. The *conclusion*
> ("account on the AWS Free Plan") is **wrong** — see the corrected diagnosis above.

Every model invocation fails `ThrottlingException: Too many tokens per day`, including
models with non-zero per-minute quota. Root cause in Service Quotas (1123 quotas inspected):

| Finding | Value |
|---|---|
| Per-model **per-day** token quotas at zero | **42 of 44** |
| Of those, **adjustable** | **0** — `adjustable=False`, cannot be raised by request |
| Anthropic token quotas non-zero | 0 of 21 |
| Amazon Nova token quotas non-zero | 0 of 33 |
| Account pool `L-E3F10727` | 150,000,000/day, `adjustable=False`, not the binding limit |
| Only tpm headroom | GPT-5.6 Terra/Luna/Sol (bedrock-mantle), AI21 Jamba 1.5 (3k tpm) |

Tested empirically — two different vendors, identical failure:

```
us.anthropic.claude-haiku-4-5-20251001-v1:0   ThrottlingException: Too many tokens per day
ai21.jamba-1-5-mini-v1:0                      ThrottlingException: Too many tokens per day
```

**Not** a permissions, model-access, or use-case-form problem — all three are resolved.
~~Hard zeros that cannot be raised via Service Quotas indicate an account still on the AWS
**Free Plan**.~~ **← WRONG. Superseded by the corrected diagnosis above: the account is
AISPL and has no Free/Paid plan concept; the binding failure is the missing card.**

**Decision (user, 2026-09-18):** proceed with Phases 1–2, which need no Bedrock. If a card is
added and Bedrock unlocks, real Bedrock drops into Phase 3 unchanged. If not, fall back per
`ARCHITECTURE.md` decision 7.

#### Re-check after Phase 1 (2026-09-18) — error signature changed, quotas did not

Still **not** on the Paid Plan. Quotas are byte-for-byte identical to the first
investigation: 1123 quotas, **42 of 43 per-day token quotas at zero, 0 adjustable**, only
the 150,000,000 `Cross-Model Account-Level Tokens Per Day` pool non-zero.

What *did* change is the error for third-party models:

| Model | Before | Now |
|---|---|---|
| `us.anthropic.claude-haiku-4-5` | `ThrottlingException: Too many tokens per day` | `AccessDeniedException: INVALID_PAYMENT_INSTRUMENT` |
| `ai21.jamba-1-5-mini` | `ThrottlingException: Too many tokens per day` | `AccessDeniedException: INVALID_PAYMENT_INSTRUMENT` |
| `us.amazon.nova-lite-v1:0` | — | `ThrottlingException: Too many tokens per day` (unchanged) |

```
Model access is denied due to INVALID_PAYMENT_INSTRUMENT: A valid payment
instrument must be provided.. Your AWS Marketplace subscription for this model
cannot be completed at this time.
```

The split is diagnostic. Third-party models need an AWS Marketplace subscription, which
requires a valid payment instrument — that is now the binding failure. Amazon's own Nova
needs no subscription, so it falls straight through to the Free Plan per-day quota of zero.

**Conclusion: the upgrade did not complete. The card on the account is missing, declined,
or unverified.** Fixing the payment instrument is the prerequisite; the plan upgrade cannot
complete without it. There is no AWS API that reports plan tier directly — this is inferred
from quota state plus the two error signatures.

**Impact is confined to Phase 3.** Phase 2 already specifies a stub agent, so the queue,
slot scheduler, token accounting, WebSocket sync and the deployed URL are all buildable now.

---

## Resolved

- **Anthropic use-case form** — submitted, gate cleared. Proven by the error changing from
  `ResourceNotFoundException` ("use case details have not been submitted") to a quota throttle.
- **IAM permissions** — `AdministratorAccess` attached to `hiveos-dev`. Proven by the error
  changing from `AccessDeniedException` to a quota throttle.
- **Root access keys retired** — now using IAM user credentials.

---

## Manual actions pending

> Two actions that used to sit here are **done**. The card: Visa •••• 3306 is on the account and
> set as default — it did not unblock Bedrock, do not repeat it. The model API key: the SSM
> SecureString `/hiveos/groq-api-key` exists and the agent is live against it.

**These three are the only things standing between the repo and a submission.** Everything
buildable is done, deployed and rehearsed.

1. **Record the demo.** Follow `DEMO.md` exactly. Run `./scripts/reset-demo.sh` first (it
   pre-warms the Lambdas and verifies the board), and `python scripts/rehearse.py --takes 2`
   before that to confirm the sequence still passes. Three browsers, ~640 px wide, separate
   profiles. Under 3:00.
2. **Upload to YouTube** (public or unlisted) and **open the link in a signed-out browser.**
   An accidentally-private video scores zero regardless of what was built. Paste the link into
   `SUBMISSION.md`.
3. **Submit** before 2026-09-20, with the public URL, the repo link and `SUBMISSION.md`.

Housekeeping, not blocking:

4. **Confirm AWS Budget notification email** — check `arunishrajput7@gmail.com` for the
   `hiveos-guardrail` subscription confirmation.
5. **Optional, post-hackathon: open an AWS Support case** about the account-level Bedrock
   restriction (42 of 43 per-day token quotas at zero, `adjustable=False`, first-party Amazon
   Nova included). Nothing waits on it any more — inference runs on Groq and Phase 3 is closed.

Items 4 and 5 do not block the submission. Items 1–3 **are** the submission.

---

## Known issues and discoveries

- **A test asserting a placeholder's behaviour will fail when the placeholder improves.** Three
  checks broke on the model swap and none was a product bug: two asserted `estimated is True`
  (only ever true of the stub) and the Phase 3 memory gate demanded the literal substring
  `Friday 16:00 UTC`, failing on a model's natural *"Friday **at** 16:00 UTC"*. Write the
  guarantee, not the current implementation's phrasing.
- **Cloudflare rejects urllib's default User-Agent with HTTP 403 `error code: 1010`**, which is
  indistinguishable from a bad API key until you read the response *body*. Any outbound call
  from a Lambda to a third-party API needs an explicit `User-Agent`.
- **Changing a `Default:` in `template.yaml` does not change a deployed stack.** CloudFormation
  keeps existing parameter values on update. A clean `sam deploy` will happily leave the old
  value in place — pin it in `samconfig.toml`'s `parameter_overrides` instead.
- **Groq retires model names.** `llama-3.3-70b-versatile` was already gone. A wrong model id
  fails at runtime, not at deploy. List with `GET https://api.groq.com/openai/v1/models`.
- **`expect()` discarding frames it read is a permanent trap, not a one-off.** It has now cost
  time twice. `rehearse.py` buffers per socket; `ws_smoke.py` still filters on identifying
  fields instead, which works but is weaker. If `ws_smoke` ever flakes on a missing frame, port
  the buffer across.
- **A silently-wrong measurement survives a green test.** Dispatch latency was anchored on
  `memory_updated`; moving the memory save earlier turned that number into "alice's whole task"
  (3,454 ms vs the true 64–84 ms) and every check still passed. Anchor a timing on the event it
  actually names.
- **Never build a `Decimal` from a float for DynamoDB.** `Decimal(24.92)` carries the binary
  expansion and boto3's `DYNAMODB_CONTEXT` raises `decimal.Inexact`; `Decimal(str(24.92))` is
  exact. Anywhere a non-integer reaches DynamoDB, this is waiting. It only reproduces with
  values that are not binary-representable — `73.5` and `0.25` pass, `24.92` does not — so a
  test with tidy fractions proves nothing.
- **A layout measurement can report success for a clipped layout.** `scrollHeight ===
  innerHeight` is true both when content fits and when `overflow: hidden` amputates it. Look at
  a screenshot before believing a fit. Clipping is worse than scrolling: scrolled content is
  still reachable.
- **`ESTIMATED_TASK_SECONDS` is not the only cross-language duplicate any more** — the avatar
  coordinate space (0–100, clamped) is asserted in `router/app.py` `_coord` and again in
  `useHive.js` `moveAvatar`. Both clamp, so a disagreement degrades rather than breaks, but they
  should change together.
- **A demo-facing script must not inherit a production-shaped default.** `reset-demo.sh` called
  `seed.sh` without a budget and got 1,000,000, at which the meter does not visibly move —
  defeating the entire point of the 5,000 calibration two phases earlier. The general shape:
  when a wrapper exists *for one specific purpose*, it owns the defaults for that purpose.
- **A test that resets some state but not all of it poisons the next run.** `ws_smoke.py`
  resets `tokens_used` and clears `MEMORY#`, but never reseeds `token_budget` — so a
  `rehearse.py --ceiling` run left a 60-token ceiling behind and `ws_smoke` failed two checks
  with messages that pointed nowhere near the cause. Anything that changes `token_budget` must
  put it back; `rehearse.py` does so in a `finally`.
- **The last task before the ceiling always overshoots it** (62 of 60). A task's cost is only
  known once it has produced a response, so it cannot be charged in advance. The UI clamps
  (`Math.min(100, pctUsed)`, `Math.max(0, remaining)`), so the meter reads 100.0% and 0
  remaining rather than 103% — correct, and worth knowing before it appears on camera.
  `ws_smoke.py` never saw this because it forces the counter to exactly the ceiling.
- **A user holding a slot cannot claim another one**, so "claim, then save a fact" is two
  rounds, not one. Folding `remember: …` into the claim prompt is what makes the memory beat a
  single continuous shot. Cost ~15 s of demo time to discover by rehearsing.
- **Amplify's `404-200` rule serves the right body with the wrong status.** A deep link like
  `/some/deep/link` returns HTTP **404** but the body is `index.html`, so the app boots
  normally. The custom rule is applied exactly as written (`aws amplify get-app --app-id
  dbavt8jr66qxx --query 'app.customRules'` confirms it). Harmless for the MVP — there is one
  route, `/`, and it returns a clean 200. Not worth a redeploy; do not chase it.
- **A flag that only rides on incremental events is invisible to a cold client.** The
  `estimated` token-provenance flag shipped on `token_update` only, so the browser most likely
  to see the board — a judge opening the URL for the first time — got an unlabelled number.
  Anything that qualifies what the board *shows* has to be on `state_snapshot` too. Same family
  of bug as the queue ETA below; the snapshot is the only thing a cold client ever reads.
- **`ws_smoke.py` assumes it is the only client.** Four CONN#-leak checks fail if any browser
  anywhere is pointed at the deployed URL. Not a regression — close the tabs.
- **`state_snapshot` is less detailed than the incremental events it replaces.** The re-sync
  pattern in `useHive.js` trades exactness for self-healing, and anything carried *only* on an
  incremental frame gets wiped when the snapshot lands. `estimated_wait_seconds` was the first
  casualty. Before adding a field to an incremental event, check whether the snapshot carries it
  too — or derive it client-side.
- **Membership is per-connection, but `user_left` is per-user.** Anyone with two tabs breaks a
  naive client-side member list. Deduped by `user_id` in `useHive.js`; see the Phase 4 bugs.
- **The frontend's `ESTIMATED_TASK_SECONDS` must track `scheduler.py`'s.** Two copies of the
  same constant in two languages. If the backend's estimate is retuned (Phase 3 should, once
  real Bedrock latency is known), `frontend/src/useHive.js` has to change in the same commit.
- **No `StrictMode` in `main.jsx`, on purpose.** Its dev-only double render opens two
  WebSockets and writes two `CONN#` rows, which makes the member count lie while developing.
- **`claimed_at` is not in `state_snapshot`.** A cold client cannot know how long a BUSY slot
  has been running, which is why the slot cards show a pulsing indicator and no elapsed timer —
  a timer would read differently on a browser that watched the transition than on one that
  joined mid-task, and "identical on every screen" is the whole claim.
- **Broadcast the state change before dispatching to SQS.** See the Phase 2 bug above. The
  general rule: a frame describing committed state must go out before the work that could
  produce the *next* frame. `CONTRACT.md` → *Frame ordering*.
- **`state_snapshot` had no `queue` field**, so a client reconnecting while queued could
  not render its own position. Added `queue[]`; `CONTRACT.md` updated in the same commit.
- **`QUEUE#` sort keys needed microsecond precision.** `now_iso()` is second-granularity,
  so two claims in the same second ordered by UUID — i.e. randomly. `state.now_iso_micros()`
  is used for queue SKs only.
- **The smoke test races the product.** A queued task only exists while the task ahead of
  it runs. Two sequential AWS CLI round trips (~0.7s each) outlasted that window and read
  an empty queue that really had been there. Both assertions now come from one
  `scheduler_rows()` query.
- **`expect()` swallowed the frame it was looking for.** It discards non-matching frames,
  so a check written as "wait for BUSY, then wait for error" silently eats the error if it
  arrives first — and then fails 20s later with no clue. Every `where=` now filters on the
  identifying field (`current_user`), not just `status`, and a timeout prints the frames it
  actually saw. This is what surfaced the ordering bug.
- **Lambda-to-client sends are invisible on success.** `send_to_connection` only logged
  `GoneException`, so "did the backend send it?" was unanswerable from CloudWatch. The
  runner now logs `delivered=` on its error replies — that single line is what turned the
  ordering bug from a guess into a diagnosis.

- **`state_snapshot` cannot be pushed from `$connect`.** API Gateway does not finish
  establishing the connection until the `$connect` integration returns, so
  `post_to_connection` against it fails with `GoneException`. `BUILD_PLAN.md` Phase 1
  task 5 said "sent immediately on `$connect`". Corrected: the client sends
  `{"action":"hello"}` on socket open and the server replies with the snapshot. Same
  behaviour, one extra ~50 ms round trip. `CONTRACT.md` documents the handshake.
- **`send_message` had no matching server event.** Added `chat_message`
  `{user_id, text, ts}` to `CONTRACT.md`.
- **`samconfig.toml` was missing from the repository.** Phase 0's `sam deploy --guided`
  writes it, but it was never committed, so `sam deploy` had no config to read. Created
  and committed — it holds no secrets. Do not run `--guided` again; it would overwrite it.
- **Only `$connect` / `$disconnect` / `$default` routes exist**, with
  `RouteSelectionExpression: $request.body.action`. Every action lands in one handler, so
  new actions are code changes only. This matters because `AWS::ApiGatewayV2::Deployment`
  is an immutable route-table snapshot — adding a route later requires renaming that
  resource's logical ID (noted in `template.yaml`).
- **Lambda packaging:** both functions build from `CodeUri: backend/` with handlers like
  `router.app.lambda_handler`, so `shared/` is importable as a top-level package. No layer.
- **DynamoDB returns `Decimal`** and `json.dumps` rejects it. Every outbound frame goes
  through `state.dumps()`.
- **Bedrock *Model access* page is retired.** Serverless models auto-enable on first invoke
  across all commercial regions. The Anthropic use-case form now lives as a banner on the
  **Model catalog** page, not Model access. `DEPLOYMENT.md` Manual Action 2 reflects this.
- **Local Python is 3.14**, newer than any Lambda runtime. Compiled dependencies would get
  wrong-platform wheels if built locally. **Always `sam build --use-container`.**
- **npm global prefix `~/.local/lib` does not exist** — `npm install -g` may fail. Use `npx`.
- **AWS CLI `configure` cannot run** through the `!` prefix in Claude Code (no TTY). Interactive
  AWS commands must be run in a normal terminal.

---

## Current deployment state

| Resource | Status |
|---|---|
| CloudFormation stack `hiveos` | ✅ `UPDATE_COMPLETE` (`us-east-1`) |
| DynamoDB `hiveos-state` | ✅ seeded — METADATA + 2 IDLE slots |
| AWS Budget `hiveos-guardrail` | ✅ $20, 80% alert |
| GitHub repo | ✅ https://github.com/arunishrajput/hiveos |
| WebSocket API `hiveos-ws` | ✅ `mel2gpat9c`, stage `prod` |
| Router Lambda `hiveos-router` | ✅ verified end to end |
| SQS `hiveos-agent-tasks` + DLQ | ✅ both empty, nothing dead-lettered |
| Agent Runner `hiveos-agent-runner` | ✅ verified end to end — **real model**, provider-reported tokens |
| SSM `/hiveos/groq-api-key` | ✅ SecureString, read at runtime, IAM-scoped to the Agent Runner |
| Amplify app `hiveos` / public URL | ✅ `dbavt8jr66qxx` → https://main.dbavt8jr66qxx.amplifyapp.com |

**The Amplify app is not managed by CloudFormation.** This is deliberate and matches
`BUILD_PLAN.md` Phase 4 task 5 and `DEPLOYMENT.md`: manual-deploy mode needs no GitHub OAuth
and no build service role, which makes it fully scriptable. The consequence is that
`describe-stacks` will never mention it — `scripts/deploy-frontend.sh` finds it by **name**
(`hiveos`) so repeat runs across `/clear` sessions reuse it instead of creating duplicates.
`sam delete` will not remove it; teardown needs `aws amplify delete-app --app-id dbavt8jr66qxx`.

---

## Next recommended action

**Record the video.** Nothing else moves the submission forward.

Every feature in `PRD.md`'s Must list is built, deployed and verified. Phase 5 was built, not
cut. Phase 3 is closed — the agent is real and the token counts are the provider's own. The
recorded sequence passes 12/12 twice unattended and takes ~13 s of product time. There is
nothing left to build that improves the submission more than a clean take does. **Do not start
new work in a fresh session — open `DEMO.md` and record.**

```bash
python scripts/rehearse.py --takes 2   # confirm the sequence still passes
./scripts/reset-demo.sh                # clean board, warm Lambdas, verified
```

Then follow `DEMO.md` beat by beat, and finish the three items in *Manual actions pending*.

> **`DEMO.md` is the only run sheet.** The earlier list that lived in this section had Alice
> claim a slot and then save a fact, which the scheduler does not allow — rehearsal caught it.
> Two run sheets is how a wrong one gets followed at 2 a.m.

If Bedrock is ever unblocked: one `bedrock-runtime converse` call to detect it and nothing
more — the evidence says it needs AWS Support. Then swap the body of `shared/llm.py:complete`
and add `bedrock:InvokeModel` to the Agent Runner role in `template.yaml` (the Phase 3 section
records where). Nothing else changes — which is the point of having moved the call behind that
seam.

### Standing gotchas for the recording

- **Close stray browser tabs before running `ws_smoke.py`.** Its CONN#-leak checks assert the
  table holds no connection rows, so one live browser fails four checks that have nothing to do
  with the code. `reset-demo.sh` warns when it finds live rows.
- Three browsers at **640×950** each is the layout the HUD is designed for. The board is 933 px
  tall with the memory panel showing, so it fits with ~17 px to spare. It was 640×880 before the
  workspace floor landed in Phase 5.
- Each browser needs a **different profile or a cleared localStorage** to hold a separate
  identity: the entry gate persists to `localStorage['hiveos.identity']`, so two tabs of the
  same origin share one name.
