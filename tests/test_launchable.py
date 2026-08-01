"""Can this agent actually DO anything?

The incident: three cards carried no `dangerous`, so their agents launched in
MANUAL MODE — a human keystroke to approve every bash call. They read `up`,
`current` and `busy` on every surface we had while being unable to advance a
single command unattended. One agent died twice, the gaming lane stalled
repeatedly, and it was found only by capturing pane footers by hand.

Two halves, and they must not be conflated: what the PANE shows (the running
truth) and what the CARD lacks (what a supervisor will re-arm). These tests pin
both, and pin the boundary between them — the card is never allowed to stand in
for an observation.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import cli, launchable
from shantytown.protocols import Agent
from shantytown.tmux import NullPanes


# Read verbatim off live crew panes, 2026-08-01.
BYPASS_FOOTER = ("⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents")
BYPASS_BUSY = ("✻ Envisioning… (12s · 4.1k tokens · esc to interrupt)\n"
               "⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt")
# Claude Code's default footer: no mode line, so permission prompts are ON.
MANUAL_FOOTER = "> \n  ? for shortcuts"


# --- the pane read ----------------------------------------------------------

def test_the_bypass_line_reads_as_bypass_idle_and_in_flight():
    """It must be readable on ANY capture, not only a quiet one — the whole point
    is that a coordinator sees posture without waiting for the agent to go idle.
    The mode line survives a turn in flight (measured)."""
    assert launchable.observed_posture(BYPASS_FOOTER, ui_up=True) == launchable.BYPASS
    assert launchable.observed_posture(BYPASS_BUSY, ui_up=True) == launchable.BYPASS


def test_a_live_pane_with_no_bypass_line_is_manual():
    """DERIVED NEGATIVELY. Only the bypass footer has been measured; pinning a
    string for 'manual mode on' would be a marker never observed passing, and this
    repo has already paid for two of those. Absence of bypass is the signal, and
    it stays correct for every other mode the runtime may grow."""
    assert launchable.observed_posture(MANUAL_FOOTER, ui_up=True) == launchable.MANUAL


def test_a_pane_with_no_ready_ui_is_unknown_not_manual():
    """A trust dialog, a consent screen or a blocking picker has no mode line at
    all. Calling that MANUAL is a fabricated measurement aimed at the wrong
    problem — and it would put an agent on the manual list for a defect it does
    not have, next to agents that do."""
    assert launchable.observed_posture("", ui_up=False) == launchable.UNKNOWN
    assert launchable.observed_posture(BYPASS_FOOTER, ui_up=False) == launchable.UNKNOWN


def test_an_agent_talking_about_bypass_is_not_in_bypass():
    """Tail-only, same rule as every other predicate in this repo. The marker is
    ordinary English — the module that defines it contains it twice — so a pane
    where an agent DISCUSSED the mode must not read as being in it."""
    screen = ("I set bypass permissions on for ellie and relaunched her\n"
              + "\n" * 12 + MANUAL_FOOTER)
    assert launchable.observed_posture(screen, ui_up=True) == launchable.MANUAL


# --- the card read ----------------------------------------------------------

def test_a_card_with_both_fields_has_no_gaps(tmp_path):
    card = Agent(name="arnold", role="worker", workspace=str(tmp_path),
                 dangerous=True)
    assert launchable.launch_gaps(card) == []


def test_the_two_faults_that_decide_whether_an_agent_can_work(tmp_path):
    """Both hit the same three cards, and neither was reported by anything: no
    workspace and no `dangerous`. They arrived a day
    apart as separate incidents; they are one question, because `retired = true`
    conceals ANY launch fault and they surface together on re-arming."""
    gaps = launchable.launch_gaps(Agent(name="goldblum", role="worker"))
    assert [g.short for g in gaps] == ["no workspace", "MANUAL MODE"]
    # Each is independently detected — a card can have one and not the other.
    assert [g.short for g in launchable.launch_gaps(
        Agent(name="x", role="worker", workspace=str(tmp_path)))] == ["MANUAL MODE"]
    assert [g.short for g in launchable.launch_gaps(
        Agent(name="x", role="worker", dangerous=True))] == ["no workspace"]


def test_only_the_workspace_fault_may_refuse_a_command():
    """The considered asymmetry. A missing workspace is never ELECTED on the
    supervisor path — nobody chose the cwd systemd handed it — so it blocks.
    `dangerous` is opt-in by design in this harness, and an attended agent that
    wants a prompt per call is making a real choice; a gate that refused it would
    override an election the harness deliberately offers. The defect was never
    that manual mode is impossible to want, it was that choosing it by accident
    was impossible to see."""
    gaps = {g.short: g for g in
            launchable.launch_gaps(Agent(name="goldblum", role="worker"))}
    assert gaps["no workspace"].blocking is True
    assert gaps["MANUAL MODE"].blocking is False


def test_every_gap_says_it_twice_short_and_long():
    """A roster line has room for two words; a refusal has room for a paragraph.
    Rendering the paragraph on the roster buries it and rendering the label at
    the refusal leaves the operator guessing — so the RULE is decided once and
    the LENGTH is the caller's, which is how `st crew` and `st tend --unretire`
    are guaranteed to be talking about the same card."""
    for gap in launchable.launch_gaps(Agent(name="goldblum", role="worker")):
        assert len(gap.short) <= 16          # fits a column
        assert len(gap.why) > 80             # says the cost and the fix
        assert "goldblum" in gap.why         # and which card it is about


def test_a_well_formed_card_can_still_be_unlaunchable():
    """The distinction this module exists for. Every field parses, the card is
    valid, `st crew` renders it — and the agent it describes cannot run a
    command. Validity was never the question."""
    card = Agent(name="ian", role="worker", pane="crew-ian",
                 reports_to="dearing", roles=("worker", "graph"))
    assert card.name and card.pane          # nothing is malformed
    assert launchable.launch_gaps(card)      # and it still cannot work


# --- st tend --unretire -----------------------------------------------------

class _RetireArgs:
    def __init__(self, root, unretire=None, retire=None, force=False):
        self.root = Path(root)
        self.backend = "files"; self.repo = None; self.registry = "files"
        self.retire, self.unretire, self.force = retire, unretire, force
        self.dry_run = False


def _card(tmp_path: Path, name: str, **fields) -> Path:
    crew = tmp_path / "crew"; crew.mkdir(exist_ok=True)
    (crew / f"{name}.json").write_text(
        json.dumps({"role": "worker", "pane": f"p-{name}", **fields}))
    return tmp_path


def test_unretire_says_manual_mode_out_loud_without_refusing(tmp_path, capsys):
    """THE new behaviour on this bead. The card is launchable — it has a tree —
    but it would come up needing a keystroke per bash call, and un-retiring is
    the last moment a person is looking at it. Said, and not blocked: the
    election is legitimate, its invisibility was not."""
    root = _card(tmp_path, "ian", retired=True,
                 workspace=str(tmp_path))            # tree yes, dangerous no
    assert cli._tend_retire(_RetireArgs(root, unretire="ian")) == cli.OK
    out = capsys.readouterr().out
    assert "MANUAL MODE" in out and "approve EVERY bash call" in out
    # It proceeded — the card really is re-armed, not quietly left retired.
    assert json.loads((root / "crew" / "ian.json").read_text())["retired"] is False


def test_unretire_still_refuses_the_workspace_fault(tmp_path, capsys):
    """The blocking half stays blocking, and the manual-mode
    warning does not soften it: a card with both faults is refused, and the
    refusal names the one that blocks."""
    root = _card(tmp_path, "goldblum", retired=True)
    assert cli._tend_retire(_RetireArgs(root, unretire="goldblum")) == cli.REFUSED
    err = capsys.readouterr().err
    assert "NO workspace" in err
    # AND IT DID NOT WRITE. A refusal that still mutated would be the worst of
    # both: the operator is told no and the card is re-armed anyway.
    assert json.loads((root / "crew" / "goldblum.json").read_text())["retired"] is True


def test_a_fully_healthy_card_is_warned_about_at_all(tmp_path, capsys):
    """The negative control for the warning. A gate that fires on everything is
    not a gate — an agent with a tree and `dangerous` must pass in silence, or
    the loud line above stops meaning anything."""
    root = _card(tmp_path, "ellie", retired=True,
                 workspace=str(tmp_path), dangerous=True)
    assert cli._tend_retire(_RetireArgs(root, unretire="ellie")) == cli.OK
    assert "MANUAL MODE" not in capsys.readouterr().out
    assert json.loads((root / "crew" / "ellie.json").read_text())["retired"] is False


def test_retiring_is_never_gated_and_never_warned(tmp_path, capsys):
    """Only UN-retiring is a launch decision. Stopping an agent that cannot work
    is exactly what an operator should be able to do without an argument — a gate
    on the safe direction would be a mechanism that resists being made safer."""
    root = _card(tmp_path, "goldblum")
    assert cli._tend_retire(_RetireArgs(root, retire="goldblum")) == cli.OK
    assert "MANUAL MODE" not in capsys.readouterr().out
    assert json.loads((root / "crew" / "goldblum.json").read_text())["retired"] is True


# --- st crew ----------------------------------------------------------------

class _Panes(NullPanes):
    def __init__(self, screens: dict):
        super().__init__(live=set(screens))
        self._screens = screens

    def capture(self, pane: str, history: int = 0, attrs: bool = False) -> str:
        return self._screens.get(pane, "")


class _CrewArgs:
    def __init__(self, root):
        self.root = Path(root)
        self.backend = "files"; self.repo = None; self.registry = "files"


def test_crew_names_the_agents_running_in_manual_mode(tmp_path, monkeypatch, capsys):
    """The roster read that did not exist. ellie is in manual mode and ian is
    not, and before this the two were indistinguishable on every surface — both
    `up`, both `busy`, only one of them able to run a command."""
    crew = tmp_path / "crew"; crew.mkdir()
    for name in ("ellie", "ian"):
        (crew / f"{name}.json").write_text(json.dumps(
            {"role": "worker", "pane": f"p-{name}",
             "workspace": str(tmp_path), "dangerous": True}))
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(
        {"p-ellie": MANUAL_FOOTER, "p-ian": BYPASS_BUSY}))

    assert cli._cmd_crew(_CrewArgs(tmp_path)) == cli.OK
    out = capsys.readouterr().out
    assert "MANUAL MODE" in out and "1 agent(s) in MANUAL MODE" in out
    manual_line = next(l for l in out.splitlines() if "MANUAL MODE" in l)
    assert "ellie" in manual_line and "ian" not in manual_line
    # And the remedy is the one that actually works: the mode is read at LAUNCH,
    # so editing the card without a relaunch changes nothing. Saying only "set
    # dangerous" would send the operator to do a no-op and believe it took.
    assert "RELAUNCH" in out


def test_crew_reports_posture_from_the_pane_not_the_card(tmp_path, monkeypatch, capsys):
    """THE distinction the fix on this bead was verified by. The card says
    dangerous=True; the running agent is in manual mode, because the card was
    edited after launch. Reporting the card here would report the fix as landed
    when nothing had changed in the process — which is precisely the mistake the
    settings column exists to prevent, one field over."""
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps(
        {"role": "worker", "pane": "p-ellie", "workspace": str(tmp_path),
         "dangerous": True}))
    monkeypatch.setattr(cli, "Tmux",
                        lambda *_a, **_k: _Panes({"p-ellie": MANUAL_FOOTER}))

    assert cli._cmd_crew(_CrewArgs(tmp_path)) == cli.OK
    out = capsys.readouterr().out
    assert "1 agent(s) in MANUAL MODE" in out          # the pane won
    assert "ellie" in next(l for l in out.splitlines() if "MANUAL MODE" in l)


def test_crew_flags_a_card_that_would_be_re_armed_badly(tmp_path, monkeypatch, capsys):
    """The dormant half. goldblum is down and retired, so nothing is stalling —
    but its card is what `st tend --unretire` re-arms, and re-arming it
    manufactures the incident again. It stayed invisible for exactly this reason:
    a retired card is never launched, so its defect never becomes a symptom."""
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "goldblum.json").write_text(json.dumps(
        {"role": "worker", "pane": "p-goldblum", "retired": True}))
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes({}))

    assert cli._cmd_crew(_CrewArgs(tmp_path)) == cli.OK
    out = capsys.readouterr().out
    assert "CANNOT WORK" in out and "goldblum" in out
    assert "no workspace" in out and "MANUAL MODE" in out


def test_a_down_agent_gets_no_posture_verdict(tmp_path, monkeypatch, capsys):
    """`—`, not MANUAL. The card may well lack `dangerous`, but that is what it
    WILL launch with, not what anything is running — and this column only ever
    reports what was observed. The card's gap is said in its own block, in the
    language of a card."""
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ian.json").write_text(json.dumps({"role": "worker", "pane": "p-gone"}))
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes({}))

    assert cli._cmd_crew(_CrewArgs(tmp_path)) == cli.OK
    out = capsys.readouterr().out
    row = next(l for l in out.splitlines() if l.strip().startswith("ian"))
    assert "MANUAL" not in row
    assert "0 agent(s) in MANUAL MODE" not in out    # no manual block at all
