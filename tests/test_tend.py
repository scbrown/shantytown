"""st tend — supervision, and every branch that made it necessary.

The acceptance list on this bead is a list of BUGS SOMEONE PAID FOR, so each
test below is named for the failure it prevents rather than the function it
calls. The one that must never go quiet: a retired agent is not respawned, and
finding one alive is an escalation.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import cli, supervisor, tend as tend_mod
from shantytown.protocols import Agent
from shantytown.tmux import NullPanes
from shantytown.workspace import WorkspaceError


IDLE = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
BUSY = "✻ Envisioning… (12s · 4.1k tokens · esc to interrupt)"

# A launch line carrying BOTH stop directions — what a wired worker looks like.
def _wired(settings: Path) -> str:
    return f"claude --settings {settings}"


@pytest.fixture
def settings(tmp_path) -> Path:
    p = tmp_path / "worker.settings.json"
    p.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "python -m shantytown.stop_event send"}]}]}}))
    return p


class _Panes(NullPanes):
    """Per-pane screens and cmdlines — a roster needs both, and `cmdline` is what
    separates "the pane is up" from "the agent can report"."""

    def __init__(self, screens=None, cmdlines=None, live=None, created=None):
        super().__init__(live=set(live if live is not None else (screens or {})),
                         cmdlines=cmdlines, created=created)
        self._screens = screens or {}

    def capture(self, pane, history=0, **kw):
        return self._screens.get(pane, IDLE)


class _Runtime:
    name = "fake"

    def __init__(self):
        self.started = []

    def shows_ready_ui(self, screen):
        return "shift+tab to cycle" in screen or "? for shortcuts" in screen

    def start(self, card, pane):
        self.started.append((card.name, pane))


class _Launches:
    def __init__(self, verdicts=None):
        self._v = verdicts or {}

    def verdict(self, name):
        return self._v.get(name, "current")


def _tender(panes, runtime, *, launches=None, spawn=None, refresh=None,
            ensure=lambda card: card.workspace, log=None, stops=None):
    return tend_mod.Tender(panes, runtime, launches or _Launches(),
                           spawn=spawn or runtime.start, refresh=refresh,
                           ensure=ensure, log=log or (lambda m: None),
                           stops=stops)


# --- the bug this exists for: died vs RETIRED --------------------------------

def test_a_retired_agent_is_never_respawned(settings):
    """The watchdog that motivated this reverted a deliberate shutdown of eight
    agents inside a minute. Retirement is read BEFORE anything can decide to
    act — ordering is the guarantee, not politeness."""
    card = Agent(name="ellie", pane="p-ellie", retired=True)
    rt = _Runtime()
    rep = _tender(_Panes(live=set()), rt).pass_over([card])

    assert [f.verdict for f in rep.findings] == [tend_mod.RETIRED]
    assert rt.started == [], "respawned a deliberately retired agent"
    assert rep.acted == []
    assert rep.healthy(), "a retired agent is not a fault"
    assert "NOT a fault" in rep.findings[0].why


def test_a_retired_agent_found_ALIVE_escalates(settings):
    """A session born after retirement really was resurrected."""
    card = Agent(name="ellie", pane="p-ellie", retired=True,
                 retired_at="2026-08-02T03:28:51+00:00")
    said = []
    rep = _tender(_Panes({"p-ellie": IDLE}, created={"p-ellie": 1785642000}), _Runtime(),
                  log=said.append).pass_over([card])

    assert rep.findings[0].verdict == tend_mod.RESURRECTED
    assert not rep.healthy(), "a resurrected retiree must not exit clean"
    assert any("ESCALATE" in m for m in said), "acted invisibly"


def test_a_session_older_than_its_retirement_is_a_SURVIVOR_not_a_fault(settings):
    """Measured zia case: session 03:05, retirement 03:28. It cannot have
    respawned after a decision that had not happened yet."""
    card = Agent(name="zia", pane="p-zia", retired=True,
                 retired_by="sattler",
                 retired_at="2026-08-02T03:28:51+00:00")
    born = 1785639947  # 2026-08-02T03:05:47Z, the measured session birth
    said = []
    rep = _tender(_Panes({"p-zia": IDLE}, created={"p-zia": born}), _Runtime(),
                  log=said.append).pass_over([card])

    f, = rep.findings
    assert f.verdict == tend_mod.SURVIVOR
    assert rep.healthy()
    assert "SURVIVED" in f.why and "session_created" in f.why
    assert any("SURVIVOR" in m for m in said)
    assert not any("ESCALATE" in m for m in said)


def test_unreadable_session_birth_stays_RESURRECTED(settings):
    """Cannot-ask is not permission to invent the reassuring ordering."""
    card = Agent(name="ellie", pane="p-ellie", retired=True,
                 retired_at="2026-08-02T03:28:51+00:00")
    rep = _tender(_Panes({"p-ellie": IDLE}), _Runtime()).pass_over([card])
    assert rep.findings[0].verdict == tend_mod.RESURRECTED
    assert not rep.healthy()


def test_retirement_survives_a_restart_because_it_lives_on_the_card(tmp_path):
    """Durability by construction: nothing about the retirement is held in the
    supervisor, so nothing about restarting the supervisor can undo it."""
    from shantytown.files import FilesRegistry
    reg = FilesRegistry(tmp_path / "crew")
    reg.set(Agent(name="ellie", pane="p-ellie", role="worker", retired=True))
    assert FilesRegistry(tmp_path / "crew").get("ellie").retired is True
    reg.set(Agent(name="ellie", pane="p-ellie", role="worker", retired=False))
    assert FilesRegistry(tmp_path / "crew").get("ellie").retired is False, \
        "un-retiring must be expressible — a one-way door is not a switch"


# --- respawn: exactly the dead one, loudly -----------------------------------

def test_a_dead_agent_is_respawned_and_the_others_are_untouched(settings):
    dead = Agent(name="ellie", pane="p-ellie", workspace="/ws/ellie")
    alive = Agent(name="ian", pane="p-ian", workspace="/ws/ian")
    panes = _Panes({"p-ian": IDLE}, cmdlines={"p-ian": _wired(settings)})
    rt = _Runtime()
    said = []
    rep = _tender(panes, rt, log=said.append).pass_over([dead, alive])

    assert rt.started == [("ellie", "p-ellie")], "respawned the wrong set"
    by = {f.agent: f for f in rep.findings}
    assert by["ellie"].verdict == tend_mod.RESPAWNED and by["ellie"].acted
    assert by["ian"].verdict == tend_mod.OK and not by["ian"].acted
    assert any("RESPAWNED ellie" in m for m in said), "a silent respawn IS the bug"


def test_dry_run_names_what_it_would_do_and_mutates_nothing(settings):
    dead = Agent(name="ellie", pane="p-ellie", workspace="/ws/ellie")
    panes = _Panes(live=set())
    rt = _Runtime()
    ensured = []
    rep = _tender(panes, rt, ensure=lambda c: ensured.append(c) or c.workspace
                  ).pass_over([dead], dry_run=True)

    assert rep.findings[0].verdict == tend_mod.WOULD
    assert "p-ellie" in rep.findings[0].why, "did not name what it would do"
    assert rt.started == [] and ensured == [] and panes.exists("p-ellie") is False
    assert rep.acted == []


def test_the_workspace_is_ensured_before_the_launch(settings):
    """A respawn that skips it launches an agent into a directory that may not
    exist, and the break surfaces inside a session that already came up."""
    card = Agent(name="ellie", pane="p-ellie", workspace="/ws/ellie")
    order = []
    rt = _Runtime()
    tender = tend_mod.Tender(
        _Panes(live=set()), rt, _Launches(),
        spawn=lambda c, p: order.append("launch"),
        ensure=lambda c: order.append("ensure") or c.workspace, log=lambda m: None)
    tender.pass_over([card])
    assert order == ["ensure", "launch"]


def test_a_STOPPED_agent_is_refused_because_st_stop_forgot_its_stamp(tmp_path, settings):
    """aegis-k9068. `st stop` prints "`st tend` will still respawn it" — and then
    deletes the launch stamp that tend gates on, which makes its own promise false.

    The bead diagnosed this as gt-launched agents never having had a stamp. The
    mechanism is wider: `st stop` calls `_launches.forget()` on EVERY stop
    (correctly — a stamp left behind would describe a process that no longer
    exists), and tend refuses any agent without one while ANY other agent has one.
    On a live fleet that second condition is always true, so the promise is false
    for every deliberately stopped agent, not only the gt-launched pair.

    Uses the REAL store rather than the module's `_Launches` stub: the stub has no
    `get`/`root`, so the gate's fail-open `except` swallows it and the gate is
    never exercised — which is why no existing test caught this.

    NOT RESPAWNING IT IS CORRECT and unchanged — a deliberate stop must stay down
    until asked back, which is what `st stop` now promises. What was wrong was the
    REASON: "never launched by st … another orchestrator owns it" is false in every
    clause here (st launched it, st removed the stamp) and sends an operator
    hunting an orchestrator that does not exist. So this asserts the verdict is
    STOPPED with the true cause and the remedy, and its partner below asserts the
    aegis-2j2r wording still fires for a genuinely foreign card. Neither test is
    meaningful alone: the defect was ONE message serving TWO populations, so only
    the pair can show they are told apart.
    """
    from shantytown.launched import FilesLaunches
    from shantytown.stopped import FilesStops

    launches = FilesLaunches(tmp_path / "launched")
    stops = FilesStops(tmp_path / "stopped")
    stamp_src = tmp_path / "settings.json"
    stamp_src.write_text("{}")
    # Two agents launched by st. Both stamped, as a real launch would.
    launches.record("ellie", stamp_src)
    launches.record("weaver", stamp_src)
    assert launches.get("ellie") is not None

    # EXACTLY what `st stop ellie` does after killing the session.
    launches.forget("ellie")
    stops.record("ellie", 1754150000.0, by="sattler", reason="governor dam")

    card = Agent(name="ellie", pane="p-ellie")
    rt = _Runtime()
    said = []
    rep = _tender(_Panes(live=set()), rt, launches=launches, stops=stops,
                  log=said.append).pass_over([card])

    assert rep.findings[0].verdict == tend_mod.STOPPED, (
        "a deliberate stop read as a generic REFUSED — that is the misdiagnosis"
    )
    why = rep.findings[0].why
    assert "deliberately stopped" in why and "sattler" in why
    assert "st new ellie" in why, "refused without naming the one command that fixes it"
    assert "another orchestrator" not in why, (
        "still blaming a foreign orchestrator for st's own stop"
    )
    assert rt.started == [], "respawned an agent it had just refused"
    assert any("STOPPED" in m for m in said)
    # A DECISION IS NOT A FAULT — same rule RETIRED/GOVERNED/BELOW_TARGET follow.
    # Five stood-down agents made `st tend` report "5 fault(s)" and exit non-zero,
    # while `st crew` called the same five "stopped ON PURPOSE" off the same record.
    assert rep.faults == [], "a deliberate stop counted as a fault"
    assert rep.healthy


def test_a_FOREIGN_unstamped_card_still_gets_the_orchestrator_wording(tmp_path, settings):
    """The negative control for the test above (aegis-k9068 / aegis-2j2r).

    Without this the fix could have been "call every unstamped agent deliberately
    stopped", which trades one wrong message for another and re-opens the trap
    aegis-2j2r closed: st resurrecting another orchestrator's cards into this
    deployment's settings. Same input as its partner — unstamped while other
    stamps exist — differing ONLY in whether a stop record exists.
    """
    from shantytown.launched import FilesLaunches
    from shantytown.stopped import FilesStops

    launches = FilesLaunches(tmp_path / "launched")
    stops = FilesStops(tmp_path / "stopped")
    stamp_src = tmp_path / "settings.json"
    stamp_src.write_text("{}")
    launches.record("weaver", stamp_src)          # somebody else's stamp exists
    # 'ghost' was never launched by st and was never stopped by st: no record.
    assert stops.get("ghost") is None

    rt = _Runtime()
    said = []
    rep = _tender(_Panes(live=set()), rt, launches=launches, stops=stops,
                  log=said.append).pass_over([Agent(name="ghost", pane="p-ghost")])

    assert rep.findings[0].verdict == tend_mod.REFUSED
    assert "another orchestrator owns it" in rep.findings[0].why
    assert rt.started == [], "respawned a foreign orchestrator's card"
    assert any("REFUSED" in m for m in said)


def test_a_missing_workspace_REFUSES_instead_of_launching(settings):
    card = Agent(name="ellie", pane="p-ellie", workspace="/ws/gone")

    def boom(c):
        raise WorkspaceError("workspace does not exist: /ws/gone")

    rt = _Runtime()
    said = []
    rep = _tender(_Panes(live=set()), rt, ensure=boom, log=said.append
                  ).pass_over([card])
    assert rep.findings[0].verdict == tend_mod.REFUSED
    assert rt.started == [], "launched into a missing workspace"
    assert not rep.healthy(), "a refusal must not exit clean"
    assert any("REFUSED" in m for m in said)


def test_a_failed_clone_refresh_is_LOUD_but_does_not_block_the_respawn():
    """Refusing to start an agent over a network blip trades a stale directive
    for an outage. Loud, not blocking — and the test asserts both halves."""
    card = Agent(name="ellie", pane="p-ellie", workspace="/ws/ellie")
    rt = _Runtime()
    said = []
    rep = _tender(_Panes(live=set()), rt, log=said.append,
                  refresh=lambda p: "fatal: could not read from remote"
                  ).pass_over([card])
    assert rt.started == [("ellie", "p-ellie")], "a pull failure blocked a respawn"
    assert rep.findings[0].verdict == tend_mod.RESPAWNED
    assert any("refresh failed" in m for m in said), "swallowed the pull failure"


def test_it_refuses_a_pane_that_appeared_and_is_BUSY(settings):
    """The race: the agent was down when we looked and is working now. triage
    owns that verdict; this must not write a second opinion."""
    card = Agent(name="ellie", pane="p-ellie")
    panes = _Panes({"p-ellie": BUSY}, live=set())
    rt = _Runtime()

    # it appears between the look and the launch
    def ensure(c):
        panes._live.add("p-ellie")
        return c.workspace

    rep = _tender(panes, rt, ensure=ensure).pass_over([card])
    assert rep.findings[0].verdict == tend_mod.BUSY
    assert rt.started == [], "typed into a working agent"


# --- liveness is not drain ---------------------------------------------------

def test_alive_but_cannot_report_is_REPORTED_not_passed(tmp_path):
    """Eight agents were alive and carried no stop-event wiring: green, and
    deaf. A pass that cannot fail on this is not a pass."""
    # A FOREIGN launcher's settings: real hooks (this is what the eight live
    # agents carried — including their own guards), and no `stop_event`
    # direction anywhere in them. Not an empty file: the point is that it looks
    # healthy and cannot report.
    foreign = tmp_path / "foreign.settings.json"
    foreign.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "gt hook stop"}]}]}}))
    card = Agent(name="ellie", pane="p-ellie", reports_to="lead")
    panes = _Panes({"p-ellie": IDLE}, cmdlines={"p-ellie": _wired(foreign)})

    rep = _tender(panes, _Runtime()).pass_over([card])
    f = rep.findings[0]
    assert f.verdict == tend_mod.DEAF
    assert "green and dead" in f.why
    assert not rep.healthy(), "a deaf agent exited clean"


def test_an_unreadable_process_is_CANNOT_TELL_not_a_pass():
    card = Agent(name="ellie", pane="p-ellie", reports_to="lead")
    panes = _Panes({"p-ellie": IDLE})            # cmdlines=None -> cannot read
    rep = _tender(panes, _Runtime()).pass_over([card])
    assert rep.findings[0].verdict == tend_mod.DEAF
    assert "CANNOT TELL" in rep.findings[0].why
    assert not rep.healthy()


def test_stale_settings_are_reported_and_NOT_auto_cycled(settings):
    """Killing a mid-flight agent to fix stale hooks is worse than the stale
    hooks. Report it; propose the rule; do not guess."""
    card = Agent(name="ellie", pane="p-ellie", reports_to=None)
    panes = _Panes({"p-ellie": IDLE}, cmdlines={"p-ellie": _wired(settings)})
    rt = _Runtime()
    rep = _tender(panes, rt, launches=_Launches({"ellie": "STALE"})).pass_over([card])

    assert rep.findings[0].verdict == tend_mod.STALE
    assert rt.started == [], "cycled a live agent on its own authority"
    assert rep.healthy(), "stale is a candidate, not a fault"


def test_a_card_with_no_pane_is_untendable_not_dead():
    rep = _tender(_Panes(live=set()), _Runtime()).pass_over([Agent(name="ellie")])
    assert rep.findings[0].verdict == tend_mod.UNTENDABLE


# --- the healer's own health signal ------------------------------------------

def test_the_pass_log_makes_an_ABSENT_supervisor_detectable(tmp_path):
    log = supervisor.PassLog(tmp_path)
    assert log.last() is None and log.age_seconds() is None, \
        "never-ran must not read as fine"

    rep = tend_mod.Report(started=1000.0)
    rep.findings.append(tend_mod.Finding("ellie", "down", tend_mod.RESPAWNED,
                                         "was down", acted=True))
    log.record(rep)
    assert log.last()["acted"] == ["ellie"]
    assert log.age_seconds(now=1600.0) == 600.0


# --- install: idempotent, reversible, and refuses a collision ----------------

# ABSOLUTE on purpose. These tests used to pass the bare name "st", which is
# exactly the bug aegis-408qs: systemd --user never resolves it, so every unit
# they "proved" would 203/EXEC on every fire. A test suite that hands its
# subject the broken input cannot fail on the breakage.
ST_BIN = "/usr/bin/st"


@pytest.fixture
def unit_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path / ".config" / "systemd" / "user"


def test_install_is_idempotent(unit_home, tmp_path):
    ran = []
    changed, msg = supervisor.install(ST_BIN, tmp_path / "root", run=ran.append)
    assert changed and (unit_home / supervisor.TIMER).exists()

    changed2, msg2 = supervisor.install(ST_BIN, tmp_path / "root", run=ran.append)
    assert not changed2, "a second install stacked units"
    assert "already installed" in msg2


def test_uninstall_removes_everything_it_wrote(unit_home, tmp_path):
    supervisor.install(ST_BIN, tmp_path / "root", run=lambda c: None)
    changed, _ = supervisor.uninstall(run=lambda c: None)
    assert changed
    assert not (unit_home / supervisor.TIMER).exists()
    assert not (unit_home / supervisor.SERVICE).exists()
    again, msg = supervisor.uninstall(run=lambda c: None)
    assert not again and "not installed" in msg


def test_install_REFUSES_a_second_supervisor_for_the_same_crew(unit_home, tmp_path):
    """Two things respawning the same agents fight, and the fight looks like
    flapping nobody can attribute. Refuse — and do NOT switch the other off."""
    changed, msg = supervisor.install(
        ST_BIN, tmp_path / "root", run=lambda c: None,
        is_active=lambda unit: unit == "gastown-crew-watchdog.timer")
    assert not changed
    assert "REFUSED" in msg and "gastown-crew-watchdog.timer" in msg
    assert not (unit_home / supervisor.TIMER).exists(), "wrote units anyway"


def test_install_REFUSES_a_boot_time_competitor_that_is_not_running_yet(unit_home, tmp_path):
    """THE aegis-np4x1 REGRESSION, and the reason listing the name was not enough.

    gastown-crew.service is a boot-time oneshot. On a host that is up it is
    INACTIVE — so the is-active check saw nothing, --install proceeded, and the
    next boot brought up a second fleet under the retired aegis-crew-* names.
    Three agents got a twin on one workspace. Nothing errored anywhere.
    """
    changed, msg = supervisor.install(
        ST_BIN, tmp_path / "root", run=lambda c: None,
        is_active=lambda unit: False,                       # nothing running NOW
        is_enabled=lambda unit: unit == "gastown-crew.service")
    assert not changed, "installed beside a competitor armed for the next boot"
    assert "REFUSED" in msg and "gastown-crew.service" in msg
    assert "next boot" in msg, "refused without saying the trap is a REBOOT away"
    assert not (unit_home / supervisor.TIMER).exists(), "wrote units anyway"


def test_the_boot_time_autostart_is_actually_in_the_list(unit_home, tmp_path):
    """A predicate that can see enabled units is worth nothing if the unit that
    motivated it never got named. Both halves of the fix, or neither."""
    assert "gastown-crew.service" in supervisor.FOREIGN_UNITS


def test_install_proceeds_when_the_competitor_is_disabled(unit_home, tmp_path):
    """The other direction: over-refusing is not free either. A unit that is
    neither running nor armed is retired, and must not block anything."""
    changed, msg = supervisor.install(
        ST_BIN, tmp_path / "root", run=lambda c: None,
        is_active=lambda unit: False, is_enabled=lambda unit: False)
    assert changed, f"refused with no competitor at all: {msg}"


def test_masked_and_static_units_do_not_read_as_ARMED(monkeypatch):
    """`systemctl is-enabled` exits NON-zero for a masked unit and ZERO for a
    static one, so an exit-code reading is wrong in BOTH directions. The word is
    the answer. Our own gastown-crew.service is masked — if `masked` read as
    armed, tend would refuse forever on the unit we deliberately retired."""
    import subprocess as _sp

    class _R:
        def __init__(self, out, rc):
            self.stdout, self.returncode, self.stderr = out, rc, ""

    # (printed state, exit code) -> is it a live competitor?
    cases = {"enabled": (0, True), "enabled-runtime": (0, True),
             "masked": (1, False), "disabled": (1, False),
             "static": (0, False), "indirect": (0, False),
             "not-found": (4, False)}
    for state, (rc, want) in cases.items():
        monkeypatch.setattr(_sp, "run",
                            lambda *a, _s=state, _c=rc, **k: _R(_s + "\n", _c))
        got = cli._systemctl_user_enabled("gastown-crew.service")
        assert got is want, f"is-enabled={state!r} (exit {rc}) read as armed={got}"


def test_install_REFUSES_to_overwrite_a_unit_it_did_not_write(unit_home, tmp_path):
    unit_home.mkdir(parents=True)
    (unit_home / supervisor.TIMER).write_text("[Timer]\n# somebody else's\n")
    changed, msg = supervisor.install(ST_BIN, tmp_path / "root", run=lambda c: None)
    assert not changed and "REFUSED" in msg
    assert "somebody else's" in (unit_home / supervisor.TIMER).read_text()


def test_uninstall_REFUSES_a_unit_it_did_not_write(unit_home, tmp_path):
    unit_home.mkdir(parents=True)
    (unit_home / supervisor.TIMER).write_text("[Timer]\n# not ours\n")
    changed, msg = supervisor.uninstall(run=lambda c: None)
    assert not changed and "REFUSED" in msg
    assert (unit_home / supervisor.TIMER).exists()


# --- the ExecStart must be absolute (aegis-408qs) ----------------------------
#
# The failure this pins is not "the unit was wrong" — it is that the unit was
# wrong SILENTLY. systemd kept the timer healthy and the service died at exec
# 687 times over two days: no supervision pass, no governor pass, no Rule Zero
# alert, and nothing anywhere said so. The only defence is refusing to write
# the unit at all, at install time, while a human is still reading output.

def test_install_REFUSES_a_bare_command_name(unit_home, tmp_path):
    changed, msg = supervisor.install("st", tmp_path / "root", run=lambda c: None)
    assert not changed
    assert "REFUSED" in msg and "absolute" in msg
    assert not (unit_home / supervisor.SERVICE).exists(), (
        "wrote a unit that can never exec — this is the 203/EXEC bug")


def test_install_REFUSES_a_relative_path(unit_home, tmp_path):
    changed, msg = supervisor.install("./bin/st", tmp_path / "root", run=lambda c: None)
    assert not changed and "REFUSED" in msg
    assert not (unit_home / supervisor.SERVICE).exists()


def test_the_written_ExecStart_is_absolute(unit_home, tmp_path):
    supervisor.install(ST_BIN, tmp_path / "root", run=lambda c: None)
    line = [l for l in (unit_home / supervisor.SERVICE).read_text().splitlines()
            if l.startswith("ExecStart=")][0]
    argv0 = line.split("=", 1)[1].split()[0]
    assert argv0.startswith("/"), f"ExecStart is not absolute: {line!r}"
    assert argv0 == ST_BIN


def test_every_foreign_supervisor_call_site_passes_is_masked():
    """The masked-tombstone fix (0eb9d3f) landed on foreign_supervisor and on
    `--install`, and MISSED `--status` — which then went on reporting a masked
    unit as an active competitor on the surface operators actually read.

    is_masked defaults to "nothing is masked", and that default is the unsafe
    one: omitting it does not disable a check, it manufactures a false
    conflict. So the wiring is what needs pinning, not just the predicate.
    """
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "shantytown" / "cli.py"
    tree = ast.parse(src.read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "foreign_supervisor"]
    assert calls, "no foreign_supervisor call sites found — test is stale"
    for c in calls:
        passed = len(c.args) + len(c.keywords)
        assert passed >= 3, (
            f"foreign_supervisor call at cli.py:{c.lineno} passes {passed} "
            f"predicates; without is_masked a masked tombstone reads as a "
            f"live competitor")


# --- systemd's failure state must mean "did not run" (aegis-unbuw) -----------
#
# exit 2 is _cmd_tend's "found a FAULT", not "failed to run". Without
# SuccessExitStatus the unit sat in `failed` on every fault-finding pass, so
# `is-failed` was stuck on and could not report the thing it exists to report.
# That is the same shape as aegis-408qs, where 687 exec failures were invisible
# because the unit's health signal did not distinguish the two worlds.

def test_a_fault_finding_pass_is_not_a_systemd_failure(unit_home, tmp_path):
    supervisor.install(ST_BIN, tmp_path / "root", run=lambda c: None)
    body = (unit_home / supervisor.SERVICE).read_text()
    assert "SuccessExitStatus=2" in body, (
        "exit 2 means the pass RAN and found faults; without this systemd "
        "marks a healthy supervisor failed and the signal is unreadable")


def test_REFUSED_is_still_a_systemd_failure(unit_home, tmp_path):
    """1 must NOT be excused. A refusal is a pass that did not happen, which is
    exactly what systemd's failure state is for — widening this to 1 would give
    back the always-green unit that aegis-408qs hid behind."""
    supervisor.install(ST_BIN, tmp_path / "root", run=lambda c: None)
    line = [l for l in (unit_home / supervisor.SERVICE).read_text().splitlines()
            if l.startswith("SuccessExitStatus=")][0]
    excused = set(line.split("=", 1)[1].split())
    assert "1" not in excused and excused == {"2"}


def test_resolve_st_bin_prefers_PATH_and_returns_an_absolute_path():
    got = supervisor.resolve_st_bin(which=lambda n: "/opt/venv/bin/st")
    assert got == "/opt/venv/bin/st"


def test_resolve_st_bin_falls_back_to_argv0_when_not_on_PATH(tmp_path):
    real = tmp_path / "st"
    real.write_text("#!/bin/sh\n")
    got = supervisor.resolve_st_bin(which=lambda n: None, argv0=str(real))
    assert got == str(real)


def test_resolve_st_bin_returns_None_rather_than_a_name_it_cannot_verify():
    """None makes install() refuse. Returning "st" here would put the bug
    back one layer down, which is how it survived the first time."""
    assert supervisor.resolve_st_bin(which=lambda n: None, argv0="st") is None


def test_gastown_crew_service_is_a_foreign_supervisor():
    """It was NOT in this list, so the boot-time competing fleet it starts
    walked straight past the check it exists to trip (aegis-np4x1).

    (Asserted against the ACTIVE predicate, which is the case where the boot
    already happened and the competing fleet is up. The armed-but-not-yet-running
    case — the one that made this bead — is the test further up.)
    """
    assert supervisor.foreign_supervisor(
        lambda u: u == "gastown-crew.service") == ("gastown-crew.service",
                                                   "active now")


# --- the command ------------------------------------------------------------

class _Args:
    def __init__(self, root, **kw):
        self.root = Path(root)
        self.backend = None; self.repo = None; self.registry = "files"
        self.install = self.uninstall = self.status = False
        self.retire = self.unretire = None; self.force = False
        self.interval = "5min"; self.dry_run = False
        for k, v in kw.items():
            setattr(self, k, v)


def _roster(tmp_path, cards):
    crew = tmp_path / "crew"; crew.mkdir()
    for name, d in cards.items():
        (crew / f"{name}.json").write_text(json.dumps(d))
    return tmp_path


def test_cmd_tend_retire_then_a_pass_leaves_it_alone(tmp_path, monkeypatch, capsys):
    """The acceptance case end to end: retire it, and the next pass says why
    rather than bringing it back."""
    root = _roster(tmp_path, {"ellie": {"role": "worker", "pane": "p-ellie"}})
    panes = _Panes(live=set())
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)

    assert cli._cmd_tend(_Args(root, retire="ellie")) == cli.OK
    assert json.loads((root / "crew" / "ellie.json").read_text())["retired"] is True
    capsys.readouterr()

    assert cli._cmd_tend(_Args(root, dry_run=True)) == cli.OK
    out = capsys.readouterr().out
    assert tend_mod.RETIRED in out and "NOT respawned" in out
    assert tend_mod.WOULD not in out


def test_cmd_tend_writes_the_pass_log_and_status_reads_it(tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": {"role": "worker", "pane": "p-ellie",
                                        "retired": True}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))
    assert cli._cmd_tend(_Args(root)) == cli.OK
    assert supervisor.PassLog(root).last() is not None
    capsys.readouterr()

    monkeypatch.setattr(cli, "_systemctl_user_active", lambda unit: False)
    cli._cmd_tend(_Args(root, status=True))
    assert "last pass" in capsys.readouterr().out


def test_cmd_tend_dry_run_writes_no_pass_log(tmp_path, monkeypatch, capsys):
    """A dry run must not leave a record claiming a supervision pass happened."""
    root = _roster(tmp_path, {"ellie": {"role": "worker", "pane": "p-ellie"}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))
    cli._cmd_tend(_Args(root, dry_run=True))
    assert supervisor.PassLog(root).last() is None


# --- the loop's own staleness (aegis-arma follow-up): re-exec on code change

def test_code_fingerprint_moves_when_a_module_changes(tmp_path):
    """MEASURED: the live `st tend --loop` ran a two-day-old memory image while
    the editable install moved under it — every fix landed on disk and reached
    nothing (the aegis-ttlr class, one level up: disk current, PROCESS stale).
    The fingerprint is what the loop watches to re-exec itself."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    mod = pkg / "notify.py"
    mod.write_text("old = 1\n")
    before = cli._code_fingerprint(pkg)
    assert before is not None
    assert cli._code_fingerprint(pkg) == before      # stable when nothing moved
    import os
    mod.write_text("new = 2\n")
    os.utime(mod, ns=(1, 1))                         # force a distinct mtime
    assert cli._code_fingerprint(pkg) != before


def test_a_new_module_changes_the_fingerprint(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n")
    before = cli._code_fingerprint(pkg)
    (pkg / "b.py").write_text("y = 2\n")
    assert cli._code_fingerprint(pkg) != before


def test_an_empty_or_unreadable_package_is_None_never_reexec_fuel(tmp_path):
    # None = could not look; the loop treats it as 'never re-exec' — a
    # supervisor that exec-loops on a stat error is worse than a stale one.
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert cli._code_fingerprint(empty) is None


# --- the supervisor survives its sweeps (aegis-ey7n, the ENOSPC death) -------

def test_a_crashing_notify_sweep_does_not_kill_the_pass(tmp_path, monkeypatch, capsys):
    """The live loop died to ONE uncaught OSError inside a ledger write: the
    notification layer took the respawn layer down with it, and nothing
    restarted the supervisor. Each sweep now fails alone and loudly."""
    import json as _json
    from shantytown import cli, notify as notify_mod

    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "w.json").write_text(_json.dumps({"role": "worker", "pane": "p-w"}))
    panes = _Panes(screens={"p-w": IDLE})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)

    class _Boom:
        def __init__(self, *a, **k): pass
        def sweep(self, *a, **k):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(notify_mod, "Notifier", _Boom)
    monkeypatch.setattr(notify_mod, "CycleDriver", _Boom)
    monkeypatch.setattr(notify_mod, "IdleFleetAlerter", _Boom)

    class _A:
        root = tmp_path; dry_run = False
        backend = "files"; repo = None; registry = "files"

    rc = cli._tend_once(_A())          # must RETURN, not raise
    err = capsys.readouterr().err
    for sweep in ("blocked-worker", "saturation-cycle", "idle-fleet"):
        assert f"the {sweep} sweep CRASHED" in err
    assert "supervision continues" in err
    # The pass itself still ran and recorded its health signal.
    assert rc in (cli.OK, cli.CANNOT_TELL)


# --- the respawn ownership gate (aegis-2j2r) --------------------------------

class _StampedLaunches:
    """Real-API stub: get() answers from a stamped set; root is a real dir so
    the any-stamps probe reads actual files."""
    def __init__(self, root, stamped=()):
        from pathlib import Path
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for n in stamped:
            (self.root / f"{n}.json").write_text("{}")
        self._stamped = set(stamped)

    def get(self, name):
        return {"agent": name} if name in self._stamped else None

    def verdict(self, name):
        return "current"


def test_an_unstamped_card_is_REFUSED_a_respawn_when_stamps_exist(tmp_path):
    """st tend was one of the dark-crew trap's own respawners: a pilot-era
    registry card for another orchestrator's fleet reads 'down' whenever that
    orchestrator cycles it, and the respawn manufactured a pane carrying st's
    worker settings (observed live: 'RESPAWNED dearing'). No launch stamp =
    never launched by st = not st's to respawn."""
    dead = Agent(name="dearing", pane="aegis-crew-dearing", workspace="/ws/d")
    panes = _Panes(live=set())
    rt = _Runtime()
    said = []
    launches = _StampedLaunches(tmp_path / "launched", stamped=("weaver",))
    rep = _tender(panes, rt, launches=launches, log=said.append).pass_over([dead])
    f, = rep.findings
    assert f.verdict == tend_mod.REFUSED and not f.acted
    assert rt.started == [], "must not manufacture a pane it does not own"
    assert any("not st's to respawn" in m for m in said), "the refusal must be loud"


def test_an_empty_stamp_store_does_not_gate_the_respawn(tmp_path):
    """CANNOT-TELL: no stamps at all proves nothing about ownership — a fresh
    deployment must still self-heal its own dead workers."""
    dead = Agent(name="ellie", pane="p-ellie", workspace="/ws/e")
    panes = _Panes(live=set())
    rt = _Runtime()
    launches = _StampedLaunches(tmp_path / "launched", stamped=())
    rep = _tender(panes, rt, launches=launches).pass_over([dead])
    f, = rep.findings
    assert f.verdict == tend_mod.RESPAWNED and f.acted


def test_a_stamped_dead_worker_is_still_respawned(tmp_path):
    dead = Agent(name="weaver", pane="p-weaver", workspace="/ws/w")
    panes = _Panes(live=set())
    rt = _Runtime()
    launches = _StampedLaunches(tmp_path / "launched", stamped=("weaver",))
    rep = _tender(panes, rt, launches=launches).pass_over([dead])
    f, = rep.findings
    assert f.verdict == tend_mod.RESPAWNED and f.acted


# --- --unretire is the ARMING moment, and it now has a pre-flight (internal-ref)
#
# THE INCIDENT. `st tend --unretire ian` re-armed a card carrying a gt-era pane
# and NO workspace. Nothing warned. tend then launched it — into the
# supervisor's cwd, not into ian's tree — producing an agent that read defunct
# in `st crew` and live to the supervisor. It died twice before anyone
# connected the two, because the two facts lived in different tools.
#
# --retire is deliberately NOT gated: it only ever removes a card from the
# supervisor's reach, so it cannot make anything launch. Only the arming
# direction can, and it is the last moment a human is present to notice.

def test_unretire_REFUSES_a_card_with_no_workspace(tmp_path, monkeypatch, capsys):
    """THE regression, end to end, on the exact ian card."""
    root = _roster(tmp_path, {"ian": {"role": "worker", "pane": "aegis-crew-ian",
                                      "retired": True}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))

    assert cli._cmd_tend(_Args(root, unretire="ian")) == cli.REFUSED
    assert json.loads((root / "crew" / "ian.json").read_text())["retired"] is True, \
        "REFUSED still armed the card"
    err = capsys.readouterr().err
    assert "workspace" in err and "unattended" in err.lower()


def test_the_refusal_survives_the_DRY_RUN_too(tmp_path, monkeypatch, capsys):
    """A dry run that said 'would mark retired=False' while the real command
    refuses would be lying about the one thing dry runs exist to answer."""
    root = _roster(tmp_path, {"ian": {"role": "worker", "pane": "p", "retired": True}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))
    assert cli._cmd_tend(_Args(root, unretire="ian", dry_run=True)) == cli.REFUSED


def test_unretire_ALLOWS_a_card_that_can_actually_be_launched(tmp_path, monkeypatch,
                                                              capsys):
    """The negative control. A gate that refused everything would be useless
    and would be routed around within a day."""
    ws = tmp_path / "crew-ellie"; ws.mkdir()
    root = _roster(tmp_path, {"ellie": {"role": "worker", "pane": "p-ellie",
                                        "retired": True, "workspace": str(ws)}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))

    assert cli._cmd_tend(_Args(root, unretire="ellie")) == cli.OK
    assert json.loads((root / "crew" / "ellie.json").read_text())["retired"] is False


def test_RETIRING_is_never_gated_even_on_an_unlaunchable_card(tmp_path, monkeypatch,
                                                              capsys):
    """Retiring only ever REMOVES a card from the supervisor's reach. Gating it
    would refuse to stop the very agents most in need of stopping."""
    root = _roster(tmp_path, {"ian": {"role": "worker", "pane": "aegis-crew-ian"}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))
    assert cli._cmd_tend(_Args(root, retire="ian")) == cli.OK
    assert json.loads((root / "crew" / "ian.json").read_text())["retired"] is True


def test_force_arms_it_anyway_but_STILL_SAYS_WHY(tmp_path, monkeypatch, capsys):
    """The escape hatch exists because the roster call is not the supervisor's
    to make. A --force that printed nothing would train everyone to pass it by
    default, which is how a gate stops being read."""
    root = _roster(tmp_path, {"ian": {"role": "worker", "pane": "p", "retired": True}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))

    assert cli._cmd_tend(_Args(root, unretire="ian", force=True)) == cli.OK
    assert json.loads((root / "crew" / "ian.json").read_text())["retired"] is False
    out = capsys.readouterr().out
    assert "FORCED" in out and "workspace" in out


# --- and it records WHO ------------------------------------------------------

def test_retiring_records_the_actor_from_SHANTY_AGENT(tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": {"role": "worker", "pane": "p"}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))
    monkeypatch.setenv("SHANTY_AGENT", "sattler")

    assert cli._cmd_tend(_Args(root, retire="ellie")) == cli.OK
    card = json.loads((root / "crew" / "ellie.json").read_text())
    assert card["retired_by"] == "sattler"
    assert card["retired_at"].startswith("20") and card["retired_at"].endswith("+00:00")


def test_un_retiring_records_the_actor_too(tmp_path, monkeypatch, capsys):
    """The question this bead could not answer was about an UN-retirement."""
    ws = tmp_path / "w"; ws.mkdir()
    root = _roster(tmp_path, {"ian": {"role": "worker", "pane": "p", "retired": True,
                                      "workspace": str(ws)}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))
    monkeypatch.setenv("SHANTY_AGENT", "sattler")

    assert cli._cmd_tend(_Args(root, unretire="ian")) == cli.OK
    assert json.loads((root / "crew" / "ian.json").read_text())["retired_by"] == "sattler"


def test_un_retiring_does_not_REPORT_itself_as_a_retirement(tmp_path, monkeypatch, capsys):
    """internal-ref item 4. The WRITE was right and stays (the fields mean "who
    last MOVED retired" — test_un_retiring_records_the_actor_too pins that, and
    internal-ref paid for it). The REPORT was wrong: it printed
    "recorded on the card: retired_by=X retired_at=T" after an UN-retirement,
    which reads as "X retired this" — the inverse of what happened, in the one
    line whose job is to say what was recorded."""
    ws = tmp_path / "w"; ws.mkdir()
    root = _roster(tmp_path, {"ian": {"role": "worker", "pane": "p", "retired": True,
                                      "workspace": str(ws),
                                      "retired_by": "zia", "retired_at": "2026-07-01T00:00:00+00:00"}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))
    monkeypatch.setenv("SHANTY_AGENT", "sattler")

    assert cli._cmd_tend(_Args(root, unretire="ian")) == cli.OK
    out = capsys.readouterr().out
    assert "UN-RETIRED by sattler" in out, "the report still describes a retirement"
    assert "recorded on the card: RETIRED by" not in out
    # the provenance it REPLACES is named, so the prior decision is not silently lost
    assert "zia" in out and "2026-07-01" in out


def test_retiring_still_REPORTS_itself_as_a_retirement(tmp_path, monkeypatch, capsys):
    """The control: the retire path must keep saying RETIRED, or the fix above
    would have made both directions equally uninformative."""
    root = _roster(tmp_path, {"ellie": {"role": "worker", "pane": "p"}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))
    monkeypatch.setenv("SHANTY_AGENT", "sattler")

    assert cli._cmd_tend(_Args(root, retire="ellie")) == cli.OK
    out = capsys.readouterr().out
    assert "RETIRED by sattler" in out and "UN-RETIRED" not in out


def test_a_human_at_a_shell_is_labelled_as_a_UNIX_login_not_a_crew_name(
        tmp_path, monkeypatch, capsys):
    """The two namespaces are not guaranteed disjoint, and an audit line that
    could not tell 'the crew member' from 'the account' answers the forensic
    question ambiguously — which is the failure it exists to remove."""
    root = _roster(tmp_path, {"ellie": {"role": "worker", "pane": "p"}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live=set()))
    monkeypatch.delenv("SHANTY_AGENT", raising=False)

    assert cli._cmd_tend(_Args(root, retire="ellie")) == cli.OK
    assert json.loads(
        (root / "crew" / "ellie.json").read_text())["retired_by"].startswith("unix:")


def test_a_crash_loop_retirement_names_the_RULE_not_the_ambient_process(
        tmp_path, monkeypatch):
    """Inside a tend pass $SHANTY_AGENT is whoever owns the supervisor — true
    and useless. The reader's question is WHAT DECIDED THIS, and the answer is
    the crash-loop rule. It also keeps the one automatic retirement path
    distinguishable from a deliberate one."""
    root = _roster(tmp_path, {"billy": {"role": "worker", "pane": "p"}})
    monkeypatch.setenv("SHANTY_AGENT", "sattler")
    cli._retire_card(_Args(root), "billy")
    card = json.loads((root / "crew" / "billy.json").read_text())
    assert card["retired"] is True
    assert card["retired_by"] == "st tend (crash-loop)"


def test_the_verdicts_CARRY_the_provenance(tmp_path):
    """RESURRECTED is a forensic finding. 'who agreed to stop this, and when'
    are the two questions it raises, both are on the card, and printing them
    turns a page into a lead."""
    card = Agent(name="ian", pane="p-ian", retired=True, retired_by="sattler",
                 retired_at="2026-08-01T22:53:51+00:00")
    rep = _tender(_Panes(live={"p-ian"}), _Runtime()).pass_over([card])
    f, = rep.findings
    assert f.verdict == tend_mod.RESURRECTED
    assert "sattler" in f.why and "2026-08-01T22:53:51+00:00" in f.why


def test_an_unrecorded_retirement_does_not_pad_the_verdict_with_UNKNOWN(tmp_path):
    """An old card must not be dressed up as a fresh mystery."""
    card = Agent(name="goldblum", pane="p-g", retired=True)
    rep = _tender(_Panes(live=set()), _Runtime()).pass_over([card])
    f, = rep.findings
    assert f.verdict == tend_mod.RETIRED and "unrecorded" not in f.why


def test_the_crash_loop_retirement_ACTUALLY_WRITES_THE_CARD(tmp_path, monkeypatch,
                                                            capsys):
    """A pre-existing NameError, found by the provenance test above and fixed
    with it (internal-ref).

    `_retire_card` used a bare `replace` while the only import of it sat inside
    `_tend_retire`, so this raised on every call and had NEVER written a card.
    The raise landed in a broad `except Exception` that printed a warning into
    the middle of a tend pass — so tend went on reporting CRASH_LOOP ("RETIRED
    rather than respawned again") while the card said nothing of the kind. The
    supervisor's report and the durable state disagreed, silently, which is the
    same shape as the bug this bead is about, one caller over.

    Asserting on the CARD, never on the absence of the warning: the warning is
    what made it invisible for so long.
    """
    root = _roster(tmp_path, {"billy": {"role": "worker", "pane": "p"}})
    cli._retire_card(_Args(root), "billy")
    card = json.loads((root / "crew" / "billy.json").read_text())
    assert card["retired"] is True, "the crash-loop give-up wrote nothing"
    assert "could not retire" not in capsys.readouterr().err


# --- a MASKED foreign unit is a tombstone, not a competitor -------------------
#
# A masked Type=oneshot + RemainAfterExit=yes unit that ran before it was masked
# reports active FOREVER. Reading that residue as a live supervisor refused
# --install with no way out short of a reboot — and refused it precisely because
# the operator had already made the call the refusal exists to demand.

def test_a_masked_unit_is_not_a_foreign_supervisor_even_while_active():
    """The live case: gastown-crew.service masked, but still reporting active
    from its pre-mask boot run. The other FOREIGN_UNITS entry is quiet."""
    assert supervisor.foreign_supervisor(
        is_active=lambda u: u == "gastown-crew.service",
        is_masked=lambda u: u == "gastown-crew.service") is None


def test_masked_beats_enabled_as_well_as_active():
    assert supervisor.foreign_supervisor(
        is_active=lambda u: False,
        is_enabled=lambda u: True,
        is_masked=lambda u: True) is None


def test_an_unmasked_active_unit_still_refuses():
    """The guard must not be softened into uselessness by the masked case."""
    got = supervisor.foreign_supervisor(
        is_active=lambda u: u == "gastown-crew.service",
        is_masked=lambda u: False)
    assert got == ("gastown-crew.service", "active now")


def test_install_PROCEEDS_past_a_masked_but_still_active_foreign_unit(unit_home, tmp_path):
    changed, msg = supervisor.install(
        ST_BIN, tmp_path / "root", run=lambda c: None,
        is_active=lambda u: True,
        is_masked=lambda u: True)
    assert changed, f"refused a tombstone: {msg}"
    assert (unit_home / supervisor.TIMER).exists()
