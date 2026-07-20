"""Background shells are work that outlives the turn — aegis-q73g.

A worker whose TURN ended while a build/test/`gh run watch` is still live is not
finished, and the tier modelled none of it: triage passed the pane dispatchable,
`st crew` was silent, the stop event said only "stopped". Three surfaces, one
missing fact.

Every screen fixture below is VERBATIM from a live crew pane (swept with
capture-pane across all sessions on this host, 2026-07-20). Synthesised chrome is
how the ready-marker miss happened one bead ago — a marker that has only ever
matched a string a test wrote is evidence about the test.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import cli, stop_event, triage
from shantytown.events import FilesEvents
from shantytown.files import FilesRegistry
from shantytown.tmux import NullPanes


# weaver, mid-flight, status line (in-turn form)
LIVE_STATUS = (
    "  ⏵⏵ bypass permissions on · 1 shell · esc to interrupt · ← for agents · ↓ to…")
# goldblum, turn just ended, shell still live (the form that matters)
TURN_END = (
    "✻ Crunched for 7m 56s · 1 shell still running\n"
    "\n"
    "────────────────────────────────\n"
    "❯ \n"
    "────────────────────────────────\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents             /rc")
IDLE_NO_SHELL = (
    "❯ \n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents             /rc")


# --- the reader -------------------------------------------------------------

def test_reads_both_forms_the_runtime_prints():
    assert triage.running_shells(TURN_END) == 1
    assert triage.running_shells(LIVE_STATUS) == 1
    assert triage.running_shells("✻ Worked for 2m · 3 shells still running\n❯ ") == 3


def test_no_indicator_is_none_not_zero():
    """None means NOT REPORTED. A pane showing no chrome at all reports nothing,
    and "I could not see" must not be printed as "none running" — that is the
    same collapse this bead is about, one level down."""
    assert triage.running_shells(IDLE_NO_SHELL) is None
    assert triage.running_shells("braino@vati:~$ ") is None


def test_an_agent_talking_about_shells_is_not_running_one():
    """sattler's own pane contained "The pane shows 1 shell still running and a
    com…" while sattler ran no shell. Tail-only is what keeps a discussion of the
    state from being read as the state."""
    screen = ("  The pane shows 1 shell still running and a com\n"
              + "\n" * 12 + IDLE_NO_SHELL)
    assert triage.running_shells(screen) is None


# --- ask 1: triage records it as an INPUT, even where the verdict is unchanged --

def test_triage_inputs_carry_the_shell_count_on_every_screen_verdict():
    """The dispatch that prompted this bead returned `NUDGE healthy` with a shell
    live, and its own inputs line could not show that it had even looked."""
    nudge = triage.triage(NullPanes(screen=TURN_END), "p", "some new work")
    assert nudge.action is triage.Action.NUDGE          # verdict deliberately unchanged
    assert nudge.inputs["shells"] == 1
    assert "shells=1" in nudge.render()

    refuse = triage.triage(NullPanes(screen=LIVE_STATUS), "p", "x")
    assert refuse.action is triage.Action.REFUSE
    assert refuse.inputs["shells"] == 1


def test_triage_says_none_when_nothing_was_reported():
    d = triage.triage(NullPanes(screen=IDLE_NO_SHELL), "p", "x")
    assert d.inputs["shells"] is None
    assert "shells=None" in d.render()


# --- ask 2: `st crew` shows it ----------------------------------------------

class _Panes(NullPanes):
    def __init__(self, screens: dict):
        super().__init__(live=set(screens))
        self._screens = screens

    def capture(self, pane: str, history: int = 0) -> str:
        return self._screens.get(pane, "")


class _Args:
    def __init__(self, root):
        self.root = Path(root)
        self.backend = "files"; self.repo = None; self.registry = "files"


def _roster(tmp_path: Path, cards: dict) -> Path:
    crew = tmp_path / "crew"; crew.mkdir()
    for name, pane in cards.items():
        (crew / f"{name}.json").write_text(json.dumps({"role": "worker", "pane": pane}))
    return tmp_path


def test_crew_marks_an_idle_agent_that_still_owns_a_shell(tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie", "ian": "p-ian"})
    monkeypatch.setattr(cli, "Tmux", lambda: _Panes({"p-ellie": TURN_END,
                                                     "p-ian": IDLE_NO_SHELL}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "idle+1sh" in out, "an idle agent's live shell was invisible"
    assert "still own live background shells: ellie(1)" in out
    assert "not a task that finished" in out
    # Deliberately still free: whether a shell BLOCKS a dispatch is unruled
    # (sattler declined to rule it). Visible, not decided.
    assert "2 free: ellie, ian" in out


def test_crew_is_quiet_when_no_shells_are_running(tmp_path, monkeypatch, capsys):
    """The negative control — a warning that always prints is decoration."""
    root = _roster(tmp_path, {"ian": "p-ian"})
    monkeypatch.setattr(cli, "Tmux", lambda: _Panes({"p-ian": IDLE_NO_SHELL}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "background shells" not in out
    assert "+1sh" not in out


# --- ask 3: the stop event carries it ---------------------------------------

def _reg(tmp_path: Path) -> FilesRegistry:
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "maldoon", "pane": "p-ellie"}))
    (crew / "maldoon.json").write_text(json.dumps(
        {"role": "administrator", "pane": "p-maldoon"}))
    return FilesRegistry(crew)


def test_send_records_the_shells_the_agent_still_owned_at_stop(tmp_path, capsys):
    ev = FilesEvents(tmp_path / "events")
    panes = _Panes({"p-ellie": TURN_END, "p-maldoon": IDLE_NO_SHELL})
    assert stop_event._send(_reg(tmp_path), ev, panes, "ellie") == 0
    got = ev.drain("maldoon")
    assert [e.shells for e in got] == [1]


def test_drain_tells_the_destination_the_work_may_not_be_done(tmp_path, capsys):
    """"ellie stopped" invites the administrator to book the item done. The whole
    point of the field is that the destination is told otherwise, in the message
    that reaches its model."""
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False, shells=1)
    assert stop_event._drain(ev, "maldoon") == 0
    reason = json.loads(capsys.readouterr().out)["reason"]
    assert "STILL RUNNING 1 background shell(s)" in reason
    assert "its TURN ended, its WORK may not have" in reason


def test_a_clean_stop_says_nothing_extra(tmp_path, capsys):
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False, shells=None)
    stop_event._drain(ev, "maldoon")
    assert "STILL RUNNING" not in json.loads(capsys.readouterr().out)["reason"]


def test_a_pane_it_could_not_read_records_none_never_zero(tmp_path):
    """The hook must not manufacture "no shells running" — that fabricated claim
    would be made at exactly the moment the destination decides the work is done."""
    ev = FilesEvents(tmp_path / "events")

    class _Blind:
        def exists(self, pane): return True
        def capture(self, pane, history=0): raise OSError("tmux is gone")

    assert stop_event._send(_reg(tmp_path), ev, _Blind(), "ellie") == 0
    assert ev.drain("maldoon")[0].shells is None


def test_events_written_before_this_field_still_read(tmp_path):
    """Back-compat: an event persisted before q73g genuinely did not report a
    shell count, so None is the correct reading of its absence."""
    root = tmp_path / "events"; root.mkdir()
    (root / "ev-1.json").write_text(json.dumps(
        {"to": "maldoon", "frm": "ellie", "reason": None, "rose": False,
         "delivered": False}))
    got = FilesEvents(root).drain("maldoon")
    assert len(got) == 1 and got[0].shells is None
