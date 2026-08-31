"""Per-harness UTILIZATION: is the capacity we already granted being USED?

WHY THIS IS NOT A SECOND GOVERNOR (aegis-967a9, under the aegis-rjrwu ruling).

Creel's setpoint controller answers a BUDGET question — "given the spend
trajectory, should the fleet be bigger or smaller?" — and this module does not
recompute one byte of it.  The ruling that forbade porting `creel-setpoint.js`
into Python stands, and `creel_advisory.controller_line` remains the only path
to that answer.

This module answers a different question that Creel structurally cannot:
OCCUPANCY.  How many agents are live against the cap something else already set,
and is the burn under the pace bound Shantytown's OWN governor already computes?

That the two are different is not an argument, it is a measurement.  Tonight
(2026-08-29) the live fleet read:

    base  ok 26/50/668 52/70/255668 CAP[6 agents]
          PACE[seven_day 52%used/58%elapsed =0.90x <=1.15x]
          | governor recommends 0 — hold

Creel said HOLD and Creel was right: the seven-day budget is 5.7 points under a
100%-by-reset trajectory, which rounds to no delta.  At that same moment ZERO
claude leads were live under a cap of SIX, and the five-hour budget was at 0.27x
pace with 668 seconds left — 74 points about to be destroyed unspent.  None of
that appears anywhere on the line, because none of it is a budget fact.  A
budget controller fed `running=0` still says hold when the budget is on track;
unfilled capacity is invisible to it by construction.

So this is not a rival opinion about the budget.  It reports occupancy against a
cap it never sets, gated on a pace ratio it never defines, and it RECOMMENDS
ONLY.  Dearing's ruling stands: the administrator actuates.

THREE RULES THIS MODULE OBEYS, each one paid for elsewhere in this repo:

1.  GROWTH IS THE DANGEROUS DIRECTION.  Every path that cannot prove its inputs
    declines to recommend growth and says why (the aegis-jrax3 armed-and-blind
    lesson, and the `lowerBoundOnly` integrator freeze from the 45vco design).
    "Cannot tell" is a distinct answer from "hold" — see `Utilization.advice`.
2.  STRICTEST WINDOW WINS.  A fleet under pace on seven_day and over pace on
    five_hour is NOT under-utilized; the tight budget binds.  Same direction as
    `Verdict.max_agents`, which takes the strictest cap rather than the newest.
3.  A RATIO IS RENDERED WITH THE NUMBERS IT CAME FROM, always — the constraint
    `Pacing` states in its own docstring.  `0.90x` alone is uncheckable;
    `52%used/58%elapsed =0.90x` reads its own arithmetic off the screen.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import governor as gov_mod


LINEAR = 1.0
"""The pace reference when a window declares no `[[governor.pace]]` bound.

NOT a policy default and never treated as one.  Spending below linear means the
window will reach its reset with budget unspent, which is arithmetic rather than
an opinion.  It is rendered as `<1.00x linear` precisely so an operator can see
that no bound was declared, instead of a fabricated threshold that reads exactly
like a configured one.
"""

# Rendered in this order so a reader compares the same budget across harnesses.
_ORDER = (gov_mod.FIVE_HOUR, gov_mod.SEVEN_DAY)


@dataclass(frozen=True)
class WindowUse:
    """One window's burn, asked as an occupancy question rather than a tier one."""

    window: str
    pct: float
    elapsed_pct: float | None
    ratio: float | None
    bound: float
    bound_declared: bool
    why_no_ratio: str
    points: float
    ceiling: float
    resets_in: float | None

    @property
    def under(self) -> bool | None:
        """Under its bound, over it, or unknowable. Never collapse the third."""
        return None if self.ratio is None else self.ratio < self.bound

    def render(self) -> str:
        bound = (f"<{self.bound:.2f}x" if self.bound_declared
                 else f"<{self.bound:.2f}x linear")
        if self.ratio is None:
            # No number at all, and the reason in its place. A window that cannot
            # be rated must not render as one that was rated and found fine.
            return f"{self.window} unrated ({self.why_no_ratio})"
        return (f"{self.window} {self.pct:.0f}%used/{self.elapsed_pct:.0f}%elapsed "
                f"={self.ratio:.2f}x {bound} "
                f"{self.points:.0f}pts/{gov_mod.fmt_eta(self.resets_in)}")


@dataclass(frozen=True)
class Utilization:
    """One harness's occupancy, and what it recommends about it.

    `advice` is deliberately three-valued and `reason` is never optional:

        +N    fill toward cap — every precondition proven
         0    hold — a proven reason NOT to grow
        None  cannot tell — an input could not be established

    Collapsing None into 0 would make an unreadable tracker and a genuinely full
    fleet render identically, which is the failure this repo names on every
    surface it has.
    """

    harness: str
    live: int
    cap: int | None
    windows: tuple[WindowUse, ...]
    advice: int | None
    reason: str
    ready: int | None
    cause: str = "fill"
    """A STABLE label for WHY, used as the dedup key instead of the prose.

    The reason sentence carries live numbers — a ratio, a points figure, an ETA —
    and every one of them drifts on every pass. Keying a ledger on it would
    re-page the administrator every five minutes about a recommendation that had
    not changed, which is the exact defect this key exists to prevent and the one
    sattler measured on the live surface within an hour of it shipping.
    """
    needs_ready: bool = False
    """True when a ready-work count is the ONLY missing input.

    This is what lets the caller pay for the tracker query lazily without
    reimplementing the precondition ladder to decide whether to pay.  `assess`
    stays a pure total function; the caller runs it once with `ready=None`, and
    only probes and re-runs when this says the answer would change.  A status bar
    polling this command every few seconds must not spawn a tracker read on every
    poll merely to be told the fleet is already at cap.
    """

    def render(self) -> str:
        cap = "uncapped" if self.cap is None else str(self.cap)
        parts = [f"live {self.live}/{cap}"]
        parts.extend(w.render() for w in self.windows)
        if self.advice is None:
            parts.append(f"? cannot tell: {self.reason}")
        elif self.advice > 0:
            parts.append(f"↑ fill toward cap: +{self.advice} — {self.reason}")
        else:
            parts.append(f"hold — {self.reason}")
        return "UTIL[" + " · ".join(parts) + "]"

    def key(self) -> str:
        """The dedup key: the RECOMMENDATION AND ITS CAUSE, never the line.

        Numbers on this line move every pass — elapsed climbs, points fall, an
        ETA counts down — so keying on the rendered text re-pages the
        administrator every five minutes about a recommendation that has not
        changed. That is gennaro's 1641346 defect, and my first cut reintroduced
        a narrower version of it by folding `live/cap` and the reason prose into
        the key: sattler measured base +3 pushed TWICE in ~20 minutes with an
        unchanged recommendation, within an hour of this shipping.

        `cause` exists so this key is a pair of stable labels. Two holds for
        DIFFERENT reasons (at cap vs over pace) are genuinely different states
        and still key apart; the same hold with drifting numbers does not.
        """
        return f"{self.cause}:{self.advice}"


def _ceiling(policy, window: str) -> float:
    """The percentage this window's own drain stops the fleet at, else 100.

    Read off the tiers rather than configured separately, for the reason
    `Policy.burn_ceiling` gives: a bound derived from the drain cannot drift away
    from the drain it is derived from.
    """
    try:
        drains = [t.at for t in policy.tiers_for(window) if t.drains]
    except Exception:
        return 100.0
    return float(min(drains)) if drains else 100.0


def _window_use(policy, window: str, reading, now: float) -> WindowUse | None:
    if reading is None or not reading.ok or reading.pct is None:
        return None
    pace = policy.pace_for(window) if hasattr(policy, "pace_for") else None
    bound = pace.ratio if pace is not None else LINEAR
    length = (pace.window_length() if pace is not None
              else gov_mod.WINDOW_LENGTH_S.get(window))
    # THE ONE ARITHMETIC CALL, and it is the governor's own. `pace_ratio` already
    # refuses every undefined case with a reason attached — a missing reset, a
    # past reset, a window shorter than its own remaining time. Re-deriving it
    # here would be the second opinion this module exists not to have.
    ratio, why = gov_mod.pace_ratio(reading.pct, reading.reset_at, now, length)
    left = None if reading.reset_at is None else reading.reset_at - now
    # Clamped at 0 for the same reason `pace_ratio` pins it there: inside the
    # reset-boundary skew allowance `left` can sit a hair past `length`, and a
    # window is never less than 0% elapsed (aegis-lvfm5). This is display
    # arithmetic on an already-decided ratio, not a second opinion about it.
    elapsed = (None if (ratio is None or not length)
               else max(0.0, 100.0 * (1.0 - (left / length))))
    ceiling = _ceiling(policy, window)
    return WindowUse(
        window=window, pct=float(reading.pct), elapsed_pct=elapsed,
        ratio=ratio, bound=float(bound), bound_declared=pace is not None,
        why_no_ratio=why, points=max(0.0, ceiling - float(reading.pct)),
        ceiling=ceiling, resets_in=left)


def assess(harness: str, *, readings, policy, cap: int | None, live: int,
           now: float, ready: int | None,
           creel_delta: int | None = None) -> Utilization:
    """Occupancy for one harness. A total function of its inputs — no clock, no
    storage, no I/O — so the replay discipline the 45vco design required of the
    controller applies here too.

    `ready` is the count of ready work, or None for COULD NOT LOOK. The two are
    kept apart all the way through: no ready work is a reason to hold, and an
    unreadable tracker is a reason to say nothing at all.
    """
    windows = tuple(
        w for w in (_window_use(policy, name, readings.get(name), now)
                    for name in _ORDER)
        if w is not None)

    def out(advice, reason, cause, needs_ready=False):
        return Utilization(harness=harness, live=live, cap=cap, windows=windows,
                           advice=advice, reason=reason, ready=ready,
                           cause=cause, needs_ready=needs_ready)

    if cap is None:
        return out(0, "no fleet cap declared — nothing to fill toward", "uncapped")
    if live >= cap:
        return out(0, f"at cap ({live}/{cap})", "at-cap")

    rated = [w for w in windows if w.ratio is not None]
    if not rated:
        why = "; ".join(f"{w.window}: {w.why_no_ratio}" for w in windows) or \
            "no readable window"
        return out(None, f"no window can be rated for pace ({why})", "unratable")

    # RULE 2 — the strictest window binds. Checked before the under-pace case so
    # a tight five-hour budget can never be outvoted by a comfortable weekly one.
    over = [w for w in rated if not w.under]
    if over:
        w = max(over, key=lambda x: x.ratio / x.bound)
        return out(0, f"{w.window} is at {w.ratio:.2f}x against its "
                      f"{w.bound:.2f}x bound — not under-utilized", "over-pace")

    # WHICH WINDOW JUSTIFIES THE RECOMMENDATION — the one with the most time
    # left, NOT the most under-spent. Measured on the live fleet 2026-08-29: base
    # was 0.28x on five_hour with 67 points and TWO MINUTES to reset, and 0.90x on
    # seven_day with 43 points and 2d22h. The deepest under-spend was the useless
    # one — agents started now cannot spend a budget that refills in 120 seconds,
    # so citing it would have justified a correct recommendation with a reason
    # that does not survive being checked. Recommending growth is still right
    # here; seven_day is why. A window with no reset timestamp sorts last: it
    # cannot be shown to have runway, and this is the growth path.
    lead = max(rated, key=lambda x: (x.resets_in is not None, x.resets_in or 0.0))
    slack = cap - live

    # RULE 1 — growth is the dangerous direction. An unreadable tracker cannot
    # authorise adding agents, and must not be reported as "hold" either.
    if ready is None:
        return out(None, f"{lead.window} is under pace at {lead.ratio:.2f}x, but "
                         "ready work could not be read — declining to recommend "
                         "growth on an unproven signal", "ready-unknown",
                         needs_ready=True)
    if ready <= 0:
        return out(0, f"{lead.window} is under pace at {lead.ratio:.2f}x but no "
                      "ready work — filling the cap would add idle agents",
                   "no-ready")
    if creel_delta is not None and creel_delta < 0:
        # The budget controller outranks occupancy when they disagree, the same
        # direction the 45vco design gives every clamp: this may only ever
        # decline to recommend growth, never authorise it.
        return out(0, f"{lead.window} is under pace at {lead.ratio:.2f}x with "
                      f"{ready} ready, but the budget controller recommends "
                      f"{creel_delta:+d} — deferring to it", "budget-shrinking")
    return out(slack, f"{lead.window} at {lead.ratio:.2f}x is under its "
                      f"{lead.bound:.2f}x bound, {ready} ready, "
                      f"{lead.points:.0f} points expire in "
                      f"{gov_mod.fmt_eta(lead.resets_in)}", "fill")
