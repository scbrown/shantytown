"""[model] — which MODEL a card runs, resolved card -> role -> fleet.

The gap this closes: `harness` could be set for a whole tier from config, but
`model` was CARD-ONLY. A deployment that wanted its administrator on the top
model and its workers on a cheap one had to stamp the slug onto every card by
hand, and every new card silently reverted to the harness default — the same
"reads as configured, isn't" failure the card-model field itself was added to
fix (GitHub #17).

WHAT THESE TESTS HAVE TO DISCRIMINATE, because a resolver that always returns
the card's own answer would pass a careless version of all of this:

  * the ROLE rule applies when the card is silent  (delete _deployment_model and
    this fails)
  * the CARD still beats the role rule            (precedence, not "last write")
  * the FLEET default applies when neither says
  * NO config at all still means no --model flag  (the pre-existing behaviour of
    every deployment that never writes this table)
  * BOTH harnesses resolve the SAME answer for the same card — a claude lead and
    a codex worker reading different ladders is the aegis-85ox mismatch class
  * an unknown ROLE is REFUSED, not silently dropped
  * an unknown MODEL SLUG is ACCEPTED — deliberately, see _model's docstring;
    the provider owns that vocabulary and a build-time allowlist would refuse
    tomorrow's model
"""
from __future__ import annotations

import pytest

from shantytown import harness as harness_mod
from shantytown.config import ConfigError, load
from shantytown.protocols import Agent


def _root(tmp_path, toml: str):
    (tmp_path / "shantytown.toml").write_text(toml)
    return tmp_path


# --- the ladder -------------------------------------------------------------

def test_role_rule_applies_when_the_card_is_silent(tmp_path):
    root = _root(tmp_path, """
[model.by_role]
administrator = "gpt-5.6-terra"
""")
    card = Agent(name="sattler", role="administrator")
    assert harness_mod.resolve_model(card, root) == "gpt-5.6-terra"


def test_card_beats_the_role_rule(tmp_path):
    root = _root(tmp_path, """
[model.by_role]
worker = "gpt-5.6-luna"
""")
    card = Agent(name="ellie", role="worker", model="gpt-5.5")
    assert harness_mod.resolve_model(card, root) == "gpt-5.5"


def test_fleet_default_applies_when_neither_card_nor_role_says(tmp_path):
    root = _root(tmp_path, """
[model]
default = "gpt-5.6-luna"
""")
    assert harness_mod.resolve_model(Agent(name="tim", role="worker"), root) \
        == "gpt-5.6-luna"


def test_role_rule_beats_the_fleet_default(tmp_path):
    root = _root(tmp_path, """
[model]
default = "gpt-5.6-luna"

[model.by_role]
administrator = "gpt-5.6-terra"
""")
    assert harness_mod.resolve_model(
        Agent(name="sattler", role="administrator"), root) == "gpt-5.6-terra"
    assert harness_mod.resolve_model(
        Agent(name="tim", role="worker"), root) == "gpt-5.6-luna"


def test_no_config_means_no_model_which_is_the_pre_existing_behaviour(tmp_path):
    assert harness_mod.resolve_model(Agent(name="tim", role="worker"),
                                     tmp_path) is None
    # and with no root at all — the hook path, which may not have one
    assert harness_mod.resolve_model(Agent(name="tim", role="worker")) is None


# --- the flag, on BOTH programs ---------------------------------------------

@pytest.mark.parametrize("harness_name", ["claude", "codex"])
def test_both_harnesses_put_the_resolved_model_on_the_command(tmp_path,
                                                              harness_name):
    root = _root(tmp_path, f"""
[harness]
default = "{harness_name}"

[model.by_role]
worker = "gpt-5.6-terra"
""")
    card = Agent(name="ellie", role="worker", workspace=str(tmp_path))
    line = harness_mod.get(harness_name).launch(card, "/dev/null", root=root)
    assert "--model gpt-5.6-terra" in line, line


@pytest.mark.parametrize("harness_name", ["claude", "codex"])
def test_no_model_anywhere_emits_no_model_flag(tmp_path, harness_name):
    card = Agent(name="ellie", role="worker", workspace=str(tmp_path))
    line = harness_mod.get(harness_name).launch(card, "/dev/null", root=tmp_path)
    assert "--model" not in line, line


def test_the_two_harnesses_agree_on_the_same_card(tmp_path):
    """A claude card and a codex card with the same role must resolve the SAME
    model. Two ladders that can disagree is the defect, not the feature."""
    root = _root(tmp_path, """
[model]
default = "gpt-5.5"
""")
    card = Agent(name="ellie", role="worker")
    assert harness_mod.resolve_model(card, root) == "gpt-5.5"
    claude_line = harness_mod.get("claude").launch(card, "/dev/null", root=root)
    codex_line = harness_mod.get("codex").launch(card, "/dev/null", root=root)
    assert "--model gpt-5.5" in claude_line
    assert "--model gpt-5.5" in codex_line


# --- validation: the asymmetry is deliberate --------------------------------

def test_unknown_role_is_refused_not_silently_dropped(tmp_path):
    root = _root(tmp_path, """
[model.by_role]
wrker = "gpt-5.6-terra"
""")
    with pytest.raises(ConfigError) as e:
        load(root)
    assert "wrker" in str(e.value)
    assert "model.by_role" in str(e.value)


def test_unknown_model_slug_is_accepted_on_purpose(tmp_path):
    """The provider owns this vocabulary. A build-time allowlist would refuse a
    model released after the installed version — wrong in the direction that
    blocks work, and the harness rejects a bad slug loudly at launch anyway."""
    root = _root(tmp_path, """
[model]
default = "gpt-9-not-released-yet"
""")
    cfg = load(root)
    assert cfg.model_default == "gpt-9-not-released-yet"


def test_empty_model_is_refused(tmp_path):
    root = _root(tmp_path, """
[model]
default = ""
""")
    with pytest.raises(ConfigError):
        load(root)


def test_unknown_key_in_the_model_table_is_refused(tmp_path):
    root = _root(tmp_path, """
[model]
defualt = "gpt-5.6-terra"
""")
    with pytest.raises(ConfigError) as e:
        load(root)
    assert "defualt" in str(e.value)
