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
| **Current phase** | **Phase 0 — Pre-project setup** |
| **Phase status** | `BLOCKED — WAITING FOR MANUAL ACTION` (Bedrock use-case form) |
| **Deployment state** | Nothing deployed. No AWS stack exists. |
| **Repository** | Local git initialised, `main`, 1 commit. Not yet on GitHub. |
| **AWS account** | `890608337320`, region `us-east-1`, credentials working |

---

## Phase board

| # | Phase | Status |
|---|---|---|
| 0 | Pre-project setup | `NOT STARTED` |
| 1 | WebSocket backbone | `NOT STARTED` |
| 2 | Scheduler + queue (no LLM) | `NOT STARTED` |
| 3 | Bedrock + agent + memory | `NOT STARTED` |
| 4 | Frontend HUD + public URL | `NOT STARTED` |
| 5 | Agent chat + 2D canvas (cuttable) | `NOT STARTED` |
| 6 | Demo readiness | `NOT STARTED` |

Status values: `NOT STARTED` · `IN PROGRESS` · `BLOCKED — WAITING FOR MANUAL ACTION` · `COMPLETE`

---

## Current phase tasks — Phase 0

- [x] Configure AWS credentials (**MANUAL**) — verified `sts get-caller-identity`
- [ ] **Submit Anthropic use-case details form** (**MANUAL — BLOCKING**, see below)
- [ ] Verify Bedrock with a real `converse` call; record working model ID in `CONTRACT.md`
- [ ] Start Docker Desktop (**MANUAL**)
- [ ] Install AWS SAM CLI
- [ ] Create AWS Budget alarm (~$20)
- [ ] Create GitHub repo (**public** — hackathon requires it), push
- [ ] Inventory existing AWS resources for name collisions
- [ ] `sam deploy` with DynamoDB table only — prove the deployment pipeline

Full detail in `BUILD_PLAN.md` → Phase 0.

---

## Completed

- **2026-09-17** — Repository scaffold: 8 docs + SAM skeleton, no application code. Commit `32fd214`.
- **2026-09-18** — AWS credentials configured and verified. Account `890608337320`, `us-east-1`.
- **2026-09-18** — Bedrock model catalogue enumerated (see below). Invoke access **not** yet granted.

---

## Blocked

| Blocker | Blocks | Resolution |
|---|---|---|
| **Bedrock model invocation unavailable on this account tier** | Phase 3 only | Decision pending — see below. Phases 0, 1, 2, 4 unaffected. |

### ⛔ Bedrock is quota-blocked account-wide (investigated 2026-09-18)

Every model invocation fails with `ThrottlingException: Too many tokens per day`,
including models whose per-minute quota is non-zero. Root cause found in Service Quotas:

| Finding | Value |
|---|---|
| Per-model **per-day** token quotas at zero | **42 of 44** |
| Of those, **adjustable** | **0** — `adjustable=False`, cannot be raised by request |
| Account-level pool (`L-E3F10727`) | 150,000,000/day, but `adjustable=False` and not the binding limit |
| Anthropic token quotas non-zero | 0 of 21 |
| Amazon Nova token quotas non-zero | 0 of 33 |
| Only models with any tpm headroom | GPT-5.6 Terra / Luna / Sol (bedrock-mantle), AI21 Jamba 1.5 (3k tpm) |

Tested empirically — **both** throttle identically despite having per-minute quota:

```
us.anthropic.claude-haiku-4-5-20251001-v1:0   ThrottlingException: Too many tokens per day
ai21.jamba-1-5-mini-v1:0                      ThrottlingException: Too many tokens per day
```

**Conclusion.** This is not a permissions, model-access, or form problem — all three are
resolved. The per-day ceilings are hard-set to 0 and **not adjustable via Service Quotas**,
which is characteristic of an AWS account that has not been upgraded from the Free Plan.
The `$134` credit cannot be spent on Bedrock while this holds.

**Impact is confined to Phase 3.** Phase 2 already specifies a stub agent, so the queue,
slot scheduler, token accounting, WebSocket sync, and deployment — the entire product
thesis — are all buildable and demonstrable now.

### ✅ Resolved — IAM permissions

`AdministratorAccess` attached to `hiveos-dev`; verified by the Bedrock error changing
from `AccessDeniedException` to a quota throttle.

### ✅ Resolved — Anthropic use-case form

**Submitted and confirmed cleared 2026-09-18.** Proven by the smoke-test error
*changing* from the form gate to a plain IAM denial:

```
before:  ResourceNotFoundException — "Model use case details have not been submitted"
after:   AccessDeniedException     — "not authorized to perform bedrock:InvokeModel"
```

The account-level Anthropic gate is open. Once IAM is fixed, Bedrock should work
with no further console steps.

**Also learned:** the Bedrock *Model access* page is retired. Serverless models now
auto-enable on first invoke in all commercial regions; the Anthropic use-case form
was the only remaining gate and it lives on the **Model catalog** page, not Model access.

---

## Manual actions pending

1. **Attach `AdministratorAccess` to `hiveos-dev`** — Console → IAM → Users → `hiveos-dev`
   → Permissions → Add permissions → Attach policies directly → `AdministratorAccess`. **BLOCKING.**
2. **Start Docker Desktop** — required for `sam build --use-container`. `DEPLOYMENT.md` → Manual Action 3.
3. **Confirm AWS Budget notification email** — once the budget is created.

---

## Bedrock model catalogue (enumerated 2026-09-18)

Inference profiles available in `us-east-1` include `us.anthropic.claude-haiku-4-5-20251001-v1:0`,
`us.anthropic.claude-sonnet-5`, `us.anthropic.claude-opus-5`, and `global.` equivalents.

**Intended target:** `us.anthropic.claude-haiku-4-5-20251001-v1:0` — fastest and cheapest tier,
which matters more for demo pacing on video than raw model capability.
**Not yet confirmed invocable** — pending the use-case form.

---

## Known issues and discoveries

- **Local Python is 3.14**, newer than any Lambda runtime. Compiled dependencies (e.g. `pydantic-core` via Strands) would install wrong-platform wheels if built locally. **Always use `sam build --use-container`.**
- **Docker is installed but the daemon is not running.** Must be started before the first `sam build`.
- **AWS account is Free Plan with ~$134 credit.** Ample for this build, but Bedrock availability on a Free Plan account is unverified — Phase 0 proves it with a real call before anything depends on it.
- **`gh` CLI is authenticated** as `arunishrajput` with `repo` + `workflow` scopes — repo creation and push are fully automatable.
- **npm global prefix `~/.local/lib` does not exist**, so `npm install -g` may fail. Avoid global npm installs; use `npx`.

---

## Current deployment state

| Resource | Status |
|---|---|
| CloudFormation stack `hiveos` | Does not exist |
| DynamoDB table | Not created |
| WebSocket API | Not created |
| SQS queue + DLQ | Not created |
| Router Lambda | Not created |
| Agent Runner Lambda | Not created |
| Amplify app | Not created |
| Public URL | None |

---

## Next recommended action

1. **You:** submit the Anthropic use-case details form (blocking, ~15 min propagation).
2. **Meanwhile, Claude Code:** finish the rest of Phase 0 — SAM install, Docker, budget alarm,
   GitHub repo, and the table-only `sam deploy`. None of it depends on Bedrock.
3. Re-run the Bedrock smoke test once the form propagates; record the working model ID in `CONTRACT.md`.
