"""The anchor's tests. One of these is the reason anchor exists as its own module.

- test_anchor_writes_nothing: asserted against the FILESYSTEM, not against the
  docstring that claims purity. Gas Town's primer mutates state from a hook,
  which is why "did I get primed?" became unanswerable. A comment saying "pure
  read" is exactly the kind of claim we keep finding untrue.
- test_lead_down / test_lead_unknown: anchor's job is to tell you WHERE your stop
  events go HERE, not to leave you to discover it when you stall. Both branches
  run, because a warning that has never fired is not a warning.
- ...and the branches assert what the RENDER DOES NOT SAY as well as what it
  does. anchor said "your stop events go nowhere" for two years about a tier
  that was routing them correctly the whole time (aegis-j1dzp). A test that only
  checks the new sentence is present passes on a render that still carries the
  old lie beside it.
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
    """cli.md item 3: if your lead is unreachable, anchor says so HERE.

    And says WHAT HAPPENS, which is not the same claim. This line used to read
    "your stop events go nowhere right now" — a statement about DELIVERY
    inferred from a probe of LIVENESS — and it was FALSE: tier's Q3 rises the
    event to the administrator with reason `lead-unreachable`. A worker read it,
    reported it upward as fact, and a working mechanism was nearly rebuilt
    (aegis-j1dzp). The assertion that the falsehood is ABSENT is the point of
    this test; asserting the replacement wording alone would pass on a render
    that said both.
    """
    _, reg, trk = world
    panes = NullPanes(); panes._exists = False
    p = anchor("ellie", reg, panes, plate=lambda w, _t=trk: plate(_t, w))
    assert p.lead_up is False
    out = p.render()
    assert "UNREACHABLE" in out
    assert "go nowhere" not in out
    assert "arnold" in out                       # named the actual destination
    assert "lead-unreachable" in out             # named the routing reason


def test_lead_unreachable_names_the_administrator_route_stop_would_pick(world):
    """anchor's claim and route_stop's behaviour, checked against each other.

    Not a re-assertion of the string: the destination anchor PRINTS must be the
    destination the router CHOOSES. These were computed separately before, which
    is exactly how a diagnostic drifts from the mechanism it describes.
    """
    from shantytown.tier import route_stop
    _, reg, trk = world
    panes = NullPanes(); panes._exists = False
    p = anchor("ellie", reg, panes, plate=lambda w, _t=trk: plate(_t, w))
    routing = route_stop(reg, "ellie", lead_is_up=lambda _n: False)
    assert routing.rose is True
    assert routing.to == p.admin == "arnold"
    assert routing.to in p.render()


def test_lead_unreachable_with_no_administrator_says_stranded(tmp_path: Path):
    """The ONE case where the old wording was true — and it must still be loud.

    Fixing an overstatement must not swing into an understatement: with nobody
    to rise to, route_stop RAISES, and the worker has to know.
    """
    crew = tmp_path / "crew"
    _card(crew, "solo", role="worker", reports_to="boss", pane="%5")
    _card(crew, "boss", role="lead", pane="%1")               # no administrator
    panes = NullPanes(); panes._exists = False
    p = anchor("solo", FilesRegistry(crew), panes)
    assert p.admin is None
    out = p.render()
    assert "STRANDED" in out
    assert "NO administrator" in out


def test_lead_that_is_the_administrator_does_not_claim_a_rise(tmp_path: Path):
    """route_stop returns to=lead for an administrator lead BEFORE it probes
    liveness — there is nothing above to rise to, so anchor must not promise a
    rise it cannot get. The event is addressed to them and waits on disk."""
    crew = tmp_path / "crew"
    _card(crew, "solo", role="worker", reports_to="boss", pane="%5")
    _card(crew, "boss", role="administrator", pane="%1")
    panes = NullPanes(); panes._exists = False
    p = anchor("solo", FilesRegistry(crew), panes)
    out = p.render()
    assert "RISE TO" not in out
    assert "persist on disk" in out


def test_lead_detail_names_restart_vs_relaunch(world):
    """The two causes of `lead-unreachable` want OPPOSITE remedies. When the
    predicate supplies the distinction, anchor must print it — the coordinator
    who cannot tell them apart absorbs both as noise (tier.LeadStatus)."""
    from shantytown.tier import LeadStatus
    _, reg, trk = world
    p = anchor("ellie", reg, NullPanes(),
               lead_status=lambda n: LeadStatus(False, f"{n} is UP but CANNOT DRAIN — RELAUNCH it"))
    assert p.lead_up is False
    out = p.render()
    assert "CANNOT DRAIN" in out
    assert "RELAUNCH" in out


def test_plain_bool_predicate_carries_no_invented_detail(world):
    """A predicate that returns a bare bool knows no reason. Absent detail stays
    ABSENT — a fabricated cause on an escalation line is worse than none."""
    _, reg, trk = world
    p = anchor("ellie", reg, NullPanes(), lead_status=lambda _n: False)
    assert p.lead_up is False
    assert p.lead_detail == ""
    assert "why:" not in p.render()


def test_lead_up_predicate_means_it_will_drain(world):
    """`up` is whatever the injected predicate says, not whatever the pane says.

    NullPanes reports the pane exists; the predicate says it cannot drain. anchor
    must follow the predicate — a live-but-deaf lead swallows events silently,
    and rendering that as "your stop events go to them" is this same bug pointing
    the other way.
    """
    from shantytown.tier import LeadStatus
    _, reg, trk = world
    p = anchor("ellie", reg, NullPanes(), lead_status=lambda _n: LeadStatus(False, "no drain hook"))
    assert p.lead_up is False
    assert "up. Your stop events go to them." not in p.render()


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


def test_orphan_is_loud_but_says_where_the_stop_actually_goes(world):
    """Being an orphan is the finding. "Go NOWHERE" was not.

    Same defect as the lead-down line and found with it: route_stop's Q4 sends a
    lead-less worker's stop STRAIGHT to the administrator (rose=False). Loud
    stays loud — nobody is triaging this agent's work — but the delivery claim
    has to match the router.
    """
    _, reg, trk = world
    p = anchor("arya", reg, NullPanes(), plate=lambda w, _t=trk: plate(_t, w))
    assert p.lead is None
    out = p.render()
    assert "ORPHAN" in out
    assert "go NOWHERE" not in out
    assert "arnold" in out


def test_orphan_with_no_administrator_really_does_go_nowhere(tmp_path: Path):
    """...and there the old wording was right, so it stays."""
    crew = tmp_path / "crew"
    _card(crew, "arya", role="worker")
    p = anchor("arya", FilesRegistry(crew), NullPanes())
    assert p.admin is None
    out = p.render()
    assert "ORPHAN" in out
    assert "go NOWHERE" in out


def test_administrator_is_not_told_its_stops_go_to_itself(tmp_path: Path):
    """An administrator has no lead, so it lands in the orphan branch. Telling
    it its stops route to itself is true-but-absurd; say the tier ends here."""
    crew = tmp_path / "crew"
    _card(crew, "boss", role="administrator", pane="%1")
    p = anchor("boss", FilesRegistry(crew), NullPanes())
    out = p.render()
    assert "you ARE the administrator" in out
    assert "go NOWHERE" not in out


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
