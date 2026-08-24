"""Codex's persisted server rate limits are a governor signal (aegis-ui0c0)."""

import json

from shantytown import governor as gov


def _event(at="2026-08-23T20:00:00Z", *, primary=None, secondary=None,
           limit_id="codex"):
    limits = {"limit_id": limit_id, "primary": primary, "secondary": secondary}
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


# --- aegis-ftsqh: WHOSE limit is this, and what did the scan drop? ------------
#
# Codex meters more than one bucket, and their WINDOWS DIFFER. Measured live
# 2026-08-24 via `account/rateLimits/read`:
#
#     codex            primary 10080 min   (no 300-minute window at all)
#     codex_bengalfox  primary   300 min, secondary 10080 min
#
# So "the five-hour reading" is not a well-formed request until you say whose,
# and a reader that files a 300-minute number without naming its bucket can
# hand the governor a number about a limit it is not governing.


def test_the_reading_CARRIES_the_limit_it_describes(tmp_path):
    _session(tmp_path, _event(
        primary={"used_percent": 12.0, "window_minutes": 10080,
                 "resets_at": 1788137002}) + "\n")
    reading = gov.CodexSessionReader(tmp_path, window="seven_day").read()
    assert reading.pct == 12.0
    assert reading.limit_id == "codex", (
        "a reading that cannot name its limit cannot be checked against the "
        "limit the governor believes it is governing")


def test_a_pinned_limit_REFUSES_another_bucket_rather_than_reporting_it(tmp_path):
    """The failure this prevents: a Spark-metered session supplying THE
    five-hour number while the governor believes it is watching `codex`."""
    _session(tmp_path, _event(
        limit_id="codex_bengalfox",
        primary={"used_percent": 0.0, "window_minutes": 300,
                 "resets_at": 1787565328}) + "\n")

    # Unpinned: reported, and it says whose it is.
    loose = gov.CodexSessionReader(tmp_path, window="five_hour").read()
    assert loose.pct == 0.0 and loose.limit_id == "codex_bengalfox"

    # Pinned to `codex`: this snapshot is NOT `codex`, so it is not reported —
    # and the refusal names what was asked for, not what was found.
    pinned = gov.CodexSessionReader(tmp_path, window="five_hour",
                                    limit_id="codex").read()
    assert pinned.pct is None
    assert "codex" in pinned.error and "no Codex rate-limit snapshot" in pinned.error


def test_an_unmappable_window_is_REPORTED_not_silently_dropped(tmp_path):
    """77 real 43200-minute (30-day) readings were discarded on this host with
    no error, because a partial map looked identical to a full one."""
    _session(tmp_path, _event(
        primary={"used_percent": 12.0, "window_minutes": 10080,
                 "resets_at": 1788137002},
        secondary={"used_percent": 6.0, "window_minutes": 43200,
                   "resets_at": 1790000000}) + "\n")
    reader = gov.CodexSessionReader(tmp_path, window="seven_day")

    assert reader.read().pct == 12.0, "the mappable window must still be reported"
    note = reader.note()
    assert "43200" in note, f"the dropped window was not named: {note!r}"

    # And the note APPENDS to a specific error; it must never replace it.
    missing = gov.CodexSessionReader(tmp_path, window="five_hour").read()
    assert "five_hour" in missing.error, (
        f"the note displaced the message that names the actual gap: "
        f"{missing.error!r}")
    assert "43200" in missing.error


def test_a_fully_unmappable_snapshot_says_which_windows_it_had(tmp_path):
    _session(tmp_path, _event(
        primary={"used_percent": 6.0, "window_minutes": 43200,
                 "resets_at": 1790000000}) + "\n")
    err = gov.CodexSessionReader(tmp_path, window="seven_day").read().error
    assert "43200" in err and "300" in err and "10080" in err, err
