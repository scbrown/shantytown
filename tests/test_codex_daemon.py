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


def test_a_FRESH_lock_is_stale_when_the_recorded_pid_does_not_EXIST(tmp_path):
    """aegis-h5wki0 defect 1, reproduced: the repair would not fire inside ten
    minutes, so `st stop` + `st new` — the one sequence an operator actually runs
    — could not clear the lock it had just been blocked by.

    The recovery was to fail once, wait, and retry, i.e. to let the clock pass the
    age gate. Here the recorded control-server PID is not in /proc at all, which
    no amount of waiting can make more true: nothing is starting up under a PID
    that does not exist. The lock is conclusively stale at ANY age.
    """
    proc = tmp_path / "proc"
    runtime = tmp_path / "run"
    proc.mkdir()
    # Another card's live daemon, to prove the verdict is not "any missing pid".
    _proc(proc, 999, cmd="codex app-server --remote-control --listen unix://",
          env={"SHANTY_AGENT": "ian"})
    _app_pid(runtime, "kelly", 101)          # recorded...
    _app_pid(runtime, "ian", 999)
    lock = (runtime / "shantytown/codex/kelly/app-server-control" /
            "app-server-startup.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.utime(lock, (995, 995))               # ...5 seconds old: far inside the gate

    found = codex_daemon.inspect("kelly", runtime_dir=runtime, proc=proc, now=1000)
    assert found.blocked, "a recorded pid that does not exist needs no waiting"
    assert found.stale_lock == lock

    killed = []
    codex_daemon.repair("kelly", runtime_dir=runtime, proc=proc, now=1000,
                        kill=lambda pid, sig: killed.append((pid, sig)))
    assert not lock.exists()
    # THE POINT: this narrows the WAIT, it must not widen the KILL.
    assert killed == [], "nothing may be signalled on the newly-admitted path"
    assert (proc / "999").exists(), "another card's daemon is untouchable"


def test_a_FRESH_lock_beside_a_LIVE_unidentifiable_pid_still_WAITS(tmp_path):
    """The other half of the same gate, and the reason the age clause stays.

    A recorded PID that EXISTS but does not yet look like an owned control server
    is the ambiguous world: it may be this card's app-server mid-exec, before its
    argv and environ are readable. Killing that is the failure the delay exists to
    prevent, so an in-window lock beside a live PID must NOT be a blocker.
    """
    proc = tmp_path / "proc"
    runtime = tmp_path / "run"
    proc.mkdir()
    _proc(proc, 101, cmd="", env={})          # exists; argv/environ not yet readable
    _app_pid(runtime, "kelly", 101)
    lock = (runtime / "shantytown/codex/kelly/app-server-control" /
            "app-server-startup.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.utime(lock, (995, 995))

    assert not codex_daemon.inspect(
        "kelly", runtime_dir=runtime, proc=proc, now=1000).blocked

    # ...and the age clause still catches it once waiting HAS happened.
    os.utime(lock, (1, 1))
    assert codex_daemon.inspect(
        "kelly", runtime_dir=runtime, proc=proc, now=1000).blocked


def test_an_UNREADABLE_proc_does_not_read_as_the_pid_being_gone(tmp_path):
    """An unreadable /proc yields the same empty pid list as "that process is
    gone", and the two have opposite meanings. Absence of evidence must fall back
    to waiting, not to the conclusive branch."""
    proc = tmp_path / "no-such-proc"           # iterdir raises OSError
    runtime = tmp_path / "run"
    _app_pid(runtime, "kelly", 101)
    lock = (runtime / "shantytown/codex/kelly/app-server-control" /
            "app-server-startup.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    os.utime(lock, (995, 995))

    assert not codex_daemon.inspect(
        "kelly", runtime_dir=runtime, proc=proc, now=1000).blocked


def test_stop_owned_reaps_server_and_updater_for_only_one_card(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    server = "codex app-server --remote-control --listen unix://"
    updater = "codex app-server daemon pid-update-loop"
    _proc(proc, 101, cmd=server, env={"SHANTY_AGENT": "kelly"})
    _proc(proc, 102, cmd=updater, env={"SHANTY_AGENT": "kelly"})
    _proc(proc, 201, cmd=server, env={"SHANTY_AGENT": "ian"})
    _proc(proc, 202, cmd=updater, env={"SHANTY_AGENT": "ian"})
    _proc(proc, 301, cmd="codex", env={"SHANTY_AGENT": "kelly"})

    killed = []
    stopped = codex_daemon.stop_owned(
        "kelly", proc=proc,
        kill=lambda pid, sig: killed.append((pid, sig)))

    assert stopped == (101, 102)
    assert killed == [(101, signal.SIGTERM), (102, signal.SIGTERM)]
