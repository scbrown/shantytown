"""The usage governor — every tier, driven by a stubbed reading.

THE ACCEPTANCE LIST IS BY MECHANISM, not by inspection, because the whole feature
is a decision nobody can see happening: at 95% the fleet stops, and if the drain
was sloppy the evidence (unpushed commits) is invisible until somebody looks days
later. So each test below stubs the reader at a number and asserts what the three
consuming surfaces actually DO — which agents tend keeps alive, which priorities
`st go` accepts, and whether a drain was broadcast AND reported.

The reader is stubbed on purpose and not mocked around. It is what let the whole
governor be built and tested before the producer existed, and it is what lets any
tier be exercised without waiting to actually be at 95% — the one tier where being
wrong costs work, and therefore the one you least want to first observe in
production.

WHAT THE SUITE DID NOT CATCH, recorded because it is the useful part: every tier
passed in isolation while the composition was wrong. Reading only the top tier's
own fields, the spoken configuration re-opened P2 dispatch at 95%, because that
tier declares no priority floor. It took driving the real CLI at 45/55/75/85/97
and reading the output to see it. `test_restriction_is_CUMULATIVE_and_monotone_in_usage`
is that bug's test; the lesson is that per-tier tests cannot see a per-tier
mistake.
"""
from __future__ import annotations

from shantytown.answer import Answer

import json
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest

from shantytown import config, governor as gov, tend as tend_mod
from shantytown.dispatch import Dispatcher, GovernorRefused
from shantytown.protocols import Agent, WorkItem
from shantytown.stopped import FilesStops
from shantytown.tmux import NullPanes


# The tiers exactly as Stiwi spoke them. Written as TOML in most tests rather
# than as Tier objects, because "thresholds changed in shantytown.toml take
# effect with NO code edit" is itself an acceptance item — a test that built the
# policy in Python would prove the mechanism while skipping the deliverable.
SPOKEN = """
[governor]
source = "stub"
relax_margin = 5

[[governor.tier]]
at = 50
min_priority = 1

[[governor.tier]]
at = 70
min_priority = 0

[[governor.tier]]
at = 80
traits = ["support"]

[[governor.tier]]
at = 95
action = "drain"

# `support` is a VALUE on the survival axis, never a magic string st ships and
# never a bare name on the card (ruled on the crew-traits design bead). A
# deployment that wants a support tier DECLARES what support is — which is the
# whole difference between a governed vocabulary and a free-text bag.
[roles.support]
attachment = "reports-to"
survival = "support"
lane = ["monitoring"]

# The coordinator is banded `last` VIA A ROLE, never implicitly protected. The
# rule is explicit: an agent that must outlive the others is one the deployment
# SAYS so about, because nobody can grep for a protection nobody wrote down.
[roles.coordinator]
attachment = "rooted"
survival = "last"
"""


def _policy(tmp_path, text: str = SPOKEN) -> gov.Policy:
    (tmp_path / "shantytown.toml").write_text(text)
    return config.load(tmp_path).governor


def _gov(tmp_path, pct, *, text: str = SPOKEN, now=1_000_000.0, ok=True,
         at=None, state=True) -> gov.Governor:
    policy = _policy(tmp_path, text)
    reader = gov.StubReader(pct=pct, at=now if at is None else at, ok=ok,
                            now=lambda: now)
    return gov.Governor(policy, reader,
                        gov.FilesGovernorState(tmp_path) if state else None,
                        now=lambda: now)


def _catalog(tmp_path, text: str = SPOKEN):
    (tmp_path / "shantytown.toml").write_text(text)
    return config.load(tmp_path).catalog()


def _item(item_id="st-1", priority=None, **kw) -> WorkItem:
    return WorkItem(id=item_id, title="t", priority=priority, **kw)


# --- the tiers, one per spoken threshold --------------------------------------

@pytest.mark.parametrize("pct,expected", [
    (45, None),      # under everything: wide open
    (55, 50),
    (75, 70),
    (85, 80),
    (97, 95),
])
def test_the_reading_selects_the_spoken_tier(tmp_path, pct, expected):
    v = _gov(tmp_path, pct).evaluate()
    assert (v.tier.at if v.tier else None) == expected
    assert v.pct == pct
    assert not v.signal_lost


def test_at_45_nothing_is_restricted(tmp_path):
    """Under the first threshold the governor must be INVISIBLE. A feature that
    quietly changes behaviour below its own floor is one nobody can reason about."""
    v = _gov(tmp_path, 45).evaluate()
    assert v.admits(_item(priority=4)) == ""
    assert v.excludes(Agent(name="tim")) == ""
    assert not v.drains


def test_at_55_only_p1_and_above_dispatch(tmp_path):
    v = _gov(tmp_path, 55).evaluate()
    assert v.admits(_item(priority=0)) == ""
    assert v.admits(_item(priority=1)) == ""
    refusal = v.admits(_item("st-9", priority=2))
    assert refusal, "P2 dispatched under the P1+ tier"
    assert "50%" in refusal and "st-9" in refusal and "P2" in refusal, (
        "a refusal that names neither the tier nor the item teaches nothing")


def test_at_75_only_p0_dispatches(tmp_path):
    v = _gov(tmp_path, 75).evaluate()
    assert v.admits(_item(priority=0)) == ""
    assert v.admits(_item(priority=1)), "P1 dispatched under the P0-only tier"


def test_an_item_with_no_priority_is_refused_under_a_floor(tmp_path):
    """Refused, not admitted — and the direction is the whole point. The
    governor's job above 50% is to spend what is left only on work whose
    importance is STATED; admitting an item nobody prioritised would defeat it
    silently. The refusal is per-item and fixable in one command."""
    v = _gov(tmp_path, 55).evaluate()
    why = v.admits(_item("st-7", priority=None))
    assert why and "NO priority" in why
    assert "--priority" in why, "a refusal with no remedy is a dead end"
    # ...and with NO tier engaged, a priority-less item still dispatches. The
    # governor must not become a general-purpose priority requirement.
    # (state=False: no hysteresis store, so this reading alone decides. With one,
    # 45 is still inside the 50% tier's relax band and the hold would rightly
    # keep the floor on — which is a different test, two below.)
    assert _gov(tmp_path, 45, state=False).evaluate().admits(
        _item(priority=None)) == ""


def test_at_85_only_support_crew_runs(tmp_path):
    """The 80% tier filters over the agent's ROLE SET resolved through the
    catalog, not a name list. A hardcoded roster rots the first time somebody is
    hired; a bare name on the card would be a vocabulary nothing governs."""
    v, cat = _gov(tmp_path, 85).evaluate(), _catalog(tmp_path)
    support = Agent(name="ellie", role="worker", roles=("worker", "support"))
    ordinary = Agent(name="tim", role="worker", roles=("worker",))
    assert v.excludes(support, cat) == ""
    assert v.excludes(ordinary, cat), "a non-support agent ran at the 80% tier"
    assert "80%" in v.excludes(ordinary, cat)
    # The 80% tier declares no floor of its own — but the 50% and 70% tiers are
    # STILL ENGAGED at 85%, so their floor holds. Restriction is cumulative.
    assert v.admits(_item(priority=4)), "the P0 floor vanished at a higher tier"
    assert v.admits(_item(priority=0)) == ""


def test_restriction_is_CUMULATIVE_and_monotone_in_usage(tmp_path):
    """A higher tier that declares no floor INHERITS the one below it. Reading
    only the top tier's own fields, the spoken config re-opened P2 dispatch at
    95% — the fleet being told to stop and push its work, while a coordinator
    could still hand out chores. Restriction can only ever tighten as usage
    climbs; found by driving the real CLI at 45/55/75/85/97, not by a unit test,
    because every tier passed in isolation."""
    for pct in (75, 85, 97):
        v = _gov(tmp_path, pct).evaluate()
        assert v.admits(_item("st-x", priority=2)), (
            f"P2 dispatched at {pct}% — the floor from a LOWER tier was dropped")
    # ...and the drain tier dispatches nothing at all, P0 included: a "full stop"
    # that still admits a P0 is not a full stop, and the session it lands in is
    # the one that has just been told to end.
    v = _gov(tmp_path, 97).evaluate()
    why = v.admits(_item("st-y", priority=0))
    assert why and "FULL STOP" in why
    assert "untouched" in why, "a refusal that does not say the work is safe"


def test_the_trait_tier_is_a_THRESHOLD_not_an_equality(tmp_path):
    """`traits = ["support"]` means support AND ABOVE. Equality matching would
    spin down an agent banded `last` while keeping the watchers — the coordinator
    gone and the monitors left, which is upside down.

    Note WHERE the band comes from: the `coordinator` role declares it, not the
    `administrator` tree position. An administrator carries no band of its own —
    see the never-implicitly-protected test below."""
    v, cat = _gov(tmp_path, 85).evaluate(), _catalog(tmp_path)
    admin = Agent(name="sattler", role="administrator",
                  roles=("administrator", "coordinator"))
    assert v.excludes(admin, cat) == "", (
        "the 80% tier spun down the coordinator: `last` outranks `support`, and "
        "the agent that has to coordinate a drain must survive every tier that "
        "spares anybody")
    assert v.excludes(Agent(name="tim", role="worker"), cat)


def test_an_UNDECLARED_administrator_is_NOT_implicitly_protected(tmp_path):
    """The ruling's sharpest requirement, and the one a reasonable implementation
    gets wrong by being helpful: nothing seeds a survival band from a role NAME.
    An administrator whose card declares no band is `normal` like everyone else
    and sheds with them. Implicit protection is how a fleet acquires agents no
    throttle can touch, and it is invisible to every audit."""
    v, cat = _gov(tmp_path, 85).evaluate(), _catalog(tmp_path)
    assert v.excludes(Agent(name="sattler", role="administrator"), cat), (
        "an administrator survived the support tier on the strength of its role "
        "NAME — that protection is nowhere on the card and nobody can grep it")


def test_a_role_name_alone_is_NOT_a_trait(tmp_path):
    """The card may not smuggle a vocabulary past the catalog. `roles = [...,
    "support"]` with nothing DECLARING what support is resolves to nothing, and
    an unresolvable agent fails OPEN (it runs) rather than being matched on the
    strength of a string."""
    text = SPOKEN.replace('''[roles.support]
attachment = "reports-to"
survival = "support"
lane = ["monitoring"]''', "")
    v, cat = _gov(tmp_path, 85, text=text).evaluate(), _catalog(tmp_path, text)
    assert v.excludes(Agent(name="ellie", roles=("worker", "support")), cat) == ""


def test_a_role_may_CARRY_the_trait_rather_than_be_it(tmp_path):
    """Both declarations are legitimate: `roles = [..., "support"]` on the card,
    or a role whose trait VALUES include it. A deployment that tags an existing
    role must not have to rename its crew."""
    text = SPOKEN + """
[roles.oncall]
attachment = "reports-to"
survival = "support"
lane = ["monitoring", "quipu"]
"""
    v, cat = _gov(tmp_path, 85, text=text).evaluate(), _catalog(tmp_path, text)
    assert v.excludes(Agent(name="ian", role="worker", roles=("oncall",)),
                      cat) == ""
    assert v.excludes(Agent(name="tim", role="worker", roles=("worker",)), cat)


def test_an_unreadable_role_lets_the_agent_RUN(tmp_path):
    """FAIL-OPEN, and it is the fail-safe stated by name: a policy we cannot
    evaluate must never be the reason the crew stops. An unknown role is a
    could-not-tell, and a could-not-tell keeps the agent alive."""
    v = _gov(tmp_path, 85).evaluate()

    class _Blows:
        def of(self, roles):
            raise ValueError("unknown role")

    assert v.excludes(Agent(name="tim", roles=("mystery",)), _Blows()) == ""


def test_at_97_every_agent_drains(tmp_path):
    v = _gov(tmp_path, 97).evaluate()
    assert v.drains
    for a in (Agent(name="ellie", roles=("worker", "support")),
              Agent(name="sattler", role="administrator")):
        why = v.excludes(a)
        assert why and "FULL STOP" in why, (
            "the 95% tier spared an agent — support crew is the 80% tier's "
            "exemption, and a full stop that exempts anybody is not one")


# --- hysteresis ---------------------------------------------------------------

def test_71_then_69_does_not_reopen_p1_dispatch(tmp_path):
    """The acceptance line, verbatim. Without hysteresis a reading oscillating
    around 70 flips the fleet's dispatch policy every pass, and every flip is a
    decision an agent already acted on."""
    assert _gov(tmp_path, 71).evaluate().tier.at == 70
    v = _gov(tmp_path, 69).evaluate()
    assert v.tier.at == 70, "left the 70% tier at 69 with relax_margin = 5"
    assert v.held, "held by hysteresis, and it must SAY so"
    assert v.admits(_item(priority=1)), "P1 re-opened inside the hold"


def test_64_does_reopen_p1_dispatch(tmp_path):
    """Below (70 - 5) the hold ends. A ratchet that never releases is not
    hysteresis, it is a fleet that never comes back."""
    assert _gov(tmp_path, 71).evaluate().tier.at == 70
    v = _gov(tmp_path, 64).evaluate()
    assert v.tier.at == 50 and not v.held
    assert v.admits(_item(priority=1)) == "", "P1 still refused after the release"


def test_hysteresis_never_holds_a_tier_LOWER_than_the_reading(tmp_path):
    """It can only ever be MORE conservative than the live number. If a hold
    could suppress a higher tier, the governor would spend past a threshold on
    the strength of a stale decision."""
    _gov(tmp_path, 55).evaluate()             # engage 50
    assert _gov(tmp_path, 97).evaluate().tier.at == 95


def test_a_dispatch_never_ratchets_the_hold(tmp_path):
    """`st go`'s path is persist=False. Fleet policy must not depend on how often
    somebody typed a dispatch command."""
    _gov(tmp_path, 71).evaluate(persist=False)
    assert gov.FilesGovernorState(tmp_path).get().at is None


# --- signal lost --------------------------------------------------------------

STALE_AT = 1_000_000.0 - 10_000        # far past the default max_age


def test_a_stale_probe_is_signal_lost_not_a_reading(tmp_path):
    """THE fail-safe. A governor reading a FROZEN number is worse than no
    governor: it holds the fleet wide open at a stale 12% and reads green the
    whole time."""
    v = _gov(tmp_path, 12, at=STALE_AT).evaluate()
    assert v.signal_lost and v.tier is None
    assert "STALE" in v.why


def test_probe_success_zero_is_signal_lost(tmp_path):
    v = _gov(tmp_path, 44, ok=False).evaluate()
    assert v.signal_lost


def test_rotation_401_reuses_a_recent_cached_reading_only_within_grace():
    recent = gov.Reading(pct=44, at=999_700, ok=False, cache_age=300,
                         probe_http_status=401)
    assert recent.lost(1_000_000, 900) == ""

    expired = gov.Reading(pct=44, at=999_300, ok=False, cache_age=700,
                          probe_http_status=401)
    assert "probe FAILED" in expired.lost(1_000_000, 900)


@pytest.mark.parametrize("status", [0, 403, 429, 500])
def test_non_rotation_probe_failures_are_immediately_signal_lost(status):
    reading = gov.Reading(pct=44, at=999_700, ok=False, cache_age=300,
                          probe_http_status=status)
    assert "probe FAILED" in reading.lost(1_000_000, 900)


def test_warn_runs_the_fleet_and_alarms_every_pass(tmp_path):
    """warn is the DEFAULT because an idled fleet from a broken probe is its own
    outage — but it must never go quiet about it."""
    g = _gov(tmp_path, 12, at=STALE_AT)
    for _ in range(3):
        v = g.evaluate()
        assert not v.frozen
        assert v.admits(_item(priority=4)) == "", "warn refused a dispatch"
        assert v.excludes(Agent(name="tim")) == "", "warn spun an agent down"
        assert v.alarm and "UNGOVERNED" in v.alarm, (
            "the alarm went quiet — a governor that stops complaining when it "
            "cannot see is indistinguishable from one with nothing to report")


def test_signal_lost_alarm_names_the_governor_harness(tmp_path):
    """A real Codex sentinel must never look like a Claude/base outage."""
    codex = gov.Governor(_policy(tmp_path),
                         gov.StubReader(pct=12, at=STALE_AT,
                                        now=lambda: 1_000_000),
                         gov.FilesGovernorState(tmp_path), now=lambda: 1_000_000,
                         name="codex")
    base = _gov(tmp_path, 12, at=STALE_AT)
    assert "USAGE SIGNAL LOST [codex]" in codex.evaluate().alarm
    assert "USAGE SIGNAL LOST [base]" in base.evaluate().alarm


def test_freeze_dispatches_nothing_new_and_still_never_drains(tmp_path):
    text = SPOKEN.replace('source = "stub"',
                          'source = "stub"\non_signal_lost = "freeze"')
    v = _gov(tmp_path, 12, text=text, at=STALE_AT).evaluate()
    assert v.frozen
    assert v.admits(_item(priority=0)), "freeze dispatched new work"
    assert "FROZEN" in v.admits(_item(priority=0))
    # NEVER FAIL INTO DRAIN. A probe bug must not be able to stop the crew.
    assert not v.drains
    assert v.excludes(Agent(name="tim")) == ""


def test_a_reader_that_raises_is_signal_lost_not_a_crash(tmp_path):
    class _Boom:
        def read(self):
            raise RuntimeError("prometheus fell over")

    g = gov.Governor(_policy(tmp_path), _Boom(),
                     gov.FilesGovernorState(tmp_path), now=lambda: 1.0)
    v = g.evaluate()
    assert v.signal_lost and not v.drains
    assert "prometheus fell over" in v.why


def test_losing_the_signal_does_not_clear_an_engaged_tier(tmp_path):
    """Blindness is not evidence that usage FELL. The hold stays recorded; it is
    simply not applied while we cannot see."""
    _gov(tmp_path, 97).evaluate()
    _gov(tmp_path, 97, at=STALE_AT).evaluate()
    assert gov.FilesGovernorState(tmp_path).get().at == 95


# --- the config IS the deliverable --------------------------------------------

def test_thresholds_move_with_no_code_edit(tmp_path):
    """"certain configured intervals" is what was asked for. A deployment that
    wants 40/60 edits TOML and touches no Python."""
    text = """
[governor]
source = "stub"

[[governor.tier]]
at = 40
min_priority = 3
"""
    v = _gov(tmp_path, 45, text=text).evaluate()
    assert v.tier.at == 40
    assert v.admits(_item(priority=3)) == ""
    assert v.admits(_item(priority=4))


def test_no_governor_table_means_off(tmp_path):
    (tmp_path / "shantytown.toml").write_text("[startup]\nmode = \"lite\"\n")
    cfg = config.load(tmp_path)
    assert not cfg.governor.active
    v = gov.Governor(cfg.governor, gov.StubReader(pct=99),
                     gov.FilesGovernorState(tmp_path)).evaluate()
    assert v.tier is None and not v.drains and not v.signal_lost
    assert v.admits(_item(priority=4)) == ""


def test_an_unknown_key_is_refused_and_names_the_file(tmp_path):
    (tmp_path / "shantytown.toml").write_text(
        '[governor]\nrelax_margins = 5\n[[governor.tier]]\nat = 50\n'
        'min_priority = 1\n')
    with pytest.raises(config.ConfigError) as e:
        config.load(tmp_path)
    assert "relax_margins" in str(e.value)
    assert "shantytown.toml" in str(e.value), "an error that does not say WHERE"


def test_a_tier_that_restricts_nothing_is_refused(tmp_path):
    """It would read as a policy in force while the fleet spends exactly as it
    did before — the silent-no-op failure this repo keeps paying for."""
    (tmp_path / "shantytown.toml").write_text('[[governor.tier]]\nat = 50\n')
    with pytest.raises(config.ConfigError) as e:
        config.load(tmp_path)
    assert "restricts NOTHING" in str(e.value)


def test_two_tiers_at_one_threshold_are_refused(tmp_path):
    (tmp_path / "shantytown.toml").write_text(
        '[[governor.tier]]\nat = 50\nmin_priority = 1\n'
        '[[governor.tier]]\nat = 50\naction = "drain"\n')
    with pytest.raises(config.ConfigError) as e:
        config.load(tmp_path)
    assert "twice" in str(e.value)


def test_a_prometheus_source_with_no_url_refuses_rather_than_stubbing(tmp_path):
    """A governor that quietly read a stub instead of Prometheus would report
    'no restriction' forever — the holds-the-fleet-open failure, arriving as a
    successful startup."""
    pol = _policy(tmp_path, SPOKEN.replace('source = "stub"',
                                           'source = "prometheus"'))
    with pytest.raises(gov.GovernorError):
        gov.reader_for(pol)


# --- the readers --------------------------------------------------------------

# The exposition as the producer actually writes it — per-account series with an
# `account` label, `five_hour`/`seven_day` windows, and NO recording rule (that is
# computed by Prometheus, not written to the file).
TEXTFILE = """# HELP claude_usage_utilization_pct utilization
claude_usage_utilization_pct{account="acct-a",window="five_hour"} 82.5
claude_usage_utilization_pct{account="acct-a",window="seven_day"} 41
claude_usage_probe_success{account="acct-a"} 1
claude_usage_probe_http_status{account="acct-a"} 200
claude_usage_probe_timestamp_seconds{account="acct-a"} 1000000
claude_usage_cache_age_seconds{account="acct-a"} 0
"""


def test_the_textfile_reader_picks_the_configured_window(tmp_path):
    p = tmp_path / "claude.prom"
    p.write_text(TEXTFILE)
    assert gov.TextfileReader(p, gov.FIVE_HOUR).read().pct == 82.5
    assert gov.TextfileReader(p, gov.SEVEN_DAY).read().pct == 41
    # A window nobody publishes is could-not-look, never 0.
    assert gov.TextfileReader(p, "30d").read().pct is None


def test_the_textfile_reader_takes_the_MAX_across_accounts(tmp_path):
    """The recording rule is `max by (window)` and is not in the file, so this
    reader does the same aggregation itself. Governing by whichever account
    sorted last would spend an exhausted account's budget it does not have."""
    p = tmp_path / "claude.prom"
    p.write_text(TEXTFILE + '''claude_usage_utilization_pct{account="two",window="five_hour"} 88
claude_usage_probe_success{account="two"} 1
claude_usage_probe_timestamp_seconds{account="two"} 1000000
''')
    assert gov.TextfileReader(p, gov.FIVE_HOUR).read().pct == 88


def test_ANY_blind_account_blinds_the_governor(tmp_path):
    """probe_success is per-account. A probe that succeeded for one account says
    nothing about another, so the fold is MIN — flying blind on any input is
    flying blind."""
    p = tmp_path / "claude.prom"
    p.write_text(TEXTFILE + '''claude_usage_utilization_pct{account="two",window="five_hour"} 88
claude_usage_probe_success{account="two"} 0
''')
    assert not gov.TextfileReader(p, gov.FIVE_HOUR).read().ok


def test_a_retained_percentage_with_a_climbing_cache_age_is_STALE(tmp_path):
    """When a probe fails the producer KEEPS serving the last good percentages.
    So the number can look current while being minutes old — the frozen-value
    failure arriving through a series that is present and parseable."""
    p = tmp_path / "claude.prom"
    p.write_text(TEXTFILE.replace(
        'claude_usage_cache_age_seconds{account="acct-a"} 0',
        'claude_usage_cache_age_seconds{account="acct-a"} 4000'))
    r = gov.TextfileReader(p, gov.FIVE_HOUR).read()
    assert "STALE" in r.lost(1_000_000.0, 900), (
        "a 4000s-old cached percentage read as a current measurement")


def test_textfile_rotation_401_is_bounded_but_429_is_not(tmp_path):
    p = tmp_path / "claude.prom"
    rotation = (TEXTFILE
                .replace('claude_usage_probe_success{account="acct-a"} 1',
                         'claude_usage_probe_success{account="acct-a"} 0')
                .replace('claude_usage_probe_http_status{account="acct-a"} 200',
                         'claude_usage_probe_http_status{account="acct-a"} 401')
                .replace('claude_usage_cache_age_seconds{account="acct-a"} 0',
                         'claude_usage_cache_age_seconds{account="acct-a"} 300'))
    p.write_text(rotation)
    assert gov.TextfileReader(p, gov.FIVE_HOUR).read().lost(1_000_000, 900) == ""

    p.write_text(rotation.replace(
        'claude_usage_probe_http_status{account="acct-a"} 401',
        'claude_usage_probe_http_status{account="acct-a"} 429'))
    assert "HTTP 429" in gov.TextfileReader(
        p, gov.FIVE_HOUR).read().lost(1_000_000, 900)


def test_a_value_with_no_success_flag_is_not_vouched_for(tmp_path):
    """Inferring "it must have worked" from the presence of a number is exactly
    the assumption the success flag exists to remove."""
    p = tmp_path / "claude.prom"
    p.write_text('claude_usage_utilization_pct{window="five_hour"} 82.5\n')
    r = gov.TextfileReader(p, gov.FIVE_HOUR).read()
    assert r.lost(1_000_000.0, 900)


def test_an_absent_textfile_is_could_not_look(tmp_path):
    r = gov.TextfileReader(tmp_path / "nope.prom", "5h").read()
    assert r.pct is None and r.lost(0, 900)


def test_the_prometheus_reader_parses_an_instant_vector(tmp_path):
    body = json.dumps({"status": "success", "data": {"result": [
        # The RECORDING RULE — already max-across-accounts, and the only usage
        # number the governor is allowed to read.
        {"metric": {"__name__": "claude:usage_utilization_pct:max",
                    "window": "five_hour"}, "value": [1_000_000, "63.25"]},
        # ...alongside a LOWER per-account series, which must not win.
        {"metric": {"__name__": "claude_usage_utilization_pct",
                    "account": "acct-a", "window": "five_hour"},
         "value": [1_000_000, "11"]},
        {"metric": {"__name__": "claude_usage_probe_success",
                    "account": "acct-a"}, "value": [1_000_000, "1"]},
        {"metric": {"__name__": "claude_usage_probe_http_status",
                    "account": "acct-a"}, "value": [1_000_000, "200"]},
        {"metric": {"__name__": "claude_usage_probe_timestamp_seconds",
                    "account": "acct-a"}, "value": [1_000_000, "1000000"]},
    ]}})
    r = gov.PrometheusReader("http://p.example", gov.FIVE_HOUR,
                             fetch=lambda url: body).read()
    assert r.pct == 63.25, "governed by a per-account series over the rule"
    assert r.ok and r.at == 1_000_000
    assert r.probe_http_status == 200
    assert r.lost(1_000_000.0, 900) == ""


def test_the_prometheus_query_actually_MATCHES_the_recording_rule():
    """The rule's name carries a COLON. A `claude_usage_.*` pattern misses it
    entirely and silently falls back to per-account — a governor reading the
    wrong series while every test about parsing still passes."""
    import re as _re
    pattern = _re.search(r'=~"([^"]+)"', gov.PrometheusReader.QUERY).group(1)
    assert _re.fullmatch(pattern, gov.USAGE_METRIC)
    assert _re.fullmatch(pattern, gov.PROBE_OK_METRIC)
    assert _re.fullmatch(pattern, gov.PROBE_HTTP_STATUS_METRIC)


def test_an_unreachable_prometheus_is_a_reading_not_an_exception(tmp_path):
    def _boom(url):
        raise OSError("connection refused")

    r = gov.PrometheusReader("http://p.example", fetch=_boom).read()
    assert r.pct is None and "unreachable" in r.lost(0, 900)


# --- tend: which agents stay alive --------------------------------------------

class _Panes(NullPanes):
    def capture(self, pane, history=0, **kw):
        return "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle)"


class _Runtime:
    name = "fake"

    def __init__(self):
        self.started = []

    def shows_ready_ui(self, screen):
        return "shift+tab to cycle" in screen

    def start(self, card, pane):
        self.started.append(card.name)


def _pass(tmp_path, pct, agents, *, live=(), text=SPOKEN):
    v, cat = _gov(tmp_path, pct, text=text).evaluate(), _catalog(tmp_path, text)
    rt = _Runtime()
    tender = tend_mod.Tender(
        _Panes(live=set(live)), rt, None, spawn=rt.start,
        ensure=lambda card: card.workspace, catalog=cat,
        governed=lambda card: v.excludes(card, cat))
    return v, rt, tender.pass_over(agents)


SUPPORT = Agent(name="ellie", pane="p-ellie", roles=("worker", "support"))
ORDINARY = Agent(name="tim", pane="p-tim", roles=("worker",))
ADMIN = Agent(name="sattler", pane="p-sattler", role="administrator")


def test_below_the_trait_tier_tend_respawns_everybody(tmp_path):
    _, rt, rep = _pass(tmp_path, 75, [SUPPORT, ORDINARY])
    assert sorted(rt.started) == ["ellie", "tim"]
    assert rep.healthy()


def test_at_85_tend_brings_up_only_support_crew(tmp_path):
    _, rt, rep = _pass(tmp_path, 85, [SUPPORT, ORDINARY])
    assert rt.started == ["ellie"], "respawned a non-support agent at the 80% tier"
    held = [f for f in rep.findings if f.verdict == tend_mod.GOVERNED]
    assert [f.agent for f in held] == ["tim"]
    assert rep.healthy(), (
        "a governed hold is a CAP, not a fault — exiting non-zero would page "
        "somebody every five minutes for the governor doing its job")
    assert "comes back" in held[0].why, "a hold that reads as permanent"


def test_at_97_tend_brings_up_nobody(tmp_path):
    _, rt, rep = _pass(tmp_path, 97, [SUPPORT, ORDINARY])
    assert rt.started == []
    assert {f.verdict for f in rep.findings} == {tend_mod.GOVERNED}


def test_tend_never_KILLS_a_live_excluded_agent(tmp_path):
    """tend's whole design argument is that a supervisor which can also kill is a
    scheduler with a supervisor's permissions. Spinning down is the drain
    protocol ASKING — and a live agent keeps its health checks meanwhile, so a
    deaf or auth-dead agent is not hidden for as long as the tier holds."""
    _, rt, rep = _pass(tmp_path, 97, [ORDINARY], live=["p-tim"])
    verdicts = {f.agent: f.verdict for f in rep.findings}
    assert verdicts["tim"] != tend_mod.GOVERNED
    assert rt.started == []


def test_a_governed_agent_does_not_eat_a_target_slot(tmp_path):
    """Otherwise --target N silently means something different whenever a tier is
    engaged: the slot is held by an agent the governor will not let up."""
    v, cat = _gov(tmp_path, 85).evaluate(), _catalog(tmp_path)
    rt = _Runtime()
    tender = tend_mod.Tender(
        _Panes(live=set()), rt, None, spawn=rt.start,
        ensure=lambda card: card.workspace, target=1, catalog=cat,
        governed=lambda card: v.excludes(card, cat))
    tender.pass_over([ORDINARY, SUPPORT])
    assert rt.started == ["ellie"], (
        "the one target slot went to the agent the governor will not let up, so "
        "the fleet sat below target with the slot held by nobody")


def test_a_broken_governor_callable_never_stops_the_fleet(tmp_path):
    """FAIL-OPEN, in the supervisor too. A policy that cannot be evaluated must
    not be the reason a fleet stays down."""
    rt = _Runtime()

    def _boom(card):
        raise RuntimeError("policy exploded")

    tend_mod.Tender(_Panes(live=set()), rt, None, spawn=rt.start,
                    ensure=lambda card: card.workspace,
                    governed=_boom).pass_over([ORDINARY])
    assert rt.started == ["tim"]


# --- st go: the dispatch gate -------------------------------------------------

class _Registry:
    def __init__(self, agents):
        self._a = {x.name: x for x in agents}

    def get(self, name):
        return self._a[name]

    def all(self):
        return Answer.complete_read(list(self._a.values()), how="test registry")


class _Tracker:
    def __init__(self, item):
        self.item = item
        self.updates = []

    def get(self, item_id):
        return self.item

    def update(self, item_id, **fields):
        self.updates.append((item_id, fields))
        # AND IT APPLIES THEM (aegis-8xc5w). `st go` now reads its tracker write
        # back, so a double that records the call and changes nothing looks
        # exactly like the swallowed write that fix exists to catch. These tests
        # are about the GOVERNOR; the tracker here is scenery and has to behave
        # like a working one.
        self.item = replace(self.item, **{
            k: v for k, v in fields.items() if hasattr(self.item, k)})

    def create(self, title, **fields):
        raise AssertionError("not used")


def _dispatcher(tmp_path, pct, item):
    v = _gov(tmp_path, pct).evaluate()
    tracker = _Tracker(item)
    panes = _Panes(live={"p-tim"})
    return Dispatcher(_Registry([ORDINARY]), tracker, panes,
                      governor=v.admits), tracker


def test_a_refused_dispatch_writes_NOTHING_and_sends_NOTHING(tmp_path):
    """Every plan() refusal is a precondition failure: no tracker write, no send.
    A half-dispatch under a governor would be the worst of both — tokens spent
    and a policy that says they should not have been."""
    d, tracker = _dispatcher(tmp_path, 75, _item("st-2", priority=2))
    sent = []
    d.panes.send = lambda pane, text: sent.append(text)
    with pytest.raises(GovernorRefused) as e:
        d.go("st-2", "tim")
    assert tracker.updates == [] and sent == []
    assert "70%" in str(e.value)


def test_an_admitted_dispatch_is_untouched_by_the_governor(tmp_path):
    d, tracker = _dispatcher(tmp_path, 75, _item("st-3", priority=0))
    d.panes.send = lambda pane, text: None
    d.verify = lambda pane, item_id: True
    d.go("st-3", "tim")
    assert tracker.updates == [("st-3", {"status": "in_progress",
                                         "assignee": "tim"})]


def test_reassign_does_not_bypass_the_governor(tmp_path):
    """Reassignment is still a dispatch. The tier is about what the fleet may
    SPEND, not about who holds what."""
    d, _ = _dispatcher(tmp_path, 75,
                       _item("st-4", priority=3, assignee="ellie"))
    with pytest.raises(GovernorRefused):
        d.go("st-4", "tim", reassign=True)


def test_a_closed_item_reports_CLOSED_not_a_tier(tmp_path):
    """Ordering: a closed bead is wrong to dispatch at ANY usage level, and
    naming the tier would send the operator to the wrong fix."""
    from shantytown.dispatch import Closed
    d, _ = _dispatcher(tmp_path, 97, _item("st-5", priority=4, status="closed"))
    with pytest.raises(Closed):
        d.go("st-5", "tim")


def test_the_dispatch_budget_is_unchanged_by_the_governor(tmp_path):
    """One registry read, one tracker read, one tracker write, one READ-BACK, one
    send — the asserted budget. The gate is handed the item plan() already
    fetched, so it costs no round trip.

    THE GOVERNOR is what this test guards, and that is still exactly zero reads:
    the second read counted here belongs to the read-back `go` now does on its own
    write (aegis-8xc5w), and it is present with or without a governor. If this
    number moves again, ask whose call it was before raising it."""
    v = _gov(tmp_path, 45).evaluate()
    reads = []
    tracker = _Tracker(_item("st-6", priority=2))
    real_get = tracker.get
    tracker.get = lambda i: (reads.append(i), real_get(i))[1]
    d = Dispatcher(_Registry([ORDINARY]), tracker, _Panes(live={"p-tim"}),
                   governor=v.admits)
    d.panes.send = lambda pane, text: None
    d.verify = lambda pane, item_id: True
    d.go("st-6", "tim")
    assert len(reads) == 2, (
        "expected plan()'s resolution read + go()'s read-back, and nothing else — "
        "a third read here would be the governor buying a round trip")


# --- the drain protocol -------------------------------------------------------

class _Inbox:
    def __init__(self, fail=()):
        self.sent = []
        self._fail = set(fail)

    def deliver(self, to, body):
        if to in self._fail:
            raise RuntimeError("store unreachable")
        self.sent.append((to, body))
        return f"msg-{len(self.sent)}"


def _drainer(tmp_path, inbox, stops, now=2_000_000.0):
    return gov.Drainer(tmp_path, deliver=inbox.deliver, stops=stops,
                       now=lambda: now)


def test_97_broadcasts_a_drain_to_every_live_agent(tmp_path):
    v = _gov(tmp_path, 97).evaluate()
    inbox, stops = _Inbox(), FilesStops(tmp_path / "stopped")
    rows = _drainer(tmp_path, inbox, stops).sweep(
        [SUPPORT, ORDINARY], v, _gov(tmp_path, 97).episode(),
        live=lambda a: True)
    assert sorted(who for who, _ in inbox.sent) == ["ellie", "tim"]
    body = inbox.sent[0][1]
    for step in ("commit", "push", "st stop", gov.DRAIN_OK, gov.DRAIN_FAIL):
        assert step in body, f"the drain instruction omits {step!r}"
    assert all(r.state == gov.PENDING for r in rows), (
        "reported drained before any agent said it pushed")


def test_the_drain_message_fits_the_durable_channel(tmp_path):
    """The durable inbox maps a message to a tracker item titled `inbox: <body>`
    under bd's 500-char title cap, leaving 493. A drain instruction that could not
    be DELIVERED durably is a drain that does not survive the session it is
    killing — which is the entire point of the tier."""
    body = gov.drain_message("a-very-long-agent-name", 95, 99.5)
    assert len(body) <= 493, f"drain message is {len(body)} chars"


def test_a_down_agent_is_not_told_to_drain(tmp_path):
    """A durable 'stop yourself' waiting for a down agent fires the moment it
    next comes up — a self-perpetuating shutdown, arriving long after the tier
    relaxed."""
    v = _gov(tmp_path, 97).evaluate()
    inbox = _Inbox()
    _drainer(tmp_path, inbox, FilesStops(tmp_path / "stopped")).sweep(
        [SUPPORT, ORDINARY], v, 1.0, live=lambda a: a.name == "ellie")
    assert [who for who, _ in inbox.sent] == ["ellie"]


def test_the_drain_is_not_rebroadcast_every_pass(tmp_path):
    v = _gov(tmp_path, 97).evaluate()
    inbox, stops = _Inbox(), FilesStops(tmp_path / "stopped")
    ep = _gov(tmp_path, 97).episode()
    for _ in range(3):
        _drainer(tmp_path, inbox, stops).sweep([ORDINARY], v, ep,
                                               live=lambda a: True)
    assert len(inbox.sent) == 1, "re-spammed a drained agent every heartbeat"


def test_a_new_episode_tells_everybody_again(tmp_path):
    """Relax below the tier and climb back and the agents told the first time are
    GONE. Deduping on a boolean would silence the second drain."""
    v = _gov(tmp_path, 97).evaluate()
    inbox, stops = _Inbox(), FilesStops(tmp_path / "stopped")
    _drainer(tmp_path, inbox, stops).sweep([ORDINARY], v, 100.0,
                                           live=lambda a: True)
    _drainer(tmp_path, inbox, stops).sweep([ORDINARY], v, 200.0,
                                           live=lambda a: True)
    assert len(inbox.sent) == 2


def test_an_undelivered_drain_is_retried_not_recorded(tmp_path):
    """Recording a message that never left would make the report say 'pending'
    forever about an agent that was never asked."""
    v = _gov(tmp_path, 97).evaluate()
    logged = []
    d = gov.Drainer(tmp_path, deliver=_Inbox(fail=["tim"]).deliver,
                    stops=FilesStops(tmp_path / "stopped"),
                    log=logged.append, now=lambda: 1.0)
    rows = d.sweep([ORDINARY], v, 1.0, live=lambda a: True)
    assert rows == [] and any("could NOT reach" in m for m in logged)
    ok = _Inbox()
    rows = gov.Drainer(tmp_path, deliver=ok.deliver,
                       stops=FilesStops(tmp_path / "stopped"),
                       now=lambda: 2.0).sweep([ORDINARY], v, 1.0,
                                              live=lambda a: True)
    assert [who for who, _ in ok.sent] == ["tim"], "never retried"


def test_the_report_reads_the_agents_own_stop_records(tmp_path):
    """UNREPORTED IS NOT DRAINED. The report's whole content is what went up, and
    a report that assumed compliance would hide the exact failure this tier
    exists to prevent."""
    v = _gov(tmp_path, 97).evaluate()
    stops = FilesStops(tmp_path / "stopped")
    inbox = _Inbox()
    crew = [Agent(name=n, pane=f"p-{n}") for n in ("ellie", "tim", "ian", "amy")]
    d = _drainer(tmp_path, inbox, stops)
    d.sweep(crew, v, 1.0, live=lambda a: True)

    stops.record("ellie", at=2_000_100.0,
                 reason=f"{gov.DRAIN_OK} shantytown@51a9f18")
    stops.record("tim", at=2_000_100.0,
                 reason=f"{gov.DRAIN_FAIL} push rejected, rebase conflicts")
    stops.record("ian", at=2_000_100.0, reason="just stopping")
    # amy: told, never reported.

    rows = {r.agent: r for r in d.report(1.0)}
    assert rows["ellie"].state == gov.DRAINED
    assert rows["ellie"].pushed == "shantytown@51a9f18"
    assert rows["tim"].state == gov.FAILED and "rebase" in rows["tim"].why
    assert rows["ian"].state == gov.PENDING, (
        "a stop with no report counted as a push — a stop is not a push")
    assert rows["amy"].state == gov.PENDING


def test_a_stop_from_BEFORE_the_drain_is_not_compliance(tmp_path):
    """The older record describes a different decision. Reading it as compliance
    would report an agent drained that was stopped yesterday for other reasons."""
    v = _gov(tmp_path, 97).evaluate()
    stops = FilesStops(tmp_path / "stopped")
    stops.record("tim", at=1_000_000.0, reason=f"{gov.DRAIN_OK} old@sha")
    d = _drainer(tmp_path, inbox := _Inbox(), stops)
    d.sweep([ORDINARY], v, 1.0, live=lambda a: True)
    assert inbox.sent
    assert d.report(1.0)[0].state == gov.PENDING


def test_the_report_says_UNREPORTED_IS_NOT_DRAINED_while_any_is_pending(tmp_path):
    text = gov.render_drain([gov.Drained("tim", 1.0, gov.PENDING, why="w")])
    assert "UNREPORTED IS NOT DRAINED" in text
    ok = gov.render_drain([gov.Drained("tim", 1.0, gov.DRAINED, pushed="r@a")])
    assert "all reported" in ok and "r@a" in ok


def test_a_drained_agent_is_not_respawned_while_usage_stays_high(tmp_path):
    """The acceptance line. A supervisor that respawned what the governor just
    asked to stand down would fight it every five minutes, and the agent would
    burn a fresh context each time to be told to stop again."""
    v = _gov(tmp_path, 97).evaluate()
    stops = FilesStops(tmp_path / "stopped")
    inbox = _Inbox()
    d = _drainer(tmp_path, inbox, stops)
    d.sweep([ORDINARY], v, 1.0, live=lambda a: True)
    stops.record("tim", at=2_000_100.0, reason=f"{gov.DRAIN_OK} st@abc123")

    # tim has stopped itself; the pane is gone. The next pass must hold it.
    _, rt, rep = _pass(tmp_path, 97, [ORDINARY], live=[])
    assert rt.started == []
    assert [f.verdict for f in rep.findings] == [tend_mod.GOVERNED]
    assert d.report(1.0)[0].state == gov.DRAINED


def test_no_tier_clears_the_ledger_so_the_next_episode_is_clean(tmp_path):
    v_high = _gov(tmp_path, 97).evaluate()
    stops = FilesStops(tmp_path / "stopped")
    inbox = _Inbox()
    _drainer(tmp_path, inbox, stops).sweep([ORDINARY], v_high, 1.0,
                                           live=lambda a: True)
    assert gov.DrainLedger(tmp_path).agents() == ["tim"]
    v_low = _gov(tmp_path, 45).evaluate()
    _drainer(tmp_path, inbox, stops).sweep([ORDINARY], v_low, 0.0,
                                           live=lambda a: True)
    assert gov.DrainLedger(tmp_path).agents() == []


def test_signal_lost_never_drains_anybody(tmp_path):
    """Never fail INTO drain — restated at the sweep, because this is the layer
    that would actually send the messages."""
    v = _gov(tmp_path, 99, at=STALE_AT).evaluate()
    inbox = _Inbox()
    rows = _drainer(tmp_path, inbox, FilesStops(tmp_path / "stopped")).sweep(
        [SUPPORT, ORDINARY], v, 1.0, live=lambda a: True)
    assert inbox.sent == [] and rows == []


# --- rendering ----------------------------------------------------------------

def test_the_status_line_distinguishes_open_from_engaged_from_blind(tmp_path):
    """An operator who cannot see the governor working cannot tell it from one
    that is silently off."""
    assert "wide open" in _gov(tmp_path, 45).evaluate().render()
    engaged = _gov(tmp_path, 85).evaluate().render()
    assert "80% tier" in engaged and "support" in engaged
    blind = _gov(tmp_path, 45, at=STALE_AT).evaluate().render()
    assert "SIGNAL LOST" in blind and "UNGOVERNED" in blind


# --- basic auth, because this deployment's Prometheus requires it -------------

def test_the_auth_header_is_sent_when_a_username_is_configured(tmp_path):
    seen = {}
    # Exercise the REAL header builder, not the injected fetch seam: the header
    # IS the feature, and a test that went through `fetch=` would pass with the
    # auth code deleted.
    import base64
    expected = base64.b64encode(b"aegis:hunter2").decode()

    class _Resp:
        def read(self):
            return b'{"status":"success","data":{"result":[]}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request as ur
    real = ur.urlopen
    try:
        ur.urlopen = lambda req, timeout=None: (
            seen.update(req.headers), _Resp())[1]
        gov.PrometheusReader("http://p.example", username="aegis",
                             password="hunter2")._http("http://p.example/x")
    finally:
        ur.urlopen = real
    assert seen.get("Authorization") == f"Basic {expected}"
    # ...and no header at all when nothing is configured, so an unauthenticated
    # Prometheus is not sent a `Basic Og==`.
    seen.clear()
    try:
        ur.urlopen = lambda req, timeout=None: (
            seen.update(req.headers), _Resp())[1]
        gov.PrometheusReader("http://p.example")._http("http://p.example/x")
    finally:
        ur.urlopen = real
    assert "Authorization" not in seen


def test_a_401_is_named_a_CREDENTIAL_problem_not_an_outage():
    """"unreachable" is false in the expensive direction for a 401: the server is
    up and refusing us, and an operator told otherwise goes hunting a Prometheus
    outage instead of a missing password_file."""
    def _boom(url):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    why = gov.PrometheusReader("http://p.example", fetch=_boom).read().lost(0, 900)
    assert "401" in why and "password_file" in why
    assert "unreachable" not in why


def test_an_unreadable_password_file_REFUSES_rather_than_going_anonymous(tmp_path):
    """Falling through unauthenticated would produce a 401, report SIGNAL LOST,
    and send the operator after an outage that is really a file permission."""
    pol = gov.Policy(source="prometheus", url="http://p.example",
                     password_file=str(tmp_path / "nope"),
                     tiers=(gov.Tier(at=50, min_priority=1),))
    with pytest.raises(gov.GovernorError) as e:
        gov.reader_for(pol)
    assert "password_file" in str(e.value)


def test_the_password_is_a_FILE_not_a_value(tmp_path):
    """shantytown.toml is tracked, diffable and hand-edited — and it is the table
    an operator is most likely to paste into a bead when asking why the governor
    is blind. An inline password would be a secret in git."""
    assert "password" not in gov._GOV_KEYS
    assert "password_file" in gov._GOV_KEYS
    secret = tmp_path / "pw"
    secret.write_text("hunter2\n")
    pol = gov.Policy(source="prometheus", url="http://p.example",
                     username="aegis", password_file=str(secret),
                     tiers=(gov.Tier(at=50, min_priority=1),))
    assert gov.reader_for(pol).password == "hunter2", "trailing newline kept"


# --- the survival bands -------------------------------------------------------

def test_the_bands_order_from_first_to_last():
    from shantytown import traits
    cat = traits.default_catalog()
    ranks = [traits.DEFAULT_PRECEDENCE[(traits.SURVIVAL, b)]
             for b in traits.SURVIVAL_BANDS]
    assert ranks == sorted(ranks), "the band order is not monotone"
    assert traits.SURVIVAL_BANDS[0] == "first"
    assert traits.SURVIVAL_BANDS[-1] == "last"
    assert cat.precedence[(traits.SURVIVAL, "last")] > \
        cat.precedence[(traits.SURVIVAL, "support")] > \
        cat.precedence[(traits.SURVIVAL, "normal")] > \
        cat.precedence[(traits.SURVIVAL, "first")]


def test_traits_leaves_survival_None_and_the_THROTTLE_defaults_it(tmp_path):
    """"unset = normal" is the throttle's policy, so the throttle applies it.
    traits.py answering a trait question with a policy answer would make "has
    this card been given a band?" unanswerable."""
    from shantytown import traits
    cat = _catalog(tmp_path)
    plain = Agent(name="tim", role="worker")
    assert traits.survival_band(cat, plain) is None, (
        "traits.py invented a band nobody declared")
    # ...and the ORDER function applies the caller's default.
    assert traits.survival_rank(cat, plain) == \
        traits.DEFAULT_PRECEDENCE[(traits.SURVIVAL, "normal")]


def test_the_drain_asks_the_LOWEST_band_first(tmp_path):
    """Descending survival, so the last agents standing can still coordinate a
    drain. Backwards, this stops the administrator first and leaves nobody to
    supervise the shutdown."""
    text = SPOKEN + """
[roles.shedfirst]
attachment = "reports-to"
survival = "first"
"""
    v, cat = _gov(tmp_path, 97, text=text).evaluate(), _catalog(tmp_path, text)
    inbox = _Inbox()
    crew = [
        Agent(name="aaa_coord", pane="p1", roles=("administrator", "coordinator")),
        Agent(name="zzz_shed", pane="p2", roles=("worker", "shedfirst")),
        Agent(name="mmm_norm", pane="p3", roles=("worker",)),
    ]
    _drainer(tmp_path, inbox, FilesStops(tmp_path / "stopped")).sweep(
        crew, v, 1.0, live=lambda a: True, catalog=cat)
    told = [who for who, _ in inbox.sent]
    assert told == ["zzz_shed", "mmm_norm", "aaa_coord"], (
        f"drain order {told} — the coordinator must be asked LAST, and the "
        f"alphabetical names here would give the opposite order if the band "
        f"were being ignored")


# --- TWO BUDGETS, NOT ONE (aegis-59hao) ---------------------------------------
#
# The governor read exactly one window and every tier was judged against it, so
# it was blind to whichever budget was actually further gone — and blind in the
# expensive direction: a five-hour window refills in HOURS, the weekly in DAYS.
# Governing only the cheap-to-recover budget is backwards.
#
# The asymmetry below is deliberate and is this fleet's own judgement, carried
# over from the deleted corpse-alerts (aegis-0qur): 80/95 on the five-hour,
# 70/90 on the weekly — TIGHTER on the budget that takes days to come back.

TWO_WINDOW = """
[governor]
source = "stub"
relax_margin = 5

[[governor.tier]]
at = 80
traits = ["support"]

[[governor.tier]]
at = 95
action = "drain"

[[governor.tier]]
at = 70
window = "seven_day"
traits = ["support"]

[[governor.tier]]
at = 90
window = "seven_day"
action = "drain"

[roles.support]
attachment = "reports-to"
survival = "support"
"""


def test_an_EXHAUSTED_WEEKLY_sheds_even_when_the_five_hour_is_nearly_EMPTY(tmp_path):
    """THE test. This is the case that was wrong, and it is wrong SILENTLY.

    5h=20 is a fleet with plenty of short-term budget; 7d=85 is one that has
    spent most of a week's. Reading only the five-hour window, the governor sees
    20% and engages nothing at all — while the budget that takes DAYS to refill
    is past its threshold.
    """
    v = _gov(tmp_path, {"five_hour": 20, "seven_day": 85}, text=TWO_WINDOW).evaluate()
    assert v.trait_tiers, "the seven_day tier did not engage — the fleet does not shed"
    assert any(t.window == "seven_day" for t in v.engaged)
    assert not v.drains, "70 is the shed tier, not the drain tier"
    assert "seven_day" in v.why, "a refusal that does not name the budget teaches nothing"


def test_the_five_hour_still_engages_on_its_own_reading(tmp_path):
    """Symmetry, proven in the other direction — otherwise the fix could be
    'always read seven_day', which is the same bug facing the other way."""
    v = _gov(tmp_path, {"five_hour": 85, "seven_day": 20}, text=TWO_WINDOW).evaluate()
    assert v.trait_tiers
    assert any(t.window == "five_hour" for t in v.engaged)
    assert not any(t.window == "seven_day" for t in v.engaged)


def test_the_STRICTER_window_wins_and_they_are_never_averaged(tmp_path):
    """Two budgets both constrain. An average would let a fresh 5h mask a spent
    weekly — 20 and 90 average to 55, which engages NOTHING."""
    v = _gov(tmp_path, {"five_hour": 20, "seven_day": 90}, text=TWO_WINDOW).evaluate()
    assert v.drains, "the weekly drain tier did not engage"


def test_a_MISSING_window_governs_on_what_is_readable_and_ALARMS(tmp_path):
    """Partial blindness is still blindness — and still not a reason to stop the
    crew. Never fail INTO drain."""
    v = _gov(tmp_path, {"five_hour": 85}, text=TWO_WINDOW).evaluate()
    assert v.trait_tiers, "governed on the window it COULD read"
    assert any(t.window == "five_hour" for t in v.engaged)
    assert not v.drains, "a window we cannot see must never drain the fleet"
    assert "seven_day" in v.alarm and "PARTIALLY LOST" in v.alarm


def test_a_config_naming_NO_window_behaves_exactly_as_before(tmp_path):
    """The compatibility floor. Every shipped config omits `window`, and this
    change must be inert for all of them."""
    v = _gov(tmp_path, 85).evaluate()          # SPOKEN: no window anywhere
    assert [t.at for t in v.engaged] == [50, 70, 80]
    assert all(t.window == "five_hour" for t in v.engaged)
    assert not v.drains


def test_hysteresis_is_held_PER_WINDOW(tmp_path):
    """One remembered tier cannot describe two budgets on different clocks: a
    relaxing five-hour reading must not drop a hold the weekly still justifies."""
    g = _gov(tmp_path, {"five_hour": 85, "seven_day": 72}, text=TWO_WINDOW)
    g.evaluate()                                # both windows engage their 80/70
    # the five-hour falls into its relax band; the weekly does not move
    v = _gov(tmp_path, {"five_hour": 76, "seven_day": 72}, text=TWO_WINDOW).evaluate()
    assert any(t.window == "seven_day" for t in v.engaged), \
        "the weekly hold was dropped by a five-hour relax"


def test_a_seven_day_only_config_never_reads_the_five_hour(tmp_path):
    """windows() is derived from the TIERS: a window nothing governs is a read
    whose answer could not matter."""
    text = TWO_WINDOW.replace("at = 80\ntraits", "at = 80\nwindow = \"seven_day\"\ntraits") \
                     .replace("at = 95\naction", "at = 95\nwindow = \"seven_day\"\naction")
    pol = _policy(tmp_path, text)
    assert pol.windows() == ("seven_day",)


def test_an_unknown_window_is_REFUSED_not_silently_never_engaged(tmp_path):
    """A tier on a window nothing publishes would never fire, and a threshold
    that can never fire reads exactly like one that is simply never reached."""
    with pytest.raises(Exception) as e:
        _policy(tmp_path, TWO_WINDOW.replace('window = "seven_day"', 'window = "7d"'))
    assert "seven_day" in str(e.value)
