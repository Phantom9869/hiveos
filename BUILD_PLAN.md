# BUILD_PLAN.md — Phased roadmap

Seven phases, each sized for one Claude Code session. Strictly sequential except Phase 5, which is cuttable.

Every phase ends with: verify → update `PROGRESS.md` → update docs → inspect diff → commit → push → stop.

| # | Phase | Cuttable |
|---|---|---|
| 0 | Pre-project setup | No |
| 1 | WebSocket backbone | No |
| 2 | Scheduler + queue (no LLM) | No |
| 3 | Bedrock + agent + memory | No |
| 4 | Frontend HUD + public URL | No |
| 5 | Agent chat + 2D canvas | **Yes — cut first** |
| 6 | Demo readiness | No |

---

## Phase 0 — Pre-project setup

**Objective.** Every blocker resolved and the deployment pipeline proven before a single feature exists.

**Dependencies.** None.

### Tasks — in this order

1. **`MANUAL` Request Bedrock model access.** Start first; longest lead time. Console → Bedrock (`us-east-1`) → Model access → request Anthropic models. See `DEPLOYMENT.md` → Manual Action 2.
2. **`MANUAL` Configure AWS credentials.** `aws configure` or SSO. Verify: `aws sts get-caller-identity`.
3. **Verify Bedrock with a real call.** `aws bedrock list-inference-profiles`, then an actual `bedrock-runtime converse` that returns a completion. A list call proves nothing about entitlement. Record the exact working model ID in `CONTRACT.md`.
4. **`MANUAL` Start Docker Desktop.** Installed, daemon down. Required for `sam build --use-container`.
5. **Install SAM CLI.** `brew install aws-sam-cli`.
6. **Create an AWS Budget alarm** (~$20) as the backstop behind the in-app token ceiling.
7. **Inventory existing AWS resources** for name collisions on `hiveos*` across DynamoDB, SQS, Lambda, API Gateway, Amplify.
8. **Git and GitHub.** `git init`, `.gitignore`, `gh repo create`, initial commit, push.
9. **Deploy a minimal stack.** `template.yaml` with the DynamoDB table only → `sam build --use-container && sam deploy --guided`.
10. **Seed team metadata** — the `TEAM#alpha / METADATA` row with `token_budget` and `tokens_used = 0`.

**Primary files.** `template.yaml`, `samconfig.toml`, `.gitignore`, `scripts/seed.py`, `CONTRACT.md`

**Validation.**
- `aws sts get-caller-identity` returns an account
- A real `converse` call returns a completion
- `aws cloudformation describe-stacks --stack-name hiveos` shows `CREATE_COMPLETE`
- `put-item` / `get-item` round trip against the real table succeeds

**Completion criteria.** Stack deployed, table seeded, repo pushed, Bedrock model ID recorded. If Bedrock access is still pending, tasks 4–10 still proceed and the phase is marked `BLOCKED — WAITING FOR MANUAL ACTION` with the verification command in `PROGRESS.md`.

---

## Phase 1 — WebSocket backbone

**Objective.** Real-time broadcast working against deployed AWS, with stale connections handled correctly.

**Dependencies.** Phase 0 complete (stack exists).

### Tasks

1. Add the WebSocket API to `template.yaml` (`AWS::ApiGatewayV2::Api` with `ProtocolType: WEBSOCKET`, plus Integration, Routes, Deployment, Stage).
2. Router Lambda: `$connect` writes a `CONN#` row; `$disconnect` deletes it.
3. `broadcast_to_team()` in `backend/shared/` — **with the GoneException handler from day one** (`CONTRACT.md`).
4. `$default` route: echo/broadcast a test message so two clients can prove fan-out.
5. `state_snapshot` sent immediately on `$connect`.
6. Grant `execute-api:ManageConnections` in the Lambda IAM policy — a common and confusing omission.

**Primary files.** `template.yaml`, `backend/router/`, `backend/shared/broadcast.py`

**Validation.**
- Two `wscat` clients connect to the deployed `wss://` URL; a message from one reaches both
- Kill one client mid-broadcast; CloudWatch shows the GoneException branch firing and the `CONN#` row deleted
- No crash in the broadcast loop afterward

**Gate.** Two clients receive the same broadcast, and a dead connection is cleaned up without breaking delivery.

---

## Phase 2 — Scheduler and queue (no LLM)

**Objective.** The entire OS-scheduler mechanic proven **without** Bedrock latency or cost, using a stub echo agent.

**Dependencies.** Phase 1 complete.

### Tasks

1. Add SQS queue + DLQ to `template.yaml`; add the Agent Runner Lambda with an SQS event source.
2. Seed the two `AGENT#` slot rows as `IDLE`.
3. `claim_agent` handler: atomic conditional update per `CONTRACT.md`; on failure across all slots, write a `QUEUE#` item.
4. Queue position calculation and the `queue_update` broadcast.
5. **Stub agent** in the Runner — sleeps briefly, returns canned text, consumes no tokens.
6. Release logic in `try/finally`: set slot `IDLE`, dispatch the oldest `QUEUE#` item to SQS, delete that item, broadcast.

**Primary files.** `template.yaml`, `backend/router/`, `backend/agent_runner/`

**Validation.**
- Claim 1 → slot `coder` BUSY; claim 2 → slot `researcher` BUSY; claim 3 → `QUEUE#` item written and position 1 broadcast
- On completion of claim 1, the queued task is auto-dispatched and its slot goes BUSY
- Force the stub agent to raise — the slot still releases (no leak)
- DynamoDB inspected directly to confirm slot and queue state

**Gate.** Third claim queues; auto-dispatch fires on release; a failing task never leaks a slot.

> This is the highest-value phase. The queue mechanic *is* the product. Proving it without an LLM in the loop keeps it fast to iterate and impossible to blame on model latency.

---

## Phase 3 — Bedrock, agent, and memory

**Objective.** Replace the stub with a real agent; make the token meter real and enforced.

**Dependencies.** Phase 2 complete; Bedrock access verified in Phase 0.

### Tasks

1. Add Bedrock invoke permissions to the Agent Runner IAM role.
2. Integrate Strands Agents SDK. **Timebox packaging to one hour** — then fall back to boto3 `converse` per `ARCHITECTURE.md` decision 7. Record which path was taken.
3. Implement the three tools: `get_team_memory`, `set_team_memory`, `get_task_context`.
4. Load team memory into the system prompt **before** the model call.
5. Token accounting — read usage from the Bedrock response, `ADD` to `tokens_used`, broadcast `token_update`.
6. **Budget ceiling** — refuse to invoke Bedrock at 100%; broadcast `budget_exhausted`.
7. Cap `max_tokens` per call.

**Primary files.** `backend/agent_runner/`, `backend/shared/memory.py`, `template.yaml`, `CONTRACT.md`

**Validation.**
- A real task returns a real model response over WebSocket
- `tokens_used` delta matches the usage reported by Bedrock
- With `tokens_used` manually set at the ceiling, Bedrock is **not** called and `budget_exhausted` is broadcast
- User A saves a fact; User B's subsequent agent response reflects it without being told

**Gate.** Real agent response, accurate token accounting, enforced ceiling, memory crossing between users.

---

## Phase 4 — Frontend HUD and public URL

**Objective.** End-to-end deployed MVP. A judge can open a URL cold and see it working.

**Dependencies.** Phase 3 complete.

### Tasks

1. Vite + React app in `frontend/`.
2. WebSocket client: connect, render from `state_snapshot`, apply incremental events, auto-reconnect.
3. HUD — token meter (green <50%, amber 50–80%, red >80%), slot badges, queue position.
4. Name picker on entry; "Get Agent" button; task prompt input.
5. Build and deploy to Amplify Hosting via CLI (`create-app` → `create-branch` → `start-deployment` with a zip — no GitHub OAuth needed).
6. Record the public URL in `PROGRESS.md` and `README.md`.

**Primary files.** `frontend/`, `scripts/deploy-frontend.sh`, `DEPLOYMENT.md`

**Validation.**
- Public HTTPS URL opens in a fresh incognito window with zero local setup
- Two browsers side by side show identical token meter, slot states, and queue position
- Claiming in one browser visibly updates the other within ~2 seconds

**Gate.** **Deployed end-to-end MVP.** Everything after this point is enhancement. If time runs out here, there is still a submission.

---

## Phase 5 — Agent chat and 2D canvas *(cuttable)*

**Objective.** The visual workspace layer.

**Dependencies.** Phase 4 complete.

**Cut this entire phase if Phase 3 or 4 overran.** The HUD is the product; this is the wrapper.

> **Built, 2026-09-18.** Not cut. Phase 3 is blocked by an AWS account restriction rather than
> overrun, and every MVP-Critical item was finished and verified with two days left — the
> condition `CLAUDE.md` says MVP-Supporting work should be built under. "Blocked externally" is
> not "ran out of time"; conflating them cost an unnecessary cut. See `PROGRESS.md`.

### Tasks

1. Sidebar: agent chat, response display, "thinking" indicator, team memory facts list.
2. 2D canvas — fixed-size div, absolutely positioned character divs, CSS transitions on x/y.
3. `move_avatar` wired through the existing broadcast path.
4. Memory-saved toast on all connected browsers.
5. Per-task token cost shown on completion.

**Primary files.** `frontend/`

**Validation.** Two browsers see each other's avatars move; chat renders; memory toast appears on both.

**Gate.** Nothing downstream depends on this phase.

---

## Phase 6 — Demo readiness

**Objective.** Record and submit. **No new features.**

**Dependencies.** Phase 4 complete (Phase 5 optional).

### Tasks

1. Seed demo data — three members, clean token budget, empty queue.
2. `scripts/reset-demo.py` to restore a known state between takes.
3. Pre-warm both Lambdas immediately before recording.
4. Rehearse the full 3-minute script twice, end to end, without intervention.
5. Fix only what breaks during rehearsal.
6. Record in a single clean take; three browser windows visible.
7. Upload to YouTube (public or unlisted); **verify the link opens in a signed-out browser**.
8. Write the submission writeup: problem, build, where AWS fits, what was learned, AI tools used.
9. Submit before the deadline.

**Validation.**
- Full demo sequence runs twice without intervention
- Video is under 3 minutes and shows every claimed feature
- Public URL works from a device that has never visited it
- Repo is public and its history matches the event dates

**Gate.** Video and URL submitted.

### Demo script beats

| Time | Beat |
|---|---|
| 0:00–0:25 | The problem — Uber burned its 2026 AI budget in four months; 79% of enterprises had overruns; only 36% have controls. Address Munder Difflin: *local harness for one dev's own CLI agents vs. cloud governance layer for a team.* |
| 0:25–0:45 | Three browsers, one workspace. The token meter is identical on every screen. |
| 0:45–1:30 | **The queue moment.** Two claims fill both slots; the meter ticks; a third user gets a real position. |
| 1:30–2:15 | **The memory moment.** Alice saves a team fact; a badge appears everywhere; a slot frees; Charlie is auto-dispatched and his agent already knows the fact. |
| 2:15–2:45 | Where AWS fits — API Gateway WebSocket, Lambda, SQS, DynamoDB, Bedrock, Amplify. Use the accurate SQS wording from `ARCHITECTURE.md`. |
| 2:45–3:00 | What was learned. |

### Fallback ladder

Fix only what is broken. **Never add features to rescue a demo.**
1. HUD works → demo the HUD only
2. WebSocket broken → record components separately
3. Bedrock unavailable → mock responses, stated honestly

---

## Phase 7 — Canvas-first workspace *(added 2026-09-18, user decision)*

**Objective.** Invert the interface hierarchy so the pixel-art office floor is the primary
surface and the HUD becomes chrome over it. Reference aesthetic: Munder Difflin — pixel art,
**top-down, not isometric**.

**Dependencies.** Phases 1–6 complete. Branch `feat/pixel-canvas` off `main`.

**Why this exists.** Phase 5 built a working floor, but a 100 px strip inside a 760 px vertical
panel stack reads as a dashboard with a decoration in it, whatever is drawn inside. The feel
being aimed at comes from the *world being the interface*, which is a shell change, not a
component restyle.

### Non-negotiables

- **Frontend only.** `components.jsx`, `App.jsx`, `styles.css`. No backend, no protocol, no
  `CONTRACT.md` edits. Every event needed already exists and is verified.
- **Coordinates stay percentages (0–100).** `CONTRACT.md` line 168. Desks and decor are
  positioned in percentages too, or they drift out of alignment with the pawns at other widths.
- **`ws_smoke.py` 58/58 before merge.** Close every browser tab on the deployed URL first, or
  four connection-leak checks fail for unrelated reasons.
- **Verify with a screenshot, not `scrollHeight === innerHeight`.** That measurement has already
  reported success once for a layout `overflow: hidden` had amputated.

### Slices — in this order, each leaving a working app

Ordered so the risky restructure is **last and cuttable**. Stop after any slice and the app
still works and still demos.

1. **Floor foundation.** Grow the floor to the hero surface. Checkerboard tile pattern, desk
   rectangles at the two slot positions, CSS monitor shapes, plant/decor in the corners. No
   sprite or layout changes yet. *Pure addition — biggest visible win per hour.*
2. **Pixel sprites.** Replace the emoji pawns with CSS `box-shadow` 16×16 sprites, three
   designs, 2-frame idle/busy via `@keyframes`. Keep `translate(-50%, -50%)` centring and the
   `busyUsers` flag driving the busy frame.
3. **Desks wired to slot state.** A desk's monitor lights up while its slot is BUSY and shows
   who holds it. This is the slice that makes the floor *mean* something rather than decorate —
   the scheduler becomes visible in the world.
4. **Chrome inversion.** Quota meter to a top bar, member status bar along the bottom, remaining
   panels collapsed or moved around the canvas. *The risky one. Everything above ships without
   it.*
5. **Demo format.** Re-measure the board, pick the new window size, update `DEMO.md`'s window
   dimensions and pre-flight, re-run `rehearse.py --takes 2`.

### Validation

- `python scripts/ws_smoke.py` → 58/58
- `python scripts/rehearse.py --takes 2` → 12/12 twice
- Screenshot at the demo window size with nothing clipped and no horizontal scroll
- Sprites legible at **actual recording size**, not at desktop zoom
- Two browsers still agree on every avatar position

**Gate.** The floor reads as a room, the scheduler is visible in it, and the demo still passes.

> **Feature freeze with 6 hours left on the clock, whatever state this is in.** The submission
> is already complete and banked on `main` (`df8282a`); Phase 7 is upside. If it is not merged
> and rehearsed by the freeze, record from `main` and ship that.

---

## Phase 8 — Depth pass *(added 2026-09-18, user decision)*

**Objective.** Close the gaps between what HiveOS *claims* and what it does, and stop the app
living in a 760 px column.

**Dependencies.** Phase 7 merged and deployed.

### Slices — in this order

Ordered so each lands on top of a verified state, and so the shell exists before anything new is
put inside it.

1. **Avatar identity (bug).** The marker chosen at the entry gate is ignored on the floor — the
   sprite is picked by `index % 3`, so two people can look identical and *your own character
   changes when somebody joins or leaves*. Key the sprite on the chosen avatar, stable per
   `user_id`. Frontend only.
2. **Full-screen responsive shell.** `.board` is capped at 760 px. Go to a real app layout on
   wide screens — room large, side rail for activity/memory/request, quota across the top,
   members across the bottom — collapsing to today's single column below ~1100 px.
   **Responsive, not fixed-wide**, so three 640 px windows still work for a side-by-side demo.
   Frontend only.
3. **Task history + spend audit.** A `TASK#` entity: who ran what, what it cost, when. This is
   what a governance product is actually for, and it is the reason `get_task_context` was never
   built. Unlocks a per-user spend breakdown. Backend + `CONTRACT.md` + a panel.
4. **Per-user fairness.** The pitch is *fair queueing* and OS-style scheduling; the mechanism is
   one shared pool and FIFO. Nothing stops one person taking slots turn after turn and starving
   the rest. Add a per-user allowance or round-robin so the scheduler matches the claim.
   Backend + `CONTRACT.md`.
5. **Real tool calling.** The model decides to call `set_team_memory` itself, instead of
   `memory.directive()` parsing `remember: k = v`. The last place the agent differs in *kind*
   from a real one. Backend only; the regex stays as the fallback path.

### Non-negotiables

- `ws_smoke.py` green before each slice is committed. Close every browser tab on the deployed
  URL first, or the connection-leak checks fail for unrelated reasons.
- Slices 3 and 4 change the protocol or the schema — `CONTRACT.md` is updated **in the same
  commit**, never after.
- `main` holds a recordable submission at all times. Anything risky goes on a branch.

**Gate.** Each slice: verified against deployed AWS, `rehearse.py --takes 2` still 12/12.

---

## If you are behind schedule

Cut in this order:
1. Phase 5 entirely
2. Per-task cost breakdown, task history, announcements
3. The second agent slot — one slot still demonstrates queueing
4. Shared memory — the queue plus token meter alone still tells the story

**Never cut:** the deployed public URL, the token meter, or the demo video. Those three are the submission.
