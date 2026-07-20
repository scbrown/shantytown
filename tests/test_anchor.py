"""The anchor's tests. One of these is the reason anchor exists as its own module.

- test_anchor_writes_nothing: asserted against the FILESYSTEM, not against the
  docstring that claims purity. Gas Town's primer mutates state from a hook,
  which is why "did I get primed?" became unanswerable. A comment saying "pure
  read" is exactly the kind of claim we keep finding untrue.
- test_lead_down / test_lead_unknown: anchor's job is to say your stop events go
  nowhere HERE, not when you stall. Both branches run, because a warning that
  has never fired is not a warning.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown.files import FilesRegistry, FilesTracker, plate
from shantytown.anchor import anchor
from shantytown.tmux import NullPanes


def _card(d: Path, name: str, **fields) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(fields))


@pytest.fixture
def world(tmp_path: Path):
    crew = tmp_path / "crew"
    _card(crew, "ellie", role="worker", reports_to="malcolm", pane="%5")
    _card(crew, "malcolm", role="lead", reports_to="arnold", pane="%1")
    _card(crew, "arnold", role="administrator")
    _card(crew, "arya", role="worker")                       # orphan
    _card(crew, "ghostlead", role="worker", reports_to="nobody")
    tracker = FilesTracker(tmp_path / "items")
    tracker.update("st-9h2", title="Restore the den service",
                   status="in_progress", assignee="ellie")
    tracker.update("st-old", title="Done thing",
                   status="closed", assignee="ellie")
    return tmp_path, FilesRegistry(crew), tracker


def test_anchor_writes_nothing(tmp_path: Path):
    """PURE READ — measured, not asserted.

    Snapshot every path under root, anchor, snapshot again, compare. This also
    catches the mkdir-in-__init__ bug: constructing a FilesTracker used to
    create its directory, so merely ASKING who you are wrote to disk.
    """
    crew = tmp_path / "crew"
    _card(crew, "solo", role="administrator")

    def snap():
        return {str(p) for p in tmp_path.rglob("*")}

    before = snap()
    # Note: the items/ dir deliberately does NOT exist. If anchor (or the plate
    # reader, or merely CONSTRUCTING the tracker) creates it, this fails — which
    # is the whole point. The tracker is built here, inside the snapshot window,
    # precisely so the mkdir-in-__init__ bug would be caught.
    trk = FilesTracker(tmp_path / "items")
    p = anchor("solo", FilesRegistry(crew), NullPanes(),
              plate=lambda who: plate(trk, who))
    after = snap()

    assert before == after, f"anchor WROTE: {after - before}"
    assert not (tmp_path / "items").exists(), "anchor created the items dir"
    assert p.me.name == "solo"


def test_constructing_a_tracker_creates_nothing(tmp_path: Path):
    """CONSTRUCTION IS SIDE-EFFECT-FREE. update() is the only writer.

    ellie's catch, and she is right that it needs its own test: FilesTracker
    .__init__ used to mkdir(parents=True), so merely BUILDING a tracker wrote to
    disk — while cli.md says anchor must never write. The mkdir now lives in
    update().

    Why this test and not just test_anchor_writes_nothing: the two-function
    interface test CANNOT catch a regression here, because the interface does not
    change — only the behaviour does. Without this, the next person restores the
    mkdir for a perfectly good-looking reason ("the tracker should own its dir")
    and anchor silently writes again, and every test still passes.
    """
    root = tmp_path / "items"
    t = FilesTracker(root)
    assert not root.exists(), "constructing a FilesTracker touched the disk"

    # ...and the write still works when a write is actually asked for.
    t.update("x-1", title="real", status="open")
    assert root.is_dir(), "update() failed to create its own directory"
    assert t.get("x-1").title == "real"


def test_anchor_is_idempotent(world):
    """Safe to run twice. It is the most-run command in the harness."""
    _, reg, trk = world
    a = anchor("ellie", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w)).render()
    b = anchor("ellie", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w)).render()
    assert a == b


def test_one_item_never_a_backlog(world):
    """cli.md: "One item, or none. A surface that prints a backlog is a dashboard."""
    _, reg, trk = world
    trk.update("st-2nd", title="Second thing", status="open", assignee="ellie")
    p = anchor("ellie", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w))
    # The type says so, but assert the behaviour: one, not two.
    assert p.item is not None
    assert p.render().count("▶") == 1


def test_closed_items_are_not_on_your_plate(world):
    _, reg, trk = world
    trk.update("st-9h2", status="closed")
    p = anchor("ellie", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w))
    assert p.item is None
    assert "nothing." in p.render()


def test_empty_plate_says_so(world):
    _, reg, trk = world
    p = anchor("arnold", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w))
    assert p.item is None
    assert "nothing." in p.render()


def test_lead_up(world):
    _, reg, trk = world
    p = anchor("ellie", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w))
    assert p.lead.name == "malcolm"
    assert p.lead_up is True
    assert "up. Your stop events go to them." in p.render()


def test_lead_down_is_said_here_not_later(world):
    """cli.md item 3: if your lead is down, anchor says so HERE."""
    _, reg, trk = world
    panes = NullPanes(); panes._exists = False
    p = anchor("ellie", reg, panes, plate=lambda w, _t=trk: plate(_t, w))
    assert p.lead_up is False
    assert "DOWN" in p.render()


def test_lead_state_unknown_is_not_up(world):
    """No pane on the card = we could not look. Never render that as 'up'.

    This is exit code 2's whole reason for existing: a check that couldn't reach
    its target reported CLEAR.
    """
    tmp, _, trk = world
    crew = tmp / "crew"
    _card(crew, "leadnopane", role="lead", reports_to="arnold")
    _card(crew, "kid", role="worker", reports_to="leadnopane", pane="%9")
    p = anchor("kid", FilesRegistry(crew), NullPanes(), plate=lambda w, _t=trk: plate(_t, w))
    assert p.lead_up is None
    out = p.render()
    assert "UNKNOWN" in out
    assert "up. Your stop events go to them." not in out


def test_orphan_is_loud(world):
    """An orphan's stop events go nowhere. That is the finding, not a footnote."""
    _, reg, trk = world
    p = anchor("arya", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w))
    assert p.lead is None
    assert "ORPHAN" in p.render()


def test_card_naming_a_missing_lead_refuses(world):
    """A card pointing at a lead who isn't in the registry is broken, not orphaned.

    Refuse (exit 1) rather than silently degrade to "you have no lead" — that
    would turn a broken card into a normal-looking one.
    """
    _, reg, trk = world
    with pytest.raises(LookupError, match="no such agent is in the registry"):
        anchor("ghostlead", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w))


def test_unknown_agent_refuses(world):
    _, reg, trk = world
    with pytest.raises(LookupError, match="no such agent"):
        anchor("nobody-here", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w))


def test_optional_sections_vanish(world):
    """cli.md item 4: with the `none` adapters, those two sections VANISH.

    Absent, not empty. An empty heading claims we looked and found nothing.
    """
    _, reg, trk = world
    bare = anchor("ellie", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w)).render()
    assert "CONTEXT" not in bare
    assert "KNOWN" not in bare

    rich = anchor("ellie", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w),
                 context=["scripts/e2e/den.sh"],
                 knowledge=['"auth-api was cowboy-deployed" — 2026-06-30']).render()
    assert "CONTEXT (bobbin)" in rich
    assert "KNOWN (quipu)" in rich
