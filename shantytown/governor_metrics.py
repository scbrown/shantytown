"""governor_metrics — every governor DECISION and its INPUTS, as Prometheus series.

Stiwi, 2026-09-01 (aegis-ycqgyx), verbatim: *"I want to ensure that we have
visibility and instrumentation for this balance that the governor is giving
whenever a governor makes a decision or uses some type of inputs we should be
tracking that in our Prometheus export we should also be able to tell the balance
and the number of Agents either codex or Claude through our Prometheus metrics"*.

THE GAP THIS CLOSES.  The governor already decides well and explains itself in
prose — `Pacing.render`, `Utilization.render`, `Relaxed.render` are careful,
falsifiable sentences.  Every one of them went to tend's stderr and nowhere else.
Measured at directive time: `{__name__=~"(st|governor|shanty|creel).*"}` returned
creel's build/doctor/lease series and NOTHING from the governor, so a night's
worth of decisions — a cap-9 hold, a -1 setpoint, a 1.02x hold, an unrated
window — existed only in a journal nobody graphs and nothing alerts on.

── WHAT IS EXPORTED, AND WHY IT IS THE INPUTS AND NOT JUST THE VERDICT ────────

A ratio on its own is uncheckable.  That is not a style preference here, it is
the constraint `Pacing` states in its own docstring and the reason the 2026-08-02
incident took ~54h to see: `0.90x` alone cannot be told apart from an arithmetic
bug, while `52%used/58%elapsed =0.90x` reads its own arithmetic off the screen.
So every ratio is exported beside the two numbers it came from, and a dashboard
can re-derive it.

── ABSENCE MUST NOT READ AS HEALTHY ───────────────────────────────────────────

Two rules, both paid for elsewhere in this repo:

*   An UNRATED window exports `st_governor_window_rated{...} = 0` rather than
    simply omitting its ratio.  A window that could not be rated must not render
    as one that was rated and found fine (`WindowUse.render`, same argument).
*   CANNOT TELL is not HOLD.  When `Utilization.advice` is None the
    recommendation gauge is OMITTED and `st_governor_recommendation_known` is 0.
    Exporting 0 there would make an unreadable tracker and a deliberate hold
    identical on every dashboard — the exact collapse `Utilization` refuses.

── TWO GROUPS, FOR THE SAME REASON CREEL'S PRODUCER HAS TWO (aegis-4zpae5) ────

A gap in a pushed series has two causes — the producer died, or it ran and had
nothing to say — and at the gateway they look identical.  So:

    job=st_governor_producer   pushed EVERY pass, unconditionally.  Its freshness
                               is tend's own liveness; `st_governor_publish_status`
                               carries what happened to the other group.
    job=st_governor            the decision samples, pushed only when a governor
                               was actually evaluated.

ONE group for ALL lanes, deliberately.  The push is a PUT, which REPLACES a
group, so a lane that stops being governed disappears on the next pass instead of
leaving a frozen corpse for a dashboard to keep drawing.  Per-lane groups would
each need a deletion nobody would remember to issue.

── COUNTERS SURVIVE A PROCESS THAT LIVES FIVE SECONDS ─────────────────────────

`st` is invocation-based: the tend pass is a fresh process every five minutes, so
an in-memory counter would be a gauge that is always 1.  `Ledger` keeps the
monotonic totals on disk beside the hysteresis state, for the same reason
`FilesGovernorState` does.

── THIS MODULE NEVER DECIDES ANYTHING ─────────────────────────────────────────

It renders what the governor already computed.  `render` is a pure total function
of its inputs — no clock, no storage, no I/O — so it replays, and every number it
prints is read off a `Verdict`/`Utilization`/`Reading` rather than recomputed.  A
second opinion about the burn would be exactly the defect `governor_utilization`
opens by refusing.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import governor as gov_mod

#: Env/`[env]` key holding the pushgateway base URL.  Unset -> this module does
#: nothing at all, the same local-first discipline `stats._maybe_export` follows:
#: the exporter is a bonus, never a dependency, and there are no import-time side
#: effects.
PUSHGATEWAY_ENV = "ST_GOVERNOR_PUSHGATEWAY"

#: The basic-auth credential, AS A FILE PATH plus a username — deliberately not
#: baked into the URL as userinfo.
#:
#: The gateway password is rotation-managed and already lives in one file on
#: every host that has one.  Copying it into a second place (a URL in a config,
#: an env var in a unit) silently SPLITS at the next rotation: both copies look
#: fine, one of them stops working, and the failure surfaces as an
#: authentication error from a producer nobody changed.  Reading the file at push
#: time means a rotation is picked up with no edit anywhere.
#:
#: Userinfo in the URL is still honoured, because that is what `stats.py` already
#: does and a deployment may have no credential file — but it is the fallback,
#: not the recipe.
PASSWORD_FILE_ENV = "ST_GOVERNOR_PUSHGATEWAY_PASSWORD_FILE"
USER_ENV = "ST_GOVERNOR_PUSHGATEWAY_USER"

JOB = "st_governor"
PRODUCER_JOB = "st_governor_producer"

#: Bounded, because a heartbeat that outlives its interval is a lock
#: (aegis-do8qz): an unbounded push inside a */5 pass can eat the cadence, and
#: tend is the SOLE respawn path since the crew watchdog was masked (aegis-qwadc).
PUSH_TIMEOUT_S = 5.0

# The publish ladder, mirroring creel's collect_status.  0 and 2 are both
# successful runs of this code; they must never collapse, because one is a
# healthy silence and the other is a broken producer that would otherwise publish
# a reassuring nothing.
OK, PUSH_FAILED, NOTHING_TO_SAY, COULD_NOT_RENDER = 0, 1, 2, 3

#: The closed vocabulary of `Utilization.cause`.  EVERY one is exported, 0 or 1,
#: rather than only the active one: a dashboard querying a cause that happens to
#: be inactive gets `0` instead of an empty vector, so a transition is graphable
#: and "no data" keeps meaning the producer is gone.  A cause NOT in this list is
#: still exported — a new cause must never be silently dropped, which is how a
#: vocabulary drifts away from the code it describes.
CAUSES = ("fill", "hold", "at-cap", "over-pace", "no-ready", "ready-unknown",
          "budget-shrinking", "uncapped", "unratable", "launch-blocked")

#: The reason a lost signal is lost, as PROSE, made safe to carry as a Prometheus
#: LABEL (aegis-tq8um5).
#:
#: WHY PROSE IS EXPORTED HERE AT ALL, against this module's own rule that the
#: prose explaining a verdict is not a series.  The rule is about DASHBOARDS, and
#: it still holds: nothing here graphs a string.  This is for the PAGE.  An alert
#: annotation can only render what is on the alert's own labels, so a responder
#: gets the reader's diagnosis in one read or they do not get it at all — and
#: measured 2026-09-03, not getting it cost two agents an hour reconstructing an
#: upstream 404 the producer had already written down.
#:
#: WHY IT IS NORMALIZED, which is the load-bearing part.  A label that changes
#: value RESTARTS an alert's `for:` timer, so a `why` carrying "the probe last
#: succeeded 1834s ago" — which every pass re-renders with a new number — would
#: give `GovernorSignalLost` a `for: 30m` it could NEVER reach.  That is not a
#: cosmetic regression: it would silently disarm the one decision-side alert the
#: governor has, in the exact fault class (staleness) it exists to catch.  So
#: the VOLATILE numbers collapse to `N`.
#:
#: WHICH NUMBERS, and why this is narrow rather than "all of them".  The first
#: cut here elided every digit run and turned
#:
#:     ...wham/usage failed: 404 Not Found   ->   ...wham/usage failed: N Not Found
#:
#: destroying the single most diagnostic token in the message — the one that says
#: UPSTREAM at a glance — in the name of protecting a timer.  Caught by the test
#: below, which is why it asserts on the 404 and not merely on stability.
#:
#: So exactly two shapes are elided, and both are volatile BY CONSTRUCTION:
#:   * a number introducing a time unit (`1834s ago`, `900s`, `30s`) — every one
#:     of these is an age or a configured limit re-rendered each pass;
#:   * a run of 6+ digits — epochs, pids, byte counts; nothing an operator reads
#:     as a diagnosis and everything that changes between passes.
#: HTTP statuses (3 digits, no unit) survive, which is the whole point.
_re = __import__("re")
_VOLATILE = (
    _re.compile(r"\d+(?=\s*(?:ms|s|m|h)\b)"),   # ages, timeouts, limits
    _re.compile(r"\d{6,}"),                       # epochs, pids
)

#: Bounded, because a label is a series. 180 chars holds every message
#: `Reading.lost` and the readers can produce, with room to be truncated
#: VISIBLY (the ellipsis) rather than silently.
WHY_MAX = 180


def stable_why(why: str) -> str:
    """`Verdict.why` as a label value: digit-runs elided, bounded, one line."""
    text = " ".join((why or "").split())
    for pattern in _VOLATILE:
        text = pattern.sub("N", text)
    return text if len(text) <= WHY_MAX else text[:WHY_MAX - 1] + "\u2026"


#: Directions the decision counter buckets into.  `unknown` is a first-class
#: bucket for the same reason `advice=None` is a first-class answer.
DIRECTIONS = ("grow", "hold", "shrink", "unknown")


def _esc(value: str) -> str:
    """Escape a Prometheus label value.  Lane/window/harness names are plain
    identifiers today; escaping anyway keeps a future role name with a quote in
    it from producing exposition the gateway rejects as a whole."""
    return (str(value).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n"))


def _labels(**kw) -> str:
    inner = ",".join(f'{k}="{_esc(v)}"' for k, v in kw.items() if v is not None)
    return "{" + inner + "}" if inner else ""


class _Out:
    """Exposition accumulator that emits each `# TYPE` line exactly ONCE.

    Not a nicety: a repeated TYPE for one metric family makes the pushgateway
    reject the whole body with 400, so a second lane would silently take out the
    first lane's samples too.  The same class of defect as a .prom textfile that
    is not metric-major — one malformed family kills everything around it.
    """

    def __init__(self) -> None:
        self._typed: dict[str, str] = {}
        self._lines: dict[str, list[str]] = {}
        self._order: list[str] = []

    def add(self, name: str, value, *, kind: str = "gauge", **labels) -> None:
        if value is None:
            return
        if name not in self._lines:
            self._typed[name] = kind
            self._lines[name] = []
            self._order.append(name)
        self._lines[name].append(f"{name}{_labels(**labels)} {_num(value)}")

    def render(self) -> str:
        out: list[str] = []
        for name in self._order:
            out.append(f"# TYPE {name} {self._typed[name]}")
            out.extend(self._lines[name])
        return "\n".join(out) + ("\n" if out else "")


def _num(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        # NaN/Inf are valid exposition but they read as a measurement. Nothing
        # here should ever produce one; if something does, say so loudly rather
        # than publishing a number-shaped non-number.
        return "NaN"
    return repr(int(value)) if value.is_integer() else repr(value)


# --- the on-disk counters --------------------------------------------------


@dataclass(frozen=True)
class Totals:
    """Monotonic counts carried across passes.  Plain data so `render` stays pure."""

    decisions: dict           # {(lane, direction): int}
    relaxations: dict         # {(lane, window): int}
    burndowns: dict           # {(lane, window): int}


EMPTY = Totals(decisions={}, relaxations={}, burndowns={})


class Ledger:
    """The counters, on disk beside the governor's hysteresis state.

    ON DISK for the reason `FilesGovernorState` gives about the engaged tier: a
    tend pass is a fresh process every five minutes, so a counter that lives in
    one is not a counter.  `st_governor_decisions_total` has to be able to answer
    "how often did the governor recommend growth this week", which is a question
    about passes this process was not present for.

    Unreadable state reads as ZERO COUNTS, never as an invented history, and a
    write failure is swallowed: telemetry may not take down a supervision pass.
    A counter that resets to 0 is visible in Prometheus as a reset and is handled
    by `rate()`/`increase()`; a crashed tend pass is not.
    """

    def __init__(self, root) -> None:
        self.path = Path(root) / "governor" / "metrics-counters.json"

    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _pairs(section: dict) -> dict:
        out = {}
        for lane, inner in (section or {}).items():
            if not isinstance(inner, dict):
                continue
            for key, n in inner.items():
                try:
                    out[(str(lane), str(key))] = int(n)
                except (TypeError, ValueError):
                    continue
        return out

    def totals(self) -> Totals:
        d = self._read()
        return Totals(decisions=self._pairs(d.get("decisions")),
                      relaxations=self._pairs(d.get("relaxations")),
                      burndowns=self._pairs(d.get("burndowns")))

    def bump(self, *, decisions=(), relaxations=(), burndowns=()) -> Totals:
        """Apply this pass's events and return the NEW totals.

        Returns them rather than making the caller re-read, so the numbers that
        are published are exactly the numbers that were stored — a re-read could
        pick up a concurrent pass's write and publish a total this pass never
        computed.
        """
        d = self._read()
        for name, events in (("decisions", decisions), ("relaxations", relaxations),
                             ("burndowns", burndowns)):
            section = d.get(name)
            if not isinstance(section, dict):
                section = {}
            for lane, key in events:
                inner = section.get(lane)
                if not isinstance(inner, dict):
                    inner = {}
                try:
                    inner[key] = int(inner.get(key, 0)) + 1
                except (TypeError, ValueError):
                    inner[key] = 1
                section[lane] = inner
            d[name] = section
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
            os.replace(tmp, self.path)
        except OSError:
            pass                       # telemetry never breaks supervision
        return Totals(decisions=self._pairs(d.get("decisions")),
                      relaxations=self._pairs(d.get("relaxations")),
                      burndowns=self._pairs(d.get("burndowns")))


def events_of(lane: str, verdict, utilization) -> dict:
    """The countable events this pass produced for one lane.

    Split out of `render` so the counter and the gauges cannot disagree about
    what happened: the same classification feeds both.
    """
    advice = None if utilization is None else utilization.advice
    if advice is None:
        direction = "unknown"
    elif advice > 0:
        direction = "grow"
    elif advice < 0:
        direction = "shrink"
    else:
        direction = "hold"
    relaxed = [(lane, r.window) for r in getattr(verdict, "relaxed", ()) or ()]
    burning = [(lane, b.window) for b in getattr(verdict, "burning", ()) or ()]
    return {"decisions": [(lane, direction)],
            "relaxations": relaxed, "burndowns": burning}


# --- rendering -------------------------------------------------------------


def _window_rows(out: _Out, lane: str, utilization, readings, now: float) -> None:
    """One row set per window, INPUTS FIRST.

    Every window the governor could see appears, including one it could not
    rate — with `window_rated=0` and its raw percentage still exported, because
    "we read 57% and could not compute a pace ratio" is a different and much more
    actionable fact than silence.
    """
    seen = set()
    for use in getattr(utilization, "windows", ()) or ():
        seen.add(use.window)
        lb = dict(lane=lane, window=use.window)
        out.add("st_governor_used_percent", use.pct, **lb)
        out.add("st_governor_elapsed_percent", use.elapsed_pct, **lb)
        out.add("st_governor_utilization_ratio", use.ratio, **lb)
        out.add("st_governor_pace_bound", use.bound, **lb)
        out.add("st_governor_pace_bound_declared", use.bound_declared, **lb)
        # THE BALANCE Stiwi asked for: points still spendable before this
        # window's own drain ceiling stops the fleet.  Read off `WindowUse`,
        # which derives the ceiling from the drain tier rather than from a second
        # configured number that could drift away from it.
        out.add("st_governor_budget_points_remaining", use.points, **lb)
        out.add("st_governor_burn_ceiling_percent", use.ceiling, **lb)
        out.add("st_governor_window_rated", use.ratio is not None, **lb)
        if use.resets_in is not None:
            out.add("st_governor_window_reset_timestamp_seconds",
                    now + use.resets_in, **lb)
            out.add("st_governor_window_resets_in_seconds", use.resets_in, **lb)
    # A window with a reading that never reached `Utilization` at all — an
    # unreadable one, or a lane whose utilization was skipped because the signal
    # was lost.  It is exported as UNRATED rather than left out: the whole point
    # of the rated flag is that a missing window must not read as a healthy one.
    for name, reading in sorted((readings or {}).items()):
        if name in seen or reading is None:
            continue
        lb = dict(lane=lane, window=name)
        out.add("st_governor_used_percent", getattr(reading, "pct", None), **lb)
        out.add("st_governor_window_rated", False, **lb)
        out.add("st_governor_reading_ok", bool(getattr(reading, "ok", False)), **lb)
        reset_at = getattr(reading, "reset_at", None)
        if reset_at is not None:
            out.add("st_governor_window_reset_timestamp_seconds", reset_at, **lb)
            out.add("st_governor_window_resets_in_seconds", reset_at - now, **lb)


def _lane_rows(out: _Out, lane: str, *, verdict, utilization, readings,
               live: int | None, blocked: int, setpoint_delta, now: float,
               totals: Totals, known_windows=()) -> None:
    lb = dict(lane=lane)
    if verdict is not None:
        out.add("st_governor_signal_lost", bool(verdict.signal_lost), **lb)
        # WHOSE FAULT, in the closed-vocabulary style `st_governor_cause` uses:
        # EVERY value 0 or 1, never only the active one, so a dashboard asking
        # about a fault class that happens to be inactive gets `0` rather than an
        # empty vector — and "no data" keeps its single meaning, that the
        # producer is gone.  `st_governor_signal_lost` itself is left EXACTLY as
        # it was, no new labels: it is the series an existing alert and dashboard
        # already select on, and widening its label set would change what
        # `max by (lane)` sums over at the moment the fleet is already blind.
        fault = getattr(verdict, "fault", "") or ""
        for name in gov_mod.FAULTS:
            out.add("st_governor_signal_lost_fault",
                    bool(verdict.signal_lost) and name == fault,
                    lane=lane, fault=name)
        # The prose, for the PAGE.  Exactly one series per lane, which is what
        # lets an alert `group_left` it without risking a many-to-one match.
        #
        # EMITTED ON EVERY PASS, healthy or not — `fault="none"`, `why=""`, value
        # 0 — and the first cut of this emitted it only while the signal was
        # lost.  That was wrong twice over, and the second reason is the one that
        # bites:
        #
        #   * it contradicts this module's own rule, three commits old here (`a
        #     counter that appears only on failure is a panel that reads green`);
        #   * goldblum's check-alert-metrics REFUSES an alert whose metric has no
        #     series, because a rule referencing a metric nobody publishes cannot
        #     fire and is indistinguishable from a healthy one.  A fault-only
        #     series has no series in the steady state BY DESIGN, so the alerts
        #     that join it would have been permanently unverifiable.
        #
        # The join stays correct because the LEFT side already filters to lanes
        # where `st_governor_signal_lost == 1`; a healthy lane's row is never
        # reached.
        out.add("st_governor_signal_lost_info", bool(verdict.signal_lost),
                lane=lane,
                fault=(fault or gov_mod.UNKNOWN_FAULT) if verdict.signal_lost
                      else "none",
                why=stable_why(verdict.why) if verdict.signal_lost else "")
        out.add("st_governor_frozen", bool(verdict.frozen), **lb)
        out.add("st_governor_drains", bool(verdict.drains), **lb)
        out.add("st_governor_engaged_tiers", len(verdict.engaged or ()), **lb)
        out.add("st_governor_cap", verdict.max_agents, **lb)
        # The floor is OMITTED when no tier declares one.  Not -1 and not 0: 0 is
        # a real floor (P0 only) and is the strictest one there is, so a sentinel
        # in this series would render "no restriction" as "the tightest possible
        # restriction" on every dashboard that reads it.
        #
        # ...and therefore it needs the companion beside it, for the same reason
        # `recommendation` has `recommendation_known`: a family that is absent in
        # the HEALTHY case leaves a panel reading "No data", which on a capacity
        # dashboard reads as headroom (goldblum's check-dashboard-metrics states
        # exactly this, and refused this panel until the companion existed). The
        # companion is always emitted, so "no floor" is a measured 0 rather than
        # a silence a reader has to interpret.
        out.add("st_governor_priority_floor", verdict.floor, **lb)
        out.add("st_governor_priority_floor_declared",
                verdict.floor is not None, **lb)
        for tier in verdict.engaged or ():
            out.add("st_governor_engaged_tier_percent", tier.at,
                    lane=lane, window=tier.window)
        for burning in verdict.burning or ():
            out.add("st_governor_burndown", True, lane=lane, window=burning.window)
        for pacing in verdict.pacing or ():
            out.add("st_governor_pacing", True, lane=lane, window=pacing.window)
        for relaxed in verdict.relaxed or ():
            out.add("st_governor_relaxed", True, lane=lane, window=relaxed.window)
        # The ALARM is a fact about the governor's own health, so it is a number
        # here even though the prose that explains it deliberately is not.
        out.add("st_governor_alarm", bool(verdict.alarm), **lb)
    if live is not None:
        out.add("st_governor_live_agents", live, **lb)
    out.add("st_governor_blocked_agents", blocked, **lb)
    out.add("st_governor_setpoint_delta", setpoint_delta, **lb)

    if utilization is not None:
        out.add("st_governor_ready_work", utilization.ready, **lb)
        known = utilization.advice is not None
        out.add("st_governor_recommendation_known", known, **lb)
        # OMITTED, not zero, when the answer is "cannot tell" — see the module
        # docstring.  A hold is a decision; an unreadable input is not.
        if known:
            out.add("st_governor_recommendation", utilization.advice, **lb)
        cause = getattr(utilization, "cause", "") or ""
        for name in dict.fromkeys(tuple(CAUSES) + ((cause,) if cause else ())):
            out.add("st_governor_cause", name == cause, lane=lane, cause=name)

    # EVERY bucket at 0 rather than only the ones that have fired, so
    # `increase()` over a quiet direction or a quiet window is 0 instead of
    # no-data — and "no data" keeps its one meaning, that the producer is gone.
    # The event counters need the same treatment as the decision one, which is
    # the inconsistency goldblum's check-dashboard-metrics found: a relaxation
    # counter that does not exist until the first relaxation leaves its panel
    # empty for exactly as long as everything is fine.
    for direction in DIRECTIONS:
        out.add("st_governor_decisions_total",
                totals.decisions.get((lane, direction), 0),
                kind="counter", lane=lane, direction=direction)
    windows = sorted({w for (ln, w) in totals.relaxations if ln == lane}
                     | {w for (ln, w) in totals.burndowns if ln == lane}
                     | set(known_windows or ()))
    for window in windows:
        out.add("st_governor_relaxations_total",
                totals.relaxations.get((lane, window), 0),
                kind="counter", lane=lane, window=window)
        out.add("st_governor_burndowns_total",
                totals.burndowns.get((lane, window), 0),
                kind="counter", lane=lane, window=window)


def render(lanes, *, agents=None, now: float, totals: Totals = EMPTY) -> str:
    """The whole `job=st_governor` body: every lane, plus the fleet's agent counts.

    `lanes` is an iterable of dicts, one per governed harness:
        {"lane", "verdict", "utilization", "readings", "live", "blocked",
         "setpoint_delta"}
    `agents` is the directive's "how many codex or Claude", as
    ``{"state": {(harness, state): n}, "work": {(harness, work): n},
    "stopped": {harness: n}}`` — answered from the SAME registry-and-pane read the
    crew table uses, so the number on a dashboard cannot disagree with the number
    in `st crew`.  None means COULD NOT LOOK and publishes nothing, because an
    empty mapping would publish a fleet of zero agents.

    Pure: no clock (`now` is passed), no storage, no I/O.
    """
    out = _Out()
    for lane in lanes:
        name = lane["lane"]
        _lane_rows(out, name, verdict=lane.get("verdict"),
                   utilization=lane.get("utilization"),
                   readings=lane.get("readings") or {},
                   live=lane.get("live"), blocked=lane.get("blocked") or 0,
                   setpoint_delta=lane.get("setpoint_delta"), now=now,
                   totals=totals,
                   # Every window this lane can SEE, so its event counters exist
                   # at 0 from the first pass rather than from the first event.
                   known_windows=set(lane.get("readings") or {}) | {
                       w.window for w in
                       getattr(lane.get("utilization"), "windows", ()) or ()})
        _window_rows(out, name, lane.get("utilization"),
                     lane.get("readings") or {}, now)
    for (harness, state), count in sorted((agents or {}).get("state", {}).items()):
        out.add("st_agents", count, harness=harness, state=state)
    for (harness, work), count in sorted((agents or {}).get("work", {}).items()):
        out.add("st_agents_work", count, harness=harness, work=work)
    for harness, count in sorted((agents or {}).get("stopped", {}).items()):
        out.add("st_agents_stopped_deliberate", count, harness=harness)
    return out.render()


def render_producer(*, now: float, status: int, lanes: int, samples: int,
                    agents_counted: int | None) -> str:
    """The `job=st_governor_producer` body — pushed on EVERY pass, whatever
    happened above.  Its freshness is the producer's liveness; without it a dead
    tend and a tend with nothing to say are the same gap in the same graph.

    `agents_counted` is None for COULD NOT LOOK and is then omitted rather than
    published as 0, because 0 agents is a real and alarming fleet state.
    """
    out = _Out()
    out.add("st_governor_pass_timestamp_seconds", now)
    out.add("st_governor_publish_status", status)
    out.add("st_governor_lanes", lanes)
    out.add("st_governor_samples", samples)
    out.add("st_governor_agents_counted", agents_counted)
    return out.render()


# --- the push --------------------------------------------------------------


def _auth_header(url: str, env) -> tuple[str, dict]:
    """(url without userinfo, headers).  Credential file first, userinfo second.

    An unreadable credential file is NOT a silent fallback to anonymous: it
    returns no header, the push then fails with a 401, and the failure is
    reported through `st_governor_publish_status` rather than being papered over
    into an unauthenticated request that looks like a gateway problem.
    """
    parts = urlsplit(url)
    netloc, cred = parts.netloc, None
    if "@" in netloc:
        cred, netloc = netloc.rsplit("@", 1)
    path = env.get(PASSWORD_FILE_ENV, "").strip()
    user = env.get(USER_ENV, "").strip()
    if path and user:
        try:
            cred = f"{user}:{Path(path).expanduser().read_text().strip()}"
        except OSError:
            cred = cred          # keep whatever userinfo carried, else None
    headers = {"Content-Type": "text/plain"}
    if cred:
        headers["Authorization"] = "Basic " + base64.b64encode(
            cred.encode()).decode()
    return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", "")), headers


def _push(url: str, job: str, instance: str, body: str,
          timeout: float = PUSH_TIMEOUT_S, env=None) -> None:
    """PUT one group.  PUT, not POST: it REPLACES the group, so a lane or a
    window that stops existing stops being served instead of being frozen at its
    last value forever — the corpse class `PushgatewayJobStale`'s runbook is
    about."""
    base, headers = _auth_header(url, os.environ if env is None else env)
    target = f"{base}/metrics/job/{job}/instance/{instance}"
    req = urllib.request.Request(target, data=body.encode(), headers=headers,
                                 method="PUT")
    urllib.request.urlopen(req, timeout=timeout).read()


def publish(root, lanes, *, agents=None, now=None, url=None, instance=None,
            env=None, log=None) -> int:
    """Render, count and push one pass.  Returns the publish status.

    NEVER RAISES.  This is telemetry hanging off the one pass that respawns dead
    agents, and tend is the sole respawn path since the crew watchdog was masked
    (aegis-qwadc) — an exporter that can take that down is a control inversion,
    the same contract `stats.capture` states about the hook it lives in.  It
    logs, and returns a status the producer group publishes.

    Unset `ST_GOVERNOR_PUSHGATEWAY` -> NOTHING_TO_SAY and no HTTP at all.

    `env` is the DEPLOYMENT's `[env]` table, which is NOT injected into
    `os.environ` — `st` hands it to the consumers that need it, the way
    `creel_advisory`'s probe path is read.  Reading only the ambient environment
    here would leave a correctly configured deployment silently unexported, which
    is the "configured but not live" class this repo names on every surface.
    Ambient environment is the fallback, so a one-off run still works.
    """
    now = time.time() if now is None else now
    env = dict(os.environ) if env is None else {**os.environ, **env}
    url = env.get(PUSHGATEWAY_ENV, "").strip() if url is None else url
    if not url:
        return NOTHING_TO_SAY
    instance = instance or os.uname().nodename.split(".")[0]
    status, body, producer = OK, "", ""
    try:
        lanes = list(lanes)
        events = {"decisions": [], "relaxations": [], "burndowns": []}
        for lane in lanes:
            for key, rows in events_of(lane["lane"], lane.get("verdict"),
                                       lane.get("utilization")).items():
                events[key].extend(rows)
        totals = Ledger(root).bump(**events)
        body = render(lanes, agents=agents, now=now, totals=totals)
        if not body.strip():
            status = NOTHING_TO_SAY
    except Exception as exc:                     # noqa: BLE001 — see docstring
        status, lanes = COULD_NOT_RENDER, []
        if log:
            log(f"governor telemetry: could not render ({exc!r})")

    samples = sum(1 for line in body.splitlines()
                  if line and not line.startswith("#"))
    if status == OK:
        try:
            _push(url, JOB, instance, body, env=env)
        except Exception as exc:                 # noqa: BLE001
            status = PUSH_FAILED
            if log:
                log(f"governor telemetry: push to {JOB} FAILED ({exc!r}) — the "
                    f"gateway will keep serving the previous pass's values, so "
                    f"read st_governor_publish_status before trusting them")
    try:
        producer = render_producer(
            now=now, status=status, lanes=len(lanes), samples=samples,
            agents_counted=(None if not agents
                            else sum((agents.get("state") or {}).values())))
        _push(url, PRODUCER_JOB, instance, producer, env=env)
    except Exception as exc:                     # noqa: BLE001
        if log:
            log(f"governor telemetry: liveness push FAILED ({exc!r}) — this pass "
                f"is now indistinguishable from a tend that never ran")
    return status
