"""Saturation: an agent PAST THE 400k CYCLE THRESHOLD is a WALL, not an idle slot.

internal-ref. Three agents (687k, 562k, 524k vs a 400k limit) sat idle for fifteen
hours reading `idle` while the tier piled work on them. The number was on the pane
the whole time and the tier discarded it. Every test here pins the fail-SILENT
case being converted into a reported one — surfaced in the verdict, refused at
dispatch, carried on the stop event.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import triage
from shantytown.triage import Action, work_state, saturated, triage as run_triage


# A real idle-but-saturated footer, verbatim in shape from the live panes.
def _saturated_pane(tokens: float) -> str:
    return ("❯ \n"
            f"                  new task? /clear to save {tokens}k tokens\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents")


IDLE_PANE = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
BUSY_PANE = "✻ Envisioning… (12s · 4.1k tokens · esc to interrupt)"


class _Panes:
    def __init__(self, screen):
        self._s = screen

    def exists(self, pane):
        return True

    def capture(self, pane, history=0, attrs=False):
        return self._s


# --- part 1: work_state surfaces it -----------------------------------------

def test_an_over_limit_idle_agent_is_SATURATED_not_idle():
    assert work_state(_saturated_pane(687.8), ui_up=True) == triage.SATURATED
    assert saturated(_saturated_pane(687.8)) is True


def test_an_under_limit_idle_agent_is_still_idle():
    assert work_state(_saturated_pane(120.0), ui_up=True) == triage.IDLE
    assert saturated(_saturated_pane(120.0)) is False


def test_exactly_at_the_limit_is_saturated():
    # The limit is the line: 400k IS over. An agent at the limit is not "fine".
    assert work_state(_saturated_pane(400.0), ui_up=True) == triage.SATURATED


def test_a_BUSY_saturated_agent_reads_busy_not_saturated():
    """Honest limit, not a gap: while a turn is in flight the runtime replaces the
    '/clear to save' footer with the spinner, so the number is genuinely
    unreadable. We do not guess it — busy wins, and saturation surfaces the moment
    it idles."""
    assert work_state(BUSY_PANE, ui_up=True) == triage.BUSY
    assert saturated(BUSY_PANE) is False   # footer absent -> None -> not over


def test_saturation_never_overrides_a_more_urgent_state():
    # additive by construction: it can only convert what would be IDLE.
    from shantytown.triage import WEDGED
    wedged = "[Process completed]\n" + _saturated_pane(687.0)
    assert work_state(wedged, ui_up=True) == WEDGED


# --- part 2: st go refuses ---------------------------------------------------

def test_st_go_REFUSES_a_saturated_pane():
    d = run_triage(_Panes(_saturated_pane(687.8)), "%1", "some new item")
    assert d.action is Action.CLEAR          # not NUDGE -> dispatch refuses
    assert "cycle threshold" in d.why and "checkpoint" in d.why
    # NO "% of limit" and NO relatedness — 400k is a cycle point, not the ceiling,
    # and the rule is unconditional (Stiwi's correction).
    assert "ratio" not in d.inputs and "overlap" not in d.inputs
    assert "checkpoint" in d.inputs["remedy"] and "THEN /clear" in d.inputs["remedy"]


def test_a_healthy_pane_still_nudges():
    d = run_triage(_Panes(_saturated_pane(50.0)), "%1", "some new item")
    assert d.action is Action.NUDGE


def test_the_refusal_records_the_number_so_it_is_auditable():
    d = run_triage(_Panes(_saturated_pane(524.1)), "%1", "x")
    assert d.inputs["context_k"] == 524.1          # raw depth, not a fraction
    assert d.inputs["cycle_threshold_k"] == 400.0


# --- part 3: the stop event carries it --------------------------------------

def test_a_stop_event_persists_and_reports_saturation(tmp_path):
    from shantytown import stop_event
    from shantytown.events import FilesEvents
    from shantytown.files import FilesRegistry

    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "gennaro.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "sattler", "pane": "p-gennaro"}))
    (crew / "sattler.json").write_text(json.dumps(
        {"role": "administrator", "pane": "p-sattler"}))
    reg = FilesRegistry(crew)
    ev = FilesEvents(tmp_path / "events")

    class _P:
        def exists(self, pane):
            return True

        def capture(self, pane, history=0, attrs=False):
            return _saturated_pane(687.8) if pane == "p-gennaro" else IDLE_PANE

        def cmdline(self, pane):
            return None

    assert stop_event._send(reg, ev, _P(), "gennaro", root=tmp_path) == 0
    [got] = ev.drain("sattler")
    assert got.context_k == 687.8

    # and it survives a round trip through the store, not just in memory
    reread = FilesEvents(tmp_path / "events")
    (reread.root / f"{got.id}.json").write_text(
        (ev.root / f"{got.id}.json").read_text())


def test_the_drain_message_tells_the_destination_not_to_pile_on(tmp_path):
    from shantytown import stop_event
    from shantytown.events import FilesEvents

    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="sattler", frm="gennaro", reason=None, rose=False, context_k=687.8)
    reason = stop_event._compose_reason(ev.drain("sattler"),
                                        {"gennaro": triage.SATURATED}, now=0.0,
                                        deferred=0)
    assert "CYCLE THRESHOLD" in reason
    assert "687k" in reason and "%" not in reason   # raw depth, no "% of limit"
    assert "do NOT hand it the next item" in reason and "CHECKPOINTS" in reason


def test_a_clean_stop_says_nothing_about_saturation(tmp_path):
    from shantytown import stop_event
    from shantytown.events import FilesEvents

    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="sattler", frm="gennaro", reason=None, rose=False, context_k=42.0)
    reason = stop_event._compose_reason(ev.drain("sattler"), {}, now=0.0, deferred=0)
    assert "SATURATED" not in reason


def test_context_k_None_is_not_reported_as_fine(tmp_path):
    """A stop taken mid-turn has no footer. None must round to 'not reported',
    never to a low number that reads as healthy."""
    from shantytown import stop_event
    from shantytown.events import FilesEvents

    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="sattler", frm="gennaro", reason=None, rose=False, context_k=None)
    reason = stop_event._compose_reason(ev.drain("sattler"), {}, now=0.0, deferred=0)
    assert "SATURATED" not in reason and "context" not in reason.lower()


def test_events_written_before_this_field_still_read(tmp_path):
    from shantytown.events import FilesEvents
    root = tmp_path / "events"; root.mkdir()
    (root / "ev-1.json").write_text(json.dumps(
        {"to": "sattler", "frm": "gennaro", "reason": None, "rose": False,
         "delivered": False}))
    [got] = FilesEvents(root).drain("sattler")
    assert got.context_k is None
