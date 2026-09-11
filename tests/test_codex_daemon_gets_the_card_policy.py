"""The card's permission opt-in must reach the DAEMON, not only the TUI
(aegis-7okaae).

Under Remote Control the model's shell commands execute in the app-server, and
`codex app-server` accepts NEITHER --dangerously-bypass-approvals-and-sandbox NOR
-s/--sandbox — only -c. So the client flag governed a process that was not the
one running the tools, and the daemon silently kept codex's default
`workspace-write`.

MEASURED 2026-09-11 with `codex sandbox`, from a crew workspace, both arms:

    workspace-write     touch <live br store>   -> Read-only file system
                        getent hosts github.com -> DNS-FAIL
    danger-full-access  touch <live br store>   -> WROTE-OK
                        getent hosts github.com -> DNS-OK

The live br store sits OUTSIDE every crew workspace, so `br close`,
`br comments add` and `st inbox -d` all failed from every codex session: three
dispatches in one day reported work done that could not be landed or closed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shantytown import harness as harness_mod

CODEX = harness_mod.get("codex")
from shantytown.protocols import Agent


def _launch(tmp_path, *, dangerous: bool) -> str:
    root = tmp_path / ".shanty"
    root.mkdir()
    (root / "shantytown.toml").write_text(
        '[env]\nSHANTY_REMOTE_CONTROL = "true"\n')
    cfg = root / "settings" / "codex" / "worker" / "config.toml"
    managed = cfg.parent / "packages" / "standalone" / "current" / "codex"
    managed.parent.mkdir(parents=True)
    managed.write_text("")
    return CODEX.launch(
        Agent(name="ellie", role="worker", workspace=str(tmp_path / "w"),
              dangerous=dangerous),
        str(cfg), root=root)


def test_a_dangerous_card_sets_the_policy_ON_THE_DAEMON(tmp_path):
    launch = _launch(tmp_path, dangerous=True)
    assert 'remote-control start -c sandbox_mode="danger-full-access"' in launch, (
        "the daemon start must carry the policy; the TUI flag cannot reach the "
        "process that actually runs the tools")
    assert '-c approval_policy="never"' in launch
    # the TUI flag is still there — this ADDS the daemon half, it does not move it
    assert "--dangerously-bypass-approvals-and-sandbox" in launch


def test_an_ORDINARY_card_leaves_the_sandbox_exactly_as_it_was(tmp_path):
    """THE CONTROL, and the reason this is opt-in.

    Without it, hardcoding the override would silently un-sandbox every codex
    worker on the fleet — the opposite of the per-card contract this repo keeps.
    """
    launch = _launch(tmp_path, dangerous=False)
    assert "sandbox_mode" not in launch
    assert "approval_policy" not in launch
    assert "--dangerously-bypass-approvals-and-sandbox" not in launch
    # and the daemon still starts, unchanged
    assert "codex remote-control start --json" in launch
