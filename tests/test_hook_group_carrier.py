"""Execute the launcher: hook children must inherit the configured group."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from shantytown import harness
from shantytown.protocols import Agent
from shantytown.runtime import _CARRIED_ENV, claude_settings_for_role


_STUB = r'''
import json, os, subprocess, sys
args = sys.argv[1:]
if args[:2] == ["remote-control", "stop"]:
    print("{}")
    raise SystemExit(0)
kind = "daemon" if args[:2] == ["remote-control", "start"] else "client"
child = json.loads(subprocess.check_output([
    sys.executable, "-c", "import os,json; print(json.dumps(os.environ.get('QUIPU_HOOK_GROUP')))"
], text=True))
with open(os.environ["GROUP_PROBE_LOG"], "a") as output:
    output.write(json.dumps({"kind": kind, "group": child}) + "\n")
if kind == "daemon":
    print(json.dumps({"status": "connected", "timedOut": False}))
'''


@pytest.mark.parametrize("program", ["claude", "codex", "codex-remote"])
@pytest.mark.parametrize("selection", ["configured", "ambient", "absent"])
def test_hook_child_group_from_launch(tmp_path, monkeypatch, program, selection):
    for key in _CARRIED_ENV:
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    root = tmp_path / "root"
    root.mkdir()
    remote = program == "codex-remote"
    config = '[env]\nSHANTY_REMOTE_CONTROL=' + json.dumps(str(remote).lower()) + '\n'
    marker = tmp_path / "must-not-execute"
    expected = None
    if selection == "configured":
        expected = f"capture group 'quoted' $(touch {marker})"
        config += 'QUIPU_HOOK_GROUP=' + json.dumps(expected) + '\n'
        monkeypatch.setenv("QUIPU_HOOK_GROUP", "wrong-parent-group")
    elif selection == "ambient":
        expected = "ambient-capture"
        monkeypatch.setenv("QUIPU_HOOK_GROUP", expected)
    (root / "shantytown.toml").write_text(config)
    log = tmp_path / "children.jsonl"
    monkeypatch.setenv("GROUP_PROBE_LOG", str(log))
    bins = tmp_path / "bin"
    bins.mkdir()
    script = f"#!{sys.executable}\n" + _STUB
    for name in ("claude", "codex"):
        exe = bins / name
        exe.write_text(script)
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bins) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONPATH", str(Path(harness.__file__).resolve().parent.parent))
    settings = root / "settings" / "codex" / "worker" / "config.toml"
    if remote:
        settings.parent.mkdir(parents=True)
        settings.write_text("")
        (settings.parent / "auth.json").write_text("{}")
        managed = settings.parent / "packages" / "standalone" / "current" / "codex"
        managed.parent.mkdir(parents=True)
        managed.write_text(script)
        managed.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix="group-env-") as runtime:
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime)
        command = harness.get("codex" if remote else program).launch(
            Agent(name="probe", role="worker"), str(settings), root=root)
        result = subprocess.run(["sh", "-c", command], capture_output=True,
                                text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["kind"] for r in rows] == (["daemon", "client"] if remote else ["client"])
    assert all(r["group"] == expected for r in rows)
    assert not marker.exists(), "configured group executed shell substitution"
    env = claude_settings_for_role("worker", root)["env"]
    assert env.get("QUIPU_HOOK_GROUP") == expected
