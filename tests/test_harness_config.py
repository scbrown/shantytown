"""[harness] — WHICH agent program, for cards that do not say.

Two levels, and a card still beats both:

    [harness]                     the fleet's program
    default = "codex"

    [harness.by_role]             that role's program
    lead = "claude"

MOST SPECIFIC WINS — card, then role, then fleet, then "claude". The tests below
are mostly about the LOSING cases, because that is where a precedence rule is
actually wrong: a fleet default that quietly overrides a card is a config that
moved an agent nobody asked it to move.

The other half is threading. The resolved answer has to be the SAME one at every
surface — the emitter that writes the artifact, the resolver that finds it, the
gate that decides whether a role is hostable, and the launcher. A config the
launcher can see and the gate cannot is aegis-85ox with a file in the middle, so
each of those is exercised through its real entry point rather than through
harness.name_for alone.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import cli, config, harness as harness_mod
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent


def _store(tmp_path: Path, toml: str = "", **cards) -> Path:
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    for name, spec in cards.items():
        (root / "crew" / f"{name}.json").write_text(json.dumps(spec))
    if toml:
        (root / "shantytown.toml").write_text(toml)
    return root


# --- precedence ----------------------------------------------------------------

def test_a_fleet_default_answers_for_a_card_that_never_said(tmp_path):
    root = _store(tmp_path, '[harness]\ndefault = "codex"\n')
    assert harness_mod.name_for(Agent(name="ellie", role="worker"), root=root) == "codex"


def test_a_role_rule_beats_the_fleet_default(tmp_path):
    """The shape the question was actually asked in: run the crew on codex, keep
    the roles that receive stop events on Claude Code."""
    root = _store(tmp_path, '[harness]\ndefault = "codex"\n\n'
                            '[harness.by_role]\nlead = "claude"\n'
                            'administrator = "claude"\n')
    name = lambda role: harness_mod.name_for(Agent(name="x", role=role), root=root)
    assert name("worker") == "codex"
    assert name("lead") == "claude"
    assert name("administrator") == "claude"


def test_the_CARD_beats_everything(tmp_path):
    """The one that matters most. A card names its program explicitly; a config
    written later must not move it. Config is what answers for the SILENT."""
    root = _store(tmp_path, '[harness]\ndefault = "codex"\n\n'
                            '[harness.by_role]\nworker = "codex"\n')
    card = Agent(name="ellie", role="worker", harness="claude")
    assert harness_mod.name_for(card, root=root) == "claude"


def test_no_table_no_root_and_no_file_all_mean_claude(tmp_path):
    """Three ways of saying nothing, one answer — the behaviour of every
    deployment that predates this table."""
    card = Agent(name="ellie", role="worker")
    assert harness_mod.name_for(card) == "claude"                      # no root
    assert harness_mod.name_for(card, root=tmp_path) == "claude"       # no file
    assert harness_mod.name_for(card, root=_store(tmp_path, "[fleet]\n")) == "claude"


def test_an_unreadable_config_means_the_deployment_DID_NOT_SAY(tmp_path):
    """This resolves inside hooks and on the launch path. A stray comma in a TOML
    file must not take the fleet down — it degrades to the card's own answer,
    which is narrower than what the config asked for, never wider."""
    root = _store(tmp_path, '[harness]\ndefault = "codex"   # unterminated [[[\n[[[')
    assert harness_mod.name_for(Agent(name="ellie", role="worker"), root=root) == "claude"


# --- validation: both halves, and each catches a different silent failure -------

def test_an_unimplemented_harness_name_is_REFUSED_at_load(tmp_path):
    """A typo in `default` moves EVERY card in the fleet. Without this the first
    symptom is `st new` refusing agent by agent — a fleet-wide config error
    reported as a per-agent launch failure."""
    root = _store(tmp_path, '[harness]\ndefault = "cdoex"\n')
    with pytest.raises(config.ConfigError, match="not a harness this build implements"):
        config.load(root)


def test_a_role_nobody_has_is_REFUSED_at_load(tmp_path):
    """`[harness.by_role] leed = "codex"` would otherwise be accepted, apply to
    nobody, and read as done — the silently-dropped key this config refuses to
    have anywhere else."""
    root = _store(tmp_path, '[harness.by_role]\nleed = "codex"\n')
    with pytest.raises(config.ConfigError, match="not a role this deployment has"):
        config.load(root)


def test_a_DECLARED_role_is_accepted(tmp_path):
    """The role vocabulary is the deployment's (GitHub #37), so by_role has to
    accept a role this file invents — otherwise the two tables disagree about
    which roles exist."""
    root = _store(tmp_path, '[roles.advisor]\nattachment = "unattached"\n\n'
                            '[harness.by_role]\nadvisor = "codex"\n')
    assert config.load(root).harness_by_role == {"advisor": "codex"}


def test_an_unknown_key_in_the_table_is_REFUSED(tmp_path):
    root = _store(tmp_path, '[harness]\ndefualt = "codex"\n')
    with pytest.raises(config.ConfigError, match="unknown key"):
        config.load(root)


def test_the_trait_table_still_refuses_a_harness_key(tmp_path):
    """WHY THIS IS A SEPARATE TABLE. `[roles.*]` is the trait vocabulary and its
    axis names are validated against the ontology; launch config there would mean
    either loosening that for everything or special-casing one key."""
    root = _store(tmp_path, '[roles.worker]\nharness = "codex"\n')
    with pytest.raises(config.ConfigError, match="unknown trait axis"):
        config.load(root)


# --- threading: the same answer at every surface --------------------------------

def test_role_set_emits_the_artifact_the_CONFIG_names(tmp_path):
    """The emitter. A worker card that says nothing, on a fleet defaulted to
    codex, must get codex's artifact — an emitter reading the card alone would
    write Claude Code's and the agent would launch pointing at nothing."""
    root = _store(tmp_path, '[harness]\ndefault = "codex"\n',
                  ada={"role": "worker", "pane": "st-ada"})
    assert cli.main(["--root", str(root), "roles", "set", "ada", "worker"]) == cli.OK
    assert (root / "settings" / "codex" / "worker" / "config.toml").is_file()
    assert not (root / "settings" / "worker.settings.json").exists()


def test_the_launch_and_the_resolver_agree_with_the_emitter(tmp_path, capsys):
    """The resolver and the launcher. Same store, one command: the composed
    launch must name codex AND point at the file the emitter wrote."""
    root = _store(tmp_path, '[harness]\ndefault = "codex"\n',
                  ada={"role": "worker", "pane": "st-ada"})
    cli.main(["--root", str(root), "roles", "set", "ada", "worker"])
    capsys.readouterr()
    assert cli.main(["--root", str(root), "new", "ada", "--dry-run"]) == cli.OK
    out = capsys.readouterr().out
    assert " codex --dangerously-bypass-hook-trust" in out
    assert f"CODEX_HOME={root / 'settings' / 'codex' / 'worker'}" in out


def test_anchor_harness_prints_the_resolved_answer(tmp_path, capsys):
    """`st anchor --harness` is a status-bar segment, so it must say what the
    agent will ACTUALLY run — not what its card happens to have written down."""
    root = _store(tmp_path, '[harness]\ndefault = "codex"\n',
                  ada={"role": "worker", "pane": "st-ada"})
    assert cli.main(["--root", str(root), "anchor", "ada", "--harness"]) == cli.OK
    assert capsys.readouterr().out.strip() == "codex"


def test_the_capability_gate_asks_the_program_the_CONFIG_resolves(tmp_path,
                                                                  monkeypatch):
    """THE ONE THAT WOULD HAVE BEEN aegis-85ox AGAIN. The gate runs inside
    tier.role_set, which had no root at all — so a config that put leads on a
    program with no blocking stop hooks would have passed role set and refused
    at `st new`, after the card was already on disk."""
    class _Stopless:
        name = "stopless-test"
        def hooks(self, card):
            from shantytown.runtime import HookSpec
            return HookSpec(blocking_stop=False)
    monkeypatch.setitem(harness_mod._HARNESSES, "stopless-test", _Stopless())
    root = _store(tmp_path, '[harness.by_role]\nlead = "stopless-test"\n',
                  malcolm={"role": "worker", "pane": "st-malcolm"})

    from shantytown import tier
    from shantytown.runtime import CapabilityError
    reg = FilesRegistry(root / "crew")
    with pytest.raises(CapabilityError, match="blocking stop hooks"):
        tier.role_set(reg, "malcolm", "lead", root=root)
    assert reg.get("malcolm").role == "worker", "the refusal wrote a card"


def test_the_config_does_not_write_the_answer_onto_the_card(tmp_path):
    """A resolved default is not a declaration. Persisting it would make the
    card claim a choice nobody made — and would then survive the config being
    changed back, which is the opposite of what a default is for."""
    root = _store(tmp_path, '[harness]\ndefault = "codex"\n',
                  ada={"role": "worker", "pane": "st-ada"})
    cli.main(["--root", str(root), "roles", "set", "ada", "worker"])
    assert "harness" not in json.loads((root / "crew" / "ada.json").read_text())
