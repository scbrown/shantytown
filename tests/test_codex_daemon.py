import json
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


def _app_pid(runtime, agent, pid):
    path = runtime / f"shantytown/codex/{agent}/app-server-daemon/app-server.pid"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": pid, "processStartTime": "fixture"}))


def test_real_control_server_argv_detects_and_repairs_per_agent_zombie(tmp_path):
    proc = tmp_path / "proc"
    runtime = tmp_path / "run"
    proc.mkdir()
    real_argv = "codex app-server --remote-control --listen unix://"
    _proc(proc, 101, cmd=real_argv,
          env={"SHANTY_AGENT": "kelly"})
    _proc(proc, 102, cmd="codex app-server", env={}, state="Z", ppid=101)
    _proc(proc, 201, cmd=real_argv,
          env={"SHANTY_AGENT": "ian"})
    _proc(proc, 202, cmd="codex app-server", env={}, state="Z", ppid=201)
    _app_pid(runtime, "kelly", 101)
    _app_pid(runtime, "ian", 201)
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
    assert lock.exists(), "an old lock beside a live server is not stale"
    assert all(pid != 201 for pid, _ in killed), "another card's daemon is untouchable"


def test_healthy_fifteen_minute_old_lock_is_not_a_blocker(tmp_path):
    proc = tmp_path / "proc"
    runtime = tmp_path / "run"
    proc.mkdir()
    _proc(proc, 101, cmd="codex app-server --remote-control --listen unix://",
          env={"SHANTY_AGENT": "kelly"})
    _app_pid(runtime, "kelly", 101)
    lock = (runtime / "shantytown/codex/kelly/app-server-control" /
            "app-server-startup.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.utime(lock, (100, 100))
    assert not codex_daemon.inspect(
        "kelly", runtime_dir=runtime, proc=proc, now=1000).blocked


def test_old_lock_with_recorded_dead_control_server_is_repaired(tmp_path):
    proc = tmp_path / "proc"
    runtime = tmp_path / "run"
    proc.mkdir()
    _app_pid(runtime, "kelly", 101)
    lock = (runtime / "shantytown/codex/kelly/app-server-control" /
            "app-server-startup.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.utime(lock, (1, 1))

    found = codex_daemon.inspect("kelly", runtime_dir=runtime, proc=proc, now=1000)
    assert found.blocked
    assert found.daemon_pids == ()
    assert found.stale_lock == lock

    codex_daemon.repair("kelly", runtime_dir=runtime, proc=proc, now=1000)
    assert not lock.exists()
