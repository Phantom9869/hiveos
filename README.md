# HiveOS

**The OS scheduler for your team's shared AI budget.** Queue management for AI agents, visible to everyone in real time.

Teams sharing AI agents have no visibility into usage, no fairness mechanism for access, and no real-time governance over token spend — one heavy agentic task can drain a monthly budget in minutes and nobody sees it happen. Operating systems solved this for CPU fifty years ago with scheduling, quotas, and fair queueing. HiveOS applies that abstraction to team AI compute.

Built for the **First Commit** hackathon (WeMakeDevs × AWS), Ship It track.

**Live URL:** **https://main.dbavt8jr66qxx.amplifyapp.com** — opens cold, no setup, no sign-in.

![The HiveOS operator console](docs/hud.png)

---

## What it does

- Each **workspace** is fully isolated — its own budget, slots, queue, memory and ledger,
  and can be **passphrase-protected**, with an owner who can set the budget or delete it
- A team shares a **token budget** and a pool of **agent slots**
- The budget meter is **identical on every member's screen** and updates live
- When all slots are busy, further requests **queue** with a real position
- A freed slot **auto-dispatches** the next queued task
- Agents share **team memory** — a fact saved by one member is known to the next member's agent
- The budget is an **enforced ceiling**, not a gauge — the server refuses to spend past it
- A shared **workspace floor** shows who is present and who is mid-task, live on every screen

**Status.** Everything above is live, deployed and verified against real AWS — 79/79 end-to-end
checks (`scripts/ws_smoke.py`) and the full demo sequence 12/12 twice unattended
(`scripts/rehearse.py`). The agent is real and the token counts are the provider's reported
usage, not estimates.

> **One honest note: model inference is the only thing not running on AWS.** Amazon Bedrock is
> blocked account-wide here — `us-east-1`, `us-west-2` and `ap-south-1` all refuse, Marketplace
> models with `INVALID_PAYMENT_INSTRUMENT` and first-party Amazon Nova against a per-day token
> quota of zero that reports `adjustable=False`. 42 of 43 quotas sit at zero. That is an
> account-level restriction, not a setting.
>
> Inference therefore calls out to Groq (`openai/gpt-oss-120b`) over HTTPS. The API Gateway
> WebSocket, both Lambdas, SQS, DynamoDB and Amplify are unchanged — one outbound HTTP call is
> the entire difference, and it touched exactly one function.
>
> If the model is ever unreachable the workspace still answers, from composed text, and flags
> those counts `estimated` everywhere they appear — including on `state_snapshot`, so a browser
> loading cold is told too. A degraded answer never gets laundered into a billed-looking meter.

See `PROGRESS.md` for the full evidence and `ARCHITECTURE.md` decision 7 for the reasoning.

---

## Architecture at a glance

```
Browser ──wss──► API Gateway WebSocket ──► Router Lambda ──► DynamoDB
                                                │                 ▲
                                                ▼                 │
                                               SQS ──► Agent Runner Lambda ──► Groq
```

React frontend on Amplify Hosting. Everything serverless, scaling to zero.

Full detail and the reasoning behind each choice: **`ARCHITECTURE.md`**.

---

## Documentation map

Read in this order:

| File | Read it for |
|---|---|
| **`CLAUDE.md`** | How to work on this project. **Start here.** |
| **`PROGRESS.md`** | Where things stand right now |
| **`BUILD_PLAN.md`** | The seven phases and what each must deliver |
| `PRD.md` | What the MVP is and is not |
| `ARCHITECTURE.md` | System design and every rejected alternative |
| `CONTRACT.md` | Schemas, protocols, and interfaces that must not drift |
| `DEPLOYMENT.md` | AWS setup, deploy commands, manual actions, troubleshooting |
| `DEMO.md` | The recording run sheet — checklist, beats, narration, fallbacks |
| `SUBMISSION.md` | The hackathon writeup |

**Fastest path to understanding:** `README.md` → `PROGRESS.md` → `ARCHITECTURE.md`.

---

## Setup

Requires: AWS CLI (configured), AWS SAM CLI, Docker (running), Node 20+, Python 3.11+.

```bash
brew install aws-sam-cli
aws configure                 # see DEPLOYMENT.md, Manual Action 1
```

The agent needs a model API key before it will answer. It is read at runtime from SSM
Parameter Store and never stored in this repository:

```bash
aws ssm put-parameter --name /hiveos/groq-api-key --type SecureString \
  --value 'gsk_...' --region us-east-1 --overwrite
```

Get a free key at <https://console.groq.com> (no card required). Without it the workspace
still runs — every task falls back to composed text flagged `estimated`.

---

## Deploy

```bash
# Backend — always --use-container; local Python is newer than the Lambda runtime
sam build --use-container
sam deploy

# Frontend — resolves the WebSocket URL from the stack, builds, zips, publishes
./scripts/deploy-frontend.sh
```

`deploy-frontend.sh` is idempotent: it reuses the existing Amplify app and branch rather than
creating duplicates, and it fails loudly if the WebSocket URL did not make it into the bundle.

For local development against the deployed backend:

```bash
cd frontend
npm install
VITE_WS_URL="$(aws cloudformation describe-stacks --stack-name hiveos \
  --query "Stacks[0].Outputs[?OutputKey=='WebSocketURL'].OutputValue" --output text)" \
  npm run dev
```

Stack outputs (WebSocket URL, table name, queue URL):

```bash
aws cloudformation describe-stacks --stack-name hiveos \
  --query 'Stacks[0].Outputs' --output table
```

Verify the deployed backend actually behaves — two live clients, fan-out, and stale-connection cleanup, all against real AWS:

```bash
pip install websockets
./scripts/reset-demo.sh               # clean board, SQS drained, Lambdas warm — and verified
python scripts/ws_smoke.py            # 79 checks: backbone, scheduler, memory, ceiling, avatars, fairness, isolation, passphrases, admin
python scripts/rehearse.py --takes 2  # the recorded demo sequence, 12 checks, unattended
```

Close any browser tab pointed at the deployed URL first — the connection-leak checks assert the
table holds no `CONN#` rows, so a live browser fails four of them. `reset-demo.sh` warns you
when it finds live rows.

`ws_smoke.py` asks whether the system is **correct**; `rehearse.py` asks whether the sequence
about to be **recorded** works, in order, inside the time available, and times every beat.

Full procedure, verification steps, and troubleshooting: `DEPLOYMENT.md`.

---

## Repository layout

```
backend/
  router/          Connection lifecycle, slot claiming, queueing, broadcast
  agent_runner/    SQS consumer, agent execution, token accounting, dispatch
  shared/          Slot scheduler, broadcast helper, memory tools, DynamoDB access
  shared/memory.py Team memory — MEMORY# rows, context loading, memory_updated
frontend/
  src/useHive.js   WebSocket client — owns all board state, reconnect, re-sync
  src/components.jsx  Quota strip, slot cards, run queue, workspace floor, toasts, activity log
  src/App.jsx      Entry gate, request form, board layout
scripts/           Seeding, demo reset, smoke test, demo rehearsal, frontend deploy
template.yaml      SAM — all AWS infrastructure
```

---

## Current MVP scope

**Built and deployed:** shared token meter · agent slots · **fair queueing** with live position · auto-dispatch · shared team memory via **model-invoked tools** · **per-person spend ledger** · enforced budget ceiling · pixel workspace floor · team chat · public URL.

**Not built:** user accounts. Workspaces can be passphrase-protected and have an owner, but
there is no identity behind a display name — administration hangs off a secret the creator
holds, not off who anyone claims to be. Also unbuilt: any retention policy on the task ledger.
See `PRD.md` and `ARCHITECTURE.md` for what was deliberately excluded.

**Deliberately excluded:** authentication, multiple teams, game-engine graphics, token-level preemption, calendar/email integrations. See `PRD.md` and `ARCHITECTURE.md` for why.

---

## Built with

AWS Lambda · API Gateway WebSocket · DynamoDB · SQS · SSM Parameter Store · AWS SAM ·
Amplify Hosting · React + Vite

Model inference runs on Groq because Amazon Bedrock is blocked account-wide on this account —
see **Status** above.

Developed with Claude Code as the implementation assistant.
