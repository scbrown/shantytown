"""stop_event — SEND (route+persist) and RECEIVE (drain->block, block-once).
shantytown #6 (arnold's ruling). The two tests arnold insisted on:
survival-vs-delivery are separate, and BLOCK-ONCE (deliver once, then idle).
"""
from __future__ import annotations
import pathlib
import tempfile
import json
from pathlib import Path

import pytest

from shantytown import stop_event
from shantytown.runtime import settings_for_role
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


# A drain-capable launch line. `up` alone is no longer enough to be routed to:
# _lead_is_up now means "will it DRAIN", not "does a pane answer to that name"
# (aegis-0v97). Panes listed in `up` are modelled as st-launched unless the test
# overrides cmdlines to model a FOREIGN launcher.
# REAL files, not string literals: live_wiring PARSES the settings the launch line
# names, so a fake path reads as "cannot tell" and (correctly) rises. Modelling
# drain-capability therefore needs a settings file that genuinely carries drain.
_FAKE = pathlib.Path(tempfile.mkdtemp(prefix="st-stopev-"))
(_FAKE / "settings").mkdir()
(_FAKE / "settings" / "lead.settings.json").write_text(
    json.dumps(settings_for_role("lead", root=_FAKE)))
# A FOREIGN launcher's settings: real, readable, valid — and carrying no
# stop_event hook. This is the gastown shape that made dearing look available.
_FOREIGN = _FAKE / "foreign.settings.json"
_FOREIGN.write_text(json.dumps({"hooks": {"Stop": [
    {"hooks": [{"command": "/home/braino/.local/bin/gt costs record &"}]}]}}))

DRAIN_CMDLINE = f"claude --settings {_FAKE / 'settings' / 'lead.settings.json'}"
FOREIGN_CMDLINE = f"claude --settings {_FOREIGN}"


class _Panes:
    def __init__(self, up, screens=None, cmdlines=None):
        self._up = set(up)
        self._screens = dict(screens or {})
        # Default: every live pane is drain-capable, which preserves the intent
        # of every test written before the rule changed. A test that wants the
        # live-but-deaf lead passes cmdlines explicitly.
        self._cmdlines = dict(cmdlines) if cmdlines is not None else None
    def exists(self, pane): return pane in self._up
    def capture(self, pane, history=0): return self._screens.get(pane, "")
    def cmdline(self, pane):
        if self._cmdlines is not None:
            return self._cmdlines.get(pane)
        return DRAIN_CMDLINE if pane in self._up else None


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


def test_send_RISES_when_the_lead_is_UP_but_CANNOT_DRAIN(tmp_path):
    """THE aegis-0v97 CASE, and the reason `up` stopped meaning "pane exists".

    dearing's pane was resurrected by a FOREIGN launcher (gt-crew-up) carrying
    gastown's settings — real hooks, but no `stop_event` direction. Under the old
    predicate lead_is_up(dearing) was True, so seven workers routed TO it with
    rose=False and nothing rose to the administrator. The lead could not drain,
    so every one of those events was write-only.

    Being restarted made routing WORSE than being down: a down lead at least
    rises. This asserts the live-but-deaf lead now rises too.
    """
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    panes = _Panes({"p-maldoon"}, cmdlines={"p-maldoon": FOREIGN_CMDLINE})
    rc = stop_event._send(reg, ev, panes, "ellie")
    assert rc == 0
    assert ev.drain("maldoon") == []              # NOT delivered to the deaf lead
    risen = ev.drain("hammond")                  # rose to the administrator
    assert len(risen) == 1
    assert risen[0].rose is True
    assert risen[0].reason == "lead-unreachable"


def test_positive_control_a_drain_capable_lead_still_receives(tmp_path):
    """The other outcome, on the SAME machinery. Without this, the test above
    would also pass if `up` had simply been hardwired to False — which would
    rise everything to the admin forever and look like a fix."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    panes = _Panes({"p-maldoon"}, cmdlines={"p-maldoon": DRAIN_CMDLINE})
    stop_event._send(reg, ev, panes, "ellie")
    got = ev.drain("maldoon")
    assert [(e.frm, e.rose) for e in got] == [("ellie", False)]
    assert ev.drain("hammond") == []


def test_unreadable_process_fails_toward_RISING(tmp_path):
    """Cannot-tell must not be read as drain-capable. "Assume it drains" is the
    assumption that lost the events in the first place."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    panes = _Panes({"p-maldoon"}, cmdlines={})     # pane up, cmdline unreadable
    stop_event._send(reg, ev, panes, "ellie")
    assert ev.drain("maldoon") == []
    assert len(ev.drain("hammond")) == 1


# --- BLOCKED ON A QUESTION reaches the coordinator (aegis-qxc2) -----------------

# A real picker footer, as captured off lowery's pane 2026-07-20. Colour-split per
# word on the live pane; the plain form is what the runtime hands the predicate.
PICKER_SCREEN = "  Chat about this\n\nEnter to select · ↑/↓ to navigate · Esc to cancel"


def _asks(screen: str) -> bool:
    """The runtime's picker check, as ClaudeRuntime.awaiting_answer does it."""
    return "Enter to select" in screen or "Ready to submit your answers?" in screen


def test_a_stop_from_an_agent_ON_A_PICKER_says_so(tmp_path, capsys):
    """The coordinator must learn it WITHOUT scraping a pane — that scrape is the
    whole investigation the stop event exists to save. Before this the verdict
    read `?`, which is honest and gives the reader nothing to do."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False,
               item="it-9", item_status="in_progress")
    stop_event._drain(ev, "maldoon", reg,
                      _Panes({"p-ellie"}, {"p-ellie": PICKER_SCREEN}), _ready,
                      _asks)
    reason = json.loads(capsys.readouterr().out)["reason"]
    assert "now: waiting" in reason
    assert "BLOCKED ON A QUESTION" in reason
    assert "held it-9 (in_progress)" in reason, "the other facts must survive"


def test_without_the_picker_check_the_same_pane_is_only_unsure(tmp_path, capsys):
    """The control. Same screen, no runtime answer — the line degrades to `?`,
    never to a confident verdict, and never crashes the drain."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False,
               item="it-9", item_status="in_progress")
    stop_event._drain(ev, "maldoon", reg,
                      _Panes({"p-ellie"}, {"p-ellie": PICKER_SCREEN}), _ready)
    reason = json.loads(capsys.readouterr().out)["reason"]
    assert "now: ?" in reason
    assert "BLOCKED ON A QUESTION" not in reason


def test_a_waiting_sender_is_DELIVERED_not_deferred(tmp_path, capsys):
    """Only BUSY defers. An agent stopped on a question is precisely who a
    coordinator needs waking for — deferring it would hold the event until the
    agent stopped being stuck, which is the thing that was never going to happen
    on its own."""
    reg = _reg(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    stop_event._drain(ev, "maldoon", reg,
                      _Panes({"p-ellie"}, {"p-ellie": PICKER_SCREEN}), _ready,
                      _asks)
    out = capsys.readouterr()
    # _drain returns 0 either way — delivery is observable ONLY in what it emits,
    # so assert on the block decision itself and on the absence of the held-back
    # notice. An rc check here would pass whether or not the event was delivered.
    assert json.loads(out.out)["decision"] == "block"
    assert "held back" not in out.err
