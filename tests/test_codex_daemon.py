import os
import signal

from shantytown import codex_daemon


def _proc(root, pid, *, cmd, env, state="S", ppid=1):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")
    (d / "environ").write_bytes(
        b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")
    (d / "stat").write_text(f"{pid} (codex) {state} {ppid} 0 0 0 0\n")


def test_detect_and_fix_are_per_agent_and_clear_only_the_stale_lock(tmp_path):
    proc = tmp_path / "proc"
    runtime = tmp_path / "run"
    proc.mkdir()
    _proc(proc, 101, cmd="codex app-server daemon pid-update-loop",
          env={"SHANTY_AGENT": "kelly"})
    _proc(proc, 102, cmd="codex app-server", env={}, state="Z", ppid=101)
    _proc(proc, 201, cmd="codex app-server daemon pid-update-loop",
          env={"SHANTY_AGENT": "ian"})
    _proc(proc, 202, cmd="codex app-server", env={}, state="Z", ppid=201)
    lock = (runtime / "shantytown/codex/kelly/app-server-control" /
            "app-server-startup.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.utime(lock, (1, 1))

    found = codex_daemon.inspect("kelly", runtime_dir=runtime, proc=proc, now=1000)
    assert found.blocked
    assert found.daemon_pids == (101,)
    assert found.zombie_pids == (102,)
    assert codex_daemon.FLAG == "codex-daemon-wedged"

    killed = []
    fixed = codex_daemon.repair(
        "kelly", runtime_dir=runtime, proc=proc, now=1000,
        kill=lambda pid, sig: killed.append((pid, sig)))
    assert fixed.blocked
    assert killed == [(101, signal.SIGTERM)]
    assert not lock.exists()
    assert all(pid != 201 for pid, _ in killed), "another card's daemon is untouchable"


def test_a_fresh_lock_without_a_zombie_is_not_a_blocker(tmp_path):
    proc = tmp_path / "proc"
    runtime = tmp_path / "run"
    proc.mkdir()
    _proc(proc, 101, cmd="codex app-server daemon pid-update-loop",
          env={"SHANTY_AGENT": "kelly"})
    _proc(proc, 102, cmd="codex app-server", env={}, state="S", ppid=101)
    lock = (runtime / "shantytown/codex/kelly/app-server-control" /
            "app-server-startup.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.utime(lock, (999, 999))
    assert not codex_daemon.inspect(
        "kelly", runtime_dir=runtime, proc=proc, now=1000).blocked
