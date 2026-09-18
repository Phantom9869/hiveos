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
| AWS credentials | ✅ IAM user `hiveos-dev` (Manual Action 1 done) |
| Bedrock model access | ⛔ **Blocked account-wide, and abandoned** — not a config problem. Inference runs on Groq; see `ARCHITECTURE.md` decision 7 |
| Model API key | ✅ SSM SecureString `/hiveos/groq-api-key`, read at runtime |
| Docker daemon | ✅ running (Manual Action 3 done) |
| AWS SAM CLI | ✅ 1.166.2 |
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

## MANUAL ACTION 2 — Anthropic use-case form ✅ DONE (2026-09-18)

> **The *Model access* page is retired.** Serverless foundation models now auto-enable on
> first invoke across all AWS commercial regions. There is no per-model enabling step.
> The only remaining gate for Anthropic models is a one-time account-level use-case form,
> and it lives as a **banner on the Model catalog page** — not on Model access.

**Location (for reference):** Console → **Amazon Bedrock** → `us-east-1` → **Model catalog**
→ banner *"Anthropic requires first-time customers to submit use case details"* → **Submit use case details**.

Form fields: company name, company website URL, industry, intended users (internal/external),
and a ≤500-char use-case description. The submission is shared with Anthropic.

**Status:** submitted and confirmed cleared. Verified by the smoke-test error changing from
`ResourceNotFoundException` (form gate) to a quota error — a different error means this gate passed.

---

## MANUAL ACTION 2b — AWS Paid Plan upgrade ⚠️ OUTSTANDING

**Reason:** Bedrock invocation is quota-blocked account-wide. **42 of 44** per-model per-day
token quotas are `0` and **all are `adjustable=False`**, so a Service Quotas increase request
is not possible. This blocks Phase 3 only.

**Location:** AWS Console → **Billing and Cost Management** → account/plan settings → upgrade
from **Free Plan** to **Paid Plan**.

**Expected result:** per-model per-day token quotas become non-zero; the `$134` credit becomes
spendable on Bedrock.

**Verification:** re-run the smoke test below. Success looks like a real completion, not a throttle.

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

> ### ⛔ Superseded, 2026-09-18 — this entire manual action is obsolete
>
> Bedrock is blocked account-wide and the block is not a setting: `us-east-1`, `us-west-2` and
> `ap-south-1` all refuse, 42 of 43 per-day token quotas are zero and report
> `adjustable=False`, and first-party Amazon Nova — which needs neither a Marketplace
> subscription nor a payment instrument — fails identically. The use-case form was submitted and
> cleared; that was never the binding constraint.
>
> **Inference runs on Groq instead — see MANUAL ACTION 2b.** Do not spend session time
> re-attempting this. One `converse` call is enough to detect if it ever unlocks.

---

## MANUAL ACTION 2b — Provision the model API key

**Reason:** The Agent Runner reads its model API key from SSM Parameter Store at runtime.
Without it every task falls back to composed text flagged `estimated`.

**Location:** <https://console.groq.com> → sign in (Google/GitHub, free, **no card required**)
→ **API Keys** → **Create API Key**. The key is shown once and begins with `gsk_`.

**Steps:** run this in a normal terminal — not through an agent session, where the key would
land in a transcript:

```bash
aws ssm put-parameter \
  --name /hiveos/groq-api-key \
  --type SecureString \
  --value 'gsk_...' \
  --region us-east-1 \
  --overwrite
```

**Expected result:** JSON containing `"Version": 1`. **No redeploy is needed** — the Lambda
retries the SSM read on every task until it succeeds, so the key goes live by itself.

**Verification:**

```bash
aws ssm get-parameter --name /hiveos/groq-api-key --region us-east-1 --query 'Parameter.Type'
# → "SecureString"   (reads the type only, never the value)
python3 scripts/ws_smoke.py   # 58/58, and token frames report estimated=False
```

**Status:** done, 2026-09-18. Verified live on the public URL.

> The key never enters `template.yaml`, `samconfig.toml`, an environment variable, or git. The
> Agent Runner's IAM grant is scoped to this one parameter.

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
sam deploy                        # config comes from samconfig.toml, which is committed
```

`samconfig.toml` is in the repository and holds no secrets — stack name, region, and build flags only. `--guided` is not needed; running it would only overwrite that file.

**Deployment order is handled by CloudFormation** — do not create stack resources by hand. A one-off `aws dynamodb create-table` produces drift and duplicate resources across `/clear` sessions, which is exactly what the SAM template exists to prevent.

### Reading stack outputs

```bash
aws cloudformation describe-stacks --stack-name hiveos \
  --query 'Stacks[0].Outputs' --output table
```

Outputs include the WebSocket URL, table name, and queue URL.

---

## `AUTOMATED` — Frontend deploy

Amplify Hosting in **manual deploy mode** — no GitHub OAuth, no build service role, fully
scriptable. One command:

```bash
./scripts/deploy-frontend.sh
```

| | |
|---|---|
| Amplify app | `hiveos` — app id **`dbavt8jr66qxx`** |
| Branch | `main` |
| Public URL | **https://main.dbavt8jr66qxx.amplifyapp.com** |

The script is idempotent and safe to re-run for every redeploy. It:

1. reads `WebSocketURL` from the stack output — the URL is never pasted by hand;
2. builds with `VITE_WS_URL` set;
3. **greps the built bundle for that URL and aborts if it is absent** — a frontend that loads
   but never connects is the likeliest failure here, and this catches it before publishing;
4. zips `dist/` with the files at the archive root;
5. finds-or-creates the app **by name** and the branch, so repeat runs never duplicate them;
6. uploads, starts the deployment, and polls `get-job` until `SUCCEED` rather than trusting the
   start call's exit code.

Override with env vars if needed: `STACK_NAME`, `AWS_REGION`, `APP_NAME`, `BRANCH`.

> **The Amplify app is not in `template.yaml`.** That is intentional — manual-deploy mode is
> what removes the OAuth and service-role setup. It does mean `describe-stacks` never mentions
> it and `sam delete` will not remove it. See Teardown.

### Local development against the deployed backend

```bash
cd frontend
npm install
VITE_WS_URL="$(aws cloudformation describe-stacks --stack-name hiveos \
  --query "Stacks[0].Outputs[?OutputKey=='WebSocketURL'].OutputValue" --output text)" \
  npm run dev
```

Each browser holds one identity in `localStorage['hiveos.identity']`, so two tabs of the same
origin share a name. Use separate browsers or profiles to act as different members.

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

# WebSocket — the real check. Two clients, fan-out, GoneException cleanup,
# and DynamoDB assertions, all against deployed AWS.
pip install websockets
python scripts/ws_smoke.py

# Queue depth
aws sqs get-queue-attributes --queue-url <QUEUE_URL> \
  --attribute-names ApproximateNumberOfMessages
```

`scripts/ws_smoke.py` resolves the endpoint from the stack output itself, so there is no URL to keep in sync. Run it after every backend deploy — it is the Phase 1 and Phase 2 gate, and the regression check for every phase after.

For poking by hand instead:

```bash
npx wscat -c "$(aws cloudformation describe-stacks --stack-name hiveos \
  --query "Stacks[0].Outputs[?OutputKey=='WebSocketURL'].OutputValue" \
  --output text)?user_id=alice"
> {"action":"hello"}
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
| `AccessDeniedException` calling Bedrock | Account-wide Bedrock block — **expected, do not chase** | Nothing. Inference runs on Groq |
| Agent replies `[coder · offline]` | The model call failed; the fallback answered | `sam logs -n AgentRunnerFunction --stack-name hiveos` and grep `model call failed` — the reason is logged verbatim |
| `groq HTTP 403 ... error code: 1010` | Cloudflare rejected the User-Agent | Not a bad key. `shared/llm.py` must send an explicit `User-Agent` |
| `groq HTTP 404 model_not_found` | Groq retired the model name | List `GET https://api.groq.com/openai/v1/models`, update `samconfig.toml` |
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
| Server-side token ceiling — refuses to call the model at 100% | Agent Runner (`ARCHITECTURE.md` decision 4) |
| Low `max_tokens` per call | `MAX_TOKENS_PER_CALL` env var |
| Fastest/cheapest model with access | `BEDROCK_MODEL_ID` in `CONTRACT.md` |
| AWS Budget alarm (~$20) | Manual Action 4 |
| Everything scales to zero | Lambda, DynamoDB on-demand, SQS |

Expected total for build and demo: a few dollars. The risk is a runaway loop, not baseline usage.

---

## Teardown (after the hackathon)

```bash
sam delete --stack-name hiveos
aws amplify delete-app --app-id dbavt8jr66qxx   # NOT covered by sam delete
```

Do **not** tear down before judging completes — the public URL must stay reachable.
