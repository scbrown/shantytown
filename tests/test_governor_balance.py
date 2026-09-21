"""The balance view: which lane should the next unit of work go to (aegis-03cstj).

The fixtures are the numbers from the directive itself, 2026-09-16 ~18:20Z, so these
tests fail if the recommendation would have been wrong on the day it was asked for.
"""

import math

from shantytown.governor_balance import (
    BALANCED, DEFAULT_BAND, PREFER, REFRESH, UNRATED, Balance, LanePace, balance,
)


def lane(name, ratio=None, **kw):
    return LanePace(lane=name, ratio=ratio, **kw)


class TestThreeRecommendations:
    """Acceptance: three fixtures producing the three recommendations."""

    def test_claude_heavy_prefers_codex(self):
        # base 31% used at 11% elapsed = 2.74x; codex genuinely fresh and idle.
        v = balance([lane("base", 2.74), lane("codex", 0.45)])
        assert v.verdict == PREFER
        assert v.prefer == "codex"
        assert v.actionable
        assert "base 2.74x" in v.why

    def test_codex_heavy_prefers_base(self):
        v = balance([lane("base", 0.40), lane("codex", 2.90)])
        assert v.verdict == PREFER
        assert v.prefer == "base"
        assert v.actionable

    def test_balanced_prefers_neither(self):
        v = balance([lane("base", 1.10), lane("codex", 1.00)])
        assert v.verdict == BALANCED
        assert v.prefer is None
        assert not v.actionable, "balanced must not steer a launch"


class TestStaleNeverDrives:
    """Acceptance: the stale fixture produces 'refresh first', NOT a restriction.

    This is the incident the directive was issued over. codex read 59% at 9% elapsed --
    a 6.5x pace, which on the numbers alone is the most alarming reading on the host --
    while ZERO codex agents had been live for hours, so the probe had not refreshed
    since the limit was reset. Pace-first would hold the only lane with budget.
    """

    def test_stale_lane_yields_refresh_not_a_preference(self):
        v = balance([lane("base", 2.74),
                     lane("codex", 6.55, stale_why="no codex session live since the "
                                                   "reading (age 4h)")])
        assert v.verdict == REFRESH
        assert v.prefer is None
        assert not v.actionable, "a stale probe must never steer a launch"
        assert "codex" in v.why and "no codex session live" in v.why

    def test_staleness_beats_pace_even_when_pace_is_computable(self):
        """Order matters: the stale lane HAS a ratio and it must still not be used."""
        stale = lane("codex", 6.55, stale_why="probe 4h old")
        assert stale.ratio is not None, "fixture must carry a real number to be a trap"
        assert not stale.usable
        assert balance([lane("base", 2.74), stale]).verdict == REFRESH

    def test_a_fresh_lane_with_the_same_numbers_DOES_recommend(self):
        """The control. Without this, the test above passes for a broken reason --
        e.g. if `balance` refused every two-lane input, we could not tell."""
        v = balance([lane("base", 2.74), lane("codex", 6.55)])
        assert v.verdict == PREFER and v.prefer == "base"


class TestCannotSayIsNotBalanced:
    def test_unrated_lane_is_unrated_not_balanced(self):
        v = balance([lane("base", 1.0),
                     lane("codex", None, unrated_why="no reset timestamp published")])
        assert v.verdict == UNRATED
        assert not v.actionable
        assert "no reset timestamp" in v.why

    def test_one_lane_cannot_be_balanced(self):
        v = balance([lane("base", 1.0)])
        assert v.verdict == UNRATED and "needs two" in v.why

    def test_no_lanes_at_all(self):
        assert balance([]).verdict == UNRATED

    def test_three_lanes_refuse_rather_than_pick_a_pair(self):
        v = balance([lane("base", 1.0), lane("codex", 3.0), lane("gemini", 0.1)])
        assert v.verdict == UNRATED
        assert "exactly two" in v.why


class TestZeroPace:
    def test_a_just_reset_lane_is_preferred_outright(self):
        v = balance([lane("base", 2.0), lane("codex", 0.0)])
        assert v.verdict == PREFER and v.prefer == "codex"
        assert math.isinf(v.ratio)

    def test_both_at_zero_is_balanced_not_a_division_error(self):
        v = balance([lane("base", 0.0), lane("codex", 0.0)])
        assert v.verdict == BALANCED and v.ratio == 1.0

    def test_zero_on_the_alphabetically_first_lane_too(self):
        v = balance([lane("base", 0.0), lane("codex", 2.0)])
        assert v.verdict == PREFER and v.prefer == "base"


class TestBand:
    def test_the_band_is_configurable_and_actually_changes_the_verdict(self):
        pair = [lane("base", 1.4), lane("codex", 1.0)]
        assert balance(pair, band=1.5).verdict == BALANCED
        assert balance(pair, band=1.2).verdict == PREFER
        assert balance(pair, band=1.2).prefer == "codex"

    def test_exactly_at_the_band_prefers(self):
        v = balance([lane("base", 1.5), lane("codex", 1.0)], band=1.5)
        assert v.verdict == PREFER and v.prefer == "codex"

    def test_default_band(self):
        assert DEFAULT_BAND == 1.5

    def test_order_of_the_input_does_not_change_the_answer(self):
        a = balance([lane("base", 2.74), lane("codex", 0.45)])
        b = balance([lane("codex", 0.45), lane("base", 2.74)])
        assert (a.verdict, a.prefer) == (b.verdict, b.prefer)


class TestLine:
    def test_prefer_line_names_the_lane_and_the_ratio(self):
        out = balance([lane("base", 2.74), lane("codex", 0.45)]).line()
        assert out.startswith("balance ") and "prefer codex" in out

    def test_refresh_line_says_REFRESH_not_a_number(self):
        out = balance([lane("base", 1.0),
                       lane("codex", 9.0, stale_why="probe 4h old")]).line()
        assert "REFRESH" in out and "prefer" not in out

    def test_balanced_line(self):
        assert "balanced" in balance([lane("base", 1.0), lane("codex", 1.0)]).line()


class TestPreferFirst:
    LANES = {"ana": "base", "bo": "codex", "cy": "base", "di": "codex"}

    def lane_of(self, n):
        return self.LANES[n]

    def order(self, verdict):
        from shantytown.governor_balance import prefer_first
        return prefer_first(["ana", "bo", "cy", "di"], self.lane_of, verdict)

    def test_preferred_lane_comes_first(self):
        v = balance([lane("base", 2.74), lane("codex", 0.45)])
        assert v.prefer == "codex"
        assert self.order(v) == ["bo", "di", "ana", "cy"]

    def test_it_never_filters(self):
        v = balance([lane("base", 2.74), lane("codex", 0.45)])
        assert sorted(self.order(v)) == ["ana", "bo", "cy", "di"]

    def test_order_within_a_lane_is_preserved(self):
        v = balance([lane("base", 0.45), lane("codex", 2.74)])
        assert self.order(v) == ["ana", "cy", "bo", "di"]

    def test_balanced_leaves_the_order_untouched(self):
        v = balance([lane("base", 1.0), lane("codex", 1.0)])
        assert self.order(v) == ["ana", "bo", "cy", "di"]

    def test_a_stale_probe_cannot_reorder_anything(self):
        v = balance([lane("base", 1.0), lane("codex", 9.0, stale_why="probe 4h old")])
        assert v.verdict == REFRESH
        assert self.order(v) == ["ana", "bo", "cy", "di"]

    def test_unrated_leaves_the_order_untouched(self):
        v = balance([lane("base", 1.0), lane("codex", None, unrated_why="no reset")])
        assert self.order(v) == ["ana", "bo", "cy", "di"]


class TestIdleFleetMessageCarriesTheLine:
    """Item 4: the balance line rides the alert that already interrupts the admin."""

    def test_the_line_appears_in_the_message(self):
        from shantytown.notify import _idle_fleet_message
        msg = _idle_fleet_message(["ana"], ["ana"], [("aegis-1", "t")],
                                  "balance 2.10x — prefer codex (x)")
        assert "prefer codex" in msg

    def test_a_refresh_verdict_is_carried_too_not_suppressed(self):
        from shantytown.notify import _idle_fleet_message
        msg = _idle_fleet_message(["ana"], ["ana"], [("aegis-1", "t")],
                                  "balance ?/? — REFRESH: codex probe 4h old")
        assert "REFRESH" in msg

    def test_no_balance_available_leaves_the_message_byte_identical(self):
        """The whole feature must be invisible when it has nothing to say."""
        from shantytown.notify import _idle_fleet_message
        a = _idle_fleet_message(["ana"], ["ana"], [("aegis-1", "t")])
        b = _idle_fleet_message(["ana"], ["ana"], [("aegis-1", "t")], "")
        assert a == b
        assert "balance" not in a


class TestLaneOfMapping:
    """`_lane_of` must agree with FleetGovernor.lane, including claude -> base."""

    def test_claude_maps_to_base_and_codex_to_codex(self):
        from shantytown.notify import IdleFleetAlerter
        from shantytown import notify as notify_mod

        class Card:
            pass

        class Reg:
            def get(self, name):
                return Card()

        alerter = IdleFleetAlerter.__new__(IdleFleetAlerter)
        alerter._reg = Reg()
        alerter._shanty_root = None
        seen = {}

        def fake_name_for(card, root=None):
            return seen['harness']
        original = notify_mod.harness_mod.name_for
        notify_mod.harness_mod.name_for = fake_name_for
        try:
            seen['harness'] = 'claude'
            assert alerter._lane_of('ana') == 'base'
            seen['harness'] = 'codex'
            assert alerter._lane_of('bo') == 'codex'
        finally:
            notify_mod.harness_mod.name_for = original

    def test_an_unresolvable_agent_is_lane_empty_not_an_exception(self):
        from shantytown.notify import IdleFleetAlerter

        class Reg:
            def get(self, name):
                raise KeyError(name)

        alerter = IdleFleetAlerter.__new__(IdleFleetAlerter)
        alerter._reg = Reg()
        alerter._shanty_root = None
        assert alerter._lane_of('ghost') == ''

    def test_an_unresolvable_agent_is_still_fed_just_not_first(self):
        """'' is a lane no verdict prefers, and prefer_first never filters."""
        from shantytown.governor_balance import prefer_first
        v = balance([lane("base", 2.74), lane("codex", 0.45)])
        lanes = {"ana": "base", "bo": "codex", "ghost": ""}
        out = prefer_first(["ana", "bo", "ghost"], lambda n: lanes[n], v)
        assert out[0] == "bo"
        assert set(out) == {"ana", "bo", "ghost"}
