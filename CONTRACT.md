# CONTRACT.md — Shared interfaces

Everything here must stay consistent across backend, frontend, and infrastructure. Changing anything in this file means updating every consumer in the same commit.

Ranks 3rd in the source-of-truth hierarchy — above `PRD.md`, below deployed AWS state and actual code.

---

## Naming and environment

| Constant | Value |
|---|---|
| Stack name | `hiveos` |
| Region | `us-east-1` |
| DynamoDB table | `hiveos-state` |
| SQS queue | `hiveos-agent-tasks` |
| SQS dead-letter queue | `hiveos-agent-tasks-dlq` |
| Team ID (hardcoded, MVP) | `alpha` |
| Agent slot IDs | `coder`, `researcher` |
| Lambda architecture | `arm64` |
| Lambda runtime | `python3.13` |
| WebSocket stage | `prod` |
| WebSocket URL | `wss://mel2gpat9c.execute-api.us-east-1.amazonaws.com/prod` |

The WebSocket URL is a stack output (`WebSocketURL`). Read it from CloudFormation rather than pasting it — it changes if the API is ever replaced.

### Lambda packaging

Both functions are built from `CodeUri: backend/` with handlers like `router.app.lambda_handler`, so `backend/shared/` is importable as a top-level `shared` package from either one. No Lambda layer — a layer buys nothing at this size and costs build complexity.

### Lambda environment variables

| Variable | Set on | Meaning |
|---|---|---|
| `TABLE_NAME` | both | DynamoDB table name |
| `TEAM_ID` | both | Team partition (`alpha` in the MVP) |
| `QUEUE_URL` | Router | SQS queue URL |
| `WS_ENDPOINT` | both | API Gateway management endpoint (`https://{api}.execute-api.{region}.amazonaws.com/{stage}`) |
| `BEDROCK_MODEL_ID` | Agent Runner | Resolved in Phase 0 — see below |
| `TOKEN_BUDGET` | Agent Runner | Team token ceiling |
| `MAX_TOKENS_PER_CALL` | Agent Runner | Per-invocation output cap |

### Bedrock model ID

```
UNKNOWN — VERIFY IN PHASE 0
```

Resolve with `aws bedrock list-inference-profiles --region us-east-1`, confirm with a real `bedrock-runtime converse` call, then record the exact working ID here. **Never guess it** — the Converse API and inference-profile forms differ, and a wrong ID fails at runtime, not at deploy time.

---

## DynamoDB single-table schema

Table `hiveos-state` · PK `PK` (string) · SK `SK` (string) · on-demand billing.

| PK | SK | Attributes |
|---|---|---|
| `TEAM#alpha` | `METADATA` | `name`, `token_budget` (N), `tokens_used` (N), `created_at` |
| `TEAM#alpha` | `CONN#<connectionId>` | `user_id`, `avatar`, `x` (N), `y` (N), `connected_at` |
| `TEAM#alpha` | `AGENT#<slotId>` | `status` (`IDLE`\|`BUSY`), `current_user`, `slot_id`, `claimed_at` |
| `TEAM#alpha` | `QUEUE#<ts>#<uuid>` | `user_id`, `agent_type`, `prompt`, `connection_id`, `enqueued_at` |
| `TEAM#alpha` | `MEMORY#<uuid>` | `key`, `val`, `updated_by`, `created_at` |

### Entity rules

- **METADATA** — one per team. `tokens_used` is only ever updated with `ADD`, never read-then-write.
- **CONN#** — one per live WebSocket connection. Deleted on `$disconnect` **and** on any `GoneException` during broadcast. **One row per connection, not per user** — the same `user_id` with two tabs open has two rows, so `state_snapshot.members[]` can contain duplicates. `user_left`, by contrast, carries only a `user_id`, so a client that trusts it blindly removes someone who still has a live socket. The frontend dedupes `members[]` by `user_id` and re-syncs on both membership events.
- **AGENT#** — one per slot. `IDLE → BUSY` on claim, `BUSY → IDLE` on completion. `current_user` is `null` when `IDLE`.
- **QUEUE#** — sorted lexicographically by SK, which gives FIFO because the timestamp leads. Deleted when dispatched. The timestamp is **microsecond** precision (`%Y-%m-%dT%H:%M:%S.%fZ`), not the second-precision `now_iso()` used everywhere else: at second granularity two people clicking within the same second tie and fall back to UUID order, i.e. random. `connection_id` is carried so the runner can reply directly to the requester once the task finally starts.
- **MEMORY#** — key/value facts saved by agents. No expiry in the MVP.

### Atomic slot claim

The only correct way to claim a slot. Two simultaneous claims must never both succeed.

```python
table.update_item(
    Key={'PK': f'TEAM#{team_id}', 'SK': f'AGENT#{slot_id}'},
    UpdateExpression='SET #s = :busy, current_user = :u, claimed_at = :t',
    ConditionExpression='#s = :idle',
    ExpressionAttributeNames={'#s': 'status'},
    ExpressionAttributeValues={
        ':busy': 'BUSY', ':idle': 'IDLE',
        ':u': user_id, ':t': now_iso,
    },
)
# ConditionalCheckFailedException  =>  slot was taken; try the next one, else enqueue
```

### Atomic token accounting

```python
table.update_item(
    Key={'PK': f'TEAM#{team_id}', 'SK': 'METADATA'},
    UpdateExpression='ADD tokens_used :n',
    ExpressionAttributeValues={':n': token_count},
    ReturnValues='UPDATED_NEW',   # returns the new total for broadcasting
)
```

---

## WebSocket protocol

All frames are JSON. Client frames carry `action`; server frames carry `event`.

The API's `RouteSelectionExpression` is `$request.body.action`, but only `$connect`, `$disconnect` and `$default` are declared as routes. Every action therefore lands in one handler. **Adding a client action is a code change, never a CloudFormation change** — which matters because `AWS::ApiGatewayV2::Deployment` is an immutable snapshot of the route table.

### Connection handshake

`user_id` and `avatar` are passed as query-string parameters on the socket URL:

```
wss://…/prod?user_id=alice&avatar=%F0%9F%90%9D
```

Both are optional. A missing `user_id` becomes `guest-<first 6 chars of connection id>`.

### Client → server

| `action` | Payload | Handler behaviour |
|---|---|---|
| `hello` | — | Reply `state_snapshot` to this connection only. Sent once, immediately after the socket opens |
| `claim_agent` | `{agent_type, prompt, user_id}` | Try atomic claim → dispatch to SQS, or enqueue and return position |
| `release_agent` | `{agent_type, user_id}` | Set slot `IDLE`, dispatch the oldest queued task |

**`agent_type` on `claim_agent` is a preference, not a reservation.** It is tried first, then the remaining slots in `SLOTS` order. Nobody queues behind an idle agent.

**One active task per user.** A `claim_agent` from a user who already holds a slot or sits in the queue is refused with `error`. Without it a double-clicked button lets one person hold both slots — precisely the monopoly the product claims to prevent.
| `send_message` | `{user_id, text}` | Broadcast to team chat |
| `move_avatar` | `{user_id, x, y}` | Update `CONN#` row, broadcast position |

### Server → client

| `event` | Payload | Sent when |
|---|---|---|
| `state_snapshot` | `{team, agents[], tokens_used, token_budget, pct_used, members[], memory[], queue[]}` | In reply to `hello` — a new client must be able to render everything from this one frame |
| `chat_message` | `{user_id, text, ts}` | `send_message` runs. `user_id` is resolved from the sender's `CONN#` row, not trusted from the frame |
| `agent_state_update` | `{agent_type, status, current_user, slot_id}` | Any slot state change |
| `token_update` | `{tokens_used, token_budget, pct_used}` | After every Bedrock call |
| `queue_update` | `{user_id, queue_position, estimated_wait_seconds}` | Queue add or removal |
| `agent_response` | `{user_id, agent_type, text, tokens_used_this_call}` | Agent task completes |
| `memory_updated` | `{key, val, updated_by}` | `set_team_memory` runs |
| `budget_exhausted` | `{tokens_used, token_budget}` | Bedrock invocation refused at the ceiling |
| `user_joined` | `{user_id, avatar, x, y}` | `$connect` |
| `user_left` | `{user_id}` | `$disconnect` or `GoneException` |
| `error` | `{message}` | Any handled failure worth surfacing |

`queue[]` entries are `{user_id, agent_type, queue_position}`, oldest first, `queue_position` 1-based.

**`queue[]` deliberately carries no `estimated_wait_seconds`, unlike `queue_update`.** The
client derives it as `queue_position * ESTIMATED_TASK_SECONDS`, which is byte-for-byte what
`scheduler.broadcast_queue` computes. This matters because the frontend re-reads the snapshot
after every board-moving event (see below), so a field present only on the incremental event
gets overwritten — the ETA used to blank out ~500 ms after appearing for exactly this reason.
`ESTIMATED_TASK_SECONDS` therefore exists **twice**: `backend/shared/scheduler.py` and
`frontend/src/useHive.js`. Retune both in the same commit or the queue will lie.

### Snapshot re-sync (client behaviour)

`queue_update` is broadcast once per *waiting* user and there is **no removal frame** — nothing
tells a client that someone has been dispatched or has left the queue. The client therefore
applies increments for instant feel and re-sends `hello` on a 500 ms debounce after any
`agent_state_update`, `queue_update`, `agent_response`, `user_joined` or `user_left`, letting
the authoritative snapshot correct any drift.

Two consequences anyone touching the protocol must know:

1. **The snapshot must stay a superset of what the incremental events convey**, or the re-sync
   destroys information (see the ETA above).
2. **`state_snapshot` must never be in the re-sync trigger set** — it would feed itself.

**`state_snapshot` is load-bearing.** A client joining mid-demo must render correct state from it alone, without waiting for the next incremental event. `queue[]` exists for exactly this reason: a user who reconnects while waiting would otherwise have no way to learn their own position until somebody else's action happened to move the queue.

### Frame ordering

**A slot's `agent_state_update` BUSY is broadcast before its task is handed to SQS.** Dispatching first lets a fast-failing task post its `agent_response` / `error` ahead of the BUSY frame, so the client applies BUSY *after* the release and shows a slot that never goes idle again. Verified: with the old order the Phase 2 smoke test failed about half of all runs.

The guaranteed per-task sequence a client can rely on:

```
agent_state_update BUSY  →  agent_response | error  →  agent_state_update IDLE
```

**Why `hello` exists.** API Gateway does not finish establishing a connection until the `$connect` integration returns, so `post_to_connection` against it inside that handler fails with `GoneException`. The snapshot cannot be pushed from `$connect`; the client pulls it on its first frame instead. Verified on the deployed stack in Phase 1.

### Client connect sequence

```
open wss://…/prod?user_id=alice&avatar=🐝
  → server writes CONN# row, broadcasts user_joined to everyone else
send {"action": "hello"}
  → server replies state_snapshot on this connection
render, then apply incremental events as they arrive
```

---

## Broadcast with GoneException handling

Mandatory in every function that broadcasts. No exceptions.

```python
def broadcast_to_team(team_id, payload, apigw, table):
    for conn_id in get_team_connections(team_id, table):
        try:
            apigw.post_to_connection(
                ConnectionId=conn_id,
                Data=json.dumps(payload).encode(),
            )
        except apigw.exceptions.GoneException:
            table.delete_item(Key={
                'PK': f'TEAM#{team_id}',
                'SK': f'CONN#{conn_id}',
            })
```

---

## SQS message format

```json
{
  "team_id": "alpha",
  "slot_id": "coder",
  "user_id": "alice",
  "agent_type": "coder",
  "prompt": "Write a user creation function",
  "connection_id": "abc123=",
  "enqueued_at": "2026-09-18T10:30:00Z"
}
```

`connection_id` is the requester's connection, used for directed replies. It may be stale by the time the runner executes — handle `GoneException` and continue.

---

## Agent tools

| Tool | Input | Behaviour |
|---|---|---|
| `get_team_memory` | — | Read all `MEMORY#` rows; return as a context string prepended to the system prompt |
| `set_team_memory` | `{key, val}` | Write a `MEMORY#` row; broadcast `memory_updated` |
| `get_task_context` | `{user_id}` | Return the original prompt and relevant prior task history |

Memory is loaded **before** the model call, not on demand, so a queued user's agent already knows the team's facts the moment it starts.

---

## Slot state machine

```
        claim_agent (atomic conditional update)
IDLE ──────────────────────────────────────────► BUSY
  ▲                                                │
  │           agent task completes or fails        │
  └────────────────────────────────────────────────┘
                        │
                        ▼
        dispatch oldest QUEUE# item, if any
```

Invariants:
- A slot is `BUSY` only with a non-null `current_user`.
- A slot must be released even when the agent task **fails** — otherwise it leaks and the demo deadlocks. Wrap the runner body in try/finally.
- Dispatching the next queued task happens after the release, in the same invocation.
- **The `QUEUE#` delete is the exactly-once gate.** Two runners finishing at the same instant both see the same head-of-queue item; the conditional delete (`attribute_exists(SK)`) decides which one owns it, and the loser moves to the next item. Without this the same task dispatches twice.
- If a task is won but every slot is then taken before it can be claimed, it is **re-written under its original SK** so the user keeps their place rather than being sent to the back of the line.

### Fault injection

A prompt containing `__hiveos_fail__` makes the Agent Runner raise. This is a deliberate hook so the no-slot-leak invariant stays verifiable against deployed AWS (`scripts/ws_smoke.py` section 10) rather than only in a local test. Worst case a user types the sentinel and gets an `error` frame.
