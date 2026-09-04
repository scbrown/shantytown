"""The deferral sweeper: lapsed dates and met conditions reach the admin ONCE.

aegis-boj8a2. `deferred` is invisible to every feeder, so a resume condition
written as prose has no mechanism behind it but the author's memory — 115 deferred
beads on the live store, 12 lapsed, three of those P1 and lapsed 26 days.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from shantytown import deferrals


NOW = datetime(2026, 9, 3, 13, 0, tzinfo=timezone.utc)


def _row(bead="aegis-1", *, defer_until=None, notes="", status="deferred", **kw):
    row = {"id": bead, "title": "a title", "status": status,
           "assignee": "dearing", "priority": 1, "notes": notes}
    if defer_until is not None:
        row["defer_until"] = defer_until
    row.update(kw)
    return row


def _iso(delta: timedelta) -> str:
    return (NOW + delta).isoformat().replace("+00:00", "Z")


# --- the field, not the status ------------------------------------------------

def test_it_keys_off_defer_until_under_EITHER_status():
    """The cutover left two representations live and both still exist on the
    board: 115 rows with `status = deferred` and 2 `open` rows carrying a
    defer_until. A sweeper reading the status would miss one shape entirely."""
    rows = [_row("aegis-old", defer_until=_iso(timedelta(days=-3)), status="deferred"),
            _row("aegis-new", defer_until=_iso(timedelta(days=-3)), status="open")]
    found = {f.bead for f in deferrals.evaluate(rows, NOW)}
    assert found == {"aegis-old", "aegis-new"}


def test_a_future_deferral_is_silent():
    """The negative control the bead asks for. A deferral that is doing its job
    must produce no line at all, or the channel is noise."""
    rows = [_row(defer_until=_iso(timedelta(days=+5)))]
    assert deferrals.evaluate(rows, NOW) == []


def test_lapsed_reports_the_age_because_age_is_the_argument():
    rows = [_row(defer_until=_iso(timedelta(days=-26)))]
    f = deferrals.evaluate(rows, NOW)[0]
    assert f.lapsed_days == 26 and "LAPSED 26d ago" in f.render()


def test_a_naive_stamp_is_read_as_UTC_and_never_crashes_the_sweep():
    """br writes Z-suffixed UTC; a hand-edited or pre-cutover row may be naive.
    Comparing naive to aware raises TypeError, which a caller sees as a crashed
    sweep rather than one bad row."""
    naive = (NOW - timedelta(days=2)).replace(tzinfo=None).isoformat()
    assert deferrals.evaluate([_row(defer_until=naive)], NOW)[0].lapsed_days == 2


def test_an_unparseable_or_absent_stamp_never_crashes_the_sweep():
    """SUPERSEDED IN PART by aegis-hm8994, deliberately, and this is why.

    This case used to assert SILENCE for all of these. Silence was the bug: a
    stamp that cannot be parsed can never lapse, so the bead was dropped on every
    pass forever while looking deferred-on-purpose. 101 of 112 live deferrals sat
    in the sibling `absent` state for exactly this reason.

    What survives unchanged is the property the test was really defending — none
    of these may raise. What changed is that being unreadable is now REPORTED
    rather than swallowed, and the two shapes are told apart: an ABSENT stamp
    needs a condition, a MALFORMED one needs its existing field corrected.
    """
    for bad in ("", "soon", "not-a-date", 0, [], {}):
        out = deferrals.evaluate([_row(defer_until=bad)], NOW)
        assert len(out) == 1, f"{bad!r} must be reported, not dropped"
        assert out[0].untestable, f"{bad!r} should read as untestable"
        assert "unparseable" in out[0].untestable
        assert out[0].conditionless is False, "it HAS a stamp; it is malformed"
        assert out[0].met is False, "unreadable must never read as met"
    # the absent case is the conditionless one, and says so differently
    absent = deferrals.evaluate([_row(defer_until=None)], NOW)
    assert len(absent) == 1 and absent[0].conditionless is True
    assert not absent[0].untestable


def test_a_row_that_is_not_a_dict_or_has_no_id_is_skipped():
    """The sweep runs over whatever the tracker returned. One malformed row must
    not cost the other 114."""
    rows = ["nonsense", None, {}, {"id": ""},
            _row("aegis-real", defer_until=_iso(timedelta(days=-1)))]
    assert [f.bead for f in deferrals.evaluate(rows, NOW)] == ["aegis-real"]


# --- structured resume conditions ---------------------------------------------

def test_a_met_closed_condition_is_reported_with_the_bead_it_waited_on():
    """The aegis-6noan specimen: deferred 'until franklin's converge lands', which
    landed the same day, and it sat nine days."""
    rows = [_row(notes="Resume when: closed:aegis-6noan.",
                 defer_until=_iso(timedelta(days=-9)))]
    f = deferrals.evaluate(rows, NOW, is_closed=lambda b: b == "aegis-6noan")[0]
    assert f.met and "CONDITION MET [closed:aegis-6noan]" in f.render()


def test_an_unmet_condition_on_an_unlapsed_deferral_says_nothing():
    rows = [_row(notes="resume_when: closed:aegis-other",
                 defer_until=_iso(timedelta(days=+5)))]
    assert deferrals.evaluate(rows, NOW, is_closed=lambda _b: False) == []


def test_CANNOT_TELL_is_reported_as_untestable_and_never_as_met():
    """The rule the cycle guard keeps for an unreadable tree, applied here. A
    lookup that failed must not send an admin to un-defer work whose blocker may
    still be open — 'we did not look' and 'we looked and it is unmet' are
    different claims and only one of them is safe to act on."""
    rows = [_row(notes="resume_when: closed:aegis-gone")]
    f = deferrals.evaluate(rows, NOW, is_closed=lambda _b: None)[0]
    assert not f.met
    assert "UNTESTABLE" in f.render() and "NOT unmet" in f.render()


def test_no_lookup_at_all_is_also_untestable_not_unmet():
    rows = [_row(notes="resume_when: closed:aegis-x")]
    assert deferrals.evaluate(rows, NOW)[0].untestable


def test_a_vocabulary_kind_this_module_cannot_test_says_so():
    """`sha-serving:` is in the bead's proposal and is not implemented. It must
    report as untested rather than silently read as absent."""
    rows = [_row(notes="resume_when: sha-serving:quipu=abc1234")]
    f = deferrals.evaluate(rows, NOW)[0]
    assert not f.met and "sha-serving:quipu=abc1234" in f.untestable


def test_prose_with_no_marker_never_guesses_a_condition():
    """'Un-defer when Stiwi acts' names no testable thing. A guess here reports
    work as ready that is not, which is worse than the silence it replaces."""
    rows = [_row(notes="Un-defer when Stiwi acts, or if a fifth copy is found.",
                 defer_until=_iso(timedelta(days=-4)))]
    f = deferrals.evaluate(rows, NOW)[0]
    assert f.condition is None and f.lapsed_days == 4      # lapsed only


def test_a_date_condition_is_decided_without_any_lookup():
    rows = [_row(notes=f"resume_when: date:{_iso(timedelta(days=-1))}")]
    assert deferrals.evaluate(rows, NOW)[0].met


def test_the_FIRST_marker_wins_because_notes_accumulate():
    """A bead's notes accumulate edits; the earliest declaration is the one whose
    author was deferring. Tested on the parser, because a row with an unmet,
    testable condition and no lapsed date is correctly SILENT — see the test
    below, which pins that silence."""
    cond = deferrals.parse_condition(
        "resume_when: closed:aegis-first\nlater edit — resume_when: closed:aegis-second")
    assert cond.kind == "closed" and cond.arg == "aegis-first"


def test_an_unmet_condition_with_no_lapsed_date_is_SILENT():
    """The quiet case that made the test above use the parser directly: a
    deferral still waiting on a thing that has not happened is working as
    intended and must produce no line."""
    rows = [_row(notes="resume_when: closed:aegis-other")]
    assert deferrals.evaluate(rows, NOW, is_closed=lambda _b: False) == []


# --- it REPORTS. it never un-defers. ------------------------------------------

def test_the_sweep_mutates_nothing_it_was_given():
    """Item 3 of the bead, asserted directly rather than by reading the code: a
    lapsed deferral may still be the right call."""
    import copy
    rows = [_row(defer_until=_iso(timedelta(days=-30)),
                 notes="resume_when: closed:aegis-6noan")]
    before = copy.deepcopy(rows)
    deferrals.evaluate(rows, NOW, is_closed=lambda _b: True)
    assert rows == before


# --- TRANSITIONS, not state (wu's constraint) ---------------------------------

def test_an_unchanged_deferral_is_reported_once_and_then_never(tmp_path):
    """115 lapsed deferrals printed every tend cycle is a channel the admin mutes
    within a day — the failure grant fixed in dfllto."""
    rows = [_row(defer_until=_iso(timedelta(days=-26)))]
    seen = deferrals.Reported(tmp_path)
    first = deferrals.evaluate(rows, NOW)
    assert seen.unreported(first) == first
    seen.record(first)
    assert seen.unreported(deferrals.evaluate(rows, NOW)) == []


def test_a_CHANGED_state_re_reports(tmp_path):
    """Silence must mean 'nothing new', not 'nothing'. A deferral whose condition
    becomes met is new information about a bead already reported as lapsed."""
    rows = [_row(defer_until=_iso(timedelta(days=-9)),
                 notes="resume_when: closed:aegis-6noan")]
    seen = deferrals.Reported(tmp_path)
    seen.record(deferrals.evaluate(rows, NOW, is_closed=lambda _b: False))
    later = deferrals.evaluate(rows, NOW, is_closed=lambda _b: True)
    assert [f.bead for f in seen.unreported(later)] == ["aegis-1"]


def test_a_deferral_that_stops_lapsing_can_lapse_again_later(tmp_path):
    """Re-deferred to a new date, then that date passes too. Keeping the old key
    would silence exactly the re-lapse the sweeper exists for."""
    seen = deferrals.Reported(tmp_path)
    seen.record(deferrals.evaluate(
        [_row(defer_until=_iso(timedelta(days=-1)))], NOW))
    seen.record(deferrals.evaluate(
        [_row(defer_until=_iso(timedelta(days=+30)))], NOW))       # re-deferred
    again = deferrals.evaluate([_row(defer_until=_iso(timedelta(days=-1)))], NOW)
    assert seen.unreported(again) == again


def test_a_malformed_ledger_degrades_to_reporting_not_to_silence(tmp_path):
    """A ledger that cannot be read must not wedge the sweep, and must fail toward
    saying too much rather than too little."""
    seen = deferrals.Reported(tmp_path)
    seen.path.parent.mkdir(parents=True, exist_ok=True)
    seen.path.write_text("{ this is not json")
    findings = deferrals.evaluate([_row(defer_until=_iso(timedelta(days=-2)))], NOW)
    assert seen.unreported(findings) == findings


# --- the report ---------------------------------------------------------------

def test_the_report_counts_its_own_tail_rather_than_truncating_silently():
    """aegis-bro88: an instrument that reports its own blindness as a clean answer.
    'and 40 more' is information; a quietly short list is a wrong answer."""
    rows = [_row(f"aegis-{i:03}", defer_until=_iso(timedelta(days=-(i + 1))))
            for i in range(30)]
    lines = deferrals.report(deferrals.evaluate(rows, NOW), cap=5)
    assert "30 deferral(s)" in lines[0]
    assert len([ln for ln in lines if ln.startswith("    aegis-")]) == 5
    assert "and 25 more" in lines[-1]


def test_the_report_says_it_changed_nothing():
    rows = [_row(defer_until=_iso(timedelta(days=-2)))]
    assert "nothing was un-deferred" in deferrals.report(
        deferrals.evaluate(rows, NOW))[0].lower()


def test_no_findings_means_no_lines_at_all():
    assert deferrals.report([]) == []


def test_most_lapsed_first_so_the_cap_keeps_the_worst():
    rows = [_row("aegis-a", defer_until=_iso(timedelta(days=-2))),
            _row("aegis-b", defer_until=_iso(timedelta(days=-40)))]
    assert [f.bead for f in deferrals.evaluate(rows, NOW)] == ["aegis-b", "aegis-a"]


# --- the alerter: delivery, fail-open, and never un-deferring ------------------

class _Reg:
    pass


class _Panes:
    pass


def _alerter(tmp_path, rows, **kw):
    from shantytown.notify import DeferralAlerter
    kw.setdefault("push", lambda _r, _p, _m: "sattler")
    kw.setdefault("now", lambda: NOW)
    return DeferralAlerter(tmp_path, _Reg(), _Panes(), read=lambda: rows, **kw)


def test_the_alerter_pushes_a_lapsed_deferral_to_the_admin_once(tmp_path):
    rows = [_row(defer_until=_iso(timedelta(days=-26)))]
    sent = []
    a = _alerter(tmp_path, rows, push=lambda _r, _p, m: sent.append(m) or "sattler")
    assert a.sweep() == ["aegis-1"]
    assert "LAPSED 26d ago" in sent[0]
    assert _alerter(tmp_path, rows).sweep() == [], "it repeated an unchanged state"


def test_an_UNREACHABLE_admin_records_nothing_so_the_finding_is_not_lost(tmp_path):
    """push_to_admin returns None when there is no admin or its pane is gone. If
    the sweep recorded these as said, they would never re-report — the state does
    not change again. A failed push must stay pending, never a silent success."""
    rows = [_row(defer_until=_iso(timedelta(days=-26)))]
    assert _alerter(tmp_path, rows, push=lambda *_a: None).sweep() == []
    assert _alerter(tmp_path, rows).sweep() == ["aegis-1"], "the finding was lost"


def test_the_alerter_fails_open_when_the_store_cannot_be_read(tmp_path):
    """A broken sweep must never break a tend pass — every alerter here fails open."""
    from shantytown.notify import DeferralAlerter
    def boom():
        raise RuntimeError("br exploded")
    notes = []
    a = DeferralAlerter(tmp_path, _Reg(), _Panes(), read=boom,
                        push=lambda *_a: "sattler", log=notes.append)
    assert a.sweep() == []
    assert notes and "could not read the store" in notes[0]


def test_a_condition_lookup_that_raises_is_untestable_not_unmet(tmp_path):
    """The cannot-tell rule, end to end through the alerter."""
    rows = [_row(notes="resume_when: closed:aegis-x",
                 defer_until=_iso(timedelta(days=-3)))]
    sent = []
    def blow_up(_bead):
        raise RuntimeError("show failed")
    a = _alerter(tmp_path, rows, is_closed=blow_up,
                 push=lambda _r, _p, m: sent.append(m) or "sattler")
    # The raise must not escape: one unreadable bead may not cost the other 114.
    assert a.sweep() == ["aegis-1"]
    assert "CONDITION MET" not in sent[0]
    assert "UNTESTABLE" in sent[0] and "NOT unmet" in sent[0]


def test_a_future_deferral_produces_no_push_at_all(tmp_path):
    """The negative control at the integration level: a working deferral is
    silent, or the admin mutes the channel."""
    sent = []
    a = _alerter(tmp_path, [_row(defer_until=_iso(timedelta(days=+9)))],
                 push=lambda _r, _p, m: sent.append(m) or "sattler")
    assert a.sweep() == [] and sent == []


# --- condition-less deferrals: invisible to BOTH paths (aegis-hm8994) ----------

def test_a_deferral_with_NO_condition_at_all_is_reported_not_dropped():
    """101 of 112 live deferrals were in this state, silently, forever.

    No defer_until and no marker means nothing can ever make it lapse or be met,
    so the two `continue` guards could never fire for it — it fell through on
    every pass. Feeders skip status=deferred, so it was on no automated path at
    all. That is the aegis-ozqtz stall with a mechanism behind it.
    """
    out = deferrals.evaluate([_row("aegis-blind")], NOW)
    assert [f.bead for f in out] == ["aegis-blind"]
    assert out[0].conditionless is True
    assert out[0].lapsed_at is None and out[0].met is False


def test_the_conditionless_report_says_what_is_wrong_and_what_fixes_it():
    out = deferrals.evaluate([_row("aegis-blind")], NOW)
    text = "\n".join(deferrals.report(out))
    assert "NO RESUME CONDITION" in text
    assert "resume_when" in text          # names the remedy, not just the fault


def test_a_conditionless_deferral_reports_ONCE_like_every_other_finding(tmp_path):
    """The whole reason report-once exists: 101 of these would otherwise print
    every tend cycle and the admin would mute the channel inside a day."""
    seen = deferrals.Reported(tmp_path)
    first = deferrals.evaluate([_row("aegis-blind")], NOW)
    assert seen.unreported(first)
    seen.record(first)
    assert not seen.unreported(deferrals.evaluate([_row("aegis-blind")], NOW))


def test_attaching_a_condition_changes_the_state_so_it_reports_again(tmp_path):
    seen = deferrals.Reported(tmp_path)
    seen.record(deferrals.evaluate([_row("aegis-blind")], NOW))
    fixed = deferrals.evaluate(
        [_row("aegis-blind", defer_until=_iso(timedelta(days=-1)))], NOW)
    assert seen.unreported(fixed), "repairing it must not stay silent"
    assert fixed[0].conditionless is False and fixed[0].lapsed_at is not None


def test_a_FUTURE_defer_until_with_no_marker_is_NOT_conditionless():
    """The negative control that stops this becoming an always-firing channel.

    A bead deferred to next week has a perfectly good condition — the date. If
    the check keyed on the missing MARKER alone it would drag every ordinary
    future deferral into the blind set and the block would never be empty.
    """
    out = deferrals.evaluate(
        [_row("aegis-future", defer_until=_iso(timedelta(days=7)))], NOW)
    assert out == []


def test_the_blind_set_is_reported_SEPARATELY_and_never_buries_the_actionable():
    """aegis-1gy64 reproduced inside its own fix, if these were interleaved.

    With 101 blind against 1 lapsed, a merged most-lapsed-first list would push
    the one bead that needs a ruling below a wall of parked ones.
    """
    rows = [_row(f"aegis-blind{i}") for i in range(101)]
    rows.append(_row("aegis-lapsed", defer_until=_iso(timedelta(days=-3))))
    lines = deferrals.report(deferrals.evaluate(rows, NOW))
    text = "\n".join(lines)
    # the actionable one is named in full, above the blind block
    assert "aegis-lapsed" in text
    assert text.index("aegis-lapsed") < text.index("NO RESUME CONDITION")
    assert "1 deferral(s) need a ruling" in text
    assert "101 deferral(s) have NO RESUME CONDITION" in text
    # and the blind block is BOUNDED — the count is the information, not 101 lines
    assert len(lines) < 20
    assert "and 95 more" in text


def test_the_blind_block_leads_with_the_worst_priority():
    rows = [_row("aegis-p3", priority=3), _row("aegis-p1", priority=1)]
    text = "\n".join(deferrals.report(deferrals.evaluate(rows, NOW)))
    assert text.index("aegis-p1") < text.index("aegis-p3")
