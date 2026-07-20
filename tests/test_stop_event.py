"""stop_event — SEND (route+persist) and RECEIVE (drain->block, block-once).
shantytown #6 (aegis-ct5q, arnold's ruling). The two tests arnold insisted on:
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
        {"role": "lead", "reports_to": "goldblum", "pane": "p-maldoon"}))
    (crew / "goldblum.json").write_text(json.dumps(
        {"role": "administrator", "pane": "p-goldblum"}))
    return FilesRegistry(crew)


class _Panes:
    def __init__(self, up, screens=None):
        self._up = set(up)
        self._screens = dict(screens or {})
    def exists(self, pane): return pane in self._up
    def capture(self, pane, history=0): return self._screens.get(pane, "")


# The two screens the drain-time verdict has to tell apart. Both are real shapes:
# the busy one is Claude Code's spinner footer (the "Envisioning… (39s)" sattler
# found on tim), the idle one is the mode line every crew pane here carries.
BUSY_SCREEN = "✻ Envisioning… (39s · ↑ 1.2k tokens · esc to interrupt)"
IDLE_SCREEN = "⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"


def _ready(screen: str) -> bool:
    """The runtime's marker check, as ClaudeRuntime.shows_ready_ui does it."""
    return "shift+tab to cycle" in screen


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
    to_admin = ev.drain("goldblum")
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
    ev.persist(to="goldblum", frm="ellie", reason="lead-unreachable", rose=True)
    stop_event._drain(ev, "goldblum")
    payload = json.loads(capsys.readouterr().out)
    assert "ROSE: lead-unreachable" in payload["reason"]


# --- main(): identity + mode guards ---------------------------------------------

def test_main_refuses_without_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_AGENT", raising=False)
    assert stop_event.main(["send", "--root", str(tmp_path)]) == 1


def test_main_rejects_a_bad_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_AGENT", "ellie")
    assert stop_event.main(["frobnicate", "--root", str(tmp_path)]) == 2


# --- aegis-w9z1: a stop is a TURN boundary, so the drain checks the pane -------

def _pending(root: Path, to: str) -> list:
    """Undelivered events for `to`, read WITHOUT draining (so a test can assert a
    deferral left the event on the store rather than dropping it)."""
    return [json.loads(p.read_text())
            for p in sorted((root / "events").glob("ev-*.json"))
            if not json.loads(p.read_text())["delivered"]
            and json.loads(p.read_text())["to"] == to]


def test_drain_DEFERS_an_event_whose_sender_is_still_mid_flight(tmp_path, capsys):
    """THE BUG (sattler, 2026-07-19). "tim stopped" arrived while tim was in
    `Envisioning… (39s)`; acting on it would have re-dispatched over live work.

    A deferral must do BOTH things: not wake the lead, and not lose the event.
    """
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    panes = _Panes({"p-ellie"}, {"p-ellie": BUSY_SCREEN})

    rc = stop_event._drain(ev, "maldoon", reg, panes, _ready)
    assert rc == 0
    assert capsys.readouterr().out == "", \
        "blocked the lead on a turn boundary — the whole aegis-w9z1 bug"
    assert len(_pending(tmp_path, "maldoon")) == 1, \
        "a deferred event was consumed, not held — that LOSES the stop"


def test_a_deferred_event_delivers_once_the_sender_really_stops(tmp_path, capsys):
    """The other half: deferral is a wait, not a filter. Same event, same store —
    only the pane changed."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False)

    stop_event._drain(ev, "maldoon", reg,
                      _Panes({"p-ellie"}, {"p-ellie": BUSY_SCREEN}), _ready)
    capsys.readouterr()
    stop_event._drain(ev, "maldoon", reg,
                      _Panes({"p-ellie"}, {"p-ellie": IDLE_SCREEN}), _ready)
    payload = json.loads(capsys.readouterr().out)
    assert "ellie stopped" in payload["reason"]
    assert "now: idle" in payload["reason"]


def test_a_DOWN_sender_is_delivered_not_deferred(tmp_path, capsys):
    """Not-busy is the delivery condition, not is-idle. An agent whose pane is
    gone is exactly who a coordinator must be woken for; deferring it would make
    a dead agent indistinguishable from a working one — silence either way."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    stop_event._drain(ev, "maldoon", reg, _Panes(set()), _ready)
    payload = json.loads(capsys.readouterr().out)
    assert "now: down" in payload["reason"]


def test_the_delivered_line_carries_when_and_what(tmp_path, capsys):
    """The three facts the old payload lacked. `reason` was the ROUTING reason and
    null in every real event, so a coordinator had to go re-read the tracker per
    agent to learn what the agent had even been holding."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False,
               item="it-7", item_status="in_progress")
    stop_event._drain(ev, "maldoon", reg,
                      _Panes({"p-ellie"}, {"p-ellie": IDLE_SCREEN}), _ready)
    reason = json.loads(capsys.readouterr().out)["reason"]
    assert "held it-7 (in_progress)" in reason
    assert "s ago" in reason, "no age — events cannot be ordered or aged"
    assert "now: idle" in reason


def test_an_unstamped_event_says_age_unknown_never_just_now(tmp_path, capsys):
    """Events written before this change have no ts. The reader must say so — a
    stale event rendered as fresh is worse than one that admits it cannot tell."""
    (tmp_path / "events").mkdir()
    (tmp_path / "events" / "ev-1.json").write_text(json.dumps(
        {"to": "maldoon", "frm": "ellie", "reason": None,
         "rose": False, "delivered": False}))          # the OLD schema, verbatim
    reg = _reg(tmp_path)
    stop_event._drain(FilesEvents(tmp_path / "events"), "maldoon", reg,
                      _Panes({"p-ellie"}, {"p-ellie": IDLE_SCREEN}), _ready)
    assert "age unknown" in json.loads(capsys.readouterr().out)["reason"]


def test_a_failed_item_lookup_never_renders_as_no_work(tmp_path, capsys):
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False,
               item=None, item_status="?")
    stop_event._drain(ev, "maldoon", reg,
                      _Panes({"p-ellie"}, {"p-ellie": IDLE_SCREEN}), _ready)
    reason = json.loads(capsys.readouterr().out)["reason"]
    assert "could not read the tracker" in reason
    assert "no open item" not in reason, \
        "a lookup that failed rendered as a finished plate"


def test_the_double_fire_collapses_to_ONE_line_for_the_agent(tmp_path, capsys):
    """kelly emitted TWO events for one continuous stretch of work. Two lines
    saying "kelly stopped" invite two decisions about one agent."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False, item="it-1")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False, item="it-1")
    stop_event._drain(ev, "maldoon", reg,
                      _Panes({"p-ellie"}, {"p-ellie": IDLE_SCREEN}), _ready)
    reason = json.loads(capsys.readouterr().out)["reason"]
    assert reason.count("ellie stopped") == 1
    assert "(2 events)" in reason, "collapsing must not hide the turnover count"


def test_drain_without_a_pane_backend_still_delivers(tmp_path, capsys):
    """No verdict available -> deliver anyway. Refusing to deliver on the strength
    of a check we never ran would be worse than the bug being fixed."""
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    assert stop_event._drain(ev, "maldoon") == 0
    assert "now: ?" in json.loads(capsys.readouterr().out)["reason"]


def test_send_records_what_the_agent_held(tmp_path):
    reg = _reg(tmp_path)
    items = tmp_path / "items"; items.mkdir()
    (items / "it-7.json").write_text(json.dumps(
        {"title": "the epic", "status": "in_progress", "assignee": "ellie"}))
    ev = FilesEvents(tmp_path / "events")
    assert stop_event._send(reg, ev, _Panes({"p-maldoon"}), "ellie", tmp_path) == 0
    got = ev.drain("maldoon")
    assert (got[0].item, got[0].item_status) == ("it-7", "in_progress")


def test_send_distinguishes_an_empty_plate_from_an_unread_one(tmp_path):
    """No items dir at all is still a READ that succeeded (nothing assigned), so
    it is an empty plate — not the '?' that means the tracker never answered."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    stop_event._send(reg, ev, _Panes({"p-maldoon"}), "ellie", tmp_path)
    got = ev.drain("maldoon")
    assert (got[0].item, got[0].item_status) == (None, None)


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
