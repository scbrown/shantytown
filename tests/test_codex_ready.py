import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from shantytown import codex_ready, harness
from shantytown.protocols import Agent


def command(tmp_path, body):
    script = tmp_path / "fixture.py"
    script.write_text(body)
    return [sys.executable, str(script)]


@pytest.mark.parametrize("payload,rc", [
    ({"status": "connected", "timedOut": False}, 1),
    ({"status": "connected", "timedOut": True}, 0),
    ({"status": "connected"}, 0),
    ({"status": "connecting", "timedOut": False}, 0),
    ({"status": "errored", "timedOut": False}, 0),
    ({"status": "disabled", "timedOut": False}, 0),
    ({"status": "new-upstream-state", "timedOut": False}, 0),
    ([], 0),
])
def test_only_confirmed_connected_status_admits_tui(tmp_path, payload, rc, capsys):
    cmd = command(tmp_path, f"print({json.dumps(payload)!r})\nraise SystemExit({rc})\n")
    assert codex_ready._attempt(cmd, timeout=2) != "connected"
    assert not codex_ready.wait_ready(cmd, timeout=.15, interval=.01)
    assert "TUI not started" in capsys.readouterr().err


def test_malformed_and_sensitive_provider_output_is_not_repeated(tmp_path, capsys):
    cmd = command(tmp_path, "import sys\nprint('PRIVATE-OUTPUT')\nprint('PRIVATE-ERROR',file=sys.stderr)\n")
    # Test the completed response, not which attempt happens to be last at a
    # 150ms deadline. CI can correctly end on a final truncated attempt timeout.
    assert codex_ready._attempt(cmd, timeout=2) == "invalid status response"
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err


def test_hung_command_is_bounded_and_reaped(tmp_path):
    import time
    cmd = command(tmp_path, "import os,time,pathlib\npathlib.Path(__file__+'.pid').write_text(str(os.getpid()))\ntime.sleep(60)\n")
    begin = time.monotonic()
    assert not codex_ready.wait_ready(cmd, timeout=.2, interval=.01)
    assert time.monotonic() - begin < 2
    pid = int(Path(cmd[1]+'.pid').read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("stale_after_stop", [False, True])
def test_emitted_shell_waits_through_transient_status_before_tui(tmp_path, monkeypatch, stale_after_stop):
    # Short runtime path is required by the real Unix socket pathname ceiling.
    # All binaries are new fixture files, never symlinks to tools we might stub.
    with tempfile.TemporaryDirectory(prefix="st-rc-") as runtime:
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("PATH", str(tmp_path / "home/.local/bin") + os.pathsep + os.environ["PATH"])
        monkeypatch.setenv("PYTHONPATH", str(Path(harness.__file__).parent.parent))
        root = tmp_path / "store"
        root.mkdir()
        (root / "shantytown.toml").write_text('[env]\nSHANTY_REMOTE_CONTROL="true"\n')
        config = root / "settings/codex/worker/config.toml"
        managed = config.parent / "packages/standalone/current/codex"
        managed.parent.mkdir(parents=True)
        log = tmp_path / "calls.jsonl"
        monkeypatch.setenv("FIXTURE_CALLS", str(log))
        managed.write_text(f'''#!{sys.executable}
import json,os,pathlib,sys
p=pathlib.Path(os.environ['FIXTURE_CALLS'])
rows=[json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
a=sys.argv[1:]
kind='stop' if a[:2]==['remote-control','stop'] else 'start' if a[:2]==['remote-control','start'] else 'tui'
n=sum(r['kind']=='start' for r in rows)+1
row={{'kind':kind,'agent':os.environ.get('SHANTY_AGENT'),'actor':os.environ.get('BEADS_ACTOR'),'tmux':os.environ.get('TMUX_PANE'),'args':a}}
with p.open('a') as f: f.write(json.dumps(row)+'\\n')
lock=pathlib.Path(os.environ['CODEX_HOME'])/'app-server-control/app-server-startup.lock'
if kind=='stop' and {stale_after_stop!r}:
 lock.parent.mkdir(parents=True,exist_ok=True)
 lock.touch()
 os.utime(lock,(0,0))
 record=lock.parent.parent/'app-server-daemon/app-server.pid'
 record.parent.mkdir(parents=True,exist_ok=True)
 record.write_text(json.dumps({{'pid':os.getpid()}}))
if kind=='start':
 status='errored' if lock.exists() else ['errored','connecting','connected'][min(n-1,2)]
 print(json.dumps({{'status':status,'timedOut':False}}))
 sys.exit(1 if n==1 else 0)
if kind=='tui': sys.exit(0 if n>=4 else 17)
''')
        managed.chmod(0o700)
        monkeypatch.setenv("TMUX_PANE", "%fixture")
        line = harness.get("codex").launch(
            Agent(name="fixture", role="worker", dangerous=True), str(config), root=root)
        # Negative control: the previous pair of immediate starts admits the
        # connecting daemon and our TUI double exits, just like the incident.
        prefix, tail = line.split(f"{shlex.quote(sys.executable)} -m shantytown.codex_ready --agent fixture -- ", 1)
        start, suffix = tail.split(" && ", 1)
        legacy = f"{prefix}({start} || {start}) && {suffix}"
        old = subprocess.run(["sh", "-c", legacy], capture_output=True, text=True, timeout=8)
        assert old.returncode == 17
        assert [json.loads(x)['kind'] for x in log.read_text().splitlines()] == [
            'stop', 'start', 'start', 'tui']
        log.unlink()
        done = subprocess.run(["sh", "-c", line], capture_output=True, text=True, timeout=8)
        assert done.returncode == 0, done.stderr
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        assert [r['kind'] for r in rows] == ['stop','start','start','start','tui']
        for row in rows[1:4]:
            assert row['agent'] == row['actor'] == 'fixture'
            assert row['tmux'] is None
            assert 'sandbox_mode=danger-full-access' in row['args']
            assert 'approval_policy=never' in row['args']
        assert rows[-1]['tmux'] == '%fixture'
        assert "connected; attaching TUI" in done.stderr


def test_post_stop_stale_lock_is_repaired_before_start(tmp_path, monkeypatch):
    """The launcher checked while the old server was healthy; stop came later."""
    from shantytown import codex_daemon
    proc = tmp_path / 'proc'
    proc.mkdir()
    runtime = tmp_path / 'run'
    home = runtime / 'shantytown/codex/fixture'
    lock = home / 'app-server-control/app-server-startup.lock'
    lock.parent.mkdir(parents=True)
    lock.touch()
    record = home / 'app-server-daemon/app-server.pid'
    record.parent.mkdir()
    record.write_text('{"pid": 101}')
    # This is the actual repair algorithm, restricted to a fake proc/runtime.
    repair = codex_daemon.repair
    monkeypatch.setattr(codex_daemon, 'repair', lambda agent: repair(
        agent, proc=proc, runtime_dir=runtime,
        kill=lambda *args: pytest.fail('absent PID must never be signalled')))
    observed = []
    def attempt(command, timeout):
        observed.append(lock.exists())
        return 'command timed out' if lock.exists() else 'connected'
    monkeypatch.setattr(codex_ready, '_attempt', attempt)
    # Negative control: the same start cannot succeed while repair is a no-op.
    actual_repair = codex_daemon.repair
    monkeypatch.setattr(codex_daemon, 'repair', lambda agent: codex_daemon.Health(agent))
    assert not codex_ready.wait_ready(['fake'], agent='fixture', timeout=.02, interval=.01)
    assert observed and all(observed) and lock.exists()
    observed.clear()
    monkeypatch.setattr(codex_daemon, 'repair', actual_repair)
    assert codex_ready.wait_ready(['fake'], agent='fixture', timeout=.1, interval=.01)
    assert observed == [False]
    assert not lock.exists()


def test_ambiguous_fresh_lock_is_preserved_and_cannot_admit_tui(tmp_path, monkeypatch):
    from shantytown import codex_daemon
    proc = tmp_path / 'proc'
    (proc / '101').mkdir(parents=True)  # PID exists; ownership is unknown.
    runtime = tmp_path / 'run'
    home = runtime / 'shantytown/codex/fixture'
    lock = home / 'app-server-control/app-server-startup.lock'
    lock.parent.mkdir(parents=True)
    lock.touch()
    record = home / 'app-server-daemon/app-server.pid'
    record.parent.mkdir()
    record.write_text('{"pid": 101}')
    repair = codex_daemon.repair
    monkeypatch.setattr(codex_daemon, 'repair', lambda agent: repair(
        agent, proc=proc, runtime_dir=runtime,
        kill=lambda *args: pytest.fail('unknown PID must never be signalled')))
    monkeypatch.setattr(codex_ready, '_attempt', lambda *args: 'command timed out')
    assert not codex_ready.wait_ready(['fake'], agent='fixture', timeout=.02, interval=.01)
    assert lock.exists()
