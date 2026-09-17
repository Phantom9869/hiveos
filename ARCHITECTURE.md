# ARCHITECTURE.md — HiveOS

Intended system design and the decisions behind it. Interfaces that must not drift live in `CONTRACT.md`.

---

## System overview

```
┌──────────────┐
│   Amplify    │  React + Vite, static hosting, public HTTPS URL
│   Hosting    │
└──────┬───────┘
       │  browser loads app, opens wss://
       ▼
┌──────────────────┐        ┌─────────────────────────────────────┐
│  API Gateway     │        │           Router Lambda             │
│  WebSocket API   │◄──────►│  $connect / $disconnect / $default  │
│                  │        │  slot claim (atomic)                │
│  $connect        │        │  enqueue + queue position           │
│  $disconnect     │        │  broadcast + GoneException handler   │
│  $default        │        └───────────┬─────────────────┬───────┘
└──────────────────┘                    │                 │
       ▲                                ▼                 ▼
       │                        ┌──────────────┐   ┌─────────────┐
       │ post_to_connection     │  DynamoDB    │   │     SQS     │
       │                        │ single table │   │ agent-tasks │
       │                        │              │   │   + DLQ     │
       │                        │ team meta    │   └──────┬──────┘
       │                        │ connections  │          │
       │                        │ agent slots  │          ▼
       │                        │ queue items  │   ┌──────────────────┐
       │                        │ team memory  │◄──┤  Agent Runner    │
       │                        └──────────────┘   │  Lambda          │
       │                                           │                  │
       └───────────────────────────────────────────┤  Strands agent   │
                                                   │  → Bedrock       │
                                                   │  token accounting │
                                                   │  release slot     │
                                                   │  dispatch next    │
                                                   └────────┬─────────┘
                                                            ▼
                                                   ┌──────────────────┐
                                                   │ Amazon Bedrock   │
                                                   │ (Claude)         │
                                                   └──────────────────┘
```

---

## Components

| Service | Role | Why this service |
|---|---|---|
| **API Gateway WebSocket** | Persistent browser connections; routes `$connect`, `$disconnect`, `$default` | The only AWS service providing persistent WebSocket connections with a serverless backend. No alternative. |
| **Router Lambda** | Connection lifecycle, message routing, atomic slot claiming, enqueue, broadcast, GoneException handling | Serverless, scales to zero, direct DynamoDB and SQS access |
| **DynamoDB** (single table) | All state: team metadata, connection IDs, slot states, queue entries, team memory | Serverless, fast, PK/SK pattern fits every access pattern; single table means fewer IAM grants and simpler debugging |
| **SQS** (+ DLQ) | Durable at-least-once handoff of every agent task to the runner | Makes task execution survive Lambda restarts, with retry and a dead-letter queue |
| **Agent Runner Lambda** | Consumes SQS, runs the agent, calls Bedrock, accounts tokens, broadcasts, releases the slot, dispatches the next queued task | Isolated from the Router so agent latency never blocks connection handling |
| **Amazon Bedrock** | Foundation model inference | Mandatory AWS integration; the model the agent actually runs on |
| **Strands Agents SDK** | Agent loop and tool calling | AWS-native open-source agent framework, named in the hackathon materials |
| **Amplify Hosting** | Static React frontend, public HTTPS URL | Fastest path to an HTTPS URL a judge can open cold; deployable from the CLI |

---

## Data flow

### Claiming an agent

```
Browser ──claim_agent──► Router Lambda
                           │
                           ├─ conditional UpdateItem: status IDLE → BUSY
                           │
                    ┌──────┴──────┐
              succeeded        failed (all slots busy)
                    │                │
         send task to SQS      write QUEUE#<ts> item
                    │                │
         broadcast slot state   broadcast queue position
                    │
                    ▼
            Agent Runner Lambda
                    │
                    ├─ load team memory from DynamoDB
                    ├─ check budget ceiling → refuse if exhausted
                    ├─ run agent → Bedrock
                    ├─ ADD tokens_used (atomic)
                    ├─ broadcast token_update + agent_response
                    ├─ set slot IDLE
                    └─ claim slot for oldest QUEUE# item → SQS → broadcast
```

### Broadcasting

Every state change fans out the same way: query connection IDs for the team from DynamoDB → `post_to_connection` per connection → on `GoneException`, delete that connection row immediately.

---

## Key decisions

### 1. The queue is gated by DynamoDB; SQS is the durable handoff

**Considered:** gate on SQS itself, using Agent Runner reserved concurrency equal to the slot count so waiting tasks physically sit in the queue.

**Rejected because:** it introduces Lambda throttling and retry behaviour, visibility-timeout tuning, and `maxReceiveCount` → DLQ risk under exactly the conditions the demo creates. Worse, per-user **queue position** becomes very hard to compute — SQS exposes approximate depth, not "where am I in line."

**Chosen:** the Router atomically claims a slot with a DynamoDB conditional update. If the claim succeeds, the task goes to SQS for execution. If it fails, a `QUEUE#<timestamp>` item is written and position is a trivial count of earlier items.

This is deterministic, race-free, and makes position display straightforward. SQS still does genuine work: every agent task is a durable, at-least-once, DLQ-backed message.

> **Narration consequence.** The demo must say *"every agent task runs through a real SQS queue, and waiting tasks auto-dispatch the moment a slot frees."* It must **not** claim a waiting user is parked inside SQS. Overclaiming on the video is the same failure as a feature that only exists in the writeup.

### 2. Task-level cooperative scheduling, never token-level preemption

Pausing an LLM mid-generation when a time slice expires is not practically feasible. A task takes a slot, runs to completion (or a turn limit), releases the slot, and the next queued task starts. **Final — do not revisit.**

### 3. Single DynamoDB table, PK/SK pattern

One table for every entity type. Simpler to manage, lower latency for co-located data, fewer IAM permissions, far easier to debug under time pressure. Schema in `CONTRACT.md`. **Final.**

### 4. The token budget is an enforced ceiling

The Agent Runner refuses to invoke Bedrock once `tokens_used >= token_budget`. This exists for two independent reasons: it is the product thesis (governance that actually governs), and the deployed URL is public and unauthenticated, so it is the primary spend guard. An AWS Budget alarm backstops it. **Never disable this to make a demo work.**

### 5. Atomic token accounting

`tokens_used` is updated with a DynamoDB `ADD` UpdateExpression, never read-then-write. Concurrent agent runs would otherwise lose updates.

```
UpdateExpression='ADD tokens_used :n'
ExpressionAttributeValues={':n': token_count}
```

### 6. GoneException handling is mandatory from the first broadcast

Lambda is stateless. When a browser closes, its connection ID stays in DynamoDB until a broadcast fails with `GoneException` (HTTP 410). Unhandled, the broadcast loop crashes and every subsequent user stops receiving updates mid-demo. Every `post_to_connection` call is wrapped, and a `GoneException` deletes the connection row immediately.

### 7. Strands Agents SDK, with a boto3 fallback

Strands is the AWS-native agent framework and scores the "AWS open-source project" criterion. It also pulls compiled dependencies (`pydantic-core`) into a Lambda package, and local Python is 3.14 — newer than any Lambda runtime — so wheels must be built in the Lambda image via `sam build --use-container`.

**Pre-agreed fallback:** if Strands packaging consumes more than one hour, switch to calling Bedrock directly with boto3 `converse` and tool use. boto3 ships in the Lambda runtime, so packaging risk drops to zero. The project remains fully AWS-eligible either way — AWS services alone satisfy the rule.

This decision is recorded with its fallback so a future session does not relitigate it under time pressure.

### 8. DOM/CSS for the workspace canvas — never a game engine

Hackathon teams routinely lose two to three days to tilemaps, collision, and sprite animation. The canvas is a fixed-size div with absolutely positioned character divs; movement is x/y updates with CSS transitions. The HUD is the product; the canvas is the wrapper. **Final.**

### 9. No authentication for the MVP

Cognito costs roughly a day of setup. The team is hardcoded to `TEAM#alpha` and members pick a name on entry. For a judge opening a URL cold, zero-login is actively better. This is an accepted, documented tradeoff — and the reason the server-side token ceiling is mandatory. Cognito is Post-Hackathon.

### 10. Infrastructure as a SAM template

All stack resources live in `template.yaml`. CloudFormation owns the inventory, which is what prevents a fresh session after `/clear` from recreating resources it has forgotten about. Lambdas are **arm64** — cheaper, faster, and building natively under `--use-container` on Apple Silicon.

---

## Intentionally simplified for the MVP

| Simplification | Real-world version |
|---|---|
| Single hardcoded team | Multi-tenant with team creation |
| No authentication | Cognito user pools |
| Fixed 2 agent slots | Configurable per-team capacity |
| FIFO only | Priority queues, fair-share scheduling |
| Team memory never expires | TTL, relevance ranking, embeddings |
| Estimated wait is a rough constant | Historical task duration modelling |
| Hardcoded token budget | Billing integration, per-user quotas |

---

## Rejected — do not reintroduce

| Rejected | Why |
|---|---|
| Phaser.js / any game engine | Consumes days of hackathon time for no judged benefit |
| Token-level preemption | Not practically feasible mid-generation |
| Google OAuth / Gmail / Calendar | Consent screen verification and token vaults cost a full day |
| Multiple DynamoDB tables | More IAM surface, more latency, harder to debug |
| Cognito (for MVP) | Roughly a day of setup; a login wall hurts a cold-open demo |
| In-memory task queue | Lost on Lambda restart; would make the queue a fiction |
| Any AWS service not listed above | Every service must do real work or it does not belong |
