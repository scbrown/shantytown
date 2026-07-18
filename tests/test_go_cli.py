"""st go, at the CLI level: an unverified send must be a clean exit 2, not a crash.

Dispatch (#2) correctly RAISES SendUnverified when the pane does not confirm the
send. But _cmd_go was catching only TriageRefused/LookupError, so SendUnverified
propagated as an UNCAUGHT TRACEBACK (exit 1) — found by the zx7l full-cycle
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
