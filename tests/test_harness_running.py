"""Tests for the process-vs-card harness split (aegis-93fajy).

`harness.name_for` reads the CARD and answers what an agent WILL launch as.
`harness.running_name` reads a live launch line and answers what one IS. They
agree for a settled fleet and differ for exactly as long as a conversion is
un-relaunched — a window `st harness` (aegis-6glmer) turned from a by-hand rarity
into routine.

The bug this pins: governor lane ACCOUNTING resolved through the card, so during
that window an agent was counted against the lane it was going to while still
spending the budget of the lane it was on.
"""

import unittest

from shantytown import harness as harness_mod
from shantytown.cli import _live_by_governor


CLAUDE_LINE = "claude --settings /s/worker.settings.json --model opus"
CODEX_LINE = "CODEX_HOME=/s/codex codex exec --full-auto"


class RunningName(unittest.TestCase):

    def test_it_identifies_each_harness_from_its_OWN_syntax(self):
        # "carries settings" is a claim about a program's syntax — a flag for
        # one, an environment export for the other.
        self.assertEqual(harness_mod.running_name(CLAUDE_LINE), "claude")
        self.assertEqual(harness_mod.running_name(CODEX_LINE), "codex")

    def test_an_unreadable_line_is_NONE_not_the_default(self):
        # The load-bearing one. Defaulting to "claude" here would quietly
        # reintroduce the card's answer under a new name, and the whole point of
        # this function is to be a SECOND opinion.
        for line in ("-bash", "", None, "vim notes.md"):
            self.assertIsNone(harness_mod.running_name(line), repr(line))

    def test_it_disagrees_with_the_card_during_a_conversion(self):
        # The exact window: the card was converted, the process has not restarted.
        card = _card("grant", harness="claude")
        self.assertEqual(harness_mod.name_for(card), "claude")
        self.assertEqual(harness_mod.running_name(CODEX_LINE), "codex")


def _card(name, *, harness=None, pane=None):
    from shantytown.protocols import Agent
    return Agent(name=name, role="worker", pane=pane or f"shanty-{name}",
                 harness=harness)


class _Panes:
    """Panes stub: every pane exists, cmdlines come from a dict."""

    def __init__(self, cmdlines=None, raise_on=None):
        self._c = cmdlines or {}
        self._raise = raise_on

    def exists(self, pane):
        return True

    def cmdline(self, pane):
        if self._raise and pane == self._raise:
            raise RuntimeError("tmux said no")
        return self._c.get(pane)


class _Gov:
    pass


class LaneAccounting(unittest.TestCase):
    """`_live_by_governor` must bucket by what is SPENDING, not what is declared."""

    def setUp(self):
        import tempfile
        from shantytown.config import load_or_default
        self._root = tempfile.mkdtemp(prefix="hrun-")
        self.cfg, _ = load_or_default(self._root)
        self.governors = {"base": _Gov(), "codex": _Gov()}

    def _count(self, cards, cmdlines, raise_on=None):
        return _live_by_governor(cards, _Panes(cmdlines, raise_on),
                                 self.cfg, self.governors, self._root)

    def test_a_settled_fleet_counts_where_it_always_did(self):
        cards = [_card("a", harness="claude"), _card("b", harness="codex")]
        live = self._count(cards, {"shanty-a": CLAUDE_LINE, "shanty-b": CODEX_LINE})
        self.assertEqual(live["base"], 1)
        self.assertEqual(live["codex"], 1)

    def test_a_CONVERTED_but_unrelaunched_agent_counts_where_it_is_SPENDING(self):
        # THE BUG. Card says claude (converted); the process is still codex. The
        # codex budget is what is being consumed, so codex is where it counts.
        cards = [_card("a", harness="claude")]
        live = self._count(cards, {"shanty-a": CODEX_LINE})
        self.assertEqual(live["codex"], 1, "the spend is codex's")
        self.assertEqual(live["base"], 0, "the card's lane must not be charged")

    def test_it_counts_the_new_lane_ONCE_THE_PROCESS_IS_THE_NEW_ONE(self):
        cards = [_card("a", harness="claude")]
        live = self._count(cards, {"shanty-a": CLAUDE_LINE})
        self.assertEqual(live["base"], 1)
        self.assertEqual(live["codex"], 0)

    def test_an_unreadable_cmdline_KEEPS_the_cards_answer(self):
        # Degrade to the previous behaviour, never to dropping the agent out of
        # both lanes — that would under-count every lane whenever tmux is slow,
        # and growth on an under-count is the direction the fail-safe forbids.
        cards = [_card("a", harness="codex")]
        live = self._count(cards, {"shanty-a": "-bash"})
        self.assertEqual(live["codex"], 1)
        self.assertEqual(sum(live.values()), 1, "counted exactly once")

    def test_a_RAISING_cmdline_reader_also_keeps_the_cards_answer(self):
        # A reader that throws must not be able to change fleet accounting.
        cards = [_card("a", harness="codex")]
        live = self._count(cards, {}, raise_on="shanty-a")
        self.assertEqual(live["codex"], 1)

    def test_panes_without_a_cmdline_reader_at_all_still_work(self):
        # Older/stub pane objects predate this seam; the count must not break.
        class Bare:
            def exists(self, pane):
                return True
        live = _live_by_governor([_card("a", harness="codex")], Bare(),
                                 self.cfg, self.governors, self._root)
        self.assertEqual(live["codex"], 1)

    def test_every_agent_is_counted_exactly_once_in_every_arm(self):
        # The invariant that makes the lane numbers a partition rather than two
        # opinions: `sum(live.values())` is the live roster, whatever we could or
        # could not read.
        cards = [_card("a", harness="claude"), _card("b", harness="codex"),
                 _card("c", harness="codex")]
        for cmdlines in ({"shanty-a": CODEX_LINE},            # one disagreement
                         {},                                  # nothing readable
                         {"shanty-a": CLAUDE_LINE, "shanty-b": CODEX_LINE,
                          "shanty-c": CLAUDE_LINE}):          # one converted back
            live = self._count(cards, cmdlines)
            self.assertEqual(sum(live.values()), 3, cmdlines)


if __name__ == "__main__":
    unittest.main()
