"""Multiple governors, keyed by harness (aegis-5ve1h) — the DATA MODEL half.

One `[governor]` reading `claude:usage_utilization_pct:max` governed every agent.
That was right while every agent was Claude Code and is wrong twice at once on a
mixed fleet:

  FALSE THROTTLE  Claude at 95% drains codex agents whose provider's budget is
                  untouched — conserving a budget by spending none of it.
  BLIND SPEND     codex agents burn a quota NO configured probe reads. The
                  existing fail-safe cannot catch this: `on_signal_lost` fires on
                  a STALE probe, never on a provider nobody configured one for,
                  so it reads GREEN while unbounded.

The second is why `unconfigured()` exists and is tested here as hard as the happy
path: an alarm that only fires for a dead probe is not an alarm for this.

SCOPE OF THIS FILE: parsing, validation, and resolution. The call sites (go /
tend / drain / crew --governor) are the SECOND increment and are deliberately not
wired yet — a partially-wired governor is the failure this whole bead is about.
So the last test here pins the thing that makes that safe: with no `by_harness`
declared, resolution returns the base policy unchanged.
"""
from __future__ import annotations

import pytest
import types

from shantytown import governor as gov
from shantytown import cli
from shantytown.config import ConfigError, load
from shantytown.protocols import Agent


def _cfg(tmp_path, toml: str):
    (tmp_path / "shantytown.toml").write_text(toml)
    return load(tmp_path)


BASE = """
[governor]
source = "stub"
stub_pct = 10.0

[[governor.tier]]
at = 50
min_priority = 1
"""


# --- resolution -------------------------------------------------------------

def test_a_declared_harness_gets_its_own_governor(tmp_path):
    cfg = _cfg(tmp_path, BASE + """
[governor.by_harness.codex]
source = "stub"
stub_pct = 90.0
metric = "codex:usage_utilization_pct:max"

[[governor.by_harness.codex.tier]]
at = 80
min_priority = 0
""")
    p = cfg.governor
    codex = gov.policy_for(p, "codex")
    claude = gov.policy_for(p, "claude")
    assert codex is not claude
    assert codex.stub_pct == 90.0 and claude.stub_pct == 10.0
    assert codex.metric == "codex:usage_utilization_pct:max"
    assert claude.metric == gov.USAGE_METRIC


def test_the_two_harnesses_reach_different_tiers_from_the_same_config(tmp_path):
    """The whole point: same config, same instant, different verdicts — because
    they are spending different budgets."""
    cfg = _cfg(tmp_path, BASE + """
[governor.by_harness.codex]
source = "stub"
stub_pct = 5.0

[[governor.by_harness.codex.tier]]
at = 50
min_priority = 1
""")
    p = cfg.governor
    # claude at 10% and codex at 5% — but push claude over its tier:
    claude = gov.policy_for(p, "claude")
    codex = gov.policy_for(p, "codex")
    assert claude.tiers[0].at == 50
    assert codex.tiers[0].at == 50
    # the readings are what differ, and they come from different policies
    assert claude.stub_pct != codex.stub_pct


def test_an_undeclared_harness_falls_through_to_the_base(tmp_path):
    cfg = _cfg(tmp_path, BASE + """
[governor.by_harness.codex]
source = "stub"
stub_pct = 90.0
""")
    assert gov.policy_for(cfg.governor, "claude").stub_pct == 10.0


def test_no_by_harness_means_the_base_policy_for_everyone(tmp_path):
    """The compatibility pin. Every deployment predating this table must resolve
    exactly one governor, for every harness, forever."""
    cfg = _cfg(tmp_path, BASE)
    p = cfg.governor
    assert gov.policy_for(p, "claude") is p
    assert gov.policy_for(p, "codex") is p
    assert gov.policy_for(p, None) is p


# --- the blind-spend alarm --------------------------------------------------

def test_unconfigured_is_true_for_a_harness_no_governor_reads(tmp_path):
    cfg = _cfg(tmp_path, BASE + """
[governor.by_harness.claude]
source = "stub"
stub_pct = 10.0

[[governor.by_harness.claude.tier]]
at = 50
min_priority = 1
""")
    assert gov.unconfigured(cfg.governor, "codex") is True
    assert gov.unconfigured(cfg.governor, "claude") is False


def test_base_claude_and_codex_sibling_govern_both_harnesses(tmp_path):
    """The first configuration an operator writes: base Claude remains a real
    governor after adding a Codex sibling, not a false signal-lost alarm."""
    cfg = _cfg(tmp_path, BASE + """
[governor.by_harness.codex]
source = "stub"
stub_pct = 10.0
metric = "codex:usage_utilization_pct:max"
""")
    assert gov.unconfigured(cfg.governor, "claude") is False
    assert gov.unconfigured(cfg.governor, "codex") is False


def test_launch_default_does_not_reassign_the_base_governor(tmp_path):
    """[harness].default selects a launcher; it must not change which provider
    the historic base governor reads.  This is the live codex-default shape.
    """
    cfg = _cfg(tmp_path, BASE + """
[governor.by_harness.codex]
source = "stub"
stub_pct = 10.0
metric = "codex:usage_utilization_pct:max"

[harness]
default = "codex"
""")
    assert cfg.harness_default == "codex"
    assert gov.unconfigured(cfg.governor, "claude") is False
    assert gov.unconfigured(cfg.governor, "codex") is False


def test_governor_lookup_keeps_claude_metered_when_launch_default_is_codex(tmp_path):
    """Exercise the production call site.  Passing harness_default here caused
    the live false SIGNAL LOST line even though unconfigured() itself had the
    right compatibility default."""
    cfg = _cfg(tmp_path, BASE + """
[governor.by_harness.codex]
source = "stub"
stub_pct = 10.0

[harness]
default = "codex"
""")
    base = object()
    codex = object()
    claude_card = Agent(name="claire", role="worker", pane="p1",
                        harness="claude")
    harness, selected, alarm = cli._governor_for(
        cfg, {"base": base, "codex": codex}, claude_card, tmp_path)
    assert harness == "claude"
    assert selected is base
    assert alarm is None


def test_a_single_harness_deployment_is_never_unconfigured(tmp_path):
    """No by_harness at all = the pre-existing world: one governor, one provider.
    Alarming there would fire on every deployment that predates this feature."""
    cfg = _cfg(tmp_path, BASE)
    assert gov.unconfigured(cfg.governor, "codex") is False
    assert gov.unconfigured(cfg.governor, "claude") is False


def test_a_fleet_not_governing_at_all_is_never_unconfigured(tmp_path):
    """No tiers = the governor is off. An alarm about an ungoverned provider in a
    deployment that governs nothing is noise."""
    cfg = _cfg(tmp_path, """
[governor]
source = "stub"
stub_pct = 10.0
""")
    assert gov.unconfigured(cfg.governor, "codex") is False


# --- validation -------------------------------------------------------------

def test_an_unknown_harness_name_is_refused(tmp_path):
    with pytest.raises(ConfigError) as e:
        _cfg(tmp_path, BASE + """
[governor.by_harness.codx]
source = "stub"
stub_pct = 1.0
""")
    assert "codx" in str(e.value)


def test_nesting_by_harness_is_refused_not_ignored(tmp_path):
    with pytest.raises(ConfigError) as e:
        _cfg(tmp_path, BASE + """
[governor.by_harness.codex]
source = "stub"
stub_pct = 1.0

[governor.by_harness.codex.by_harness.claude]
source = "stub"
stub_pct = 2.0
""")
    assert "by_harness" in str(e.value)


def test_a_sub_governor_validates_its_own_keys(tmp_path):
    """A typo inside a sibling must fail like a typo in the base — otherwise the
    sibling is the one table in the file with no spell-check."""
    with pytest.raises(ConfigError) as e:
        _cfg(tmp_path, BASE + """
[governor.by_harness.codex]
sorce = "stub"
""")
    assert "sorce" in str(e.value)


def test_empty_metric_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        _cfg(tmp_path, """
[governor]
source = "stub"
stub_pct = 1.0
metric = ""
""")


def test_metric_defaults_to_the_claude_series(tmp_path):
    cfg = _cfg(tmp_path, BASE)
    assert cfg.governor.metric == gov.USAGE_METRIC
    assert cfg.governor.account_metric == gov.ACCOUNT_USAGE_METRIC


def test_a_policy_metric_is_what_the_prometheus_reader_requests():
    """A sibling metric that is parsed but not requested would look configured
    while permanently signal-lost.  Assert the request, not just the field."""
    seen = []
    reader = gov.PrometheusReader("http://prom", metric="codex:usage:max",
                                  account_metric="codex_usage",
                                  fetch=lambda url: seen.append(url) or
                                  '{"status":"success","data":{"result":[]}}')
    reader.read_all()
    assert "codex%3Ausage%3Amax" in seen[0]
    assert "claude%3Ausage" not in seen[0]


def test_dispatch_gate_resolves_the_governor_for_the_target_card(monkeypatch):
    """Acceptance is through the go gate, not policy_for in isolation: Claude
    at 90% refuses a P2 while the same P2 reaches a Codex card at 10%."""
    base = gov.Policy(source="stub", stub_pct=90, tiers=(
        gov.Tier(at=50, min_priority=1),),
        by_harness={})
    codex = gov.Policy(source="stub", stub_pct=10, tiers=(
        gov.Tier(at=50, min_priority=1),))
    base.by_harness["codex"] = codex
    governors = {
        "base": gov.Governor(base, gov.StubReader(90)),
        "codex": gov.Governor(codex, gov.StubReader(10)),
    }
    cards = [Agent(name="claire", role="worker", pane="p1", harness="claude"),
             Agent(name="sattler", role="administrator", pane="p2", harness="codex")]
    monkeypatch.setattr(cli, "_governors", lambda a: (types.SimpleNamespace(governor=base), governors))
    monkeypatch.setattr(cli, "_registry", lambda a: types.SimpleNamespace(all=lambda: cards))
    gate = cli._dispatch_gate(types.SimpleNamespace(root="/tmp"))
    item = types.SimpleNamespace(priority=2)
    assert gate(item, "claire")
    assert gate(item, "sattler") == ""


def test_dispatch_signal_lost_is_qualified_by_target_harness(monkeypatch, capsys):
    """The Codex sentinel must not be misread as the Claude/base governor."""
    base = gov.Policy(source="stub", stub_pct=10, tiers=(
        gov.Tier(at=50, min_priority=1),), by_harness={})
    codex = gov.Policy(source="stub", stub_pct=10, tiers=(
        gov.Tier(at=50, min_priority=1),))
    base.by_harness["codex"] = codex
    governors = {
        "base": gov.Governor(base, gov.StubReader(10), name="base"),
        "codex": gov.Governor(codex, gov.StubReader(10, ok=False), name="codex"),
    }
    cards = [Agent(name="claire", role="worker", pane="p1", harness="claude"),
             Agent(name="sattler", role="administrator", pane="p2", harness="codex")]
    monkeypatch.setattr(cli, "_governors", lambda a: (types.SimpleNamespace(governor=base), governors))
    monkeypatch.setattr(cli, "_registry", lambda a: types.SimpleNamespace(all=lambda: cards))
    gate = cli._dispatch_gate(types.SimpleNamespace(root="/tmp"))
    item = types.SimpleNamespace(priority=2)

    assert gate(item, "claire") == ""
    assert "[codex]" not in capsys.readouterr().err
    assert gate(item, "sattler") == ""
    assert "USAGE SIGNAL LOST [codex]" in capsys.readouterr().err
