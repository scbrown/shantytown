from __future__ import annotations

import json
import threading

import pytest

from shantytown import window


def _roster():
    return [
        {"agent": "ian", "pane": "st-ian", "live": True, "input": "EMPTY"},
        {"agent": "ellie", "pane": "st-ellie", "live": False, "input": "DOWN"},
    ]


def _plan(root, wid="w1", **kw):
    return window.plan(
        root, wid, roster=_roster(), anchors=[{"id": "x1", "assignee": "ian"}],
        deployed_sha=kw.pop("deployed_sha", "abc1234"),
        target_version=kw.pop("target_version", "def5678"),
        timer={"unit": "st-tend.timer", "active": True, "enabled": True},
        now=1, **kw)


def test_already_satisfied_target_refuses_before_writing_a_plan(tmp_path):
    with pytest.raises(window.WindowRefused, match="already installed"):
        _plan(tmp_path, deployed_sha="abc1234-dirty", target_version="abc")
    assert window.active(tmp_path) is None


def test_two_simultaneous_window_ids_cannot_coexist(tmp_path):
    barrier = threading.Barrier(2)
    outcomes = []

    def contender(wid):
        barrier.wait()
        try:
            _plan(tmp_path, wid)
            outcomes.append((wid, "won"))
        except window.WindowRefused:
            outcomes.append((wid, "refused"))

    threads = [threading.Thread(target=contender, args=(wid,)) for wid in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(result for _, result in outcomes) == ["refused", "won"]
    assert window.active(tmp_path)["id"] in {"a", "b"}


def test_loser_is_refused_even_when_the_winner_has_not_written_yet(tmp_path):
    """A loser that arrives mid-create must be REFUSED, not told the ledger is corrupt.

    create() establishes exclusivity with O_EXCL, which leaves the file EMPTY
    until the json.dump lands. A contender arriving inside that gap used to read
    "" and raise WindowUnreadable — the wrong verdict, and one the caller cannot
    act on: it says "corrupt" when the truth is "you lost a race".

    This is deterministic where the threaded test above is not: it recreates the
    exact intermediate state rather than hoping to hit it. That matters, because
    the threaded test PASSED locally ten times in a row while main was red.
    """
    (tmp_path / "window").mkdir(parents=True)
    (tmp_path / "window" / "active.json").write_text("")  # winner mid-create

    with pytest.raises(window.WindowRefused):
        window.WindowStore(tmp_path).create({"id": "b"})


def test_dirty_or_live_worker_prevents_clear_and_is_named(tmp_path):
    _plan(tmp_path)
    window.drain(tmp_path, "w1", pause_timer=lambda: None)
    with pytest.raises(window.WindowRefused, match="ian.*TYPED"):
        window.clear(tmp_path, "w1", observe=lambda _: ["ian pane=st-ian input=TYPED"])
    assert window.active(tmp_path)["state"] == "draining"


def test_abort_restores_exact_pre_window_roster_and_timer(tmp_path):
    _plan(tmp_path)
    window.drain(tmp_path, "w1", pause_timer=lambda: None)
    live = set()
    timer = {"active": False}
    seen_states = []
    def start(name):
        seen_states.append(window.active(tmp_path)["state"])
        live.add(name)
    restored = window.restore(
        tmp_path, "w1", start_agent=start, is_live=lambda name: name in live,
        timer_active=lambda: timer["active"],
        resume_timer=lambda: timer.__setitem__("active", True), require_clear=False)
    assert live == {"ian"}
    assert timer["active"] is True
    assert restored["roster"] == _roster()
    assert seen_states == ["restoring"]
    assert window.active(tmp_path) is None


def test_release_requires_clear_then_restores(tmp_path):
    _plan(tmp_path)
    window.drain(tmp_path, "w1", pause_timer=lambda: None)
    with pytest.raises(window.WindowRefused, match="requires a successful CLEAR"):
        window.restore(tmp_path, "w1", start_agent=lambda _: None,
                       is_live=lambda _: True, timer_active=lambda: True,
                       resume_timer=lambda: None, require_clear=True)
    window.clear(tmp_path, "w1", observe=lambda _: [])
    live = set()
    window.restore(tmp_path, "w1", start_agent=live.add,
                   is_live=lambda name: name in live, timer_active=lambda: True,
                   resume_timer=lambda: None, require_clear=True)
    assert live == {"ian"}


def test_unreadable_ledger_fails_closed(tmp_path):
    path = tmp_path / "window" / "active.json"
    path.parent.mkdir()
    path.write_text("{")
    with pytest.raises(window.WindowUnreadable, match="ledger unreadable"):
        window.active(tmp_path)


def test_manifest_is_auditable_json(tmp_path):
    _plan(tmp_path)
    value = json.loads((tmp_path / "window" / "active.json").read_text())
    assert value["anchors"][0]["id"] == "x1"
    assert value["deployed_sha"] == "abc1234"
    assert value["timer"]["active"] is True


def test_dry_plan_and_clear_change_nothing(tmp_path):
    _plan(tmp_path, wid="preview", persist=False)
    assert window.active(tmp_path) is None
    _plan(tmp_path)
    window.drain(tmp_path, "w1", pause_timer=lambda: None)
    preview = window.clear(tmp_path, "w1", observe=lambda _: [], persist=False)
    assert preview["state"] == "clear"
    assert window.active(tmp_path)["state"] == "draining"
