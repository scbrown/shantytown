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


def test_the_guard_invokes_hanks_pre_edit_event():
    """The guard must still CALL hank — laundering the exit code must not become
    "stop asking hank". It wraps the call; it does not replace it.

    The previous version of this test asserted the command was `hank hook pre-edit`
    VERBATIM, on the reasoning that a `||` wrapper was redundant because "hank never
    exits 2". That reasoning was falsified in production on 2026-07-19: installed
    hank 0.1.0 knows only `post-edit`, clap treated `pre-edit` as a usage error, and
    clap exits 2 — blocking every Write/Edit for every worker. The wrapper is now
    mandatory, and the stdout hazard it was accused of is handled by only echoing
    hank's output when hank exited 0 (see _HANK_GUARD).
    """
    hook = settings_for_role("worker")["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "hank hook pre-edit" in hook["command"], "guard no longer consults hank"
    assert hook.get("timeout") == 5, "guard has no timeout; a hung guard stalls every edit"


def test_guard_can_never_produce_the_blocking_exit_code(tmp_path):
    """THE fail-open invariant, stated correctly and PROVEN by running it.

    Exit 2 is the ONLY code that blocks a tool call. So the requirement is not
    "always exits 0" — a missing hank exits 127, which is fine — it is "can never
    exit 2". A guard that could exit 2 would brick every crew agent the moment
    hank was absent, crashed, or lagging a release.
    """
    import subprocess
    cmd = _guard_commands(settings_for_role("worker"))[0]
    empty = tmp_path / "emptybin"
    empty.mkdir()
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       env={"PATH": str(empty)})
    assert r.returncode != 2, f"guard hard-blocked with hank absent (rc={r.returncode})"
    # ALLOW IS SILENCE: it must not forge a permission decision either way.
    assert "permissionDecision" not in r.stdout, "guard forged a decision with no hank"


def test_guard_cannot_block_when_hank_is_present_but_stale(tmp_path):
    """THE REGRESSION. hank ABSENT (127) was already covered; hank PRESENT and
    older than the event name was not — and that is the case that took the fleet
    down on 2026-07-19. A CLI that does not know the subcommand exits 2 (clap's
    usage-error code), which is the one code Claude Code treats as a hard block.

    Specimen: `hank hook pre-edit` against hank 0.1.0 ->
        error: invalid value 'pre-edit' for '<EVENT>'   (exit 2)
    """
    import subprocess
    cmd = _guard_commands(settings_for_role("worker"))[0]
    stale = tmp_path / "stalebin"
    stale.mkdir()
    fake = stale / "hank"
    fake.write_text(
        "#!/bin/sh\n"
        "echo \"error: invalid value 'pre-edit' for '<EVENT>'\" >&2\n"
        "exit 2\n"
    )
    fake.chmod(0o755)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       env={"PATH": str(stale)})
    assert r.returncode != 2, (
        f"guard passed a stale hank's usage-error exit 2 straight through (rc={r.returncode}) "
        "— this is the fleet-wide edit outage"
    )
    assert "permissionDecision" not in r.stdout, "guard forged a decision from a failed hank"


def test_settings_env_carries_BOBBIN_ROLE_for_the_guard_tenant():
    """hank's shipped spec puts BOBBIN_ROLE in the settings `env` block, and that
    is where the guard reads its tenant. A launch-string export sets it for the
    agent PROCESS, but a hook is re-exec'd by the harness — so settings.env is the
    binding that actually reaches the guard. Without it the guard resolves no
    scope and decides nothing: running, wired, and inert."""
    for role in ("worker", "lead", "administrator"):
        env = settings_for_role(role).get("env", {})
        assert env.get("BOBBIN_ROLE") == role, (
            f"{role} settings carry no tenant; its guard would decide nothing"
        )


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
