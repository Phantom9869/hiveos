"""HiveOS Router Lambda — every WebSocket frame lands here.

Routes (CONTRACT.md):
  $connect     register the connection, tell the room someone arrived
  $disconnect  drop the row, tell the room someone left
  $default     every client action

The API's RouteSelectionExpression is `$request.body.action` but only
`$default` is declared, so all actions fall through to one handler. That keeps
the route table static — Phase 2 adds actions, not CloudFormation resources.

Phase 1 handles `hello` and `send_message`.
`claim_agent` / `release_agent` arrive in Phase 2, `move_avatar` in Phase 5.

Why `hello` exists: API Gateway does not finish establishing a connection
until the $connect integration returns, so post_to_connection against it fails
with GoneException. The client therefore asks for its opening state_snapshot
on the first frame after the socket opens.
"""

import json
import traceback

from shared import broadcast, state

OK = {"statusCode": 200}

MAX_USER_ID = 40
MAX_TEXT = 500


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
