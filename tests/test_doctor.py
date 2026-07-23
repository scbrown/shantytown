"""st doctor. Every state is reached from injected probes, and each
one that could hide a lie is tested for BOTH outcomes:

  - absent AND present (a doctor that only ever says "healthy" is indistinguishable
    from a broken one; roles --check once printed "0 agents, every one
    reports somewhere" and exited 0 on a missing registry).
  - stale AND current (the positive control that STALE is a real signal, not always-on).
  - version-error is UNKNOWN, never ABSENT (quipu-server --version opens a store and
    fails; "I could not tell" is exit 2, a different answer from "not installed").
  - detect touches NOTHING (asking a binary who it is must not write — prime's old bug).
"""
from __future__ import annotations
from pathlib import Path

import pytest

from shantytown import doctor as doc
from shantytown.doctor import ToolSpec, detect, plan_install, run_install, exit_code


SPEC = ToolSpec("bobbin", "bobbin", ("bobbin", "--version"), r"(\d+\.\d+\.\d+)",
                toolchain="cargo", installs_via="cargo build",
                leverage="context", release="github:x/bobbin")
QUIPU = ToolSpec("quipu", "quipu-server", ("quipu-server", "--version"), r"(\d+\.\d+\.\d+)",
                 toolchain="cargo", installs_via="cargo build", leverage="registry",
                 release="github:x/quipu", version_broken=True)
REACTOR = ToolSpec("reactor", "reactor", ("reactor", "--version"), r"(\d+\.\d+\.\d+)",
                   toolchain="unknown", installs_via="no release yet", leverage="events", release=None)


def probes(*, on_path=True, version_out=("bobbin 0.3.1", 0), latest=("0.3.1", None),
           toolchain=True, recorder=None):
    def which(name):
        if name == "cargo":
            return "/usr/bin/cargo" if toolchain else None
        return "/usr/bin/x" if on_path else None

    def run(argv):
        if recorder is not None:
            recorder.append(tuple(argv))
        out, rc = version_out
        return rc, out

    def fetch(release):
        return latest
    return dict(which=which, run=run, fetch=fetch)


# --- ABSENT vs PRESENT ------------------------------------------------------

def test_absent_tool_is_absent():
    h = detect(SPEC, **probes(on_path=False))
    assert h.present is False and h.state == doc.ABSENT


def test_present_and_versioned():
    h = detect(SPEC, **probes(latest=(None, None)))
    assert h.present and h.version == "0.3.1" and h.state == doc.PRESENT


# --- STALE vs CURRENT (positive control) ------------------------------------

def test_stale_when_latest_is_newer():
    h = detect(SPEC, **probes(latest=("0.6.0", None)))
    assert h.state == doc.STALE


def test_current_when_latest_matches_the_control_that_stale_is_real():
    h = detect(SPEC, **probes(latest=("0.3.1", None)))
    assert h.state == doc.CURRENT, "if this also read STALE, the STALE test proves nothing"


# --- the quipu case: version-error is UNKNOWN, never ABSENT ------------------

def test_version_error_is_UNKNOWN_not_absent():
    h = detect(QUIPU, **probes(version_out=("error opening store .bobbin/quipu/quipu.db", 1),
                               latest=(None, None)))
    assert h.present is True, "the binary IS on PATH — it is not absent"
    assert h.version is None and h.version_error is not None
    assert h.state == doc.UNKNOWN


def test_latest_unreachable_is_UNKNOWN_not_current():
    h = detect(SPEC, **probes(latest=(None, "could not reach release source: timeout")))
    assert h.uncertain is True
    assert h.state != doc.CURRENT  # never launder "could not check" into a clean bill


# --- exit codes -------------------------------------------------------------

def test_exit_0_when_all_current():
    hs = [detect(SPEC, **probes(latest=("0.3.1", None)))]
    assert exit_code(hs) == 0


def test_exit_1_when_something_absent():
    hs = [detect(SPEC, **probes(on_path=False))]
    assert exit_code(hs) == 1


def test_exit_2_when_something_could_not_tell():
    hs = [detect(QUIPU, **probes(version_out=("err", 1), latest=(None, None)))]
    assert exit_code(hs) == 2


def test_uncertainty_dominates_absence():
    absent = detect(REACTOR, **probes(on_path=False))
    unknown = detect(QUIPU, **probes(version_out=("err", 1), latest=(None, None)))
    assert exit_code([absent, unknown]) == 2, "a report you can't trust outranks 'fix this'"


# --- install: detect-before-install, refuse-loudly --------------------------

def test_install_refuses_when_toolchain_missing():
    h = detect(SPEC, **probes(latest=("0.6.0", None), toolchain=False))  # stale, cargo absent
    p = plan_install(h)
    assert p.action == "refuse" and "cargo" in p.reason and p.steps == ()


def test_install_plans_when_absent_and_toolchain_present():
    h = detect(SPEC, **probes(on_path=False, toolchain=True))  # absent, cargo present
    p = plan_install(h)
    assert p.action == "install" and p.steps


def test_upgrade_when_stale_and_toolchain_present():
    h = detect(SPEC, **probes(latest=("0.6.0", None), toolchain=True))
    assert plan_install(h).action == "upgrade"


def test_present_working_tool_is_skipped_not_churned():
    h = detect(SPEC, **probes(latest=("0.3.1", None)))  # current
    assert plan_install(h).action == "skip"


def test_reactor_with_no_mechanism_refuses_cleanly():
    h = detect(REACTOR, **probes(on_path=False))  # absent, toolchain unknown, no release
    p = plan_install(h)
    assert p.action == "refuse" and "no known install mechanism" in p.reason


# --- the two things that MUST NOT touch the box -----------------------------

def test_dry_run_runs_no_steps():
    ran = []
    h = detect(SPEC, **probes(on_path=False, toolchain=True))
    plan = plan_install(h)
    run_install(plan, run=lambda argv: ran.append(argv) or (0, ""), dry_run=True)
    assert ran == [], "dry-run executed an install step"


def test_non_dry_run_DOES_run_steps_the_control():
    ran = []
    h = detect(SPEC, **probes(on_path=False, toolchain=True))
    plan = plan_install(h)
    run_install(plan, run=lambda argv: ran.append(argv) or (0, ""), dry_run=False)
    assert ran, "if this is also empty, the dry-run test proves nothing"


def test_detect_touches_nothing_on_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ran = []
    doc.detect(QUIPU, **probes(version_out=("err", 1), latest=(None, None), recorder=ran))
    # nothing written to the cwd, and the only subprocess was the read-only --version
    assert list(tmp_path.iterdir()) == []
    assert ran == [("quipu-server", "--version")]


# --- UNPATHED: installed to GOBIN, invisible to PATH (internal-ref) ------------

GO_SPEC = ToolSpec("desirepath", "dp", ("dp", "--version"), r"(\d+\.\d+\.\d+)",
                   toolchain="go", installs_via="go install", leverage="signal",
                   release=None)


def go_probes(*, dp_at=None, version_out=("dp 0.2.0", 0), recorder=None):
    """dp absent from PATH; go present; dp optionally sitting in a fake GOBIN."""
    def which(name):
        return "/usr/bin/go" if name == "go" else None

    def run(argv):
        if recorder is not None:
            recorder.append(tuple(argv))
        if tuple(argv) == ("go", "env", "GOBIN"):
            return 0, ""          # unset -> default ~/go/bin path logic
        out, rc = version_out
        return rc, out

    def offpath(spec, *, which, run):
        return dp_at
    return dict(which=which, run=run, fetch=lambda r: (None, None), offpath=offpath)


def test_gobin_install_is_unpathed_not_absent():
    """The wmy7 lie: a SUCCESSFUL go install must not read as 'not installed'."""
    h = detect(GO_SPEC, **go_probes(dp_at="/fake/gobin/dp"))
    assert h.present is False
    assert h.state == doc.UNPATHED
    assert h.unpathed_at == "/fake/gobin/dp"
    assert h.version == "0.2.0"          # version read via the absolute path


def test_truly_absent_go_tool_is_still_absent():
    """Both worlds must differ: no binary anywhere is ABSENT, not UNPATHED."""
    h = detect(GO_SPEC, **go_probes(dp_at=None))
    assert h.state == doc.ABSENT and h.unpathed_at is None


def test_unpathed_exit_code_is_actionable_1():
    h = detect(GO_SPEC, **go_probes(dp_at="/fake/gobin/dp"))
    assert doc.exit_code([h]) == 1


def test_unpathed_plan_skips_with_the_path_fix_not_a_reinstall():
    h = detect(GO_SPEC, **go_probes(dp_at="/fake/gobin/dp"))
    p = doc.plan_install(h)
    assert p.action == "skip" and p.steps == ()
    assert "/fake/gobin" in p.reason and "PATH" in p.reason


def test_unpathed_report_names_the_location_and_the_action():
    h = detect(GO_SPEC, **go_probes(dp_at="/fake/gobin/dp"))
    r = doc.report([h])
    assert "installed at /fake/gobin/dp" in r
    assert "NOT on your PATH" in r and "add /fake/gobin to PATH" in r
    assert "not installed" not in r


def test_off_path_location_respects_explicit_gobin():
    """_off_path_location itself: an explicit `go env GOBIN` wins over ~/go/bin."""
    calls = []
    def which(name):
        return "/usr/bin/go" if name == "go" else None
    def run(argv):
        calls.append(tuple(argv))
        return 0, "/custom/gobin\n"
    import os as _os
    real_isfile, real_access = _os.path.isfile, _os.access
    try:
        _os.path.isfile = lambda p: p == "/custom/gobin/dp"
        _os.access = lambda p, m: True
        got = doc._off_path_location(GO_SPEC, which=which, run=run)
    finally:
        _os.path.isfile, _os.access = real_isfile, real_access
    assert got == "/custom/gobin/dp"
    assert ("go", "env", "GOBIN") in calls
