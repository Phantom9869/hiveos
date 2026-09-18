# HiveOS — First Commit (WeMakeDevs × AWS), Ship It track

**The OS scheduler for your team's shared AI budget.**

| | |
|---|---|
| **Live URL** | <https://main.dbavt8jr66qxx.amplifyapp.com> — opens cold, no setup, no sign-in |
| **Repository** | <https://github.com/arunishrajput/hiveos> |
| **Video** | *(paste the YouTube link here before submitting)* |
| **Track** | Ship It |
| **Built by** | Arunish Rajput, solo, in ~72 hours |
| **Region** | `us-east-1` |

---

## The problem

Teams are handing AI agents a shared budget and no way to govern it.

- Uber burned its entire 2026 AI coding budget in four months *(Fortune / The Information, May 2026)* — one internal demo cost $1,200 in two hours
- 79% of enterprises had AI cost overruns in the past 12 months *(DoiT / Sapio Research, Feb 2026)*
- Only 36% of organisations have any token or usage controls *(PointFive Research, Jul 2026)*

The gap isn't dashboards — those report yesterday. The gap is **real-time, shared, enforced**
governance: who is using the agents right now, what is it costing, whose turn is next, and what
happens when the budget runs out.

Operating systems solved exactly this for CPU fifty years ago: scheduling, quotas, fair
queueing. **HiveOS applies that abstraction to a team's shared AI compute.**

This is deliberately not a local agent harness governing one developer's own CLI agents on
their own machine. It is a **cloud governance layer for a team sharing one budget** — the state
is shared, the queue is shared, and the ceiling is enforced server-side for everyone.

---

## What I built

A deployed, public, multi-user workspace where:

- One team shares a **token budget** and a pool of **agent slots**
- The budget meter is **identical on every member's screen**, updating live over WebSocket
- When every slot is busy, further requests **queue with a real position**, visible team-wide
- A freed slot **auto-dispatches** the next queued task — nobody re-asks
- Agents share **team memory**: a fact one member saves is loaded into the next member's agent
  before their task starts
- The budget is an **enforced ceiling, not a gauge** — at 100% the server refuses to invoke the
  agent, and not one token is spent
- A shared **workspace floor** shows who is in the room and who is mid-task, so "three people,
  one board" is something you can see rather than something the narrator claims

Measured against the deployed system, not localhost:

| | |
|---|---|
| A claim reaching a second browser | **282 ms** |
| Auto-dispatch visible after a slot frees | **187 ms** |
| An avatar move painted on a second browser | **270–294 ms** |
| End-to-end checks against real AWS | **58/58** (`scripts/ws_smoke.py`) |
| Rehearsed demo sequence | **12/12**, two consecutive unattended takes (`scripts/rehearse.py`) |

Timings are click-to-paint across two separate browsers — a 20 ms DOM sampler in the *observing*
browser compared against the acting browser's click — not a server-side round trip.

---

## Where AWS fits

```
Browser ──wss──► API Gateway WebSocket ──► Router Lambda ──► DynamoDB
                                                │                 ▲
                                                ▼                 │
                                               SQS ──► Agent Runner Lambda ──► Groq
```

| Service | Job |
|---|---|
| **API Gateway (WebSocket)** | The live board. Every member holds an open socket; the server fans out every state change |
| **Lambda** (×2) | Router owns connections, slot claims and queueing. Agent Runner executes tasks. Split so agent latency never blocks connection handling |
| **SQS** (+ DLQ) | Durable, at-least-once handoff of every agent task, with a dead-letter queue |
| **DynamoDB** | Single-table store. **Atomic conditional writes** do the slot claiming; **atomic counters** do the token accounting |
| **Amplify Hosting** | The React frontend and the public URL |
| **CloudFormation / SAM** | All infrastructure as code in one `template.yaml` |
| **AWS Budgets** | A spend backstop behind the in-app ceiling |
| **SSM Parameter Store** | Holds the model API key as a SecureString, read at runtime — never in the template, the stack, or git |

Two AWS design decisions worth naming:

**The queue is gated in DynamoDB, not in SQS.** Gating on SQS itself (reserved concurrency equal
to the slot count) was the obvious approach and I rejected it: it pulls in Lambda throttling,
visibility-timeout tuning and `maxReceiveCount` → DLQ risk under exactly the conditions a demo
creates, and SQS exposes approximate depth, not *"where am I in line."* Instead the Router claims
a slot with an atomic conditional update; if that fails it writes a `QUEUE#<timestamp>` item, and
position is a trivial count of earlier items. Deterministic, race-free, and position display is
free. SQS still does real work — every running task is a durable, DLQ-backed message.

**The ceiling is checked immediately before the model would be called, not at claim time.** A
task can sit in the queue while the tasks ahead of it burn what was left, so claim time is the
wrong moment to decide. It is also the spend guard, which is why it is never disabled to make a
demo work.

---

## Honest status: inference is the one thing not on AWS

**Amazon Bedrock is blocked account-wide on this AWS account.** 42 of 43 per-day token quotas
sit at zero and are marked `adjustable=False`, so they cannot be raised even by request —
including first-party Amazon Nova, which needs no Marketplace subscription and no payment
instrument. I diagnosed this to the account level rather than guessing: adding a card fixed a
genuine, separate `INVALID_PAYMENT_INSTRUMENT` failure for third-party models, and Nova still
returned `ThrottlingException: Too many tokens per day` against a zero quota. I re-verified in
three regions — `us-east-1`, `us-west-2`, `ap-south-1` — before giving up on it. That is an AWS
Support matter, not a config fix, and it was never going to turn around before the deadline.

**So model inference calls out to Groq (`openai/gpt-oss-120b`) over HTTPS.** Everything else —
the WebSocket API, both Lambdas, SQS, DynamoDB, SSM, Amplify, SAM — is AWS. One outbound HTTP
call is the entire difference.

I think that is the right call rather than a retreat, for a reason the product itself argues: a
governance layer that only works against one vendor's models is a worse governance layer. The
scheduler does not care where a token was spent, only that it was counted. The swap proved it
concretely — it touched exactly one function plus a new 130-line client, and nothing about the
queue, the slot state machine, the atomic accounting or the enforced ceiling moved.

**The counts are real.** `tokens_used` is the `total_tokens` the provider reports, not an
estimate — which matters, because a product whose entire pitch is token governance cannot show
invented numbers.

**The workspace degrades instead of breaking.** If the model is unreachable, the Agent Runner
still answers from composed text and charges the standard `len(text)/4` heuristic — but every
frame carrying such a count sets an `estimated` flag, the UI says *"partly estimated — the model
was unreachable"*, and per-task costs get a `~` prefix. The flag is sticky, so one degraded call
marks the whole total for as long as it stands. A degraded answer never gets laundered into a
billed-looking meter.

During verification I found a real hole in that safeguard: the flag originally rode only on live
`token_update` frames, so a browser opening the URL cold — *which is every judge* — saw an
unlabelled number. Fixed by persisting the provenance on the metadata row and returning it on
`state_snapshot`, with a smoke-test check for exactly that case.

**One gap I chose not to close.** A fact is saved by a `remember: key = value` prompt convention
rather than the model deciding to call `set_team_memory` itself. Tool calling adds a second
round trip and a failure surface, and with the rest green and a deadline close, it was not worth
the risk. Everything the convention then touches — the row, the broadcast, the context load on
the next task — is the real mechanism.

---

## What I learned

**The hard part was not the AI.** It was making three browsers agree on one number.

**Frame ordering is a correctness property, not a detail.** `claim_agent` dispatched to SQS
*before* broadcasting the BUSY state, so a fast-failing task could post its reply ahead of the
BUSY frame. The client would then apply BUSY *after* the release and show a slot stuck busy for
the rest of the session. It failed about half the time, which is the worst failure rate there
is. The rule that came out of it: **a frame describing committed state must go out before the
work that could produce the next frame.**

**A flag that only rides on incremental events is invisible to the client that matters most.**
The cold-load path is the one a judge takes, and it reads exactly one frame. Twice I shipped
something correct for a watching client and wrong for a joining one — the token-provenance flag,
and a queue ETA that got erased 500 ms after appearing because the snapshot didn't carry it.

**Test what the user sees, not what the code returns.** The slot-leak bug and the ordering bug
were both invisible to unit tests and obvious the moment I asserted on frames arriving at a
second, *observing* client. Every check in this repo runs against deployed AWS for that reason —
a zero exit code proves a command succeeded, not that the system behaved.

**A passing test can be passing for the wrong reason.** Avatar moves were silently failing:
`Decimal(24.92)` built from a float carries its binary expansion and boto3 raises
`decimal.Inexact` rather than rounding. My own test had passed — because I had picked `73.5` and
`21.25` as "obviously fractional" coordinates, and those are exactly representable in binary
floating point. Only a real mouse click produced one that wasn't. The failure mode was the worst
kind for this product: the mover's optimistic UI still moved them, so they were standing
somewhere **nobody else could see**. Choosing awkward test data is a skill, and tidy numbers are
a trap.

**A green measurement can describe a broken screen.** When the new panel overflowed the window I
pinned the board to the viewport with `overflow: hidden`, and my check — `scrollHeight ===
innerHeight` — went green. It was green because the layout was *amputated*: two panels were not
merely off-screen but unreachable. A screenshot showed it in one second. Verify the artifact,
not the proxy.

**Diagnose the account, not the code.** I lost real hours assuming Bedrock was a permissions or
model-access problem. The thing that actually resolved it was reading 1,123 service quotas and
noticing that two different vendors failed identically — which ruled out everything in my
codebase in one step.

**Ship the thing that survives being cut.** I built the deployed public URL before the model
integration, against the plan's own ordering. That inversion is why there is a submission at all.

**Tests that encode a placeholder's behaviour break when the placeholder gets better.** Swapping
the stub for a real model broke three checks, and not one of them was a product bug. Two
asserted `estimated is True` — only ever true of the stub. The third was the Phase 3 memory
gate, asserting the response contained the literal string `Friday 16:00 UTC`; the real model
wrote *"Friday **at** 16:00 UTC"* and the gate failed on an inserted preposition while the
memory load was perfectly correct. Each had been written against what the stub *happened to do*
rather than what the system must *guarantee*. Rewritten as the real invariants — the frame
declares its provenance, an estimated call stickily flags the total, and the answer demonstrates
knowledge of the fact however phrased — they now hold in both modes and are strictly stronger.

**The failure a layer below yours will impersonate your own.** Three of the four real defects in
that swap looked like something they were not: Cloudflare rejecting the stdlib's default
User-Agent returns HTTP 403, indistinguishable from a bad API key until you print the body; a
retired model name fails at *runtime*, not deploy; and CloudFormation keeps a deployed stack's
existing parameter values on update, so editing a template `Default:` changed nothing and the
Lambda kept calling the dead model after a clean deploy. Reading the actual error body, once,
beat every hypothesis I had.

---

## AI tools used

**Claude Code (Opus) as the sole implementation assistant**, operated by me. It inspected the
repo, wrote the code, ran the AWS commands, read CloudWatch logs, debugged, and prepared commits.

What made it work over ~72 hours and many context resets was treating **the repository as the
agent's memory**. `CLAUDE.md` defines the workflow and a source-of-truth hierarchy where
*deployed AWS state* outranks documentation; `PROGRESS.md` holds current execution state;
`CONTRACT.md` pins the schemas and protocol so they cannot drift between sessions. A fresh
session reads those three files and knows the phase, the blocker and the next step — I never
re-explained the project after a `/clear`.

The rule that paid off most: **verify against deployed AWS, never against an exit code.** Every
phase gate in `BUILD_PLAN.md` is a runtime check, which is how the frame-ordering bug and the
cold-client provenance bug were caught before they reached the recording instead of during it.

---

## Reproducing it

```bash
sam build --use-container && sam deploy      # backend
./scripts/deploy-frontend.sh                 # frontend + public URL

./scripts/reset-demo.sh                      # clean, warm, verified demo board
python scripts/ws_smoke.py                   # 49 checks against deployed AWS
python scripts/rehearse.py --takes 2         # the recorded sequence, unattended
```

`DEPLOYMENT.md` has the full procedure and troubleshooting. `DEMO.md` is the recording run sheet.
