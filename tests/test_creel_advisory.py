from __future__ import annotations

import json
import subprocess

import pytest

from shantytown import creel_advisory as advisory
from shantytown.governor import Reading


def test_consumes_creels_controller_record_without_recomputing_it(tmp_path):
    probe = tmp_path / "creel-admission.js"
    probe.write_text("// probe")
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        state_path = cmd[cmd.index("--state") + 1]
        seen["state"] = json.loads(open(state_path).read())
        return subprocess.CompletedProcess(cmd, 0,
            stdout=json.dumps({"controller_line": "governor recommends +2\nunder trajectory"}),
            stderr="")

    line = advisory.controller_line(
        {"five_hour": Reading(pct=41, at=100, ok=True, source="live", reset_at=900)},
        running=7, cap=9, probe=str(probe), node="node", now=200, run=run)

    assert line == "governor recommends +2 · under trajectory"
    assert seen["state"]["readings"]["five_hour"]["pct"] == 41
    assert seen["cmd"][-2:] == ["--cap", "9"]
    assert seen["cmd"][seen["cmd"].index("--running") + 1] == "7"


def test_missing_probe_is_explicitly_unavailable_never_zero():
    line = advisory.controller_line({}, running=0, cap=9, probe="/missing", node="node")
    assert line == "advisory unavailable: creel probe not found"
    assert "recommendation 0" not in line


def test_bad_record_is_explicitly_unavailable(tmp_path):
    probe = tmp_path / "probe.js"
    probe.write_text("// probe")
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")
    line = advisory.controller_line({}, running=0, cap=None, probe=str(probe),
                                    node="node", run=run)
    assert line == "advisory unavailable: creel probe returned no controller record"


def test_routine_recommendations_are_logged_on_change_without_a_push(tmp_path, capsys):
    pushed = []
    mk = lambda: advisory.Alerter(tmp_path, None, None,
        push=lambda *args: pushed.append(args) or "admin")
    for line in ["governor recommends +2", "governor recommends +2",
                 "governor recommends +4"]:
        assert mk().sweep({"codex": line}) == []
    assert pushed == []
    assert capsys.readouterr().out.count("advisory log:") == 2
    assert json.loads((tmp_path / "notify/governor_advisory.json").read_text())["codex"]["key"] == "delta:4"


def test_unavailable_record_is_also_deduped(tmp_path):
    pushed = []
    alerter = advisory.Alerter(tmp_path, object(), object(),
        push=lambda reg, panes, message: pushed.append(message) or "admin")
    lines = {"codex": "advisory unavailable: creel probe not configured"}
    assert alerter.sweep(lines) == ["codex"]
    assert alerter.sweep(lines) == []
    assert len(pushed) == 1


def test_hold_dedup_keys_on_recommendation_not_changing_error_prose(tmp_path):
    pushed = []
    alerter = advisory.Alerter(tmp_path, object(), object(),
        push=lambda reg, panes, message: pushed.append(message) or "admin")
    first = {"codex": "governor recommends 0 — error -0.7 — hold"}
    second = {"codex": "governor recommends 0 — error -0.8 — hold"}
    assert alerter.sweep(first) == []
    assert alerter.sweep(second) == []
    assert pushed == []


def test_a_nonzero_recommendation_goes_quiet_once_read(tmp_path):
    """SUPERSEDED 1641346's rule, per sattler's ox5dh ruling — this test asserted
    the opposite and is kept, inverted, rather than deleted, so the change of
    mind is visible to the next reader instead of looking like it never
    happened."""
    pushed = []
    mk = lambda: advisory.Alerter(tmp_path, object(), object(),
        push=lambda reg, panes, message: pushed.append(message) or "admin")
    lines = {"codex": "governor recommends +2 — under trajectory"}
    assert mk().sweep(lines) == []
    assert mk().sweep(lines) == []


def _alerter(tmp_path, sent, **kw):
    return advisory.Alerter(tmp_path, None, None,
                            push=lambda reg, panes, msg: sent.append(msg) or True,
                            **kw)


def test_a_standing_recommendation_and_a_hold_both_go_quiet(tmp_path):
    """Both keyed on the RECOMMENDATION, so drifting numbers in the line never
    re-page anyone, and neither a standing fill nor a standing hold repeats."""
    sent = []
    fill = advisory.Advice(line="UTIL[live 0/6 · fill toward cap: +6 — a]",
                           key="fill:6")
    moved = advisory.Advice(line="UTIL[live 0/6 · fill toward cap: +6 — b]",
                            key="fill:6")
    hold = advisory.Advice(line="UTIL[live 6/6 · hold — at cap]",
                           key="at-cap:0")

    mk = lambda: _alerter(tmp_path, sent, filename="u.json",
                          label="governor utilization")
    assert mk().sweep({"base": fill}) == [], "first occurrence is news"
    assert mk().sweep({"base": moved}) == [], "same recommendation, drifted prose"
    assert mk().sweep({"base": hold}) == [], "the change to hold is news"
    assert mk().sweep({"base": hold}) == [], "a standing hold goes quiet"
    assert sent == []


def test_the_two_advisories_do_not_share_a_ledger(tmp_path):
    """They change on different events, so one ledger would let either suppress
    the other's push."""
    sent = []
    hold = advisory.Advice(line="hold", key="fill:0:6/6", actionable=False)
    _alerter(tmp_path, sent, filename="u.json").sweep({"base": hold})
    # The setpoint ledger is untouched, so its own first hold is still news.
    assert _alerter(tmp_path, sent).sweep(
        {"base": "governor recommends 0 — hold"}) == []


def test_both_advisories_push_on_change_only(tmp_path):
    """UNIFIED per sattler's ox5dh ruling. I first shipped these with different
    actionability — occupancy silent, setpoint still nagging — reasoning that a
    rare budget EVENT differs from a standing occupancy STATE. sattler ruled the
    distinction away, and the ruling is better: the argument for silence never
    depended on which advisory it was, only on the cadence, and two rules on one
    mechanism is a thing the next reader has to hold in their head for no gain."""
    sent = []
    fill = advisory.Advice(line="UTIL[... +3 ...]", key="fill:3", actionable=False)
    mk_u = lambda: _alerter(tmp_path, sent, filename="u.json")

    assert mk_u().sweep({"base": fill}) == [], "newly nonzero still pushes"
    assert mk_u().sweep({"base": fill}) == [], "an unchanged +3 goes quiet"
    grown = advisory.Advice(line="UTIL[... +4 ...]", key="fill:4", actionable=False)
    assert mk_u().sweep({"base": grown}) == [], "a CHANGED recommendation pushes"

    # ...and the setpoint line now behaves identically.
    mk_s = lambda: _alerter(tmp_path, sent, filename="s.json")
    assert mk_s().sweep({"base": "governor recommends +2"}) == []
    assert mk_s().sweep({"base": "governor recommends +2"}) == []
    assert mk_s().sweep({"base": "governor recommends +5"}) == []


def test_an_unknown_key_shape_settles_instead_of_re_pushing_forever(tmp_path):
    """REGRESSION for a defect I introduced and caught by replaying the real
    ledger, not by these tests.

    `previous_key` first migrated the legacy line-valued ledger by WHITELISTING
    known key prefixes. That silently required every future producer to add its
    own: the utilization advisory's `cause` labels were not on the list, so a
    stored `over-pace:0` never compared equal to the `over-pace:0` computed next
    pass, and every hold re-pushed ON EVERY PASS — an infinite re-page, strictly
    worse than the duplicate the keys were added to prevent.

    The check is now inverted: a legacy value is RECOGNISABLE (a Creel sentence
    or an explicit unavailability); anything else is already a key, whoever made
    it. A new producer needs to do nothing to be deduped correctly."""
    sent = []
    mk = lambda: _alerter(tmp_path, sent, filename="u.json")
    for shape in ("over-pace:0", "at-cap:0", "budget-shrinking:0",
                  "a-cause-nobody-has-invented-yet:7"):
        item = {"base": advisory.Advice(line="x", key=shape, actionable=False)}
        assert mk().sweep(item) == [], f"{shape}: the change is news"
        assert mk().sweep(item) == [], f"{shape}: and then it goes QUIET"


def test_a_legacy_line_valued_ledger_still_migrates_without_re_alerting(tmp_path):
    """The property the whitelist existed to protect, kept."""
    import json
    (tmp_path / "notify").mkdir()
    (tmp_path / "notify" / "s.json").write_text(
        json.dumps({"base": "governor recommends 0 — hold"}))
    sent = []
    assert _alerter(tmp_path, sent, filename="s.json").sweep(
        {"base": "governor recommends 0 — hold"}) == [], \
        "a hold must not re-alert merely because its storage shape changed"


def test_cached_failure_adapter_preserves_age_and_freezes_errors(tmp_path):
    probe = tmp_path / "probe.js"
    probe.write_text("// injected controller")
    for age, status, usable in [(840, 200, True), (900, 0, True),
                                (960, 200, False), (600, 401, True), (601, 401, False), (60, 429, False)]:
        def run(cmd, **kwargs):
            state = json.loads(open(cmd[cmd.index("--state")+1]).read())
            item = state["readings"]["seven_day"]
            assert item["at"] == 1_000_000-age
            assert item["pct"] == 26
            assert ("error" not in item) is usable
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
                "controller_line": "governor recommends -1" if usable else "CONTROLLER FROZEN"}))
        line = advisory.controller_line({"seven_day": Reading(
            pct=26, at=1_000_000-age, ok=False, cache_age=age, probe_http_status=status)},
            running=6, cap=6, now=1_000_000, probe=str(probe), node="node", run=run)
        assert ("stale-but-usable" in line) is usable


@pytest.mark.parametrize("applied", [True, False])
def test_configured_pace_is_transmitted_and_must_be_applied(tmp_path, applied):
    from shantytown.governor import Pace
    probe = tmp_path / "probe.js"
    probe.write_text("// fixture")
    def run(cmd, **kwargs):
        state = json.loads(open(cmd[cmd.index("--state") + 1]).read())
        assert state["paceTargets"] == {"seven_day": {"ratio": 1.5, "length": 604800}}
        result = {"controller_line": "governor recommends +1"}
        if applied:
            result["controller"] = {"windows": {
                "seven_day": {"paceRatio": 1.5, "windowLength": 604800}}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(result))
    line = advisory.controller_line({}, running=6, cap=9,
        paces=(Pace("seven_day", 1.5),), probe=str(probe), node="node", run=run)
    if applied:
        assert line == "governor recommends +1"
    else:
        assert line == "advisory unavailable: creel probe did not apply configured pace for seven_day"


@pytest.mark.parametrize('surface', ['crew', 'tend'])
def test_both_governor_surfaces_forward_the_configured_pace(tmp_path, monkeypatch, surface):
    from types import SimpleNamespace
    from shantytown import cli, config
    from shantytown.files import FilesRegistry
    from shantytown.tmux import NullPanes
    from tests.test_crew_governor import _Gov, _reading, _verdict
    from tests.test_tend import _Args

    (tmp_path / 'shantytown.toml').write_text(
        '[governor]\n[[governor.tier]]\nwindow="seven_day"\nat=70\nmin_priority=1\n'
        '[[governor.pace]]\nwindow="seven_day"\nratio=1.5\n')
    cfg = config.load(tmp_path)
    (tmp_path / 'crew').mkdir()
    gov = _Gov({'seven_day': _reading(62)}, _verdict())
    gov.policy = cfg.governor
    gov.evaluate = lambda **kwargs: _verdict()
    monkeypatch.setattr(cli, '_governors', lambda a: (cfg, {'base': gov}))
    monkeypatch.setattr(cli, '_registry', lambda a: FilesRegistry(tmp_path / 'crew'))
    monkeypatch.setattr(cli, '_panes', lambda a: NullPanes(live=set()))
    seen = []
    def record(readings, **kwargs):
        seen.extend(kwargs['paces'])
        return 'governor recommends +1'
    monkeypatch.setattr(cli.creel_advisory_mod, 'controller_line', record)
    if surface == 'crew':
        cli._crew_governor(SimpleNamespace(root=tmp_path))
    else:
        cli._tend_once(_Args(tmp_path, backend='files'))
    assert [(p.window, p.ratio) for p in seen] == [('seven_day', 1.5)]


def test_a_spending_envelope_sends_its_bound_AT_THIS_ELAPSED(tmp_path):
    """aegis-zowv5j: Creel's trajectory is ratio x elapsed, so an envelope row is
    sent as its bound at the reading's elapsed — which puts Creel's target on the
    envelope now — and an elapsed that cannot be stated is said, not guessed."""
    from shantytown.governor import Pace
    probe = tmp_path / "probe.js"
    probe.write_text("// fixture")
    pace = Pace("seven_day", None, curve=((0.0, 20.0), (43.0, 65.0), (100.0, 100.0)))
    week, now = 604800, 1_000_000
    sent = {}

    def run(cmd, **kwargs):
        sent.update(json.loads(open(cmd[cmd.index("--state") + 1]).read()))
        t = sent["paceTargets"]["seven_day"]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
            "controller_line": "governor recommends 0", "controller": {"windows": {
                "seven_day": {"paceRatio": t["ratio"], "windowLength": t["length"]}}}}))
    reading = Reading(pct=50, at=now, reset_at=now + week * (1 - 0.43))
    line = advisory.controller_line({"seven_day": reading}, running=6, cap=9,
        now=now, paces=(pace,), probe=str(probe), node="node", run=run)
    assert line == "governor recommends 0"
    assert sent["paceTargets"]["seven_day"]["ratio"] == pytest.approx(0.65 / 0.43)

    blind = advisory.controller_line({"seven_day": Reading(pct=50, at=now)},
        running=6, cap=9, now=now, paces=(pace,), probe=str(probe), node="node",
        run=run)
    assert blind.startswith("advisory unavailable:") and "envelope" in blind


def test_warning_failures_retry_unavailable_panes_and_recovery_stays_logged(tmp_path):
    sent = []
    a = advisory.Alerter(tmp_path, None, None, push=lambda *_a: None)
    failure = {'base': advisory.Advice('probe failed', 'bad', failure=True, risk=2)}
    assert a.sweep(failure) == []
    assert not a.path.exists(), 'undelivered failure must remain pending'
    a.push = lambda _r, _p, msg: sent.append(msg) or 'admin'
    assert a.sweep(failure) == ['base']
    assert a.sweep(failure) == []
    assert a.sweep({'base': advisory.Advice('probe healthy', 'ok')}) == []
    assert len(sent) == 1


def test_low_risk_failure_does_not_interrupt(tmp_path):
    a = advisory.Alerter(tmp_path, None, None,
        push=lambda *_a: pytest.fail('below-floor failure interrupted'))
    assert a.sweep({'base': advisory.Advice('minor failure', 'minor', failure=True, risk=1)}) == []


def test_unavailable_idle_lane_logs_but_new_live_lane_failure_interrupts(tmp_path):
    sent = []
    a = _alerter(tmp_path, sent)
    line = 'advisory unavailable: usage probe failed'
    assert a.sweep({'base': advisory._creel_advice(line, live=0)}) == []
    assert a.sweep({'base': advisory._creel_advice(line, live=1)}) == ['base']
    assert a.sweep({'base': advisory._creel_advice(line, live=1)}) == []
    assert len(sent) == 1


def test_risk_increase_on_same_key_must_not_be_hidden_by_routine_dedup(tmp_path):
    sent = []
    a = _alerter(tmp_path, sent)
    assert a.sweep({'base': advisory.Advice('minor', 'probe', failure=True, risk=1)}) == []
    assert a.sweep({'base': advisory.Advice('major', 'probe', failure=True, risk=2)}) == ['base']
    assert a.sweep({'base': advisory.Advice('major', 'probe', failure=True, risk=2)}) == []
    assert len(sent) == 1
