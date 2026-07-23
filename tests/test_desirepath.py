"""desirepath — the OPTIONAL dp data source must hide, never error (internal-ref).

The whole contract: st reads dp's signal when dp is there, and shows NOTHING when
it is not — the same discipline the shanty segments follow for a missing st. So
the tests that matter are the absence ones: dp missing, dp broken, dp silent.
"""
from __future__ import annotations

import json
import subprocess

from shantytown import desirepath


class _Proc:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def _fake_run(stdout, rc=0):
    return lambda *a, **k: _Proc(rc, stdout)


STATS = json.dumps({
    "total_desires": 321,
    "unique_paths": 7,
    "top_desires": [
        {"name": "Bash", "count": 313},
        {"name": "Read", "count": 3},
        {"name": "StructuredOutput", "count": 1},
    ],
})


def test_absent_dp_returns_none(monkeypatch):
    monkeypatch.setattr(desirepath.shutil, "which", lambda _: None)
    # Not even run() should be reached, but if it were, fail the test loudly.
    monkeypatch.setattr(desirepath.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dp was invoked while absent")))
    assert desirepath.available() is False
    assert desirepath.summary() is None
    assert desirepath.summary_line() is None


def test_present_dp_summarizes(monkeypatch):
    monkeypatch.setattr(desirepath.shutil, "which", lambda _: "/usr/bin/dp")
    monkeypatch.setattr(desirepath.subprocess, "run", _fake_run(STATS))
    s = desirepath.summary()
    assert s == {"total": 321, "unique": 7,
                 "top": [("Bash", 313), ("Read", 3), ("StructuredOutput", 1)]}
    line = desirepath.summary_line()
    assert line == "321 failed tool calls captured (7 unique); top: Bash×313, Read×3, StructuredOutput×1"


def test_nonzero_exit_returns_none(monkeypatch):
    monkeypatch.setattr(desirepath.shutil, "which", lambda _: "/usr/bin/dp")
    monkeypatch.setattr(desirepath.subprocess, "run", _fake_run("boom", rc=1))
    assert desirepath.summary() is None
    assert desirepath.summary_line() is None


def test_garbage_output_returns_none(monkeypatch):
    monkeypatch.setattr(desirepath.shutil, "which", lambda _: "/usr/bin/dp")
    monkeypatch.setattr(desirepath.subprocess, "run", _fake_run("not json at all"))
    assert desirepath.summary() is None
    assert desirepath.summary_line() is None


def test_dp_crash_returns_none(monkeypatch):
    monkeypatch.setattr(desirepath.shutil, "which", lambda _: "/usr/bin/dp")

    def _boom(*a, **k):
        raise OSError("no such process")

    monkeypatch.setattr(desirepath.subprocess, "run", _boom)
    assert desirepath.summary() is None


def test_summary_line_without_top(monkeypatch):
    monkeypatch.setattr(desirepath.shutil, "which", lambda _: "/usr/bin/dp")
    monkeypatch.setattr(desirepath.subprocess, "run",
                        _fake_run(json.dumps({"total_desires": 5, "unique_paths": 2, "top_desires": []})))
    assert desirepath.summary_line() == "5 failed tool calls captured (2 unique)"


def test_fresh_dp_with_null_top_desires_does_not_crash(monkeypatch):
    """A FRESH dp — zero data, exactly the state `st doctor --install` leaves it
    in — emits `"top_desires": null`. `.get(key, [])` returns that existing None,
    and iterating it crashed doctor with a TypeError the moment its own install
    became visible (internal-ref, found live in the e2e sandbox)."""
    fresh = json.dumps({
        "total_desires": 0,
        "unique_paths": 0,
        "top_sources": {},
        "top_desires": None,
    })
    monkeypatch.setattr(desirepath.shutil, "which", lambda _: "/usr/bin/dp")
    monkeypatch.setattr(desirepath.subprocess, "run", _fake_run(fresh))
    s = desirepath.summary()
    assert s == {"total": 0, "unique": 0, "top": []}
    # zero captures renders a line (total is 0, not None) — the caller's
    # total-is-None guard hides only ABSENT/unreadable, not "installed, no data"
    assert desirepath.summary_line() == "0 failed tool calls captured (0 unique)"
