"""`st fleet watch` — mutual administrator liveness (aegis-az0a40).

Stiwi, 2026-09-29: "You should have an alert for this. And she should have an
alert for if you can't be contacted. And each of you should try to fix that."

The contract these pin:
  · probes run cheapest first, and a probe that could not run is UNKNOWN —
    never DOWN — so it never alerts, repairs or escalates
  · DOWN alerts the LOCAL administrator, attempts ONE repair per cooldown, and
    a repair counts only when a second probe sees the peer up
  · a human is paged only when the repair FAILED or the outage outlived the
    threshold — and once per outage, not once per pass
  · the textfile reports up=1/0 and OMITS up for unknown
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from shantytown import peer_watch as pw
from shantytown.config import HostPeer

PEER = HostPeer(name="host-b", ssh="user@peer.example", root="/opt/st/.shanty")


# ------------------------------------------------------------------ probes --

class Res:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def row(**kw):
    base = dict(name="bea", role="administrator", state="up", work="busy",
                posture="auto", harness="claude", settings="ok", tree="ok",
                pane="shanty-bea", live=True, foreground="claude",
                last_active=1000.0, host="host-b")
    base.update(kw)
    return base


def census(*rows, error=None):
    return lambda peer: {"host": peer.name, "agents": list(rows), "error": error}


def ssh_ok(argv, **kw):
    return Res(0)


def test_probe_unreachable_host_is_unknown_not_down():
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired("ssh", 15)
    obs = pw.probe(PEER, "bea", run=boom, read_peer=census(row()))
    assert obs.verdict == pw.UNKNOWN and "did not answer ssh" in obs.reason
    obs = pw.probe(PEER, "bea", run=lambda a, **k: Res(255, err="No route to host"),
                   read_peer=census(row()))
    assert obs.verdict == pw.UNKNOWN and "No route to host" in obs.reason


def test_probe_st_that_does_not_answer_is_unknown():
    obs = pw.probe(PEER, "bea", run=ssh_ok, read_peer=census(error="ValueError: bad"))
    assert obs.verdict == pw.UNKNOWN and "did not answer" in obs.reason


def test_probe_missing_pane_is_down_and_repairable():
    obs = pw.probe(PEER, None, run=ssh_ok, read_peer=census(row(live=False, state="down")))
    assert (obs.verdict, obs.repairable, obs.peer) == (pw.DOWN, True, "bea")


def test_probe_shell_pane_is_down_but_not_repairable():
    obs = pw.probe(PEER, "bea", run=ssh_ok, read_peer=census(row(foreground="bash")))
    assert obs.verdict == pw.DOWN and not obs.repairable
    assert "agent new" in obs.repair_why


def test_probe_auth_dead_is_down_not_repairable():
    obs = pw.probe(PEER, "bea", run=ssh_ok, read_peer=census(row(work="auth-dead")))
    assert obs.verdict == pw.DOWN and not obs.repairable


def test_probe_healthy_reports_activity_age_but_never_judges_it():
    obs = pw.probe(PEER, "bea", run=ssh_ok,
                   read_peer=census(row(work="idle", last_active=0.0)),
                   now=lambda: 90000.0)          # 25h quiet at an idle prompt
    assert obs.verdict == pw.OK and obs.activity_age == 90000.0


def test_probe_older_peer_without_new_fields_is_still_ok():
    r = row(); del r["foreground"]; del r["last_active"]
    obs = pw.probe(PEER, "bea", run=ssh_ok, read_peer=census(r))
    assert obs.verdict == pw.OK and obs.activity_age is None
    assert ("runtime", "not measured (peer st predates it)") in obs.probes


def test_probe_cycling_is_unknown():
    obs = pw.probe(PEER, "bea", run=ssh_ok,
                   read_peer=census(row(state="cycling", live=False)))
    assert obs.verdict == pw.UNKNOWN


def test_probe_resolves_the_one_administrator_and_refuses_to_guess_among_two():
    worker = row(name="wu", role="worker")
    obs = pw.probe(PEER, None, run=ssh_ok, read_peer=census(worker, row()))
    assert obs.peer == "bea"
    obs = pw.probe(PEER, None, run=ssh_ok, read_peer=census(row(), row(name="cat")))
    assert obs.verdict == pw.UNKNOWN and "--peer" in obs.reason


def test_repair_runs_the_peer_hosts_own_agent_new():
    seen = []
    def run(argv, **kw):
        seen.append(argv); return Res(0, out="  -> bea up")
    ok, detail = pw.repair(PEER, "bea", run=run)
    assert ok and seen[0][:2] == ["ssh", "-o"] and "user@peer.example" in seen[0]
    assert "--root /opt/st/.shanty --registry files agent new bea" in seen[0][-1]
    ok, detail = pw.repair(PEER, "bea", run=lambda a, **k: Res(1, err="refused: x"))
    assert not ok and "refused: x" in detail


def test_escalate_unconfigured_pages_nobody_and_says_so():
    rc, detail = pw.escalate_via_command(None, "high", "d")
    assert rc is None and "NOT CONFIGURED" in detail
    seen = []
    rc, _ = pw.escalate_via_command("/opt/esc.sh --x", "high", "desc",
                                    run=lambda a, **k: (seen.append(a), Res(0))[1])
    assert rc == 0 and seen[0] == ["/opt/esc.sh", "--x", "-s", "high", "desc"]


# -------------------------------------------------------------------- pass --

class Harness:
    """The side effects of a pass, recorded. `script` is the sequence of probe
    verdicts; each probe_fn call consumes one."""

    def __init__(self, tmp_path, *script, repair_ok=True, esc_rc=0):
        self.script = list(script)
        self.alerts, self.repairs, self.escalations = [], [], []
        self.repair_ok, self.esc_rc = repair_ok, esc_rc
        self.state = pw.State(tmp_path / "peer-watch" / "host-b.json")
        self.log = tmp_path / "peer-watch" / "log.jsonl"
        self.t = 10_000.0

    def probe(self, name):
        return self.script.pop(0)

    def run(self, advance=0.0, **kw):
        self.t += advance
        return pw.run_pass(
            watcher="ada", peer_host="host-b", peer_name=None,
            probe_fn=self.probe,
            alert_fn=lambda text: (self.alerts.append(text), (True, "ok"))[1],
            repair_fn=kw.pop("repair_fn", lambda name: (self.repairs.append(name),
                                                        (self.repair_ok, "relaunch"))[1]),
            escalate_fn=lambda desc: (self.escalations.append(desc), (self.esc_rc, "sent"))[1],
            state=self.state, log_path=self.log, now=lambda: self.t,
            say=lambda s: None, **kw)


def up():
    return pw.Observation(pw.OK, "bea is up", peer="bea", activity_age=30.0)


def down(repairable=True):
    return pw.Observation(pw.DOWN, "bea's pane is not there", peer="bea",
                          repairable=repairable,
                          repair_why="" if repairable else "runtime exited")


def unknown():
    return pw.Observation(pw.UNKNOWN, "host host-b did not answer ssh")


def test_healthy_pass_does_nothing_and_exits_zero(tmp_path):
    h = Harness(tmp_path, up())
    out = h.run()
    assert out.verdict == pw.OK and out.exit_code == 0
    assert h.alerts == h.repairs == h.escalations == []
    assert not h.log.exists()                 # a healthy pass is not news


def test_down_then_repaired_alerts_repairs_verifies_and_never_pages(tmp_path):
    h = Harness(tmp_path, down(), up())
    out = h.run()
    assert out.verdict == pw.OK and out.observed == pw.DOWN and out.exit_code == 0
    assert h.repairs == ["bea"]
    assert "DOWN" in h.alerts[0] and "verified up" in h.alerts[1]
    assert h.escalations == []
    rec = json.loads(h.log.read_text().splitlines()[-1])
    assert rec["observed"] == "down" and rec["verdict"] == "ok"
    assert rec["actions"] == ["alert", "repair"]
    assert "down_since" not in h.state.read()


def test_a_relaunch_that_exits_zero_but_is_not_up_is_a_failed_repair(tmp_path):
    h = Harness(tmp_path, down(), down())
    out = h.run()
    assert out.verdict == pw.DOWN and out.exit_code == 1
    assert len(h.escalations) == 1 and "repair failed" in h.escalations[0]


def test_failed_repair_escalates_at_once(tmp_path):
    h = Harness(tmp_path, down(), repair_ok=False)
    out = h.run()
    assert out.exit_code == 1 and h.repairs == ["bea"]
    assert len(h.escalations) == 1


def test_unrepairable_waits_the_threshold_then_pages_once(tmp_path):
    h = Harness(tmp_path, *[down(repairable=False)] * 4)
    h.run()
    assert h.escalations == [] and h.repairs == [] and len(h.alerts) == 1
    h.run(advance=10 * 60)                    # 10m: under the 15m threshold
    assert h.escalations == []
    h.run(advance=6 * 60)                     # 16m: over it
    assert len(h.escalations) == 1 and "~16m" in h.escalations[0]
    h.run(advance=5 * 60)                     # the same outage does not re-page
    assert len(h.escalations) == 1


def test_unknown_never_alerts_repairs_or_escalates_however_long(tmp_path):
    h = Harness(tmp_path, *[unknown()] * 5)
    for _ in range(5):
        out = h.run(advance=60 * 60)
        assert out.verdict == pw.UNKNOWN and out.exit_code == 2
    assert h.alerts == h.repairs == h.escalations == []


def test_alerts_and_repairs_are_rate_limited(tmp_path):
    h = Harness(tmp_path, *[down(), down()] * 3, repair_ok=False, esc_rc=0)
    h.run()                                    # alert, repair, page
    h.run(advance=5 * 60)                      # all three suppressed
    assert len(h.alerts) == 1 and len(h.repairs) == 1 and len(h.escalations) == 1
    h.run(advance=31 * 60)                     # repair cooldown over, alert not yet
    assert len(h.repairs) == 2 and len(h.alerts) == 1 and len(h.escalations) == 1


def test_an_escalation_that_paged_nobody_is_retried_after_its_cooldown(tmp_path):
    h = Harness(tmp_path, *[down(repairable=False)] * 3, esc_rc=2)
    h.run(advance=0)
    h.run(advance=16 * 60)                     # past threshold: attempt 1, rc=2
    h.run(advance=10 * 60)                     # in retry cooldown
    assert len(h.escalations) == 1
    h.script.append(down(repairable=False))
    h.run(advance=25 * 60)                     # retry
    assert len(h.escalations) == 2


def test_recovery_ends_the_outage_and_says_so(tmp_path):
    h = Harness(tmp_path, down(repairable=False), up(), down(repairable=False))
    h.run()
    h.run(advance=60)
    assert "back up" in h.alerts[-1]
    h.run(advance=60)                          # a NEW outage alerts again
    assert len(h.alerts) == 3


def test_dry_run_touches_nothing(tmp_path):
    h = Harness(tmp_path, down(repairable=True))
    out = h.run(dry_run=True)
    assert out.actions == ["would-alert", "would-repair"]
    assert h.alerts == h.repairs == h.escalations == []
    assert not h.state.path.exists() and not h.log.exists()


def test_no_repair_flag_skips_to_escalation_rules(tmp_path):
    h = Harness(tmp_path, down(), down())
    h.run(repair_fn=None)
    h.run(advance=16 * 60, repair_fn=None)
    assert h.repairs == [] and len(h.escalations) == 1


# ----------------------------------------------------------------- metrics --

def _outcome(verdict, down_seconds=None, age=None):
    return pw.Outcome(verdict=verdict, observed=verdict, peer="bea", reason="r",
                      actions=[], down_seconds=down_seconds, activity_age=age)


def test_metrics_up_down_and_unknown():
    ok = pw.metrics("ada", _outcome(pw.OK, 0.0, 12.0), 1700000000.0)
    assert 'admin_peer_up{watcher="ada",peer="bea"} 1\n' in ok
    assert 'admin_peer_unknown{watcher="ada",peer="bea"} 0\n' in ok
    assert 'admin_peer_last_activity_age_seconds{watcher="ada",peer="bea"} 12\n' in ok
    assert 'admin_peer_watch_last_run_timestamp_seconds{watcher="ada"} 1700000000\n' in ok
    dn = pw.metrics("ada", _outcome(pw.DOWN, 300.0), 1.0)
    assert 'admin_peer_up{watcher="ada",peer="bea"} 0\n' in dn
    assert 'admin_peer_down_seconds{watcher="ada",peer="bea"} 300\n' in dn
    un = pw.metrics("ada", _outcome(pw.UNKNOWN), 1.0)
    assert "admin_peer_up{" not in un           # unknown is neither up nor down
    assert 'admin_peer_unknown{watcher="ada",peer="bea"} 1\n' in un
    assert "admin_peer_watch_last_run_timestamp_seconds" in un


def test_metrics_write_is_atomic_and_readable(tmp_path):
    path = tmp_path / "tf" / "admin_peer.prom"
    pw.write_metrics(path, "x 1\n")
    assert path.read_text() == "x 1\n"
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    assert [p.name for p in path.parent.iterdir()] == ["admin_peer.prom"]


# --------------------------------------------------------------------- cli --

def _deployment(tmp_path, peers=1):
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "ada.json").write_text(json.dumps(
        {"role": "administrator", "pane": "shanty-ada", "host": "host-a"}))
    (crew / "wu.json").write_text(json.dumps(
        {"role": "lead", "reports_to": "ada", "pane": "shanty-wu", "host": "host-a"}))
    body = '[host]\nname = "host-a"\n'
    for i in range(peers):
        name = "host-b" if i == 0 else f"host-{chr(99 + i)}"
        body += f'\n[host.peers.{name}]\nssh = "user@peer.example"\nroot = "/opt/st/.shanty"\n'
    body += '\n[env]\nSHANTY_ESCALATE_COMMAND = "/opt/esc/escalate.sh"\n'
    (tmp_path / "shantytown.toml").write_text(body)
    return tmp_path


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.delenv("SHANTY_HOST", raising=False)
    calls = {"alert": [], "repair": [], "escalate": []}
    script = []

    def probe(peer, name, **kw):
        calls.setdefault("probe_peer", []).append((peer.name, name))
        return script.pop(0)
    monkeypatch.setattr(pw, "probe", probe)
    monkeypatch.setattr(pw, "alert_via_inbox",
                        lambda root, w, text, **k: (calls["alert"].append((w, text)), (True, "ok"))[1])
    monkeypatch.setattr(pw, "repair",
                        lambda peer, name, **k: (calls["repair"].append(name), (False, "no"))[1])
    monkeypatch.setattr(pw, "escalate_via_command",
                        lambda cmd, sev, desc, **k: (calls["escalate"].append((cmd, sev, desc)), (0, "paged"))[1])
    return calls, script


def test_cli_resolves_watcher_and_peer_and_writes_metrics(tmp_path, wired, capsys):
    from shantytown.cli import main
    calls, script = wired
    root = _deployment(tmp_path)
    script.append(down())
    prom = tmp_path / "tf" / "admin_peer.prom"
    rc = main(["--root", str(root), "fleet", "watch", "--metrics", str(prom)])
    assert rc == 1
    assert calls["probe_peer"] == [("host-b", None)]
    assert calls["alert"][0][0] == "ada"
    assert calls["repair"] == ["bea"]
    assert calls["escalate"][0][:2] == ("/opt/esc/escalate.sh", "high")
    text = prom.read_text()
    assert 'admin_peer_up{watcher="ada",peer="bea"} 0' in text
    assert (root / "peer-watch" / "host-b.json").is_file()


def test_cli_exit_codes_ok_and_unknown(tmp_path, wired):
    from shantytown.cli import main
    calls, script = wired
    root = _deployment(tmp_path)
    script.extend([up(), unknown()])
    assert main(["--root", str(root), "fleet", "watch"]) == 0
    assert main(["--root", str(root), "fleet", "watch"]) == 2
    assert calls["alert"] == calls["escalate"] == []


def test_cli_dry_run_writes_no_metrics_or_state(tmp_path, wired, capsys):
    from shantytown.cli import main
    calls, script = wired
    root = _deployment(tmp_path)
    script.append(down())
    prom = tmp_path / "admin_peer.prom"
    assert main(["--root", str(root), "fleet", "watch", "-n", "--metrics", str(prom)]) == 1
    out = capsys.readouterr().out
    assert "would: alert" in out and "would: repair" in out
    assert not prom.exists() and not (root / "peer-watch").exists()
    assert calls["alert"] == calls["repair"] == []


def test_cli_refuses_to_guess_between_peer_hosts(tmp_path, wired, capsys):
    from shantytown.cli import main
    root = _deployment(tmp_path, peers=2)
    assert main(["--root", str(root), "fleet", "watch"]) == 1
    assert "--peer-host" in capsys.readouterr().err


def test_cli_refuses_without_a_declared_host(tmp_path, wired, capsys):
    from shantytown.cli import main
    root = _deployment(tmp_path)
    (root / "shantytown.toml").write_text("")
    assert main(["--root", str(root), "fleet", "watch"]) == 1
    assert "no [host] name" in capsys.readouterr().err


# ------------------------------------------------ aegis-emretk: sticky sentinel --

def test_a_failed_first_pass_does_not_poison_the_peer_for_every_later_pass(tmp_path):
    """Mac launchd, 2026-09-30 (hammond). The LAN was blocked, the first pass
    could not resolve a peer, and the pass PERSISTED the display sentinel
    "unresolved" as the peer. Every later pass then asked the census for a card
    literally named "unresolved" and reported "no administrator card" forever,
    even after the LAN came back. Only an explicit --peer escaped it."""
    asked = []
    h = Harness(tmp_path,
                pw.Observation(pw.UNKNOWN, "host did not answer ssh", peer=None),
                up())
    h.probe = lambda name: (asked.append(name), h.script.pop(0))[1]
    h.run()
    assert h.state.read().get("peer") in (None, "")   # the sentinel is not stored
    out = h.run(advance=300)
    assert asked == [None, None]                       # auto-resolve is retried
    assert out.verdict == pw.OK


def test_a_state_file_already_poisoned_by_the_old_code_heals(tmp_path):
    asked = []
    h = Harness(tmp_path, up())
    h.state.write({"peer": "unresolved"})
    h.probe = lambda name: (asked.append(name), h.script.pop(0))[1]
    assert h.run().verdict == pw.OK
    assert asked == [None]


def test_a_resolved_peer_is_still_remembered(tmp_path):
    asked = []
    h = Harness(tmp_path, up(), up())
    h.probe = lambda name: (asked.append(name), h.script.pop(0))[1]
    h.run(); h.run(advance=300)
    assert asked == [None, "bea"]                      # CONTROL: real names persist
