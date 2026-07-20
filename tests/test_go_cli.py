"""st go, at the CLI level: an unverified send must be a clean exit 2, not a crash.

Dispatch (#2) correctly RAISES SendUnverified when the pane does not confirm the
send. But _cmd_go was catching only TriageRefused/LookupError, so SendUnverified
propagated as an UNCAUGHT TRACEBACK (exit 1) — found by the full-cycle
validation against a real pane that could not echo. The exception's own docstring
pins it to exit 2 (could-not-tell); this proves the CLI honors that.
"""
from __future__ import annotations
import json
from pathlib import Path

import shantytown.cli as cli
from shantytown.cli import main, OK, REFUSED, CANNOT_TELL
from shantytown.tmux import NullPanes


def _root(tmp_path: Path) -> Path:
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))
    (root / "items").mkdir()
    (root / "items" / "item-1.json").write_text(
        json.dumps({"title": "Restore the den", "status": "open"}))
    return root


def test_go_returns_2_on_a_dropped_send_not_a_traceback(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path)
    # drops=True: send-keys "succeeds" but the pane never shows the work -> verify
    # fails -> SendUnverified. The CLI must render this as could-not-tell, not crash.
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: NullPanes(screen="", drops=True))
    rc = main(["--root", str(root), "go", "item-1", "ellie"])
    assert rc == CANNOT_TELL
    err = capsys.readouterr().err
    assert "could not tell" in err
    # and, per verify-then-record, the item was NOT marked in_progress
    assert json.loads((root / "items" / "item-1.json").read_text())["status"] == "open"


def test_go_returns_0_when_the_send_lands(tmp_path, monkeypatch):
    """Positive control: the same path returns 0 when the pane DOES confirm — so
    the exit-2 above is the drop, not a gate that always fires."""
    root = _root(tmp_path)
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: NullPanes(screen=""))   # echoes
    rc = main(["--root", str(root), "go", "item-1", "ellie"])
    assert rc == OK
    assert json.loads((root / "items" / "item-1.json").read_text())["status"] == "in_progress"


# --- #5: a RESTART verdict must name the remedy, now that one exists ---------

def test_restart_verdict_names_the_commands_that_fix_it(tmp_path, monkeypatch, capsys):
    """GitHub #5's complaint was that shantytown could DIAGNOSE a wedged session
    and then not act on it, because new/stop did not exist. They exist now, so a
    RESTART refusal points at them by name rather than dead-ending in a verdict.

    We deliberately do NOT relaunch inside `go`: killing an agent as a side
    effect of dispatching work is precisely the act that must stay explicit.
    """
    root = _root(tmp_path)
    # "[Process completed]" in the pane tail is the wedged marker -> RESTART.
    monkeypatch.setattr(cli, "Tmux",
                        lambda *a, **k: NullPanes(screen="[Process completed]"))
    rc = main(["--root", str(root), "go", "item-1", "ellie"])
    assert rc == REFUSED
    err = capsys.readouterr().err
    assert "RESTART" in err
    assert "st stop ellie" in err and "st new ellie" in err, (
        "a RESTART verdict that does not name the remedy is the #5 dead end"
    )
    assert "never handoff" in err
    # and, as with every refusal, nothing was written
    assert json.loads((root / "items" / "item-1.json").read_text())["status"] == "open"


def test_the_remedy_line_is_specific_to_RESTART(tmp_path, monkeypatch, capsys):
    """Positive control: an in-flight pane is ALSO a refusal, and stopping the
    agent is the wrong advice there — it is working. If the remedy printed on
    every refusal it would be noise, and worse, dangerous noise."""
    root = _root(tmp_path)
    monkeypatch.setattr(cli, "Tmux",
                        lambda *a, **k: NullPanes(screen="esc to interrupt"))
    rc = main(["--root", str(root), "go", "item-1", "ellie"])
    assert rc == REFUSED
    err = capsys.readouterr().err
    assert "REFUSE" in err
    assert "st stop" not in err, "told the operator to kill an agent that is mid-flight"
