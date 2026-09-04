"""deferrals — surface a deferral whose time or condition has come. REPORT ONLY.

THE FAILURE (aegis-boj8a2). `deferred` is the one status invisible to EVERY feeder:
`br ready` excludes it by design, hauls take only ready items, stop_event/tend/
feed_check all read ready, and the stop policy flags free workers against
dispatchable beads. So a resume condition written as prose — "until franklin's
converge lands" — has no mechanism behind it except the author's memory.

Measured 2026-09-03: **115 deferred beads, 12 of them LAPSED**, three of those P1
and lapsed for 26 days. aegis-6noan was deferred "until franklin's converge lands";
it landed the SAME DAY and the bead sat nine days, found by a human checking
whether someone else's queue was dry. `aegis-o2w6v`, the campaign bead to burn down
the deferral queue, is itself deferred.

TWO REPRESENTATIONS, AND BOTH ARE LIVE. The clbx2 cutover (aegis-vyc3aa) moved
deferral from `status = 'deferred'` to a `defer_until` TIMESTAMP with status left
`open`. Measured on the live store the same day: 115 rows still carry
`status = deferred` (14 of them WITH a defer_until) and 2 `open` rows carry a
defer_until. A sweeper that reads only one of those shapes is blind to most of the
board, so everything here keys off the FIELD and ignores the status entirely.

WHAT THIS DOES NOT DO: it never un-defers anything. A lapsed deferral may still be
the right call, and the person who deferred it is the person who knows. It reports;
the admin rules. That is item 3 of the bead and it is not negotiable — an
auto-undefer would take a queue nobody reads and start feeding it work nobody
re-judged.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# A resume condition a MACHINE can test, as opposed to prose. Kept deliberately
# small: `closed:<id>` covers the specimen that produced this bead and 30 of the
# 101 prose deferrals name another bead in their notes, so it is where the value
# is. `date:` is the degenerate case of defer_until and is accepted because
# authors write it. `sha-serving:` is in the bead's proposal and is NOT
# implemented — see `UNSUPPORTED`, which reports it as untested rather than
# silently treating it as absent.
_CONDITION = re.compile(
    r"resume[_ -]?when\s*[:=]\s*([a-z-]+)\s*:\s*(\S+)", re.IGNORECASE)

#: Condition kinds this module can actually decide.
SUPPORTED = ("closed", "date")
#: Kinds the vocabulary defines and this module cannot yet test. Reported as
#: UNTESTABLE, never as unmet — "we did not look" and "we looked and it is not
#: met" must not render the same, which is the whole defect this bead is about.
UNSUPPORTED = ("sha-serving",)


def parse_stamp(value) -> datetime | None:
    """An ISO stamp as an aware UTC datetime, or None.

    NAIVE IS READ AS UTC rather than crashed on: br writes Z-suffixed UTC, but a
    hand-edited or pre-cutover row may be naive, and comparing naive to aware
    raises TypeError — which a caller sees as a crashed sweep, not a bad row.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Condition:
    """A parsed resume condition. `kind` is lowercase; `arg` is verbatim."""
    kind: str
    arg: str

    def testable(self) -> bool:
        return self.kind in SUPPORTED

    def render(self) -> str:
        return f"{self.kind}:{self.arg}"


def parse_condition(text: str) -> Condition | None:
    """The FIRST `resume_when: <kind>:<arg>` in `text`, or None.

    First, not last: a bead's notes accumulate, and the earliest declaration is
    the one whose author was deferring. Prose with no such marker returns None and
    is reported as a lapsed date only — this module never guesses a condition out
    of a sentence, because a wrong guess here reports work as ready that is not.
    """
    if not text:
        return None
    m = _CONDITION.search(text)
    if not m:
        return None
    return Condition(m.group(1).lower(), m.group(2).rstrip(".,;)"))


@dataclass
class Finding:
    """One deferral worth the admin's attention, and WHY.

    `lapsed` and `met` are separate because they are different claims and have
    different remedies: a lapsed date means the author's own deadline passed, a
    met condition means the thing they were waiting for happened. A bead can be
    both, and collapsing them would lose which one to say.
    """
    bead: str
    title: str = ""
    assignee: str = ""
    priority: int | None = None
    lapsed_at: datetime | None = None
    lapsed_days: int = 0
    condition: Condition | None = None
    met: bool = False
    untestable: str = ""
    # NO defer_until AND no `resume_when:` marker. Such a bead is invisible to
    # BOTH paths at once — feeders skip status=deferred, and the two guards in
    # evaluate() used to drop it here — so it is not "waiting", it is off every
    # automated path there is. 101 of 112 deferrals were in this state when the
    # check was added (aegis-hm8994).
    conditionless: bool = False

    def key(self) -> str:
        """The transition key: bead + the STATE being reported.

        Includes the condition and both verdicts, so a bead whose condition later
        becomes met re-reports (the state changed) while an unchanged lapsed
        deferral stays silent however many passes run over it.
        """
        cond = self.condition.render() if self.condition else ""
        raw = (f"{self.bead}|{cond}|{int(bool(self.lapsed_at))}|{int(self.met)}"
               f"|{self.untestable}|{int(self.conditionless)}")
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def render(self) -> str:
        bits = []
        if self.lapsed_at:
            bits.append(f"LAPSED {self.lapsed_days}d ago "
                        f"({self.lapsed_at.date().isoformat()})")
        if self.met:
            bits.append(f"CONDITION MET [{self.condition.render()}]")
        elif self.untestable:
            bits.append(f"condition UNTESTABLE [{self.untestable}] — "
                        f"not evaluated, NOT unmet")
        if self.conditionless:
            bits.append("NO RESUME CONDITION — invisible to feeders AND to this "
                        "sweeper; needs a resume_when:/defer_until or a close")
        who = self.assignee or "unassigned"
        pri = "" if self.priority is None else f"P{self.priority} "
        return (f"{self.bead} {pri}({who}): {'; '.join(bits)}"
                f"{' — ' + self.title if self.title else ''}")


def evaluate(rows, now: datetime, is_closed=None) -> list:
    """Findings for `rows`, newest-lapse last. Reads only; mutates nothing.

    `is_closed(bead_id) -> bool | None` decides a `closed:` condition. **None
    means CANNOT TELL and is reported as untestable, never as met** — the same
    rule the cycle guard keeps for an unreadable tree. Reporting a condition met
    on a lookup that failed would send an admin to un-defer work whose blocker may
    still be open, which is worse than staying quiet.
    """
    out: list[Finding] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bead = str(row.get("id") or "").strip()
        if not bead:
            continue
        raw_when = row.get("defer_until")
        when = parse_stamp(raw_when)
        # A stamp that was WRITTEN but cannot be parsed is not the same as no
        # stamp at all. Both are invisible — nothing will ever make either lapse
        # — but the remedies differ and so must the wording: one needs a
        # condition, the other needs its existing one CORRECTED. Reporting
        # `defer_until: "soon"` as "no resume condition" would send the reader
        # looking for a field that is already there (aegis-hm8994).
        malformed = raw_when is not None and when is None
        lapsed = when is not None and when <= now
        cond = parse_condition(str(row.get("notes") or ""))

        met, untestable = False, ""
        if cond is not None:
            if cond.kind == "date":
                stamp = parse_stamp(cond.arg)
                met = stamp is not None and stamp <= now
                if stamp is None:
                    untestable = f"{cond.render()} (unparseable date)"
            elif cond.kind == "closed":
                # A RAISING LOOKUP IS CANNOT-TELL, NOT A CRASHED SWEEP. The
                # guard lives here rather than in each caller because the whole
                # point of this leg is that one unreadable bead must not cost the
                # other 114 — and a caller that forgot would fail in the loudest
                # possible way, taking the tend pass with it.
                verdict = None
                if is_closed is not None:
                    try:
                        verdict = is_closed(cond.arg)
                    except Exception:
                        verdict = None
                if verdict is None:
                    untestable = f"{cond.render()} (could not read {cond.arg})"
                else:
                    met = bool(verdict)
            else:
                untestable = cond.render()

        if malformed and cond is None:
            out.append(Finding(
                bead=bead, title=str(row.get("title") or "")[:70],
                assignee=str(row.get("assignee") or ""),
                priority=row.get("priority"),
                untestable=f"defer_until {raw_when!r} is unparseable — "
                           f"nothing will ever make it lapse"))
            continue
        if not lapsed and cond is None and when is None:
            # NOT "nothing to say at all" — that was the bug (aegis-hm8994).
            # No date and no condition means nothing will EVER make this lapse or
            # be met, so the two `continue`s below could never fire for it and it
            # was dropped on every pass forever. Report it as its own kind.
            out.append(Finding(
                bead=bead, title=str(row.get("title") or "")[:70],
                assignee=str(row.get("assignee") or ""),
                priority=row.get("priority"), conditionless=True))
            continue
        if not lapsed and not met and not untestable:
            continue        # deferred, on time, condition genuinely unmet — quiet
        if not lapsed and cond is None:
            continue        # a future defer_until with no marker: on time, quiet
        out.append(Finding(
            bead=bead, title=str(row.get("title") or "")[:70],
            assignee=str(row.get("assignee") or ""),
            priority=row.get("priority"),
            lapsed_at=when if lapsed else None,
            lapsed_days=(now - when).days if lapsed else 0,
            condition=cond, met=met, untestable=untestable))
    out.sort(key=lambda f: (-(f.lapsed_days), f.bead))
    return out


class Reported:
    """What has already been said, so a steady state is said ONCE.

    wu's constraint on this bead, and it is the difference between a mechanism and
    noise: 115 lapsed deferrals printed every tend cycle is a channel the admin
    mutes within a day — the exact failure grant fixed in dfllto (advisories
    repeating on an unchanged cause) and that gh-ci-watch solves with a three-state
    log. So: report a deferral once when its state first changes, stay silent while
    it is unchanged, re-report when it changes again.

    Keyed by bead id -> state key, NOT a growing log: the newest state is the only
    one worth holding, and a file that only grows is its own outage eventually.
    """

    def __init__(self, root):
        self.path = Path(root) / "notify" / "deferral-sweep.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            # NEVER raises. A malformed ledger degrades to "nothing reported yet"
            # — which re-reports once and is self-healing — rather than wedging
            # the sweep for the whole fleet. Same conservative direction as
            # cycle.Requests._load.
            return {}

    def unreported(self, findings) -> list:
        """The findings whose state has CHANGED since the last sweep."""
        seen = self._load()
        return [f for f in findings if seen.get(f.bead) != f.key()]

    def record(self, findings) -> None:
        """Mark the CURRENT state of every finding as said.

        Beads absent from `findings` are DROPPED: a deferral that stopped being
        lapsed (re-deferred to a new date, or closed) should re-report if it
        lapses again, and keeping its old key would silence exactly that.
        """
        from .files import write_json_atomic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, {f.bead: f.key() for f in findings})


def report(findings, cap: int = 12, blind_cap: int = 6) -> list:
    """Lines for the admin, most-lapsed first, capped.

    The cap is a display bound, and the tail is COUNTED rather than dropped
    silently — "and 40 more" is information; a quietly truncated list is the
    aegis-bro88 defect (an instrument reporting its own blindness as a clean
    answer).

    CONDITION-LESS deferrals are reported in their OWN block, not merged into the
    list above, and that separation is load-bearing (aegis-hm8994). There were 101
    of them against 11 actionable findings when this was written; interleaving them
    would have buried every lapsed date and met condition under a wall of beads
    that are merely parked — which is precisely the aegis-1gy64 mechanism this
    sweeper's own design notes refuse, reproduced inside the fix for it. They are
    a BACKLOG (drive it down once), not a queue of rulings (act on each), so they
    are summarised by count and shown worst-priority-first.
    """
    actionable = [f for f in findings if not f.conditionless]
    blind = [f for f in findings if f.conditionless]
    lines = []
    if actionable:
        lines.append(f"{len(actionable)} deferral(s) need a ruling "
                     f"(REPORT ONLY — nothing was un-deferred):")
        lines += [f"    {f.render()}" for f in actionable[:cap]]
        if len(actionable) > cap:
            lines.append(f"    ... and {len(actionable) - cap} more — "
                         f"`br list --status deferred --limit 0`")
    if blind:
        worst = sorted(blind, key=lambda f: (f.priority if f.priority is not None
                                             else 99, f.bead))
        lines.append(f"{len(blind)} deferral(s) have NO RESUME CONDITION — no "
                     f"defer_until and no `resume_when:`. Nothing will ever "
                     f"surface these; they need a condition or a close:")
        lines += [f"    {f.render()}" for f in worst[:blind_cap]]
        if len(blind) > blind_cap:
            lines.append(f"    ... and {len(blind) - blind_cap} more — "
                         f"`br list --status deferred --limit 0`")
    return lines
