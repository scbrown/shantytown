"""Launch-time delivery of workspace hooks to Codex (aegis-jlmqn)."""
from __future__ import annotations

import tomllib
from pathlib import Path

from shantytown import codex
from shantytown.provision import provision
from shantytown.protocols import Agent


def _commands(cfg: dict, event: str) -> list[str]:
    return [h["command"] for group in cfg["hooks"].get(event, [])
            for h in group.get("hooks", [])]


def _root(tmp_path: Path, role="worker") -> tuple[Path, Path]:
    root = tmp_path / "store"
    config = root / "settings" / "codex" / role / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(codex.render(codex.settings_for_role(role, root=root)))
    return root, config


def test_codex_launch_provision_delivers_all_three_hooks(tmp_path):
    root, config = _root(tmp_path)
    ws = tmp_path / "ws"; ws.mkdir()
    provision(Agent(name="franklin", role="worker", harness="codex",
                    workspace=str(ws)), root)
    cfg = tomllib.loads(config.read_text())
    assert any("shantytown.stats capture" in c
               for c in _commands(cfg, "PostToolUse"))
    assert any("shantytown.stats capture" in c for c in _commands(cfg, "Stop"))
    assert any("shantytown.untracked" in c for c in _commands(cfg, "PreToolUse"))
    assert any("shantytown.stale_guard" in c for c in _commands(cfg, "PreToolUse"))


def test_codex_reprovision_is_idempotent_and_preserves_other_config(tmp_path):
    root, config = _root(tmp_path)
    original = tomllib.loads(config.read_text())
    original["operator_key"] = "keep-me"
    config.write_text(codex.dumps(original))
    ws = tmp_path / "ws"; ws.mkdir()
    card = Agent(name="franklin", role="worker", harness="codex", workspace=str(ws))
    provision(card, root); once = config.read_text()
    provision(card, root); twice = config.read_text()
    cfg = tomllib.loads(twice)
    assert once == twice
    assert cfg["operator_key"] == "keep-me"
    assert sum("shantytown.stats capture" in c
               for c in _commands(cfg, "Stop")) == 1
    assert sum("shantytown.untracked" in c
               for c in _commands(cfg, "PreToolUse")) == 1
    assert sum("shantytown.stale_guard" in c
               for c in _commands(cfg, "PreToolUse")) == 1


def test_codex_admin_negative_control_has_no_untracked_nudge(tmp_path):
    root, config = _root(tmp_path, "administrator")
    ws = tmp_path / "ws"; ws.mkdir()
    provision(Agent(name="sattler", role="administrator", harness="codex",
                    workspace=str(ws)), root)
    cfg = tomllib.loads(config.read_text())
    assert not any("shantytown.untracked" in c
                   for c in _commands(cfg, "PreToolUse"))
    assert any("shantytown.stale_guard" in c
               for c in _commands(cfg, "PreToolUse"))


def test_claude_provision_does_not_touch_codex_config(tmp_path):
    root, config = _root(tmp_path)
    before = config.read_text()
    ws = tmp_path / "ws"; ws.mkdir()
    provision(Agent(name="ellie", role="worker", harness="claude",
                    workspace=str(ws)), root)
    assert config.read_text() == before
