"""Tests for Mattermost notification sender."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import httpx
import pytest

from tokenleak.notifications.mattermost import Mattermost


# ── Helpers ───────────────────────────────────────────────────────────────────

def _config(url="https://mm.example.com", token="tok", channel="alerts"):
    cfg = MagicMock()
    cfg.mattermost_url = url
    cfg.mattermost_token = token
    cfg.mattermost_channel = channel
    return cfg


def _disabled_config():
    return _config(url="", token="")


def _alert(severity="high", file_path="secrets.env", alert_type="secret", description="Found key"):
    a = MagicMock()
    a.severity = severity
    a.file_path = file_path
    a.alert_type = alert_type
    a.agent_json = {"description": description}
    return a


# ── enabled / disabled ───────────────────────────────────────────────────────

def test_disabled_when_no_url_or_token():
    mm = Mattermost(_disabled_config())
    assert not mm.enabled


def test_enabled_with_url_and_token():
    mm = Mattermost(_config())
    assert mm.enabled


# ── send() ────────────────────────────────────────────────────────────────────

def test_send_posts_correct_payload():
    mm = Mattermost(_config(channel="alerts"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=201)
        mm.send("hello")
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["text"] == "hello"
    assert kwargs["json"]["channel"] == "alerts"


def test_send_no_channel_key_when_channel_empty():
    """If no channel configured, 'channel' key must not appear in the payload."""
    mm = Mattermost(_config(channel=""))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=201)
        mm.send("hello")
    _, kwargs = mock_post.call_args
    assert "channel" not in kwargs["json"]


def test_send_override_channel():
    mm = Mattermost(_config(channel="default"))
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=201)
        mm.send("hi", channel="custom")
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["channel"] == "custom"


def test_send_raises_on_http_error():
    mm = Mattermost(_config())
    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(httpx.ConnectError):
            mm.send("hello")


def test_send_raises_on_4xx():
    mm = Mattermost(_config())
    resp = MagicMock()
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403", request=MagicMock(), response=MagicMock()
    )
    with patch("httpx.post", return_value=resp):
        with pytest.raises(httpx.HTTPStatusError):
            mm.send("hello")


def test_send_noop_when_disabled():
    mm = Mattermost(_disabled_config())
    with patch("httpx.post") as mock_post:
        mm.send("hello")
    mock_post.assert_not_called()


# ── send_scan_summary() ───────────────────────────────────────────────────────

def test_send_scan_summary_no_alerts():
    mm = Mattermost(_config())
    with patch.object(mm, "send") as mock_send:
        mm.send_scan_summary("https://github.com/org/repo", [], scan_id=1)
    mock_send.assert_called_once()
    assert "No secrets" in mock_send.call_args[0][0]


def test_send_scan_summary_with_alerts():
    mm = Mattermost(_config())
    alerts = [_alert("critical"), _alert("high"), _alert("high")]
    with patch.object(mm, "send") as mock_send:
        mm.send_scan_summary("https://github.com/org/repo", alerts, scan_id=5)
    text = mock_send.call_args[0][0]
    assert "3 alert(s)" in text
    assert "CRITICAL" in text
    assert "HIGH" in text


def test_send_scan_summary_truncates_at_10():
    mm = Mattermost(_config())
    alerts = [_alert() for _ in range(15)]
    with patch.object(mm, "send") as mock_send:
        mm.send_scan_summary("https://github.com/org/repo", alerts, scan_id=2)
    text = mock_send.call_args[0][0]
    assert "5 more" in text


def test_send_scan_summary_none_severity_safe():
    """AlertRow.severity=None must not crash formatting."""
    mm = Mattermost(_config())
    alerts = [_alert(severity=None, file_path=None)]
    with patch.object(mm, "send") as mock_send:
        mm.send_scan_summary("https://github.com/org/repo", alerts, scan_id=3)
    mock_send.assert_called_once()
    text = mock_send.call_args[0][0]
    assert "UNKNOWN" in text


def test_send_scan_summary_swallows_send_error():
    """A Mattermost failure in send_scan_summary must not propagate."""
    mm = Mattermost(_config())
    with patch.object(mm, "send", side_effect=httpx.ConnectError("down")):
        mm.send_scan_summary("https://github.com/org/repo", [], scan_id=9)
    # No exception raised — test passes


def test_send_scan_summary_noop_when_disabled():
    mm = Mattermost(_disabled_config())
    with patch.object(mm, "send") as mock_send:
        mm.send_scan_summary("url", [], 1)
    mock_send.assert_not_called()


# ── send_skipped_large_repo() ─────────────────────────────────────────────────

def test_send_skipped_large_repo_sends_message():
    mm = Mattermost(_config())
    with patch.object(mm, "send") as mock_send:
        mm.send_skipped_large_repo("https://github.com/org/big", 3000.0, 2048)
    text = mock_send.call_args[0][0]
    assert "3000" in text
    assert "2048" in text
    assert "big" in text


def test_send_skipped_large_repo_swallows_send_error():
    mm = Mattermost(_config())
    with patch.object(mm, "send", side_effect=httpx.ConnectError("down")):
        mm.send_skipped_large_repo("url", 9999.0, 2048)
    # No exception raised


def test_send_skipped_large_repo_noop_when_disabled():
    mm = Mattermost(_disabled_config())
    with patch.object(mm, "send") as mock_send:
        mm.send_skipped_large_repo("url", 500.0, 2048)
    mock_send.assert_not_called()
