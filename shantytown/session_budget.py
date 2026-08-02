"""session_budget — a ceiling on what ONE SESSION may do before it reports.

THIS IS NOT governor.py, AND THE DISTINCTION IS THE WHOLE POINT (aegis-xxae9).

    governor.py    governs the FLEET by Claude USAGE. "We are burning the
                   budget; run fewer agents." Its question is how much is left.
    session_budget governs ONE SESSION by WHAT IT HAS DONE. "You alone have been
                   going for six hours and deployed three times; stop and
                   report." Its question is how far this one has gone.

WHY A SECOND AXIS EXISTS. Measured 2026-08-01: a single unattended session ran
~6 hours, consumed four haul items back to back, pushed to three repos, built
and deployed binaries to a production host three times with a service restart
each, restarted a second production service, and ran a 67-minute reindex against
production data. The usage governor was ARMED and CORRECT the entire time — the
five-hour budget sat around 45%, below its first tier at 50%. Nothing was wrong
with it. It simply cannot see this: eleven cheap agents and one agent doing
eleven deploys read the same on a usage gauge.

EVERY INDIVIDUAL GATE ALSO HELD. Each bead was separately gated, the glibc check
ran before each cutover, backups were kept, and a dry run that surfaced unrelated
drift was correctly stopped rather than applied. The defect was never per-step
caution; it was that NOTHING BOUNDED THE AGGREGATE. So the fix belongs here and
not in more per-bead hedging, which would slow the work and still not stop it.

WHY THE HAUL COULD NOT STOP ITSELF. The advance is self-feeding by construction:
closing a bead is what UNBLOCKS the next one, so completing work produces more
work and there is no natural stopping point anywhere in the loop. The advance's
one existing ceiling is the context handoff line, and that is a RECYCLE, not a
stop — it says shed context and the haul resumes. Nothing measured cumulative
wall-clock, item count, or the RISK of what was being done.

    THE THREE THINGS COUNTED, and why these three:

    hours    wall-clock since this session's first recorded event. The one an
             absent human actually feels.
    items    haul items served this session. The bead count is the thing that
             was four when it should have been one or two.
    risk     production-class actions — deploys and service restarts, as
             classified at capture time in stats.py. Bead item 2: these must
             count against a TIGHTER budget than code and docs, because three
             binary deploys and two restarts in one unattended stretch is a
             different animal from three hours of editing.

Any ONE of them tripping is enough. They are not weighted into a single score,
deliberately: a composite number cannot tell an operator WHICH thing to change,
and the report has to name what was spent.

FAIL-OPEN, AND FAIL-OPEN LOUDLY. Every error path here returns "wide open". A
budget that cannot read its own counters must never trap a worker at its own
stop — that would take the whole crew down over a corrupt sqlite file. When the
signal is missing rather than merely low, that is SIGNAL LOST and it is said out
loud, never silently treated as zero. Same rule the usage governor learned the
expensive way (aegis-jrax3): it was armed and blind for a whole session, and it
was only survivable because being blind ALARMED instead of reading as healthy.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

# An agent whose most recent event is older than this is not running now — the
# reader refuses to attribute a fresh advance to a dead stretch.
STALE_AFTER_S = 3600.0
# THE STRETCH BOUNDARY, and why the budget is not scoped to the harness session.
#
# `/clear` starts a NEW session id, and the haul's context handoff line tells the
# agent to /clear and then RESUMES the haul on the fresh context. So a budget
# keyed on session id resets at exactly the moment the haul hands itself over —
# the ceiling would be bypassed by the mechanism it sits next to, silently, and
# it would look like it was working.
#
# Measured on the incident's own agent (2026-08-01): the session id spans 3.65h
# where the continuous stretch of work spans 5.08h. The shorter number is the one
# a session-scoped budget would have used, and it is the wrong number — an agent
# is not less far along because it dropped its context.
#
# So the unit is the STRETCH: this agent's events with no idle gap wider than
# this. Fleet-wide, 99% of inter-event gaps are under 15 minutes and p99.5 is
# 34 minutes, while a genuine break in that same run was 78; between 45 and 60
# minutes the tail is flat (45 gaps vs 41 out of 10,228). 45 sits on the flat
# part, comfortably past working pauses and short of any real break.
STRETCH_GAP_S = 45 * 60.0
WARN = "warn"
STOP = "stop"


class BudgetError(ValueError):
    """A malformed [session_budget] table. Raised at parse, never at read."""


@dataclass(frozen=True)
class Limits:
    """The [session_budget] table, resolved.

    THERE IS NO `enabled` KEY, matching governor.Policy: declaring a limit IS the
    enabling act. All-unset is off, and off is the default, so a deployment that
    has never heard of this is untouched.

    BUT NOTE WHAT THAT COST LAST TIME. The usage governor shipped correct and
    INERT for weeks because the live root had no shantytown.toml, so the feature
    existed and governed nothing. Shipping this one unarmed too would repeat that
    exactly — with the difference that we now have a measured incident saying the
    ceiling is needed. Arming it is a config change in the deployment, and it is
    part of this work, not a later step for somebody else to remember.
    """
    max_hours: float | None = None
    max_items: int | None = None
    max_risk: int | None = None
    on_signal_lost: str = WARN

    @property
    def active(self) -> bool:
        return any(v is not None
                   for v in (self.max_hours, self.max_items, self.max_risk))


@dataclass(frozen=True)
class Spend:
    """What this session has actually done. `signal_lost` is a THIRD state, never
    folded into zero: "nothing recorded" and "nothing happened" need different
    responses, and only one of them is safe to ignore."""
    session: str = ""
    started: float = 0.0
    hours: float = 0.0
    items: int = 0
    risk: int = 0
    risk_kinds: dict[str, int] = field(default_factory=dict)
    signal_lost: bool = False

    def summary(self) -> str:
        bits = [f"{self.hours:.1f}h", f"{self.items} item(s)"]
        if self.risk:
            kinds = ", ".join(f"{n}x {k}" for k, n in sorted(self.risk_kinds.items()))
            bits.append(f"{self.risk} production action(s) ({kinds})")
        return ", ".join(bits)


@dataclass(frozen=True)
class Ceiling:
    """A tripped limit, with the number that tripped it.

    NAMES THE MEASURE, NEVER JUST "the budget". An agent told only "you are done"
    learns nothing and an operator cannot tell a correct stop from a broken
    counter — the same reason governor.Tier.label refuses to print a bare
    percentage."""
    measure: str
    measured: float
    limit: float
    spend: Spend

    def label(self) -> str:
        shown = (f"{self.measured:.1f}" if self.measure == "hours"
                 else f"{int(self.measured)}")
        lim = (f"{self.limit:.1f}" if self.measure == "hours" else f"{int(self.limit)}")
        unit = {"hours": "hours unattended",
                "items": "haul items this session",
                "risk": "production actions (deploys/restarts)"}[self.measure]
        return f"{shown} {unit} — the ceiling is {lim}"


def parse(tbl: dict) -> Limits:
    """[session_budget] -> Limits. Raises BudgetError with the key named.

    REFUSES A ZERO OR NEGATIVE CEILING rather than accepting it. `max_items = 0`
    reads as "no items allowed" and would wedge every worker on its first
    advance, fleet-wide, from one typo. Off is expressed by OMITTING the key —
    there is exactly one way to say it, so a 0 is always a mistake worth
    refusing at parse rather than discovering at 3am."""
    if not tbl:
        return Limits()
    known = {"max_hours", "max_items", "max_risk", "on_signal_lost"}
    for k in tbl:
        if k not in known:
            raise BudgetError(
                f"[session_budget] unknown key {k!r}; known keys: "
                f"{', '.join(sorted(known))}")

    def num(key, cast):
        v = tbl.get(key)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise BudgetError(f"[session_budget] {key} must be a number, got {v!r}")
        if v <= 0:
            raise BudgetError(
                f"[session_budget] {key} = {v} — a ceiling of zero or less would "
                f"stop every session immediately. Omit the key to disable it.")
        return cast(v)

    sl = tbl.get("on_signal_lost", WARN)
    if sl not in (WARN, STOP):
        raise BudgetError(
            f"[session_budget] on_signal_lost must be {WARN!r} or {STOP!r}, got {sl!r}")
    return Limits(max_hours=num("max_hours", float),
                  max_items=num("max_items", int),
                  max_risk=num("max_risk", int),
                  on_signal_lost=sl)


# --- reading the spend -----------------------------------------------------

def _db_path(root: Path) -> Path:
    return Path(root) / "stats.sqlite"


def stretch_start(rows: list[float], now: float,
                  gap: float = STRETCH_GAP_S) -> float | None:
    """Where the CURRENT stretch began: walk back from the newest event until an
    idle gap wider than `gap`. `rows` is ascending timestamps.

    Split out and pure so the boundary rule is testable without a database."""
    if not rows:
        return None
    start = rows[-1]
    for i in range(len(rows) - 1, 0, -1):
        if rows[i] - rows[i - 1] > gap:
            break
        start = rows[i - 1]
    return start


def read_spend(root: Path, agent: str, now: float | None = None,
               gap: float = STRETCH_GAP_S) -> Spend:
    """What `agent`'s current STRETCH has spent, from the stats store.

    THE STORE IS ALREADY THERE AND ALREADY WRITTEN TO on every tool call, so this
    adds no new plumbing on the capture path and nothing to keep in sync.

    A LAST EVENT OLDER THAN AN HOUR IS SIGNAL LOST, not a stretch with a lot of
    elapsed time. Reading a long-dead run as "six hours elapsed" would stop a
    FRESH session on its first advance, over work it did not do. Unknown is its
    own answer here, and it is the safe one.

    Never raises: any failure is signal_lost, which the caller treats as open."""
    now = time.time() if now is None else now
    p = _db_path(root)
    if not p.exists():
        return Spend(signal_lost=True)
    conn = None
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=3)
        conn.execute("PRAGMA busy_timeout=2000")
        # Bounded window: a stretch cannot reach back further than the staleness
        # horizon plus the longest plausible run, and an unbounded scan of a
        # months-old store on every stop is a cost the Stop hook should not pay.
        rows = [r[0] for r in conn.execute(
            "SELECT ts FROM events WHERE agent=? AND ts >= ? ORDER BY ts",
            (agent, now - 7 * 86400.0))]
        if not rows:
            return Spend(signal_lost=True)
        if now - rows[-1] > STALE_AFTER_S:
            return Spend(signal_lost=True)
        start = stretch_start(rows, now, gap)
        if start is None:
            return Spend(signal_lost=True)
        session = (conn.execute(
            "SELECT session FROM events WHERE agent=? AND session<>''"
            " ORDER BY ts DESC LIMIT 1", (agent,)).fetchone() or [""])[0]
        items = conn.execute(
            "SELECT COUNT(*) FROM events WHERE agent=? AND ts >= ? AND kind=?",
            (agent, start, "haul")).fetchone()[0]
        kinds = dict(conn.execute(
            "SELECT risk, COUNT(*) FROM events WHERE agent=? AND ts >= ?"
            " AND risk IS NOT NULL GROUP BY risk", (agent, start)).fetchall())
        return Spend(session=session or "",
                     started=start,
                     hours=max(0.0, (now - start) / 3600.0),
                     items=int(items),
                     risk=sum(kinds.values()),
                     risk_kinds={str(k): int(v) for k, v in kinds.items()})
    except Exception:                    # noqa: BLE001 — a budget never raises
        return Spend(signal_lost=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:            # noqa: BLE001
                pass


def record_item(root: Path, agent: str, session: str, bead_id: str,
                now: float | None = None) -> None:
    """Record one haul item served, so `items` can be counted at all.

    The ONLY write this module makes, and it is the advance's own footprint: the
    stats store records tool calls and stops, neither of which is an item. Best
    effort by contract — a failed write means the ceiling under-counts items,
    which is the safe direction and is never worth failing an advance over."""
    now = time.time() if now is None else now
    try:
        # stats._db, not a bare connect: it OWNS the schema and the in-place
        # column migrations. Connecting directly meant that on a deployment where
        # the advance fired before the capture hook had ever created the store,
        # the INSERT hit a missing table, was swallowed by the best-effort
        # except, and the item counter stayed silently at zero — a ceiling that
        # cannot count the thing it is counting. Found by the tests below.
        from .stats import _db
        conn = _db(Path(root))
        try:
            conn.execute(
                "INSERT INTO events(ts, agent, kind, tool, session, detail)"
                " VALUES (?,?,?,?,?,?)",
                (now, agent, "haul", "haul", session, bead_id))
            conn.commit()
        finally:
            conn.close()
    except Exception:                    # noqa: BLE001
        pass


def times_served(root: Path, agent: str, bead_id: str, since: float) -> int:
    """How many times this bead has ALREADY been fed to this agent in the current
    stretch (bead item 4).

    WHY THIS IS COUNTED AT ALL. Being handed the same bead back reads as an
    explicit instruction to keep going — it was read exactly that way, twice, in
    the run that produced this module. It is usually nothing of the kind: the
    haul re-serves any open, assigned, ready bead, so a bead the agent decided
    it was finished with keeps returning until its assignee is cleared. The
    re-serve carries no intent, and the release affordance was already in the
    message; what was missing is that a REPEAT is visibly a repeat.

    Returns 0 on any failure — an uncounted repeat is the status quo, and no
    reason to fail an advance."""
    try:
        p = _db_path(root)
        if not p.exists():
            return 0
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=3)
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM events WHERE agent=? AND kind=? AND detail=?"
                " AND ts >= ?", (agent, "haul", bead_id, since)).fetchone()[0])
        finally:
            conn.close()
    except Exception:                    # noqa: BLE001
        return 0


def current_session(root: Path, agent: str) -> str | None:
    """The session id the stats store last saw for `agent`, or None."""
    sp = read_spend(root, agent)
    return sp.session or None


# --- block-once, so the ceiling TERMINATES ---------------------------------
#
# A Stop hook that blocks whenever the ceiling is over is an INFINITE LOOP: the
# agent reports, stops, the hook fires again, the ceiling is still over, it
# blocks again. The agent can never actually stop — the control meant to end the
# run would be the thing preventing it from ending, which is worse than no
# control at all.
#
# So the ceiling blocks EXACTLY ONCE per stretch: the first stop after it trips
# gets the report instruction, and every stop after that is allowed through. The
# marker is keyed on the stretch's START, which is stable while the stretch lives
# and changes the moment a real break begins a new one — so a genuinely new run
# is never silenced by an old marker.

def _marker(root: Path, agent: str) -> Path:
    return Path(root) / "session_budget" / f"{agent}.json"


def already_reported(root: Path, agent: str, spend: Spend) -> bool:
    """Has this stretch already been told to stop? Unreadable marker -> False,
    which costs one extra report and never costs a trapped worker."""
    try:
        import json
        d = json.loads(_marker(root, agent).read_text(encoding="utf-8"))
        return abs(float(d.get("started") or 0.0) - spend.started) < 1.0
    except Exception:                    # noqa: BLE001
        return False


def mark_reported(root: Path, agent: str, spend: Spend) -> None:
    """Record that this stretch has had its one report. Best effort: a failed
    write means one more report next stop, never a wedged worker."""
    try:
        import json
        p = _marker(root, agent)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"started": spend.started, "at": time.time(),
                                 "session": spend.session}), encoding="utf-8")
    except Exception:                    # noqa: BLE001
        pass


def limits_for(root: Path) -> Limits:
    """The deployment's [session_budget], or wide open if it cannot be read.

    A CONFIG THAT WILL NOT PARSE MUST NOT STOP THE CREW. Same direction as every
    other fail path here: the cost of a missed ceiling is a long session, and the
    cost of a wrongly-applied one is the whole fleet stuck at its own stop."""
    try:
        from . import config
        cfg, _err = config.load_or_default(Path(root))
        return cfg.session_budget
    except Exception:                    # noqa: BLE001
        return Limits()


def gate(root: Path, agent: str, now: float | None = None
         ) -> tuple[Limits, Spend, Ceiling | None]:
    """The one call an advance makes: (limits, spend, tripped-ceiling-or-None).

    Wholly fail-open — a raise anywhere in here returns wide open, because this
    runs inside a Stop hook and the alternative to a missed ceiling is a fleet
    that cannot stop."""
    try:
        limits = limits_for(root)
        if not limits.active:
            return limits, Spend(), None
        spend = read_spend(root, agent, now)
        return limits, spend, verdict(limits, spend)
    except Exception:                    # noqa: BLE001
        return Limits(), Spend(signal_lost=True), None


def signal_lost_note(limits: Limits, spend: Spend, agent: str) -> str:
    """What to say when the budget is armed but blind.

    SIGNAL LOST IS NEVER SILENCE. The usage governor was armed and blind for an
    entire session (aegis-jrax3) and survived it only because being blind ALARMED
    every pass instead of reading as a healthy low number. Same rule, same reason:
    an unmeasured ceiling that says nothing is indistinguishable from a ceiling
    with room to spare."""
    if not limits.active or not spend.signal_lost:
        return ""
    return (f"session budget: SIGNAL LOST for {agent} — no recent events in the "
            f"stats store, so this session is running UNMEASURED. The ceiling "
            f"cannot fire. Check that the stats capture hook is wired.")


# --- the verdict -----------------------------------------------------------

def verdict(limits: Limits, spend: Spend) -> Ceiling | None:
    """The tripped ceiling, or None for wide open.

    MOST-EXCEEDED WINS when more than one trips, by ratio over its own limit, so
    the report names the thing that is furthest gone rather than whichever key
    happens to be checked first. An operator reading "4 items, ceiling 3" when
    the session is also nine hours into a three-hour budget would tune the wrong
    number."""
    if not limits.active or spend.signal_lost:
        return None
    checks = (("hours", spend.hours, limits.max_hours),
              ("items", float(spend.items), limits.max_items),
              ("risk", float(spend.risk), limits.max_risk))
    tripped = [(m, v, float(lim)) for m, v, lim in checks
               if lim is not None and v >= float(lim)]
    if not tripped:
        return None
    m, v, lim = max(tripped, key=lambda t: t[1] / t[2] if t[2] else 0.0)
    return Ceiling(measure=m, measured=v, limit=lim, spend=spend)


def headroom(limits: Limits, spend: Spend) -> str:
    """One clause naming what is left, for the advance's own message.

    THE HAUL'S STANDING-AUTHORITY LINE IS WHY THIS EXISTS (bead item 3). The
    advance says "the coordinator was not pinged: this queue is yours", which is
    true and which reads as unconditional permission to keep going — it was read
    that way, four items deep. It cannot be deleted (a self-feeding queue does
    need to say nobody is coming), so instead it now carries the remaining
    headroom beside it. Authority with a number attached is a different sentence
    from authority without one."""
    if not limits.active:
        return ""
    if spend.signal_lost:
        return "session budget: SIGNAL LOST — running unmeasured"
    left = []
    if limits.max_hours is not None:
        left.append(f"{max(0.0, limits.max_hours - spend.hours):.1f}h")
    if limits.max_items is not None:
        left.append(f"{max(0, limits.max_items - spend.items)} item(s)")
    if limits.max_risk is not None:
        left.append(f"{max(0, limits.max_risk - spend.risk)} production action(s)")
    return "session budget: " + ", ".join(left) + " left before you stop and report"


def stop_message(c: Ceiling, next_bead: str | None = None) -> str:
    """What the worker is told when the ceiling trips.

    IT ASKS FOR A REPORT, NOT SILENCE. A session that just stops is
    indistinguishable from one that crashed, and the next session inherits no
    idea what the last one did — which is most of the cost of an unattended run
    in the first place. The bead trail is the durable half; the stop reason is
    what a human scanning `st crew` sees.

    IT ALSO DOES NOT FEED THE NEXT BEAD. Naming it would be handing over the
    thing the ceiling exists to withhold, and an agent told "stop, and by the way
    your next item is aegis-xyz" will do the item."""
    held = (f" {next_bead} stays claimed for whoever picks it up next."
            if next_bead else "")
    return (
        f"SESSION CEILING: {c.label()}. This session has run {c.spend.summary()} "
        f"unattended, and the haul is NOT serving another item.{held}\n"
        f"Do this now, in order: (1) commit and push anything unpushed — work "
        f"stranded on one box is the cost this exists to avoid; (2) write what "
        f"you did and what is left onto the bead trail, so the next session "
        f"starts informed rather than re-deriving it; (3) stop cleanly, so your "
        f"stop event fires and the coordinator learns you are free.\n"
        f"Do NOT pick up more work to 'finish the thought' — the aggregate is "
        f"what tripped, and every individual next step will look defensible too. "
        f"That is the failure mode, not an exception to it.")
