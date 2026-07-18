"""role set EMITS the settings.json — the content st new's --settings reads.
shantytown #6 (aegis-ct5q, arnold's ruling). Closes the launch/hooks loop:
declaring a role writes its stop hooks in the SAME operation as the card, and
st new then finds the settings it refused for a moment ago.
"""
from __future__ import annotations
import json
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


def test_worker_settings_send_only():
    cmds = _stop_commands(settings_for_role("worker"))
    assert cmds == ["python -m shantytown.stop_event send"]


def test_lead_settings_send_and_drain():
    """A lead sends its OWN stop up AND drains its reports' — send + drain."""
    cmds = _stop_commands(settings_for_role("lead"))
    assert cmds == ["python -m shantytown.stop_event send",
                    "python -m shantytown.stop_event drain"]


def test_administrator_settings_drain_only():
    """Root: receives only; its own stop terminates (no send)."""
    cmds = _stop_commands(settings_for_role("administrator"))
    assert cmds == ["python -m shantytown.stop_event drain"]


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
    assert _stop_commands(json.loads(p.read_text())) == ["python -m shantytown.stop_event send"]


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
