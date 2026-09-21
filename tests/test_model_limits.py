"""An agent whose MODEL is out of budget is not idle, and not auth-dead either.

Stiwi, 2026-09-21: "we hit fable limits. we need to support this in st".

A usage limit is the third kind of "the pane is up and nothing runs", and it is
the only one of the three a supervisor can clear on its own:

    expired login   waits for a PERSON to re-authenticate
    a crash         waits for a FIX
    a usage limit   waits for a CLOCK — or for the same agent to come back on a
                    different model, which is a thing st can do unattended

Before this, st had no name for it. A limited pane rendered `idle`, landed on the
free list, and every dispatch into it failed against a banner st could not see —
the same failure an expired login cost this fleet before auth-dead had a name,
one axis over.
"""
from shantytown import harness as harness_mod
from shantytown import triage as triage_mod
from shantytown.protocols import Agent
from shantytown.runtime import ClaudeRuntime, limit_reached
from shantytown.tend import LIMITED, Tender
from shantytown.tmux import NullPanes


def _rt():
    return ClaudeRuntime.__new__(ClaudeRuntime)


# --------------------------------------------------------------------------
# SEEING IT
# --------------------------------------------------------------------------

def test_the_banner_is_recognised_in_both_measured_forms():
    """Read out of the shipped binary, 2026-09-21. Lowercased before matching:
    the same sentence ships capitalised as a banner and lowercase inside a
    longer line, and a case-sensitive tuple would cover one and silently miss
    the other."""
    assert _rt().limit_reached("● Usage limit reached · resets in 3h")
    assert _rt().limit_reached("usage credit limit reached")
    assert not _rt().limit_reached("? for shortcuts")


def test_an_agent_QUOTING_the_banner_has_not_hit_one():
    """The trap every marker in runtime.py documents, and the one that has been
    sprung here before: an agent reading this source, grepping a log or quoting
    the banner to a colleague is not an agent that is out of budget. Tail-only is
    what separates the state from a mention of the state."""
    screen = "Usage limit reached\n" + "\n".join("work" for _ in range(12))
    assert not _rt().limit_reached(screen)


def test_a_runtime_that_reads_no_panes_cannot_manufacture_a_limit():
    """False-because-could-not-ask is safe ONLY because of where it lands: the
    flag can convert some other verdict INTO limited, never the reverse."""
    assert limit_reached(object(), "Usage limit reached") is False


# --------------------------------------------------------------------------
# NOT CALLING IT IDLE
# --------------------------------------------------------------------------

def test_a_limited_pane_is_not_idle():
    """THE REGRESSION TEST. `idle` here puts a pane that cannot make a single
    call back on the free list."""
    assert triage_mod.work_state("? for shortcuts", True) == triage_mod.IDLE
    assert triage_mod.work_state("? for shortcuts", True,
                                 limited=True) == triage_mod.LIMITED


def test_auth_dead_still_wins_over_limited():
    """Both are 'alive and nothing runs', and they prescribe opposite recoveries
    — one waits for a person, the other can be fixed now. An agent that is BOTH
    needs the person first, because no model switch survives an expired login."""
    assert triage_mod.work_state("x", True, auth_dead=True,
                                 limited=True) == triage_mod.AUTH_DEAD


def test_a_computing_agent_is_busy_even_with_the_banner_on_screen():
    """The conservative direction, chosen deliberately: a limited session may sit
    on a spinner waiting for its window to reset, so `busy` may mask `limited`
    for as long as that spinner runs. The cost is a late verdict; the other
    ordering risks calling a genuinely computing agent limited on the strength of
    a banner left over from before."""
    spinning = "Usage limit reached\n✻ Churning… (12s · esc to interrupt)"
    assert triage_mod.work_state(spinning, True, limited=True) == triage_mod.BUSY


# --------------------------------------------------------------------------
# DOING SOMETHING ABOUT IT
# --------------------------------------------------------------------------

def test_the_fallback_ladder_is_card_then_role_then_fleet():
    card = Agent(name="a", model="claude-fable-5-1",
                 fallback_model="claude-opus-5")
    assert harness_mod.resolve_fallback_model(card) == "claude-opus-5"


def test_a_fallback_equal_to_the_current_model_is_no_fallback():
    """A relaunch onto the same limit reads as a fix, costs a session, and comes
    straight back — the crash-loop shape tend already refuses to feed."""
    card = Agent(name="a", model="claude-opus-5", fallback_model="claude-opus-5")
    assert harness_mod.resolve_fallback_model(card) is None


def test_no_declared_fallback_means_report_and_wait_not_guess():
    """A supervisor that picked its own replacement model would move an
    unattended agent onto a slug the deployment never chose, at the one moment
    nobody is watching. Declaring the fallback IS the saying-yes."""
    card = Agent(name="sattler", pane="p", model="claude-fable-5-1")
    panes = NullPanes(screen="Usage limit reached · resets in 3h")
    found = Tender(panes, _rt(), None)._live(card, [card])
    assert found.verdict == LIMITED
    assert "No fallback model is declared" in found.why
    assert "claude-opus-5" not in found.why


def test_a_declared_fallback_is_named_in_the_finding():
    card = Agent(name="sattler", pane="p", model="claude-fable-5-1")
    panes = NullPanes(screen="Usage limit reached · resets in 3h")
    found = Tender(panes, _rt(), None,
                   fallback_model=lambda c: "claude-opus-5",
                   current_model=lambda c: c.model)._live(card, [card])
    assert found.verdict == LIMITED
    assert "claude-fable-5-1" in found.why and "claude-opus-5" in found.why


def test_a_healthy_pane_is_never_called_limited():
    """The negative control. A verdict never seen failing to fire is not
    evidence."""
    card = Agent(name="sattler", pane="p")
    found = Tender(NullPanes(screen="? for shortcuts"), _rt(), None)._live(
        card, [card])
    assert found.verdict != LIMITED


def test_the_deployment_can_declare_a_fleet_wide_fallback(tmp_path):
    """Same shape, same precedence and the same table position as [model]
    default — a reader who has learned one has learned the other."""
    from shantytown.config import load_or_default
    (tmp_path / "shantytown.toml").write_text(
        '[model]\ndefault = "claude-fable-5-1"\nfallback = "claude-opus-5"\n\n'
        '[model.fallback_by_role]\nworker = "claude-haiku-4-5-20251001"\n')
    cfg, err = load_or_default(tmp_path)
    assert err is None, err
    assert cfg.model_fallback == "claude-opus-5"
    assert cfg.model_fallback_by_role["worker"] == "claude-haiku-4-5-20251001"


def test_a_fallback_for_a_role_nobody_has_is_refused(tmp_path):
    """A rule for a role nobody has applies to nobody and reads as applied."""
    from shantytown.config import load_or_default
    (tmp_path / "shantytown.toml").write_text(
        '[model]\n[model.fallback_by_role]\nwrker = "claude-opus-5"\n')
    _cfg, err = load_or_default(tmp_path)
    assert err is not None and "wrker" in str(err)
