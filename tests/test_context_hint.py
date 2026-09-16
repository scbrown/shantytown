"""Exercise the actual Stop consumers, including their formerly silent exits."""
import io
import json

import pytest

from shantytown import context_hint as ch, context_spend as cs, session_budget as sb
from shantytown import stop_event, stop_policy
from shantytown.protocols import Agent


def world(tmp_path, role="worker", extra=""):
    (tmp_path / "shantytown.toml").write_text(
        '[session_budget]\ncontext_window = 1000\ncontext_threshold_pct = 70\n' + extra)
    path = tmp_path / "session.jsonl"
    turn(path, 700)
    card = Agent(name="reader", role=role, pane="p")
    return card, {"session_id": "session-one", "transcript_path": str(path)}


def turn(path, tokens):
    from pathlib import Path
    Path(path).write_text(json.dumps({"message": {"model": "ambiguous-model", "usage": {
        "input_tokens": 2, "cache_read_input_tokens": tokens - 2,
        "cache_creation_input_tokens": 0}}}) + "\n")


@pytest.mark.parametrize("role", ["worker", "lead", "administrator"])
def test_actual_stop_paths_emit_once_even_with_active_work(tmp_path, monkeypatch, capsys, role):
    card, payload = world(tmp_path, role)
    class Reg:
        def get(self, _):
            return card
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    # If occupancy were checked after the old early-return/decision paths,
    # these calls would run instead of the hint reaching the model.
    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary stop policy reached before context feedback")
    monkeypatch.setattr(stop_event, "_bd_json", forbidden)
    monkeypatch.setattr(stop_policy, "gather", forbidden)
    if role == "administrator":
        stop_policy.run(tmp_path, card.name, reg=Reg())
    else:
        stop_event._haul(Reg(), None, card.name, tmp_path)
    message = json.loads(capsys.readouterr().out)
    assert message["decision"] == "block"
    assert "70%" in message["reason"]
    assert "st cycle --self --checkpoint-file <notes-file>" in message["reason"]
    assert "advisory" in message["reason"]
    assert not ch.emit(tmp_path, card, payload)
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "notify/cycle-requests.json").exists()
    assert not (tmp_path / "session_budget").exists(), "never latch a work ceiling"
    assert sb.gate(tmp_path, card.name)[2] is None


def test_below_threshold_rearms_and_new_session_rearms(tmp_path, capsys):
    card, payload = world(tmp_path)
    assert ch.emit(tmp_path, card, payload)
    turn(payload["transcript_path"], 699)
    assert not ch.emit(tmp_path, card, payload)
    turn(payload["transcript_path"], 701)
    assert ch.emit(tmp_path, card, payload)
    assert not ch.emit(tmp_path, card, payload)
    payload["session_id"] = "new-session"
    assert ch.emit(tmp_path, card, payload)


@pytest.mark.parametrize("change,expected", [
    ({"transcript_path": "missing"}, "UNKNOWN"),
    ({"session_id": None}, "UNKNOWN"),
])
def test_unreadable_identity_or_transcript_is_not_zero(tmp_path, capsys, change, expected):
    card, payload = world(tmp_path)
    payload.update(change)
    assert ch.emit(tmp_path, card, payload)
    message = json.loads(capsys.readouterr().out)["reason"]
    assert expected in message and "0%" not in message


def test_over_window_is_configuration_fault_not_cycle_hint(tmp_path, capsys):
    card, payload = world(tmp_path)
    turn(payload["transcript_path"], 1001)
    assert ch.emit(tmp_path, card, payload)
    message = json.loads(capsys.readouterr().out)["reason"]
    assert "EXCEEDS" in message and "CONTEXT HINT" not in message
    assert not ch.emit(tmp_path, card, payload), "fault must not trap the session"


def test_crew_reads_same_transcript_but_rejects_stale_session(tmp_path, monkeypatch):
    card, payload = world(tmp_path)
    ch.emit(tmp_path, card, payload)
    monkeypatch.setattr(sb, "current_session", lambda *_: payload["session_id"])
    assert "ceiling (context), advisory" in ch.crew_label(tmp_path, card)
    turn(payload["transcript_path"], 400)
    assert "40%" in ch.crew_label(tmp_path, card)
    monkeypatch.setattr(sb, "current_session", lambda *_: "other-session")
    assert "UNKNOWN" in ch.crew_label(tmp_path, card)


@pytest.mark.parametrize("key,value", [
    ("context_window", 0), ("context_window", 1000.5), ("context_window", True),
    ("context_threshold_pct", 0), ("context_threshold_pct", 100),
    ("context_threshold_pct", float("nan")), ("context_threshold_pct", True),
    ("context_by_role", []), ("context_by_role", {"lead": {"typo": 1}}),
])
def test_invalid_window_or_threshold_fails_loud(key, value):
    with pytest.raises(sb.BudgetError):
        sb.parse({key: value})


def test_role_override_inherits_and_omission_stays_off():
    assert sb.parse({}).context_for("worker") is None
    limits = sb.parse({"context_window": 1000, "context_by_role": {
        "lead": {"context_threshold_pct": 60}}})
    assert limits.context_for("worker") == sb.ContextLimits(1000, 70)
    assert limits.context_for("lead") == sb.ContextLimits(1000, 60)
    assert sb.parse({"context_threshold_pct": 70}).context.window is None


def test_bad_config_emits_unknown_not_silent_off(tmp_path, capsys):
    card, payload = world(tmp_path)
    (tmp_path / "shantytown.toml").write_text('[session_budget]\ncontext_window = -1\n')
    assert ch.emit(tmp_path, card, payload)
    assert "UNKNOWN" in json.loads(capsys.readouterr().out)["reason"]


@pytest.mark.parametrize("usage", [{"output_tokens": 90}, {"input_tokens": -1},
                                    {"input_tokens": True}, {"input_tokens": "100"}])
def test_malformed_latest_usage_is_unknown_not_zero_or_old_value(tmp_path, usage):
    path = tmp_path / "s.jsonl"
    turn(path, 700)
    with path.open("a") as f:
        f.write(json.dumps({"message": {"usage": usage}}) + "\n")
    assert cs.read(path, 1000).state == cs.UNKNOWN


def test_reverse_reader_crosses_large_records_and_ignores_sidechain(tmp_path):
    path = tmp_path / "s.jsonl"
    turn(path, 700)
    with path.open("a") as f:
        f.write(json.dumps({"content": "x" * 140000}) + "\n")
        f.write(json.dumps({"isSidechain": True, "message": {"usage": {"input_tokens": 1}}}) + "\n")
        f.write('{"partial":')
    assert cs.read_consumed(path)[0] == 700


def test_codex_uses_last_input_not_cumulative_or_cached_again(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "payload": {
        "type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 9000000},
            "last_token_usage": {"input_tokens": 700, "cached_input_tokens": 650}}}}))
    assert cs.read_consumed(path)[0] == 700


@pytest.mark.parametrize("data", [{"payload": []}, {"payload": {"session_id": "s"}, "at": "bad"}])
def test_corrupt_snapshot_is_unknown(tmp_path, data):
    card, _ = world(tmp_path)
    ch._save(ch._path(tmp_path, card), data)
    assert "UNKNOWN" in ch.crew_label(tmp_path, card)


def test_explicit_agent_window_overrides_role_and_reaches_hook(tmp_path, capsys):
    card, payload = world(tmp_path, extra='[session_budget.context_by_agent.reader]\ncontext_window = 500\n')
    assert ch.policy(tmp_path, card)[0].window == 500
    assert ch.emit(tmp_path, card, payload)
    assert "EXCEEDS" in json.loads(capsys.readouterr().out)["reason"]
    assert sb.parse({"context_by_agent": {"reader": {"context_window": 1000}}}).context_for("worker", "other") is None


@pytest.mark.parametrize("native,expected", [(1000, cs.MEASURED), (0, cs.UNKNOWN),
                                             (True, cs.UNKNOWN), ("1000", cs.UNKNOWN),
                                             (None, cs.UNKNOWN)])
def test_codex_native_capacity_without_declaration(tmp_path, native, expected):
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count",
        "info": {"last_token_usage": {"input_tokens": 750}, "model_context_window": native}}}))
    reading = cs.read(path, None)
    assert reading.state == expected
    if expected == cs.MEASURED:
        assert reading.pct == 75
    assert cs.read(path, 2000).pct == 37.5, "explicit override remains authoritative"


def test_native_window_and_usage_come_from_same_latest_record(tmp_path, capsys):
    card, payload = world(tmp_path)
    (tmp_path / "shantytown.toml").write_text('[session_budget]\ncontext_threshold_pct = 70\n')
    from pathlib import Path
    def record(window):
        return {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "model_context_window": window, "last_token_usage": {"input_tokens": 750}}}}
    path = Path(payload["transcript_path"])
    path.write_text(json.dumps(record(2000)) + "\n" + json.dumps(record(1000)))
    assert ch.emit(tmp_path, card, payload)
    assert "75%" in json.loads(capsys.readouterr().out)["reason"]
    path.write_text(json.dumps(record(1000)) + "\n" + json.dumps(record(None)))
    assert cs.read(path, None).state == cs.UNKNOWN, "never reuse old capacity after a change"
