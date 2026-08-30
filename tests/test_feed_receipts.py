import json

from shantytown.feed_audit import (FeedAudit, START_UNACKNOWLEDGED,
                                    codex_turn_starts)


def _codex_event(path, payload):
    with path.open("a") as f:
        f.write(json.dumps({"type": "event_msg", "payload": payload}) + "\n")


def test_matching_codex_turn_is_the_receipt(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout = sessions / "rollout.jsonl"
    _codex_event(rollout, {"type": "task_started", "turn_id": "turn-1"})
    _codex_event(rollout, {"type": "item_completed", "turn_id": "turn-1",
                          "item": {"type": "UserMessage", "content": [
                              {"text": "work\n[st serve:s1 worker:muldoon]"}]}})
    assert codex_turn_starts(sessions) == {("s1", "muldoon")}


def test_marker_without_task_started_and_wrong_worker_do_not_ack(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    rollout = sessions / "rollout.jsonl"
    _codex_event(rollout, {"type": "item_completed", "turn_id": "missing",
                          "item": {"type": "UserMessage", "content": [
                              {"text": "[st serve:s1 worker:muldoon]"}]}})
    assert codex_turn_starts(sessions) == set()


def test_no_receipt_becomes_start_unacknowledged(tmp_path, monkeypatch):
    audit = FeedAudit(tmp_path, window_id="w")
    monkeypatch.setattr("shantytown.feed_audit.time.time", lambda: 10.0)
    audit.record("w", leg="delivery", worker="muldoon", item="aegis-x",
                 serve_id="s1", state="input_sent", acted_on=True)
    assert audit.reconcile_turn_starts(set(), now=41.0, timeout_s=30) == ["s1"]
    rows = [json.loads(x) for x in audit.path.read_text().splitlines()]
    assert rows[-1]["state"] == START_UNACKNOWLEDGED
    assert rows[-1]["acted_on"] is False and rows[-1]["refused"] is True


def test_late_receipt_cannot_revive_terminal_serve(tmp_path, monkeypatch):
    audit = FeedAudit(tmp_path, window_id="w")
    monkeypatch.setattr("shantytown.feed_audit.time.time", lambda: 10.0)
    audit.record("w", leg="delivery", worker="muldoon", item="aegis-x",
                 serve_id="s1", state="input_sent", acted_on=True)
    audit.reconcile_turn_starts(set(), now=41.0, timeout_s=30)
    audit.reconcile_turn_starts({("s1", "muldoon")}, now=42.0, timeout_s=30)
    rows = [json.loads(x) for x in audit.path.read_text().splitlines()]
    assert rows[-1]["state"] == "late_turn_started"
    assert rows[-1]["refused"] is True


def test_matching_receipt_advances_exactly_once(tmp_path, monkeypatch):
    audit = FeedAudit(tmp_path, window_id="w")
    monkeypatch.setattr("shantytown.feed_audit.time.time", lambda: 10.0)
    audit.record("w", leg="delivery", worker="muldoon", item="aegis-x",
                 serve_id="s1", state="input_sent", acted_on=True)
    receipt = {("s1", "muldoon")}
    audit.reconcile_turn_starts(receipt, now=11.0)
    audit.reconcile_turn_starts(receipt, now=12.0)
    rows = [json.loads(x) for x in audit.path.read_text().splitlines()]
    assert [r["state"] for r in rows].count("turn_started") == 1
