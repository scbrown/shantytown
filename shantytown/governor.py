"""governor — spin the crew down as CLAUDE USAGE climbs (aegis-hdqej).

Stiwi, 2026-08-01: *"i want st to be claude code usage-aware. i want to be able to
spin down the crew at certain configured intervals. like 50% spin down and only
work p1+ issues. 70% p0 issues, 80% only support crew and then 95% stop and have
all crew ensure work is pushed"*

    usage >= 50%   dispatch only P1 and above
    usage >= 70%   dispatch only P0
    usage >= 80%   only SUPPORT CREW runs at all
    usage >= 95%   FULL STOP — every agent ensures its work is PUSHED, then stops

THE THRESHOLDS AND THE TIER ACTIONS ARE DEPLOYMENT POLICY, NOT ST OPINION. Every
number above is `[[governor.tier]]` in shantytown.toml and nothing here hardcodes
one — "certain configured intervals" is the deliverable, so a deployment that
wants 40/60/85 edits a config file and does not touch this package. What st owns
is the MECHANISM: read a number, decide a tier, and make three existing surfaces
honour it.

NO NEW MACHINERY, AND NO NEW DAEMON. The governor is a policy INPUT to decisions
st already makes:

  st go      already triage-gated, and already REFUSES rather than typing into a
             pane it should not. A priority floor is one more refusal, and it
             names the tier and the reading that caused it.
  st tend    already runs every ~5 min and already decides who comes up
             (`--target N` fills the tier from the root down). The governor
             withholds a respawn the same way the target cap does.
  st stop    already RECORDS INTENT (stopped.py), so a governor-drained agent is
             distinguishable from a crashed one and tend will not fight the
             governor by respawning what the governor just asked to stand down.

IT NEVER KILLS. tend's whole design argument is that a supervisor which could
also kill is a scheduler with a supervisor's permissions. So "spin down" is
DRAIN — a protocol that ASKS, and whose last step the agent performs itself:

  1. broadcast drain to every live excluded agent, DURABLY, so a dying session
     still got it;
  2. each agent commits WIP on its own branch/worktree, pushes, and reports what
     it pushed;
  3. it stops itself cleanly, so the stop event carries the intent;
  4. tend does not respawn it while the tier holds;
  5. an agent that CANNOT push escalates rather than dying silently.

THE 95% TIER IS THE ONE THAT MUST NOT BE SLOPPY, and the reason is measured:
~/gt/shantytown sat with 10 commits that existed on ONE BOX ONLY for three days
(pushed 2026-08-01, 94f1146..51a9f18). A fleet stopped at 95% with unpushed work
has thrown away the tokens that produced it. So the drain REPORTS by agent —
who drained, what SHAs went up, who failed — and UNREPORTED IS NOT DRAINED. The
report reads the agents' own stop records; it never infers a push from the fact
that a message was sent.

FAIL-SAFE, STATED ON PURPOSE. A governor reading a FROZEN number is worse than no
governor: it holds the fleet wide open at a stale 12% forever, and reads green
the entire time. So a stale probe is SIGNAL LOST, never "the last value is still
current" — and the two ways to be wrong are not symmetric:

    on_signal_lost = "warn"    (default) the fleet runs UNGOVERNED and this
                               ALARMS on every pass. An idled fleet caused by a
                               broken probe is its own outage; the loud version
                               of "I cannot see the tank" is the honest one.
    on_signal_lost = "freeze"  no NEW dispatch, for a deployment that would
                               rather stall than overspend. Still no drain.

AND IT NEVER FAILS INTO DRAIN. No probe bug, no unreachable Prometheus, no
unreadable role catalog may stop the whole crew. Every could-not-tell inside this
module resolves toward KEEP RUNNING, loudly. Draining is only ever the
consequence of a reading we actually got.

THE SIGNAL IS LIVE, and the names below were taken from the running Prometheus
rather than from the bead that specified them — which is the only reason they are
right. The spec said `claude_usage_pct{window="5h"}`; what shipped is
`claude_usage_utilization_pct{window="five_hour"}` behind the recording rule
`claude:usage_utilization_pct:max`, sourced from an authenticated
`GET /api/oauth/usage` (server truth, not an estimate from local transcripts).
Coding against the spec would have produced a governor that queries nothing, and
an expression with no data never fires and never reads as broken — the exact
failure the producer's own bead was filed about.

The reader is still an injected seam with a `stub` source, and that stays: it is
what let this be built and tested against 45/55/75/85/97 before the producer
landed, and it is what lets a deployment rehearse a tier without waiting to
actually be at 95%.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# The series this governor consumes, named once, and VERIFIED AGAINST THE LIVE
# PROMETHEUS rather than against the bead that specified them (they differed: the
# spec said `claude_usage_pct{window="5h"}` and what shipped is
# `claude_usage_utilization_pct{window="five_hour"}`, reusing a name this fleet had
# already chosen). A governor that queries a series nobody produces is the exact
# failure the producer's own bead was filed about — an expression with no data
# never fires and never reads as broken.
#
# READ THE RECORDING RULE, NEVER THE BARE PER-ACCOUNT METRIC. The producer is
# multi-account by design; `claude:usage_utilization_pct:max` is `max by (window)`
# across accounts, so adding an account cannot silently change what the governor
# sees. Reading the per-account series directly would govern by whichever account
# happened to be last in the response.
USAGE_METRIC = "claude:usage_utilization_pct:max"
# The per-account source. Named because the TEXTFILE reader has no choice: a
# recording rule is computed by Prometheus and is not in the file, so that reader
# takes the max across accounts ITSELF — the same aggregation, so both readers
# answer the same question rather than one of them quietly governing by one
# account.
ACCOUNT_USAGE_METRIC = "claude_usage_utilization_pct"
PROBE_OK_METRIC = "claude_usage_probe_success"
PROBE_TS_METRIC = "claude_usage_probe_timestamp_seconds"
# How old the PUBLISHED percentage is. Distinct from the probe timestamp and worth
# reading separately: when the probe fails the producer RETAINS the last good
# percentages and flags them, so the number stays fresh-looking while this climbs.
CACHE_AGE_METRIC = "claude_usage_cache_age_seconds"
# WHEN THE BUDGET REFILLS — the ON ramp's whole input (aegis-9mehy). The producer
# has published this since aegis-fnd8g and the governor never read it, so the
# throttle had a well-built OFF ramp and no ON ramp: a refreshed budget re-engaged
# the crew only if a human happened to notice.
#
# IT IS NEVER A REASON TO ACT ON ITS OWN. A reset timestamp says when the budget
# is SCHEDULED to refill, which is a prediction; the tier is left because the
# READING dropped, never because the clock passed. Everything downstream of this
# metric only ever decides WHEN TO LOOK.
RESET_TS_METRIC = "claude_usage_reset_timestamp_seconds"

# The producer's window labels. `five_hour`/`seven_day`, not `5h`/`7d`.
FIVE_HOUR, SEVEN_DAY = "five_hour", "seven_day"

SOURCES = ("prometheus", "textfile", "stub")
WARN, FREEZE = "warn", "freeze"
ON_SIGNAL_LOST = (WARN, FREEZE)

DRAIN = "drain"
ACTIONS = (DRAIN,)

# How old a probe reading may be before it is SIGNAL LOST rather than a number.
# 15 minutes is three tend passes: long enough that one missed scrape does not
# blind the governor, short enough that a dead probe cannot hold a spending fleet
# open for an hour. Overridable — it is a property of the producer's cadence, and
# the producer is a different bead.
DEFAULT_MAX_AGE_S = 900

# Leaving a tier requires falling this far BELOW its threshold. Without it a
# reading oscillating around 70 flips the whole fleet between "P0 only" and "P1+"
# every pass, and each flip is a dispatch decision an agent already acted on.
#
# IT APPLIES ON THE WAY UP TOO, and that direction is the one aegis-9mehy is
# about: a five-hour window that refreshes to 48% must not re-engage the whole
# fleet into an immediate re-throttle at 50%. Flapping the crew is worse than
# staying down a few minutes longer.
DEFAULT_RELAX_MARGIN = 5

# How long AFTER a published reset the wake fires. The producer runs on a ~5 min
# cron and the clocks are not the same clock, so waking exactly AT the timestamp
# reads a percentage the reset has not reached yet — which looks like "the reset
# did not land" and holds the tier for a further five minutes. A minute of skew
# buys the first read after the wake being genuinely post-reset.
WAKE_SKEW_S = 60

# A reset timestamp further ahead than this is not scheduled, it is WRONG. The
# longest budget here is seven days; anything past eight is a parse error or a
# clock problem, and arming a timer for it would silently replace a five-minute
# fallback with a wake in 2087. Refused, loudly, and the fallback stands.
MAX_WAKE_AHEAD_S = 8 * 86400


class GovernorError(ValueError):
    """The [governor] table exists and is wrong. config.ConfigError wraps it —
    the operator must see the file and the key, never a bare value error."""


# --- the policy (all of it from shantytown.toml) ------------------------------

@dataclass(frozen=True)
class Tier:
    """One configured interval. Every field is a DIFFERENT kind of restriction and
    a tier may carry more than one — they compose, they do not exclude.

        at            engage while usage >= this percent
        window        WHICH budget `at` is read against (aegis-59hao)
        min_priority  a DISPATCH floor (see admits below — read the sign)
        traits        only agents whose role set carries one of these RUN at all
        action        "drain" — the full stop, with the push protocol

    THERE ARE TWO BUDGETS AND THEY EXHAUST INDEPENDENTLY (aegis-59hao). The
    producer publishes both windows and the governor used to read exactly one, so
    it was blind to whichever budget was actually further gone — and blind in the
    expensive direction, because a five-hour window refills in HOURS while the
    weekly does not refill for DAYS. Governing only the cheap-to-recover budget is
    backwards. `window` lives on the TIER rather than in a second config table so
    that a seven-day rule is just another tier row: nothing new to learn, and a
    config that never mentions a window keeps its exact current meaning.
    """
    at: int
    min_priority: int | None = None
    traits: tuple[str, ...] = ()
    action: str | None = None
    window: str = FIVE_HOUR

    @property
    def drains(self) -> bool:
        return self.action == DRAIN

    def label(self) -> str:
        """What this tier does, in one clause, for a refusal message. A refusal
        that names only a number teaches nothing — and with two budgets, one that
        names only a NUMBER is worse than useless: "usage 72%" sends an operator
        to look at the wrong budget. Every label names its window.
        """
        bits = []
        if self.min_priority is not None:
            bits.append(f"dispatch only P{self.min_priority} and above")
        if self.traits:
            bits.append(f"only {'/'.join(self.traits)} crew runs")
        if self.drains:
            bits.append("FULL STOP — every agent pushes its work, then stops")
        what = "; ".join(bits) or "no restriction declared"
        return f"{what} [{self.window} >= {self.at}%]"


@dataclass(frozen=True)
class Burndown:
    """SPEND a budget that is about to be DESTROYED (aegis-yegfx).

    Stiwi, 2026-08-04: *"we're about to have our 7 day limit reset. I want to
    build in governor support for fully utilizing the limit before it resets."*

    THE TIERS ARE TIME-BLIND, AND THAT IS THE BUG THIS FIXES. A tier compares a
    consumption fraction to a constant with no reference to how much of the
    window is LEFT. "70% consumed" is a warning at hour 20 of a week and is
    meaningless at hour 165 — the budget refills either way, and every point not
    spent before it does is destroyed, not saved. So the ladder is most
    aggressive exactly when it should be most relaxed, and it gets worse every
    week deterministically.

    Measured 2026-08-04 15:28, which is why this exists: seven_day 86%, resetting
    in 3h32m, floor P0-only, ZERO ready P0s of 213 ready beads, entire fleet
    down. Fourteen points of a weekly budget were being *conserved* by spending
    none of them, hours before they evaporated.

        window   which budget this applies to
        within   arm when that budget refills within this many seconds
        reserve  percentage points to hold back BELOW the ceiling (see below)

    WHAT IT SUSPENDS, AND WHAT IT CANNOT TOUCH. Burndown drops that window's
    PRIORITY-FLOOR and TRAIT tiers. It never drops a `drain`, and there is no
    config key that would let it: the drain is the bound on the whole mechanism.
    At the 2026-08-04 reading — 86% with a 90% drain — the most this can spend
    is four points, and that number is readable off the config rather than
    argued from the code.

    ITS FAIL-SAFE RUNS BACKWARDS FROM EVERY OTHER ONE IN THIS MODULE, and that
    asymmetry is the thing to keep in mind when editing it. Everywhere else a
    could-not-tell resolves toward KEEP RUNNING, because the failure to avoid is
    a probe bug stopping the fleet. This is the one mechanism that RELAXES, so
    here every could-not-tell must resolve toward KEEP THE TIER:

        signal lost for the window   -> no burndown (a stale percentage must
                                        never be able to open the taps)
        no reset timestamp published -> no burndown (we cannot know it is
                                        about to be destroyed, so assume not)
        reset already past           -> no burndown (the producer has not
                                        re-read; that is not a fresh budget)
        pct at or above the ceiling  -> no burndown

    A WRONG RESET TIMESTAMP IS THE FAILURE MODE, named on purpose. If the
    producer publishes a reset that is EARLIER than the truth, burndown opens
    the taps against a budget that really does still have to last — which is
    precisely what the governor exists to prevent. That is the whole reason the
    drain is untouchable and `reserve` exists: the damage is bounded to
    (ceiling - pct) points no matter how wrong the clock is, and an operator who
    does not trust the producer's clock raises `reserve` rather than turning the
    feature off.
    """
    window: str
    within: int
    reserve: int = 0


@dataclass(frozen=True)
class Policy:
    """The whole [governor] table, resolved.

    THERE IS NO `enabled` KEY, deliberately. Declaring a tier IS the enabling act:
    a separate boolean creates exactly one new failure mode — an operator writes
    four tiers, sees nothing happen, and has to discover that a fifth line was
    required. `tiers == ()` is off, and it is the default, so a deployment that
    has never heard of this feature is unaffected.
    """
    source: str = "stub"
    window: str = FIVE_HOUR
    on_signal_lost: str = WARN
    relax_margin: int = DEFAULT_RELAX_MARGIN
    max_age_seconds: int = DEFAULT_MAX_AGE_S
    # source-specific coordinates. url for prometheus, path for a node_exporter
    # textfile, stub_pct for tests and for a dry deployment before fnd8g lands.
    url: str | None = None
    path: str | None = None
    # Basic auth for a Prometheus that requires it. The PASSWORD IS A FILE PATH,
    # never a value: shantytown.toml is tracked, diffable and hand-edited, so an
    # inline secret is a secret in git — and this is the one table an operator is
    # most likely to paste into a bead when asking why the governor is blind.
    username: str | None = None
    password_file: str | None = None
    stub_pct: float | None = None
    tiers: tuple[Tier, ...] = ()
    # Agents whose dispatches the PRIORITY FLOOR does not apply to (aegis-yegfx).
    #
    # A floor is a fleet-wide instrument and the fleet is not uniform. Measured
    # 2026-08-02: seven_day at 70% engaged a P0-only floor with ZERO P0 beads
    # board-wide, so every agent was idle for ~54h with 19 P1s ready. The floor
    # was working exactly as specified and the outcome was a total stall — the
    # budget was being conserved by spending none of it.
    #
    # WHAT THIS IS NOT: a way to make room for work. The two escape hatches an
    # operator reaches for under a floor are both worse than this one. Raising a
    # bead's priority (which `admits` itself suggests) edits the board to satisfy
    # the instrument, and the inflation is permanent and untraceable. Lowering the
    # tier disables the guard for everyone. Naming the agent leaves the floor,
    # the tier and every other agent exactly as they were, and it is legible in
    # one grep of the config.
    #
    # DELIBERATELY NOT HONOURED BY A DRAIN OR A FREEZE — see `admits`. Those are
    # about total spend and about a blind signal; this is about the SHAPE of what
    # remains dispatchable under a floor, which is a different question.
    exempt: tuple[str, ...] = ()
    # Windows whose tiers stand down near a reset, because the budget they are
    # protecting is about to be destroyed rather than saved (aegis-yegfx). See
    # the Burndown docstring — in particular that its fail-safe runs the OTHER
    # WAY from everything else here.
    burndowns: tuple[Burndown, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.tiers)

    def burndown_for(self, window: str) -> Burndown | None:
        for b in self.burndowns:
            if b.window == window:
                return b
        return None

    def burn_ceiling(self, window: str, burn: Burndown) -> float:
        """The percentage burndown will not spend past, for `window`.

        The window's LOWEST drain threshold, minus `reserve`; 100 - reserve if
        this window declares no drain. Read off the tiers rather than configured
        separately so the bound cannot drift away from the drain it is derived
        from: an operator who moves the drain moves this, with nothing to
        remember. A deployment that wants a tighter bound than its own drain
        raises `reserve`.
        """
        drains = [t.at for t in self.tiers_for(window) if t.drains]
        return (min(drains) if drains else 100) - burn.reserve

    def tier_at(self, level: int, window: str = FIVE_HOUR) -> Tier | None:
        """The tier at `level` IN `window`. Window-qualified since aegis-59hao:
        `at` alone stopped being a key the moment two budgets could each declare
        a tier at 80, and a hysteresis hold that resolved to the wrong window's
        tier would hold the wrong restriction."""
        for t in self.tiers:
            if t.at == level and t.window == window:
                return t
        return None

    def windows(self) -> tuple[str, ...]:
        """Every window some tier declares, in a stable order (aegis-59hao).

        Derived from the tiers rather than configured separately: a window with
        no tier governs nothing, so asking the producer for it would be a read
        whose answer can never matter, and a tier whose window nobody reads is
        the bug this bead is about. One list, from the tiers themselves.
        """
        seen = [t.window for t in self.tiers]
        return tuple(sorted(set(seen), key=seen.index))

    def tiers_for(self, window: str) -> tuple[Tier, ...]:
        return tuple(t for t in self.tiers if t.window == window)

    def candidate(self, pct: float, window: str = FIVE_HOUR) -> Tier | None:
        """The highest tier THIS WINDOW's reading alone engages.

        Window-scoped since aegis-59hao: comparing a seven-day percentage against
        a five-hour tier's threshold is comparing two different budgets, and the
        thresholds are deliberately asymmetric (tighter on the weekly, because it
        refills in days rather than hours). Tiers are stored sorted.
        """
        found = None
        for t in self.tiers_for(window):
            if pct >= t.at:
                found = t
        return found

    def engaged(self, top: Tier | None) -> tuple[Tier, ...]:
        """EVERY tier at or below `top` — because the tiers are CUMULATIVE.

        THIS IS THE BUG THAT SHIPPED FOR ABOUT AN HOUR, and it was invisible to a
        test suite that exercised each tier alone. Reading only the highest
        tier's own fields, the spoken configuration does this:

            75%  ->  70% tier, min_priority = 0   ->  P2 refused    (right)
            97%  ->  95% tier, action = "drain"   ->  P2 ADMITTED   (absurd)

        The 95% tier declares no priority floor, so the floor VANISHED at exactly
        the usage where it matters most: the fleet is being asked to stop and push
        its work, and a coordinator could still hand out P2 chores. Restriction
        must be monotone in usage — it can only ever tighten as the number climbs.

        Found end-to-end, by driving the real CLI at 45/55/75/85/97 and reading
        what it did, which is the only reason it is not still there.
        """
        if top is None:
            return ()
        # Within ONE window (aegis-59hao). Cumulative means "everything this
        # budget's climb already engaged"; a five-hour tier is not below a
        # seven-day one, they are different scales. The union ACROSS windows is
        # taken by the caller, from each window's own cumulative set.
        return tuple(t for t in self.tiers_for(top.window) if t.at <= top.at)


# --- the reading --------------------------------------------------------------

@dataclass(frozen=True)
class Reading:
    """One observation of the usage signal.

    `pct is None` and `ok is False` are BOTH could-not-look, and they are kept
    apart because they need different fixes: no value means the series is absent
    (fnd8g has not landed, or the query is wrong), while ok=False means the
    producer ran and told us its own probe FAILED. Collapsing them would send an
    operator to read a Prometheus config when the answer is in a probe log.
    """
    pct: float | None = None
    at: float | None = None          # the probe's own timestamp, epoch seconds
    ok: bool = True                  # claude_usage_probe_success
    error: str = ""                  # why we could not look at all
    source: str = ""
    # How old the PUBLISHED percentage is, straight from the producer. A SECOND,
    # independent staleness signal and not a duplicate of `at`: when the probe
    # fails the producer keeps serving the last good percentages, so a value can
    # be minutes old while everything else about the exposition looks current.
    # None = not published; the timestamp check below still applies.
    cache_age: float | None = None
    # WHEN THIS WINDOW'S BUDGET REFILLS, epoch seconds (aegis-9mehy). Unlike
    # everything else on this record it is per-WINDOW, because it describes a
    # budget rather than the probe.
    #
    # ITS ABSENCE IS NOT SIGNAL LOST, deliberately, and `lost()` below does not
    # mention it. A missing reset timestamp costs PROMPTNESS — the re-engagement
    # falls back to tend's five-minute pass — and promptness is not worth the
    # power to stop a fleet. Letting a missing ON-ramp input blind the governor
    # would make a nice-to-have able to do what the fail-safe forbids.
    reset_at: float | None = None

    def resets_in(self, now: float) -> float | None:
        """Seconds until this budget refills, or None if nothing published one.
        Negative means the published reset is already past — a real state
        (the probe has not re-read yet), not an error."""
        return None if self.reset_at is None else self.reset_at - now

    def lost(self, now: float, max_age: float) -> str:
        """"" if this reading is usable, else WHY it is not.

        A STALE READING IS NOT A READING. This is the whole fail-safe: the last
        value of a dead probe is not the current value, and treating it as one
        holds a spending fleet wide open at a number from last Tuesday.
        """
        if self.error:
            return self.error
        if not self.ok:
            # FLYING BLIND, not low usage. The producer RETAINS the last good
            # percentages when a probe fails and flags them here, so the number
            # still present alongside this flag is history, not a measurement —
            # and it is the direction that spends: a retained 11% holds the fleet
            # wide open for as long as the probe stays down.
            return (f"{PROBE_OK_METRIC} is 0 — a probe FAILED, so the published "
                    f"percentage is the last good one, not a measurement. This "
                    f"is flying blind, never low usage")
        if self.pct is None:
            # (The producer is a separate, still-open item; cited in the module
            # docstring's comment, never in an emittable string — this repo is
            # public and a string literal is a value the program can print.)
            return (f"no {USAGE_METRIC} series — nothing is publishing a "
                    f"utilization number to govern by")
        if self.at is None:
            return (f"{USAGE_METRIC} carries no {PROBE_TS_METRIC}, so its age "
                    f"cannot be checked — an unaged reading is indistinguishable "
                    f"from a frozen one")
        # THE OLDER OF THE TWO AGES WINS. The producer keeps serving the last good
        # percentages when a probe fails, so `cache_age` can be minutes while the
        # probe timestamp still looks recent. Taking the max is "be more
        # conservative, not less" — the number being governed by is only as fresh
        # as the stalest thing that produced it.
        age = now - self.at
        if self.cache_age is not None:
            age = max(age, self.cache_age)
        if age > max_age:
            return (f"the probe last succeeded {int(age)}s ago (limit "
                    f"{int(max_age)}s) — STALE, and a stale number reads green "
                    f"forever")
        # A clock skew big enough to put the probe in the future is not a
        # reading either: it would defeat every staleness check that follows.
        if age < -max_age:
            return (f"the probe timestamp is {int(-age)}s in the FUTURE — clock "
                    f"skew, so its age cannot be trusted")
        return ""


# --- the readers --------------------------------------------------------------

_SAMPLE = re.compile(r"^\s*([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([^\s#]+)")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')


def parse_prom(body: str) -> list[tuple[str, dict, float]]:
    """Prometheus text exposition -> [(name, labels, value)].

    Small on purpose: this reads a node_exporter textfile that WE produce
    (aegis-fnd8g), not arbitrary third-party exposition. It skips comments and
    unparseable values rather than raising, because a malformed line in a file
    that also contains the value we want must not blind the governor — but it
    never INVENTS a sample, so a file with nothing usable yields nothing and the
    caller reports could-not-look.
    """
    out: list[tuple[str, dict, float]] = []
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _SAMPLE.match(line)
        if not m:
            continue
        name, raw_labels, raw_value = m.group(1), m.group(2) or "", m.group(3)
        try:
            value = float(raw_value)
        except ValueError:
            continue
        labels = {k: v for k, v in _LABEL.findall(raw_labels)}
        out.append((name, labels, value))
    return out


def _from_samples(samples, window: str, source: str) -> Reading:
    """The ONE place samples become a Reading, shared by every reader.

    Shared deliberately: the textfile and the Prometheus paths must not be able
    to disagree about which window governs or about what a missing probe flag
    means. Two implementations of "is this signal lost" is two policies.

    EVERY MULTI-SERIES FOLD GOES THE CONSERVATIVE WAY, because the producer is
    multi-account and each of these arrives once per account:

        pct    MAX     the recording rule's own aggregation. An account at 88%
                       and one at 12% is a fleet at 88 — governing by the average
                       or by whichever came last spends the exhausted account's
                       budget it does not have.
        ok     MIN     ANY blind account blinds the governor. A probe that
                       succeeded for one account tells us nothing about another.
        age    MAX     the OLDEST reading wins. Freshness is only as good as the
                       stalest input to the number being governed by.
    """
    by_window = _readings_by_window(samples, source)
    if window in by_window:
        return by_window[window]
    # Asked for a window the exposition does not carry. Not a Reading with a
    # None pct dressed up as fine — say which window, because with two budgets
    # "no value" without a window name sends the reader to the wrong metric.
    return Reading(source=source,
                   error=f"no {USAGE_METRIC} series for window {window!r} — the "
                         f"exposition carries {sorted(by_window) or 'no windows'}")


def _readings_by_window(samples, source: str) -> dict[str, Reading]:
    """Fold samples into ONE Reading PER WINDOW (aegis-59hao).

    THE TRANSPORT ALREADY CARRIED EVERY WINDOW AND WE THREW THEM AWAY. Both
    readers fetch the whole `claude_usage_*` family in one call — the Prometheus
    query is a regex over the family, the textfile is read whole — and then
    filtered to a single configured window. So the governor could see that the
    weekly budget was further gone than the five-hour one and did not look. This
    function is that filter deleted; no reader changed shape to get it.

    WHAT IS PER-WINDOW AND WHAT IS NOT: the percentage and the reset timestamp
    carry a `window` label — they describe a BUDGET. The probe's success flag,
    its timestamp and the published cache age describe the PROBE, so they are
    folded once and shared by every window. Splitting them would invent a
    per-window staleness the producer never published.

    Every fold stays conservative, for the same reasons as before: pct MAX (an
    account at 88 and one at 12 is a fleet at 88), ok MIN (any blind account
    blinds the governor), age MAX (freshness is only as good as the stalest
    input) — and reset MAX for the same family of reason (aegis-9mehy): the
    governed percentage is the WORST account's, so the moment that number can
    fall is the LAST account's reset, not the first. Taking the earliest would
    arm a wake that reads a still-high number, report "the reset did not land",
    and hold the tier — correct behaviour reached by an avoidably wrong route.
    """
    at = cache_age = None
    ok = True
    saw_ok = False
    pct: dict[str, float] = {}
    rule_pct: dict[str, float] = {}
    reset: dict[str, float] = {}
    for name, labels, value in samples:
        w = labels.get("window")
        if name == RESET_TS_METRIC and w:
            reset[w] = value if w not in reset else max(reset[w], value)
        elif name == USAGE_METRIC and w:
            # The recording rule is ALREADY the max across accounts. If Prometheus
            # somehow returned several, max again — it cannot be wrong.
            rule_pct[w] = value if w not in rule_pct else max(rule_pct[w], value)
        elif name == ACCOUNT_USAGE_METRIC and w:
            pct[w] = value if w not in pct else max(pct[w], value)
        elif name == PROBE_OK_METRIC:
            ok, saw_ok = (ok and bool(value)), True
        elif name == PROBE_TS_METRIC:
            at = value if at is None else min(at, value)
        elif name == CACHE_AGE_METRIC:
            cache_age = value if cache_age is None else max(cache_age, value)
    # The rule wins where it exists (Prometheus); the per-account max is the
    # textfile's equivalent, computed here rather than left to whichever account
    # sorted last.
    pct.update(rule_pct)
    out: dict[str, Reading] = {}
    for w, v in pct.items():
        if not saw_ok:
            # The producer published a value and no success flag. NOT read as ok:
            # the flag is how staleness is meant to be visible, and inferring "it
            # must have worked" from the presence of a number is exactly the
            # assumption the flag exists to remove.
            out[w] = Reading(pct=v, at=at, ok=True, source=source,
                             cache_age=cache_age, reset_at=reset.get(w),
                             error=f"no {PROBE_OK_METRIC} series — the value "
                                   f"cannot be vouched for, so it is not read "
                                   f"as a measurement")
        else:
            out[w] = Reading(pct=v, at=at, ok=ok, source=source,
                             cache_age=cache_age, reset_at=reset.get(w))
    return out


class StubReader:
    """A fixed reading. The seam that makes the whole governor testable NOW —
    aegis-fnd8g has not produced the real series yet, and a governor that could
    only be exercised against a metric that does not exist would ship untested and
    be discovered wrong at 95%."""

    def __init__(self, pct: float | None = None, at: float | None = None,
                 ok: bool = True, error: str = "", now=time.time,
                 window: str = FIVE_HOUR, resets=None):
        # pct may be a NUMBER (one window, the original shape) or a
        # {window: pct} MAP (aegis-59hao). The map is what lets a test express
        # the case the bug is about — a nearly-empty five-hour budget beside an
        # exhausted weekly — which a single number cannot say at all.
        self._pct, self._at, self._ok, self._error = pct, at, ok, error
        self._now = now
        self._window = window
        # {window: epoch} — the reset timestamps (aegis-9mehy). Same shape as
        # pct so REHEARSING A RESET is one dict change: drop the percentage,
        # move the timestamp forward. That rehearsal is the point of the stub —
        # the case that must be proved is the one where the clock passed and the
        # NUMBER DID NOT FALL, and waiting five hours to observe it is not a test.
        self._resets = resets if isinstance(resets, dict) else (
            {} if resets is None else {window: resets})

    def read(self) -> Reading:
        return self.read_all().get(
            self._window,
            Reading(source="stub", at=self._at,
                    error=f"the stub declares no window {self._window!r}"))

    def read_all(self) -> dict[str, Reading]:
        at = self._at if self._at is not None else self._now()
        if isinstance(self._pct, dict):
            return {w: Reading(pct=v, at=at, ok=self._ok, error=self._error,
                               source="stub", reset_at=self._resets.get(w))
                    for w, v in self._pct.items()}
        return {self._window: Reading(pct=self._pct, at=at, ok=self._ok,
                                      error=self._error, source="stub",
                                      reset_at=self._resets.get(self._window))}


class TextfileReader:
    """The node_exporter textfile aegis-fnd8g writes, read DIRECTLY.

    Worth having alongside the Prometheus reader because it removes a whole
    hop: on the box that runs both the probe and the crew, going through
    Prometheus adds a scrape interval of lag and a service that can be down, to
    read a file sitting on local disk. It is also the honest fallback when
    Prometheus is the thing that is broken.
    """

    def __init__(self, path, window: str = FIVE_HOUR):
        self.path = Path(path)
        self.window = window

    def read(self) -> Reading:
        try:
            body = self.path.read_text()
        except OSError as e:
            return Reading(source="textfile",
                           error=f"cannot read {self.path}: {e}")
        return _from_samples(parse_prom(body), self.window, "textfile")

    def read_all(self) -> dict[str, Reading]:
        """Every window the file carries. The read was always whole-file; only
        the filter was single-window (aegis-59hao)."""
        try:
            body = self.path.read_text()
        except OSError as e:
            return {}
        return _readings_by_window(parse_prom(body), "textfile")


class PrometheusReader:
    """`/api/v1/query` against a Prometheus. One request, not three: the whole
    `claude_usage_*` family comes back in a single instant-vector query, so a
    tend pass costs one round trip and the three values are consistent with each
    other rather than sampled a second apart."""

    # BOTH families: the recording rule (`claude:usage_utilization_pct:max`)
    # carries a COLON, so a `claude_usage_.*` pattern silently misses the one
    # series the governor is supposed to read and falls back to per-account.
    QUERY = '{__name__=~"claude_usage_.*|claude:usage_.*"}'

    def __init__(self, url: str, window: str = FIVE_HOUR, timeout: float = 5.0,
                 fetch=None, username: str | None = None,
                 password: str | None = None):
        self.url = (url or "").rstrip("/")
        self.window = window
        self.timeout = timeout
        self.username, self.password = username, password
        # One tiny transport seam so tests inject a fake without patching
        # urllib — the same shape forgejo.py uses.
        self._fetch = fetch or self._http

    def _http(self, url: str) -> str:
        headers = {"Accept": "application/json"}
        if self.username:
            # BASIC AUTH, because a Prometheus that requires it and a reader that
            # cannot send it produce a governor that is permanently signal-lost —
            # and on the `warn` default that is a fleet running ungoverned with an
            # alarm nobody can fix from the config file. The password comes from
            # `password_file`, never inline: shantytown.toml is a tracked,
            # diffable, hand-edited file and a secret in it is a secret in git.
            import base64
            token = base64.b64encode(
                f"{self.username}:{self.password or ''}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read().decode()

    def read(self) -> Reading:
        samples, err = self._samples()
        if err is not None:
            return err
        return _from_samples(samples, self.window, "prometheus")

    def read_all(self) -> dict[str, Reading]:
        """Every window the query returned. QUERY is a regex over the whole
        `claude_usage_*` family, so both windows were always in this response —
        the single-window filter was the only thing hiding one (aegis-59hao)."""
        samples, err = self._samples()
        if err is not None:
            # One transport failure is not per-window news. Give every window the
            # SAME error rather than an empty map, so the caller reports "cannot
            # see" for each budget instead of silently governing on none of them.
            return {}
        return _readings_by_window(samples, "prometheus")

    def _samples(self):
        """(samples, error_reading) — exactly one is not None."""
        from urllib.parse import quote
        url = f"{self.url}/api/v1/query?query={quote(self.QUERY)}"
        try:
            body = self._fetch(url)
        except urllib.error.HTTPError as e:
            # NAME AN AUTH FAILURE AS ONE. "unreachable" is what the generic
            # branch below says, and for a 401 it is false in the expensive
            # direction: the server is up and answering, it is refusing US, and
            # an operator told "unreachable" goes looking for a Prometheus
            # outage instead of a missing `username`/`password_file`.
            if e.code in (401, 403):
                return None, Reading(source="prometheus",
                               error=f"{self.url} answered {e.code} "
                                     f"{'UNAUTHORIZED' if e.code == 401 else 'FORBIDDEN'}"
                                     f" — it is up and refusing this query. Set "
                                     f"`username` and `password_file` under "
                                     f"[governor]; this is a credential problem, "
                                     f"not an outage")
            return None, Reading(source="prometheus",
                           error=f"{self.url} answered HTTP {e.code}")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            return None, Reading(source="prometheus",
                           error=f"{self.url} unreachable: {e}")
        try:
            data = json.loads(body)
            if data.get("status") != "success":
                return None, Reading(source="prometheus",
                               error=f"{self.url} answered status="
                                     f"{data.get('status')!r}")
            result = data["data"]["result"]
        except (ValueError, KeyError, TypeError) as e:
            return None, Reading(source="prometheus",
                           error=f"{self.url} answered something this reader "
                                 f"cannot parse ({type(e).__name__})")
        samples = []
        for row in result:
            metric = row.get("metric") or {}
            name = metric.get("__name__", "")
            try:
                value = float(row["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            samples.append((name, metric, value))
        return samples, None


def reader_for(policy: Policy, *, now=time.time):
    """Policy -> a reader. RAISES for a source it cannot wire, rather than
    silently degrading to the stub: a governor that quietly reads a stub instead
    of Prometheus reports 'no restriction' forever, which is the exact
    holds-the-fleet-open failure this module's fail-safe section is about."""
    if policy.source == "stub":
        return StubReader(pct=policy.stub_pct, now=now)
    if policy.source == "textfile":
        if not policy.path:
            raise GovernorError("source = \"textfile\" needs `path` — the "
                                "node_exporter textfile to read")
        return TextfileReader(policy.path, policy.window)
    if policy.source == "prometheus":
        if not policy.url:
            raise GovernorError("source = \"prometheus\" needs `url` — e.g. "
                                "url = \"http://prometheus.example\"")
        password = None
        if policy.password_file:
            try:
                password = Path(policy.password_file).expanduser().read_text().strip()
            except OSError as e:
                # REFUSE, never fall through unauthenticated. An unreadable
                # password file that degraded to an anonymous request would get a
                # 401, report SIGNAL LOST, and send the operator hunting a
                # Prometheus outage that is really a file permission.
                raise GovernorError(
                    f"password_file {policy.password_file!r} cannot be read "
                    f"({e}) — refusing to query unauthenticated, which would "
                    f"look like an outage rather than a config error") from e
        return PrometheusReader(policy.url, policy.window,
                                username=policy.username, password=password)
    raise GovernorError(f"unknown source {policy.source!r}; "
                        f"expected one of: {', '.join(SOURCES)}")


# --- hysteresis state ---------------------------------------------------------

@dataclass
class Engaged:
    """Which tier is currently HELD, and since when.

    `since` is the drain EPISODE key as well as a timestamp: a drain broadcast is
    deduped against it, so relaxing below a drain tier and climbing back into it
    starts a NEW episode and every agent is told again. Carrying one episode
    across a relax would silence the second drain, which is the failure that
    matters — the first drain's agents are gone and the fleet that came back has
    never been asked.

    HYSTERESIS IS PER-WINDOW (aegis-59hao). Two budgets rise and fall on their
    own clocks — the five-hour refills in hours, the weekly in days — so one
    remembered `at` cannot describe both. A shared hold would let a relaxing
    five-hour reading drop a tier the weekly budget still justifies, which is
    exactly the "spends a budget it does not have" failure hysteresis exists to
    prevent, arriving through the back door.

    `at`/`since` remain the FIVE_HOUR values so an on-disk state written by the
    single-window governor still reads correctly — an upgrade must not silently
    forget an engaged tier.
    """
    at: int | None = None
    since: float = 0.0
    # window -> {"at":int|None, "since":float, "reset":float|None}
    by_window: dict = field(default_factory=dict)

    def hold(self, window: str) -> tuple[int | None, float]:
        """(at, since) for one window, falling back to the legacy flat fields."""
        if window in self.by_window:
            d = self.by_window[window] or {}
            at = d.get("at")
            return (None if at is None else int(at)), float(d.get("since") or 0.0)
        if window == FIVE_HOUR:
            return self.at, self.since
        return None, 0.0

    def reset_of(self, window: str) -> float | None:
        """The reset timestamp this window carried on the LAST pass, or None.

        Remembered for exactly one purpose (aegis-9mehy): telling a budget that
        REFRESHED apart from a budget that merely fell. A percentage dropping
        because agents stopped burning it and a percentage dropping because the
        window rolled over look identical in the number, and the log line an
        operator reads at 22:40 should not have to guess which it was. A reset we
        recorded that is now in the past, replaced by a later one, is the
        rollover — observed, not predicted.
        """
        d = self.by_window.get(window) or {}
        v = d.get("reset")
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None


class FilesGovernorState:
    """The engaged tier, on disk beside launched/ and stopped/.

    ON DISK because hysteresis that lives in a process is hysteresis that resets
    every tend pass — and a tend pass is a fresh process every five minutes. The
    whole point of relax_margin is to remember what we decided last time.
    """

    def __init__(self, root):
        self.path = Path(root) / "governor" / "state.json"

    def get(self) -> Engaged:
        try:
            d = json.loads(self.path.read_text())
            at = d.get("at")
            bw = d.get("by_window")
            return Engaged(at=None if at is None else int(at),
                           since=float(d.get("since") or 0.0),
                           by_window=bw if isinstance(bw, dict) else {})
        except (OSError, ValueError, TypeError):
            # Unreadable state is NO hold, never an invented one. Fail-open: the
            # governor still engages whatever the live reading says, it just does
            # not remember a higher tier it can no longer prove.
            return Engaged()

    def set(self, engaged: Engaged) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            from .files import write_json_atomic
            # `at`/`since` stay at top level as the FIVE_HOUR mirror so a
            # DOWNGRADE reads a sane hold instead of none (aegis-59hao). The
            # per-window map is the real record.
            write_json_atomic(self.path, {"at": engaged.at,
                                          "since": engaged.since,
                                          "by_window": engaged.by_window})
        except OSError:
            pass          # best-effort, same rule as the launch stamp


# --- the verdict --------------------------------------------------------------

# Why an agent is excluded / an item refused — the strings a caller renders.
ADMITTED = ""


def fmt_eta(seconds: float | None) -> str:
    """A duration a human reads at a glance: `1h35m`, `12m`, `2d 22h`, `now`.

    "Resets in 1h35m" is the difference between an operator WAITING and an
    operator INTERVENING, which is the entire argument for surfacing this at all
    (aegis-9mehy decision 7). A bare epoch on a status bar is a number nobody
    subtracts in their head at 3am.
    """
    if seconds is None:
        return "—"
    s = int(seconds)
    if s <= 0:
        # PAST, not zero. The producer's cron has not re-read yet, so the
        # timestamp is history — and saying "0s" would read as "about to happen".
        return "now (overdue)" if s < -60 else "now"
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m"
    return f"{s}s"


def fmt_when(seconds: float | None) -> str:
    """The same duration as a PHRASE — `in 1h35m`, `now`, `now (overdue)`.

    Separate from fmt_eta because "resets in now" is what you get from gluing
    "in " onto a duration formatter that can also return an instant, and that is
    what the first live run of this feature printed. The past case is not a
    degenerate duration; it is a different sentence, and the caller should not
    have to know which one it is about to get.
    """
    if seconds is None:
        return "—"
    eta = fmt_eta(seconds)
    return eta if eta.startswith("now") else f"in {eta}"


@dataclass(frozen=True)
class Relaxed:
    """One window LEFT a tier this pass — the ON ramp's observable (aegis-9mehy).

    THIS RECORD IS WRITTEN FROM A READING, NEVER FROM A CLOCK. `refreshed` says
    the budget was measured to have rolled over (a reset we recorded has passed
    and a later one has replaced it); it is reported, and it is not what caused
    the tier to be left. The tier was left because the percentage fell below
    (threshold - relax_margin). A pass where the clock rolled and the number did
    not produces no Relaxed at all, which is the behaviour that matters: re-
    engaging on a predicted drop would spend the whole refreshed budget in
    minutes, the exact failure the governor exists to prevent.
    """
    window: str
    was: int                       # the tier `at` that is no longer held
    now_at: int | None             # the tier still engaged in this window, if any
    pct: float
    refreshed: bool = False        # the window's budget was MEASURED to roll over
    reset_at: float | None = None  # the NEXT reset, from this same reading

    def render(self, woke_for: str = "", now: float | None = None) -> str:
        """The line tend prints. It names WHICH WINDOW and WHICH PATH.

        Decision 3 of the bead: the timer exists for promptness and tend's
        five-minute pass is the fallback, so a reader must be able to tell which
        one actually got us here. If the timer silently stopped working we would
        otherwise keep re-engaging — five minutes later, every time — and nothing
        would ever say so.
        """
        why = ("its budget REFRESHED" if self.refreshed
               else "the reading fell on its own (no rollover observed)")
        to = (f"the {self.now_at}% tier still holds" if self.now_at is not None
              else "NO tier holds this window now")
        if woke_for == self.window:
            path = f"the {self.window} reset wake timer"
        elif woke_for:
            path = (f"the {woke_for} reset wake timer (it woke for a different "
                    f"window; this one relaxed too)")
        else:
            path = "the scheduled tend pass (no wake timer fired)"
        nxt = ""
        if self.reset_at is not None and now is not None:
            nxt = f", next {self.window} reset {fmt_when(self.reset_at - now)}"
        return (f"governor RE-ENGAGE: {self.window} is {self.pct:.0f}% and "
                f"{why} — the {self.was}% tier is RELEASED, {to}. Found by "
                f"{path}{nxt}. Held agents become eligible on this pass; "
                f"`retired` ones do not.")


@dataclass(frozen=True)
class Burning:
    """One window is IN BURNDOWN this pass — the observable (aegis-yegfx).

    THIS RECORD EXISTS SO THE RELAXATION CANNOT BE SILENT. A governor that
    quietly stops governing is indistinguishable from a governor that is broken,
    and this repo has already paid for that once: aegis-yc864 was a display which
    disagreed with enforcement, and the display was the one that lied. Burndown
    is the only mechanism here that makes the fleet spend MORE, so it is the one
    that most needs to say so on every surface, every pass.

    It names the ceiling and the headroom because "the governor stood down" is
    an alarming sentence on its own and a reasonable one with a bound attached.
    """
    window: str
    pct: float
    ceiling: float
    resets_in: float
    suspended: tuple = ()          # the tiers that would otherwise be in force

    @property
    def headroom(self) -> float:
        """Points still spendable before the ceiling stops this."""
        return max(0.0, self.ceiling - self.pct)

    def render(self) -> str:
        what = "; ".join(t.label() for t in self.suspended) or "no tier was in force"
        return (f"governor BURNDOWN: {self.window} is {self.pct:.0f}% and its "
                f"budget refills in {fmt_eta(self.resets_in)} — the points left "
                f"are use-it-or-lose-it, so its tiers STAND DOWN ({what}). "
                f"Spending is capped at {self.ceiling:.0f}% "
                f"({self.headroom:.0f} points of headroom); the drain is NOT "
                f"waived and re-engages the moment the reading reaches it.")


@dataclass(frozen=True)
class Verdict:
    """What the governor decided this pass, and on what evidence.

    Every field a caller needs to explain itself is here, because a refusal that
    cannot state the reading behind it is indistinguishable from a bug — the same
    argument dispatch.TriageRefused makes for carrying the whole Decision.
    """
    reading: Reading
    pct: float | None = None
    tier: Tier | None = None
    held: bool = False               # the tier is held by hysteresis, not by pct
    signal_lost: bool = False
    frozen: bool = False             # signal lost AND on_signal_lost = "freeze"
    why: str = ""
    alarm: str = ""                  # non-empty -> say it LOUDLY, EVERY pass
    # EVERY engaged tier, not just the top one. Restriction is CUMULATIVE and
    # must be monotone in usage — see Policy.engaged for the bug that proves it.
    engaged: tuple[Tier, ...] = ()
    # {window: epoch} — when each READABLE window's budget refills (aegis-9mehy).
    # Only windows we actually got a reading for; a blind window contributes
    # nothing here, because "we cannot see the budget" must not become "the
    # budget resets at 0".
    resets: dict = field(default_factory=dict)
    # Windows that LEFT a tier on this pass. Empty on almost every pass, which is
    # the point: it is the ON ramp firing, and it is the thing to log.
    relaxed: tuple = ()
    # Windows STANDING DOWN because their budget is about to be destroyed
    # (aegis-yegfx). Distinct from `relaxed`: that one is a tier that stopped
    # being justified by the reading, this one is a tier that IS justified by the
    # reading and is being suspended anyway, because conserving a budget that
    # refills in an hour conserves nothing. The tiers stay in `engaged`'s
    # computation upstream and are filtered on the way out, so the hysteresis
    # hold survives a burndown exactly as it survives a lost signal — burndown
    # suppresses APPLICATION, never the decision.
    burning: tuple = ()
    # {window: pct} — the reading each READABLE window was judged on this pass.
    #
    # WHY THIS EXISTS AT ALL (aegis-cjjdx). `pct` above is a single scalar taken
    # from the POLICY'S DEFAULT window, while `tier` can belong to a different
    # one. Every message that printed the two together therefore reported a
    # percentage that had nothing to do with the tier beside it. Two careful
    # readers independently concluded from `45% tier is engaged (usage 11%)` that
    # the governor had LATCHED at a high-water mark and was throttling a fleet on
    # a condition that had cleared — a bug that did not exist. The real reading
    # was seven_day 57%, correctly above its own 45% threshold.
    #
    # That near-miss is the argument for the field: the proposed "fix" for the
    # imagined latch was to release a tier early, i.e. to disable a spend guard
    # that was working. A display bug that manufactures a plausible false bug
    # report is more dangerous than one that merely confuses.
    by_window: dict = field(default_factory=dict)
    # Agents the PRIORITY FLOOR does not apply to. Copied off the policy so a
    # Verdict stays self-contained — every other field a refusal needs to explain
    # itself is here, and "why was this one let through" is the same question.
    exempt: tuple[str, ...] = ()

    def resets_in(self, window: str, now: float) -> float | None:
        at = self.resets.get(window)
        return None if at is None else at - now

    def holding_windows(self) -> set[str]:
        """The windows that must ALL refill before the GOVERNING restriction lifts.

        Derived from the same computation `admits` enforces and `governing`
        displays — never a second opinion about which restriction is in force.
        Order matches `governing`: a drain outranks any floor, then the strictest
        declared floor, then a who-runs restriction.

        The set is usually one window. It is more than one when two tiers in
        DIFFERENT budgets declare the SAME strictest restriction — and that case
        is the whole reason this returns a set rather than a tier. Each of them
        holds the restriction up on its own, so the restriction survives until the
        LAST of them refills; picking either one alone understates it.
        """
        if self.drains:
            return {t.window for t in self.engaged if t.drains and t.window}
        floor = self.floor
        if floor is not None:
            # `== floor`, not `is not None`: a tier declaring P1 under a P0 floor
            # is NOT holding the floor up, and its refill changes nothing a
            # dispatcher can act on. This is the same distinction cjjdx drew
            # between engaged and unengaged, one level finer.
            return {t.window for t in self.engaged
                    if t.min_priority == floor and t.window}
        trait_windows = {t.window for t in self.trait_tiers if t.window}
        if trait_windows:
            return trait_windows
        g = self.governing
        return {g.window} if g is not None and g.window else set()

    def next_reset(self, now: float) -> tuple[str, float] | None:
        """(window, seconds) until the GOVERNING restriction lifts, or None.

        THE FIRST BUG THIS FIXED (aegis-cjjdx). It used to take `min()` over every
        readable window. Measured live 2026-08-02: `five_hour` was NOT engaged
        (`at: null`) and refilled in 3h18m, while `seven_day` WAS engaged at 45%
        and refilled in 56.6h. `st crew` therefore told the coordinator the fleet
        "comes back on its own when the five_hour budget resets in 3h18m" — the
        wrong window, understating by 17x, for a fleet that was actually
        restricted to P1-and-above for two and a half days.

        THE SECOND, WHICH THE FIRST FIX DID NOT REACH (aegis-qhi9a). Restricting
        to ENGAGED windows is necessary and not sufficient: with TWO windows
        engaged, the soonest of them need not be the one holding the restriction
        up either. Measured live 2026-08-02 by dearing:

            seven_day usage 65% · 65% tier · dispatch only P0 and above
                                           · five_hour resets in 1h20m

        Every number true, and read together they say the P0 floor lifts in
        1h20m. It does not: the floor comes from the seven_day tier ~54h out, and
        five_hour dropping below its own threshold leaves seven_day@65 engaged
        and the floor exactly where it was. This is the same failure as cjjdx —
        a true number answering an ADJACENT question — which is why the same
        reader found it, and it is why the ruling is `holding_windows` above
        rather than another filter on `resets`: the display now derives the
        answer from the enforcement, instead of approximating it.

        Which reset to show when two windows are engaged was a JUDGEMENT, not a
        correction, so it was ruled rather than assumed: dearing, on the bead —
        the one that lifts the GOVERNING restriction, named.

        MAX, NOT MIN, over the holding windows. They each hold the restriction on
        their own, so it stands until the last of them refills. min() would
        reintroduce the very bug this fixes one level in.

        Returns None when ANY holding window has no readable reset. That is the
        honest answer and it is deliberately not "report the one we can see": a
        window we cannot read might be the long pole, so naming the readable one
        would state a lift time we have no basis for — the same manufactured
        confidence as a blind window becoming "resets at 0". Silence is already
        this file's convention for unknowable (see `reset_note`).

        KNOWN LIMIT, stated rather than left to be rediscovered: when a floor and
        a TRAIT tier are engaged in different budgets, this reports the floor's
        window, so a who-runs restriction in a longer budget can outlast the time
        given here. `restrictions` (aegis-upo93) is the field that shows the whole
        stack; this one answers "when does the binding restriction lift".
        """
        holding = self.holding_windows()
        if not holding:
            return None
        if any(w not in self.resets for w in holding):
            return None
        w = max(holding, key=lambda k: self.resets[k])
        return w, self.resets[w] - now

    @property
    def drains(self) -> bool:
        """Draining is only ever the consequence of a reading we GOT. A signal we
        could not read can never reach this — see the fail-safe note in the module
        docstring: never fail INTO drain."""
        if self.signal_lost:
            return False
        return any(t.drains for t in self.engaged)

    @property
    def floor(self) -> int | None:
        """The effective priority floor: the STRICTEST any engaged tier declares.

        Strictest is the LOWEST number (0 is the highest priority), so this is a
        min, not a max. A higher tier that declares no floor inherits the one
        below it rather than erasing it.
        """
        floors = [t.min_priority for t in self.engaged if t.min_priority is not None]
        return min(floors) if floors else None

    @property
    def governing(self) -> Tier | None:
        """The tier a HUMAN must be shown: the one whose restriction is in force.

        This exists because the display and the enforcement disagreed, and the
        display was the one that lied (aegis-yc864). `st crew --governor` and
        `st tend` named `engaged[-1]` — a POSITIONAL pick, justified by a comment
        reading "cumulative, so the last one is the most restrictive". That was
        true when every tier was read against one budget. The two-budget change
        (aegis-59hao) made `engaged` span WINDOWS, and across windows position no
        longer implies strictness — but the caller kept its old justification.

        Measured cost: five_hour engaged at 50 (min_priority=1) and seven_day at
        65 (min_priority=0). `floor` correctly returned 0, so `st go` refused P1
        — while the status line rendered the 50 tier and announced "dispatch only
        P1 and above". A coordinator was told P1 was dispatchable by one command
        and refused by another, and the refusal text suggests raising an item's
        priority as the way out. That is a priority-inflation pump: the operator
        who trusts the display edits real work to satisfy a floor that was never
        what the display said. It happened to the person who had just written the
        warning about it.

        So this derives from the SAME computation `admits` enforces, rather than
        being a second opinion about it. Order: a drain outranks any floor (it
        refuses everything, including P0), then the strictest declared floor.
        """
        if not self.engaged:
            return None
        drains = [t for t in self.engaged if t.drains]
        if drains:
            return drains[-1]
        floors = [t for t in self.engaged if t.min_priority is not None]
        if floors:
            # min on the value the enforcement path uses — NOT on position.
            return min(floors, key=lambda t: t.min_priority)
        return self.engaged[-1]

    @property
    def trait_tiers(self) -> tuple[Tier, ...]:
        """Every engaged tier that restricts WHO RUNS. An agent must satisfy them
        all — two tiers each naming a band is two conditions, not a choice."""
        return tuple(t for t in self.engaged if t.traits)

    @property
    def restrictions(self) -> tuple[Tier, ...]:
        """EVERY engaged restriction a human must be shown — not one (aegis-upo93).

        `governing` above answers a NARROWER question than a display needs: "of
        the engaged tiers, which FLOOR is in force". Returning one tier is right
        for that, because floors compose into a single strictest number. It is
        wrong as the whole of a display, because a TRAIT tier is a restriction of
        a different KIND engaged at the same time, and one tier can only name one
        of them.

        MEASURED COST (aegis-upo93, 2026-08-03): seven_day at 79% with the floor
        tier and the traits tier both engaged. `st crew --governor` printed

            ok 3/50/2877 79/90/118076 dispatch only P0 and above [seven_day >= 65%]

        while `excludes` was refusing to let most of the roster run at all. The
        coordinator read that line, concluded the priority floor was the only
        restriction in force, and moved to restore an agent the governor had
        already banned. Nothing on the line could have said otherwise.

        This is aegis-yc864 one type out. That bug was two answers to "WHICH
        FLOOR"; this one is a display that could only ever answer one of two
        different QUESTIONS. So the fix is the same shape: derive the display
        from the same computations the enforcement uses (`governing` for the
        floor, `trait_tiers` for who runs), rather than leave each caller to
        remember there is a second kind.

        ORDER: the floor first, because it is the field most readers came for,
        then each trait tier. Deduplicated by value — a tier that is both the
        governing one and a trait tier is one restriction, not two.

        A DRAIN COLLAPSES TO ITSELF, matching `effect()`. Under a full stop
        nothing dispatches and nobody runs at any band, so listing "only support
        crew runs" beside it would describe a distinction the drain has already
        erased — and reading it as "support crew still runs" is exactly the wrong
        direction to be wrong in.
        """
        if not self.engaged:
            return ()
        top = self.governing
        if self.drains:
            return (top,) if top is not None else ()
        out: list[Tier] = [top] if top is not None else []
        for tier in self.trait_tiers:
            if tier not in out:
                out.append(tier)
        return tuple(out)

    def waives(self, item, agent=None) -> bool:
        """True if `agent` is floor-exempt AND the floor is what would otherwise
        have refused this item. Not "is this agent exempt" — the narrower
        question, because it is the one that must be REPORTED.

        An exemption that fires silently on every dispatch is indistinguishable
        from a governor that is off, which is precisely the state aegis-hdqej's
        fail-safe section refuses to allow. So callers announce a waiver, and to
        announce it only when it CHANGED the outcome they need this rather than a
        membership test: an exempt agent taking a P0 under a P0 floor was going
        to be admitted anyway, and saying "waived" there would train the reader
        to ignore the word.
        """
        if not agent or agent not in self.exempt:
            return False
        if self.frozen or self.drains:
            return False          # never waived — see admits
        floor = self.floor
        if self.tier is None or floor is None:
            return False          # no floor in force; nothing to waive
        priority = getattr(item, "priority", None)
        return priority is None or priority > floor

    def waiver_says(self, item, agent=None) -> str:
        """The one line a caller prints when `waives` is true. Names the agent,
        the item, the floor it cleared and WHERE the exemption is configured, so
        the reader can undo it without going looking."""
        priority = getattr(item, "priority", None)
        shown = "no priority" if priority is None else f"P{priority}"
        return (f"{self._tier_says()} and {getattr(item, 'id', 'this item')} is "
                f"{shown} — dispatched anyway because {agent} is FLOOR-EXEMPT "
                f"(`exempt` in [governor]). The floor still applies to every "
                f"other agent; spend against it is still spend.")

    def admits(self, item, agent=None) -> str:
        """"" if this item may be dispatched, else WHY not.

        READ THE SIGN OF `min_priority`. It is bd's numbering, where 0 is the
        HIGHEST priority — so `min_priority = 1` admits P0 and P1 and refuses P2
        and below. The key is named for what an operator means ("only P1 and
        above") and the comparison is therefore `<=`. This is a foot-gun and it
        is documented at every place it appears rather than renamed, because the
        name is the one Stiwi's spec used.

        AN ITEM WITH NO PRIORITY IS REFUSED while a floor is in force, and that
        direction is deliberate. The governor's entire job above 50% is to spend
        the remaining budget only on work whose importance is stated; admitting
        an item whose importance nobody stated defeats it silently. The refusal
        is per-item and operator-fixable in one command, which is a different and
        much cheaper thing than a fleet-wide stall.
        """
        if self.frozen:
            return (f"the usage governor is FROZEN: {self.why}. "
                    f"on_signal_lost = \"freeze\", so no NEW work is dispatched "
                    f"until the signal comes back. Nothing has been stopped and "
                    f"nothing is lost — in-flight work continues.")
        if self.drains:
            # A DRAIN TIER DISPATCHES NOTHING. Every live agent has been asked to
            # push its work and stop; handing one a new item contradicts the
            # instruction it is already acting on, and the item would be picked up
            # by a session that is about to end. Refused regardless of priority —
            # "full stop" that still admits a P0 is not a full stop.
            return (f"{self._tier_says()}: FULL STOP — the fleet is draining, so "
                    f"NOTHING is dispatched. The item is untouched and stays "
                    f"ready; dispatch it when usage falls.")
        # FLOOR EXEMPTION (aegis-yegfx). Deliberately placed AFTER frozen and
        # drain and before the floor, because that ordering IS the policy: a
        # drain is a full stop for spend reasons and a freeze means we cannot see
        # the budget at all, and neither becomes negotiable because of who is
        # asking. Only the priority floor — a judgement about which work is worth
        # the remaining budget — admits the idea that one agent's lane was
        # misjudged by a fleet-wide rule.
        if self.waives(item, agent):
            return ADMITTED
        floor = self.floor
        if self.tier is None or floor is None:
            return ADMITTED
        priority = getattr(item, "priority", None)
        if priority is None:
            return (f"{self._tier_says()} and {getattr(item, 'id', 'this item')} "
                    f"carries NO priority, so it cannot be shown to clear the "
                    f"floor. Set one and re-dispatch "
                    f"(`bd update {getattr(item, 'id', '<id>')} --priority=<0-4>`).")
        if priority > floor:
            return (f"{self._tier_says()} and {getattr(item, 'id', 'this item')} "
                    f"is P{priority}. Dispatch it when usage falls, or raise its "
                    f"priority if it really is that important.")
        return ADMITTED

    def excludes(self, agent, catalog=None) -> str:
        """"" if this agent may RUN under the current tier, else WHY not.

        FAIL-OPEN ON EVERY COULD-NOT-TELL. An unknown role, a catalog that cannot
        resolve a stacked conflict, no catalog at all — each of those means we do
        not know whether this agent is support crew, and the safe direction is to
        LET IT RUN. Spinning down an agent because we could not read its role
        would let a config typo stop the crew, which the fail-safe rules out by
        name.
        """
        if self.tier is None or self.signal_lost:
            return ADMITTED
        if self.drains:
            return (f"{self._tier_says()}: FULL STOP. Push your work, report what "
                    f"went up, then stop cleanly.")
        # EVERY trait tier, not just the top one — two engaged tiers each naming
        # a band are two conditions to satisfy, not a choice between them.
        for tier in self.trait_tiers:
            carried, known = carries_any(agent, tier.traits, catalog)
            if not known:
                continue                  # could not tell -> this tier lets it run
            if not carried:
                return (f"{self._tier_says()}, and {getattr(agent, 'name', '?')} "
                        f"does not carry {'/'.join(tier.traits)}.")
        return ADMITTED

    def effect(self) -> str:
        """What is ACTUALLY in force — composed across every engaged tier, not
        read off the top one. The status line uses this because a line that
        reported only the top tier's own fields would have hidden the cumulative
        bug (`95% tier · FULL STOP` while a P2 dispatched happily underneath)."""
        if self.drains:
            return "FULL STOP — every agent pushes its work, then stops; nothing dispatches"
        bits = []
        if self.floor is not None:
            bits.append(f"dispatch only P{self.floor} and above")
        for tier in self.trait_tiers:
            bits.append(f"only {'/'.join(tier.traits)} crew runs")
        return "; ".join(bits) or "no restriction declared"

    def _tier_says(self) -> str:
        """`the usage governor's 45% tier is engaged (seven_day usage 57%)`.

        NAMES THE TIER'S OWN WINDOW AND THAT WINDOW'S READING (aegis-cjjdx).
        It used to print the bare scalar `self.pct`, which is the POLICY'S
        DEFAULT window — so a seven_day tier was reported beside a five_hour
        percentage, with nothing on the line to say they were different budgets.

        Measured live 2026-08-02: `the usage governor's 45% tier is engaged
        (usage 11%)`. Both numbers were true and they described different
        windows; read together they say the governor is throttling on a
        condition that cleared, which is a LATCH BUG. Two readers reached that
        conclusion independently and one filed it. The actual reading was
        seven_day 57% — above its own 45% threshold, correctly engaged, nothing
        wrong at all.

        The remedy that false finding implied was to release the tier early:
        turn off a working spend guard on a budget that was genuinely 57% gone.
        Hence naming the window here rather than only in `why` — the refusal is
        what an operator reads, and it must not require cross-checking a second
        field to be read correctly.
        """
        t = self.tier
        assert t is not None
        pct = self.by_window.get(t.window, self.pct)
        seen = ("held from a previous pass (hysteresis)" if self.held
                else f"{t.window} usage {pct:.0f}%" if pct is not None else "usage")
        return f"the usage governor's {t.at}% tier is engaged ({seen})"

    def render(self, now: float | None = None) -> str:
        """One line for `st tend` / `st tend --status`.

        WITH A TIER ENGAGED IT NAMES THE RESET (aegis-9mehy decision 7). "we are
        throttled" and "we are throttled for another 1h35m" are different
        sentences to the person reading them: the first invites intervention, the
        second invites waiting. The number was already in the exposition and the
        governor simply never looked at it.
        """
        if not self.tier and not self.signal_lost:
            pct = "—" if self.pct is None else f"{self.pct:.0f}%"
            return f"  governor    usage {pct} · no tier engaged · wide open"
        if self.signal_lost:
            state = "FROZEN — no new dispatch" if self.frozen else "running UNGOVERNED"
            return f"  governor    SIGNAL LOST · {state} · {self.why}"
        # THE PERCENTAGE MUST BELONG TO THE TIER BESIDE IT (aegis-cjjdx, and
        # again in aegis-yc864). `self.pct` is read against the POLICY'S DEFAULT
        # window while `self.tier` may belong to another, so this line rendered
        # `usage 57% · 65% tier` — a five_hour reading beside a seven_day tier,
        # which reads as a governor latched above its own threshold. `effect()`
        # was fixed for exactly this and `render()` was not, so the refusal text
        # and the status line disagreed about the same pass.
        p = self.by_window.get(self.tier.window, self.pct)
        pct = "—" if p is None else f"{p:.0f}%"
        held = " (held)" if self.held else ""
        line = (f"  governor    {self.tier.window} usage {pct} · "
                f"{self.tier.at}% tier{held} · {self.effect()}")
        return line + self.reset_note(now)

    def reset_note(self, now: float | None = None) -> str:
        """` · five_hour resets in 1h35m`, or "" when nothing published one.

        SILENT WHEN UNKNOWN, never a placeholder. A governor that printed
        "resets in —" every pass would train an operator to ignore the field on
        the pass where it matters.
        """
        if now is None or not self.resets:
            return ""
        nxt = self.next_reset(now)
        if nxt is None:
            return ""
        window, left = nxt
        return f" · {window} resets {fmt_when(left)}"


def carries_any(agent, traits, catalog=None) -> tuple[bool, bool]:
    """(carries, could_tell). Does this agent's ROLE SET carry any of `traits`?

    RESOLVED THROUGH THE CATALOG, never by matching a role NAME (ruled on the
    crew-traits design bead, 2026-08-01). A name match would let `roles = [...,
    "support"]` work with nothing declaring what `support` IS — a second,
    ungoverned vocabulary beside the role stack, with no axis, no cardinality and
    no precedence. `support` is a VALUE on the survival axis, and a deployment
    that wants a support tier declares it:

        [roles.oncall]
        survival = "support"
        lane = ["monitoring"]

    TWO MATCH RULES, because the two new axes mean different things:

      survival (SINGLE, RANKED)  a THRESHOLD, not an equality. `traits =
                                 ["support"]` means "support AND ABOVE" — an
                                 agent banded `last` obviously survives a tier
                                 that spares `support`, and equality matching
                                 would spin down a coordinator banded `last`
                                 while keeping the watchers, which is upside
                                 down.
      everything else            exact membership. A lane is a set you are in or
                                 are not; there is no ordering to threshold on.

    `could_tell` is False when nothing could be resolved — an undeclared role, an
    unranked stacked conflict, no catalog at all. The caller must then fail OPEN
    (Verdict.excludes): a policy we cannot evaluate never spins the crew down.
    """
    from .traits import AXES, MULTI, SURVIVAL
    wanted = {t.lower() for t in traits}
    roles = tuple(getattr(agent, "effective_roles", lambda: ())() or ())
    if not roles:
        role = getattr(agent, "role", None)
        roles = (role,) if role else ()
    if not roles or catalog is None:
        return False, False
    try:
        composed = catalog.of(list(roles))
    except Exception:                     # noqa: BLE001 — UnknownRole/Ambiguous/anything
        return False, False               # could not tell -> the agent runs

    # THE THRESHOLD, on the one axis that declares an order.
    mine = getattr(composed, SURVIVAL, None)
    if mine is not None:
        my_rank = catalog.precedence.get((SURVIVAL, mine))
        for t in traits:
            want_rank = catalog.precedence.get((SURVIVAL, t.lower()))
            if want_rank is None or my_rank is None:
                continue
            if my_rank >= want_rank:
                return True, True

    # Exact membership everywhere else (and on survival too, so an UNRANKED
    # survival value still matches itself rather than being unusable).
    for axis, kind in AXES.items():
        value = getattr(composed, axis, None)
        if kind == MULTI:
            for v in value or ():
                if str(v).lower() in wanted:
                    return True, True
        elif value is not None and str(value).lower() in wanted:
            return True, True
    return False, True


# --- the governor -------------------------------------------------------------

class Governor:
    """Read the number, decide the tier. Nothing else — the ACTING lives in the
    surfaces that already act (`st go` refuses, `st tend` withholds, the Drainer
    asks). Every dependency is injected so a test drives 45/55/75/85/97 through
    the whole decision with no Prometheus, no clock and no filesystem.
    """

    def __init__(self, policy: Policy, reader, state=None, *, now=time.time):
        self.policy = policy
        self.reader = reader
        self.state = state
        self._now = now

    def evaluate(self, *, persist: bool = True) -> Verdict:
        """One decision, with hysteresis.

        `persist=False` is the READ path (`st go`). It honours a hold that is
        already recorded but never extends one — a dispatch command must not
        ratchet fleet policy as a side effect of being run. `st tend` is the
        evaluation point (it is the pass that already decides who lives), so it
        is the writer.
        """
        pol = self.policy
        now = self._now()
        if not pol.active:
            return Verdict(reading=Reading(source="none"),
                           why="no [[governor.tier]] declared — the governor is off")

        # transport_error is shared by EVERY window when the read itself failed.
        # One unreachable Prometheus is not per-window news, and synthesising
        # "no series for window X" from it would send an operator looking for a
        # missing metric instead of a dead endpoint.
        transport_error: Reading | None = None
        try:
            if hasattr(self.reader, "read_all"):
                readings = self.reader.read_all()
            else:
                # A reader from before aegis-59hao, or any injected test double
                # that implements only read(). The seam is the whole design; it
                # must not require every implementer to grow a second method.
                readings = {pol.window: self.reader.read()}
        except Exception as e:            # noqa: BLE001 — a reader must never fatal a pass
            readings = {}
            transport_error = Reading(
                source=pol.source,
                error=f"the {pol.source} reader raised "
                      f"{type(e).__name__}: {str(e)[:120]}")
        reading = readings.get(pol.window) or transport_error or Reading(
            source=pol.source,
            error=f"no usage series for window {pol.window!r} — the "
                  f"{pol.source} source carries "
                  f"{sorted(readings) or 'no windows'}")

        # ---- per-window evaluation (aegis-59hao) ----------------------------
        # TWO BUDGETS BOTH CONSTRAIN, and the tighter one wins. Each tier is
        # judged against ITS OWN window's reading and the results are UNIONED —
        # never averaged, never "the primary window", because an average lets a
        # freshly-refilled five-hour budget mask an exhausted weekly, which is
        # the entire bug. Restriction is the union of what every budget says.
        decided: dict[str, tuple] = {}     # window -> (chosen, held, since, pct)
        blind: list[tuple[str, str]] = []  # (window, why) — seen but unreadable
        resets: dict[str, float] = {}      # window -> epoch the budget refills
        relaxed: list[Relaxed] = []        # windows that LEFT a tier this pass
        prior = self.state.get() if self.state is not None else Engaged()
        for w in pol.windows():
            r = readings.get(w) or transport_error or Reading(
                source=pol.source,
                error=f"no usage series for window {w!r}")
            why_lost = r.lost(now, pol.max_age_seconds)
            if why_lost:
                blind.append((w, why_lost))
                continue
            # RECORDED FROM A READING WE ACCEPTED, never from a blind one. A
            # reset timestamp carried alongside a stale percentage is as stale as
            # the percentage; arming a wake from it would schedule a look based
            # on a number we have already refused to govern by.
            if r.reset_at is not None:
                resets[w] = r.reset_at
            wpct = float(r.pct)
            cand = pol.candidate(wpct, w)
            held_at, held_since = prior.hold(w)
            # THE HOLD, per window. A tier is left only BELOW (threshold -
            # relax_margin) so a reading oscillating around a threshold does not
            # flip fleet policy every pass. It can only ever hold a tier HIGHER
            # than the live reading justifies — never lower.
            wheld, wchosen = False, cand
            if held_at is not None and wpct >= held_at - pol.relax_margin:
                kept = pol.tier_at(held_at, w)
                if kept is not None and (wchosen is None or kept.at > wchosen.at):
                    wchosen, wheld = kept, True
            wsince = held_since
            if wchosen is None:
                wsince = 0.0
            elif held_at != wchosen.at or not wsince:
                # A NEW tier, or the first pass that engaged this one: start a
                # new episode. The episode key is what a drain dedupes on.
                wsince = now
            decided[w] = (wchosen, wheld, wsince, wpct)

            # ---- THE ON RAMP FIRING (aegis-9mehy) ---------------------------
            # A tier is LEFT here and nowhere else, and it is left because
            # `wpct` — a number we just read — fell below (held_at -
            # relax_margin). No branch above consults a reset timestamp, and
            # that is the design: the timer decides when to LOOK, the reading
            # decides what to DO. `refreshed` is reported on the way past.
            if held_at is not None and (wchosen is None or wchosen.at < held_at):
                prior_reset = prior.reset_of(w)
                refreshed = bool(
                    prior_reset is not None and prior_reset <= now
                    and (r.reset_at is None or r.reset_at > prior_reset))
                relaxed.append(Relaxed(
                    window=w, was=held_at,
                    now_at=None if wchosen is None else wchosen.at,
                    pct=wpct, refreshed=refreshed, reset_at=r.reset_at))

        # EVERY window blind = the old signal-lost path, unchanged. PARTIAL
        # blindness is different and must NOT stop the crew: govern on what can
        # be seen and alarm about the rest (never fail INTO drain).
        if pol.windows() and not decided:
            lost = "; ".join(f"{w}: {why}" for w, why in blind) or "no usable reading"
            reading = readings.get(pol.window) or reading
        else:
            lost = ""

        if lost:
            frozen = pol.on_signal_lost == FREEZE
            # DO NOT clear the engaged tier here. Losing sight of the number is
            # not evidence that usage fell, and dropping the hold would silently
            # re-open dispatch at the exact moment we can least justify it. The
            # hold stays on disk; it simply is not APPLIED while blind, because
            # applying a remembered tier to an unmeasured present is the frozen-
            # number failure wearing the other hat.
            return Verdict(
                reading=reading, pct=reading.pct, signal_lost=True, frozen=frozen,
                why=lost,
                alarm=(f"USAGE SIGNAL LOST: {lost}. "
                       + ("on_signal_lost = \"freeze\": no new work is being "
                          "dispatched. " if frozen else
                          "on_signal_lost = \"warn\": THE FLEET IS RUNNING "
                          "UNGOVERNED and will not spin down at any threshold. ")
                       + "Fix the usage probe — this alarms every pass on "
                         "purpose; a governor that goes quiet when it cannot "
                         "see is indistinguishable from one with nothing to "
                         "report."))

        # ---- BURNDOWN (aegis-yegfx) -----------------------------------------
        # Which windows are about to have their budget DESTROYED rather than
        # saved? Computed only from windows in `decided` — i.e. from readings we
        # ACCEPTED — so a blind or stale window can never reach this. That is the
        # inverted fail-safe the Burndown docstring is about: this is the one
        # mechanism that relaxes, so every could-not-tell below resolves toward
        # KEEPING the tier, which is the exact opposite of the rule the rest of
        # this method follows.
        burning: list[Burning] = []
        for w, (wchosen, _, _, wpct) in decided.items():
            burn = pol.burndown_for(w)
            if burn is None:
                continue
            at = resets.get(w)
            if at is None:
                # No published reset. We cannot know the budget is about to be
                # destroyed, so we must assume it is not — a missing input may
                # cost promptness, never the guard.
                continue
            left = at - now
            # `left <= 0` is a reset the producer has not re-read past yet, NOT a
            # fresh budget. Treating it as one would open the taps on the reading
            # least likely to be current.
            if not 0 < left <= burn.within:
                continue
            ceiling = pol.burn_ceiling(w, burn)
            if wpct >= ceiling:
                continue
            suspended = tuple(t for t in pol.engaged(wchosen) if not t.drains)
            if not suspended:
                # Nothing to stand down. Recording a burndown here would print an
                # alarming line about a fleet that was never restricted.
                continue
            burning.append(Burning(window=w, pct=wpct, ceiling=ceiling,
                                   resets_in=left, suspended=suspended))
        burning_windows = {b.window for b in burning}

        # THE UNION. Verdict derives drains/floor/trait_tiers from `engaged`, so
        # simply concatenating each window's own cumulative set gives "the most
        # restrictive across all windows" with no comparison between budgets —
        # which is right, because two windows' percentages are not comparable.
        engaged_tiers: list[Tier] = []
        for w, (wchosen, _, _, _) in decided.items():
            wtiers = pol.engaged(wchosen)
            if w in burning_windows:
                # DRAINS SURVIVE A BURNDOWN, always. The drain is what bounds the
                # whole mechanism: it is where `burn_ceiling` is derived from, so
                # letting burndown suspend it would remove the bound and the
                # thing the bound is computed from in one step.
                wtiers = tuple(t for t in wtiers if t.drains)
            engaged_tiers.extend(wtiers)

        # A representative tier, for MESSAGING and `held` only — every actual
        # restriction is read off `engaged`. A drain outranks everything (it is
        # the loudest thing to name); otherwise the highest threshold, which is
        # what the single-window governor reported and what the tests below
        # pin. Comparing `at` across windows is meaningless in general, so this
        # is deliberately a display choice and nothing reads policy from it.
        def _restriction(t: Tier):
            return (t.drains, t.at)
        chosen = max(engaged_tiers, key=_restriction) if engaged_tiers else None
        held = any(h for _, (c, h, _, _) in decided.items() if c is chosen)

        if persist and self.state is not None:
            self.state.set(Engaged(
                # the FIVE_HOUR mirror, so a downgrade still reads a hold
                at=(decided.get(FIVE_HOUR) or (None,))[0].at
                   if decided.get(FIVE_HOUR) and decided[FIVE_HOUR][0] else None,
                since=(decided.get(FIVE_HOUR) or (None, None, 0.0))[2]
                      if decided.get(FIVE_HOUR) else 0.0,
                # `reset` rides along so the NEXT pass can tell a budget that
                # rolled over from one that merely fell (aegis-9mehy). Written
                # for every decided window, tier or no tier — a reset observed
                # while wide open is exactly what makes the first throttled pass
                # after it able to say "refreshed".
                by_window={w: {"at": None if c is None else c.at, "since": s,
                               "reset": resets.get(w)}
                           for w, (c, _, s, _) in decided.items()}))

        # EVERY refusal names its WINDOW (aegis-59hao). With two budgets, "usage
        # 72%" without saying which one is worse than useless — it sends the
        # reader to the wrong number and the wrong recovery time.
        why = "; ".join(
            f"{w} {p:.0f}%"
            + (f" (holding the {c.at}% tier; left below {c.at - pol.relax_margin}%)"
               if h else "")
            + (" (BURNDOWN: tiers stood down, budget refills before it could be "
               "spent)" if w in burning_windows else "")
            for w, (c, h, _, p) in decided.items()) or "no window readable"

        alarm = ""
        if blind:
            # HALF-BLIND IS STILL BLIND, and it is still not a reason to stop the
            # crew. Govern on what we can see; say loudly what we cannot.
            alarm = ("USAGE SIGNAL PARTIALLY LOST: "
                     + "; ".join(f"{w}: {why_lost}" for w, why_lost in blind)
                     + f". Governing on {', '.join(sorted(decided))} ONLY — the "
                       f"unreadable budget could be further gone than the one "
                       f"being read, and this is the direction that overspends. "
                       f"No drain is taken from a window we cannot see.")

        pct = decided[pol.window][3] if pol.window in decided else (
            max((p for _, _, _, p in decided.values()), default=None))
        # Carry EVERY window's reading, so a message about a tier can quote the
        # budget that tier was actually judged against (aegis-cjjdx). `pct` above
        # stays exactly as it was — it is the default window's number and several
        # callers and tests depend on that — but it is no longer the only one
        # available, which is what forced the mismatched messages.
        by_window = {w: p for w, (_, _, _, p) in decided.items()}
        return Verdict(reading=reading, pct=pct, tier=chosen, held=held, why=why,
                       by_window=by_window,
                       alarm=alarm, engaged=tuple(engaged_tiers),
                       resets=resets, relaxed=tuple(relaxed),
                       burning=tuple(burning),
                       exempt=pol.exempt)

    def episode(self) -> float:
        """The current tier's episode key — what a drain dedupes against."""
        return self.state.get().since if self.state is not None else 0.0


def wake_plan(verdict: Verdict, now: float, *, skew: float = WAKE_SKEW_S,
              max_ahead: float = MAX_WAKE_AHEAD_S) -> dict[str, int]:
    """{window: seconds from now} — when to WAKE AND RE-READ. Pure, so the
    scheduling decision is testable without systemd, a clock or a fleet.

    ONLY WINDOWS WITH A TIER ENGAGED. A wide-open window has nothing to
    re-engage, so a wake for it would be a tend pass bought for nothing — and an
    idle timer that fires forever is how a mechanism stops being read as
    meaningful.

    ONLY WINDOWS WE CAN SEE. `verdict.resets` already excludes blind windows,
    and a signal-lost verdict arms nothing at all: waking to re-read a budget we
    could not read this pass adds no information, and the loud SIGNAL LOST alarm
    is already the right response.

    A RESET ALREADY IN THE PAST ARMS NOTHING, because THIS pass is the post-reset
    read. Arming for a moment that has gone would fire immediately and forever.

    AND AN ABSURD TIMESTAMP IS REFUSED rather than trusted. Everything this
    function declines to schedule degrades to tend's five-minute pass, never to a
    stuck fleet — the timer is for promptness and is allowed to be missing.
    """
    plan: dict[str, int] = {}
    if verdict.signal_lost:
        return plan
    for window in {t.window for t in verdict.engaged}:
        reset_at = verdict.resets.get(window)
        if reset_at is None:
            continue
        delay = int(reset_at + skew - now)
        if delay <= 0 or delay > max_ahead:
            continue
        plan[window] = delay
    return plan


# --- the drain protocol -------------------------------------------------------

@dataclass(frozen=True)
class Drained:
    """One agent's outcome in a drain. `pushed` is what the AGENT reported, never
    what we assumed: a message we sent is not a push, and this whole record
    exists because a fleet stopped with unpushed work has thrown away the tokens
    that produced it."""
    agent: str
    told_at: float
    state: str                    # drained | pending | failed
    pushed: str = ""              # what the agent said went up
    why: str = ""


DRAINED, PENDING, FAILED = "drained", "pending", "failed"

# The marker an agent puts in `st stop --reason` so the report can read its
# outcome. A protocol needs one word both ends agree on, and free text does not
# make one — the drain message below tells the agent exactly what to write.
DRAIN_OK = "drained:"
DRAIN_FAIL = "drain-failed:"


class DrainLedger:
    """Who was told, in which episode. One file per agent, beside untracked/.

    DEDUPED BY EPISODE, not by a boolean: a fleet that relaxes below the drain
    tier and climbs back into it must be told AGAIN, because the agents that were
    told the first time are gone. A boolean would silence exactly that.
    """

    def __init__(self, root):
        self.root = Path(root) / "governor" / "drain"

    def _path(self, agent: str) -> Path:
        return self.root / f"{agent}.json"

    def told(self, agent: str) -> tuple[float, float] | None:
        """(episode, told_at) or None."""
        try:
            d = json.loads(self._path(agent).read_text())
            return float(d["episode"]), float(d["at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def tell(self, agent: str, episode: float, at: float, tier: int) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            from .files import write_json_atomic
            write_json_atomic(self._path(agent), {"episode": episode, "at": at,
                                                  "tier": tier})
        except OSError:
            pass

    def agents(self) -> list[str]:
        try:
            return sorted(p.stem for p in self.root.glob("*.json"))
        except OSError:
            return []

    def clear(self) -> None:
        """The tier relaxed. Forget the episode — the next one starts clean."""
        for p in self.root.glob("*.json") if self.root.is_dir() else []:
            try:
                p.unlink()
            except OSError:
                pass


# The instruction an agent receives. It MUST fit an inbox message (the durable
# channel caps a body at 493 chars — inbox.py maps it to a tracker item titled
# `inbox: <body>` under bd's 500-char title limit), so it is a pointer, not a
# document: the exact commands, in order, and nothing else. Tested for length.
def drain_message(agent: str, tier: int, pct: float | None) -> str:
    seen = f"{pct:.0f}%" if pct is not None else "signal high"
    return (
        f"DRAIN ({tier}% usage tier, at {seen}): stop taking new work. "
        f"1) commit WIP in your OWN worktree/branch; "
        f"2) git push (rejected? rebase on origin/main, retry, NEVER force); "
        f"3) st stop {agent} --reason '{DRAIN_OK} <repo>@<sha> ...' — that reason "
        f"IS the report, and unreported counts as NOT drained. "
        f"Cannot push? st stop {agent} --reason '{DRAIN_FAIL} <why>' and escalate "
        f"— never die silently on unpushed work."
    )


class Drainer:
    """Broadcast the drain, then REPORT it from the agents' own stop records.

    Every dependency injected for the usual reason: this must be provable without
    a tmux, a bd, or a five-minute wait. `deliver` is the durable send (it must
    survive the recipient's session dying, which is the entire point at 95%);
    `stops` is the FilesStops the agents write when they stop themselves.
    """

    def __init__(self, root, deliver, stops, *, log=None, now=time.time):
        self.ledger = DrainLedger(root)
        self._deliver = deliver              # (agent_name, body) -> str (msg id)
        self._stops = stops
        self._log = log or (lambda msg: None)
        self._now = now

    def sweep(self, agents, verdict: Verdict, episode: float,
              *, live=None, catalog=None) -> list[Drained]:
        """Tell everyone the tier excludes, once per episode, then report.

        `live(agent) -> bool` decides who is worth telling. A DOWN agent is not
        told: it has no session to push from, and a durable message it will read
        on some future relaunch would instruct it to stop the moment it comes
        back — a self-perpetuating shutdown nobody asked for.
        """
        if not verdict.tier or verdict.signal_lost:
            self.ledger.clear()
            return []
        excluded = [a for a in agents if verdict.excludes(a, catalog)]
        if not excluded:
            return []
        # DRAIN FROM THE TAIL — lowest survival rank first, so the last agents
        # standing are the ones that can still coordinate a drain. Same key the
        # bring-up order reads (traits.survival_key), reversed, because bring-up
        # takes from the head and spin-down takes from the tail: one comparator,
        # so the two directions cannot disagree about who matters. Getting this
        # backwards asks the administrator to stop first and leaves nobody to
        # supervise the shutdown.
        from .traits import survival_key
        for a in sorted(excluded, key=survival_key(catalog), reverse=True):
            if live is not None and not live(a):
                continue
            prior = self.ledger.told(a.name)
            if prior is not None and prior[0] == episode:
                continue                     # already told, this episode
            body = drain_message(a.name, verdict.tier.at, verdict.pct)
            try:
                self._deliver(a.name, body)
            except Exception as e:           # noqa: BLE001 — one failure is not the sweep
                # LOUD, and NOT recorded as told: an undelivered drain must be
                # retried next pass, and recording it would make the report say
                # "pending" forever about a message that never left.
                self._log(f"⚠ drain: could NOT reach {a.name} "
                          f"({type(e).__name__}: {str(e)[:80]}) — retrying next pass")
                continue
            self.ledger.tell(a.name, episode, self._now(), verdict.tier.at)
            self._log(f"DRAIN {a.name}: told to push and stop "
                      f"({verdict.tier.at}% tier)")
        return self.report(episode)

    def report(self, episode: float) -> list[Drained]:
        """Who drained, what went up, who failed. Read from the STOP RECORDS.

        UNREPORTED IS NOT DRAINED. A told agent with no stop record is `pending`,
        never a silent success — the failure this tier exists to prevent is work
        that only ever existed on one box, and a report that assumed compliance
        would hide exactly that.
        """
        out: list[Drained] = []
        for name in self.ledger.agents():
            told = self.ledger.told(name)
            if told is None or told[0] != episode:
                continue
            stop = self._stops.get(name) if self._stops is not None else None
            if stop is None or stop.at < told[1]:
                # No stop, or a stop from BEFORE we asked — the older record
                # describes a different decision and must not be read as
                # compliance with this one.
                out.append(Drained(name, told[1], PENDING,
                                   why="told, and has not reported a stop yet"))
                continue
            reason = (stop.reason or "").strip()
            if reason.startswith(DRAIN_FAIL):
                out.append(Drained(name, told[1], FAILED,
                                   why=reason[len(DRAIN_FAIL):].strip()
                                   or "reported a failure with no reason"))
            elif reason.startswith(DRAIN_OK):
                out.append(Drained(name, told[1], DRAINED,
                                   pushed=reason[len(DRAIN_OK):].strip()))
            else:
                # It stopped, but did not say what it pushed. That is NOT drained:
                # the report's whole content is the SHAs, and a stop with no
                # report is indistinguishable from an agent that quit.
                out.append(Drained(name, told[1], PENDING,
                                   why=f"stopped without a {DRAIN_OK} report "
                                       f"(reason: {reason or 'none'}) — a stop is "
                                       f"not a push"))
        return out


def render_drain(rows: list[Drained]) -> str:
    if not rows:
        return ""
    by = {DRAINED: [], PENDING: [], FAILED: []}
    for r in rows:
        by.setdefault(r.state, []).append(r)
    lines = ["", "  DRAIN REPORT"]
    for r in sorted(rows, key=lambda x: (x.state, x.agent)):
        detail = r.pushed or r.why
        lines.append(f"    {r.state:<8} {r.agent:<12} {detail}")
    lines.append(f"    {len(by[DRAINED])} drained · {len(by[PENDING])} pending · "
                 f"{len(by[FAILED])} FAILED "
                 f"({'all reported' if not by[PENDING] and not by[FAILED] else 'UNREPORTED IS NOT DRAINED'})")
    return "\n".join(lines)


# --- parsing (called by config.py, which owns the file and the error text) ----

_GOV_KEYS = {"source", "window", "on_signal_lost", "relax_margin",
             "max_age_seconds", "url", "path", "username", "password_file",
             "stub_pct", "tier", "exempt", "burndown"}
_TIER_KEYS = {"at", "min_priority", "traits", "action", "window"}
_BURN_KEYS = {"window", "within", "reserve"}


def parse(tbl: dict) -> Policy:
    """[governor] -> Policy. Raises GovernorError; config.py adds the file name.

    UNKNOWN KEYS ARE REFUSED, like every other table in shantytown.toml except
    [env]. A dropped `relax_margin` would leave an operator believing hysteresis
    is configured while the fleet flips tiers every pass, with nothing anywhere
    saying so.
    """
    if not tbl:
        return Policy()
    unknown = sorted(set(tbl) - _GOV_KEYS)
    if unknown:
        raise GovernorError(f"unknown key(s) in [governor]: {', '.join(unknown)}; "
                            f"expected one of: {', '.join(sorted(_GOV_KEYS))}")

    source = tbl.get("source", Policy.source)
    if source not in SOURCES:
        raise GovernorError(f"[governor] source = {source!r} is not one of: "
                            f"{', '.join(SOURCES)}")
    on_lost = tbl.get("on_signal_lost", Policy.on_signal_lost)
    if on_lost not in ON_SIGNAL_LOST:
        raise GovernorError(
            f"[governor] on_signal_lost = {on_lost!r} is not one of: "
            f"{', '.join(ON_SIGNAL_LOST)}. \"warn\" runs the fleet ungoverned and "
            f"alarms; \"freeze\" dispatches nothing new. Neither one drains — a "
            f"probe bug must never be able to stop the crew.")
    window = tbl.get("window", Policy.window)
    if not isinstance(window, str) or not window.strip():
        raise GovernorError(f"[governor] window must be a non-empty string "
                            f"(which {USAGE_METRIC} window governs), got {window!r}")

    margin = _int(tbl, "relax_margin", Policy.relax_margin, minimum=0)
    max_age = _int(tbl, "max_age_seconds", Policy.max_age_seconds, minimum=1)

    for key in ("url", "path", "username", "password_file"):
        v = tbl.get(key)
        if v is not None and not isinstance(v, str):
            raise GovernorError(f"[governor] {key} must be a string, got "
                                f"{type(v).__name__}")
    stub = tbl.get("stub_pct")
    if stub is not None:
        if isinstance(stub, bool) or not isinstance(stub, (int, float)):
            raise GovernorError(f"[governor] stub_pct must be a number 0-100, got "
                                f"{stub!r}")
        stub = float(stub)

    raw_exempt = tbl.get("exempt", [])
    if isinstance(raw_exempt, str):
        # ONE NAME IS NOT A LIST OF CHARACTERS. `exempt = "ellie"` is the typo an
        # operator writes first, and iterating it silently exempts nobody while
        # every letter looks like an agent name in a dump. Refuse and say the fix.
        raise GovernorError(
            f"[governor] exempt must be an ARRAY of agent names, got the string "
            f'{raw_exempt!r}. Write `exempt = ["{raw_exempt}"]`.')
    if not isinstance(raw_exempt, list):
        raise GovernorError(f"[governor] exempt must be an array of agent names, "
                            f"got {type(raw_exempt).__name__}")
    exempt = []
    for name in raw_exempt:
        if not isinstance(name, str) or not name.strip():
            raise GovernorError(f"[governor] exempt entries must be non-empty "
                                f"agent names, got {name!r}")
        exempt.append(name.strip())
    dupes = sorted({n for n in exempt if exempt.count(n) > 1})
    if dupes:
        raise GovernorError(f"[governor] exempt names {', '.join(dupes)} twice; "
                            f"a repeated name means one of them is a typo nobody "
                            f"will notice.")

    raw_tiers = tbl.get("tier", [])
    if isinstance(raw_tiers, dict):
        raw_tiers = [raw_tiers]
    if not isinstance(raw_tiers, list):
        raise GovernorError("[[governor.tier]] must be an array of tables")
    tiers = tuple(sorted((_tier(t) for t in raw_tiers), key=lambda t: t.at))
    seen = set()
    for t in tiers:
        # KEYED ON (window, at), NOT ON `at` ALONE. Every tier has been
        # window-qualified since aegis-59hao and this check was not, so it
        # rejected the one configuration the operator is most likely to want:
        # the SAME ladder on both budgets. Stiwi's spoken intervals are
        # 50/70/80/95, and writing them for both windows — which is exactly what
        # this file's own comment claims it does — raised "declares `at = 50`
        # twice" and refused to load. That made aegis-yegfx finding 1's
        # recommendation, "restore the seven_day rungs to the spoken numbers",
        # literally unconfigurable, and the workaround (offsetting the weekly
        # ladder by 5 points) is the undocumented margin that finding is about.
        # Two tiers at one threshold in DIFFERENT windows are not ambiguous:
        # they are judged against different readings and both can engage.
        if (t.window, t.at) in seen:
            raise GovernorError(
                f"[[governor.tier]] declares `at = {t.at}` twice for window "
                f"{t.window!r}. Two tiers at one threshold in the same window "
                f"cannot both engage, and which one wins would be a silent "
                f"choice st made for you. (The same `at` in a DIFFERENT window "
                f"is fine — they are judged against different budgets.)")
        seen.add((t.window, t.at))

    raw_burn = tbl.get("burndown", [])
    if isinstance(raw_burn, dict):
        raw_burn = [raw_burn]
    if not isinstance(raw_burn, list):
        raise GovernorError("[[governor.burndown]] must be an array of tables")
    burndowns = tuple(_burndown(b) for b in raw_burn)
    bseen = set()
    for b in burndowns:
        if b.window in bseen:
            raise GovernorError(
                f"[[governor.burndown]] declares window {b.window!r} twice; "
                f"which `within` applied would be a silent choice st made for "
                f"you.")
        bseen.add(b.window)
    # A burndown for a window with NO tiers governs nothing and is almost
    # certainly a typo in the window name — the failure it produces otherwise is
    # silence, which for this feature reads as "the budget was protected" when
    # the truth is "nothing was ever configured".
    windows_with_tiers = {t.window for t in tiers}
    for b in burndowns:
        if b.window not in windows_with_tiers:
            raise GovernorError(
                f"[[governor.burndown]] window = {b.window!r} has no "
                f"[[governor.tier]] rows, so it can never stand anything down. "
                f"Configured windows: "
                f"{', '.join(sorted(windows_with_tiers)) or 'none'}.")

    return Policy(source=source, window=window.strip(), on_signal_lost=on_lost,
                  relax_margin=margin, max_age_seconds=max_age,
                  url=tbl.get("url"), path=tbl.get("path"),
                  username=tbl.get("username"),
                  password_file=tbl.get("password_file"), stub_pct=stub,
                  tiers=tiers, exempt=tuple(exempt), burndowns=burndowns)


def _burndown(spec) -> Burndown:
    if not isinstance(spec, dict):
        raise GovernorError(f"[[governor.burndown]] must be a table, got "
                            f"{type(spec).__name__}")
    unknown = sorted(set(spec) - _BURN_KEYS)
    if unknown:
        raise GovernorError(
            f"unknown key(s) in [[governor.burndown]]: {', '.join(unknown)}; "
            f"expected one of: {', '.join(sorted(_BURN_KEYS))}")
    window = spec.get("window")
    if not isinstance(window, str) or not window.strip():
        raise GovernorError("[[governor.burndown]] needs `window` — WHICH budget "
                            "stands down near its reset")
    if "within" not in spec:
        raise GovernorError(
            f"[[governor.burndown]] window = {window!r} needs `within` — how "
            f"many SECONDS before the reset the tiers stand down. There is no "
            f"default on purpose: the right horizon is how long it takes this "
            f"fleet to actually spend the headroom, which st cannot know.")
    within = _int(spec, "within", 0, minimum=1)
    reserve = _int(spec, "reserve", Burndown.reserve, minimum=0)
    if reserve >= 100:
        raise GovernorError(
            f"[[governor.burndown]] reserve = {reserve} leaves no headroom at "
            f"any reading, so the burndown could never arm — which is a silent "
            f"way to disable it. Delete the row instead.")
    return Burndown(window=window.strip(), within=within, reserve=reserve)


def _int(tbl: dict, key: str, default: int, *, minimum: int) -> int:
    v = tbl.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise GovernorError(f"[governor] {key} must be an integer >= {minimum}, "
                            f"got {v!r}")
    return v


def _tier(spec) -> Tier:
    if not isinstance(spec, dict):
        raise GovernorError(f"[[governor.tier]] must be a table, got "
                            f"{type(spec).__name__}")
    unknown = sorted(set(spec) - _TIER_KEYS)
    if unknown:
        raise GovernorError(
            f"unknown key(s) in [[governor.tier]]: {', '.join(unknown)}; expected "
            f"one of: {', '.join(sorted(_TIER_KEYS))}")
    at = spec.get("at")
    if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at <= 100:
        raise GovernorError(f"[[governor.tier]] `at` must be an integer percent "
                            f"0-100, got {at!r}")
    prio = spec.get("min_priority")
    if prio is not None and (isinstance(prio, bool) or not isinstance(prio, int)
                             or prio < 0):
        raise GovernorError(
            f"[[governor.tier]] at = {at}: min_priority must be a non-negative "
            f"integer in your tracker's numbering, where 0 is the HIGHEST "
            f"priority — `min_priority = 1` admits P0 and P1. Got {prio!r}")
    traits = spec.get("traits", [])
    if isinstance(traits, str):
        traits = [traits]
    if not isinstance(traits, list) or not all(isinstance(t, str) and t.strip()
                                               for t in traits):
        raise GovernorError(f"[[governor.tier]] at = {at}: traits must be a list "
                            f"of non-empty strings, got {traits!r}")
    action = spec.get("action")
    if action is not None and action not in ACTIONS:
        raise GovernorError(f"[[governor.tier]] at = {at}: action = {action!r} is "
                            f"not one of: {', '.join(ACTIONS)}")
    # WHICH BUDGET this tier's `at` is read against (aegis-59hao). Defaulting to
    # five_hour is what makes every pre-existing config keep its exact meaning.
    window = spec.get("window", FIVE_HOUR)
    if not isinstance(window, str) or window not in (FIVE_HOUR, SEVEN_DAY):
        raise GovernorError(
            f"[[governor.tier]] at = {at}: window = {window!r} is not one of: "
            f"{FIVE_HOUR}, {SEVEN_DAY}. These are the producer's own labels — a "
            f"tier on a window nothing publishes would never engage, and a "
            f"threshold that can never fire reads exactly like one that is "
            f"simply never reached.")
    if prio is None and not traits and action is None:
        raise GovernorError(
            f"[[governor.tier]] at = {at} restricts NOTHING — no min_priority, no "
            f"traits, no action. A tier that engages and changes no behaviour "
            f"would read as a policy in force while the fleet spends exactly as "
            f"it did before.")
    return Tier(at=at, min_priority=prio,
                traits=tuple(t.strip() for t in traits), action=action,
                window=window)
