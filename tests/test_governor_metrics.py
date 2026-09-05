"""governor_metrics — the decisions and their inputs as Prometheus series.

The load-bearing properties, in the order they cost something to get wrong:

  * `test_cannot_tell_omits_the_recommendation` — CANNOT TELL is not HOLD. If
    `advice=None` published 0, an unreadable tracker and a deliberate hold would
    be the same point on every dashboard.
  * `test_unrated_window_says_so` — an unrated window exports `window_rated 0`
    rather than vanishing. Absence must not read as healthy.
  * `test_the_ratio_ships_with_its_own_arithmetic` — a ratio is uncheckable
    without the two numbers it came from (`Pacing`'s own constraint).
  * `test_type_lines_are_emitted_once` — two lanes in one body must not produce a
    duplicate `# TYPE`, which the gateway rejects wholesale.
  * `test_publish_never_raises` — telemetry hangs off the only respawn path
    there is; it may log and it may fail, it may not take the pass down.
  * `test_counters_survive_a_new_process` — st is invocation-based, so a counter
    that is not on disk is a gauge stuck at 1.
"""
from __future__ import annotations

import json

import pytest

from shantytown import governor as gov_mod
from shantytown import governor_metrics as gm
from shantytown import governor_utilization as util


NOW = 1_000_000.0
WEEK = gov_mod.WINDOW_LENGTH_S[gov_mod.SEVEN_DAY]
FIVE = gov_mod.WINDOW_LENGTH_S[gov_mod.FIVE_HOUR]


def _reading(pct, *, elapsed, length, ok=True, reset=True):
    return gov_mod.Reading(pct=pct, at=NOW, ok=ok, source="stub",
                           reset_at=(NOW + length * (1.0 - elapsed)) if reset
                           else None)


def _policy(*, paces=(), tiers=()):
    return gov_mod.Policy(tiers=tuple(tiers), paces=tuple(paces))


def _assess(**kw):
    kw.setdefault("harness", "base")
    kw.setdefault("policy", _policy(paces=(gov_mod.Pace(window=gov_mod.SEVEN_DAY,
                                                        ratio=1.15),)))
    kw.setdefault("now", NOW)
    kw.setdefault("cap", 9)
    kw.setdefault("live", 3)
    kw.setdefault("ready", 5)
    return util.assess(**kw)


def _samples(body: str) -> dict[str, float]:
    """{'name{labels}': value} — the exposition parsed the way a reader reads it."""
    out = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        out[name] = float(value)
    return out


def _lane(**kw):
    kw.setdefault("lane", "base")
    kw.setdefault("readings", {})
    kw.setdefault("live", 3)
    kw.setdefault("blocked", 0)
    kw.setdefault("setpoint_delta", None)
    kw.setdefault("verdict", None)
    kw.setdefault("utilization", None)
    return kw


# --- the three-valued answer ------------------------------------------------

def test_cannot_tell_omits_the_recommendation():
    """`advice=None` must NOT publish 0. 0 means hold, which is a decision."""
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                         length=WEEK)},
                   ready=None)
    assert seen.advice is None                      # the precondition
    s = _samples(gm.render([_lane(utilization=seen)], now=NOW))
    assert 'st_governor_recommendation{lane="base"}' not in s
    assert s['st_governor_recommendation_known{lane="base"}'] == 0


def test_a_hold_publishes_zero_and_says_it_is_known():
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                         length=WEEK)},
                   live=9, cap=9)
    assert seen.advice == 0 and seen.cause == "at-cap"
    s = _samples(gm.render([_lane(utilization=seen)], now=NOW))
    assert s['st_governor_recommendation{lane="base"}'] == 0
    assert s['st_governor_recommendation_known{lane="base"}'] == 1
    # The CAUSE is a label, so a hold at cap and a hold over pace are different
    # points rather than one indistinguishable zero.
    assert s['st_governor_cause{lane="base",cause="at-cap"}'] == 1
    assert s['st_governor_cause{lane="base",cause="over-pace"}'] == 0


def test_every_known_cause_is_exported_so_a_query_never_returns_no_data():
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                         length=WEEK)})
    s = _samples(gm.render([_lane(utilization=seen)], now=NOW))
    for cause in gm.CAUSES:
        assert f'st_governor_cause{{lane="base",cause="{cause}"}}' in s


def test_an_unknown_cause_is_still_exported():
    """A new `Utilization.cause` must never be silently dropped by this
    module's vocabulary going stale."""
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                         length=WEEK)})
    forged = util.Utilization(**{**seen.__dict__, "cause": "brand-new-cause"})
    s = _samples(gm.render([_lane(utilization=forged)], now=NOW))
    assert s['st_governor_cause{lane="base",cause="brand-new-cause"}'] == 1


# --- absence must not read as healthy ---------------------------------------

def test_unrated_window_says_so():
    """A window with no reset timestamp cannot be rated. It must export
    `window_rated 0`, not simply omit its ratio — the `WindowUse.render`
    constraint, one layer out."""
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                         length=WEEK,
                                                         reset=False)})
    s = _samples(gm.render([_lane(utilization=seen)], now=NOW))
    lb = 'lane="base",window="seven_day"'
    assert s[f'st_governor_window_rated{{{lb}}}'] == 0
    assert f'st_governor_utilization_ratio{{{lb}}}' not in s
    # ...and the raw percentage is still there, because "we read 52% and could
    # not rate it" is far more actionable than silence.
    assert s[f'st_governor_used_percent{{{lb}}}'] == 52


def test_a_blind_lane_is_exported_rather_than_skipped():
    """Signal lost means NO utilization object at all. The lane must still appear,
    or a governor that cannot see the number looks like one nobody configured."""
    verdict = gov_mod.Verdict(reading=gov_mod.Reading(error="probe down"),
                              signal_lost=True)
    readings = {gov_mod.FIVE_HOUR: gov_mod.Reading(pct=None, ok=False,
                                                   error="probe down")}
    s = _samples(gm.render([_lane(verdict=verdict, utilization=None,
                                  readings=readings)], now=NOW))
    assert s['st_governor_signal_lost{lane="base"}'] == 1
    assert s['st_governor_window_rated{lane="base",window="five_hour"}'] == 0
    assert s['st_governor_reading_ok{lane="base",window="five_hour"}'] == 0


def test_no_priority_floor_is_omitted_not_zero():
    """0 is a REAL floor (P0 only) and the strictest there is. A sentinel here
    would render 'no restriction' as 'the tightest possible restriction'."""
    s = _samples(gm.render([_lane(verdict=gov_mod.Verdict(
        reading=gov_mod.Reading(pct=10.0), pct=10.0))], now=NOW))
    assert 'st_governor_priority_floor{lane="base"}' not in s

    tier = gov_mod.Tier(at=65, min_priority=0, window=gov_mod.SEVEN_DAY)
    s = _samples(gm.render([_lane(verdict=gov_mod.Verdict(
        reading=gov_mod.Reading(pct=65.0), pct=65.0, tier=tier,
        engaged=(tier,)))], now=NOW))
    assert s['st_governor_priority_floor{lane="base"}'] == 0


def test_agent_counts_absent_when_the_read_failed():
    """None is COULD NOT LOOK. An empty mapping would publish a fleet of zero
    agents, which is a real and alarming state."""
    assert "st_agents" not in gm.render([_lane()], agents=None, now=NOW)
    body = gm.render([_lane()], now=NOW, agents={
        "state": {("claude", "up"): 4, ("codex", "down"): 2},
        "work": {("claude", "busy"): 2},
        "stopped": {"claude": 1}})
    s = _samples(body)
    assert s['st_agents{harness="claude",state="up"}'] == 4
    assert s['st_agents{harness="codex",state="down"}'] == 2
    assert s['st_agents_work{harness="claude",work="busy"}'] == 2
    assert s['st_agents_stopped_deliberate{harness="claude"}'] == 1


# --- a ratio ships with its arithmetic --------------------------------------

def test_the_ratio_ships_with_its_own_arithmetic():
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                         length=WEEK)})
    s = _samples(gm.render([_lane(utilization=seen)], now=NOW))
    lb = 'lane="base",window="seven_day"'
    used = s[f'st_governor_used_percent{{{lb}}}']
    elapsed = s[f'st_governor_elapsed_percent{{{lb}}}']
    ratio = s[f'st_governor_utilization_ratio{{{lb}}}']
    # The whole point: a reader can re-derive the ratio and catch an arithmetic
    # bug from the exposition alone, which `0.90x` on its own can never allow.
    assert ratio == pytest.approx(used / elapsed, rel=1e-6)
    assert s[f'st_governor_pace_bound{{{lb}}}'] == pytest.approx(1.15)
    assert s[f'st_governor_pace_bound_declared{{{lb}}}'] == 1


def test_budget_balance_is_points_to_the_drain_ceiling():
    """Stiwi's 'balance': what is still spendable before the drain stops us."""
    tiers = (gov_mod.Tier(at=90, action=gov_mod.DRAIN, window=gov_mod.SEVEN_DAY),)
    seen = _assess(policy=_policy(tiers=tiers,
                                  paces=(gov_mod.Pace(window=gov_mod.SEVEN_DAY,
                                                      ratio=1.15),)),
                   readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                          length=WEEK)})
    s = _samples(gm.render([_lane(utilization=seen)], now=NOW))
    lb = 'lane="base",window="seven_day"'
    assert s[f'st_governor_burn_ceiling_percent{{{lb}}}'] == 90
    assert s[f'st_governor_budget_points_remaining{{{lb}}}'] == pytest.approx(38)


def test_reset_is_exported_as_an_absolute_timestamp_and_a_countdown():
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.5,
                                                          length=WEEK)})
    s = _samples(gm.render([_lane(utilization=seen)], now=NOW))
    lb = 'lane="base",window="seven_day"'
    assert s[f'st_governor_window_reset_timestamp_seconds{{{lb}}}'] == \
        pytest.approx(NOW + WEEK * 0.5)
    assert s[f'st_governor_window_resets_in_seconds{{{lb}}}'] == \
        pytest.approx(WEEK * 0.5)


# --- exposition well-formedness ---------------------------------------------

def test_type_lines_are_emitted_once():
    """A repeated `# TYPE` makes the gateway reject the WHOLE body — so a second
    lane would silently take out the first lane's samples too."""
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                         length=WEEK)})
    body = gm.render([_lane(lane="base", utilization=seen),
                      _lane(lane="codex", utilization=seen)], now=NOW)
    types = [ln for ln in body.splitlines() if ln.startswith("# TYPE")]
    assert len(types) == len(set(types))
    assert 'st_governor_used_percent{lane="codex",window="seven_day"} 52' in body


def test_label_values_are_escaped():
    """A role name with a quote in it must not produce exposition the gateway
    rejects as a whole — one malformed family takes every other one with it."""
    body = gm.render([_lane(lane='we"ird', live=1)], now=NOW,
                     agents={"state": {('cl\\aude', "up"): 1}})
    assert 'st_governor_live_agents{lane="we\\"ird"} 1' in body
    assert 'st_agents{harness="cl\\\\aude",state="up"} 1' in body


# --- counters ---------------------------------------------------------------

def test_counters_survive_a_new_process(tmp_path):
    """st is invocation-based: a tend pass is a fresh process every five minutes,
    so an in-memory counter is a gauge that is always 1."""
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                          length=WEEK)})
    events = gm.events_of("base", None, seen)
    assert events["decisions"] == [("base", "grow")]
    for _ in range(3):
        totals = gm.Ledger(tmp_path).bump(**events)      # three separate "passes"
    assert totals.decisions[("base", "grow")] == 3
    # A brand-new Ledger over the same root reads the same history.
    assert gm.Ledger(tmp_path).totals().decisions[("base", "grow")] == 3
    s = _samples(gm.render([_lane(utilization=seen)], now=NOW, totals=totals))
    assert s['st_governor_decisions_total{lane="base",direction="grow"}'] == 3
    # Every direction is exported at 0 rather than missing, so `increase()` over
    # a direction that has not fired yet is 0 instead of no-data.
    assert s['st_governor_decisions_total{lane="base",direction="shrink"}'] == 0


def test_unreadable_counter_state_reads_as_zero_never_as_invented_history(tmp_path):
    (tmp_path / "governor").mkdir()
    (tmp_path / "governor" / "metrics-counters.json").write_text("{not json")
    assert gm.Ledger(tmp_path).totals().decisions == {}


def test_cannot_tell_counts_as_its_own_direction():
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                          length=WEEK)},
                   ready=None)
    assert gm.events_of("base", None, seen)["decisions"] == [("base", "unknown")]


def test_relaxation_and_burndown_are_counted_as_events():
    verdict = gov_mod.Verdict(
        reading=gov_mod.Reading(pct=40.0),
        relaxed=(gov_mod.Relaxed(window=gov_mod.FIVE_HOUR, was=50, now_at=None,
                                 pct=40.0),),
        burning=(gov_mod.Burning(window=gov_mod.SEVEN_DAY, pct=80.0,
                                 ceiling=90.0, resets_in=3600.0),))
    ev = gm.events_of("base", verdict, None)
    assert ev["relaxations"] == [("base", "five_hour")]
    assert ev["burndowns"] == [("base", "seven_day")]


# --- the push contract ------------------------------------------------------

def test_publish_is_a_no_op_without_the_env(tmp_path, monkeypatch):
    monkeypatch.delenv(gm.PUSHGATEWAY_ENV, raising=False)
    assert gm.publish(tmp_path, [_lane()]) == gm.NOTHING_TO_SAY
    # No counters were moved either — an unconfigured export must not have
    # side effects on disk.
    assert not (tmp_path / "governor" / "metrics-counters.json").exists()


def test_publish_never_raises(tmp_path, monkeypatch):
    """This hangs off the only respawn path there is (aegis-qwadc). It may log,
    it may fail, it may not take the pass down."""
    monkeypatch.setenv(gm.PUSHGATEWAY_ENV, "http://127.0.0.1:1/")
    logged = []
    # A lane whose render will explode, on a gateway that will refuse.
    class Boom:
        signal_lost = property(lambda self: 1 / 0)
    rc = gm.publish(tmp_path, [{"lane": "base", "verdict": Boom()}],
                    log=logged.append)
    assert rc in (gm.PUSH_FAILED, gm.COULD_NOT_RENDER)
    assert logged, "a failure that logs nothing is indistinguishable from success"


def test_the_producer_group_reports_what_happened_to_the_other_one():
    body = gm.render_producer(now=NOW, status=gm.PUSH_FAILED, lanes=2,
                              samples=41, agents_counted=9)
    s = _samples(body)
    assert s["st_governor_publish_status"] == gm.PUSH_FAILED
    assert s["st_governor_pass_timestamp_seconds"] == NOW
    assert s["st_governor_lanes"] == 2 and s["st_governor_samples"] == 41
    assert s["st_governor_agents_counted"] == 9
    # None = could not look, and is omitted rather than published as 0.
    assert "st_governor_agents_counted" not in _samples(
        gm.render_producer(now=NOW, status=gm.OK, lanes=1, samples=1,
                           agents_counted=None))


def test_publish_pushes_both_groups_and_bumps_the_counter(tmp_path, monkeypatch):
    monkeypatch.setenv(gm.PUSHGATEWAY_ENV, "http://gw.invalid/")
    sent = []
    monkeypatch.setattr(gm, "_push",
                        lambda url, job, inst, body, timeout=None, env=None:
                        sent.append((job, inst, body)))
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                          length=WEEK)})
    rc = gm.publish(tmp_path, [_lane(utilization=seen)], instance="host-a",
                    agents={"state": {("claude", "up"): 2}, "work": {},
                            "stopped": {}}, now=NOW)
    assert rc == gm.OK
    jobs = [job for job, _i, _b in sent]
    assert jobs == [gm.JOB, gm.PRODUCER_JOB]
    assert all(inst == "host-a" for _j, inst, _b in sent)
    assert 'st_agents{harness="claude",state="up"} 2' in sent[0][2]
    assert "st_governor_publish_status 0" in sent[1][2]
    stored = json.loads((tmp_path / "governor" / "metrics-counters.json").read_text())
    assert stored["decisions"]["base"]["grow"] == 1


def test_liveness_is_pushed_even_when_the_sample_push_failed(tmp_path, monkeypatch):
    """The two-group split (creel's aegis-4zpae5 argument): a dead producer and a
    producer with nothing to say must not be the same gap in the same graph."""
    monkeypatch.setenv(gm.PUSHGATEWAY_ENV, "http://gw.invalid/")
    sent = []

    def _fake(url, job, inst, body, timeout=None, env=None):
        if job == gm.JOB:
            raise OSError("gateway refused")
        sent.append((job, body))
    monkeypatch.setattr(gm, "_push", _fake)
    rc = gm.publish(tmp_path, [_lane()], instance="host-a", log=lambda _m: None)
    assert rc == gm.PUSH_FAILED
    assert sent and sent[0][0] == gm.PRODUCER_JOB
    assert f"st_governor_publish_status {gm.PUSH_FAILED}" in sent[0][1]


# --- the credential, which must not become a second copy --------------------

def test_the_password_is_read_from_its_file_at_push_time(tmp_path):
    """A rotation-managed secret copied into a config splits at the next rotate:
    both copies look fine, one stops working, and it surfaces as an auth error
    from a producer nobody changed. Reading the file at push time means a
    rotation needs no edit anywhere."""
    pw = tmp_path / "pw"
    pw.write_text("first-secret\n")
    env = {gm.PASSWORD_FILE_ENV: str(pw), gm.USER_ENV: "someuser"}
    _base, headers = gm._auth_header("http://gw.invalid/", env)
    import base64 as b64
    assert b64.b64decode(headers["Authorization"].split()[1]) == b"someuser:first-secret"
    # ROTATE the file only — no config change anywhere.
    pw.write_text("second-secret\n")
    _base, headers = gm._auth_header("http://gw.invalid/", env)
    assert b64.b64decode(headers["Authorization"].split()[1]) == b"someuser:second-secret"


def test_userinfo_in_the_url_still_works_and_is_stripped_from_the_target():
    base, headers = gm._auth_header("http://u:p@gw.invalid/", {})
    assert base == "http://gw.invalid"          # never send userinfo in the path
    assert "Authorization" in headers


def test_an_unreadable_credential_file_does_not_silently_go_anonymous(tmp_path):
    """It must fail with a 401 that shows up as publish_status, not become an
    unauthenticated request that reads like a gateway problem."""
    env = {gm.PASSWORD_FILE_ENV: str(tmp_path / "nope"), gm.USER_ENV: "someuser"}
    _base, headers = gm._auth_header("http://gw.invalid/", env)
    assert "Authorization" not in headers


def test_the_deployment_env_is_consulted_not_only_os_environ(tmp_path, monkeypatch):
    """st does not export `[env]` into its own process. A publish that read only
    the ambient environment would leave a configured deployment unexported —
    configured-but-not-live, which is the class this repo names everywhere."""
    monkeypatch.delenv(gm.PUSHGATEWAY_ENV, raising=False)
    sent = []
    monkeypatch.setattr(gm, "_push",
                        lambda url, job, inst, body, timeout=None, env=None:
                        sent.append(job))
    assert gm.publish(tmp_path, [_lane()], instance="host-a") == gm.NOTHING_TO_SAY
    assert not sent
    rc = gm.publish(tmp_path, [_lane()], instance="host-a",
                    env={gm.PUSHGATEWAY_ENV: "http://gw.invalid/"})
    assert rc == gm.OK and sent == [gm.JOB, gm.PRODUCER_JOB]


def test_quiet_counters_and_the_floor_companion_exist_from_the_first_pass():
    """A family that only appears once something has gone wrong leaves its panel
    reading "No data" for exactly as long as everything is fine — and on a
    capacity dashboard that reads as headroom. goldblum's check-dashboard-metrics
    refused three of these; this is the rule they were missing."""
    seen = _assess(readings={gov_mod.SEVEN_DAY: _reading(52, elapsed=0.58,
                                                          length=WEEK)})
    s = _samples(gm.render([_lane(utilization=seen, verdict=gov_mod.Verdict(
        reading=gov_mod.Reading(pct=52.0), pct=52.0))], now=NOW))
    lb = 'lane="base",window="seven_day"'
    assert s[f'st_governor_relaxations_total{{{lb}}}'] == 0
    assert s[f'st_governor_burndowns_total{{{lb}}}'] == 0
    # No tier declares a floor, so the floor itself is absent BY DESIGN — and the
    # companion says so as a measured 0 rather than as a silence.
    assert 'st_governor_priority_floor{lane="base"}' not in s
    assert s['st_governor_priority_floor_declared{lane="base"}'] == 0


def test_a_window_that_has_relaxed_keeps_its_total_after_the_event_passes():
    totals = gm.Totals(decisions={}, burndowns={},
                       relaxations={("base", "five_hour"): 4})
    s = _samples(gm.render([_lane()], now=NOW, totals=totals))
    assert s['st_governor_relaxations_total{lane="base",window="five_hour"}'] == 4


# --- whose fault, and the reader's own words (aegis-tq8um5) -------------------


def _lost(why, fault):
    return gov_mod.Verdict(reading=gov_mod.Reading(error=why),
                           signal_lost=True, why=why, fault=fault)


def test_the_fault_is_a_closed_vocabulary_every_value_exported():
    """Same discipline as `st_governor_cause`: a class that happens to be
    inactive reads 0, not no-data, so a transition is graphable and "no data"
    keeps its one meaning — the producer is gone."""
    s = _samples(gm.render([_lane(verdict=_lost("provider said 404",
                                                gov_mod.UPSTREAM),
                                  utilization=None, readings={})], now=NOW))
    assert s['st_governor_signal_lost_fault{lane="base",fault="upstream"}'] == 1
    assert s['st_governor_signal_lost_fault{lane="base",fault="local"}'] == 0
    assert s['st_governor_signal_lost_fault{lane="base",fault="unknown"}'] == 0
    # The load-bearing series is left untouched — an existing alert and
    # dashboard select on it, and widening its labels would change what
    # `max by (lane)` sums over at the moment the fleet is already blind.
    assert s['st_governor_signal_lost{lane="base"}'] == 1


def test_a_healthy_lane_publishes_every_fault_at_zero_and_no_info_series():
    s = _samples(gm.render([_lane(verdict=gov_mod.Verdict(
        reading=gov_mod.Reading(pct=10), pct=10), utilization=None,
        readings={})], now=NOW))
    for fault in gov_mod.FAULTS:
        assert s[f'st_governor_signal_lost_fault{{lane="base",fault="{fault}"}}'] == 0
    # THE PROSE SERIES IS STILL PUBLISHED, at 0, with fault="none" and no why.
    # A series that appears only on failure cannot be verified to exist while
    # things are fine — which is both this module's own rule and the reason
    # goldblum's check-alert-metrics refuses an alert whose metric has no
    # series (it cannot fire, and that is indistinguishable from healthy).
    info = [k for k in s if k.startswith("st_governor_signal_lost_info")]
    assert len(info) == 1, info
    assert 'fault="none"' in info[0] and 'why=""' in info[0], info[0]
    assert s[info[0]] == 0


def test_the_reader_error_reaches_the_label_a_page_can_render():
    why = ("seven_day: account/rateLimits/read failed: GET "
           "https://chatgpt.com/backend-api/wham/usage failed: 404 Not Found")
    s = _samples(gm.render([_lane(verdict=_lost(why, gov_mod.UPSTREAM),
                                  utilization=None, readings={})], now=NOW))
    info = [k for k in s if k.startswith("st_governor_signal_lost_info")]
    assert len(info) == 1, info          # exactly one per lane -> group_left is safe
    assert "404 Not Found" in info[0]
    assert 'fault="upstream"' in info[0]


def test_a_churning_number_in_the_why_does_NOT_churn_the_label():
    """THE PROPERTY THE `for:` TIMER DEPENDS ON.

    A label that changes value restarts an alert's `for:` clock. The staleness
    message re-renders its age EVERY pass — "last succeeded 1834s ago", then
    1839, then 2139 — so an un-normalized `why` would hand
    `GovernorSignalLost` a `for: 30m` it could never reach, silently disarming
    the governor's only decision-side alert in the exact fault class it exists
    to catch. Digits collapse to N; the label is then stable for as long as the
    FAULT is, which is the window the timer is measuring.
    """
    first = gm.stable_why("the probe last succeeded 1834s ago (limit 900s)")
    later = gm.stable_why("the probe last succeeded 2139s ago (limit 900s)")
    assert first == later
    # ...and it must still be a sentence a human can act on.
    assert "probe last succeeded" in first and "limit" in first
    # Epochs and pids churn the same way and are read by nobody.
    assert (gm.stable_why("timestamp is 1788573475 in the FUTURE")
            == gm.stable_why("timestamp is 1788573999 in the FUTURE"))


def test_normalizing_for_the_timer_must_NOT_eat_the_http_status():
    """THE COUNTERWEIGHT, and the first cut of this function failed it.

    Eliding every digit run made the timer safe and turned `404 Not Found` into
    `N Not Found` — deleting the one token that says UPSTREAM at a glance, in
    the very message this whole bead exists to put in front of a responder. A
    status is 3 digits with no unit after it; an age is a number introducing a
    time unit. Only the second is volatile, so only the second is elided.
    """
    for status in ("404 Not Found", "401 Unauthorized", "429 Too Many Requests",
                   "500 Internal Server Error"):
        assert status in gm.stable_why(f"usage endpoint failed: {status}")
    assert "(HTTP 401)" in gm.stable_why(
        "claude_usage_probe_success is 0 (HTTP 401) — a probe FAILED")


def test_a_long_why_is_truncated_VISIBLY_rather_than_silently():
    out = gm.stable_why("x" * 400)
    assert len(out) == gm.WHY_MAX and out.endswith("…")


def test_two_different_faults_stay_two_different_labels():
    """The whole point of the label: same alert, same lane, opposite remedy."""
    up = _samples(gm.render([_lane(verdict=_lost("provider 404",
                                                 gov_mod.UPSTREAM),
                                   utilization=None, readings={})], now=NOW))
    loc = _samples(gm.render([_lane(verdict=_lost("codex is not on PATH",
                                                  gov_mod.LOCAL),
                                    utilization=None, readings={})], now=NOW))
    assert up['st_governor_signal_lost_fault{lane="base",fault="upstream"}'] == 1
    assert loc['st_governor_signal_lost_fault{lane="base",fault="local"}'] == 1
    assert loc['st_governor_signal_lost_fault{lane="base",fault="upstream"}'] == 0
