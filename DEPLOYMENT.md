# DEPLOYMENT.md — HiveOS

Operational AWS guide. Every step is tagged **`AUTOMATED`** (Claude Code runs it) or **`MANUAL HUMAN ACTION`** (only you can).

| | |
|---|---|
| Region | `us-east-1` |
| Stack | `hiveos` |
| Account | Free Plan, ~$134 credit at project start |

---

## Prerequisites

| Requirement | State on this machine |
|---|---|
| AWS CLI | ✅ 2.36.47 |
| AWS credentials | ❌ **Not configured** — Manual Action 1 |
| Bedrock model access | ❌ **Unverified** — Manual Action 2 |
| Docker daemon | ❌ **Not running** — Manual Action 3 |
| AWS SAM CLI | ❌ Not installed — `brew install aws-sam-cli` |
| Node / npm | ✅ 26.8.2 / 11.19.1 |
| Python | ⚠️ 3.14 locally — **newer than any Lambda runtime** |
| git + gh | ✅ authenticated as `arunishrajput` |

> **Why Docker is mandatory.** Local Python is 3.14; Lambda runs 3.13. Building dependencies locally produces wrong-platform wheels for compiled packages like `pydantic-core`. `sam build --use-container` builds inside the official Lambda image and sidesteps this entirely. Always use it.

---

## MANUAL ACTION 1 — Configure AWS credentials

**Reason:** No credentials exist on this machine. Every AWS call currently fails at credential resolution, so nothing can be created, inspected, or deployed.

**Location:** AWS Console → top-right account menu → **Security credentials** → **Access keys** → *Create access key* → choose **Command Line Interface (CLI)**.

**Steps:**
1. Create the access key and copy both the Access Key ID and Secret Access Key.
2. In your terminal run `aws configure`.
3. Enter the Access Key ID, then the Secret Access Key.
4. Default region: `us-east-1`
5. Default output format: `json`

**Expected result:** `~/.aws/credentials` now exists with a `[default]` profile.

**Verification:**
```bash
aws sts get-caller-identity
```
Returns `UserId`, `Account`, and `Arn`.

**Resume by:** telling Claude Code *"credentials configured"*.

> **Never paste the secret key into a file in this repo, a commit, or a chat message.** `aws configure` writes it to `~/.aws/credentials`, which is outside the repo and git-ignored by location.

---

## MANUAL ACTION 2 — Request Bedrock model access

**Reason:** Bedrock models are not enabled by default. Without access, Phase 3 cannot run. This has the **longest lead time — start it first**.

**Location:** AWS Console → **Amazon Bedrock** → region **US East (N. Virginia) `us-east-1`** → left sidebar **Model access** → *Modify model access* / *Enable specific models*.

**Steps:**
1. Select the **Anthropic** models (prefer a Haiku-tier model for demo speed and cost; enable a Sonnet-tier as a backup).
2. Submit the access request.
3. Some models grant instantly; others need a short use-case form. Fill it in if prompted.
4. Wait until status reads **Access granted**.

**Expected result:** Model access page shows *Access granted* for at least one Anthropic model.

**Verification:**
```bash
aws bedrock list-inference-profiles --region us-east-1 \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId,'anthropic')].inferenceProfileId" \
  --output table
```

Then prove entitlement with a **real call** — a list operation does not prove you can invoke:
```bash
aws bedrock-runtime converse \
  --region us-east-1 \
  --model-id "<INFERENCE_PROFILE_ID>" \
  --messages '[{"role":"user","content":[{"text":"Say OK"}]}]' \
  --inference-config '{"maxTokens":16}'
```

**Resume by:** telling Claude Code *"Bedrock access granted"*. Claude Code records the working ID in `CONTRACT.md`.

> If access is denied or unavailable on a Free Plan account, say so. Phases 1–2 and 4 do not need Bedrock, and `BUILD_PLAN.md` carries a documented mock-agent fallback.

---

## MANUAL ACTION 3 — Start Docker Desktop

**Reason:** `sam build --use-container` needs a running Docker daemon to build Lambda packages against the correct Python runtime.

**Location:** macOS Applications → **Docker Desktop** → launch and wait for the whale icon to show *Running*.

**Verification:**
```bash
docker info
```
Prints server info rather than a socket connection error.

**Resume by:** telling Claude Code *"Docker running"*.

---

## MANUAL ACTION 4 — Verify the AWS Budget alarm

**Reason:** Second line of defence behind the in-app token ceiling. The public URL is unauthenticated by design.

Claude Code creates the budget with `aws budgets create-budget`; you must confirm the notification email.

**Location:** email inbox for `arunishrajput7@gmail.com` → AWS notification subscription confirmation.

**Verification:**
```bash
aws budgets describe-budgets --account-id "$(aws sts get-caller-identity --query Account --output text)"
```

---

## `AUTOMATED` — Backend deploy

```bash
brew install aws-sam-cli          # once
sam build --use-container         # always --use-container (see note above)
sam deploy --guided               # first time only; writes samconfig.toml
sam deploy                        # thereafter
```

**Deployment order is handled by CloudFormation** — do not create stack resources by hand. A one-off `aws dynamodb create-table` produces drift and duplicate resources across `/clear` sessions, which is exactly what the SAM template exists to prevent.

### Reading stack outputs

```bash
aws cloudformation describe-stacks --stack-name hiveos \
  --query 'Stacks[0].Outputs' --output table
```

Outputs include the WebSocket URL, table name, and queue URL.

---

## `AUTOMATED` — Frontend deploy

Amplify Hosting in **manual deploy mode** — no GitHub OAuth required, fully scriptable.

```bash
cd frontend && npm run build && cd ..
# zip dist/, then:
aws amplify create-app --name hiveos --region us-east-1                 # once
aws amplify create-branch --app-id <APP_ID> --branch-name main          # once
aws amplify create-deployment --app-id <APP_ID> --branch-name main
# upload the zip to the returned URL, then:
aws amplify start-deployment --app-id <APP_ID> --branch-name main --job-id <JOB_ID>
```

Wrapped in `scripts/deploy-frontend.sh` from Phase 4.

The WebSocket URL is injected at build time as a Vite env var (`VITE_WS_URL`) from the stack output.

---

## `AUTOMATED` — Verification

Never trust a zero exit code. Verify behaviour.

```bash
# Identity and stack
aws sts get-caller-identity
aws cloudformation describe-stacks --stack-name hiveos --query 'Stacks[0].StackStatus'

# DynamoDB round trip
aws dynamodb get-item --table-name hiveos-state \
  --key '{"PK":{"S":"TEAM#alpha"},"SK":{"S":"METADATA"}}'

# WebSocket (two terminals)
npx wscat -c "$(aws cloudformation describe-stacks --stack-name hiveos \
  --query "Stacks[0].Outputs[?OutputKey=='WebSocketURL'].OutputValue" --output text)"

# Queue depth
aws sqs get-queue-attributes --queue-url <QUEUE_URL> \
  --attribute-names ApproximateNumberOfMessages
```

---

## `AUTOMATED` — Logs

```bash
sam logs -n RouterFunction      --stack-name hiveos --tail
sam logs -n AgentRunnerFunction --stack-name hiveos --tail

aws logs tail /aws/lambda/hiveos-router --follow --since 10m
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `NoCredentials` on every call | Credentials not configured | Manual Action 1 |
| `AccessDeniedException` calling Bedrock | Model access not granted | Manual Action 2 |
| `sam build` fails on a compiled dependency | Built with local Python 3.14 | Use `sam build --use-container` |
| `sam build --use-container` cannot connect | Docker daemon down | Manual Action 3 |
| Broadcast fails with `403` | Missing `execute-api:ManageConnections` | Add to the Lambda IAM policy in `template.yaml` |
| Broadcast loop crashes mid-demo | GoneException unhandled | Implement the handler in `CONTRACT.md` — mandatory |
| Messages land in the DLQ | Agent Runner throwing | Read the DLQ body and CloudWatch logs; check the slot is released in `finally` |
| Slot stuck `BUSY` forever | Runner failed before release | Release must be in `try/finally`; reset with `scripts/reset-demo.py` |
| Stack stuck in `ROLLBACK_COMPLETE` | Failed first create | `aws cloudformation delete-stack --stack-name hiveos`, wait, redeploy |
| Frontend loads but never connects | Wrong `VITE_WS_URL` at build time | Rebuild with the current stack output |

---

## Cost control

| Guard | Where |
|---|---|
| Server-side token ceiling — refuses Bedrock at 100% | Agent Runner (`ARCHITECTURE.md` decision 4) |
| Low `max_tokens` per call | `MAX_TOKENS_PER_CALL` env var |
| Fastest/cheapest model with access | `BEDROCK_MODEL_ID` in `CONTRACT.md` |
| AWS Budget alarm (~$20) | Manual Action 4 |
| Everything scales to zero | Lambda, DynamoDB on-demand, SQS |

Expected total for build and demo: a few dollars. The risk is a runaway loop, not baseline usage.

---

## Teardown (after the hackathon)

```bash
sam delete --stack-name hiveos
aws amplify delete-app --app-id <APP_ID>
```

Do **not** tear down before judging completes — the public URL must stay reachable.
