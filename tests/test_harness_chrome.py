"""`--chrome` is per-card opt-in; `--no-chrome` stays the default (aegis-neffw).

Stiwi 2026-08-02: "lets configure st to enable the --chrome parameter".

WHY THIS IS NOT A ONE-CHARACTER CHANGE, and why the default test below matters
more than the feature test. `harness.py` passes `--no-chrome` on every crew launch
because without it a first-run claude stops at a "Claude in Chrome extension
detected" consent prompt that BLOCKS the ready UI — so `st new`'s verify never
sees live and returns could-not-tell (2) for an agent that is perfectly fine.
That is aegis-84z1, live-fire confirmed, and it was a production 0-path failure.

Flipping the flag globally re-breaks that fleet-wide. So the capability lands as a
per-card field — one agent's decision on one card — and every card that does not
ask for a browser keeps the measured default.

There is a second reason it must not be global, from Anthropic's own docs:
"Enabling Chrome by default in the CLI increases context usage since browser tools
are always loaded." 40+ tools in every session's context across the fleet, while
the governor sits at a P0-only floor, is a cost nobody asked for.

The contract:
  · a card that says nothing gets `--no-chrome`   <- the aegis-84z1 default
  · `chrome = true` gets `--chrome`, and never both
  · the field round-trips through the card file and through shantytown.toml
  · `role set` does not silently clear it (it is launch config, not tier config)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shantytown.files import FilesRegistry
from shantytown.harness import ClaudeHarness
from shantytown.protocols import Agent


def _launch(**card_kw):
    card = Agent(name="ellie", role="worker", **card_kw)
    return ClaudeHarness().launch(card, settings_path="/tmp/s.json", root="/tmp/r")


def test_the_default_is_no_chrome():
    """THE LOAD-BEARING TEST. If this ever goes red, `st new`'s liveness verify is
    about to return could-not-tell for every healthy agent on the fleet — that is
    what aegis-84z1 measured and fixed. A green feature test beside a red default
    test is still a broken fleet."""
    cmd = _launch()
    assert "--no-chrome" in cmd
    assert "--chrome " not in cmd.replace("--no-chrome", ""), \
        "a card that asked for nothing must not get the browser integration"


def test_a_card_that_opts_in_gets_chrome():
    cmd = _launch(chrome=True)
    assert "--chrome" in cmd
    assert "--no-chrome" not in cmd, "the two flags are exclusive; claude takes the last, do not rely on it"


def test_opting_one_card_in_does_not_change_another():
    """Per-card means per-card. The pilot shape `dangerous` already uses: a crew
    worker that needs something sets it on its OWN card and nobody inherits it."""
    assert "--chrome" in _launch(chrome=True)
    assert "--no-chrome" in _launch()


def test_the_field_round_trips_through_the_card_file(tmp_path):
    reg = FilesRegistry(tmp_path / "crew")
    reg.set(Agent(name="weaver", role="worker", chrome=True))
    assert reg.get("weaver").chrome is True
    assert json.loads((tmp_path / "crew" / "weaver.json").read_text())["chrome"] is True


def test_a_card_without_chrome_writes_no_key(tmp_path):
    """Write-only-when-true, same as `dangerous`. An absent key reads False, and
    not writing it keeps a card that never asked for a browser free of a field
    implying somebody considered it."""
    reg = FilesRegistry(tmp_path / "crew")
    reg.set(Agent(name="zia", role="worker"))
    assert "chrome" not in json.loads((tmp_path / "crew" / "zia.json").read_text())
    assert reg.get("zia").chrome is False


def test_role_set_does_not_clear_it(tmp_path):
    """`chrome` is LAUNCH config; the tier does not own it. A `role set` that
    silently dropped it would revert an agent's browser access on an unrelated
    change — the same bug #9 fixed for `model` and #17 for `workspace`."""
    reg = FilesRegistry(tmp_path / "crew")
    reg.set(Agent(name="ian", role="worker", chrome=True))
    reg.set(Agent(name="ian", role="lead"))          # a tier write, carrying no chrome
    assert reg.get("ian").chrome is True


def test_it_is_declarable_in_shantytown_toml(tmp_path):
    from shantytown import config
    (tmp_path / "shantytown.toml").write_text(
        '[crew.malcolm]\nrole = "worker"\nchrome = true\n')
    cfg = config.load(tmp_path)
    assert cfg.crew["malcolm"].chrome is True


def test_a_non_boolean_chrome_is_refused(tmp_path):
    """Same validation as dangerous/retired. `chrome = "yes"` is truthy in python
    and would silently enable a browser the operator did not ask for."""
    from shantytown import config
    (tmp_path / "shantytown.toml").write_text(
        '[crew.malcolm]\nrole = "worker"\nchrome = "yes"\n')
    with pytest.raises(config.ConfigError) as ei:
        config.load(tmp_path)
    assert "chrome" in str(ei.value)
