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

    def __init__(self, screens=None, cmdlines=None, live=None):
        super().__init__(live=set(live if live is not None else (screens or {})),
                         cmdlines=cmdlines)
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
            ensure=lambda card: card.workspace, log=None):
    return tend_mod.Tender(panes, runtime, launches or _Launches(),
                           spawn=spawn or runtime.start, refresh=refresh,
                           ensure=ensure, log=log or (lambda m: None))


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
    """We did not start it, and something did. That is not a log line."""
    card = Agent(name="ellie", pane="p-ellie", retired=True)
    said = []
    rep = _tender(_Panes({"p-ellie": IDLE}), _Runtime(),
                  log=said.append).pass_over([card])

    assert rep.findings[0].verdict == tend_mod.RESURRECTED
    assert not rep.healthy(), "a resurrected retiree must not exit clean"
    assert any("ESCALATE" in m for m in said), "acted invisibly"


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

@pytest.fixture
def unit_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path / ".config" / "systemd" / "user"


def test_install_is_idempotent(unit_home, tmp_path):
    ran = []
    changed, msg = supervisor.install("st", tmp_path / "root", run=ran.append)
    assert changed and (unit_home / supervisor.TIMER).exists()

    changed2, msg2 = supervisor.install("st", tmp_path / "root", run=ran.append)
    assert not changed2, "a second install stacked units"
    assert "already installed" in msg2


def test_uninstall_removes_everything_it_wrote(unit_home, tmp_path):
    supervisor.install("st", tmp_path / "root", run=lambda c: None)
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
        "st", tmp_path / "root", run=lambda c: None,
        is_active=lambda unit: unit == "gastown-crew-watchdog.timer")
    assert not changed
    assert "REFUSED" in msg and "gastown-crew-watchdog.timer" in msg
    assert not (unit_home / supervisor.TIMER).exists(), "wrote units anyway"


def test_install_REFUSES_to_overwrite_a_unit_it_did_not_write(unit_home, tmp_path):
    unit_home.mkdir(parents=True)
    (unit_home / supervisor.TIMER).write_text("[Timer]\n# somebody else's\n")
    changed, msg = supervisor.install("st", tmp_path / "root", run=lambda c: None)
    assert not changed and "REFUSED" in msg
    assert "somebody else's" in (unit_home / supervisor.TIMER).read_text()


def test_uninstall_REFUSES_a_unit_it_did_not_write(unit_home, tmp_path):
    unit_home.mkdir(parents=True)
    (unit_home / supervisor.TIMER).write_text("[Timer]\n# not ours\n")
    changed, msg = supervisor.uninstall(run=lambda c: None)
    assert not changed and "REFUSED" in msg
    assert (unit_home / supervisor.TIMER).exists()


# --- the command ------------------------------------------------------------

class _Args:
    def __init__(self, root, **kw):
        self.root = Path(root)
        self.backend = None; self.repo = None; self.registry = "files"
        self.install = self.uninstall = self.status = False
        self.retire = self.unretire = None
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


# --- the loop's own staleness (internal-ref follow-up): re-exec on code change

def test_code_fingerprint_moves_when_a_module_changes(tmp_path):
    """MEASURED: the live `st tend --loop` ran a two-day-old memory image while
    the editable install moved under it — every fix landed on disk and reached
    nothing (the internal-ref class, one level up: disk current, PROCESS stale).
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


# --- the supervisor survives its sweeps (internal-ref, the ENOSPC death) -------

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
    assert err.count("CRASHED") == 3, "each sweep must fail alone and loudly"
    assert "supervision continues" in err
    # The pass itself still ran and recorded its health signal.
    assert rc in (cli.OK, cli.CANNOT_TELL)


# --- the respawn ownership gate (internal-ref) --------------------------------

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
