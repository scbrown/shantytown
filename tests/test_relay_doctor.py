"""The SSH symptom needs a failing incoming environment and a repaired control."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

import shantytown
from shantytown import cli, relay_doctor


@pytest.fixture
def relay_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "fleet ' $(false)" / ".shanty"
    root.mkdir(parents=True)
    bindir = home / ".local" / "bin"
    bindir.mkdir(parents=True)
    for name in ("st", "tmux"):
        path = bindir / name
        path.write_text("#!/bin/sh\nexit 99\n")
        path.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("SHANTY_ROOT", str(root))
    monkeypatch.setenv("SHANTY_BACKEND", "files")
    return root


def test_healthy_does_not_run_tools_or_open_tracker(relay_env, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("relay doctor must not execute a tool or open a tracker")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(shantytown, "deployed_sha", lambda: "test")
    monkeypatch.setattr(cli, "_tracker", forbidden)
    assert cli.main(["--root", str(relay_env), "ops", "doctor", "--relay"]) == 0
    out = capsys.readouterr().out
    assert "OK PATH/tmux" in out and "OK SHANTY_BACKEND" in out
    assert not list(relay_env.iterdir())


@pytest.mark.parametrize("missing", ["PATH", "SHANTY_ROOT"])
def test_each_missing_export_fails_despite_deployment_config(relay_env, monkeypatch, missing):
    # Config must not silently repair the input before doctor sees it.
    (relay_env / "shantytown.toml").write_text('[env]\nSHANTY_BACKEND = "files"\n')
    monkeypatch.delenv(missing)
    assert cli.main(["--root", str(relay_env), "ops", "doctor", "--relay"]) == 1


@pytest.mark.parametrize("key,value", [
    ("SHANTY_ROOT", "relative/.shanty"),
    ("SHANTY_ROOT", "/nonexistent-relay-root"),
    ("SHANTY_BACKEND", "typo"),
])
def test_wrong_exports_fail(relay_env, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    assert relay_doctor.check(relay_env)[0] == 1


def test_backend_conflict_is_not_healthy(relay_env):
    (relay_env / "shantytown.toml").write_text('[env]\nSHANTY_BACKEND = "br"\n')
    code, report = relay_doctor.check(relay_env, backend="files")
    assert code == 1
    assert "conflicts" in report


def test_bad_config_is_unknown(relay_env):
    (relay_env / "shantytown.toml").write_text("[broken")
    code, report = relay_doctor.check(relay_env)
    assert code == 2 and "UNKNOWN deployment config" in report


def test_unified_recipe_repairs_scratch_environment_and_quotes_paths(relay_env, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("SHANTY_ROOT")
    monkeypatch.delenv("SHANTY_BACKEND")
    code, report = relay_doctor.check(relay_env)
    assert code == 1
    lines = [line.strip() for line in report.splitlines() if line.startswith("  export ")]
    assert len(lines) == 3
    result = subprocess.run(["/bin/sh", "-c", "\n".join(lines) + "\n/usr/bin/env"],
                            env={"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"},
                            text=True, capture_output=True, check=True)
    repaired = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert repaired["SHANTY_ROOT"] == str(relay_env)
    for name in ("PATH", "SHANTY_ROOT", "SHANTY_BACKEND"):
        monkeypatch.setenv(name, repaired[name])
    assert relay_doctor.check(relay_env)[0] == 0
    assert not (Path(os.environ["HOME"]) / ".beads").exists()


def test_macos_recipe_has_both_homebrew_prefixes(relay_env, monkeypatch):
    monkeypatch.setattr(relay_doctor.sys, "platform", "darwin")
    _, report = relay_doctor.check(relay_env)
    assert "/opt/homebrew/bin:/usr/local/bin" in report


@pytest.mark.parametrize("extra", [["--install"], ["--dry-run"], ["bobbin"]])
def test_relay_refuses_unrelated_modes(relay_env, extra):
    assert cli.main(["--root", str(relay_env), "ops", "doctor", "--relay", *extra]) == 1


def test_scratch_subprocess_reproduces_noninteractive_ssh_environment(relay_env):
    env = {"HOME": os.environ["HOME"], "PATH": str(relay_env / "missing-bin"),
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    result = subprocess.run(
        [sys.executable, "-c", "from shantytown.cli import main; raise SystemExit(main())",
         "--root", str(relay_env), "ops", "doctor", "--relay"],
        cwd=env["HOME"], env=env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 1, result.stderr
    for name in ("PATH/st", "PATH/tmux", "SHANTY_ROOT", "SHANTY_BACKEND"):
        assert "MISSING/WRONG " + name in result.stdout
    assert not list(relay_env.iterdir())
    assert not (Path(env["HOME"]) / ".beads").exists()


@pytest.mark.parametrize("incoming", [None, "beads", "br", "typo"])
def test_declared_backend_matches_actual_resolver_without_duplicate_export(relay_env, monkeypatch, incoming):
    from argparse import Namespace
    (relay_env / "shantytown.toml").write_text('[env]\nSHANTY_BACKEND = "br"\n')
    (relay_env / "env.json").write_text('{"SHANTY_BACKEND":"beads"}')
    if incoming is None:
        monkeypatch.delenv("SHANTY_BACKEND")
    else:
        monkeypatch.setenv("SHANTY_BACKEND", incoming)
    assert cli._backend(Namespace(root=relay_env, backend=None)) == "br"
    code, report = relay_doctor.check(relay_env)
    assert code == 0
    assert "shantytown.toml [env]" in report
    assert "  export SHANTY_BACKEND=" not in report
    if incoming and incoming != "br":
        assert "Remove the redundant shell export" in report


def test_backend_still_required_without_declaration(relay_env, monkeypatch):
    monkeypatch.delenv("SHANTY_BACKEND")
    assert relay_doctor.check(relay_env)[0] == 1


def test_unknown_declared_backend_refuses(relay_env):
    (relay_env / "shantytown.toml").write_text('[env]\nSHANTY_BACKEND = "typo"\n')
    assert relay_doctor.check(relay_env)[0] == 1
