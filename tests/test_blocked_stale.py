"""A bead blocked on a HUMAN is stopped, not waiting — and nothing re-asked.

The specimen: a P1 SECURITY bead (two live outward-facing bot tokens in git
history) sat BLOCKED for seventeen days. Surfaced to a human once, then blocked,
and blocked meant invisible — off `bd ready`, off the Rule Zero sweep, off every
capacity report. The only thing still touching it was the plate reader, handing
it to an agent to fail on; removing THAT (the right fix) removed the last thing
that looked at it at all.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from shantytown.inbox import is_blocked
from shantytown.notify import (BLOCKED_MIN_AGE_DAYS, BlockedStaleAlerter,
                               BlockedMisstatusAlerter, _bead_age_days)

NOW = datetime(2026, 8, 2, tzinfo=timezone.utc).timestamp()


def _row(bid, days_old, *, labels=("decision-needed",), status="blocked",
         prio=1, assignee="muldoon", updated_days=0):
    created = datetime.fromtimestamp(NOW - days_old * 86400, timezone.utc)
    updated = datetime.fromtimestamp(NOW - updated_days * 86400, timezone.utc)
    return {"id": bid, "status": status, "labels": list(labels), "priority": prio,
            "assignee": assignee, "title": f"{bid} title",
            "created_at": created.isoformat().replace("+00:00", "Z"),
            "updated_at": updated.isoformat().replace("+00:00", "Z")}


class _Push:
    def __init__(self, ok=True):
        self.msgs = []; self.ok = ok
    def __call__(self, reg, panes, msg):
        self.msgs.append(msg); return "sattler" if self.ok else None


def _alerter(tmp_path, rows, push, now=NOW):
    return BlockedStaleAlerter(tmp_path, reg=None, panes=None, push=push,
                                 bd_blocked=lambda: rows, now=now)


def _detail(*deps):
    return {"dependencies": [
        {"id": bid, "dependency_type": "blocks", "status": status}
        for bid, status in deps
    ]}


def _misstatus(tmp_path, rows, details, push):
    return BlockedMisstatusAlerter(
        tmp_path, reg=None, panes=None, push=push,
        bd_blocked=lambda: rows, bd_show=lambda bid: details[bid], now=NOW)


def test_all_closed_dependencies_are_reported_as_MIS_STATUS_not_age(tmp_path):
    push = _Push()
    rows = [_row("hac0", 18)]
    got = _misstatus(tmp_path, rows, {
        "hac0": _detail(("9p7a1", "closed"), ("b7ve", "closed"))}, push).sweep()
    assert got == ["hac0"]
    assert "MIS-STATUSED" in push.msgs[0] and "EVERY one is closed" in push.msgs[0]
    assert "Clear/correct the status" in push.msgs[0]


def test_a_genuinely_open_blocker_is_NOT_misstatused(tmp_path):
    push = _Push()
    rows = [_row("real", 40)]
    got = _misstatus(tmp_path, rows, {
        "real": _detail(("done", "closed"), ("still-open", "open"))}, push).sweep()
    assert got == [] and push.msgs == []


def test_a_blocked_bead_with_NO_dependencies_is_NOT_misstatused(tmp_path):
    push = _Push()
    rows = [_row("prose-block", 40)]
    got = _misstatus(tmp_path, rows, {"prose-block": _detail()}, push).sweep()
    assert got == [] and push.msgs == []


# --- the clock. This is the part that would silently defeat the whole feature --

def test_age_comes_from_created_NOT_updated(tmp_path):
    """updated_at is reset by ANY touch — a comment, a label, a rehome. The
    17-day bead read ZERO days on updated_at because a roster cut touched it
    that morning. A re-surfacer built on updated_at is silenced by its own
    fleet's housekeeping, which is how the bead stayed quiet."""
    r = _row("degd", days_old=17, updated_days=0)
    assert round(_bead_age_days(r, NOW)) == 17, "age followed updated_at — the silencing bug"


def test_unreadable_created_is_None_not_zero(tmp_path):
    """None means CANNOT TELL and is skipped. Zero would read as 'brand new' and
    suppress the alert forever — the failure mode dressed as a fresh bead."""
    assert _bead_age_days({"created_at": "not-a-date"}, NOW) is None
    assert _bead_age_days({}, NOW) is None


# --- why there is no auto-classification (both candidates REFUTED on live data) --

def test_a_blocked_bead_with_NO_decision_label_is_still_re_surfaced(tmp_path):
    """The first refuted discriminator. 0 of 16 blocked beads on the live store
    carried a decision label — INCLUDING the 17-day P1 security specimen. A
    label-gated alerter is inert, which is worse than absent: it reports zero
    and looks healthy."""
    push = _Push()
    assert _alerter(tmp_path, [_row("degd", 17, labels=["security", "secrets"])],
                    push).sweep() == ["degd"]


def test_a_blocked_bead_whose_BLOCKER_CLOSED_is_re_surfaced_too(tmp_path):
    """The second refuted discriminator, and the more interesting one.
    `dependency_count` counts CLOSED dependencies, so a bead can read
    'blocked on work' while nothing holds it. Measured: one blocked bead's only
    dependency had closed weeks earlier.

    That case is the WORST one, not an excluded one — unblocked in fact, blocked
    on paper, invisible to everything. So it must alert."""
    push = _Push()
    row = _row("7p0", 36, labels=["ci", "deploy"])
    row["dependency_count"] = 1          # ...and that dependency is closed
    assert _alerter(tmp_path, [row], push).sweep() == ["7p0"]
    assert "blocker" in push.msgs[0].lower(), "did not tell the reader to check the blocker"


# --- the sweep ----------------------------------------------------------------

def test_a_stale_human_blocked_bead_is_RE_SURFACED(tmp_path):
    push = _Push()
    got = _alerter(tmp_path, [_row("degd", 17)], push).sweep()
    assert got == ["degd"]
    m = push.msgs[0]
    assert "degd" in m and "17d" in m
    assert "UPPER bound" in m, "must not claim it was blocked for 17d — it claims created"


def test_a_FRESHLY_blocked_bead_is_NOT_shouted_about(tmp_path):
    """The discrimination control. Without it the alerter could fire on
    everything and the test above would still pass. A human may simply not have
    got to it yet — the alarm is for FORGOTTEN, not for pending."""
    push = _Push()
    assert _alerter(tmp_path, [_row("fresh", BLOCKED_MIN_AGE_DAYS - 1)], push).sweep() == []
    assert push.msgs == []


def test_it_does_not_re_nudge_every_pass(tmp_path):
    """tend runs every 5 minutes. A daily nudge is a nudge; a nudge every pass is
    noise about forgotten work, which is how work stays forgotten."""
    push = _Push()
    rows = [_row("degd", 17)]
    assert _alerter(tmp_path, rows, push).sweep() == ["degd"]
    assert _alerter(tmp_path, rows, push).sweep() == [], "re-nudged on the very next pass"
    assert len(push.msgs) == 1
    # ...but a day later it comes back, because it is still stopped.
    later = _alerter(tmp_path, rows, push, now=NOW + 86400 * 1.5).sweep()
    assert later == ["degd"], "went silent again — the exact defect this exists for"


def test_a_FAILED_push_is_not_ledgered(tmp_path):
    """A push that did not land must retry, not be recorded as delivered — the
    silent-success failure this fleet keeps paying for."""
    push = _Push(ok=False)
    assert _alerter(tmp_path, [_row("degd", 17)], push).sweep() == []
    assert _alerter(tmp_path, [_row("degd", 17)], _Push()).sweep() == ["degd"]


def test_it_FAILS_OPEN_when_the_store_cannot_be_read(tmp_path):
    """A broken re-surfacer must never break a tend pass."""
    def boom(): raise RuntimeError("bd is down")
    a = BlockedStaleAlerter(tmp_path, reg=None, panes=None, push=_Push(),
                              bd_blocked=boom, now=NOW)
    assert a.sweep() == []


# --- ordering: the headline case must not be buried (found in production) -----
#
# The first deploy sorted on AGE alone. On the live store that put four 36-40d
# P2s ahead of the 17-day P1 SECURITY bead the whole feature exists for — it came
# SIXTH and was held back by the per-pass cap. Simulation did not show this;
# watching a real pass did.

def test_a_P1_outranks_an_OLDER_P2(tmp_path):
    push = _Push()
    rows = [_row("old_p2", 40, prio=2), _row("degd", 17, prio=1)]
    got = _alerter(tmp_path, rows, push).sweep()
    assert got[0] == "degd", f"buried the P1 behind an older P2: {got}"


def test_age_still_breaks_ties_within_a_priority(tmp_path):
    """The control: priority must not flatten age, or every P2 becomes equal and
    the genuinely forgotten ones stop rising."""
    push = _Push()
    rows = [_row("newer", 10, prio=2), _row("older", 40, prio=2)]
    assert _alerter(tmp_path, rows, push).sweep() == ["older", "newer"]


def test_an_unparseable_priority_sorts_LAST_not_first(tmp_path):
    """A malformed row must not displace a real P1 — an unknown is not an
    emergency."""
    push = _Push()
    bad = _row("bad", 40, prio=None); bad["priority"] = "not-a-number"
    rows = [bad, _row("degd", 17, prio=1)]
    assert _alerter(tmp_path, rows, push).sweep()[0] == "degd"
