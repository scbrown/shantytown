"""stop_event — SEND (route+persist) and RECEIVE (drain->block, block-once).
shantytown #6 (arnold's ruling). The two tests arnold insisted on:
survival-vs-delivery are separate, and BLOCK-ONCE (deliver once, then idle).
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import stop_event
from shantytown.events import FilesEvents
from shantytown.files import FilesRegistry


def _reg(tmp_path: Path) -> FilesRegistry:
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "maldoon", "pane": "p-ellie"}))
    (crew / "maldoon.json").write_text(json.dumps(
        {"role": "lead", "reports_to": "hammond", "pane": "p-maldoon"}))
    (crew / "hammond.json").write_text(json.dumps(
        {"role": "administrator", "pane": "p-hammond"}))
    return FilesRegistry(crew)


class _Panes:
    def __init__(self, up): self._up = set(up)
    def exists(self, pane): return pane in self._up


# --- SEND: route_stop -> persist. survival, and the rise on a down lead ----------

def test_send_persists_to_the_lead_when_up(tmp_path):
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    rc = stop_event._send(reg, ev, _Panes({"p-maldoon"}), "ellie")
    assert rc == 0
    got = ev.drain("maldoon")
    assert [(e.frm, e.rose) for e in got] == [("ellie", False)]


def test_send_RISES_to_admin_when_the_lead_is_down(tmp_path):
    """The survival case that matters: the lead is down, so the event must not sit
    for a reader that will never come — it rises to the admin, carrying the reason."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    rc = stop_event._send(reg, ev, _Panes(set()), "ellie")     # nothing up
    assert rc == 0
    to_admin = ev.drain("hammond")
    assert len(to_admin) == 1
    assert to_admin[0].rose is True
    assert to_admin[0].reason == "lead-unreachable"
    assert ev.drain("maldoon") == [], "a risen event must NOT also sit on the down lead"


# --- RECEIVE: drain -> decision:block+reason, and BLOCK-ONCE ---------------------

def test_drain_emits_block_with_reason_then_idles(tmp_path, capsys):
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False)

    rc = stop_event._drain(ev, "maldoon")
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["decision"] == "block"        # reaches the MODEL
    assert "ellie stopped" in payload["reason"]
    assert "systemMessage" not in payload         # rail 2: never terminal-only

    # BLOCK-ONCE: the next stop drains nothing -> NO block -> the lead can idle.
    rc2 = stop_event._drain(ev, "maldoon")
    assert rc2 == 0
    assert capsys.readouterr().out == "", "re-blocked a delivered event -> wedge"


def test_drain_with_nothing_pending_is_silent(tmp_path, capsys):
    ev = FilesEvents(tmp_path / "events")
    rc = stop_event._drain(ev, "maldoon")
    assert rc == 0
    assert capsys.readouterr().out == ""          # no block -> idle


def test_drain_reason_flags_a_rise(tmp_path, capsys):
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="hammond", frm="ellie", reason="lead-unreachable", rose=True)
    stop_event._drain(ev, "hammond")
    payload = json.loads(capsys.readouterr().out)
    assert "ROSE: lead-unreachable" in payload["reason"]


# --- main(): identity + mode guards ---------------------------------------------

def test_main_refuses_without_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_AGENT", raising=False)
    assert stop_event.main(["send", "--root", str(tmp_path)]) == 1


def test_main_rejects_a_bad_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_AGENT", "ellie")
    assert stop_event.main(["frobnicate", "--root", str(tmp_path)]) == 2


def test_main_send_end_to_end(tmp_path, monkeypatch):
    """main() wires the real registry+events from --root and $SHANTY_AGENT; only
    the pane-liveness backend is faked."""
    _reg(tmp_path)
    monkeypatch.setenv("SHANTY_AGENT", "ellie")
    monkeypatch.setattr(stop_event, "Tmux", lambda: _Panes({"p-maldoon"}))
    rc = stop_event.main(["send", "--root", str(tmp_path)])
    assert rc == 0
    got = FilesEvents(tmp_path / "events").drain("maldoon")
    assert [e.frm for e in got] == ["ellie"]
