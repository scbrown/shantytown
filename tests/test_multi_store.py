"""Multi-store haul/plate resolution (aegis-qmfa1).

An agent whose work lives in a repo's own embedded bd store could never
SELF-FEED. `hauls()` decides who is self-feeding, and a self-feeding worker is
deliberately EXCLUDED from the Rule Zero feedable list — so an embedded-store
agent got both halves wrong at once: its haul was invisible so it never
advanced, AND it correctly read as having no queue so it landed back on the
coordinator every cycle. Measured 2026-08-03: ellie needed a hand `st go` for
every na item while the rest of the fleet self-fed.

These tests fake `bd` at the ONE place it is spawned (`_bd_in`), so they assert
the store-routing contract without a live store.
"""
import json
import subprocess

import pytest

from shantytown.beads import (BeadsTracker, EXTRA_REPOS_KEY, parse_extra_repos,
                              plate, rows as store_rows)
from shantytown.feed_check import hauls


def _cp(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess(args=["bd"], returncode=rc,
                                       stdout=stdout, stderr=stderr)


def _row(id_, assignee, status="open", title="t"):
    return {"id": id_, "title": title, "status": status, "assignee": assignee,
            "priority": 2}


def _fake_stores(monkeypatch, tracker, by_repo, fail=()):
    """Route _bd_in per repo. `fail` names repos whose list must error."""
    def fake(self, repo, *args):
        if repo in fail:
            return _cp(rc=1, stderr=f"store down: {repo}")
        if args and args[0] == "list":
            return _cp(stdout=json.dumps(by_repo.get(repo, [])))
        if args and args[0] == "show":
            want = args[1]
            for r in by_repo.get(repo, []):
                if r["id"] == want:
                    return _cp(stdout=json.dumps(r))
            return _cp(rc=1, stderr=f"not found in {repo}")
        return _cp()
    monkeypatch.setattr(BeadsTracker, "_bd_in", fake, raising=True)
    # `_bd` must be faked TOO, not just `_bd_in`. conftest replaces `_bd` with a
    # refusal sentinel so no test can reach the real store, and the primary hop
    # of both `rows()` and `_bd_for()` deliberately goes through `_bd` to stay
    # bit-identical with the single-store path. Patching only the new seam would
    # leave the primary store answering "refused" and quietly test nothing.
    monkeypatch.setattr(BeadsTracker, "_bd",
                        lambda self, *args: fake(self, self.repo, *args),
                        raising=True)


# -- the parser -------------------------------------------------------------

@pytest.mark.parametrize("value,want", [
    (None, []),
    ("", []),
    ('["/a", "/b"]', ["/a", "/b"]),
    ("/a,/b", ["/a", "/b"]),
    ("/a\n/b", ["/a", "/b"]),
    (["/a", "/b"], ["/a", "/b"]),
    ("/a, ,/b", ["/a", "/b"]),
])
def test_parse_extra_repos_accepts_json_and_text(value, want):
    """env.json carries JSON, an exported env var carries text; one key, both."""
    assert parse_extra_repos(value) == want


def test_malformed_config_degrades_to_single_store_not_a_crash():
    """A stray comma in one path must not take the plate reader down fleet-wide.

    The conservative direction on purpose: a store you forgot to add is a bug
    someone notices, a stop-event drain that crashes for every agent is an
    outage.
    """
    assert parse_extra_repos("[not json") == []


# -- the union --------------------------------------------------------------

def test_list_rows_unions_across_stores(monkeypatch, tmp_path):
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    t = BeadsTracker(repo=a, extra_repos=[b])
    _fake_stores(monkeypatch, t, {t.repo: [_row("aegis-1", "ellie")],
                                  t.extra_repos[0]: [_row("na-1", "ellie")]})
    assert {r["id"] for r in store_rows(t)} == {"aegis-1", "na-1"}


def test_a_store_that_cannot_be_read_RAISES_never_a_partial_union(
        monkeypatch, tmp_path):
    """THE test for this change.

    A reader that quietly drops one store returns a SHORTER answer at exit 0 —
    indistinguishable from "that agent has no work". That is the exact shape of
    the bug being fixed, so silently degrading to it would be the worst possible
    failure mode: the fix would reintroduce the bug under load.
    """
    a, b = str(tmp_path / "a"), str(tmp_path / "b")
    t = BeadsTracker(repo=a, extra_repos=[b])
    _fake_stores(monkeypatch, t, {t.repo: [_row("aegis-1", "ellie")]},
                 fail=(t.extra_repos[0],))
    with pytest.raises(RuntimeError, match="bd list failed for store"):
        store_rows(t)


def test_single_store_behaviour_goes_through_the_ORIGINAL_seam(
        monkeypatch, tmp_path):
    """Back-compat, asserted at the seam rather than just at the result.

    With no extra stores, rows() must call `_bd` — the exact method it always
    called — and NOT the new `_bd_in`. That is what keeps every existing fake
    working, including conftest's own `_bd` refusal sentinel, and it is why the
    seven test_beads_plate tests needed no edit. Patching `_bd` here (not
    `_bd_in`) is the point of the test: if the single-store path ever drifts onto
    the new seam, this fails.
    """
    a = str(tmp_path / "a")
    t = BeadsTracker(repo=a)
    assert t.repos == [t.repo] and t.extra_repos == []
    calls = []

    def fake_bd(self, *args):
        calls.append(args)
        return _cp(stdout=json.dumps([_row("aegis-1", "ellie")]))

    monkeypatch.setattr(BeadsTracker, "_bd", fake_bd, raising=True)
    assert [r["id"] for r in store_rows(t)] == ["aegis-1"]
    assert calls == [("list", "--json", "--limit", "0")], (
        "single-store rows() must make exactly the one original bd call")


# -- what the agent actually experiences ------------------------------------

def test_plate_sees_work_living_only_in_an_embedded_store(monkeypatch, tmp_path):
    """ellie's case: her ONLY open work is an na bead. Before this, empty."""
    a, b = str(tmp_path / "aegis"), str(tmp_path / "na")
    t = BeadsTracker(repo=a, extra_repos=[b])
    _fake_stores(monkeypatch, t, {
        t.repo: [_row("aegis-9", "sattler")],          # somebody else's
        t.extra_repos[0]: [_row("na-htm", "ellie", status="in_progress")],
    })
    got = plate(t, "ellie")
    assert got is not None, "plate read empty while na-htm was in_progress"
    assert got.id == "na-htm"


def test_the_haul_advances_so_the_agent_is_not_re_dispatched(
        monkeypatch, tmp_path):
    """The feeding half. hauls() is store-agnostic already — it takes rows — so
    the union at the fetch seam is what makes an embedded-store worker read as
    self-feeding instead of landing back on the coordinator every cycle."""
    a, b = str(tmp_path / "aegis"), str(tmp_path / "na")
    t = BeadsTracker(repo=a, extra_repos=[b])
    _fake_stores(monkeypatch, t, {
        t.repo: [], t.extra_repos[0]: [_row("na-yd4", "ellie")]})
    assert hauls(store_rows(t)).get("ellie") == ["na-yd4"]


# -- item routing -----------------------------------------------------------

def test_get_routes_to_whichever_store_holds_the_id(monkeypatch, tmp_path):
    a, b = str(tmp_path / "aegis"), str(tmp_path / "na")
    t = BeadsTracker(repo=a, extra_repos=[b])
    _fake_stores(monkeypatch, t, {
        t.repo: [_row("aegis-1", "x")], t.extra_repos[0]: [_row("na-1", "ellie")]})
    assert t.get("na-1").assignee == "ellie"
    assert t.get("aegis-1").id == "aegis-1"


def test_a_total_miss_reports_the_PRIMARY_stores_error(monkeypatch, tmp_path):
    """The error must name the store the caller expected, not a random extra."""
    a, b = str(tmp_path / "aegis"), str(tmp_path / "na")
    t = BeadsTracker(repo=a, extra_repos=[b])
    _fake_stores(monkeypatch, t, {t.repo: [], t.extra_repos[0]: []})
    with pytest.raises(LookupError, match="aegis"):
        t.get("nope-1")
