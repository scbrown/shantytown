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


def test_alerter_keeps_nonzero_recommendations_actionable(tmp_path):
    pushed = []
    alerter = advisory.Alerter(tmp_path, object(), object(),
        push=lambda reg, panes, message: pushed.append(message) or "admin")
    lines = {"codex": "governor recommends +2"}

    assert alerter.sweep(lines) == ["codex"]
    assert alerter.sweep(lines) == ["codex"]
    assert pushed == ["governor setpoint [codex]: governor recommends +2"] * 2


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


def test_nonzero_recommendation_remains_actionable_each_pass(tmp_path):
    pushed = []
    alerter = advisory.Alerter(tmp_path, object(), object(),
        push=lambda reg, panes, message: pushed.append(message) or "admin")
    lines = {"codex": "governor recommends +2 — under trajectory"}
    assert alerter.sweep(lines) == ["codex"]
    assert alerter.sweep(lines) == ["codex"]


def _alerter(tmp_path, sent, **kw):
    return advisory.Alerter(tmp_path, None, None,
                            push=lambda reg, panes, msg: sent.append(msg) or True,
                            **kw)


def test_a_standing_recommendation_keeps_asking_but_a_hold_goes_quiet(tmp_path):
    """gennaro's 1641346 semantics, now shared with the utilization advisory
    (aegis-967a9): an unactuated recommendation must keep nagging, while a hold
    is read once. Both are keyed on the RECOMMENDATION, so drifting numbers in
    the line never re-page anyone."""
    sent = []
    fill = advisory.Advice(line="UTIL[live 0/6 · fill toward cap: +6 — a]",
                           key="fill:6:0/6", actionable=True)
    moved = advisory.Advice(line="UTIL[live 0/6 · fill toward cap: +6 — b]",
                            key="fill:6:0/6", actionable=True)
    hold = advisory.Advice(line="UTIL[live 6/6 · hold — at cap]",
                           key="fill:0:6/6", actionable=False)

    mk = lambda: _alerter(tmp_path, sent, filename="u.json",
                          label="governor utilization")
    assert mk().sweep({"base": fill}) == ["base"]
    assert mk().sweep({"base": moved}) == ["base"], "unactuated advice keeps asking"
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


def test_utilization_pushes_on_change_only_while_setpoint_stays_actionable(tmp_path):
    """The two advisories carry DIFFERENT actionability on purpose (sattler,
    measured 2026-08-29).

    A nonzero SETPOINT delta is a rare trajectory event and keeps asking until
    acted on — gennaro's 1641346, deliberately. Occupancy is not an event:
    "under cap with work ready" stays true for hours, and re-pushing it every
    pass put base +3 in front of the admin twice in twenty minutes while the
    standing answer was known and deliberate."""
    sent = []
    fill = advisory.Advice(line="UTIL[... +3 ...]", key="fill:3", actionable=False)
    mk_u = lambda: _alerter(tmp_path, sent, filename="u.json")

    assert mk_u().sweep({"base": fill}) == ["base"], "newly nonzero still pushes"
    assert mk_u().sweep({"base": fill}) == [], "an unchanged +3 goes quiet"
    grown = advisory.Advice(line="UTIL[... +4 ...]", key="fill:4", actionable=False)
    assert mk_u().sweep({"base": grown}) == ["base"], "a CHANGED recommendation pushes"

    # ...while the setpoint line keeps its own behaviour, unchanged.
    mk_s = lambda: _alerter(tmp_path, sent, filename="s.json")
    assert mk_s().sweep({"base": "governor recommends +2"}) == ["base"]
    assert mk_s().sweep({"base": "governor recommends +2"}) == ["base"]


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
