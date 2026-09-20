"""WHICH LANE should the next unit of work go to.

The per-lane governor answers "is THIS lane ahead of its own trajectory". That is a
different question from "of the lanes with room, which one should we feed", and nothing
answered the second one. Stiwi, 2026-09-16 ~18:20Z, verbatim:

    "We've used a lot of our claude limit, which is fine but letd ensure we stay
     balanced, and maybe add a balance governor in st"

WHAT WAS MEASURED AT DIRECTIVE TIME, and why it is the shape of this module. base
(claude) read seven_day 31% used at 11% elapsed — 2.74x pace. codex read 59% at 9%
elapsed, WHICH WAS A LIE: zero codex agents had been live for hours, so the codex probe
had not refreshed since Stiwi reset that limit. The per-lane governor held codex DOWN on
a stale number while claude, the genuinely exhausted lane, ran everything. So the
balance view is not merely a nicety on top of the per-lane view; without freshness it
would have recommended exactly the wrong lane with more confidence than before.

Hence the ordering of the rules in `balance()`: STALENESS IS CHECKED BEFORE PACE, and a
stale lane produces REFRESH, never a preference and never a restriction. This module can
only ever say "prefer" or "cannot say". It does not hold, cap or drain anything — the
per-lane governor remains the only brake, per Stiwi's standing rule — so the worst a bug
here can do is route work to the wrong lane that the per-lane governor still gates.

This is deliberately PURE: it takes paces and returns a verdict. Reading usage, deciding
staleness and mapping harness->lane all live in `governor`/`fleet_governor`, which
already do them. A function that both gathers and decides cannot be given the three
fixtures this feature was specified with.
"""

from __future__ import annotations

from dataclasses import dataclass

# The verdicts. `REFRESH` and `UNRATED` are both "cannot say", kept APART because they
# want opposite follow-ups: REFRESH is actionable right now (run a probe of that lane),
# UNRATED is a fact about the window that no amount of probing changes.
PREFER, BALANCED, REFRESH, UNRATED = "prefer", "balanced", "refresh", "unrated"

# Crossing this many times the other lane's pace is what makes a lane "ahead".
# 1.5x is the bead's suggested starting band. It is a CONFIG value, not a constant, and
# `balance()` takes it as an argument for that reason.
DEFAULT_BAND = 1.5


@dataclass(frozen=True)
class LanePace:
    """One lane's seven_day pace, or the reason there isn't one.

    `ratio` is `consumed_fraction / elapsed_fraction`, exactly what
    `governor.pace_ratio` returns — 1.0 is "spending exactly on trajectory".

    `stale_why` is kept SEPARATE from `unrated_why` although both mean "no usable
    number". A stale lane HAS a ratio, and that is precisely the danger: it is a real
    number, computed correctly, about a moment that has passed. The field exists so the
    verdict can distinguish "I could not compute this" from "I computed it and you must
    not use it", which is the distinction the directive was issued over.
    """

    lane: str
    ratio: float | None = None
    unrated_why: str = ""
    stale_why: str = ""

    @property
    def usable(self) -> bool:
        return self.ratio is not None and not self.stale_why


@dataclass(frozen=True)
class Balance:
    verdict: str
    prefer: str | None = None
    ratio: float | None = None
    why: str = ""

    @property
    def actionable(self) -> bool:
        """True only when this may steer a launch.

        REFRESH and UNRATED are false. Callers gate on THIS rather than on
        `prefer is not None`, so that a future verdict which carries a lane for
        reporting cannot accidentally become a routing instruction.
        """
        return self.verdict == PREFER

    def line(self) -> str:
        """The one-line form for `st crew --governor` and the idle-fleet alert."""
        if self.verdict == PREFER:
            return f"balance {self.ratio:.2f}x — prefer {self.prefer} ({self.why})"
        if self.verdict == BALANCED:
            return f"balance {self.ratio:.2f}x — balanced ({self.why})"
        return f"balance ?/? — {self.verdict.upper()}: {self.why}"


def balance(paces, band: float = DEFAULT_BAND) -> Balance:
    """Recommend a lane, or say why not. Never holds anything.

    Rule ORDER is the whole safety argument, so it is spelled out rather than left to
    the reader of the ifs:

      1. fewer than two lanes      — there is nothing to balance
      2. ANY lane stale            — REFRESH. Checked BEFORE pace, because a stale
                                     reading produces a perfectly plausible ratio and
                                     that is how the directive's incident happened.
      3. any remaining lane unrated— UNRATED, carrying that lane's own reason
      4. otherwise                 — compare
    """
    lanes = sorted(paces, key=lambda p: p.lane)
    if len(lanes) < 2:
        only = lanes[0].lane if lanes else "none"
        return Balance(UNRATED, why=f"only one lane to compare ({only}); "
                                    "balance needs two")

    stale = [p for p in lanes if p.stale_why]
    if stale:
        return Balance(REFRESH, why="; ".join(
            f"{p.lane}: {p.stale_why}" for p in stale))

    unrated = [p for p in lanes if p.ratio is None]
    if unrated:
        return Balance(UNRATED, why="; ".join(
            f"{p.lane}: {p.unrated_why or 'no pace'}" for p in unrated))

    if len(lanes) > 2:
        # Deliberately refuse rather than guess. A 3-lane fleet wants a policy about
        # WHICH pair (or a different statistic entirely), and silently comparing the
        # two alphabetically-first lanes would be a wrong answer wearing a right one's
        # clothes. Nothing on this host has three lanes today; when something does,
        # that is the moment to decide, not now.
        return Balance(UNRATED, why=(f"{len(lanes)} lanes configured "
                                     f"({', '.join(p.lane for p in lanes)}); "
                                     "balance is defined for exactly two"))

    a, b = lanes
    if a.ratio == 0 and b.ratio == 0:
        return Balance(BALANCED, ratio=1.0, prefer=None,
                       why="both lanes at 0.00x — nothing spent in either window")

    # A zero-pace lane is wide open, and dividing by it is not a special case to be
    # defended against but the ANSWER: prefer it, unambiguously.
    if b.ratio == 0:
        return Balance(PREFER, prefer=b.lane, ratio=float("inf"),
                       why=f"{b.lane} at 0.00x, {a.lane} at {a.ratio:.2f}x")
    if a.ratio == 0:
        return Balance(PREFER, prefer=a.lane, ratio=float("inf"),
                       why=f"{a.lane} at 0.00x, {b.lane} at {b.ratio:.2f}x")

    ratio = a.ratio / b.ratio
    detail = f"{a.lane} {a.ratio:.2f}x vs {b.lane} {b.ratio:.2f}x, band {band:g}x"
    if ratio >= band:
        return Balance(PREFER, prefer=b.lane, ratio=ratio, why=detail)
    if ratio <= 1 / band:
        return Balance(PREFER, prefer=a.lane, ratio=ratio, why=detail)
    return Balance(BALANCED, ratio=ratio, why=detail)


def prefer_first(names, lane_of, verdict):
    """Order feed candidates so the preferred lane's agents come first.

    A REORDER, NEVER A FILTER, and that is the whole safety argument for wiring this
    into tend. The bead asks for "when both lanes have room, feed the lane the balance
    view prefers; when only one has room, that one" — and a stable reorder gives both
    without this module learning anything about room. Admission stays exactly where it
    already is (`FleetGovernor.admits_launch`), which is the only thing that may refuse,
    so the worst a wrong preference can do is try a lane in a different order. Filtering
    here would create a second gate that can starve a lane, and a lane starved by a
    RANKING is far harder to diagnose than one refused by a gate that prints its reason.

    Not actionable (BALANCED, REFRESH, UNRATED) returns the input order untouched — so
    a stale probe cannot reorder anything either.

    `sorted` is stable, so agents within the same lane keep whatever order the caller
    established (idle-longest-first, alphabetical, whatever it was).
    """
    if not verdict.actionable:
        return list(names)
    return sorted(names, key=lambda n: 0 if lane_of(n) == verdict.prefer else 1)
