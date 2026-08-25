"""untracked — WARN a non-admin crew member ACTING with an EMPTY hook, and
ESCALATE to its lead when it persists. A PreToolUse hook:

    python -m shantytown.untracked --root <dir>

Stiwi's directive (aegis-fv2zc): *if a NON-ADMIN crew member is working WITHOUT a
bead on its hook, st should TELL them; if it continues, ESCALATE to the admin (or
their reports_to / lead).*

WHY. Work that is not on a bead is invisible to the tier: the lead cannot see it,
it does not survive the session, nothing coordinates against it, and no one can
audit it afterwards. This is not hypothetical — the night this was scoped, a
coordinator dispatched via `st inbox` instead of `st go` and a whole shift of
agents worked with empty hooks. Every one of them looked busy and none of them
was tracked. Nothing in the harness noticed, because nothing was looking.

A NUDGE, NOT A BLOCK — and that is load-bearing, not politeness. Untracked work
is sometimes legitimate (setup, orientation, a two-command fix); SUSTAINED
untracked work is drift. A guard that hard-refused the first unhooked Edit would
brick an agent doing the legal thing, and this repo has already shipped one
fail-closed hook that took the whole fleet down (aegis-w1nd: `hank hook pre-edit`
exiting 2 on every Write). So the ladder is:

    hook empty, acting        -> WARN the agent, in its own context, on a cooldown
    still empty, N calls, T   -> ESCALATE ONCE to reports_to (route_stop picks it)
    something on the hook     -> the record RESETS. No memory of the stretch.

FAIL OPEN, EVERYWHERE, AND NEVER ON A GUESS. main() catches everything and exits
0 with no output, because a governance nudge must not be able to stop an agent
from working. And a plate read that COULD NOT LOOK (bd down, store unreachable)
is SILENT — never a warning. "I could not tell" rendered as "you are untracked"
is the exact could-not-look-as-a-verdict class this codebase names in every other
reader (anchor's exit 2, _plate_of's `(None, "?")`, RankUnavailable). An agent
scolded for untracked work during a Dolt flap would learn to ignore the warning,
and then the warning is worth nothing when it is right.

WHY IT POLLS RATHER THAN READS EVERY TIME. This runs before every acting tool
call. On the deployment that needs it the plate lives in beads, and `bd list
--json` there is 0.15s and 306KB (measured 2026-07-24) — paid per Edit, per
Write, per Bash, forever. So the tracker is consulted at most once per POLL_S and
the verdict is carried in the ledger between polls. Strikes still count every
call: the cheap half is the counting, the expensive half is the looking.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# --- the ladder's constants, and why each number is what it is ----------------

# How often the TRACKER is actually consulted. Between polls the ledger's last
# verdict stands. 60s is short enough that `st go` -> next tool call reflects the
# new hook within a minute, and long enough that a fast edit loop is not paying
# a 306KB store dump per keystroke.
POLL_S = 60.0

# At most one warning per this window. Without it a worker doing legitimate setup
# gets the same paragraph 50 times in one turn, and the ONLY thing an agent can
# learn from that is to skip anything that starts with a warning glyph.
WARN_COOLDOWN_S = 300.0

# ESCALATION needs BOTH — a count AND a duration. The bead says "N warned actions
# (or T minutes)"; OR is wrong here and the difference is the false-positive rate.
# A worker legitimately orienting itself can fire 20 tool calls in 40 seconds, and
# on OR that agent's lead gets woken for a burst of setup. Requiring elapsed TIME
# too is what makes the signal mean "sustained", which is the word the directive
# actually uses ("sustained untracked work is drift").
ESCALATE_AFTER_STRIKES = 12
ESCALATE_AFTER_S = 600.0

# The tracker read is on the critical path of a tool call. Bounded, and a timeout
# surfaces as could-not-look (silent), never as a verdict.
BD_TIMEOUT_S = 10

SILENT, WARN, ESCALATE = "silent", "warn", "escalate"

# Read-side verdicts for consumers of the ledger. Kept separate from the hook's
# action verdicts above: this reader never warns, escalates, or mutates.
ACTIVITY_CLEAR = "clear"
ACTIVITY_RECENT = "recent"
ACTIVITY_UNKNOWN = "unknown"


def ledger_activity(root, agent: str, *, now=None, read=None, stat=None):
    """What the existing untracked ledger says about an idle-looking agent.

    Returns ``(clear|recent|unknown, detail)``.  ``recent`` means the hook has
    observed this non-admin acting with an empty hook within the same ten-minute
    window used by the escalation ladder.  Missing, malformed, or unreadable
    evidence is ``unknown`` — never a fabricated all-clear.

    The file mtime is the last acting-tool observation: ``Governor.check`` writes
    the ledger on every checked call, including cooldown-silent calls.  ``since``
    is the start of the stretch and cannot answer when activity last occurred.
    """
    now = time.time() if now is None else float(now)
    path = Path(root) / "untracked" / f"{agent}.json"
    try:
        text = read(path) if read is not None else path.read_text()
        mtime = stat(path) if stat is not None else path.stat().st_mtime
        data = json.loads(text)
    except (OSError, ValueError, TypeError):
        return ACTIVITY_UNKNOWN, "untracked ledger unreadable"
    if not isinstance(data, dict) or not isinstance(mtime, (int, float)):
        return ACTIVITY_UNKNOWN, "untracked ledger unreadable"
    if data.get("hooked") is True:
        return ACTIVITY_CLEAR, "hooked"
    if data.get("hooked") is not False:
        return ACTIVITY_UNKNOWN, "untracked ledger has no hook verdict"

    age = max(0.0, now - float(mtime))
    if age <= ESCALATE_AFTER_S:
        return ACTIVITY_RECENT, f"untracked activity {_age(age)} ago"
    return ACTIVITY_CLEAR, f"last untracked activity {_age(age)} ago"


def _age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60:02d}m"


@dataclass(frozen=True)
class Verdict:
    """What the hook decided, and WHY — the why is not decoration. Every SILENT
    outcome here has a different cause (exempt / holding work / could not look /
    on cooldown) and collapsing them is how you end up unable to tell a working
    guard from a dead one. `st doctor` and the tests both read it."""
    action: str                    # SILENT | WARN | ESCALATE
    why: str
    text: str = ""                 # what the AGENT is told (WARN/ESCALATE)
    to: str | None = None          # who was told (ESCALATE)
    strikes: int = 0


class Governor:
    """The check itself, with every backend injected — registry, plate reader,
    events sink, clock. Nothing here shells out or reads argv; main() wires the
    real ones. That split is what lets the escalation ladder be tested by
    mechanism (advance a fake clock, count fake events) instead of by waiting ten
    minutes and hoping.
    """

    def __init__(self, root, registry, plate, events, *, now=time.time,
                 route=None):
        self.root = Path(root)
        self.registry = registry
        self.plate = plate                     # (name) -> WorkItem | None, may RAISE
        self.events = events
        self.now = now
        if route is None:
            from .tier import route_stop
            route = route_stop
        self._route = route

    # --- the ledger: one file per agent, beside notify's and tend's ------------

    def _path(self, me: str) -> Path:
        return self.root / "untracked" / f"{me}.json"

    def _load(self, me: str) -> dict:
        try:
            d = json.loads(self._path(me).read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, me: str, led: dict) -> None:
        p = self._path(me)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(led, indent=2, sort_keys=True))
        os.replace(tmp, p)                     # atomic, same reason as FilesEvents

    def _reset(self, me: str, led: dict, now: float) -> None:
        """The stretch ended. Forget it COMPLETELY — strikes, timers, and the
        escalated marker. A worker that gets hooked, finishes, and goes unhooked
        again is starting a NEW stretch and deserves the full ladder from the
        top; carrying the old escalated flag forward would silence the second one.

        But KEEP THE POLL CACHE. Writing nothing here (or unlinking the file) was
        a real cost, and it fell entirely on the agents doing the right thing: a
        properly-hooked worker would re-read the tracker on EVERY acting tool
        call — 0.17s and a 306KB store dump per Edit, per Bash, forever —
        because the only record of "I looked, and recently" went with the
        strikes. Two different facts lived in one file; only one of them ends
        with the stretch.
        """
        self._save(me, {"checked_at": led.get("checked_at", now), "hooked": True})

    # --- the check -----------------------------------------------------------

    def check(self, me: str) -> Verdict:
        """Decide, and record. Called once per acting tool call."""
        try:
            card = self.registry.get(me)
        except LookupError:
            # Not in the registry -> not a crew member we govern. Say nothing:
            # this hook may be wired into a session the tier does not own.
            return Verdict(SILENT, "not in the registry")

        # ADMIN IS EXEMPT (the directive, explicitly). A coordinator legitimately
        # acts with an empty hook all day — dispatching, triage, draining — and
        # that is the JOB, not drift. Checked at run time as well as at emit time
        # (runtime.py wires the hook for non-admin roles only) because a worker
        # promoted to administrator keeps its already-launched settings file until
        # it restarts, and for that window only this check stands between the
        # promotion and a stream of warnings for doing exactly what it should.
        if card.role == "administrator":
            return Verdict(SILENT, "administrator — exempt")

        led = self._load(me)
        now = self.now()

        hooked = self._hooked(me, led, now)
        if hooked is None:
            # COULD NOT LOOK. Not a warning, and NOT a reset either: neither
            # asserting untracked work nor forgiving a stretch already in
            # progress. The ledger is left exactly as it was, so a flap that
            # spans a few tool calls costs the ladder nothing.
            return Verdict(SILENT, "could not read the tracker")
        if hooked:
            self._reset(me, led, now)
            return Verdict(SILENT, "work is on the hook")

        # --- the hook is empty and the agent is acting ------------------------
        strikes = int(led.get("strikes", 0)) + 1
        since = float(led.get("since") or now)
        led.update(strikes=strikes, since=since)

        elapsed = now - since
        if (strikes >= ESCALATE_AFTER_STRIKES and elapsed >= ESCALATE_AFTER_S
                and not led.get("escalated")):
            v = self._escalate(me, card, strikes, elapsed)
            led["escalated"] = True
            led["warned_at"] = now
            self._save(me, led)
            return v

        if now - float(led.get("warned_at") or 0.0) >= WARN_COOLDOWN_S:
            led["warned_at"] = now
            self._save(me, led)
            return Verdict(WARN, "hook empty", text=_warn_text(me, card, strikes, elapsed),
                           strikes=strikes)

        self._save(me, led)
        return Verdict(SILENT, "hook empty, warning on cooldown", strikes=strikes)

    def _hooked(self, me: str, led: dict, now: float) -> bool | None:
        """Is something on this agent's hook? True / False / None = COULD NOT LOOK.

        Three answers, deliberately, and the third is the whole reason this
        returns an optional bool instead of a bool. Polled: the cached answer
        stands for POLL_S, and a poll that RAISES leaves the cache untouched
        rather than poisoning it with a guess.
        """
        fresh = now - float(led.get("checked_at") or 0.0) < POLL_S
        if fresh and "hooked" in led:
            return bool(led["hooked"])
        try:
            item = self.plate(me)
        except Exception:
            return None
        led["checked_at"] = now
        led["hooked"] = item is not None
        return led["hooked"]

    def _escalate(self, me: str, card, strikes: int, elapsed: float) -> Verdict:
        """Tell the LEAD. route_stop picks the destination, which is the point of
        reusing it: reports_to first, an administrator for a worker with no lead,
        and a LOUD rise to the administrator when the lead itself is down (Q3).
        Re-deriving that routing here would be a second, quietly different tier.

        A routing failure (no lead AND no administrator) is reported to the AGENT
        rather than swallowed: an escalation with nowhere to go is a real
        misconfiguration, and the agent is the only party still reachable.
        """
        from .tier import Reason
        try:
            routing = self._route(self.registry, me)
        except LookupError as e:
            return Verdict(WARN, f"nowhere to escalate: {e}",
                           text=_stranded_text(me, strikes, elapsed, str(e)),
                           strikes=strikes)
        self.events.persist(
            to=routing.to, frm=me, reason=Reason.UNTRACKED_WORK.value,
            rose=routing.rose,
            # item=None + item_status=None is "the plate was EMPTY", which is
            # exactly the fact being escalated. The other encoding — status "?" —
            # means could-not-look, and this path is unreachable when we could not
            # look (check() returns SILENT first). The distinction is load-bearing
            # in _item_note; do not collapse it.
            item=None, item_status=None,
        )
        return Verdict(ESCALATE, "hook empty, persisted", to=routing.to,
                       text=_escalated_text(me, routing.to, strikes, elapsed),
                       strikes=strikes)


# --- what the agent is told ---------------------------------------------------

def _age(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}m"


def _how_to_hook(me: str, card) -> str:
    lead = card.reports_to
    ask = (f"  · or ask {lead} to dispatch you one: `st go <id> {me}`\n"
           if lead else "")
    return (f"  · `bd create \"<what you are actually doing>\" -p 2` then "
            f"`bd update <id> --assignee={me} --status=in_progress`\n{ask}")


def _warn_text(me: str, card, strikes: int, elapsed: float) -> str:
    return (
        f"⚠ UNTRACKED WORK — nothing is on your hook.\n"
        f"You are {me} ({card.role}) and you have made {strikes} acting tool "
        f"call(s) over {_age(elapsed)} with no open bead assigned to you.\n"
        f"Work should be bead-tracked: it is how your lead sees what you are "
        f"doing, how it survives your session ending, and how the tier keeps two "
        f"agents off the same problem. Untracked work is invisible work.\n"
        f"Put it on a hook:\n{_how_to_hook(me, card)}"
        f"This is a NUDGE, not a block — if this really is setup, carry on. "
        f"If it keeps up it escalates to "
        f"{card.reports_to or 'the administrator'}."
    )


def _escalated_text(me: str, to: str, strikes: int, elapsed: float) -> str:
    return (
        f"⚠ UNTRACKED WORK — ESCALATED to {to}.\n"
        f"{strikes} acting tool calls over {_age(elapsed)} with an empty hook. "
        f"{to} has been told, and will see it at their next stop.\n"
        f"Nothing is blocked and nothing is undone — but put your work on a bead "
        f"now, and if it was already tracked somewhere else, say so on the bead "
        f"so the record matches what happened."
    )


def _stranded_text(me: str, strikes: int, elapsed: float, why: str) -> str:
    return (
        f"⚠ UNTRACKED WORK — and it could NOT be escalated: {why}\n"
        f"{strikes} acting tool calls over {_age(elapsed)} with an empty hook, "
        f"and your card has no reachable lead or administrator. That is a "
        f"misconfiguration in the tier itself — your stop events have nowhere to "
        f"go either. Put your work on a bead, then get your card's reports_to "
        f"fixed (`st roles show`)."
    )


# --- the hook entry -----------------------------------------------------------

def _root(argv: list[str]) -> Path:
    """--root <dir>, else the shared discovery chain (deployment.resolve_root).

    ONE resolver, four callers. This function was written out longhand in the CLI
    and in each of the three hook entry points, which is the drift deployment.py
    exists to prevent — and it meant extending discovery would have had to be done
    four times or be done inconsistently.
    """
    if "--root" in argv:
        return Path(argv[argv.index("--root") + 1])
    return resolve_root()[0]


def plate_reader(root: Path):
    """The plate reader for the backend this DEPLOYMENT actually uses.

    NOT files-only. stop_event._plate_of can afford to be files-only (it says so,
    and a missing item there only thins a stop event); this cannot. On a beads-
    backed fleet a files-only reader finds an empty <root>/items every single
    time and warns EVERY agent, forever, while never once looking at the store
    where the work actually is. A governance nudge that is wrong by construction
    is worse than none: it trains the fleet to ignore it.

    A backend this reader cannot wire from a hook (forgejo needs coordinates the
    hook has no way to supply) RAISES, which check() reads as could-not-look and
    stays silent. Refusing to guess is the same rule as everywhere else here.
    """
    from .deployment import deployment_default, resolve_root
    backend = deployment_default(root, "SHANTY_BACKEND") or "files"
    if backend == "beads":
        from .beads import (BeadsTracker, EXTRA_REPOS_KEY, parse_extra_repos,
                            plate as beads_plate)
        trk = BeadsTracker(repo=deployment_default(root, "SHANTY_BEADS_REPO"),
                           timeout=BD_TIMEOUT_S,
                           # aegis-qmfa1
                           extra_repos=parse_extra_repos(
                               deployment_default(root, EXTRA_REPOS_KEY)))
        return lambda who: beads_plate(trk, who)
    if backend == "files":
        from .files import FilesTracker, plate as files_plate
        trk = FilesTracker(root / "items")
        return lambda who: files_plate(trk, who)

    def _cannot(_who):
        raise RuntimeError(
            f"SHANTY_BACKEND={backend!r} needs coordinates this hook cannot "
            f"supply; not guessing a plate.")
    return _cannot


def _emit(v: Verdict) -> None:
    """Claude Code PreToolUse: additionalContext puts the text in the MODEL's
    context without touching the permission decision. NEVER permissionDecision —
    not even "allow", which SUPPRESSES the user's own prompt and would silently
    downgrade the agent's permission posture (the rail runtime._guard_hook spells
    out). This hook only ever ADDS words; it can never subtract or add
    permission."""
    if not v.text:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": v.text,
    }}))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        me = os.environ.get("SHANTY_AGENT")
        if not me:
            return 0                       # no identity -> nothing to govern
        root = _root(argv)
        from .events import FilesEvents
        from .files import FilesRegistry
        gov = Governor(root, FilesRegistry(root / "crew"), plate_reader(root),
                       FilesEvents(root / "events"))
        _emit(gov.check(me))
    except Exception:
        # FAIL OPEN AND SILENT. A governance nudge must never be the reason an
        # agent could not edit a file. Deliberately bare: the failure modes here
        # are a store outage, a malformed ledger, a registry mid-write — none of
        # which the agent can act on, and all of which would otherwise print a
        # traceback into its context on every tool call.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
