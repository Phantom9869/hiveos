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
       └───────────────────────────────────────────┤  load memory     │
                                                   │  call the model  │
                                                   │  token accounting │
                                                   │  release slot     │
                                                   │  dispatch next    │
                                                   └────────┬─────────┘
                                                            ▼ HTTPS
                                                   ┌──────────────────┐
                                                   │ Groq             │
                                                   │ gpt-oss-120b     │
                                                   │ (the only hop    │
                                                   │  that is not AWS)│
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
| **Agent Runner Lambda** | Consumes SQS, loads team memory, calls the model, accounts tokens, broadcasts, releases the slot, dispatches the next queued task | Isolated from the Router so model latency never blocks connection handling |
| **Groq** (`openai/gpt-oss-120b`) | Foundation model inference — **the only component not on AWS** | Bedrock is blocked account-wide on this account (decision 7). Reached with one stdlib `urllib` POST; the key is an SSM SecureString read at runtime |
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
                    ├─ save any `remember:` fact (before the call)
                    ├─ call the model → Groq
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

The Agent Runner refuses to invoke the model once `tokens_used >= token_budget`. This exists for two independent reasons: it is the product thesis (governance that actually governs), and the deployed URL is public and unauthenticated, so it is the primary spend guard. An AWS Budget alarm backstops it. **Never disable this to make a demo work.**

### 5. Atomic token accounting

`tokens_used` is updated with a DynamoDB `ADD` UpdateExpression, never read-then-write. Concurrent agent runs would otherwise lose updates.

```
UpdateExpression='ADD tokens_used :n'
ExpressionAttributeValues={':n': token_count}
```

### 6. GoneException handling is mandatory from the first broadcast

Lambda is stateless. When a browser closes, its connection ID stays in DynamoDB until a broadcast fails with `GoneException` (HTTP 410). Unhandled, the broadcast loop crashes and every subsequent user stops receiving updates mid-demo. Every `post_to_connection` call is wrapped, and a `GoneException` deletes the connection row immediately.

### 7. Inference calls out to Groq; everything else is AWS

> **Superseded, 2026-09-18.** The original decision was "Strands Agents SDK, with a boto3
> `converse` fallback", chosen so the project stayed fully AWS-native. Both options assumed
> Bedrock was reachable. It is not, on this account, and no amount of configuration fixes it.

**What was tried.** Bedrock refuses across `us-east-1`, `us-west-2` and `ap-south-1`. Marketplace-served models (Anthropic, AI21, Mistral) return `AccessDeniedException: INVALID_PAYMENT_INSTRUMENT`; a valid card was added and did not change it. First-party Amazon Nova needs no Marketplace subscription and still fails with `ThrottlingException: Too many tokens per day` against a per-day quota of zero that reports `adjustable=False` — so it cannot even be raised by request. 42 of 43 per-day token quotas are zero. That is an account-level restriction, not a setting, and AWS Support will not turn it around before the deadline.

**The decision.** Inference moves to Groq (`openai/gpt-oss-120b`) over HTTPS from the Agent Runner. The API Gateway WebSocket, both Lambdas, SQS, DynamoDB and Amplify are unchanged. One outbound HTTP call is the entire difference.

**Why this is defensible rather than a retreat.** A governance layer that only works against one vendor's models is a worse governance layer. The scheduler does not care where a token was spent, only that it was counted — and the swap proved that concretely: it touched one function, `_run_agent`, plus a new 130-line `shared/llm.py`. Nothing about the queue, the slot state machine, the atomic accounting or the enforced ceiling moved.

**What it costs.** The project is no longer end-to-end AWS, and the demo says so out loud rather than hiding it. Against the alternative — shipping a stub and calling the token meter an estimate on a product whose entire pitch is token governance — this is the better trade. The counts are now the provider's reported `total_tokens`, so the meter means what it says.

**Implementation notes that cost real time:**
- `urllib` from the stdlib, no SDK: nothing extra to package, and no compiled wheel to resolve against a local Python 3.14 that does not match the Lambda runtime.
- The key is an SSM SecureString read at runtime, never a CloudFormation parameter — so a redeploy cannot wipe it and it never enters git.
- Cloudflare rejects urllib's default User-Agent with HTTP 403 `error code: 1010`, which is indistinguishable from a bad key until you read the body.
- The stub is retained as an automatic fallback, flagged `estimated`, so a provider outage degrades the answer instead of breaking the workspace.

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
