"""Codex rollouts must not depend on winning a race against a reboot.

aegis-tx4fiy, the root-cause end of the aegis-xfmon3 chain.

A card's CODEX_HOME is under XDG_RUNTIME_DIR -- tmpfs, i.e. RAM. Capture (the
Stop hook, and since aegis-ay3gv2 the kill paths) copies rollouts out, so
durability depended on a capture running before the machine went down. These
tests pin the inversion: `sessions/` is redirected to durable state, so the
record is durable by default and no capture has to win anything.
"""
from __future__ import annotations
import os
import subprocess
from pathlib import Path

import pytest

from shantytown import harness as harness_mod
from shantytown.harness import codex_sessions_setup

CODEX = harness_mod.get("codex")
from shantytown.protocols import Agent


def _run(snippet: str) -> None:
    subprocess.run(["sh", "-c", snippet], check=False,
                   capture_output=True, text=True, timeout=60)


def test_a_fresh_home_gets_sessions_linked_to_durable_state(tmp_path):
    dh, dur = tmp_path / "run" / "ellie", tmp_path / "state" / "ellie" / "sessions"
    dh.mkdir(parents=True)
    _run(codex_sessions_setup(dh, dur))
    assert (dh / "sessions").is_symlink()
    assert (dh / "sessions").resolve() == dur.resolve()


def test_an_EXISTING_tmpfs_sessions_directory_is_migrated_not_nested(tmp_path):
    """THE TRAP. `ln -sfn` onto a real directory does not replace it -- it links
    INSIDE it. Every card that has already run codex has exactly that directory,
    so without the migration the rollouts keep going to tmpfs and nothing looks
    wrong."""
    dh, dur = tmp_path / "run" / "ellie", tmp_path / "state" / "ellie" / "sessions"
    old = dh / "sessions" / "2026" / "09" / "01"
    old.mkdir(parents=True)
    (old / "rollout-OLD.jsonl").write_text('{"pre":"existing"}')

    _run(codex_sessions_setup(dh, dur))

    assert (dh / "sessions").is_symlink(), "the real directory was not replaced"
    assert not (dh / "sessions" / "sessions").exists(), \
        "ln -sfn nested the link inside the directory — rollouts still on tmpfs"
    moved = dur / "2026" / "09" / "01" / "rollout-OLD.jsonl"
    assert moved.is_file(), "the existing rollout was not migrated"
    assert moved.read_text() == '{"pre":"existing"}', "content changed in migration"


def test_the_migration_never_overwrites_an_already_durable_rollout(tmp_path):
    """`cp -an`: no-clobber. The durable copy is the one that has survived, so a
    tmpfs file of the same name must never overwrite it."""
    dh, dur = tmp_path / "run" / "ellie", tmp_path / "state" / "ellie" / "sessions"
    (dh / "sessions").mkdir(parents=True)
    (dh / "sessions" / "r.jsonl").write_text("from tmpfs")
    dur.mkdir(parents=True)
    (dur / "r.jsonl").write_text("already durable")

    _run(codex_sessions_setup(dh, dur))

    assert (dur / "r.jsonl").read_text() == "already durable"


def test_it_is_idempotent(tmp_path):
    dh, dur = tmp_path / "run" / "ellie", tmp_path / "state" / "ellie" / "sessions"
    (dh / "sessions").mkdir(parents=True)
    (dh / "sessions" / "r.jsonl").write_text("x")
    for _ in range(3):
        _run(codex_sessions_setup(dh, dur))
    assert (dh / "sessions").is_symlink()
    assert (dur / "r.jsonl").read_text() == "x"
    assert not (dh / "sessions" / "sessions").exists()


def test_it_can_never_fail_a_launch(tmp_path):
    """NEGATIVE CONTROL. A durability improvement that can stop an agent coming
    up is worse than the exposure it closes -- the same doctrine the startup
    inbox follows. Point it somewhere unwritable and it must still exit 0."""
    snippet = codex_sessions_setup(Path("/proc/nonexistent/ellie"),
                                   Path("/proc/nonexistent/state/sessions"))
    done = subprocess.run(["sh", "-c", snippet], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, "a failed setup would break the launch chain"


def test_the_durable_path_is_NOT_under_the_runtime_dir(tmp_path, monkeypatch):
    """The whole point. If the destination were still under XDG_RUNTIME_DIR this
    would be an elaborate no-op."""
    root = tmp_path / ".shanty"; root.mkdir()
    (root / "shantytown.toml").write_text(
        '[env]\nSHANTY_REMOTE_CONTROL = "true"\n')
    cfg = root / "settings" / "codex" / "worker" / "config.toml"
    managed = cfg.parent / "packages" / "standalone" / "current" / "codex"
    managed.parent.mkdir(parents=True); managed.write_text("")

    launch = CODEX.launch(Agent(name="ellie", role="worker"), str(cfg), root=root)

    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", Path.home() / ".cache"))
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    durable = state / "shantytown" / "codex" / "ellie" / "sessions"
    assert f"ln -sfn {durable}" in launch
    assert str(durable).startswith(str(state))
    assert not str(durable).startswith(str(runtime)), \
        "sessions still resolve under the runtime dir — nothing was made durable"


def test_the_setup_runs_after_stop_and_before_start(tmp_path):
    """Migrating a directory the old daemon still holds open is how you get a
    half-copied record."""
    root = tmp_path / ".shanty"; root.mkdir()
    (root / "shantytown.toml").write_text(
        '[env]\nSHANTY_REMOTE_CONTROL = "true"\n')
    cfg = root / "settings" / "codex" / "worker" / "config.toml"
    managed = cfg.parent / "packages" / "standalone" / "current" / "codex"
    managed.parent.mkdir(parents=True); managed.write_text("")

    launch = CODEX.launch(Agent(name="ellie", role="worker"), str(cfg), root=root)

    # Anchor on the DURABLE link specifically: the bootstrap ahead of `stop`
    # already emits `ln -sfn` three times (config.toml, auth.json, packages), so
    # a bare search finds one of those and proves nothing about this one.
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    mine = f"ln -sfn {state / 'shantytown' / 'codex' / 'ellie' / 'sessions'}"
    assert (launch.index("remote-control stop")
            < launch.index(mine)
            < launch.index("remote-control start"))


def test_capture_follows_the_sessions_symlink(tmp_path):
    """Shipping the redirect without this makes capture silently archive
    NOTHING for codex: plain `find` does not descend a symlinked directory, and
    the sweep still reports success."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "st-history-capture.sh"
    codex_root = tmp_path / "run" / "codex"
    real = tmp_path / "state" / "ellie" / "sessions" / "2026" / "09" / "01"
    real.mkdir(parents=True)
    (real / "rollout-X.jsonl").write_text('{"a":1}')
    (codex_root / "ellie").mkdir(parents=True)
    (codex_root / "ellie" / "sessions").symlink_to(real.parents[2])

    dest = tmp_path / "hist"
    done = subprocess.run(
        [str(script)], capture_output=True, text=True, timeout=120,
        env={**os.environ, "ST_HISTORY_DIR": str(dest),
             "ST_CODEX_ROOT": str(codex_root),
             "ST_CLAUDE_ROOT": str(tmp_path / "no-claude")})
    assert done.returncode == 0, done.stderr
    found = list(dest.rglob("rollout-X.jsonl"))
    assert found, f"capture did not follow the symlink: {done.stdout}"
