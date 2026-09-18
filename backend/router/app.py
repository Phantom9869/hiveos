"""HiveOS Router Lambda — every WebSocket frame lands here.

Routes (CONTRACT.md):
  $connect     register the connection, tell the room someone arrived
  $disconnect  drop the row, tell the room someone left
  $default     every client action

The API's RouteSelectionExpression is `$request.body.action` but only
`$default` is declared, so all actions fall through to one handler. That keeps
the route table static — Phase 2 adds actions, not CloudFormation resources.

Phase 1 handles `hello` and `send_message`; Phase 2 adds `claim_agent` and
`release_agent`. `move_avatar` arrives in Phase 5.

Why `hello` exists: API Gateway does not finish establishing a connection
until the $connect integration returns, so post_to_connection against it fails
with GoneException. The client therefore asks for its opening state_snapshot
on the first frame after the socket opens.
"""

import json
import traceback

from shared import broadcast, scheduler, state

OK = {"statusCode": 200}

MAX_USER_ID = 40
MAX_TEXT = 500
MAX_PROMPT = 2000


def lambda_handler(event, context):
    request = event.get("requestContext", {})
    route = request.get("routeKey")
    connection_id = request.get("connectionId")

    try:
        if route == "$connect":
            return _on_connect(event, connection_id)
        if route == "$disconnect":
            return _on_disconnect(connection_id)
        return _on_message(event, connection_id)
    except Exception:
        traceback.print_exc()
        if route == "$connect":
            # Refuse the connection rather than leave a client half-registered.
            return {"statusCode": 500}
        _try_error(connection_id, "internal error")
        return OK


# --- Routes ----------------------------------------------------------------


def _on_connect(event, connection_id):
    params = event.get("queryStringParameters") or {}
    user_id = (params.get("user_id") or f"guest-{connection_id[:6]}").strip()[:MAX_USER_ID]
    avatar = (params.get("avatar") or "\U0001f41d")[:8]

    member = state.add_connection(connection_id, user_id, avatar)
    print(f"[connect] connection={connection_id} user={user_id}")

    broadcast.broadcast_to_team(
        {"event": "user_joined", **member},
        exclude=connection_id,
    )
    return OK


def _on_disconnect(connection_id):
    previous = state.remove_connection(connection_id) or {}
    user_id = previous.get("user_id")
    print(f"[disconnect] connection={connection_id} user={user_id}")

    if user_id:
        broadcast.broadcast_to_team({"event": "user_left", "user_id": user_id})
    return OK


def _on_message(event, connection_id):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(connection_id, "malformed JSON")
    if not isinstance(body, dict):
        return _error(connection_id, "frame must be a JSON object")

    action = body.get("action")
    print(f"[message] connection={connection_id} action={action}")

    if action == "hello":
        broadcast.send_to_connection(connection_id, state.state_snapshot())
        return OK

    if action == "claim_agent":
        return _claim_agent(connection_id, body)

    if action == "release_agent":
        return _release_agent(connection_id, body)

    if action == "send_message":
        text = (body.get("text") or "").strip()[:MAX_TEXT]
        if not text:
            return _error(connection_id, "send_message requires text")
        broadcast.broadcast_to_team(
            {
                "event": "chat_message",
                "user_id": _user_for(connection_id, body),
                "text": text,
                "ts": state.now_iso(),
            }
        )
        return OK

    return _error(connection_id, f"unknown action: {action!r}")


# --- Scheduling ------------------------------------------------------------


def _claim_agent(connection_id, body):
    """Take a slot if one is free, otherwise take a place in line."""
    user_id = _user_for(connection_id, body)
    prompt = (body.get("prompt") or "").strip()[:MAX_PROMPT]
    if not prompt:
        return _error(connection_id, "claim_agent requires a prompt")

    # agent_type is a preference, not a reservation: claim_any falls back to
    # the other slot rather than queueing someone behind an idle agent.
    agent_type = body.get("agent_type")
    if agent_type not in scheduler.SLOTS:
        agent_type = None

    if _already_working(user_id):
        return _error(connection_id, "you already have an agent running or queued")

    slot_id = scheduler.claim_any(agent_type, user_id)

    if slot_id is None:
        scheduler.enqueue(user_id, agent_type, prompt, connection_id)
        scheduler.broadcast_queue()
        return OK

    # Announce BUSY *before* handing the task to SQS. The slot is already BUSY
    # in DynamoDB, so this frame is accurate either way — but dispatching first
    # lets a fast-failing task post its reply ahead of this frame, and a client
    # that sees BUSY arrive after the release is left showing a slot that never
    # goes idle again.
    scheduler.broadcast_slot(slot_id, "BUSY", user_id)
    scheduler.dispatch(slot_id, user_id, agent_type or slot_id, prompt, connection_id)
    return OK


def _release_agent(connection_id, body):
    """Manual release. The Agent Runner also releases automatically when a
    task ends — this exists so a wedged demo slot can be freed from the UI."""
    agent_type = body.get("agent_type")
    if agent_type not in scheduler.SLOTS:
        return _error(connection_id, f"unknown agent_type: {agent_type!r}")

    scheduler.release_and_dispatch(agent_type)
    return OK


def _already_working(user_id):
    """One task per user at a time.

    Without this a double-clicked "Get Agent" button lets one person hold both
    slots, which is precisely the unfair-monopoly behaviour the product claims
    to prevent — and it would happen live on the recording.

    One query rather than a get per slot plus a queue query: this runs on the
    claim path, which is the interaction the whole demo hangs on.
    """
    for item in state.query_team():
        sk = item["SK"]
        if sk.startswith("AGENT#") and item.get("current_user") == user_id:
            return True
        if sk.startswith("QUEUE#") and item.get("user_id") == user_id:
            return True
    return False


# --- Helpers ---------------------------------------------------------------


def _user_for(connection_id, body):
    """Trust the CONN# row over the frame — the row is what $connect recorded."""
    return state.connection_user(connection_id) or body.get("user_id") or "unknown"


def _error(connection_id, message):
    broadcast.send_to_connection(connection_id, {"event": "error", "message": message})
    return OK


def _try_error(connection_id, message):
    """Best-effort error reply from the top-level exception handler."""
    try:
        _error(connection_id, message)
    except Exception:
        traceback.print_exc()
