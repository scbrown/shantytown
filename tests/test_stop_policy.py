"""stop_policy — ONE stop decision. The acceptance criteria of
docs/stop-policy-spec.md, one test each.

Every one of these fails on the two-independent-hooks design it replaces, which
is the point: the defect was never in either hook, it was that neither could see
the other's verdict.
"""
from __future__ import annotations

from shantytown.answer import Answer

import json

import pytest

from shantytown import stop_policy as sp
from shantytown.config import Hibernate
from shantytown.events import StopEvent
from shantytown.tier import Reason
from shantytown import stop_event as se


def _ev(frm="billy", reason=None, rose=False, eid="ev-1"):
    return StopEvent(id=eid, to="sattler", frm=frm, reason=reason, rose=rose)


def _inp(**kw):
    base = dict(me="sattler", role="administrator")
    base.update(kw)
    return sp.Inputs(**base)


# --- criterion 1: Rule Zero overrides hibernate, and SAYS SO -----------------

def test_rule_zero_overrides_hibernate_and_names_it():
    """THE defect. With `schedule` not elapsed, the old design let the drain
    decline while feed_check blocked anyway — the admin stayed awake and nothing
    said why, so a documented knob looked broken."""
    v = sp.decide(_inp(
        pending=[_ev()],
        free_feedable=["billy", "tim"], dispatchable=4,
        hibernate=Hibernate(enabled=True, max_quiet_minutes=30),
        minutes_quiet=1.0))
    assert v.block and v.by == sp.BY_RULE_ZERO
    assert "billy" in v.reason and "4 dispatchable" in v.reason
    assert "OVERRIDDEN" in v.reason, (
        "the override must be named at the moment it happens, or the operator "
        "reads the config, reads the docs, and watches nothing happen")


def test_rule_zero_says_nothing_about_hibernate_when_it_is_off():
    v = sp.decide(_inp(free_feedable=["billy"], dispatchable=1))
    assert v.block and v.by == sp.BY_RULE_ZERO
    assert "OVERRIDDEN" not in v.reason


# --- criterion 2: quiet when quiet is correct, and NOTHING is consumed -------

def test_hibernate_allows_when_there_is_nothing_to_dispatch():
    v = sp.decide(_inp(
        pending=[_ev(), _ev(eid="ev-2")],
        free_feedable=[], dispatchable=0,
        hibernate=Hibernate(enabled=True, max_quiet_minutes=60),
        minutes_quiet=5.0))
    assert not v.block and v.by == sp.BY_HIBERNATE
    assert "2 event(s) left PENDING" in v.reason
    assert "55 min of quiet remaining" in v.reason


def test_a_sleeping_decision_consumes_nothing(tmp_path):
    """The work-loss invariant, at the level that can now break it: rank 3 ALLOWS
    without ever reaching the drain that marks events delivered."""
    from shantytown.events import FilesEvents
    events = FilesEvents(tmp_path / "events")
    events.persist("sattler", "billy", None, False)
    events.persist("sattler", "tim", None, False)

    inp = _inp(pending=list(events.pending("sattler")),
               hibernate=Hibernate(enabled=True, max_quiet_minutes=0))
    assert not sp.decide(inp).block
    assert len(events.pending("sattler")) == 2


def test_hibernate_sleeps_through_an_ORDINARY_report():
    """Rank 3 above rank 4 is the feature: with nothing dispatchable, 'kelly
    stopped' is informational and there is no decision to make."""
    v = sp.decide(_inp(pending=[_ev(frm="kelly")],
                       hibernate=Hibernate(enabled=True, max_quiet_minutes=0)))
    assert not v.block and v.by == sp.BY_HIBERNATE


def test_the_quiet_bound_forces_a_read_eventually():
    """max_quiet_minutes is not a wake schedule — it bounds how long a pending
    batch may sit unread while nothing pushes."""
    hib = Hibernate(enabled=True, max_quiet_minutes=30)
    assert not sp.decide(_inp(pending=[_ev()], hibernate=hib, minutes_quiet=5)).block
    v = sp.decide(_inp(pending=[_ev()], hibernate=hib, minutes_quiet=31))
    assert v.block and v.by == sp.BY_EVENTS


def test_zero_disables_the_bound():
    """A legitimate choice: `st tend` pushes, and a push is a wake with a REASON."""
    v = sp.decide(_inp(pending=[_ev()], minutes_quiet=99999,
                       hibernate=Hibernate(enabled=True, max_quiet_minutes=0)))
    assert not v.block


def test_never_woken_reads_the_batch_once_to_start_the_clock():
    v = sp.decide(_inp(pending=[_ev()], minutes_quiet=None,
                       hibernate=Hibernate(enabled=True, max_quiet_minutes=30)))
    assert v.block and v.by == sp.BY_EVENTS


# --- criterion 3: urgent is never slept on ----------------------------------

def test_a_risen_event_beats_hibernate_AND_an_empty_fleet():
    v = sp.decide(_inp(pending=[_ev(reason=Reason.LEAD_UNREACHABLE.value, rose=True)],
                       hibernate=Hibernate(enabled=True, max_quiet_minutes=0)))
    assert v.block and v.by == sp.BY_URGENT


def test_a_governance_alert_beats_hibernate():
    from shantytown.tier import GOVERNANCE_REASONS
    v = sp.decide(_inp(pending=[_ev(reason=sorted(GOVERNANCE_REASONS)[0])],
                       hibernate=Hibernate(enabled=True, max_quiet_minutes=0)))
    assert v.block and v.by == sp.BY_URGENT


def test_urgent_outranks_rule_zero_too():
    """Rank 1 is first for a reason: an untracked-work alert says an agent is
    working RIGHT NOW outside the tracker."""
    v = sp.decide(_inp(pending=[_ev(rose=True)],
                       free_feedable=["billy"], dispatchable=3))
    assert v.by == sp.BY_URGENT


# --- the ordinary paths ------------------------------------------------------

def test_events_block_when_hibernate_is_off():
    v = sp.decide(_inp(pending=[_ev()]))
    assert v.block and v.by == sp.BY_EVENTS


def test_nothing_at_all_allows():
    v = sp.decide(_inp())
    assert not v.block and v.by == sp.BY_NOTHING
    assert v.reason == "", "an allow with nothing to say must say nothing"


def test_an_empty_backlog_is_NOT_reported_as_hibernating():
    """Measured live: with 0 events pending it announced a hibernation, i.e. it
    claimed to be holding a backlog back when there was none. An operator reading
    that goes looking for events that do not exist."""
    v = sp.decide(_inp(hibernate=Hibernate(enabled=True, max_quiet_minutes=60)))
    assert not v.block
    assert v.by == sp.BY_NOTHING, "the ordinary idle stop, not a hibernation"


# --- criterion 4: exactly ONE pane sweep -------------------------------------

class _CountingPanes:
    def __init__(self, agents):
        self.captures = 0
        self._agents = agents

    def exists(self, pane):
        return True

    def capture(self, pane, history=0, attrs=False):
        self.captures += 1
        return "> "

    def cmdline(self, pane):
        return "python -m shantytown.stop_event send"


def test_one_invocation_sweeps_the_panes_ONCE(tmp_path, monkeypatch):
    """The guard against a fourth sweep creeping back. drain scraped per event
    sender, feed_check scraped the roster, and hibernate scraped it again — three
    sweeps, three chances to disagree about who is busy, in one turn boundary."""
    from shantytown import feed_check as feed_mod
    panes = _CountingPanes(["billy"])
    monkeypatch.setattr(feed_mod, "gate_inputs",
                        lambda *a, **k: (panes.capture("p") and [], 0, []))

    class _Reg:
        def get(self, n):
            from shantytown.protocols import Agent
            return Agent(name=n, role="administrator", pane="p-sattler")

        def all(self):
            return Answer.complete_read([self.get("sattler")], how="test registry")

    class _Events:
        def pending(self, me):
            return []

    sp.gather(tmp_path, "sattler", reg=_Reg(), panes=panes,
              runtime=object(), events=_Events())
    assert panes.captures == 1, f"expected ONE sweep, got {panes.captures}"


# --- criterion 5: fail open --------------------------------------------------

def test_an_unreadable_card_allows_the_stop_but_says_what_it_disabled(tmp_path, capsys):
    """Degrading to `worker` turns OFF both Rule Zero and hibernate. Failing open
    is right; doing it silently would make a coordinator quietly stop
    coordinating — the same shape as the defects this module consolidates."""
    class _Boom:
        def get(self, _n):
            raise RuntimeError("registry unreadable")

        def all(self):
            return Answer.complete_read([], how="test registry")

    class _Events:
        def pending(self, me):
            return []

    rc = sp.run(tmp_path, "sattler", reg=_Boom(), panes=object(),
                runtime=object(), events=_Events())
    assert rc == 0
    out = capsys.readouterr()
    assert out.out == "", "a fail-open must emit NO block payload"
    assert "could not read my own card" in out.err
    assert "Rule Zero and hibernate are OFF" in out.err


def test_a_hard_failure_in_gathering_allows_the_stop(tmp_path, capsys):
    rc = sp.run(tmp_path, "sattler", reg=object())   # no .get, no .all
    assert rc == 0
    out = capsys.readouterr()
    assert out.out == "", "a fail-open must emit NO block payload"
    assert "ALLOWING" in out.err or "could not read my own card" in out.err


def test_a_broken_gate_does_not_block(tmp_path, monkeypatch):
    """Rule Zero unreachable (bd down, tmux down) must not trap the coordinator."""
    from shantytown import feed_check as feed_mod

    def boom(*a, **k):
        raise RuntimeError("bd unreachable")

    monkeypatch.setattr(feed_mod, "gate_inputs", boom)

    class _Reg:
        def get(self, n):
            from shantytown.protocols import Agent
            return Agent(name=n, role="administrator", pane="p")

        def all(self):
            return Answer.complete_read([self.get("sattler")], how="test registry")

    class _Events:
        def pending(self, me):
            return []

    inp = sp.gather(tmp_path, "sattler", reg=_Reg(), panes=object(),
                    runtime=object(), events=_Events())
    assert inp.free_feedable == [] and inp.dispatchable == 0
    assert not sp.decide(inp).block


# --- non-administrators ------------------------------------------------------

def test_a_worker_gets_no_rule_zero_and_no_hibernate(tmp_path):
    """A lead's drain is how it ABSORBS its reports; sleeping it would hold back
    the absorbing half of the tier while the delegating half kept running."""
    class _Reg:
        def get(self, n):
            from shantytown.protocols import Agent
            return Agent(name=n, role="worker", pane="p")

        def all(self):
            return Answer.complete_read([self.get("billy")], how="test registry")

    class _Events:
        def pending(self, me):
            return []

    inp = sp.gather(tmp_path, "billy", reg=_Reg(), panes=object(),
                    runtime=object(), events=_Events())
    assert inp.hibernate is None
    assert inp.free_feedable == [] and inp.dispatchable == 0


# --- criterion 7: the wiring checker still sees the admin --------------------

def test_the_unified_entry_still_reads_as_DRAIN_wiring(tmp_path):
    """A checker that cannot see the thing it checks for is the exact defect
    `roles --check` and `st tend` exist to catch — an administrator on the new
    chain must not read as DEAF."""
    from shantytown.runtime import settings_for_role, stop_directions_in
    (tmp_path / "administrator.settings.json").write_text(
        json.dumps(settings_for_role("administrator")))
    assert stop_directions_in(tmp_path / "administrator.settings.json") == {"drain"}


# ---------------------------------------------------------------------------
# aegis-d1qko — BLOCK on the DELIVERABLE set, not on raw pending.
# ---------------------------------------------------------------------------

def _d1qko_ev(frm="ellie", reason=None, rose=False, ts=None):
    import time as _t
    return StopEvent(id="ev-1", to="maldoon", frm=frm, reason=reason, rose=rose,
                     ts=_t.time() if ts is None else ts)


def test_a_held_event_does_not_block_the_coordinator(tmp_path):
    """THE BUG (sattler, 2026-08-24). Rank 4's own docstring says "a DELIVERABLE
    pending event" and the code read `pending`. _drain holds an event back while
    its sender is mid-flight, so the coordinator BLOCKED on events the very next
    call then declined to hand over: ~10 no-op turns in one night against 2 held
    events. On a healthy fleet the senders keep working, so that is indefinite."""
    inp = sp.Inputs(me="maldoon", role="administrator",
                             pending=[_d1qko_ev(frm="ellie")],
                             busy_senders={"ellie"})
    assert inp.deliverable == [], "a busy sender's event is not deliverable"
    v = sp.decide(inp)
    assert v.block is False, \
        "blocked on an event the drain will refuse to deliver — a no-op wake"


def test_a_deliverable_event_still_blocks(tmp_path):
    """The other half: this must not turn into 'never block'."""
    inp = sp.Inputs(me="maldoon", role="administrator",
                             pending=[_d1qko_ev(frm="ellie")], busy_senders=set())
    v = sp.decide(inp)
    assert v.block is True
    assert "1 pending event(s) to deliver" in v.reason


def test_the_block_line_names_the_held_back_remainder(tmp_path):
    """Its absence is what sent a coordinator to the events directory hunting a
    stuck-delivery bug that was disclosed design all along."""
    inp = sp.Inputs(me="maldoon", role="administrator",
                             pending=[_d1qko_ev(frm="ellie"), _d1qko_ev(frm="tim")],
                             busy_senders={"tim"})
    v = sp.decide(inp)
    assert v.block is True
    assert "1 pending event(s) to deliver" in v.reason
    assert "1 more held back" in v.reason


def test_urgent_events_are_deliverable_even_from_a_busy_sender(tmp_path):
    """_drain NEVER defers a governance alert or a risen event, so the two
    filters must agree — otherwise a rank-1 block hands over nothing."""
    gov = _d1qko_ev(frm="ellie", reason="untracked-work")
    inp = sp.Inputs(me="maldoon", role="administrator",
                             pending=[gov], busy_senders={"ellie"})
    assert len(inp.urgent) == 1
    assert inp.deliverable == [gov], \
        "urgent-but-not-deliverable would block forever and deliver nothing"


def test_a_held_event_past_the_ceiling_becomes_deliverable(tmp_path):
    """stop_policy and _drain must share the ceiling, or they disagree again."""
    import time as _t
    old = _d1qko_ev(frm="ellie", ts=_t.time() - (se.DEFER_MAX_AGE_S + 60))
    inp = sp.Inputs(me="maldoon", role="administrator",
                             pending=[old], busy_senders={"ellie"})
    assert len(inp.deliverable) == 1, \
        "past the ceiling _drain WILL deliver it, so rank 4 must block for it"
