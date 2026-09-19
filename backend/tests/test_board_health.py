"""
Tests for the state-aware board health check (check_and_alert_boards),
which is what the scheduler now calls periodically to catch stale
Greenhouse/Lever/Ashby tokens automatically instead of relying on
someone manually hitting GET /admin/board-health.

The core behavior under test: an alert must fire exactly once on the
LIVE -> DEAD transition, then go quiet on subsequent checks while the
token stays dead (no repeat-spam), and recovery (DEAD -> LIVE) must
clear that state without itself alerting.
"""
from unittest.mock import patch

from app.db import SessionLocal, BoardTokenStatus
from app.core.board_validator import check_and_alert_boards


def _fake_validate_boards_all_live(boards):
    return {"results": {"greenhouse": {"stripe": True}}, "stale": []}


def _fake_validate_boards_one_dead(boards):
    return {"results": {"greenhouse": {"stripe": False}}, "stale": ["greenhouse:stripe"]}


def test_first_check_of_a_dead_token_alerts_once(client):
    db = SessionLocal()
    try:
        with patch("app.core.board_validator.validate_boards", side_effect=_fake_validate_boards_one_dead), \
             patch("app.core.ingest.resolve_boards", return_value={"greenhouse": ["stripe"]}), \
             patch("app.core.board_validator.send_telegram") as mock_send:
            result = check_and_alert_boards(db)

        assert result["newly_stale"] == ["greenhouse:stripe"]
        mock_send.assert_not_called()  # ADMIN_TELEGRAM_CHAT_ID not set in test env - logs only, doesn't crash trying to send

        row = db.query(BoardTokenStatus).filter(BoardTokenStatus.source_token_key == "greenhouse:stripe").first()
        assert row is not None
        assert row.is_live is False
        assert row.last_alerted_at is not None
    finally:
        db.close()


def test_repeated_check_of_an_already_dead_token_does_not_realert(client):
    """The actual point of state tracking: a token dead for days must
    not produce a fresh 'newly_stale' entry (and therefore not a fresh
    alert) on every single check cycle."""
    db = SessionLocal()
    try:
        with patch("app.core.board_validator.validate_boards", side_effect=_fake_validate_boards_one_dead), \
             patch("app.core.ingest.resolve_boards", return_value={"greenhouse": ["stripe"]}):
            first = check_and_alert_boards(db)
            second = check_and_alert_boards(db)

        assert first["newly_stale"] == ["greenhouse:stripe"]
        assert second["newly_stale"] == []  # already known dead - not reported as NEW again

        # Exactly one row should exist for this token, not one per check
        count = db.query(BoardTokenStatus).filter(BoardTokenStatus.source_token_key == "greenhouse:stripe").count()
        assert count == 1
    finally:
        db.close()


def test_recovery_is_detected_and_does_not_alert(client):
    db = SessionLocal()
    try:
        with patch("app.core.board_validator.validate_boards", side_effect=_fake_validate_boards_one_dead), \
             patch("app.core.ingest.resolve_boards", return_value={"greenhouse": ["stripe"]}):
            check_and_alert_boards(db)  # goes dead

        with patch("app.core.board_validator.validate_boards", side_effect=_fake_validate_boards_all_live), \
             patch("app.core.ingest.resolve_boards", return_value={"greenhouse": ["stripe"]}), \
             patch("app.core.board_validator.send_telegram") as mock_send:
            result = check_and_alert_boards(db)  # recovers

        assert result["newly_recovered"] == ["greenhouse:stripe"]
        assert result["newly_stale"] == []
        mock_send.assert_not_called()  # recovery is quiet, not alerted

        row = db.query(BoardTokenStatus).filter(BoardTokenStatus.source_token_key == "greenhouse:stripe").first()
        assert row.is_live is True
    finally:
        db.close()


def test_alert_sent_via_telegram_when_admin_chat_id_configured(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_CHAT_ID", "123456")
    db = SessionLocal()
    try:
        with patch("app.core.board_validator.validate_boards", side_effect=_fake_validate_boards_one_dead), \
             patch("app.core.ingest.resolve_boards", return_value={"greenhouse": ["stripe"]}), \
             patch("app.core.board_validator.send_telegram") as mock_send:
            check_and_alert_boards(db)

        mock_send.assert_called_once()
        call_chat_id = mock_send.call_args[0][0]
        assert call_chat_id == "123456"
    finally:
        db.close()
