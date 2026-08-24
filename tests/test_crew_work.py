"""`st crew` answers "who is free?".

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
from shantytown.cycle import Requests
from shantytown import triage
from shantytown.tmux import NullPanes


# Real Claude Code chrome, as it appears at the bottom of a pane.
IDLE_SCREEN = "> \n  ? for shortcuts"
BUSY_SCREEN = "✻ Envisioning… (12s · 4.1k tokens · esc to interrupt)\n? for shortcuts"
SHELL_SCREEN = "user@host:~$ "
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
    """The wedged-verdict lesson, one column over: agents print tracebacks constantly
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
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)

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
                        lambda *_a, **_k: _Panes({"p-ellie": BUSY_SCREEN,
                                                  "p-ian": BUSY_SCREEN}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "0 free" in out and "interrupts work" in out
    assert "free: " not in out.replace("0 free", "")


def test_unknown_work_is_counted_loudly_and_prevents_false_saturation_summary(
        tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie", "ian": "p-ian"})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(
        {"p-ellie": BUSY_SCREEN, "p-ian": SHELL_SCREEN}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "1 busy: ellie" in out
    assert "1 UNKNOWN work state: ian" in out
    assert "every live agent is mid-flight" not in out


def test_a_down_agent_is_never_free(tmp_path, monkeypatch, capsys):
    """`down` is not `idle`. A down agent on the free list sends work into a
    session that does not exist."""
    root = _roster(tmp_path, {"ellie": "p-ellie", "ian": "p-gone"})
    # only p-ellie is live; p-gone exists on the card but not in tmux
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes({"p-ellie": IDLE_SCREEN}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "1 free: ellie" in out
    # The FREE LINE, not the rest of the output. `ian` legitimately appears
    # further down (it is on the roster, and other blocks name it); the claim
    # under test is that it is not offered as available.
    free_line = next(l for l in out.splitlines() if "1 free:" in l)
    assert "ian" not in free_line


def test_pending_cycle_is_visible_and_not_offered_for_time_sensitive_work(
        tmp_path, monkeypatch, capsys):
    """The durable request protects the interval before tend stops the pane."""
    root = _roster(tmp_path, {"ellie": "p-ellie", "ian": "p-ian"})
    Requests(root).request("ian", "checkpoint is durable")
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(
        {"p-ellie": IDLE_SCREEN, "p-ian": IDLE_SCREEN}))

    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.split() and
               line.split()[0] == "ian")
    assert "cycling" in row
    assert "planned context cycle(s): ian" in out
    assert "holds the window until relaunch completes" in out
    assert "1 free: ellie" in out


def test_work_is_answered_for_agents_with_no_launch_stamp(tmp_path, monkeypatch, capsys):
    """The roster's other blind spot (second defect): over half the fleet
    has no launch stamp, so the settings column can only say `?`. The work verdict
    is derived from the PANE, so it is answerable anyway — and the stamp is NOT
    backfilled to make the other column look answered."""
    root = _roster(tmp_path, {"ellie": "p-ellie"})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes({"p-ellie": IDLE_SCREEN}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "no launch stamp" in out, "the settings column stopped being honest"
    assert "1 free: ellie" in out, "work must be answerable without a stamp"
    assert not (Path(root) / "launched").exists(), "a stamp was fabricated"


# --- the ready marker, re-measured ------------------------------

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


# --- the roster's blind spot: sessions it never enumerated (aegis-np4x1) ------

def test_crew_NAMES_a_duplicate_running_under_a_retired_naming_scheme(
        tmp_path, monkeypatch, capsys):
    """MEASURED 2026-08-01. A boot-time autostart left enabled through the
    shanty-* rename brought up six agents under the old aegis-crew-* names, and
    this command reported 19 agents and ZERO faults beside them — every check it
    owns is addressed by a pane name off a card, so a session under a name no
    card carries could not be looked at, let alone judged.

    The `down` in the assertion is the sharp end: st did not merely stay quiet,
    it reported goldblum DOWN while goldblum was running one socket over.
    """
    root = _roster(tmp_path, {"goldblum": "shanty-goldblum", "ian": "shanty-ian"})
    panes = _Panes({"shanty-ian": BUSY_SCREEN,
                    "aegis-crew-goldblum": BUSY_SCREEN})   # the twin; card pane down
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)

    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out

    assert "aegis-crew-goldblum" in out, "the duplicate was not NAMED"
    assert "→ goldblum" in out, "named the session but not the agent it doubles"
    assert "card says DOWN" in out, \
        "did not say the card contradicts the socket — the whole tell"
    assert "at the next boot" in out, \
        "sent a human to kill a session without warning them it respawns"


def test_crew_stays_quiet_when_every_session_is_on_a_card(tmp_path, monkeypatch, capsys):
    """The control, and it is the one that decides whether this feature is
    usable: `st crew` runs constantly, so a stray warning that cries on a clean
    fleet gets trained out and the real one goes with it."""
    root = _roster(tmp_path, {"ellie": "p-ellie", "ian": "p-ian"})
    monkeypatch.setattr(cli, "Tmux",
                        lambda *_a, **_k: _Panes({"p-ellie": IDLE_SCREEN,
                                                  "p-ian": BUSY_SCREEN}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "no card claims" not in out and "→" not in out


def test_crew_reports_an_unrecognised_session_without_calling_it_crew(
        tmp_path, monkeypatch, capsys):
    """A session matching no roster name is listed, not blamed. It may be
    somebody's shell — but st cannot tend what it cannot name, and dropping it
    would rebuild the same blind spot one size smaller."""
    root = _roster(tmp_path, {"ellie": "p-ellie"})
    monkeypatch.setattr(cli, "Tmux",
                        lambda *_a, **_k: _Panes({"p-ellie": IDLE_SCREEN,
                                                  "scratch": SHELL_SCREEN}))
    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    assert "no card claims: scratch" in out
    assert "listed, not judged" in out
    assert "→" not in out, "blamed an agent for a session that names none"


# --- card-vs-process ROLE DRIFT (internal-ref) --------------------------------
#
# Settings are read ONCE at launch; the card is read CONTINUOUSLY. Promote an
# agent to lead by card edit and the running process never gets `drain`, so its
# reports' stop events rise to the administrator as `lead-unreachable` while the
# lead is visibly up. The tier is configured and INERT, and the cards — the only
# place anyone looks — show it as correct.

from shantytown.runtime import settings_for_role


class _CmdlinePanes(_Panes):
    """Panes that can also report a launch cmdline — the only evidence of what a
    running process actually carries."""

    def __init__(self, screens: dict, cmdlines: dict):
        super().__init__(screens)
        self._cmdlines = cmdlines

    def cmdline(self, pane: str) -> str:
        return self._cmdlines.get(pane, "")


def _tier_roster(tmp_path: Path):
    """A lead with a report under an administrator — the real shape."""
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "boss.json").write_text(json.dumps(
        {"role": "administrator", "pane": "p-boss"}))
    (crew / "mal.json").write_text(json.dumps(
        {"role": "lead", "reports_to": "boss", "pane": "p-mal"}))
    (crew / "ellie.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "mal", "pane": "p-ellie"}))
    return tmp_path


def _launched_as(tmp_path: Path, role: str) -> str:
    """A cmdline naming a REAL emitted settings artifact for `role`. Built by the
    production emitter so the fake cannot drift from what st actually writes."""
    p = tmp_path / f"{role}.settings.json"
    p.write_text(json.dumps(settings_for_role(role, root=tmp_path)))
    return f"claude --settings {p}"


def _crew_out(tmp_path, monkeypatch, capsys, launched_role):
    root = _tier_roster(tmp_path)
    panes = _CmdlinePanes(
        {"p-boss": IDLE_SCREEN, "p-mal": IDLE_SCREEN, "p-ellie": IDLE_SCREEN},
        {"p-mal": _launched_as(tmp_path, launched_role),
         "p-ellie": _launched_as(tmp_path, "worker"),
         "p-boss": _launched_as(tmp_path, "administrator")})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    assert cli._cmd_crew(_Args(root)) == cli.OK
    return capsys.readouterr().out


def test_crew_NAMES_a_lead_running_settings_that_lack_drain(tmp_path, monkeypatch, capsys):
    """mal's CARD says lead; mal's PROCESS was launched as a worker."""
    out = _crew_out(tmp_path, monkeypatch, capsys, "worker")
    assert "MATCH THEIR ROLE" in out, (
        "a promoted-but-not-relaunched lead was not named — the tier is inert "
        "and st crew reports it as fine")
    assert "mal" in out
    assert "st stop" in out and "st new" in out, "named the fault but not the fix"


def test_crew_is_SILENT_when_the_live_process_matches_the_card(tmp_path, monkeypatch, capsys):
    """The discrimination control. Without it the warning above could be
    unconditional and the test could not tell the two worlds apart."""
    out = _crew_out(tmp_path, monkeypatch, capsys, "lead")
    assert "MATCH THEIR ROLE" not in out, (
        "warned about a lead whose live process DOES carry drain — false positive")


# --- ALIVE ELSEWHERE is not DOWN (aegis-k9068) --------------------------------


class _SessPanes:
    """Panes that knows which sessions exist, so `st crew` can tell a dead agent
    from one running in another orchestrator's namespace."""

    def __init__(self, live, sessions):
        self._live, self._sessions = set(live), sessions

    def exists(self, pane):
        return pane in self._live

    def sessions(self):
        return self._sessions


def test_an_agent_alive_under_another_namespace_is_not_reported_as_plain_down():
    """The 2h gap. A crew member launched by another orchestrator lands in that
    orchestrator's session namespace, so the pane st manages is absent and the
    roster rendered it IDENTICALLY to a crashed agent — while it was alive,
    taking no dispatches, and holding tier-1 alert cover."""
    from shantytown.cli import _alive_elsewhere_note
    from shantytown.protocols import Agent

    agents = [Agent(name="muldoon", pane="shanty-muldoon")]
    panes = _SessPanes(live=[], sessions=["aegis-crew-muldoon", "shanty-ellie"])

    note = _alive_elsewhere_note(agents, panes)
    assert "ALIVE ELSEWHERE" in note
    assert "muldoon" in note and "aegis-crew-muldoon" in note
    assert "st new" in note, "said the problem without the recovery"


def test_a_genuinely_dead_agent_says_nothing():
    """The note must not fire for an agent that is simply down, or it becomes
    noise on every ordinary stop and stops being read."""
    from shantytown.cli import _alive_elsewhere_note
    from shantytown.protocols import Agent

    agents = [Agent(name="muldoon", pane="shanty-muldoon")]
    panes = _SessPanes(live=[], sessions=["shanty-ellie", "shanty-arnold"])
    assert _alive_elsewhere_note(agents, panes) == ""


def test_cannot_enumerate_sessions_says_nothing_rather_than_accusing():
    """`sessions()` returns None when it could not look. A roster that cannot
    enumerate must not report every absent pane as alive-elsewhere: "I could not
    look" is not "they are running somewhere". Same fail-silent discipline as the
    throttle note beside it."""
    from shantytown.cli import _alive_elsewhere_note
    from shantytown.protocols import Agent

    agents = [Agent(name="muldoon", pane="shanty-muldoon")]
    assert _alive_elsewhere_note(agents, _SessPanes(live=[], sessions=None)) == ""


def test_an_UP_agent_is_never_flagged_even_if_its_name_appears_elsewhere():
    """The pane st manages is present, so there is nothing to warn about — a
    same-named session elsewhere is somebody else's business."""
    from shantytown.cli import _alive_elsewhere_note
    from shantytown.protocols import Agent

    agents = [Agent(name="muldoon", pane="shanty-muldoon")]
    panes = _SessPanes(live=["shanty-muldoon"],
                       sessions=["shanty-muldoon", "aegis-crew-muldoon"])
    assert _alive_elsewhere_note(agents, panes) == ""
