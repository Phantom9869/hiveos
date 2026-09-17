# CLAUDE.md — How to work on HiveOS

Read this file, `PROGRESS.md`, and `BUILD_PLAN.md` before doing anything else. Then read only the docs the next phase actually needs.

---

## Project identity

**HiveOS** is a cloud-deployed team workspace where human members request shared AI agents through an OS-style queue, with a real-time token budget visible to everyone. Built entirely on AWS.

**One sentence:** HiveOS is the OS scheduler for your team's shared AI budget — queue management for AI agents, visible to everyone in real time.

Teams sharing AI agents have no visibility into usage, no fairness mechanism for access, and no real-time governance over token spend. One heavy agentic task can drain a monthly budget in minutes and nobody sees it happen. Operating systems solved this for CPU 50 years ago: scheduling, quotas, fair queueing. HiveOS applies that abstraction to team AI compute.

**Context:** First Commit hackathon (WeMakeDevs × AWS), **Ship It** track — deployed, public URL. Window **17–20 September 2026**. Solo developer, ~72 hours. A 3-minute recorded video is the only thing judges see.

---

## Session workflow

When the user says **"Start the next phase"**:

1. `git status` — check for uncommitted work from an interrupted session.
2. Read `CLAUDE.md`, `PROGRESS.md`, `BUILD_PLAN.md`.
3. Read only the additional docs the next phase needs (`CONTRACT.md` before touching the schema or protocol; `DEPLOYMENT.md` before any AWS work).
4. Determine: current phase, whether the previous phase genuinely completed, blockers, pending manual actions, current branch, current deployment state.
5. Verify deployment state against **AWS itself** (`aws cloudformation describe-stacks --stack-name hiveos`), not against what `PROGRESS.md` claims.
6. Implement **one phase**. Not more.
7. End-of-phase protocol (below).

Do not ask "what should I work on?" — the repository answers that. Ask only if the repo genuinely cannot determine the next step.

### End-of-phase protocol

```
Finish implementation
→ Run the phase's validation steps
→ git status && git diff   (inspect — never commit blind)
→ Verify no secrets in the diff
→ Update PROGRESS.md
→ Update any docs the phase changed
→ git add && git commit && git push
→ Confirm the push succeeded
→ Report the phase summary
→ STOP
```

Then tell the user to run `/clear`. Do **not** silently begin the next phase in the same session.

### Phase completion summary format

```
PHASE COMPLETE: <number — name>

Implemented:
Verified:
Deployment:
Documentation:
Git: commit <sha>, push successful
Current project state:
Next phase:
Manual actions required:
```

---

## Source of truth

When sources disagree, higher wins:

1. **Deployed AWS state** — what actually exists and runs
2. **Repository code** — what is actually implemented
3. **`CONTRACT.md`** — shared interfaces that must not drift
4. **`PRD.md`** — what the MVP is supposed to do
5. **`ARCHITECTURE.md`** — how the system is intended to fit together
6. **`BUILD_PLAN.md`** — how implementation is phased
7. **`PROGRESS.md`** — current execution state
8. Other docs
9. **Conversation history** — background, never an automatic override

Never assume documentation is current. On a discrepancy: detect it, determine what actually exists, determine what was intended, correct the appropriate source, and record it in `PROGRESS.md`. Escalate to the user when the gap materially changes scope, security, cost, or core architecture.

---

## Scope control

Before implementing anything, classify it:

- **MVP-Critical** — required for the demo or deployment. Build it.
- **MVP-Supporting** — useful if time permits. Build only after all MVP-Critical work is done and verified.
- **Post-Hackathon** — do not build now.
- **Out of Scope** — never build. See the never-build list below.

Ask: is it in `PRD.md`'s Must list? Does it appear in the demo script? Does it unblock required work? Is it needed for deployment or demo reliability? If no to all — it is Post-Hackathon.

Priority order, always:

> **Working deployed software > architectural correctness > feature count > documentation polish**

### Never build

Phaser.js or any game engine · Google OAuth / Gmail / Calendar · CPU-style token preemption mid-generation · multiple DynamoDB tables · a mobile app · voice or video · an agent marketplace · multi-team analytics · Cognito (MVP) · any AWS service not already in `ARCHITECTURE.md`

Each of these was considered and rejected with reasons recorded in `ARCHITECTURE.md`. Do not reintroduce them.

---

## AWS workflow

**All infrastructure lives in `template.yaml` and is deployed with SAM.** Never create a stack-managed resource with a one-off `aws ... create-*` command — CloudFormation owns the inventory, and side-created resources cause drift and duplicates across `/clear` sessions.

```bash
sam build --use-container      # builds in the Lambda image; local Python is 3.14 and would produce wrong-platform wheels
sam deploy                     # config lives in samconfig.toml
```

Prefer the AWS CLI for everything else: identity checks, inspecting resources, reading logs, testing deployed endpoints, verifying deployments.

**Before creating any resource, check whether it already exists.** `aws cloudformation describe-stacks --stack-name hiveos` is the authoritative answer.

### Deployment is never assumed

A zero exit code is not proof. A deployment is verified only when the deployed system **behaves correctly**: a real request returns a real response, logs show the expected path, the database holds the expected item. Every phase gate in `BUILD_PLAN.md` is a runtime check for this reason.

### Manual actions

Some steps cannot be done from the CLI. When one is required, output exactly this and stop work that depends on it:

```
MANUAL ACTION REQUIRED

Reason:          <why this is needed>
Location:        <exact AWS Console path>
Steps:           1. ... 2. ... 3. ...
Expected result: <what you should see afterward>
Verification:    <the exact command Claude Code will run to confirm>
Resume by:       <what to tell Claude Code once done>
```

Never write "configure this in AWS." Give the exact path, the exact setting, the exact value.

Continue with anything that does *not* depend on the manual action. If the phase is genuinely blocked, mark it `BLOCKED — WAITING FOR MANUAL ACTION` in `PROGRESS.md` with the blocker, the reason, and the verification command. **Do not mark a phase complete because most of it is done.**

---

## Cost control

The AWS account is a Free Plan with limited credit. A careful demo costs a few dollars; a runaway loop does not.

- The token ceiling is **enforced in code** — Agent Runner refuses to call Bedrock once `tokens_used >= token_budget`. This is a product feature *and* a spend guard. Never disable it to "make the demo work."
- Keep `max_tokens` low per call.
- Prefer the fastest/cheapest model with access granted — it also makes the demo feel snappier on video.
- An AWS Budget alarm is the backstop. Do not remove it.

---

## Git workflow

Solo developer: **work directly on `main`.** No feature branches, no PR ceremony.

- Commit at the end of each phase, and at meaningful checkpoints within a long phase.
- Inspect `git diff` before every commit.
- Commit messages identify the phase: `feat: complete phase 02 scheduler and queue`
- Push after every commit — GitHub is the recovery point after `/clear`.
- Never force-push.

### Never commit

AWS credentials or access keys · API keys or tokens · `.env` files · `samconfig.toml` if it ever contains secrets · anything under `.aws/` · generated build artifacts (`.aws-sam/`, `node_modules/`, `dist/`)

Check the diff for these every time. If a secret was ever committed, stop and tell the user — rotation is required, not just a revert.

---

## Testing

This is a 72-hour hackathon MVP. Do not build a large test suite.

Test what can break the demo:
- Critical path — slot claim, queue, dispatch, token accounting, broadcast
- Integration — components actually working together against deployed AWS
- Deployment — the deployed system genuinely responds
- Smoke — the app still starts and the core flow still runs

Do not write tests for coverage numbers.

---

## Debugging

1. Reproduce it.
2. Read the actual logs (`sam logs -n <fn> --stack-name hiveos --tail`) — do not guess.
3. Find the smallest plausible root cause.
4. Fix that.
5. Re-run the failing verification.
6. Re-run related checks for regressions.
7. Record anything materially surprising in `PROGRESS.md`.

Never rewrite large areas of working code to chase a bug.

---

## Security

Never commit secrets. Never hardcode credentials. Never log credentials or full request bodies that might contain them. Never disable a security control to make something work. Never delete or overwrite an AWS resource without understanding what depends on it — and for anything destructive, explain the intended action and confirm first.

The deployed URL is public and unauthenticated by design for the MVP. That is an accepted, documented tradeoff — which is exactly why the server-side token ceiling is mandatory.

---

## When the plan and reality diverge

You will find things the plan got wrong — an AWS limit, an unsuitable dependency, a mis-sized phase, an approach that cannot work. Do not blindly follow the plan.

1. Name the discrepancy.
2. State the practical impact.
3. Choose the simplest solution preserving the MVP goal.
4. Update the affected docs and `PROGRESS.md`.
5. Continue if the change is safe.

Stop for the user only when the change materially alters product scope, security, cost, or core architecture.

---

## Recovering from an interrupted session

If `git status` shows uncommitted work:

1. Read the diff to understand what was in flight.
2. Cross-check `PROGRESS.md` for the phase that was running.
3. Verify actual AWS state — the previous session may have deployed something it never recorded.
4. Either finish the phase properly, or revert cleanly to the last commit. Do not leave the repo half-changed.
5. Correct `PROGRESS.md` to match reality before continuing.

---

## Working style

- Backend before frontend. The scheduler must work headlessly before any UI exists.
- Thin vertical slices — build, verify, then expand.
- Prefer boring and obvious over clever. Fewer moving parts, fewer dependencies, simpler deploys.
- Surface assumptions before acting on them. If requirements conflict, stop and ask rather than guessing.
- Push back when an approach has a real problem. Explain the concrete downside and propose an alternative.
- Report outcomes honestly — if a check fails, say so with the output; if a step was skipped, say that.
