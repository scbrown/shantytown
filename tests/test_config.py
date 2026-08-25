"""shantytown.toml — the modes, the hibernate policy, and the roster they resolve to.

Two properties carry most of the weight here, and each is a failure this repo has
already paid for in another form:

  AN UNKNOWN KEY IS REFUSED, never ignored. A silently-dropped config key is the
  same species as a settings file that was written, deployed and never read: the
  operator edits a file, sees no error, and believes a policy is in force.

  A RETIRED CARD IS NEVER SELECTED, not even by `*`. `st start --mode heavy` is
  the exact command that would resurrect a considered shutdown, which is what
  tend.py's whole retirement rule exists to prevent.
"""
from __future__ import annotations

import pytest

from shantytown import config
from shantytown.protocols import Agent


def _write(root, text: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / config.CONFIG_NAME).write_text(text)
    return root


# --- the file is optional, and the defaults are the CHEAP ones ---------------

def test_no_file_means_builtin_defaults_and_says_so(tmp_path):
    cfg = config.load(tmp_path)
    assert cfg.mode == "lite", "the default mode must be the token-conservation one"
    assert cfg.selectors() == ["administrator"]
    assert cfg.hibernate.enabled is False, "no config must never put an agent to sleep"
    assert cfg.path is None, (
        "path must be None with no file — 'you are on the defaults' and 'your "
        "config chose this' need different fixes")
    assert cfg.dream.enabled is False, "absence must never spend background tokens"


def test_dream_policy_parses_and_refuses_unsafe_values(tmp_path):
    cfg = config.load(_write(tmp_path, """
[dream]
enabled = true
interval_minutes = 90
min_headroom_pct = 35
domains = ["ontology", "infra"]
"""))
    assert cfg.dream.enabled is True
    assert cfg.dream.interval_minutes == 90
    assert cfg.dream.min_headroom_pct == 35
    assert cfg.dream.domains == ("ontology", "infra")

    with pytest.raises(config.ConfigError, match="min_headroom_pct"):
        config.load(_write(tmp_path, "[dream]\nmin_headroom_pct = 101\n"))
    with pytest.raises(config.ConfigError, match="domains"):
        config.load(_write(tmp_path, "[dream]\ndomains = []\n"))


def test_the_filename_is_shantytown_not_shanty(tmp_path):
    """`shanty` is a DIFFERENT program on the same operator's PATH (cli.py's
    docstring says why the binary is `st`). A `shanty.toml` is a file that tool has
    every right to claim, so we must not read one."""
    assert config.CONFIG_NAME == "shantytown.toml"
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / "shanty.toml").write_text('[startup]\nmode = "heavy"\n')
    assert config.load(tmp_path).mode == "lite", "a shanty.toml must be ignored"


def test_builtin_heavy_is_everything(tmp_path):
    assert config.load(tmp_path).selectors("heavy") == [config.ALL]


# --- operator modes MERGE over the built-ins ---------------------------------

def test_a_custom_mode_does_not_remove_lite_and_heavy(tmp_path):
    root = _write(tmp_path, """
[startup]
mode = "night"

[modes.night]
crew = ["administrator", "lead"]
""")
    cfg = config.load(root)
    assert cfg.mode == "night"
    assert cfg.selectors() == ["administrator", "lead"]
    assert cfg.selectors("lite") == ["administrator"], "lite must survive"
    assert cfg.selectors("heavy") == [config.ALL], "heavy must survive"


def test_a_config_may_redefine_a_builtin_mode(tmp_path):
    root = _write(tmp_path, """
[modes.lite]
crew = ["sattler", "arnold"]
""")
    assert config.load(root).selectors("lite") == ["sattler", "arnold"]


def test_an_unknown_mode_names_the_ones_that_exist(tmp_path):
    cfg = config.load(tmp_path)
    with pytest.raises(config.ConfigError) as e:
        cfg.selectors("medium")
    assert "medium" in str(e.value)
    assert "lite" in str(e.value) and "heavy" in str(e.value)


# --- refusals: a config error must never be silent ---------------------------

def test_malformed_toml_refuses_and_names_the_file(tmp_path):
    root = _write(tmp_path, "[startup\nmode = lite")
    with pytest.raises(config.ConfigError) as e:
        config.load(root)
    assert config.CONFIG_NAME in str(e.value)


def test_an_unknown_key_is_refused_not_ignored(tmp_path):
    """THE typo case: a near-miss key must be a refusal at the top of `st start`,
    not a coordinator that mysteriously never sleeps."""
    root = _write(tmp_path, """
[hibernate]
enabled = true
hibernate_minutes = 80
""")
    with pytest.raises(config.ConfigError) as e:
        config.load(root)
    assert "hibernate_minutes" in str(e.value)
    assert "max_quiet_minutes" in str(e.value), "the refusal must show the valid keys"


def test_an_unknown_top_level_table_is_refused(tmp_path):
    root = _write(tmp_path, '[startupp]\nmode = "lite"\n')
    with pytest.raises(config.ConfigError):
        config.load(root)


def test_startup_mode_must_be_a_defined_mode(tmp_path):
    root = _write(tmp_path, '[startup]\nmode = "enormous"\n')
    with pytest.raises(config.ConfigError) as e:
        config.load(root)
    assert "enormous" in str(e.value)


def test_a_mode_with_an_empty_crew_is_refused(tmp_path):
    """A mode that selects nobody would start nothing and exit 0 — a boot command
    reporting success over an empty fleet."""
    root = _write(tmp_path, "[modes.ghost]\ncrew = []\n")
    with pytest.raises(config.ConfigError) as e:
        config.load(root)
    assert "EMPTY" in str(e.value)


def test_a_crew_string_instead_of_a_list_is_refused_with_the_right_shape(tmp_path):
    root = _write(tmp_path, '[modes.solo]\ncrew = "administrator"\n')
    with pytest.raises(config.ConfigError) as e:
        config.load(root)
    assert "LIST" in str(e.value)


@pytest.mark.parametrize("bad", [
    'enabled = "yes"',
    "enabled = 1",
    "max_quiet_minutes = -5",
    "max_quiet_minutes = 0.5",
    'max_quiet_minutes = "30"',
])
def test_bad_hibernate_values_are_refused(tmp_path, bad):
    root = _write(tmp_path, f"[hibernate]\n{bad}\n")
    with pytest.raises(config.ConfigError):
        config.load(root)


def test_booleans_are_not_integers_for_a_minute_count(tmp_path):
    """`max_quiet_minutes = true` is 1 in Python's type system, nonsense as config."""
    root = _write(tmp_path, "[hibernate]\nmax_quiet_minutes = true\n")
    with pytest.raises(config.ConfigError):
        config.load(root)


# --- load_or_default: the hook path must never raise ------------------------

def test_load_or_default_returns_the_defaults_AND_the_error(tmp_path):
    """A stop hook runs inside every agent's process. A config typo must not wedge
    the fleet — but the error is HANDED BACK, not swallowed, so the caller can say
    out loud that it is running on defaults."""
    root = _write(tmp_path, "[hibernate]\nenabled = 'nonsense'\n")
    cfg, err = config.load_or_default(root)
    assert cfg.hibernate.enabled is False
    assert err and "nonsense" in err


def test_load_or_default_is_quiet_when_the_config_is_fine(tmp_path):
    cfg, err = config.load_or_default(tmp_path)
    assert err is None and cfg.mode == "lite"


# --- hibernate policy ------------------------------------------------------

def test_hibernate_is_a_boolean_and_a_bound(tmp_path):
    root = _write(tmp_path, """
[hibernate]
enabled = true
max_quiet_minutes = 45
""")
    h = config.load(root).hibernate
    assert h.enabled and h.max_quiet_minutes == 45


def test_zero_is_a_legitimate_bound(tmp_path):
    """0 = only wake when something PUSHES. `st tend` pushes, and a push is a
    wake with a reason, which beats a timer."""
    root = _write(tmp_path, "[hibernate]\nenabled = true\nmax_quiet_minutes = 0\n")
    assert config.load(root).hibernate.max_quiet_minutes == 0


def test_off_by_default(tmp_path):
    assert config.load(tmp_path).hibernate.enabled is False


# --- selectors -> roster ---------------------------------------------------

def _crew():
    return [
        Agent(name="sattler", role="administrator", pane="p-sattler"),
        Agent(name="arnold", role="lead", reports_to="sattler", pane="p-arnold"),
        Agent(name="billy", role="worker", reports_to="arnold", pane="p-billy"),
        Agent(name="harding", role="worker", reports_to="arnold", pane="p-harding"),
        Agent(name="ellie", role="worker", reports_to="arnold", pane="p-ellie",
              retired=True),
    ]


def test_star_selects_everyone_except_the_retired():
    r = config.resolve_crew([config.ALL], _crew())
    assert r.names == ["sattler", "arnold", "billy", "harding"]
    assert r.skipped_retired == ["ellie"]
    assert r.unknown == []


def test_a_retired_agent_named_EXPLICITLY_is_still_not_started():
    """Naming it is not authority to resurrect it. Retirement is durable and
    deliberate; the command that undoes it is `st tend --unretire`."""
    r = config.resolve_crew(["ellie"], _crew())
    assert r.names == [] and r.skipped_retired == ["ellie"]


def test_the_boot_order_is_administrator_then_leads_then_workers():
    """Not cosmetic: a worker whose lead is down has its stop events RISE to the
    administrator with `lead-unreachable` (tier.py Q3), so booting bottom-up
    manufactures the escalation the tier exists to avoid."""
    r = config.resolve_crew(["worker", "administrator", "lead"], _crew())
    assert r.names == ["sattler", "arnold", "billy", "harding"]


def test_a_role_selector_that_matches_nobody_is_empty_not_unknown():
    """`lite` on a store with no administrator: the selector is VALID and matched
    nothing. That is a different fix (`st roles set`) from a typo, so it must be a
    different signal."""
    workers = [Agent(name="billy", role="worker", pane="p")]
    r = config.resolve_crew(["administrator"], workers)
    assert r.names == [] and r.unknown == []


def test_a_name_with_no_card_is_unknown():
    r = config.resolve_crew(["nobody"], _crew())
    assert r.unknown == ["nobody"] and r.names == []


def test_selectors_are_deduped():
    r = config.resolve_crew([config.ALL, "sattler", "administrator"], _crew())
    assert r.names.count("sattler") == 1
