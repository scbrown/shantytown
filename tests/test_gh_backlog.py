"""One test per GitHub issue closed in the backlog sweep, named by number.

The issues are individually small; what they share is a genre — a surface that
answers a question it could not have answered, or destroys something on the way to
reporting success. Each test below is the specific claim in the issue, so a
regression is attributable to the report that found it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shantytown import cli, doctor as doc, harness as harness_mod
from shantytown.protocols import Agent


# --- #24: bd events must be attributable to the agent, not $USER -------------

def test_gh24_launch_carries_BEADS_ACTOR():
    launch = harness_mod.get("claude").launch(
        Agent(name="kelly", role="worker"), "/s/worker.json")
    assert "BEADS_ACTOR=kelly" in launch


def test_gh24_the_actor_is_the_card_name_not_the_role():
    launch = harness_mod.get("claude").launch(
        Agent(name="sattler", role="administrator"), "/s/administrator.json")
    assert "BEADS_ACTOR=sattler" in launch


# --- #17: the card's model must reach the launch -----------------------------

def test_gh17_card_model_is_honoured_at_launch():
    """The field was persisted so a restart would not silently revert to the
    default — and the launcher never read it, so it reverted anyway."""
    launch = harness_mod.get("claude").launch(
        Agent(name="kelly", role="worker", model="opus"), "/s/worker.json")
    assert "--model opus" in launch


def test_gh17_no_model_means_no_flag():
    launch = harness_mod.get("claude").launch(
        Agent(name="kelly", role="worker"), "/s/worker.json")
    assert "--model" not in launch


def test_gh17_settings_resolve_per_AGENT_before_role(tmp_path):
    """All workers shared one file, so nothing could differ per agent."""
    sdir = tmp_path / "settings"
    sdir.mkdir()
    (sdir / "worker.settings.json").write_text("{}")
    resolve = cli._default_settings(tmp_path)
    card = Agent(name="kelly", role="worker")
    assert resolve(card).endswith("worker.settings.json")

    (sdir / "agent-kelly.settings.json").write_text("{}")
    assert resolve(card).endswith("agent-kelly.settings.json")


def test_gh17_no_settings_at_all_is_still_None(tmp_path):
    """compose REFUSES on None. No settings, no launch — never a fallback."""
    (tmp_path / "settings").mkdir()
    assert cli._default_settings(tmp_path)(Agent(name="kelly")) is None


# --- #15 / #16: emitting settings must not erase the operator's keys ---------

def test_gh15_operator_keys_survive_a_re_emit(tmp_path):
    sdir = tmp_path / "settings"
    sdir.mkdir()
    p = sdir / "worker.settings.json"
    p.write_text(json.dumps({
        "permissions": {"allow": ["Bash(ls:*)"]},
        "env": {"MY_VAR": "1"},
    }))
    cli._emit_role_settings(tmp_path, {"worker"})
    got = json.loads(p.read_text())
    assert got["permissions"] == {"allow": ["Bash(ls:*)"]}
    assert got["env"]["MY_VAR"] == "1", "the operator's var survives"
    assert got["env"]["BOBBIN_ROLE"] == "worker", "and st's own still lands"
    assert "Stop" in got["hooks"], "and st's own hooks are still emitted"


def test_gh16_a_hand_wired_hook_EVENT_survives(tmp_path):
    """cli.md tells the reader to wire their own SessionStart prime; the emitter
    erased it, which made the documented escape hatch unkeepable. An event st does
    NOT emit must be left exactly as found."""
    sdir = tmp_path / "settings"
    sdir.mkdir()
    p = sdir / "worker.settings.json"
    mine = [{"hooks": [{"type": "command", "command": "/my/notify.sh"}]}]
    p.write_text(json.dumps({"hooks": {"Notification": mine}}))
    cli._emit_role_settings(tmp_path, {"worker"})
    got = json.loads(p.read_text())
    assert got["hooks"]["Notification"] == mine


def test_gh15_a_stale_st_hook_does_NOT_survive(tmp_path):
    """The other direction, and it is why this is a per-EVENT replace rather than
    a deep merge: a stale stop direction surviving a rewrite is the drift
    `roles set` exists to remove."""
    sdir = tmp_path / "settings"
    sdir.mkdir()
    p = sdir / "worker.settings.json"
    p.write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "python -m shantytown.stop_event bogus"}]}]}}))
    cli._emit_role_settings(tmp_path, {"worker"})
    got = json.loads(p.read_text())
    assert "bogus" not in json.dumps(got["hooks"]["Stop"])


def test_gh15_a_corrupt_existing_file_does_not_block_the_emit(tmp_path):
    sdir = tmp_path / "settings"
    sdir.mkdir()
    p = sdir / "worker.settings.json"
    p.write_text("{not json")
    cli._emit_role_settings(tmp_path, {"worker"})
    assert "Stop" in json.loads(p.read_text())["hooks"]


# --- #18: a session must not inherit the launcher's cwd ----------------------

def test_gh18_new_session_starts_in_the_agents_own_directory(tmp_path, monkeypatch):
    from shantytown.tmux import Tmux
    calls = []

    class _R:
        returncode = 0
        stdout = ""

    def fake_run(argv, **kw):
        calls.append(argv)
        return _R()

    monkeypatch.setattr("subprocess.run", fake_run)
    t = Tmux()
    monkeypatch.setattr(t, "exists", lambda _n: False)
    t.new_session("st-kelly", cwd=str(tmp_path))
    created = next(a for a in calls if "new-session" in a)
    assert "-c" in created and str(tmp_path) in created


def test_gh18_a_missing_workspace_does_not_fail_the_launch(tmp_path, monkeypatch):
    """tmux fails the whole new-session on a missing -c. A launch refused because
    a directory is not there yet is worse than a session in the wrong cwd —
    ensure_workspace runs first and reports the real problem."""
    from shantytown.tmux import Tmux
    calls = []

    class _R:
        returncode = 0
        stdout = ""

    monkeypatch.setattr("subprocess.run", lambda argv, **kw: (calls.append(argv), _R())[1])
    t = Tmux()
    monkeypatch.setattr(t, "exists", lambda _n: False)
    t.new_session("st-kelly", cwd=str(tmp_path / "nope"))
    created = next(a for a in calls if "new-session" in a)
    assert "-c" not in created


# --- #25: a probe with a side effect is not a probe --------------------------

def test_gh25_the_quipu_version_is_never_probed():
    """`quipu-server --version` answers by STARTING A SERVER, which binds a port —
    then doctor rendered the failed probe's own address as if it were quipu's."""
    spec = next(s for s in doc.SPECS if s.name == "quipu")
    assert spec.version_unsafe is True
    ran = []
    h = doc.detect(spec, which=lambda _n: "/usr/bin/quipu-server",
                   run=lambda argv: (ran.append(argv), (0, "1.2.3"))[1],
                   fetch=lambda _r: (None, None), check_latest=False)
    assert ran == [], "the version probe must not be RUN at all"
    assert h.version is None
    assert "side effect" in (h.version_error or "")


def test_gh25_a_safe_probe_is_still_read():
    spec = next(s for s in doc.SPECS if s.name == "beads")
    assert spec.version_unsafe is False
    h = doc.detect(spec, which=lambda _n: "/usr/bin/bd",
                   run=lambda _a: (0, "bd version 1.0.5"),
                   fetch=lambda _r: (None, None), check_latest=False)
    assert h.version == "1.0.5"


# --- #27: a service is present when it is RUNNING ---------------------------

def test_gh27_a_running_service_is_not_reported_absent():
    """reactor had been up for 9 days while doctor said 'not installed' and
    advised an --install that would put a CLI beside the running service."""
    spec = next(s for s in doc.SPECS if s.name == "reactor")
    assert spec.service_unit == "reactor"
    h = doc.detect(spec, which=lambda _n: None,
                   run=lambda argv: (0, "") if "systemctl" in argv[0] else (1, ""),
                   fetch=lambda _r: (None, None), check_latest=False,
                   offpath=lambda *a, **k: None)
    assert h.present is True


def test_gh27_a_dead_service_is_still_absent():
    spec = next(s for s in doc.SPECS if s.name == "reactor")
    h = doc.detect(spec, which=lambda _n: None, run=lambda _a: (3, ""),
                   fetch=lambda _r: (None, None), check_latest=False,
                   offpath=lambda *a, **k: None)
    assert h.present is False


def test_gh27_no_systemd_is_absent_not_present():
    """'We could not ask' must never render as 'it is running'."""
    def boom(_argv):
        raise FileNotFoundError("systemctl")

    assert doc._service_active("reactor", run=boom) is False


# --- #14: --read must show what it consumed ---------------------------------

def test_gh14_read_prints_the_bodies(tmp_path, capsys, monkeypatch):
    from shantytown.inbox import FilesInbox
    box = FilesInbox(tmp_path / "inbox")
    box.deliver("kelly", "the roof needs patching", frm="sattler")

    class _A:
        root = tmp_path
        agent = "kelly"
        registry = "files"
        backend = "files"
        repo = None
        read = True
        count = False
        message: list = []
        durable = False
        dry_run = False

    monkeypatch.setenv("SHANTY_AGENT", "kelly")
    assert cli._inbox_read(_A(), "kelly") == cli.OK
    out = capsys.readouterr().out
    assert "the roof needs patching" in out, "the body, not just a count"
    assert "from sattler" in out
    assert "marked 1 message(s) read" in out


# --- #36: an absent kit and a DELETED kit are different facts ----------------

def test_gh36_a_deleted_template_REFUSES(tmp_path):
    """provision.py opens by saying every rule there is 'a refusal rather than a
    warning'. This was the one path that was not — and it is the one that fires
    when the whole kit disappears."""
    from shantytown import provision as prov
    from shantytown.provision import ProvisionError
    ws = tmp_path / "ws"
    ws.mkdir()
    d = prov.provision_dir(tmp_path)
    d.mkdir(parents=True)
    (d / prov.SECRETS).write_text("TOKEN=abc\n")      # the store DOES provision
    card = Agent(name="kelly", role="worker", workspace=str(ws))
    with pytest.raises(ProvisionError) as e:
        prov.provision(card, tmp_path)
    assert "MISSING" in str(e.value)
    assert "deleted or renamed" in str(e.value)


def test_gh36_no_provision_dir_at_all_still_launches(tmp_path):
    """A store with no kit describes a fleet that wants no MCP servers. Refusing
    would break every install that is not ours."""
    from shantytown import provision as prov
    ws = tmp_path / "ws"
    ws.mkdir()
    card = Agent(name="kelly", role="worker", workspace=str(ws))
    assert prov.provision(card, tmp_path) == []


def test_gh36_an_EMPTY_provision_dir_still_launches(tmp_path):
    from shantytown import provision as prov
    ws = tmp_path / "ws"
    ws.mkdir()
    prov.provision_dir(tmp_path).mkdir(parents=True)
    card = Agent(name="kelly", role="worker", workspace=str(ws))
    assert prov.provision(card, tmp_path) == []


# --- #20: a landed send with a failed tracker write is its own outcome --------

def test_gh20_a_failed_tracker_write_after_a_LANDED_send_is_named(tmp_path):
    from shantytown.dispatch import Dispatcher, DispatchedButUntracked

    class _Tracker:
        def get(self, i):
            from shantytown.protocols import WorkItem
            return WorkItem(id=i, title="fix the roof", status="open")

        def update(self, *a, **k):
            raise RuntimeError("bd unreachable")

    class _Panes:
        def exists(self, p):
            return True

        def capture(self, p, history=0, attrs=False):
            return "st-1 >"

        def send(self, p, t):
            pass

    class _Reg:
        def get(self, n):
            return Agent(name=n, role="worker", pane="p-kelly")

        def all(self):
            return [self.get("kelly")]

    d = Dispatcher(_Reg(), _Tracker(), _Panes())
    with pytest.raises(DispatchedButUntracked) as e:
        d.go("st-1", "kelly")
    msg = str(e.value)
    assert "WAS DELIVERED" in msg
    assert "Do NOT re-run" in msg, "re-running would deliver it twice"
    assert "bd unreachable" in msg, "and it names the underlying cause"


# --- #29 / #23: Rule Zero must be satisfiable by RESTRAINT --------------------

def test_gh29_a_stood_down_fleet_is_not_re_dispatched():
    """Measured: an operator out of usage credits stopped nine of eleven crew on
    instruction, and the gate blocked every stop demanding they come back."""
    from shantytown import stop_policy as sp
    from shantytown.config import Fleet
    v = sp.decide(sp.Inputs(me="sattler", role="administrator",
                            free_feedable=["bond"], dispatchable=2,
                            fleet=Fleet(stood_down=True)))
    assert not v.block and v.by == sp.BY_STOOD_DOWN
    assert "STOOD DOWN" in v.reason
    assert "stood_down" in v.reason, "and it says how to resume"


def test_gh23_an_over_capacity_host_is_not_asked_for_more():
    """Forced dispatch to 9 agents on an 8-core box at load 33."""
    from shantytown import stop_policy as sp
    from shantytown.config import Fleet
    v = sp.decide(sp.Inputs(me="sattler", role="administrator",
                            free_feedable=["bond"], dispatchable=9,
                            fleet=Fleet(max_load_per_core=4.0),
                            load_per_core=4.1))
    assert not v.block and v.by == sp.BY_STOOD_DOWN
    assert "over the" in v.reason


def test_gh23_under_the_ceiling_still_blocks():
    from shantytown import stop_policy as sp
    from shantytown.config import Fleet
    v = sp.decide(sp.Inputs(me="sattler", role="administrator",
                            free_feedable=["bond"], dispatchable=9,
                            fleet=Fleet(max_load_per_core=4.0),
                            load_per_core=1.0))
    assert v.block and v.by == sp.BY_RULE_ZERO


def test_gh23_an_unmeasurable_load_does_not_suppress_the_gate():
    """Refusing to dispatch on a number we could not measure would be the mirror
    of the bug."""
    from shantytown import stop_policy as sp
    from shantytown.config import Fleet
    v = sp.decide(sp.Inputs(me="sattler", role="administrator",
                            free_feedable=["bond"], dispatchable=9,
                            fleet=Fleet(max_load_per_core=4.0),
                            load_per_core=None))
    assert v.block and v.by == sp.BY_RULE_ZERO


def test_gh23_zero_disables_the_capacity_check():
    from shantytown import stop_policy as sp
    from shantytown.config import Fleet
    v = sp.decide(sp.Inputs(me="sattler", role="administrator",
                            free_feedable=["bond"], dispatchable=9,
                            fleet=Fleet(max_load_per_core=0.0),
                            load_per_core=99.0))
    assert v.block and v.by == sp.BY_RULE_ZERO


# --- #12: a crash-looping agent must not become a respawn thrasher -----------

class _Crashes:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    def get(self, a):
        return self.rows.get(a, (0, 0.0))

    def died(self, a, now):
        n, _ = self.get(a)
        self.rows[a] = (n + 1, now)

    def clear(self, a):
        self.rows.pop(a, None)


def _tender(crashes, now=1000.0, retired=None, spawned=None):
    from shantytown import tend as tend_mod
    from shantytown.tmux import NullPanes
    panes = NullPanes(live=set())

    class _RT:
        def shows_ready_ui(self, s):
            return True

        def start(self, card, pane):
            (spawned if spawned is not None else []).append(card.name)

    return tend_mod.Tender(
        panes, _RT(), None, spawn=lambda c, p: None, refresh=None,
        ensure=lambda card: card.workspace, crashes=crashes,
        retire=(retired.append if retired is not None else None),
        now=lambda: now, log=lambda m: None)


def test_gh12_a_second_death_inside_the_window_BACKS_OFF():
    from shantytown import tend as tend_mod
    crashes = _Crashes({"kelly": (1, 990.0)})       # died 10s ago
    rep = _tender(crashes, now=1000.0).pass_over([Agent(name="kelly", pane="p-kelly")])
    f = rep.findings[0]
    assert f.verdict == tend_mod.BACKOFF
    assert not f.acted, "a backoff must not launch"
    assert "before retry 2" in f.why


def test_gh12_once_the_window_passes_it_retries():
    from shantytown import tend as tend_mod
    crashes = _Crashes({"kelly": (1, 0.0)})         # died long ago
    rep = _tender(crashes, now=100000.0).pass_over([Agent(name="kelly", pane="p-kelly")])
    assert rep.findings[0].verdict == tend_mod.RESPAWNED


def test_gh12_the_window_GROWS():
    from shantytown import tend as tend_mod
    # 3 prior deaths -> 60 * 2**2 = 240s. At 100s elapsed it must still wait.
    crashes = _Crashes({"kelly": (3, 900.0)})
    rep = _tender(crashes, now=1000.0).pass_over([Agent(name="kelly", pane="p-kelly")])
    assert rep.findings[0].verdict == tend_mod.BACKOFF


def test_gh12_a_persistent_crash_loop_is_RETIRED_not_thrashed():
    """A supervisor that never gives up hides a broken agent behind infinite
    optimism."""
    from shantytown import tend as tend_mod
    retired = []
    crashes = _Crashes({"kelly": (tend_mod.BACKOFF_RETRIES, 0.0)})
    rep = _tender(crashes, now=100000.0, retired=retired).pass_over(
        [Agent(name="kelly", pane="p-kelly")])
    f = rep.findings[0]
    assert f.verdict == tend_mod.CRASH_LOOP
    assert retired == ["kelly"], "durably, on the card"
    assert not rep.healthy(), "and it is a FAULT, so the exit code says so"
    assert "unretire" in f.why, "and it says how to undo it"


def test_gh12_seeing_an_agent_ALIVE_clears_the_episode():
    """An agent that recovers must not be punished for an old episode."""
    from shantytown import tend as tend_mod
    from shantytown.tmux import NullPanes
    crashes = _Crashes({"kelly": (3, 900.0)})
    panes = NullPanes(live={"p-kelly"}, screen="> ")

    class _RT:
        def shows_ready_ui(self, s):
            return True

    tend_mod.Tender(panes, _RT(), None, crashes=crashes, now=lambda: 1000.0,
                    log=lambda m: None).pass_over([Agent(name="kelly", pane="p-kelly")])
    assert crashes.get("kelly") == (0, 0.0)


def test_gh12_no_crash_store_means_the_old_behaviour():
    """crashes=None disables the backoff entirely, so every existing caller is
    unchanged."""
    from shantytown import tend as tend_mod
    rep = _tender(None, now=1000.0).pass_over([Agent(name="kelly", pane="p-kelly")])
    assert rep.findings[0].verdict == tend_mod.RESPAWNED


def test_gh12_the_crash_log_survives_a_round_trip(tmp_path):
    from shantytown.supervisor import CrashLog
    log = CrashLog(tmp_path)
    assert log.get("kelly") == (0, 0.0)
    log.died("kelly", 1000.0)
    log.died("kelly", 1100.0)
    assert log.get("kelly") == (2, 1100.0)
    log.clear("kelly")
    assert log.get("kelly") == (0, 0.0)


def test_gh12_an_unreadable_crash_log_does_not_stop_supervision(tmp_path):
    from shantytown.supervisor import CrashLog
    log = CrashLog(tmp_path)
    log.path.write_text("{not json")
    assert log.get("kelly") == (0, 0.0)


# --- #28: we must not inherit bd's silent truncation -------------------------

def test_gh28_every_bd_list_or_ready_asks_for_everything():
    """`bd ready --json` returned 10 of 174 and `bd list --json` 50 of 190, with
    empty stderr and exit 0. Every consumer here reasons about the WHOLE queue, so
    a silently short list is a wrong answer, not a small one."""
    import re
    src = Path("shantytown")
    offenders = []
    for py in src.glob("*.py"):
        text = py.read_text()
        # bd ONLY. `pipx list --json` and friends are other tools with other
        # contracts; this guard is about the one that truncates silently.
        for m in re.finditer(r'(?:"bd"|_bd|\bbd_json)[^\n]{0,120}?"(ready|list)"', text):
            window = text[m.start():m.start() + 300]
            if '--limit' not in window:
                offenders.append(f"{py.name}: {m.group(0)[:70]}")
    assert not offenders, (
        "these bd calls can be silently truncated: " + "; ".join(offenders))
