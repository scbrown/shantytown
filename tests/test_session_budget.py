"""The session ceiling — the axis the usage governor cannot see (aegis-xxae9).

WHAT THIS SUITE IS ACTUALLY GUARDING. The incident was not a wrong number
anywhere; it was that no number existed. A single session ran ~6 hours, took four
haul items back to back, deployed binaries to production three times and
restarted two live services, while the usage governor sat correctly below its
first tier the entire time. So the tests below are mostly about the SHAPE of the
control rather than its thresholds: that it counts a stretch rather than a
harness session, that it stops instead of recycling, that it stops ONCE, and that
every failure path leaves the crew running.

THE THREE WAYS THIS FEATURE COULD BE WORSE THAN NOTHING, each with a test:

  1. it never fires        — ships inert, like the usage governor did for weeks
  2. it never stops firing — blocks the stop it exists to cause, wedging a worker
  3. it fires on a lie     — a blind counter reading 0 as "nothing happened"

Numbers 2 and 3 are why `blocks_once` and the signal-lost tests exist, and they
are the ones a reviewer should look at first: a control that traps every worker
in the fleet at its own stop is a far more expensive bug than the one it fixes.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from shantytown import session_budget as sb
from shantytown import stats
from shantytown.feed_check import haul_feed_message


# --- the store, built by hand so the counters are unambiguous ---------------

def _store(root: Path, rows):
    """rows: (ts, agent, kind, risk) — the three columns the ceiling reads."""
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "stats.sqlite")
    conn.executescript(stats._SCHEMA)
    conn.executemany(
        "INSERT INTO events(ts, agent, kind, session, risk) VALUES (?,?,?,?,?)",
        [(ts, a, k, "sess-1", r) for ts, a, k, r in rows])
    conn.commit()
    conn.close()


ARMED = sb.Limits(max_hours=3.0, max_items=4, max_risk=2)


# --- parse ------------------------------------------------------------------

def test_no_table_is_off_and_off_never_trips():
    """The default is off BY OMISSION, matching [governor]: no separate enable
    key to forget. An unarmed budget must not stop anyone no matter the spend."""
    lim = sb.parse({})
    assert not lim.active
    assert sb.verdict(lim, sb.Spend(hours=99, items=99, risk=99)) is None


def test_declaring_one_limit_is_the_enabling_act():
    assert sb.parse({"max_hours": 3}).active
    assert sb.parse({"max_items": 4}).active
    assert sb.parse({"max_risk": 2}).active


@pytest.mark.parametrize("tbl", [{"max_items": 0}, {"max_hours": 0},
                                 {"max_risk": -1}, {"max_hours": -0.5}])
def test_a_zero_ceiling_is_REFUSED_not_accepted(tbl):
    """`max_items = 0` reads as "no items allowed" and would wedge every worker
    in the fleet on its first advance, from one typo. Off is spelled by omitting
    the key, so a 0 is always a mistake and is refused where it is cheap."""
    with pytest.raises(sb.BudgetError, match="zero or less"):
        sb.parse(tbl)


def test_unknown_key_and_bad_signal_lost_are_named():
    with pytest.raises(sb.BudgetError, match="max_hours"):
        sb.parse({"max_hourz": 3})
    with pytest.raises(sb.BudgetError, match="on_signal_lost"):
        sb.parse({"max_hours": 3, "on_signal_lost": "panic"})


def test_config_wires_the_table_and_names_the_file(tmp_path):
    from shantytown import config
    (tmp_path / "shantytown.toml").write_text(
        "[session_budget]\nmax_hours = 2.5\nmax_items = 3\n", encoding="utf-8")
    cfg, err = config.load_or_default(tmp_path)
    assert err is None and cfg.session_budget.max_hours == 2.5
    (tmp_path / "shantytown.toml").write_text(
        "[session_budget]\nmax_items = 0\n", encoding="utf-8")
    _cfg, err = config.load_or_default(tmp_path)
    assert err and "shantytown.toml" in err        # the file, not a bare complaint


# --- the stretch, not the harness session -----------------------------------

def test_the_stretch_spans_a_clear_but_not_a_real_break():
    """THE LOAD-BEARING TEST. `/clear` starts a new harness session id, and the
    haul's own context handoff instructs a /clear and then resumes — so a budget
    keyed on session id would reset at exactly the moment the haul hands over,
    silently, while appearing to work. Measured on the incident's agent: session
    id spans 3.65h where the work spans 5.08h."""
    now = 10_000_000.0
    # five hours of work with a 20-minute pause in the middle (a /clear)
    rows = [now - 5 * 3600 + i * 300 for i in range(12)]          # first hour
    rows += [now - 3.7 * 3600 + i * 300 for i in range(44)]       # after the pause
    start = sb.stretch_start(rows, now)
    assert (now - start) / 3600 == pytest.approx(5.0, abs=0.05)


def test_a_real_break_starts_a_NEW_stretch():
    """A 78-minute gap is somebody coming back later, not a long session. The
    fresh run must not inherit the old one's spend."""
    now = 10_000_000.0
    rows = [now - 6 * 3600 + i * 300 for i in range(12)]          # old run
    rows += [now - 900 + i * 300 for i in range(4)]               # fresh run
    start = sb.stretch_start(rows, now)
    assert (now - start) / 3600 == pytest.approx(0.25, abs=0.02)


def test_read_spend_counts_items_and_risk_over_the_stretch(tmp_path):
    now = time.time()
    # Ordinary working density (an event every few minutes) — anything sparser
    # than the 45-minute gap would be a different stretch, which is the next
    # test's job, not this one's.
    rows = [(now - 4 * 3600 + i * 300, "billy", "tool", None) for i in range(48)]
    rows += [(now - 3 * 3600, "billy", "haul", None),
             (now - 2 * 3600, "billy", "tool", "deploy"),
             (now - 1.5 * 3600, "billy", "haul", None),
             (now - 1 * 3600, "billy", "tool", "restart"),
             (now - 0.5 * 3600, "billy", "tool", "deploy"),
             (now - 60, "billy", "tool", None)]
    _store(tmp_path, rows)
    sp = sb.read_spend(tmp_path, "billy", now)
    assert not sp.signal_lost
    assert sp.hours == pytest.approx(4.0, abs=0.05)
    assert sp.items == 2
    assert sp.risk == 3 and sp.risk_kinds == {"deploy": 2, "restart": 1}


def test_one_agents_spend_is_not_anothers(tmp_path):
    now = time.time()
    _store(tmp_path, [(now - 3600, "billy", "haul", None),
                      (now - 3600, "billy", "tool", "deploy"),
                      (now - 60, "franklin", "tool", None)])
    assert sb.read_spend(tmp_path, "franklin", now).risk == 0
    assert sb.read_spend(tmp_path, "billy", now).risk == 1


# --- the verdict ------------------------------------------------------------

@pytest.mark.parametrize("spend,measure", [
    (sb.Spend(hours=3.1, items=1, risk=0), "hours"),
    (sb.Spend(hours=0.5, items=4, risk=0), "items"),
    (sb.Spend(hours=0.5, items=1, risk=2), "risk"),
])
def test_any_one_measure_trips_it(spend, measure):
    """Not weighted into a composite score: a single number cannot tell an
    operator WHICH ceiling to change, and the report has to name what was spent."""
    c = sb.verdict(ARMED, spend)
    assert c is not None and c.measure == measure


def test_under_every_ceiling_is_wide_open():
    assert sb.verdict(ARMED, sb.Spend(hours=2.9, items=3, risk=1)) is None


def test_the_MOST_exceeded_measure_is_the_one_reported():
    """Reporting "4 items, ceiling 4" to a session that is also nine hours into a
    three-hour budget sends the operator to tune the wrong number."""
    c = sb.verdict(ARMED, sb.Spend(hours=9.0, items=4, risk=0))
    assert c.measure == "hours"


def test_risk_has_a_TIGHTER_ceiling_than_items(tmp_path):
    """Bead item 2: production actions must not be budgeted like edits. Two
    deploys is over; two haul items of ordinary work is not."""
    assert sb.verdict(ARMED, sb.Spend(items=2)) is None
    assert sb.verdict(ARMED, sb.Spend(risk=2)) is not None


def test_the_label_names_the_measure_and_both_numbers():
    c = sb.verdict(ARMED, sb.Spend(hours=0.5, items=1, risk=3))
    assert "3 production actions" in c.label() and "ceiling is 2" in c.label()


# --- signal lost: never a zero, never a stop --------------------------------

def test_signal_lost_never_trips_the_ceiling():
    """A blind counter must not read as "nothing happened". The usage governor
    was armed and blind for a whole session and survived only because blindness
    ALARMED rather than reporting a healthy low number (aegis-jrax3)."""
    assert sb.verdict(ARMED, sb.Spend(signal_lost=True)) is None


def test_signal_lost_SAYS_SO_rather_than_going_quiet():
    note = sb.signal_lost_note(ARMED, sb.Spend(signal_lost=True), "billy")
    assert "SIGNAL LOST" in note and "UNMEASURED" in note and "billy" in note
    assert sb.signal_lost_note(sb.Limits(), sb.Spend(signal_lost=True), "x") == ""


def test_a_stale_last_event_is_signal_lost_not_a_long_session(tmp_path):
    """Otherwise a fresh session's first advance is stopped over a dead run's
    elapsed time — the ceiling firing on work the agent did not do."""
    now = time.time()
    _store(tmp_path, [(now - 40 * 3600, "billy", "tool", None)])
    assert sb.read_spend(tmp_path, "billy", now).signal_lost


def test_missing_store_and_unreadable_root_fail_OPEN(tmp_path):
    assert sb.read_spend(tmp_path / "nope", "billy").signal_lost
    (tmp_path / "stats.sqlite").write_text("not a database", encoding="utf-8")
    assert sb.read_spend(tmp_path, "billy").signal_lost
    lim, sp, c = sb.gate(tmp_path, "billy")
    assert c is None                              # never a stop from a broken read


def test_gate_is_open_when_the_config_will_not_parse(tmp_path):
    """A config that will not parse must not stop the crew: the cost of a missed
    ceiling is a long session, the cost of a wrong one is a wedged fleet."""
    (tmp_path / "shantytown.toml").write_text("[session_budget]\nmax_items = 0\n",
                                              encoding="utf-8")
    _lim, _sp, c = sb.gate(tmp_path, "billy")
    assert c is None


# --- block once, so the stop can actually happen ----------------------------

def test_the_ceiling_blocks_ONCE_per_stretch(tmp_path):
    """A Stop hook that blocks while the ceiling is over is an infinite loop: the
    agent reports, stops, the hook fires, the ceiling is still over. The control
    meant to end the run would be what prevents it ending."""
    spend = sb.Spend(started=1000.0, hours=9.0)
    assert not sb.already_reported(tmp_path, "billy", spend)
    sb.mark_reported(tmp_path, "billy", spend)
    assert sb.already_reported(tmp_path, "billy", spend)


def test_a_NEW_stretch_is_not_silenced_by_the_old_marker(tmp_path):
    sb.mark_reported(tmp_path, "billy", sb.Spend(started=1000.0))
    assert not sb.already_reported(tmp_path, "billy", sb.Spend(started=90_000.0))


def test_the_stop_message_does_not_hand_over_the_next_bead():
    """Naming it would give away the exact thing the ceiling is withholding, and
    an agent told "stop; your next item is aegis-xyz" will do the item."""
    c = sb.verdict(ARMED, sb.Spend(hours=7.0, items=4, risk=3))
    msg = sb.stop_message(c, next_bead="aegis-1jfa0")
    assert "SESSION CEILING" in msg
    assert "push" in msg and "bead trail" in msg and "stop cleanly" in msg
    assert "finish the thought" in msg           # names the actual failure mode
    assert "next on your haul" not in msg


# --- the haul's own sentences (items 3 and 4) -------------------------------

def test_the_queue_is_yours_line_carries_the_headroom():
    """It was read as standing authority to keep going, four items deep. It
    cannot be deleted — a self-feeding queue does have to say nobody is coming —
    so it now carries a number."""
    hr = sb.headroom(ARMED, sb.Spend(hours=1.0, items=1, risk=0))
    msg = haul_feed_message("aegis-1", "t", 2, headroom=hr)
    assert "Yours to work —" in msg
    assert "2.0h" in msg and "3 item(s)" in msg


def test_an_unarmed_deployment_gets_the_ORIGINAL_sentence():
    """A caveat with no number behind it is noise to learn to skip."""
    msg = haul_feed_message("aegis-1", "t", 2, headroom="")
    assert "Yours to work." in msg
    assert "session budget" not in msg


def test_a_re_serve_is_visibly_a_re_serve():
    """Being handed the same bead back reads as an instruction to continue; it is
    actually just the re-serve rule and carries no intent at all."""
    plain = haul_feed_message("aegis-1", "t", 2)
    again = haul_feed_message("aegis-1", "t", 2, repeats=2)
    assert "SAME bead" not in plain
    assert "SAME bead" in again and "twice" in again
    assert "not a verdict" in again


def test_times_served_counts_only_this_agents_repeats_in_this_stretch(tmp_path):
    now = time.time()
    sb.record_item(tmp_path, "billy", "s", "aegis-1", now - 3600)
    sb.record_item(tmp_path, "billy", "s", "aegis-1", now - 1800)
    sb.record_item(tmp_path, "billy", "s", "aegis-2", now - 900)
    sb.record_item(tmp_path, "franklin", "s", "aegis-1", now - 900)
    assert sb.times_served(tmp_path, "billy", "aegis-1", now - 7200) == 2
    assert sb.times_served(tmp_path, "billy", "aegis-2", now - 7200) == 1
    assert sb.times_served(tmp_path, "billy", "aegis-1", now - 2000) == 1  # stretch
    assert sb.times_served(tmp_path, "nobody", "aegis-1", 0) == 0


def test_headroom_reports_signal_loss_rather_than_a_comfortable_number():
    assert "SIGNAL LOST" in sb.headroom(ARMED, sb.Spend(signal_lost=True))
    assert sb.headroom(sb.Limits(), sb.Spend()) == ""


# --- end to end: the incident's own shape -----------------------------------

def test_the_incident_would_have_been_STOPPED(tmp_path):
    """The measured run: ~6h, four haul items, three deploys, two restarts. The
    usage governor sat below its first tier throughout and was right to. This is
    the whole point of the second axis, so it is asserted directly."""
    now = time.time()
    rows = [(now - 6 * 3600 + i * 120, "billy", "tool", None) for i in range(170)]
    rows += [(now - 5.5 * 3600, "billy", "haul", None),
             (now - 4 * 3600, "billy", "haul", None),
             (now - 2.5 * 3600, "billy", "haul", None),
             (now - 1 * 3600, "billy", "haul", None),
             (now - 5 * 3600, "billy", "tool", "deploy"),
             (now - 3 * 3600, "billy", "tool", "deploy"),
             (now - 2 * 3600, "billy", "tool", "deploy"),
             (now - 2.9 * 3600, "billy", "tool", "restart"),
             (now - 1.9 * 3600, "billy", "tool", "restart")]
    _store(tmp_path, rows)
    spend = sb.read_spend(tmp_path, "billy", now)
    assert spend.items == 4 and spend.risk == 5
    assert spend.hours == pytest.approx(6.0, abs=0.1)
    c = sb.verdict(ARMED, spend)
    assert c is not None
    # and it trips WELL before the end — the first deploy alone is 5h from the end
    early = sb.read_spend(tmp_path, "billy", now - 4.5 * 3600)
    assert sb.verdict(ARMED, early) is not None


# --- the ceiling could not hold for an agent that OBEYED it (aegis-hqbwci) ---
#
# THE SHAPE OF THIS BUG, and why the tests below are mostly about the CONTROL
# rather than the fix: the ceiling's own instruction is "stop cleanly" and "do
# NOT pick up more work". Complying means going idle, and going idle past
# STRETCH_GAP_S is precisely what rolled the stretch and restored a full budget.
# Obedience was the trigger for the reset. So the first test is the incident,
# and the SECOND is the one a reviewer should look at hardest — a fix that never
# releases the hold would pass the first test perfectly and stop the fleet.

def _store_sessions(root: Path, rows):
    """rows: (ts, agent, kind, session, risk). `_store` pins one session id; the
    hold is keyed on session identity, so these tests have to vary it."""
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "stats.sqlite")
    conn.executescript(stats._SCHEMA)
    conn.executemany(
        "INSERT INTO events(ts, agent, kind, session, risk) VALUES (?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()


def _busy(a: float, b: float, session: str):
    """Tool events every two minutes from `a` to `b`. Dense on purpose: any gap
    wider than STRETCH_GAP_S inside the run would roll the stretch by itself and
    the test would pass for the wrong reason."""
    n = max(2, int((b - a) // 120))
    return [(a + i * (b - a) / n, "billy", "tool", session, None)
            for i in range(n + 1)]


def _armed(root: Path):
    (root / "shantytown.toml").write_text(
        "[session_budget]\nmax_hours = 4.0\nmax_items = 4\nmax_risk = 3\n",
        encoding="utf-8")


IDLE_MIN = 81.3          # arnold's measured gap, 23:12:13 -> 00:33:29


def _ceilinged(root: Path, trip_at: float, session: str = "sess-A"):
    """The run up to the moment the ceiling trips: four haul items over a busy
    4.5 hours. Stops there, so the trip can be measured the way the live gate
    measured it — before the idle gap exists in the store at all."""
    _armed(root)
    # 3.5 hours, four items: UNDER max_hours=4.0 on purpose, so the measure that
    # trips is unambiguously `items` and the assertions below are about the thing
    # they say they are about.
    rows = _busy(trip_at - 3.5 * 3600, trip_at, session)
    rows += [(trip_at - (3.0 - i) * 3600, "billy", "haul", session, None)
             for i in range(4)]
    _store_sessions(root, rows)


def _stir(root: Path, ts: float, session: str = "sess-A"):
    """One event after the idle gap — the agent waking up in the SAME session."""
    conn = sqlite3.connect(root / "stats.sqlite")
    conn.execute("INSERT INTO events(ts, agent, kind, session) VALUES (?,?,?,?)",
                 (ts, "billy", "tool", session))
    conn.commit()
    conn.close()


def _trip_and_comply(root: Path, now: float, session: str = "sess-A"):
    """The whole incident: ceiling, marker, compliant idle, wake. Returns the
    Ceiling that was recorded at the trip."""
    trip_at = now - IDLE_MIN * 60.0
    _ceilinged(root, trip_at, session)
    trip = sb.read_spend(root, "billy", trip_at)
    ceiling = sb.verdict(sb.limits_for(root), trip)
    assert ceiling is not None, "the run must actually trip, or nothing is proven"
    sb.mark_reported(root, "billy", trip, ceiling)
    _stir(root, now, session)
    return ceiling


def test_the_ceiling_TRIPS_on_the_measured_run(tmp_path):
    """Control for the two tests below: before the idle gap, this run is over."""
    now = time.time()
    _ceilinged(tmp_path, now)
    _lim, spend, c = sb.gate(tmp_path, "billy", now)
    assert c is not None and not c.held and spend.items == 4
    assert c.measure == "items"


def test_OBEYING_the_ceiling_no_longer_un_trips_it(tmp_path):
    """THE BUG, in one test. The agent was told to stop, complied, and idled
    81.3 minutes — the measured gap. That rolls the stretch, so every counter
    the gate reads is back to zero and `verdict` alone sees a clean session."""
    now = time.time()
    _trip_and_comply(tmp_path, now)
    limits, spend, c = sb.gate(tmp_path, "billy", now)
    assert spend.items == 0                      # the stretch really did roll
    assert sb.verdict(limits, spend) is None     # ... and the live read is clean
    assert c is not None and c.held              # ... and the ceiling STILL holds
    assert c.measure == "items" and c.measured == 4.0


def test_a_GENUINELY_FRESH_agent_still_gets_its_full_budget(tmp_path):
    """THE CONTROL, and the more important half. A hold that never releases is
    not a ceiling, it is a fleet-wide stop with extra steps. Same store, same
    marker, same idle gap — only the session id differs, which is exactly the
    fact that separates 'complied and waited' from 'has done nothing today'."""
    now = time.time()
    _trip_and_comply(tmp_path, now)
    # the agent was relaunched: its newest event carries a NEW session id
    _store_sessions(tmp_path / "fresh", [(now, "billy", "tool", "sess-B", None)])
    (tmp_path / "fresh" / "shantytown.toml").write_text(
        (tmp_path / "shantytown.toml").read_text(), encoding="utf-8")
    (tmp_path / "fresh" / "session_budget").mkdir(parents=True, exist_ok=True)
    (tmp_path / "fresh" / "session_budget" / "billy.json").write_text(
        (tmp_path / "session_budget" / "billy.json").read_text(), encoding="utf-8")
    _lim, spend, c = sb.gate(tmp_path / "fresh", "billy", now)
    assert spend.session == "sess-B"
    assert c is None                             # wide open, correctly


def test_the_held_message_SAYS_it_is_held_and_quotes_the_TRIP(tmp_path):
    """A held ceiling reporting the rolled stretch's zeros would tell the agent
    'you have run 0.0h, 0 items, and I am not feeding you' — which reads as a
    broken counter, and invites exactly the wait that used to clear it."""
    now = time.time()
    _trip_and_comply(tmp_path, now)
    _lim, _sp, c = sb.gate(tmp_path, "billy", now)
    msg = sb.stop_message(c)
    assert "4 haul items" in msg                 # the trip's number, not 0
    assert "0 item(s)" not in msg
    assert "Idling does not clear it" in msg
    assert "THIS SESSION" in msg


def test_told_once_survives_the_idle_gap_it_causes(tmp_path):
    """Without this the complying agent is re-told the same thing on every
    45-minute boundary for the rest of its session — noise aimed at a worker
    that already did as it was asked."""
    trip = sb.Spend(session="sess-A", started=1000.0, hours=9.0)
    sb.mark_reported(tmp_path, "billy", trip)
    rolled = sb.Spend(session="sess-A", started=90_000.0)
    assert sb.already_reported(tmp_path, "billy", rolled)
    # ... and a different session is NOT silenced by it
    assert not sb.already_reported(
        tmp_path, "billy", sb.Spend(session="sess-B", started=90_000.0))


@pytest.mark.parametrize("why,mutate", [
    ("a marker written before this existed cannot name what tripped",
     lambda d: d.pop("ceiling")),
    ("an empty session id is UNKNOWN, and unknown never holds a worker",
     lambda d: d.update(session="")),
    ("a marker from some other session says nothing about this one",
     lambda d: d.update(session="sess-OTHER")),
])
def test_the_hold_FAILS_OPEN_on_anything_it_cannot_establish(tmp_path, why, mutate):
    import json
    now = time.time()
    _trip_and_comply(tmp_path, now)
    m = tmp_path / "session_budget" / "billy.json"
    d = json.loads(m.read_text())
    mutate(d)
    m.write_text(json.dumps(d), encoding="utf-8")
    _lim, _sp, c = sb.gate(tmp_path, "billy", now)
    assert c is None, why


def test_RAISING_the_limit_lifts_the_hold_without_deleting_a_file(tmp_path):
    """An operator who decides four items was too tight must have a way out that
    is not 'find the json'. A stale marker must not pin a worker after the
    config that justified it has been changed."""
    now = time.time()
    _trip_and_comply(tmp_path, now)
    assert sb.gate(tmp_path, "billy", now)[2] is not None
    (tmp_path / "shantytown.toml").write_text(
        "[session_budget]\nmax_hours = 4.0\nmax_items = 8\nmax_risk = 3\n",
        encoding="utf-8")
    assert sb.gate(tmp_path, "billy", now)[2] is None


def test_a_held_ceiling_never_outranks_a_LIVE_one(tmp_path):
    """The held numbers are the trip's. A session that is over budget RIGHT NOW
    must report today's, or the operator tunes against a stale measurement."""
    now = time.time()
    _armed(tmp_path)
    rows = _busy(now - 4.5 * 3600, now, "sess-A")
    rows += [(now - (4.0 - 0.6 * i) * 3600, "billy", "haul", "sess-A", None)
             for i in range(6)]
    _store_sessions(tmp_path, rows)
    _lim, spend, c = sb.gate(tmp_path, "billy", now)
    sb.mark_reported(tmp_path, "billy", sb.Spend(session="sess-A", started=1.0,
                                                 hours=9.0, items=4),
                     sb.Ceiling("items", 4.0, 4.0, sb.Spend(items=4), held=False))
    _lim, _sp, c = sb.gate(tmp_path, "billy", now)
    assert c is not None and not c.held and c.measured == 6.0
