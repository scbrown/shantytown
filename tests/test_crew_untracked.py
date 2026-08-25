"""`st crew` consumes the existing untracked-work ledger (aegis-eh6ok)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import shantytown.cli as cli
from tests.test_crew_work import IDLE_SCREEN, _Args, _Panes, _roster


def _ledger(root: Path, agent: str, value: dict, *, age: float = 0) -> Path:
    path = root / "untracked" / f"{agent}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def _run(root, monkeypatch, capsys, *, count=False):
    panes = _Panes({"p-ellie": IDLE_SCREEN})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    args = _Args(root)
    args.count = count
    assert cli._cmd_crew(args) == cli.OK
    return capsys.readouterr().out


def test_recent_untracked_activity_is_unknown_and_not_free(
        tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie"})
    _ledger(root, "ellie", {"hooked": False, "strikes": 3, "since": 1})

    out = _run(root, monkeypatch, capsys)

    assert "? (untracked activity" in out
    assert "UNKNOWN work state: ellie" in out
    assert "free: ellie" not in out


def test_hooked_ledger_preserves_idle_and_free(tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie"})
    _ledger(root, "ellie", {"hooked": True, "checked_at": time.time()})

    out = _run(root, monkeypatch, capsys)

    assert "1 free: ellie" in out
    assert "UNKNOWN work state" not in out


def test_stale_untracked_stretch_no_longer_claims_current_activity(
        tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie"})
    _ledger(root, "ellie", {"hooked": False, "strikes": 20, "since": 1},
            age=601)

    out = _run(root, monkeypatch, capsys)

    assert "1 free: ellie" in out


def test_missing_ledger_is_unknown_not_a_fabricated_all_clear(
        tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie"})
    (root / "untracked" / "ellie.json").unlink()

    out = _run(root, monkeypatch, capsys)

    assert "? (untracked ledger unreadable)" in out
    assert "free: ellie" not in out


def test_count_uses_the_same_untracked_verdict_as_the_roster(
        tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie"})
    _ledger(root, "ellie", {"hooked": False, "strikes": 3, "since": 1})

    assert _run(root, monkeypatch, capsys, count=True).strip() == "0/0"


def test_administrators_remain_exempt(tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie"})
    card = root / "crew" / "ellie.json"
    data = json.loads(card.read_text())
    data["role"] = "administrator"
    card.write_text(json.dumps(data))

    out = _run(root, monkeypatch, capsys)

    assert "1 free: ellie" in out
