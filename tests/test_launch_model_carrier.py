"""The emitted shell must carry the selected model into every child process."""
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


_STUB = r'''
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
if args[:2] == ["remote-control", "stop"]:
    print("{}")
    raise SystemExit(0)
kind = "daemon" if args[:2] == ["remote-control", "start"] else "client"
row = {"kind": kind, "model": os.environ.get("SHANTY_MODEL"), "args": args}
if kind == "daemon":
    row["tool_model"] = json.loads(subprocess.check_output([
        sys.executable, "-c",
        "import os,json; print(json.dumps(os.environ.get('SHANTY_MODEL')))"
    ], text=True))
with open(os.environ["MODEL_PROBE_LOG"], "a") as output:
    output.write(json.dumps(row) + "\n")
if kind == "daemon":
    print(json.dumps({"status": "connected", "timedOut": False}))
'''


@pytest.mark.parametrize("program", ["claude", "codex", "codex-remote"])
@pytest.mark.parametrize("selection", ["card", "role", "fleet", "absent", "quoted"])
def test_model_matches_flag_and_reaches_daemon_tools(tmp_path, monkeypatch,
                                                    program, selection):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # Long pytest case names must not consume the Unix socket path budget.
    with tempfile.TemporaryDirectory(prefix="model-env-") as runtime:
        monkeypatch.setenv("XDG_RUNTIME_DIR", runtime)
        _exercise(tmp_path, monkeypatch, program, selection)


def _exercise(tmp_path, monkeypatch, program, selection):
    root = tmp_path / ".shanty"
    root.mkdir()
    remote = program == "codex-remote"
    config = '[env]\nSHANTY_REMOTE_CONTROL = "' + str(remote).lower() + '"\n'
    role = "administrator" if program == "claude" else "worker"
    declared = None
    expected = None
    marker = tmp_path / "must-not-execute"
    if selection == "quoted":
        declared = f"fixture model $(touch {marker})"
        expected = declared
    elif selection == "card":
        declared = expected = "card-model"
    if selection != "absent":
        config += '[model]\ndefault = "fleet-model"\n'
        if selection != "fleet":
            config += f'[model.by_role]\n{role} = "role-model"\n'
        if selection == "role":
            expected = "role-model"
        elif selection == "fleet":
            expected = "fleet-model"
    (root / "shantytown.toml").write_text(config)
    log = tmp_path / "children.jsonl"
    monkeypatch.setenv("MODEL_PROBE_LOG", str(log))
    # An unconfigured new card must not claim the launching agent's model.
    monkeypatch.setenv("SHANTY_MODEL", "stale-parent-model")
    bins = tmp_path / "bin"
    bins.mkdir()
    script = f"#!{sys.executable}\n" + _STUB
    for name in ("claude", "codex"):
        executable = bins / name
        executable.write_text(script)
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bins) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONPATH", str(Path(harness.__file__).resolve().parent.parent))
    settings = root / "settings" / "codex" / role / "config.toml"
    if remote:
        settings.parent.mkdir(parents=True)
        settings.write_text("")
        (settings.parent / "auth.json").write_text("{}")
        managed = settings.parent / "packages" / "standalone" / "current" / "codex"
        managed.parent.mkdir(parents=True)
        managed.write_text(script)  # A fixture file, never a link to a live tool.
        managed.chmod(0o755)
    card = Agent(name="probe", role=role, model=declared)
    command = harness.get("claude" if program == "claude" else "codex").launch(
        card, str(settings), root=root)
    completed = subprocess.run(["sh", "-c", command], capture_output=True,
                               text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [row["kind"] for row in rows] == (["daemon", "client"] if remote else ["client"])
    for row in rows:
        assert row["model"] == expected
    client = rows[-1]
    if expected is None:
        assert "--model" not in client["args"]
    else:
        assert client["args"][client["args"].index("--model") + 1] == expected
    if remote:
        assert rows[0]["tool_model"] == expected
    assert not marker.exists(), "model data was executed by the launch shell"
