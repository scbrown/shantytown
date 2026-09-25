import json

from shantytown import stop_event
from shantytown.events import FilesEvents
from shantytown.files import FilesRegistry
from shantytown.stopped import FilesStops
from shantytown.tmux import NullPanes


def setup(tmp_path):
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "worker.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "lead", "pane": "worker-pane"}))
    (crew / "lead.json").write_text(json.dumps(
        {"role": "administrator", "pane": "lead-pane"}))
    return FilesRegistry(crew), FilesEvents(tmp_path / "events")


def test_opt_in_keeps_pane_private_and_records_state(tmp_path, monkeypatch, capsys):
    reg, events = setup(tmp_path)
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setenv("SHANTY_STOP_SAMPLES", str(private))
    monkeypatch.setattr(stop_event, "_plate_of", lambda *a: ("task-1", "in_progress", []))
    FilesStops(tmp_path / "stopped").record("worker", 123, "operator", "pause")
    assert stop_event._send(reg, events, NullPanes(screen="PRIVATE_SENTINEL"), "worker", tmp_path) == 0
    data = json.loads((private / "stop_samples/collection.json").read_text())["samples"][0]
    assert data["pane_tail"] == "PRIVATE_SENTINEL"
    assert data["item"] == "task-1" and data["item_status"] == "in_progress"
    assert data["explicit_stop"]["reason"] == "pause"
    assert "PRIVATE_SENTINEL" not in capsys.readouterr().err
    assert "PRIVATE_SENTINEL" not in next((tmp_path / "events").glob("ev-*.json")).read_text()


def test_disabled_writes_no_samples(tmp_path):
    reg, events = setup(tmp_path)
    assert stop_event._send(reg, events, NullPanes(screen="tail"), "worker", tmp_path) == 0
    assert not (tmp_path / "stop_samples").exists()


def test_capture_failure_keeps_normal_delivery_and_hides_message(tmp_path, monkeypatch, capsys):
    reg, events = setup(tmp_path)
    monkeypatch.setenv("SHANTY_STOP_SAMPLES", str(tmp_path))
    from shantytown import stop_samples
    def failed(*a, **kw):
        raise RuntimeError("PRIVATE_SENTINEL")
    monkeypatch.setattr(stop_samples, "collect", failed)
    assert stop_event._send(reg, events, NullPanes(), "worker", tmp_path) == 0
    assert events.drain("lead")
    output = capsys.readouterr()
    assert "RuntimeError" in output.err
    assert "PRIVATE_SENTINEL" not in output.err + output.out
