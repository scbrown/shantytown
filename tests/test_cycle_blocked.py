"""A REFUSED cycle request must not render as a cycle in flight (aegis-7xptd5).

Measured 2026-09-01 18:20Z in the st-tend journal: five agents (ellie, harding,
malcolm, muldoon, dearing) plus sattler had REQUESTED cycles that tend REFUSED on
dirty or unpushed trees — "the request stays pending" — and `st crew` rendered
every one of them as `cycling`, indistinguishable from a cycle in progress, for
over an hour. The coordinator read "6 planned context cycle(s)" as progress. It
was six stalls.

The two states are the same OBSERVABLE — a record in cycle-requests.json — and
they need opposite responses: a cycle in flight is waited out; a refused one needs
somebody to commit a tree. Worse, nobody told the AGENT, which is the one party
that can clear a dirty tree, and `st cycle --self` had explicitly told it to
expect nothing ("you stay up until it does").

These tests pin three things: the refusal is RECORDED, `st crew` renders it
distinctly, and the agent is told ONCE per refusal reason.
"""
from __future__ import annotations

from shantytown import cycle as cycle_mod
from shantytown.notify import CycleBlockedNotifier
from shantytown.protocols import Agent

from tests.test_cycle import _Panes, _Reg


class _Risk:
    def __init__(self, path):
        self.path = path


def _requests(tmp_path):
    return cycle_mod.Requests(tmp_path)


# --- the record: a refusal must be stamped on the pending request -----------

def test_a_refusal_is_recorded_on_the_pending_request(tmp_path):
    r = _requests(tmp_path)
    r.request("malcolm", "mid-way through the scaling probe")
    assert r.pending()["malcolm"]["refused"] is None, "a fresh request is not refused"

    assert r.mark_refused("malcolm", "work would be lost or stranded",
                          [_Risk("/home/x/quipu-wt/malcolm"), _Risk("/home/x/gt")])

    rec = r.pending()["malcolm"]
    assert rec["refused"]["reason"].startswith("work would be lost")
    assert rec["refused"]["paths"] == ["/home/x/quipu-wt/malcolm", "/home/x/gt"]
    # The checkpoint survives — a refusal annotates the request, never replaces it.
    assert rec["checkpoint"] == "mid-way through the scaling probe"


def test_marking_an_agent_that_never_REQUESTED_a_cycle_mints_nothing(tmp_path):
    """An operator's ad-hoc `st cycle <agent>` refusing must not create a request.

    Otherwise the display this bead fixes would start inventing stalls: every
    refused hand-run cycle would appear in `st crew` as an agent waiting for a
    cycle it never asked for.
    """
    r = _requests(tmp_path)
    assert r.mark_refused("ellie", "no checkpoint", []) is False
    assert r.pending() == {}


def test_a_new_request_re_arms_and_clears_the_old_refusal(tmp_path):
    """The agent fixed its tree and asked again. Carrying the stale refusal
    forward would report a stall that is already resolved."""
    r = _requests(tmp_path)
    r.request("harding", "first")
    r.mark_refused("harding", "work would be lost or stranded", [_Risk("/t")])
    r.request("harding", "second, after committing")
    assert r.pending()["harding"]["refused"] is None


def test_clearing_a_served_request_drops_the_refusal_with_it(tmp_path):
    r = _requests(tmp_path)
    r.request("dearing", "ckpt")
    r.mark_refused("dearing", "work would be lost or stranded", [_Risk("/t")])
    r.clear("dearing")
    assert r.pending() == {}


def test_refusal_summary_names_the_FIRST_blocking_path(tmp_path):
    r = _requests(tmp_path)
    r.request("malcolm", "ckpt")
    r.mark_refused("malcolm", "work would be lost or stranded",
                   [_Risk("/a"), _Risk("/b")])
    path, why = cycle_mod.refusal_summary(r.pending()["malcolm"])
    assert path == "/a"
    assert "lost or stranded" in why


def test_refusal_summary_on_an_unrefused_request_is_empty(tmp_path):
    r = _requests(tmp_path)
    r.request("ian", "ckpt")
    assert cycle_mod.refusal_summary(r.pending()["ian"]) == ("", "")


def test_a_legacy_bare_string_record_can_still_be_annotated(tmp_path):
    """Request records predating the dict form must not be un-stallable — a
    legacy request is exactly as capable of sitting refused for an hour."""
    import json
    p = tmp_path / "notify" / "cycle-requests.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"muldoon": "an old bare-string checkpoint"}))
    r = _requests(tmp_path)
    assert r.mark_refused("muldoon", "work would be lost or stranded", [_Risk("/t")])
    rec = r.pending()["muldoon"]
    assert rec["checkpoint"] == "an old bare-string checkpoint"
    assert rec["refused"]["paths"] == ["/t"]


# --- st crew: the refused request must render distinctly --------------------

def _crew_states(cycling, cycle_blocked, names=("malcolm", "ellie")):
    from shantytown.cli import _crew_states
    agents = [Agent(name=n, role="worker", pane=None) for n in names]
    return {ag.name: state for ag, state, _w, _p in _crew_states(
        agents, _Panes({}), None, cycling=cycling, cycle_blocked=cycle_blocked)}


def test_a_refused_request_renders_cycle_blocked_not_cycling():
    """THE REGRESSION. Under the bug both agents printed `cycling`."""
    states = _crew_states(cycling={"malcolm"}, cycle_blocked={"ellie"})
    assert states["malcolm"] == "cycling"
    assert states["ellie"] == "cycle-blocked", (
        "a refused request rendered as a cycle in flight — the aegis-7xptd5 defect")


def test_cycle_blocked_wins_over_cycling_for_the_same_agent():
    """Belt and braces on the caller's set arithmetic: if an agent somehow
    appears in both, the REFUSAL is the more urgent truth and must show."""
    states = _crew_states(cycling={"ellie"}, cycle_blocked={"ellie"})
    assert states["ellie"] == "cycle-blocked"


# --- the agent gets told, once per reason -----------------------------------

def _notifier(tmp_path, panes=None):
    panes = panes if panes is not None else _Panes({"shanty-malcolm": ""})
    reg = _Reg([Agent(name="malcolm", role="worker", pane="shanty-malcolm")])
    return CycleBlockedNotifier(tmp_path, reg, panes), panes


def _pending(paths, reason="work would be lost or stranded"):
    return {"malcolm": {"checkpoint": "c", "checkpoint_bead": "",
                        "quipu_nodes": [],
                        "refused": {"reason": reason, "paths": list(paths),
                                    "at": 0}}}


def test_the_agent_is_told_which_path_blocks_its_cycle(tmp_path):
    n, panes = _notifier(tmp_path)
    assert n.sweep(_pending(["/home/x/quipu-wt/malcolm"])) == ["malcolm"]
    (pane, text), = panes.sent
    assert pane == "shanty-malcolm"
    assert "/home/x/quipu-wt/malcolm" in text
    assert "st push" in text, "the message must name the remedy, not just the fault"


def test_the_same_refusal_is_NOT_repeated_every_pass(tmp_path):
    """tend runs every 5 minutes and a dirty tree stays dirty. Twelve identical
    interruptions an hour is how a channel stops being read."""
    n, panes = _notifier(tmp_path)
    pending = _pending(["/t"])
    assert n.sweep(pending) == ["malcolm"]
    assert n.sweep(pending) == []
    assert n.sweep(pending) == []
    assert len(panes.sent) == 1


def test_a_CHANGED_blockage_speaks_again(tmp_path):
    """The agent committed the named tree and stalled on the next one. That is
    new information, and an agent that acted deserves to hear it was not enough."""
    n, panes = _notifier(tmp_path)
    assert n.sweep(_pending(["/first"])) == ["malcolm"]
    assert n.sweep(_pending(["/second"])) == ["malcolm"]
    assert len(panes.sent) == 2


def test_a_request_no_longer_refused_re_arms_the_notice(tmp_path):
    n, panes = _notifier(tmp_path)
    assert n.sweep(_pending(["/t"])) == ["malcolm"]
    # served, or re-requested: nothing pending is refused any more
    assert n.sweep({}) == []
    # a LATER refusal on the same path must speak, not be silenced by the ledger
    assert n.sweep(_pending(["/t"])) == ["malcolm"]


def test_an_unpushed_pane_is_retried_rather_than_recorded_as_told(tmp_path):
    """Fail-open. A pane that could not be reached was not told, so ledgering it
    would lose the notice permanently."""
    n, _ = _notifier(tmp_path, panes=_Panes({}))     # no such pane
    assert n.sweep(_pending(["/t"])) == []
    # pane comes back
    n2, panes2 = _notifier(tmp_path)
    assert n2.sweep(_pending(["/t"])) == ["malcolm"]


def test_an_unrefused_pending_request_is_never_notified(tmp_path):
    """A cycle genuinely in flight must not be reported to its agent as blocked."""
    n, panes = _notifier(tmp_path)
    assert n.sweep({"malcolm": {"checkpoint": "c", "refused": None}}) == []
    assert panes.sent == []


# --- the control: the OLD behaviour, pinned so the two worlds differ ---------

def test_control_the_old_undifferentiated_reading_is_reproducible():
    """A test that passes in both worlds proves nothing, so pin the world this
    fix leaves behind.

    Before aegis-7xptd5, `st crew` did exactly this: every pending request went
    into one `cycling` set, refused or not. Feeding both names as `cycling` with
    an empty `cycle_blocked` reproduces that reading verbatim — both agents print
    `cycling` — and the test above shows the same two agents now printing
    different states. If someone re-collapses the split, that test fails while
    this one still passes, which is the signal.
    """
    states = _crew_states(cycling={"malcolm", "ellie"}, cycle_blocked=set())
    assert states == {"malcolm": "cycling", "ellie": "cycling"}
