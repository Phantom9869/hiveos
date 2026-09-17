# HiveOS

**The OS scheduler for your team's shared AI budget.** Queue management for AI agents, visible to everyone in real time.

Teams sharing AI agents have no visibility into usage, no fairness mechanism for access, and no real-time governance over token spend — one heavy agentic task can drain a monthly budget in minutes and nobody sees it happen. Operating systems solved this for CPU fifty years ago with scheduling, quotas, and fair queueing. HiveOS applies that abstraction to team AI compute.

Built for the **First Commit** hackathon (WeMakeDevs × AWS), Ship It track.

**Live URL:** _not yet deployed — see `PROGRESS.md`_

---

## What it does

- One team shares a **token budget** and a pool of **agent slots**
- The budget meter is **identical on every member's screen** and updates live
- When all slots are busy, further requests **queue** with a real position
- A freed slot **auto-dispatches** the next queued task
- Agents share **team memory** — a fact saved by one member is known to the next member's agent
- The budget is an **enforced ceiling**, not a gauge — the server refuses to spend past it

---

## Architecture at a glance

```
Browser ──wss──► API Gateway WebSocket ──► Router Lambda ──► DynamoDB
                                                │                 ▲
                                                ▼                 │
                                               SQS ──► Agent Runner Lambda ──► Bedrock
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

**Fastest path to understanding:** `README.md` → `PROGRESS.md` → `ARCHITECTURE.md`.

---

## Setup

Requires: AWS CLI (configured), AWS SAM CLI, Docker (running), Node 20+, Python 3.11+.

```bash
brew install aws-sam-cli
aws configure                 # see DEPLOYMENT.md, Manual Action 1
```

Bedrock model access must be requested in the console before the agent works — `DEPLOYMENT.md`, Manual Action 2.

---

## Deploy

```bash
# Backend — always --use-container; local Python is newer than the Lambda runtime
sam build --use-container
sam deploy

# Frontend
./scripts/deploy-frontend.sh
```

Stack outputs (WebSocket URL, table name, queue URL):

```bash
aws cloudformation describe-stacks --stack-name hiveos \
  --query 'Stacks[0].Outputs' --output table
```

Full procedure, verification steps, and troubleshooting: `DEPLOYMENT.md`.

---

## Repository layout

```
backend/
  router/          Connection lifecycle, slot claiming, queueing, broadcast
  agent_runner/    SQS consumer, agent execution, token accounting, dispatch
  shared/          Broadcast helper, memory tools, DynamoDB access
frontend/          React + Vite — HUD, workspace, agent chat
scripts/           Seeding, demo reset, frontend deploy
template.yaml      SAM — all AWS infrastructure
```

---

## Current MVP scope

**Working toward:** shared token meter · agent slots · FIFO queue with live position · auto-dispatch · shared team memory · enforced budget ceiling · public URL.

**Deliberately excluded:** authentication, multiple teams, game-engine graphics, token-level preemption, calendar/email integrations. See `PRD.md` and `ARCHITECTURE.md` for why.

---

## Built with

AWS Lambda · API Gateway WebSocket · DynamoDB · SQS · Amazon Bedrock · Strands Agents SDK · AWS SAM · Amplify Hosting · React + Vite

Developed with Claude Code as the implementation assistant.
