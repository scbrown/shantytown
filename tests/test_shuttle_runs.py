"""shuttle_runs — the consumer half of the shuttle contract.

Load-bearing behaviours: the capability probe turns a pre-graph-kinds store
into CANNOT TELL (never an empty workload); queries name their scope (the
open-windows dataset) instead of reading the default graph's silent zero
rows; each (run, state) pair routes exactly once, with the run's first
sighting flagged so the sink opens one bead and nudges thereafter;
could-not-look keeps the seen set untouched.
"""
from __future__ import annotations

import pytest

from shantytown import shuttle_runs as sr
from shantytown.protocols import EventsUnavailable


class _Fake(sr.ShuttleRuns):
    """ShuttleRuns with the HTTP seam replaced. `runs_data` is a list of
    (iri, definition, state) or an EventsUnavailable; `probe_error` models a
    store that predates graph kinds."""

    def __init__(self, runs_data, probe_error=None):
        super().__init__(server="http://test")
        self._runs = runs_data
        self._probe_error = probe_error

    def probe(self):
        if self._probe_error:
            raise self._probe_error

    def runs(self):
        self.probe()
        if isinstance(self._runs, EventsUnavailable):
            raise self._runs
        return [sr.ShuttleRun(iri=r[0], definition=r[1], state=r[2]) for r in self._runs]


def _routes(report_state=None):
    state = report_state or sr.RunsState()
    routed = []
    return state, routed, lambda r, new: routed.append((r.iri, r.state, new))


def test_each_run_state_routes_once_and_the_first_sighting_is_flagged():
    state, routed, sink = _routes()
    src = _Fake([("urn:shuttle:run:r1", "urn:shuttle:workflow:triage", "open")])
    report = sr.poll_runs(src, state, sink)
    assert report.verdict == "live"
    assert routed == [("urn:shuttle:run:r1", "open", True)]

    # Same state again: idle, nothing re-routed.
    report = sr.poll_runs(src, state, sink)
    assert report.verdict == "idle"
    assert len(routed) == 1

    # A state CHANGE routes, but the run is no longer new.
    src = _Fake([("urn:shuttle:run:r1", "urn:shuttle:workflow:triage", "claimed")])
    report = sr.poll_runs(src, state, sink)
    assert report.verdict == "live"
    assert routed[-1] == ("urn:shuttle:run:r1", "claimed", False)


def test_could_not_look_is_cannot_tell_and_the_seen_set_survives():
    state, routed, sink = _routes()
    src = _Fake(EventsUnavailable("down"))
    report = sr.poll_runs(src, state, sink)
    assert report.verdict == "cannot tell"
    assert routed == [] and state.seen == set()


def test_a_pre_graph_kinds_store_is_cannot_tell_never_an_empty_workload():
    state, routed, sink = _routes()
    src = _Fake([], probe_error=EventsUnavailable(
        "quipu at http://test has no graph registry endpoint"))
    report = sr.poll_runs(src, state, sink)
    assert report.verdict == "cannot tell"
    assert "graph registry" in report.detail


def test_the_query_names_its_scope_never_the_default_graph():
    """The silent-zero-rows fix, pinned: the /query body must carry the
    open-windows dataset as its graph scope."""
    captured = {}

    class _Capture(sr.ShuttleRuns):
        def probe(self):
            return None

        def _post(self, path, body):
            captured["path"] = path
            captured["body"] = body
            return {"rows": []}

    _Capture(server="http://test").runs()
    assert captured["path"] == "/query"
    assert captured["body"]["graph"] == sr.OPEN_DATASET


def test_the_none_source_is_the_negative_control():
    state, routed, sink = _routes()
    report = sr.poll_runs(sr.NoShuttleRuns(), state, sink)
    assert report.verdict == "idle" and routed == []


def test_state_round_trips_through_disk(tmp_path):
    state = sr.RunsState(seen={("urn:shuttle:run:r1", "open")})
    state.save(tmp_path / "s.json")
    loaded = sr.RunsState.load(tmp_path / "s.json")
    assert loaded.seen == state.seen
