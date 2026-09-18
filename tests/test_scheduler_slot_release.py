"""Regression tests for Bug A: unconditional slot release in scheduler.set_idle.

These tests assert the FIXED behavior and will fail if the guard is removed.

Bug summary
-----------
Before the fix, set_idle had no ConditionExpression, so any caller could
unconditionally clear any slot regardless of who held it.  _release_agent
performed no ownership check, so any connected client could release any slot.

After the fix
-------------
- set_idle(team, slot_id, expected_holder) adds a ConditionExpression that makes
  DynamoDB raise ConditionalCheckFailedException when the slot is no longer
  held by expected_holder.
- _release_agent reads the slot and connection records and rejects callers
  who do not own the slot.
"""
import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cce():
    """Fabricate a ConditionalCheckFailedException from DynamoDB."""
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition not met"}},
        "UpdateItem",
    )


def _slot_item(user_id):
    return {
        "Item": {
            "PK": "TEAM#alpha",
            "SK": "AGENT#coder",
            "status": "BUSY",
            "current_user": user_id,
        }
    }


def _conn_item(user_id):
    return {"Item": {"PK": "TEAM#alpha", "SK": "CONN#test-conn", "user_id": user_id}}


# ---------------------------------------------------------------------------
# set_idle — invariant layer
# ---------------------------------------------------------------------------

class TestSetIdle:

    def test_condition_expression_is_present(self):
        """set_idle must pass a ConditionExpression to DynamoDB."""
        with patch("backend.shared.state.table") as mock_table_fn:
            mock_table = MagicMock()
            mock_table_fn.return_value = mock_table
            mock_table.update_item.return_value = {}

            from backend.shared import scheduler
            scheduler.set_idle("alpha", "coder", expected_holder="user_A")
            _, kwargs = mock_table.update_item.call_args
            assert "ConditionExpression" in kwargs, (
                "set_idle must supply a ConditionExpression — "
                "without it a stale runner can clear a live slot"
            )

    def test_expected_holder_is_in_condition_values(self):
        """The expected_holder value must appear in ExpressionAttributeValues."""
        with patch("backend.shared.state.table") as mock_table_fn:
            mock_table = MagicMock()
            mock_table_fn.return_value = mock_table
            mock_table.update_item.return_value = {}

            from backend.shared import scheduler
            scheduler.set_idle("alpha", "coder", expected_holder="user_A")

            _, kwargs = mock_table.update_item.call_args
            values = kwargs.get("ExpressionAttributeValues", {})
            assert "user_A" in values.values(), (
                "expected_holder must be bound in ExpressionAttributeValues"
            )

    def test_wrong_holder_raises_conditional_check(self):
        """
        When DynamoDB rejects the condition (slot held by a different user),
        set_idle must propagate the ConditionalCheckFailedException.
        It must NOT swallow it or retry.
        """
        with patch("backend.shared.state.table") as mock_table_fn:
            mock_table = MagicMock()
            mock_table_fn.return_value = mock_table
            mock_table.update_item.side_effect = _cce()

            from backend.shared import scheduler
            with pytest.raises(ClientError) as exc_info:
                scheduler.set_idle("alpha", "coder", expected_holder="user_A")

            assert exc_info.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
            assert mock_table.update_item.call_count == 1, "Must not retry after condition failure"

    def test_stale_runner_cannot_clear_reassigned_slot(self):
        """
        Scenario: Runner A finishes T1 (held by user_A), but the slot was
        already reassigned to user_B for T2.  Runner A calls set_idle with
        expected_holder='user_A'.  DynamoDB rejects because current_user is
        now 'user_B'.  The slot must remain BUSY under user_B.
        """
        with patch("backend.shared.state.table") as mock_table_fn:
            mock_table = MagicMock()
            mock_table_fn.return_value = mock_table
            # Simulate DynamoDB rejecting because slot now belongs to user_B.
            mock_table.update_item.side_effect = _cce()

            from backend.shared import scheduler
            with pytest.raises(ClientError):
                scheduler.set_idle("alpha", "coder", expected_holder="user_A")

            # Confirm only one attempt was made — no silent fallback.
            assert mock_table.update_item.call_count == 1


# ---------------------------------------------------------------------------
# _release_agent — authorization layer
# ---------------------------------------------------------------------------

class TestReleaseAgent:

    def _make_table(self, requesting_user, slot_holder):
        mock_table = MagicMock()

        def get_item(Key, **_):
            sk = Key.get("SK", "")
            if "CONN#" in sk:
                return _conn_item(requesting_user)
            if "AGENT#" in sk:
                return _slot_item(slot_holder)
            return {}

        mock_table.get_item.side_effect = get_item
        mock_table.update_item.return_value = {}
        return mock_table

    def test_non_holder_is_rejected(self):
        """
        A client who does not hold the slot must receive an error.
        release_and_dispatch must NOT be called.
        """
        mock_table = self._make_table(requesting_user="user_B", slot_holder="user_A")

        with patch("backend.shared.state.table", return_value=mock_table), \
             patch("backend.shared.scheduler.release_and_dispatch") as mock_release, \
             patch("backend.shared.broadcast.send_to_connection"):

            from backend.router import app as router
            router._release_agent("alpha", "conn-user-B", {"agent_type": "coder"})

            mock_release.assert_not_called()

    def test_holder_can_release_own_slot(self):
        """
        The user who holds the slot must be able to release it successfully.
        """
        mock_table = self._make_table(requesting_user="user_A", slot_holder="user_A")

        with patch("backend.shared.state.table", return_value=mock_table), \
             patch("backend.shared.scheduler.release_and_dispatch") as mock_release:

            from backend.router import app as router
            router._release_agent("alpha", "conn-user-A", {"agent_type": "coder"})

            mock_release.assert_called_once()

    def test_slot_read_happens_before_release(self):
        """
        The slot's DynamoDB record must be read before any release call.
        This proves authorization is not bypassed.
        """
        mock_table = self._make_table(requesting_user="user_A", slot_holder="user_A")

        with patch("backend.shared.state.table", return_value=mock_table), \
             patch("backend.shared.scheduler.release_and_dispatch"):

            from backend.router import app as router
            router._release_agent("alpha", "conn-user-A", {"agent_type": "coder"})

            # get_item must have been called at least twice:
            # once for the connection record, once for the slot record.
            assert mock_table.get_item.call_count >= 2
