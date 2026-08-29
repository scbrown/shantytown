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
