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

### Inference model

```
openai/gpt-oss-120b   via Groq   (https://api.groq.com/openai/v1/chat/completions)
```

**Not Bedrock.** Bedrock is blocked account-wide on this AWS account: `us-east-1`, `us-west-2` and `ap-south-1` all refuse, Marketplace models with `INVALID_PAYMENT_INSTRUMENT` and first-party Amazon Nova with a hard zero per-day token quota that reports `adjustable=False`. Verified again on 2026-09-18 before the switch. See `PROGRESS.md`.

Every other component is AWS. Only inference leaves.

| | |
|---|---|
| Client | `backend/shared/llm.py` — one `urllib` POST, no SDK |
| Credential | SSM SecureString `/hiveos/groq-api-key`, read at runtime, cached per container. **Never** in the template, the stack, an env var, or git |
| Model | `GROQ_MODEL` env var, pinned in `samconfig.toml` |
| Output cap | `MAX_TOKENS_PER_CALL` = 400 |

**Never guess the model name.** Groq retires them: `llama-3.3-70b-versatile`, the name this was first written against, was already gone and failed at runtime rather than at deploy. List the current ids with `GET https://api.groq.com/openai/v1/models` before changing it.

**A `Default:` change does not reach a deployed stack.** CloudFormation keeps an existing stack's parameter values on update, so the model name is pinned in `samconfig.toml`'s `parameter_overrides`, not left to the template default.

If Bedrock is ever unblocked, swap the body of `llm.py:complete` and add `bedrock:InvokeModel` to the Agent Runner role. Nothing else changes.

---

## DynamoDB single-table schema

Table `hiveos-state` · PK `PK` (string) · SK `SK` (string) · on-demand billing.

| PK | SK | Attributes |
|---|---|---|
| `TEAM#alpha` | `METADATA` | `name`, `token_budget` (N), `tokens_used` (N), `created_at` |
| `TEAM#alpha` | `CONN#<connectionId>` | `user_id`, `avatar`, `x` (N), `y` (N), `connected_at` |
| `TEAM#alpha` | `AGENT#<slotId>` | `status` (`IDLE`\|`BUSY`), `current_user`, `slot_id`, `claimed_at` |
| `TEAM#alpha` | `QUEUE#<ts>#<uuid>` | `user_id`, `agent_type`, `prompt`, `connection_id`, `enqueued_at` |
| `TEAM#alpha` | `MEMORY#<slug(key)>` | `key`, `val`, `updated_by`, `created_at` |

### Entity rules

- **METADATA** — one per team. `tokens_used` is only ever updated with `ADD`, never read-then-write.
- **CONN#** — one per live WebSocket connection. Deleted on `$disconnect` **and** on any `GoneException` during broadcast. **One row per connection, not per user** — the same `user_id` with two tabs open has two rows, so `state_snapshot.members[]` can contain duplicates. `user_left`, by contrast, carries only a `user_id`, so a client that trusts it blindly removes someone who still has a live socket. The frontend dedupes `members[]` by `user_id` and re-syncs on both membership events.
- **AGENT#** — one per slot. `IDLE → BUSY` on claim, `BUSY → IDLE` on completion. `current_user` is `null` when `IDLE`.
- **QUEUE#** — the SK leads with a microsecond timestamp, so sorting by SK gives arrival order. **Arrival order is not dispatch order.** Deleted when dispatched. The timestamp is **microsecond** precision (`%Y-%m-%dT%H:%M:%S.%fZ`), not the second-precision `now_iso()` used everywhere else: at second granularity two people clicking within the same second tie and fall back to UUID order, i.e. random. `connection_id` is carried so the runner can reply directly to the requester once the task finally starts.

  **Dispatch order is fair queueing, not FIFO** (`state.fair_order`): sorted by
  how long each person has gone without a turn — read off the `TASK#` ledger —
  with arrival as the tie-break only. Someone who has never run outranks
  someone who just did, so a user who re-requests the instant their task
  finishes cannot jump a colleague who has been waiting. With one task each and
  nobody having run yet, this is indistinguishable from FIFO, which is why the
  demo sequence is unaffected.

  **There is exactly one definition of that order and all three consumers use
  it** — `take_next_task`, `broadcast_queue`, and `state_snapshot`. If the
  board numbered positions by arrival while the runner picked by fairness, the
  position on screen would be wrong about who goes next.
- **TASK#** — the ledger: one row per task that reached the runner, including
  the ones that never ran. `status` is `done`, `failed` or `refused`; a refused
  task records **zero** tokens, which is the clearest evidence that the ceiling
  is a control and not a gauge. Sort key is microsecond-precision for the same
  reason `QUEUE#` is: at second granularity two tasks finishing together tie
  and fall back to UUID order, i.e. random. Cleared by `seed.sh` — the spend
  breakdown aggregates every row, so stale rows would open the board showing a
  team that had already spent its budget.

- **MEMORY#** — key/value facts saved by agents. No expiry in the MVP.

  **The SK is derived from the key, not a UUID** (`memory._slug`: lowercased,
  non-alphanumerics collapsed to `_`). This file previously specified
  `MEMORY#<uuid>`, which made `set_team_memory` non-idempotent — saving the
  same key twice left two rows carrying the same `key`, and `state_snapshot`
  handed a client both of them as separate facts. A key/value store keyed by
  the key makes a write an upsert, which is the behaviour every consumer
  already assumed. Two keys differing only in case or punctuation collapse to
  one fact; that is deliberate.

  `created_at` is the **write** time, so an upsert refreshes it and `facts()`
  orders by most-recently-set.

- **METADATA `usage_estimated`** — set when any spend folded into
  `tokens_used` was an estimate rather than billed model usage. Sticky: never
  cleared by the runner, only by `seed.sh` rewriting the row. See
  *Token provenance* below.

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
| `send_message` | `{text}` | Broadcast to team chat as `chat_message` |
| `move_avatar` | `{x, y}` | Update `CONN#` row, broadcast `avatar_moved` |

**`agent_type` on `claim_agent` is a preference, not a reservation.** It is tried first, then the remaining slots in `SLOTS` order. Nobody queues behind an idle agent.

**One active task per user.** A `claim_agent` from a user who already holds a slot or sits in the queue is refused with `error`. Without it a double-clicked button lets one person hold both slots — precisely the monopoly the product claims to prevent.

**Neither `send_message` nor `move_avatar` takes a `user_id`.** It is resolved from the sender's `CONN#` row, because a frame is whatever the client chose to type and the row is what `$connect` actually recorded — trusting the frame would let any client move someone else's avatar or speak as them. (This table previously listed `user_id` on both; the handlers never read it.)

**Avatar coordinates are percentages of the canvas (0–100), not pixels.** Three browsers at different widths have to agree on where everyone is standing, and a pixel coordinate breaks that on the first mismatched window. The Router clamps to the range and rejects non-numeric values, so a hand-crafted frame cannot push an avatar off the board for everyone else.

**A position is stored per connection but drawn per user.** The `CONN#` row carries `x`/`y`, so someone with two tabs open has two stored positions — but `state_snapshot.members[]` carries no `connection_id` (deliberately: it is an internal address used only by `post_to_connection`, and broadcasting it to every client buys nothing). The frontend therefore dedupes `members[]` by `user_id` for both the count and the canvas, and `avatar_moved` is keyed by `user_id`, so a second tab moves the same avatar. One person, one marker, which is also the reading that makes sense on a team board.

### Server → client

| `event` | Payload | Sent when |
|---|---|---|
| `state_snapshot` | `{team, agents[], tokens_used, token_budget, pct_used, usage_estimated, members[], memory[], queue[], history[], spend[]}` | In reply to `hello` — a new client must be able to render everything from this one frame |
| `chat_message` | `{user_id, text, ts}` | `send_message` runs. `user_id` is resolved from the sender's `CONN#` row, not trusted from the frame |
| `agent_state_update` | `{agent_type, status, current_user, slot_id}` | Any slot state change |
| `token_update` | `{tokens_used, token_budget, pct_used, estimated}` | After every agent call that spent tokens |
| `queue_update` | `{user_id, queue_position, estimated_wait_seconds}` | Queue add or removal |
| `agent_response` | `{user_id, agent_type, text, tokens_used_this_call, estimated}` | Agent task completes |
| `memory_updated` | `{key, val, updated_by}` | `set_team_memory` runs |
| `budget_exhausted` | `{tokens_used, token_budget}` | Bedrock invocation refused at the ceiling |
| `user_joined` | `{user_id, avatar, x, y}` | `$connect` |
| `user_left` | `{user_id}` | `$disconnect` or `GoneException` |
| `avatar_moved` | `{user_id, x, y}` | `move_avatar` runs |
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

### `history[]` and `spend[]`

Both are derived from `TASK#` rows and both ride on `state_snapshot`, because
the client most likely to want *"who spent what"* is the one that just opened
the public URL.

| Field | Shape | Notes |
|---|---|---|
| `history[]` | `{user_id, agent_type, tokens, estimated, status, prompt, at}` | Newest first, capped at 12 — every client parses the snapshot on connect, and nobody reads the 40th most recent task off a board |
| `spend[]` | `{user_id, tokens, tasks}` | Biggest spender first |

**`spend[]` aggregates every task row, not the twelve in `history[]`.** The
whole point is the total; a breakdown of only the last twelve would be a
different and much less useful number. Refusals and failures count as *tasks*
but not as *spend*, which is what they are.

### Token provenance — `estimated` / `usage_estimated`

Every frame carrying a token count says where the number came from, because
not every number is billed model usage:

| Field | On | Means |
|---|---|---|
| `estimated` | `agent_response`, `token_update` | the total this frame reports includes estimated spend |
| `usage_estimated` | `state_snapshot` | the same fact, for a client that loaded cold |

**Normally both are false.** `tokens_used_this_call` is the `total_tokens` the
provider reported for that call — actual counted usage, typically 200–400 per
task.

They go true only on the **fallback path**: if the model is unreachable, the
Agent Runner still answers, from composed text, and charges
`len(prompt + memory_context + response) / 4` — the standard rough heuristic
over the real strings, not an invented number. A degraded answer is better
than a broken workspace, but it must never be laundered into a billed-looking
meter, which is what these two flags prevent.

**The snapshot field is not optional.** A client that was not connected when
the spend happened — which is every judge opening the public URL — has no
other way to learn the total is partly estimated, and would otherwise render
it as billed usage. The flag is sticky once set: an estimate already folded
into the total does not stop being one. Only `seed.sh` clears it.

### The budget ceiling

Enforced in the Agent Runner immediately before the agent is invoked, never at
claim time: a task can sit in the queue while the tasks ahead of it spend what
was left, so the only honest moment to decide is the last one.

**METADATA is the single source of truth for the ceiling**, not the
`TOKEN_BUDGET` env var. It is the same row the counter increments and the same
number `state_snapshot` shows a client, so the guard can never disagree with
the meter a user is looking at. `TOKEN_BUDGET` supplies only the default
`seed.sh` writes.

On refusal the runner broadcasts `budget_exhausted`, replies `error` to the
requester, spends nothing, and **still releases the slot** — the refusal path
is subject to the same no-leak invariant as every other path.

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

| Tool | Implemented as | Behaviour |
|---|---|---|
| `get_team_memory` | `memory.facts()` / `memory.as_context()` | Read all `MEMORY#` rows; return a context block for the system prompt |
| `set_team_memory` | `memory.remember(key, val, updated_by)` | Upsert a `MEMORY#` row; broadcast `memory_updated` |
| `get_task_context` | **not built** | See below |

Memory is loaded **before** the model call, not on demand, so a queued user's agent already knows the team's facts the moment it starts.

**`get_task_context` is still not implemented, but the reason changed.** It was
specified to return "the original prompt and relevant prior task history", and
the original objection was that no task history existed anywhere. It does now —
`TASK#`, above — so the blocker is gone and this is a small tool away from
being buildable.

It stays unbuilt because the agent has no tools at all yet: memory is loaded
into the system prompt before the call rather than fetched on demand, and the
prompt is already on the SQS message. Build it with the rest of the tool
surface, not before it.

**How a fact gets saved: a prompt convention, not a tool call.** `remember:
<key> = <value>` (colon optional, case-insensitive), parsed by
`memory.directive()`. A model given tools would decide to call
`set_team_memory` itself; this decides for it.

That is a deliberate remaining gap, and the only place the agent still differs
in *kind* rather than degree from the Phase 3 design. Tool calling adds a
second round trip and a failure surface, and was judged not worth it against a
deadline once the rest was green. Everything the directive then touches — the
row, the broadcast, the context load on the next task — is the real mechanism.

**The save happens before the model call, not after it.** The fact is the
user's explicit instruction, so it must persist even when the model is
unreachable; and saving first puts it in its own call's context. One
consequence worth knowing: `memory_updated` now broadcasts at the *start* of a
task rather than at the end, so it can overtake frames a client might expect to
see first. Anything asserting on frame order has to buffer rather than assume.

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
