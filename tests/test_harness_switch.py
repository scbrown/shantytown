"""Tests for `harness_switch` — the conversion planner and the cross-lane
recommendation (aegis-6glmer, Stiwi 2026-09-04).

Test names shout the invariant they turn on. Both halves are pure, so every arm
here runs without a fleet, a governor reading or a tmux pane — which is the
point: the evening that produced the directive failed on rules nobody could
state, and an unstatable rule is an untestable one.
"""

import unittest

from shantytown.harness_switch import (
    Lane, NoChange, Plan, Refusal, cross_harness_advice, plan_switch,
)

KNOWN = ("claude", "codex")


def switch(**kw):
    base = dict(agent="grant", current="codex", target="claude", role="worker",
                required_by_role={}, known=KNOWN)
    base.update(kw)
    return plan_switch(**base)


class ConversionPlanner(unittest.TestCase):

    def test_an_ordinary_worker_conversion_is_planned(self):
        got = switch()
        self.assertIsInstance(got, Plan)
        self.assertEqual((got.from_harness, got.to_harness), ("codex", "claude"))

    def test_a_repeat_is_NO_CHANGE_not_a_second_write(self):
        got = switch(current="claude", target="claude")
        self.assertIsInstance(got, NoChange)

    def test_idempotence_survives_a_pin_that_would_forbid_the_change_TODAY(self):
        # A verb that refuses to confirm the state it produced is not idempotent.
        # The lead is already claude and pinned to claude; asking again must
        # report NoChange, not trip the pin.
        got = switch(current="claude", target="claude", role="lead",
                     required_by_role={"lead": "claude"})
        self.assertIsInstance(got, NoChange)

    def test_a_lead_cannot_be_moved_OFF_its_pinned_harness(self):
        got = switch(current="claude", target="codex", role="lead",
                     required_by_role={"lead": "claude"})
        self.assertIsInstance(got, Refusal)
        self.assertEqual(got.code, "role-pinned")
        self.assertIn("claude", got.reason)

    def test_a_pin_on_ANOTHER_role_does_not_constrain_this_one(self):
        # Workers were unpinned on 2026-09-04; leads stayed pinned. A worker
        # conversion must not inherit the lead's rule.
        got = switch(role="worker", required_by_role={"lead": "claude"})
        self.assertIsInstance(got, Plan)

    def test_force_overrides_the_pin_but_SAYS_the_card_is_now_unlaunchable(self):
        got = switch(current="claude", target="codex", role="lead",
                     required_by_role={"lead": "claude"}, force=True)
        self.assertIsInstance(got, Plan)
        joined = " ".join(got.warnings)
        self.assertIn("FORCED", joined)
        # The consequence, not just the fact: require_role_harness runs at launch,
        # so a forced card is refused by `st new` until something changes.
        self.assertIn("refuse to launch", joined)

    def test_moving_ONTO_a_held_lane_is_refused(self):
        got = switch(lane_held=True)
        self.assertIsInstance(got, Refusal)
        self.assertEqual(got.code, "target-lane-held")

    def test_force_overrides_a_held_lane_and_says_so(self):
        got = switch(lane_held=True, force=True)
        self.assertIsInstance(got, Plan)
        self.assertIn("HELD", " ".join(got.warnings))

    def test_an_unknown_harness_is_refused_BEFORE_the_policy_gates(self):
        # A typo must be told it is a typo. Reporting lead-pinning to someone who
        # wrote `codx` sends them to the wrong problem entirely.
        got = switch(target="codx", role="lead",
                     required_by_role={"lead": "claude"})
        self.assertIsInstance(got, Refusal)
        self.assertEqual(got.code, "unknown-harness")

    def test_a_model_change_alone_is_a_real_change_not_a_no_op(self):
        got = switch(current="claude", target="claude",
                     current_model="sonnet", model="opus")
        self.assertIsInstance(got, Plan)
        self.assertEqual(got.model, "opus")

    def test_takes_effect_REFUSES_to_claim_a_running_agent_converted(self):
        # The card is authoritative at launch, so an un-restarted agent is still
        # running the old harness. Saying "converted" of that is the lie this
        # property exists to prevent.
        later = switch()
        self.assertIn("still running codex", later.takes_effect)
        now = switch(restart_now=True)
        self.assertIn("starting now", now.takes_effect)


class CrossLaneRecommendation(unittest.TestCase):
    """The bead's item 4, both directions."""

    def test_held_lane_plus_open_lane_RECOMMENDS_a_conversion(self):
        line = cross_harness_advice([
            Lane("codex", delta=-1, live=9, candidates=("a", "b", "c", "d")),
            Lane("base", delta=+3, live=3, harness="claude"),
        ])
        self.assertIsNotNone(line)
        self.assertIn("codex -1", line)
        self.assertIn("base +3", line)
        # The lane is called `base`; the thing you convert TO is `claude`, which
        # is what `st harness` takes. A line naming the lane is unactionable.
        self.assertIn("convert up to 3 codex workers to claude", line)
        self.assertIn("candidates: a, b, c", line)
        # capped by the RECEIVER's room, so the fourth candidate is not offered
        self.assertNotIn("d", line.split("candidates:")[1])

    def test_both_lanes_shrinking_recommends_NOTHING(self):
        self.assertIsNone(cross_harness_advice([
            Lane("codex", delta=-1, live=9),
            Lane("base", delta=-2, live=3),
        ]))

    def test_both_lanes_with_room_recommends_NOTHING(self):
        # The move here is to LAUNCH, which the per-lane advisories already say.
        # Recommending a conversion would trade a free agent for a moved one.
        self.assertIsNone(cross_harness_advice([
            Lane("codex", delta=+2, live=1),
            Lane("base", delta=+3, live=3),
        ]))

    def test_it_works_in_REVERSE_too(self):
        # The bead asks for both directions explicitly: base over bound, codex
        # with room.
        line = cross_harness_advice([
            Lane("codex", delta=+2, live=1),
            Lane("base", delta=-1, live=6, candidates=("x", "y"),
                 harness="claude"),
        ])
        self.assertIn("convert up to 2 claude workers to codex", line)
        self.assertIn("candidates: x, y", line)

    def test_a_HELD_lane_with_no_delta_still_donates(self):
        # `held` is the hysteresis case: the lane publishes no number but is
        # restricting. Requiring a negative delta would miss exactly the state
        # the fleet sat in all evening.
        line = cross_harness_advice([
            Lane("codex", held=True, live=9, candidates=("a",)),
            Lane("base", delta=+3, live=3),
        ])
        self.assertIn("codex held", line)
        self.assertIn("convert up to 3", line)

    def test_a_donor_with_NOBODY_on_it_recommends_nothing(self):
        # A held lane running zero agents has nobody to move; the recommendation
        # would be unactionable.
        self.assertIsNone(cross_harness_advice([
            Lane("codex", delta=-1, live=0),
            Lane("base", delta=+3, live=3),
        ]))

    def test_the_move_is_capped_by_who_is_actually_THERE(self):
        line = cross_harness_advice([
            Lane("codex", delta=-1, live=2, candidates=("a", "b")),
            Lane("base", delta=+5, live=1),
        ])
        self.assertIn("convert up to 2 codex workers", line)

    def test_a_lane_names_its_PROGRAM_not_itself(self):
        # `base` is a governor name; `claude` is what you can type.
        self.assertEqual(Lane("base", harness="claude").program, "claude")
        self.assertEqual(Lane("codex").program, "codex")

    def test_one_lane_alone_recommends_nothing(self):
        self.assertIsNone(cross_harness_advice([Lane("base", delta=+3, live=1)]))

    def test_missing_candidate_names_are_SAID_not_silently_omitted(self):
        # A bare recommendation looks like one whose list was empty for a reason.
        line = cross_harness_advice([
            Lane("codex", delta=-1, live=9),
            Lane("base", delta=+3, live=3),
        ])
        self.assertIn("candidates: none with ready work", line)

    def test_a_lane_with_no_reading_never_donates_or_receives(self):
        # delta None + not held is "we could not tell", and a fleet move made on
        # a lane nobody can read is the failure the governor's fail-safe forbids.
        self.assertIsNone(cross_harness_advice([
            Lane("codex", delta=None, live=9),
            Lane("base", delta=None, live=3),
        ]))
        self.assertIsNone(cross_harness_advice([
            Lane("codex", delta=None, live=9),
            Lane("base", delta=+3, live=3),
        ]))

    def test_singular_wording_when_exactly_one_moves(self):
        line = cross_harness_advice([
            Lane("codex", delta=-1, live=1, candidates=("solo",)),
            Lane("base", delta=+1, live=3, harness="claude"),
        ])
        self.assertIn("convert up to 1 codex worker to claude", line)
        self.assertNotIn("workers", line)


if __name__ == "__main__":
    unittest.main()
