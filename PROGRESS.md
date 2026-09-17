# PROGRESS.md

> Current execution state. A fresh Claude Code session reads this to know exactly where things stand.
> Keep it operational and short. Not a diary — history lives in git.

**Last updated:** 2026-09-17

---

## Project status

| | |
|---|---|
| **Project** | HiveOS — OS-style scheduler for a team's shared AI agent budget |
| **Track** | Ship It (deployed, public URL) |
| **Deadline** | 2026-09-20 |
| **Current phase** | **Phase 0 — Pre-project setup** |
| **Phase status** | `NOT STARTED` |
| **Deployment state** | Nothing deployed. No AWS stack exists. |
| **Repository** | Not yet created on GitHub |

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

- [ ] Configure AWS credentials (**MANUAL**)
- [ ] Request Bedrock model access in console (**MANUAL** — start first, longest lead time)
- [ ] Verify Bedrock with a real `converse` call; record working model ID in `CONTRACT.md`
- [ ] Start Docker Desktop (**MANUAL**)
- [ ] Install AWS SAM CLI
- [ ] Create AWS Budget alarm (~$20)
- [ ] `git init`, create GitHub repo, initial commit, push
- [ ] Inventory existing AWS resources for name collisions
- [ ] `sam deploy` with DynamoDB table only — prove the deployment pipeline

Full detail in `BUILD_PLAN.md` → Phase 0.

---

## Completed

Nothing yet. Repository scaffold created 2026-09-17 (docs + SAM skeleton, no application code).

---

## Blocked

| Blocker | Blocks | Resolution |
|---|---|---|
| No AWS credentials on this machine | All AWS work | Phase 0 task 1 — see `DEPLOYMENT.md` → Manual Action 1 |
| Bedrock model access unverified | Phase 3 | Phase 0 tasks 2–3 — see `DEPLOYMENT.md` → Manual Action 2 |

---

## Manual actions pending

1. **AWS credentials** — `aws configure` or SSO login. `DEPLOYMENT.md` → Manual Action 1.
2. **Bedrock model access** — Console → Bedrock → Model access → Anthropic. `DEPLOYMENT.md` → Manual Action 2.
3. **Start Docker Desktop** — required for `sam build --use-container`. `DEPLOYMENT.md` → Manual Action 3.

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

Start Phase 0. Begin with the Bedrock model access request — it has the longest lead time and blocks Phase 3.
