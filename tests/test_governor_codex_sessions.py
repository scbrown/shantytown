"""Codex's persisted server rate limits are a governor signal (aegis-ui0c0)."""

import json

from shantytown import governor as gov


def _event(at="2026-08-23T20:00:00Z", *, primary=None, secondary=None):
    limits = {"limit_id": "codex", "primary": primary, "secondary": secondary}
    return json.dumps({
        "timestamp": at,
        "type": "event_msg",
        "payload": {"type": "token_count", "rate_limits": limits},
    })


def _session(root, body):
    path = root / "worker" / "sessions" / "2026" / "08" / "23" / "rollout.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(body)
    return path


def test_codex_session_reader_returns_server_usage_and_reset(tmp_path):
    _session(tmp_path, _event(
        primary={"used_percent": 86.0, "window_minutes": 10080,
                 "resets_at": 1787967695},
        secondary={"used_percent": 42.0, "window_minutes": 300,
                   "resets_at": 1787520000}) + "\n")

    readings = gov.CodexSessionReader(tmp_path).read_all()

    assert readings[gov.FIVE_HOUR].pct == 42.0
    assert readings[gov.SEVEN_DAY].pct == 86.0
    assert readings[gov.SEVEN_DAY].reset_at == 1787967695.0
    assert readings[gov.SEVEN_DAY].source == "codex_sessions"


def test_codex_session_reader_uses_the_latest_snapshot(tmp_path):
    _session(tmp_path, "\n".join([
        _event("2026-08-23T19:00:00Z", primary={
            "used_percent": 86.0, "window_minutes": 10080}),
        _event("2026-08-23T20:00:00Z", primary={
            "used_percent": 12.0, "window_minutes": 10080}),
    ]) + "\n")

    reading = gov.CodexSessionReader(tmp_path, gov.SEVEN_DAY).read()

    assert reading.pct == 12.0
    assert reading.at == 1787515200.0


def test_codex_session_reader_reports_signal_lost_when_no_snapshot_exists(tmp_path):
    _session(tmp_path, "not json\n")

    reading = gov.CodexSessionReader(tmp_path, gov.SEVEN_DAY).read()

    assert reading.pct is None
    assert "no Codex rate-limit snapshot" in reading.error


def test_reader_for_requires_a_codex_sessions_path():
    policy = gov.Policy(source="codex_sessions")

    try:
        gov.reader_for(policy)
    except gov.GovernorError as error:
        assert "needs `path`" in str(error)
    else:
        raise AssertionError("missing Codex session path was accepted")
