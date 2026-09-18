"""WebSocket fan-out to every live member of the team.

The GoneException branch is mandatory (CONTRACT.md). API Gateway keeps handing
back connection IDs for browsers that already went away — a closed laptop lid,
a refreshed tab, a lost network. Without the handler the first dead connection
in the loop kills delivery for everyone after it, which on stage looks exactly
like the product being broken.
"""

import os

import boto3

from . import state

_api = None


def _client():
    global _api
    if _api is None:
        _api = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=os.environ["WS_ENDPOINT"],
        )
    return _api


def send_to_connection(connection_id, payload):
    """Send one frame. Returns False if the connection was already gone."""
    api = _client()
    try:
        api.post_to_connection(
            ConnectionId=connection_id,
            Data=state.dumps(payload).encode(),
        )
        return True
    except api.exceptions.GoneException:
        # Which team's partition holds this row? Looked up here rather than
        # passed in, because this function is also called for directed replies
        # — errors, snapshots — where the caller has no team to hand. The cost
        # is one GetItem on the *stale* path only; the delivering path, which
        # is every other call, pays nothing.
        team = state.connection_team(connection_id)
        print(
            f"[broadcast] gone connection={connection_id} team={team}"
            " — deleting CONN# row"
        )
        state.remove_connection(team, connection_id)
        return False


def broadcast_to_team(team, payload, exclude=None):
    """Fan a frame out to the team. One dead connection never stops the rest."""
    delivered = 0
    stale = 0
    for connection_id in state.connection_ids(team):
        if connection_id == exclude:
            continue
        if send_to_connection(connection_id, payload):
            delivered += 1
        else:
            stale += 1
    print(
        f"[broadcast] event={payload.get('event')} "
        f"delivered={delivered} stale={stale}"
    )
    return delivered
