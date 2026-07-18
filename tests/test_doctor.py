"""st doctor (aegis-q9eh). Every state is reached from injected probes, and each
one that could hide a lie is tested for BOTH outcomes:

  - absent AND present (a doctor that only ever says "healthy" is indistinguishable
    from a broken one — aegis-mt0r; roles --check once printed "0 agents, every one
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
