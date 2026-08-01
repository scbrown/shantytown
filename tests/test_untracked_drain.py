"""The lead's END of the untracked-work escalation (aegis-fv2zc).

The alert rides the stop-event channel because that is the only channel that
reaches a destination's MODEL — but it is NOT a stop, and these pin the two
places that difference has to be honoured. Both were live bugs waiting: the
drain's defer gate would have held back exactly the true alerts, and the
renderer would have told a coordinator that a working agent had stopped.
"""
from __future__ import annotations

import json
from dataclasses import replace

from shantytown import triage
from shantytown.events import FilesEvents
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent
from shantytown.stop_event import _compose_reason, _drain
from shantytown.tier import Reason, is_governance


def _events(tmp_path) -> FilesEvents:
    return FilesEvents(tmp_path / "events")


def _alert(ev: FilesEvents, frm="weaver", to="sattler", rose=False):
    return ev.persist(to=to, frm=frm, reason=Reason.UNTRACKED_WORK.value,
                      rose=rose, item=None, item_status=None)


def _stop(ev: FilesEvents, frm="tim", to="sattler"):
    return ev.persist(to=to, frm=frm, reason=None, rose=False,
                      item="st-9", item_status="in_progress")


def test_untracked_work_is_a_governance_reason():
    assert is_governance(Reason.UNTRACKED_WORK.value)
    assert not is_governance(Reason.LEAD_UNREACHABLE.value)
    assert not is_governance(None)


# --- the renderer -------------------------------------------------------------

def test_an_alert_is_never_rendered_as_a_stop(tmp_path):
    ev = _events(tmp_path)
    text = _compose_reason([_alert(ev)], {}, now=0.0)
    assert "WORKING UNTRACKED" in text
    assert "have NOT stopped" in text
    assert "agent(s) stopped" not in text, (
        "an untracked-work alert rendered under the stopped header states the "
        "exact opposite of the fact it exists to report")


def test_alerts_and_real_stops_render_as_separate_sections(tmp_path):
    ev = _events(tmp_path)
    text = _compose_reason([_alert(ev), _stop(ev)], {"tim": triage.IDLE}, now=0.0)
    assert "WORKING UNTRACKED" in text and "weaver" in text
    assert "1 agent(s) stopped" in text and "tim" in text
    # The alert leads: an agent burning turns untracked is the thing to handle
    # before triaging who has stopped.
    assert text.index("WORKING UNTRACKED") < text.index("agent(s) stopped")


def test_the_alert_says_what_to_do(tmp_path):
    ev = _events(tmp_path)
    text = _compose_reason([_alert(ev)], {}, now=0.0)
    assert "st go" in text, "a coordinator alert with no action is a notification"


def test_the_age_is_not_doubled(tmp_path):
    """_age carries its own 'ago'. The first live run of this section printed
    'as of 6s ago ago' — caught by reading real output, not by a test, which is
    why this one exists now."""
    ev = _events(tmp_path)
    e = _alert(ev)
    text = _compose_reason([e], {}, now=e.ts + 6)
    assert "6s ago" in text and "ago ago" not in text


def test_an_unstamped_alert_says_age_unknown(tmp_path):
    """The doubling bug's other half: 'age unknown ago' is not English, and
    'age unknown' is the one answer that is never wrong for an unstamped event."""
    ev = _events(tmp_path)
    e = _alert(ev)
    text = _compose_reason([replace(e, ts=0.0)], {}, now=1000.0)
    assert "age unknown" in text and "unknown ago" not in text


def test_repeat_alerts_about_one_agent_collapse_to_one_line(tmp_path):
    ev = _events(tmp_path)
    for _ in range(3):
        _alert(ev)
    text = _compose_reason([e for e in ev.pending("sattler")], {}, now=0.0)
    assert "1 agent(s) WORKING UNTRACKED" in text
    assert text.count("- weaver") == 1


def test_a_risen_alert_says_the_lead_was_unreachable(tmp_path):
    """Q3 carries through: an alert that rose past a down lead must say so, or
    the administrator reads it as routine traffic it was never meant to see."""
    ev = _events(tmp_path)
    text = _compose_reason([_alert(ev, rose=True)], {}, now=0.0)
    assert "ROSE" in text and "unreachable" in text


# --- the defer gate -----------------------------------------------------------

# The same marker tests/test_stop_event.py uses for a mid-flight pane. Real
# chrome, not a sentinel: triage.mid_flight reads the tail of the actual screen.
BUSY_SCREEN = "✻ Envisioning… (39s · ↑ 1.2k tokens · esc to interrupt)"


class _Panes:
    """Every pane exists and shows an agent MID-FLIGHT — the state the defer gate
    holds events back for."""

    def exists(self, _p):
        return True

    def capture(self, _p, history=None, attrs=False):
        return BUSY_SCREEN


def _reg(tmp_path):
    r = FilesRegistry(tmp_path / "crew")
    r.set(Agent(name="sattler", role="administrator", reports_to=None, pane="p-s"))
    r.set(Agent(name="weaver", role="worker", reports_to="sattler", pane="p-w"))
    return r


def test_an_alert_is_delivered_even_though_its_sender_is_busy(tmp_path, capsys):
    """THE ONE THAT MATTERS. The defer gate exists so a TURN BOUNDARY does not
    wake a coordinator for a mid-flight agent (aegis-w9z1) — but "that agent is
    mid-flight" IS this alert's content. Through the normal gate, every true
    alert would be held back and only the stale ones released.
    """
    ev = _events(tmp_path)
    _alert(ev)
    rc = _drain(ev, "sattler", _reg(tmp_path), _Panes(),
                shows_ready_ui=lambda _s: False,   # mid-flight: no ready UI
                awaiting_answer=lambda _s: False)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "WORKING UNTRACKED" in out["reason"]
    assert ev.pending("sattler") == [], "delivered, so block-once marked it"


def test_a_real_stop_from_a_busy_sender_is_still_deferred(tmp_path, capsys):
    """The gate still works for what it was built for — this change widens
    nothing except the one reason that means the opposite."""
    ev = _events(tmp_path)
    _stop(ev, frm="weaver")
    rc = _drain(ev, "sattler", _reg(tmp_path), _Panes(),
                shows_ready_ui=lambda _s: False,
                awaiting_answer=lambda _s: False)
    assert rc == 0
    assert capsys.readouterr().out == "", "a mid-flight sender's stop must not deliver"
    assert len(ev.pending("sattler")) == 1, "deferred, not dropped"
