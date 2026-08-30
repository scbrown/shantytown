from __future__ import annotations

import json
import subprocess

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


def test_alerter_is_silent_on_every_repeat_including_a_nonzero_one(tmp_path):
    """sattler's ruling on aegis-ox5dh (2026-08-29) — push on FIRST OCCURRENCE
    and on VALUE CHANGE, silent on all repeats including a nonzero one. It
    supersedes 1641346, whose rule this test previously asserted in the opposite
    direction (`test_alerter_keeps_nonzero_recommendations_actionable`).

    That rule's instinct was right — an unactuated recommendation must not go
    silent — and its cadence was what made it wrong. Measured live: the same +3
    at 20:27, 20:32 and 20:37 while the standing answer was known and deliberate.
    The standing state now lives on `st crew --governor`, a place you LOOK rather
    than a thing that interrupts you. A slow re-nag returns only if a nonzero
    recommendation is ever MEASURED sitting unactioned."""
    pushed = []
    mk = lambda: advisory.Alerter(tmp_path, object(), object(),
        push=lambda reg, panes, message: pushed.append(message) or "admin")
    lines = {"codex": "governor recommends +2"}

    assert mk().sweep(lines) == ["codex"], "first occurrence is news"
    assert mk().sweep(lines) == [], "and every repeat after it is not"
    assert mk().sweep({"codex": "governor recommends +4"}) == ["codex"], \
        "a CHANGED value is news again"
    assert pushed == ["governor setpoint [codex]: governor recommends +2",
                      "governor setpoint [codex]: governor recommends +4"]


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
    assert alerter.sweep(first) == ["codex"]
    assert alerter.sweep(second) == []
    assert len(pushed) == 1


def test_a_nonzero_recommendation_goes_quiet_once_read(tmp_path):
    """SUPERSEDED 1641346's rule, per sattler's ox5dh ruling — this test asserted
    the opposite and is kept, inverted, rather than deleted, so the change of
    mind is visible to the next reader instead of looking like it never
    happened."""
    pushed = []
    mk = lambda: advisory.Alerter(tmp_path, object(), object(),
        push=lambda reg, panes, message: pushed.append(message) or "admin")
    lines = {"codex": "governor recommends +2 — under trajectory"}
    assert mk().sweep(lines) == ["codex"]
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
    assert mk().sweep({"base": fill}) == ["base"], "first occurrence is news"
    assert mk().sweep({"base": moved}) == [], "same recommendation, drifted prose"
    assert mk().sweep({"base": hold}) == ["base"], "the change to hold is news"
    assert mk().sweep({"base": hold}) == [], "a standing hold goes quiet"
    assert sent[-1].startswith("governor utilization [base]: ")


def test_the_two_advisories_do_not_share_a_ledger(tmp_path):
    """They change on different events, so one ledger would let either suppress
    the other's push."""
    sent = []
    hold = advisory.Advice(line="hold", key="fill:0:6/6", actionable=False)
    _alerter(tmp_path, sent, filename="u.json").sweep({"base": hold})
    # The setpoint ledger is untouched, so its own first hold is still news.
    assert _alerter(tmp_path, sent).sweep(
        {"base": "governor recommends 0 — hold"}) == ["base"]


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

    assert mk_u().sweep({"base": fill}) == ["base"], "newly nonzero still pushes"
    assert mk_u().sweep({"base": fill}) == [], "an unchanged +3 goes quiet"
    grown = advisory.Advice(line="UTIL[... +4 ...]", key="fill:4", actionable=False)
    assert mk_u().sweep({"base": grown}) == ["base"], "a CHANGED recommendation pushes"

    # ...and the setpoint line now behaves identically.
    mk_s = lambda: _alerter(tmp_path, sent, filename="s.json")
    assert mk_s().sweep({"base": "governor recommends +2"}) == ["base"]
    assert mk_s().sweep({"base": "governor recommends +2"}) == []
    assert mk_s().sweep({"base": "governor recommends +5"}) == ["base"]


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
        assert mk().sweep(item) == ["base"], f"{shape}: the change is news"
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
