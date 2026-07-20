"""role set EMITS the settings.json — the content st new's --settings reads.
shantytown #6 (aegis-ct5q, arnold's ruling). Closes the launch/hooks loop:
declaring a role writes its stop hooks in the SAME operation as the card, and
st new then finds the settings it refused for a moment ago.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import pytest

from shantytown import cli
from shantytown.cli import main, OK
from shantytown.runtime import settings_for_role
from shantytown.tmux import NullPanes


# --- the settings CONTENT per role (send / receive from the same fact) ----------

def _stop_commands(settings: dict) -> list[str]:
    hooks = settings["hooks"]["Stop"][0]["hooks"]
    return [h["command"] for h in hooks]


_PY = sys.executable or "python3"


def test_worker_settings_send_only():
    cmds = _stop_commands(settings_for_role("worker"))
    assert cmds == [f"{_PY} -m shantytown.stop_event send"]


def test_lead_settings_send_and_drain():
    """A lead sends its OWN stop up AND drains its reports' — send + drain."""
    cmds = _stop_commands(settings_for_role("lead"))
    assert cmds == [f"{_PY} -m shantytown.stop_event send",
                    f"{_PY} -m shantytown.stop_event drain"]


def test_administrator_settings_drain_only():
    """Root: receives only; its own stop terminates (no send)."""
    cmds = _stop_commands(settings_for_role("administrator"))
    assert cmds == [f"{_PY} -m shantytown.stop_event drain"]


def test_hook_interpreter_actually_exists():
    """The emitted hook must be RUNNABLE, not merely well-formed.

    Regression guard for the live-use bug: the command hardcoded the bare name
    `python`, which does not exist on stock Ubuntu (python3 only), so every Stop
    hook died with `/bin/sh: 1: python: not found` and the whole send/drain route
    silently never ran. A settings file whose interpreter is absent is not a
    hook; it is a no-op that reports success.
    """
    for role in ("worker", "lead", "administrator"):
        for cmd in _stop_commands(settings_for_role(role)):
            interp = cmd.split()[0]
            assert os.path.isabs(interp) and os.path.exists(interp), (
                f"{role} hook interpreter {interp!r} does not exist"
            )


def test_every_role_pre_answers_the_project_mcp_prompt():
    """A fresh workspace asks 'N new MCP servers found — enable?', and that picker
    BLOCKS the ready UI: st new then reports could-not-tell for a healthy agent
    (observed on harding's first launch — it sat there until a human hit Enter).
    Every role must launch past it, or launching a NEW agent always needs a human."""
    for role in ("worker", "lead", "administrator"):
        assert settings_for_role(role).get("enableAllProjectMcpServers") is True, (
            f"{role} would stall on the project-MCP consent screen"
        )


def _guard_commands(settings: dict) -> list[str]:
    out = []
    for entry in settings["hooks"].get("PreToolUse", []):
        out += [h["command"] for h in entry["hooks"]]
    return out


def test_every_role_gets_the_hank_policy_guard():
    """First-class means you cannot launch an UNGUARDED agent by forgetting a flag
    (Stiwi 2026-07-19). Every role wires hank's pre-edit guard on edit-shaped tools."""
    for role in ("worker", "lead", "administrator"):
        cmds = _guard_commands(settings_for_role(role))
        assert cmds, f"{role} has no hank guard — an unguarded agent is launchable"
        assert "hank hook pre-edit" in cmds[0]
        matcher = settings_for_role(role)["hooks"]["PreToolUse"][0]["matcher"]
        for tool in ("Edit", "Write"):
            assert tool in matcher, f"{role} guard does not cover {tool}"


def test_the_guard_fails_OPEN():
    """NON-NEGOTIABLE. A guard that failed closed would brick every crew agent the
    moment hank was absent, crashed, or lagging a release — a code-intelligence
    nicety turned into a fleet outage. hank denies via block JSON on stdout with
    exit 0, so failing open cannot swallow a real deny."""
    cmd = _guard_commands(settings_for_role("worker"))[0]
    assert "command -v hank" in cmd, "guard does not check hank is installed"
    assert "|| exit 0" in cmd, "guard does not fail open on hank failure"


def test_guard_fail_open_actually_allows_when_hank_is_missing(tmp_path):
    """Prove it by RUNNING it, not by reading it: with hank absent from PATH the
    guard must exit 0 (allow) and emit no deny."""
    import subprocess
    cmd = _guard_commands(settings_for_role("worker"))[0]
    empty = tmp_path / "emptybin"
    empty.mkdir()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       env={"PATH": str(empty)})
    assert r.returncode == 0, f"guard blocked when hank was absent: rc={r.returncode}"
    assert "deny" not in r.stdout.lower()


def test_unknown_role_is_refused():
    with pytest.raises(ValueError):
        settings_for_role("overlord")


# --- role set EMITS the file (generative, same write as the card) ---------------

def _world(tmp_path: Path) -> Path:
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%1"}))
    return root


def test_role_set_emits_the_settings_file(tmp_path):
    root = _world(tmp_path)
    rc = main(["--root", str(root), "role", "set", "ellie", "worker"])
    assert rc == OK
    p = root / "settings" / "worker.settings.json"
    assert p.is_file(), "role set did not emit the role's settings.json"
    assert _stop_commands(json.loads(p.read_text())) == [
        f"{_PY} -m shantytown.stop_event send --root {root.resolve()}"
    ]


def test_emitted_hook_carries_an_absolute_root(tmp_path):
    """The hook must reach THIS store, not cwd/.shanty.

    stop_event resolves root as --root, else $SHANTY_ROOT, else CWD/.shanty — and
    the launcher runs the agent in its OWN workspace, which has no .shanty. So an
    unrooted hook looked for the registry under e.g.
    ~/gt/beads_aegis/crew/<agent>/.shanty, found nothing, and every stop event died
    unpersisted: four live workers produced zero events and `events/` was never
    created. Without an absolute root, send/drain silently route nowhere.
    """
    root = _world(tmp_path)
    assert main(["--root", str(root), "role", "set", "ellie", "worker"]) == OK
    cmd = _stop_commands(json.loads((root / "settings" / "worker.settings.json").read_text()))[0]
    assert "--root " in cmd, "hook has no --root; it will resolve against the agent's cwd"
    given = cmd.split("--root ", 1)[1].strip()
    assert Path(given).is_absolute(), f"hook root {given!r} is not absolute"
    assert Path(given) == root.resolve()


def test_role_set_dry_run_emits_nothing(tmp_path):
    root = _world(tmp_path)
    rc = main(["--root", str(root), "role", "set", "ellie", "worker", "-n"])
    assert rc == OK
    assert not (root / "settings").exists(), "dry-run emitted a settings file"


# --- THE LOOP CLOSES: role set then st new no longer refuses on settings ---------

def test_role_set_then_new_no_longer_refuses_for_missing_settings(tmp_path, monkeypatch):
    """Before #6, st new REFUSED a worker with no settings file (the invariant).
    After role set emits it, compose materializes and st new proceeds to launch +
    verify. We only assert it got PAST the settings refusal — the pane shows the
    ready banner so verify returns 0."""
    root = _world(tmp_path)
    assert main(["--root", str(root), "role", "set", "ellie", "worker"]) == OK
    # st new: a live pane with the ready banner -> verify 0. If settings were still
    # missing, compose would REFUSE (exit 1) before ever creating a session.
    panes = NullPanes(screen="… Welcome to Claude Code …\n? for shortcuts", live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    monkeypatch.setattr(cli, "_LIVE_ATTEMPTS", 1)
    monkeypatch.setattr(cli, "_LIVE_DELAY", 0)
    rc = main(["--root", str(root), "new", "ellie"])
    assert rc == OK, "role set emitted settings but st new still refused"
    assert panes.sent, "st new should have launched now that settings exist"
    _, launch = panes.sent[-1]
    assert "--settings" in launch and "worker.settings.json" in launch
