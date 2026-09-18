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
| **Current phase** | **Phase 5 — agent chat + 2D canvas (cuttable)** |
| **Phase status** | `NOT STARTED`. Phase 4 complete; **Phase 3 deferred, not done** |
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
| 0 | Pre-project setup | `COMPLETE` (Bedrock deferred — see below) |
| 1 | WebSocket backbone | `COMPLETE` |
| 2 | Scheduler + queue (no LLM) | `COMPLETE` |
| 3 | Bedrock + agent + memory | `DEFERRED` — blocked on an account-level Bedrock restriction |
| 4 | Frontend HUD + public URL | `COMPLETE` |
| 5 | Agent chat + 2D canvas (cuttable) | `NOT STARTED` ← **next**, or cut |
| 6 | Demo readiness | `NOT STARTED` |

**Phase 3 was skipped deliberately** (user decision, 2026-09-18), on the reasoning already
recorded below: Phase 4 produces the submission, the stub agent exercises the entire
queue/slot/broadcast path without a single Bedrock call, and the blocker needs an AWS Support
ticket that will not turn around before the deadline. Shipping Phase 4 on the stub was strictly
safer than blocking. **Phase 3 is deferred, not cancelled** — if Bedrock ever unlocks it drops
into one function.

---

## Completed

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
| **Bedrock unusable — account-level zero quotas; card added and did NOT fix it** | Phase 3 only | Needs AWS Support. Phase 3 deferred; Phase 4 shipped on the stub. |

### What the Bedrock blocker actually costs the demo

Now that the HUD is live, the consequence is concrete and worth being honest about on camera:

- **The token meter renders correctly but never moves on its own.** The stub agent reports
  `tokens_used_this_call: 0` and nothing broadcasts `token_update`, so the quota strip sits at
  `0 / 1,000,000` for the whole demo. The client-side handler for `token_update` is implemented
  and correct per `CONTRACT.md`, but **no server code path emits that event yet** — it arrives
  with Phase 3. The meter's thresholds and geometry were verified by rendering the real
  component against the built CSS at nine percentages; the live tick is what is missing.
- **Team memory never populates**, so the memory panel stays hidden (it renders only when there
  are facts). The "Alice saves a fact, Charlie's agent already knows it" beat in the demo script
  cannot be shown.
- **`budget_exhausted` cannot be demonstrated end to end**, because nothing spends tokens.

Everything else in the demo script — the shared board, the slot lifecycle, the real queue
position, auto-dispatch — is live and recorded above. Per `PRD.md`, the honest framing is
fallback ladder rung 3: *mock agent responses, stated plainly.*

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

> The "add a card" action that used to sit here is **done** — Visa •••• 3306 is on the account
> and set as default. It did not unblock Bedrock. Do not repeat it.

1. **Confirm AWS Budget notification email** — check `arunishrajput7@gmail.com` for the
   `hiveos-guardrail` subscription confirmation.
2. **Optional, Phase 3 only: open an AWS Support case** about the account-level Bedrock
   restriction (42 of 43 per-day token quotas at zero, `adjustable=False`, first-party Amazon
   Nova included). Will not turn around before 2026-09-20, so this is for after the hackathon.
   Nothing in the remaining plan waits on it.

Nothing on this list blocks the submission.

---

## Known issues and discoveries

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
| Agent Runner `hiveos-agent-runner` | ✅ verified end to end (stub agent) |
| Amplify app `hiveos` / public URL | ✅ `dbavt8jr66qxx` → https://main.dbavt8jr66qxx.amplifyapp.com |

**The Amplify app is not managed by CloudFormation.** This is deliberate and matches
`BUILD_PLAN.md` Phase 4 task 5 and `DEPLOYMENT.md`: manual-deploy mode needs no GitHub OAuth
and no build service role, which makes it fully scriptable. The consequence is that
`describe-stacks` will never mention it — `scripts/deploy-frontend.sh` finds it by **name**
(`hiveos`) so repeat runs across `/clear` sessions reuse it instead of creating duplicates.
`sam delete` will not remove it; teardown needs `aws amplify delete-app --app-id dbavt8jr66qxx`.

---

## Next recommended action

**Go to Phase 6 — demo readiness. Skip Phase 5.**

There is a submittable deliverable right now, and `BUILD_PLAN.md` is explicit that Phase 5 is
the first thing to cut and that nothing downstream depends on it. With the deadline on
2026-09-20 and the video being the only judge touchpoint, rehearsing and recording beats adding
a 2D canvas.

**But consider this Phase-3-without-Bedrock slice first (~1 session), because it buys demo
beats that Phase 5 does not:**

- `backend/shared/memory.py` + the `MEMORY#` rows
- `get_team_memory` / `set_team_memory` and the `memory_updated` broadcast
- The budget ceiling check and `budget_exhausted`

None of these need a model call. The frontend **already renders all three** (memory panel,
`memory_updated` handler, `budget_exhausted` handler and the alarm state) — they are dark only
because no server path emits them. Wiring them up would restore the memory beat from the demo
script and make the enforced ceiling demonstrable, which is the single most distinctive claim
in `PRD.md`. A `set_team_memory` triggered by a keyword in the stub agent's prompt would be
enough, and honest, as long as the video says the agent is stubbed.

Weigh that against simply recording now. **Recording something that works outranks both.**

If Bedrock is ever unblocked: open with one `bedrock-runtime converse` call and nothing more —
the evidence says it needs AWS Support. `_run_agent` in `backend/agent_runner/app.py` is the
single seam it drops into; nothing else in the runner changes.

### Before recording

- `./scripts/seed.sh` resets the board to a clean demo state.
- Pre-warm both Lambdas — a cold Router adds visible latency to the first claim.
- Three browsers at ~640 px wide each is the layout the HUD was designed for; it fits without
  scrolling at 640×880.
- Each browser needs a **different origin or a cleared localStorage** to hold a separate
  identity: the entry gate persists to `localStorage['hiveos.identity']`, so two tabs of the
  same origin share one name. Separate browsers or profiles are the simplest fix.
