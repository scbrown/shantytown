"""`st crew` answers "who is free?" — aegis-o8we.

The verdict is triage's and has been load-bearing since #1 (dispatch refuses a
send into a busy pane); `st crew` just never asked it. These tests pin BOTH the
per-agent column and the free list, and — the part that matters — pin the two
states that must never be rounded to `idle`: a pane with no runtime UI in it, and
a pane that is down.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import cli
from shantytown import triage
from shantytown.tmux import NullPanes


# Real Claude Code chrome, as it appears at the bottom of a pane.
IDLE_SCREEN = "> \n  ? for shortcuts"
BUSY_SCREEN = "✻ Envisioning… (12s · 4.1k tokens · esc to interrupt)\n? for shortcuts"
SHELL_SCREEN = "braino@vati:~$ "
WEDGED_SCREEN = "[Process completed]"


# --- the predicate ----------------------------------------------------------

def test_work_state_reads_busy_idle_wedged():
    assert triage.work_state(BUSY_SCREEN, ui_up=True) == triage.BUSY
    assert triage.work_state(IDLE_SCREEN, ui_up=True) == triage.IDLE
    assert triage.work_state(WEDGED_SCREEN, ui_up=False) == triage.WEDGED


def test_quiet_pane_with_no_runtime_ui_is_unsure_not_idle():
    """A bare shell shows no in-flight marker either. Calling that `idle` puts a
    pane with nothing running in it on the free list, and the dispatch lands in a
    shell."""
    assert triage.work_state(SHELL_SCREEN, ui_up=False) == triage.UNSURE


def test_a_traceback_on_screen_does_not_hide_a_free_agent():
    """The aegis-hd2q lesson, one column over: agents print tracebacks constantly
    (a failing test prints one), so free-ness must key on the POSITIVE ready
    signal, never on is_live's DEAD_MARKERS."""
    screen = "Traceback (most recent call last):\nZeroDivisionError\n> \n? for shortcuts"
    assert triage.work_state(screen, ui_up=True) == triage.IDLE


def test_busy_beats_a_wedge_marker_in_scrollback():
    """Tail-only, same as every other predicate here: a marker further up the
    screen is an agent TALKING about a state, not being in it."""
    screen = "someone typed [Process completed] earlier\n" + "\n" * 10 + BUSY_SCREEN
    assert triage.work_state(screen, ui_up=True) == triage.BUSY


# --- the command ------------------------------------------------------------

class _Panes(NullPanes):
    """NullPanes returns one screen for every pane; a roster needs one each."""

    def __init__(self, screens: dict):
        super().__init__(live=set(screens))
        self._screens = screens

    def capture(self, pane: str, history: int = 0, attrs: bool = False) -> str:
        return self._screens.get(pane, "")


class _Args:
    def __init__(self, root):
        self.root = Path(root)
        self.backend = "files"; self.repo = None; self.registry = "files"


def _roster(tmp_path: Path, cards: dict) -> Path:
    crew = tmp_path / "crew"; crew.mkdir()
    for name, pane in cards.items():
        (crew / f"{name}.json").write_text(
            json.dumps({"role": "worker", "pane": pane} if pane else {"role": "worker"}))
    return tmp_path


def test_crew_prints_a_work_verdict_and_the_free_list(tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie", "ian": "p-ian", "malcolm": "p-mal"})
    panes = _Panes({"p-ellie": IDLE_SCREEN, "p-ian": BUSY_SCREEN,
                    "p-mal": IDLE_SCREEN})
    monkeypatch.setattr(cli, "Tmux", lambda: panes)

    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out

    rows = {ln.split()[0]: ln for ln in out.splitlines() if ln.startswith("  ") and
            ln.split() and ln.split()[0] in {"ellie", "ian", "malcolm"}}
    assert triage.IDLE in rows["ellie"] and triage.BUSY in rows["ian"]
    # The dispatcher's actual question, answered without scanning the table.
    assert "2 free: ellie, malcolm" in out
    assert "1 busy: ian" in out


def test_crew_says_zero_free_when_everyone_is_mid_flight(tmp_path, monkeypatch, capsys):
    """The negative control. A free list that has never been empty is not a
    measurement, and 'nobody is free' is the answer that changes what the
    dispatcher does next."""
    root = _roster(tmp_path, {"ellie": "p-ellie", "ian": "p-ian"})
    monkeypatch.setattr(cli, "Tmux",
                        lambda: _Panes({"p-ellie": BUSY_SCREEN, "p-ian": BUSY_SCREEN}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "0 free" in out and "interrupts work" in out
    assert "free: " not in out.replace("0 free", "")


def test_a_down_agent_is_never_free(tmp_path, monkeypatch, capsys):
    """`down` is not `idle`. A down agent on the free list sends work into a
    session that does not exist."""
    root = _roster(tmp_path, {"ellie": "p-ellie", "ian": "p-gone"})
    # only p-ellie is live; p-gone exists on the card but not in tmux
    monkeypatch.setattr(cli, "Tmux", lambda: _Panes({"p-ellie": IDLE_SCREEN}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "1 free: ellie" in out
    assert "ian" not in out.split("1 free:")[1]


def test_work_is_answered_for_agents_with_no_launch_stamp(tmp_path, monkeypatch, capsys):
    """The roster's other blind spot (o8we, second defect): over half the fleet
    has no launch stamp, so the settings column can only say `?`. The work verdict
    is derived from the PANE, so it is answerable anyway — and the stamp is NOT
    backfilled to make the other column look answered."""
    root = _roster(tmp_path, {"ellie": "p-ellie"})
    monkeypatch.setattr(cli, "Tmux", lambda: _Panes({"p-ellie": IDLE_SCREEN}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "no launch stamp" in out, "the settings column stopped being honest"
    assert "1 free: ellie" in out, "work must be answerable without a stamp"
    assert not (Path(root) / "launched").exists(), "a stamp was fabricated"


# --- the ready marker, re-measured (aegis-o8we) ------------------------------

# Verbatim from a live crew pane, 2026-07-20. Not a synthesised string: the
# earlier marker set was validated on ONE pane in default mode and matched
# nothing on a fleet that runs with a permission mode on.
LIVE_MODE_LINE = (
    "────────────────────────────────────\n"
    "❯ \n"
    "────────────────────────────────────\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents             /rc"
)


def test_a_pane_in_a_permission_mode_is_recognised_as_ready():
    """The mode line REPLACES "? for shortcuts", and every crew agent here runs
    with a mode on — so the pinned markers matched zero of nine live panes. This
    is the regression test for that miss: it fails against the old marker set."""
    from shantytown.runtime import ClaudeRuntime
    rt = ClaudeRuntime(NullPanes(), lambda card: "/s.json")
    assert rt.shows_ready_ui(LIVE_MODE_LINE)
    assert rt.is_live(LIVE_MODE_LINE)
    assert triage.work_state(LIVE_MODE_LINE, rt.shows_ready_ui(LIVE_MODE_LINE)) == triage.IDLE
