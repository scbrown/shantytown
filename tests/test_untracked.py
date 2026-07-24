"""untracked — the propulsion-governance nudge (aegis-fv2zc).

The ladder, by mechanism: an injected clock and an injected plate reader, so
"escalates after 12 calls AND 10 minutes" is a test that runs in milliseconds
rather than a claim in a docstring. Every SILENT branch is pinned separately —
the four causes (exempt / holding work / could not look / cooldown) look
identical from outside and this is the only place they are told apart.
"""
from __future__ import annotations

import json

import pytest

from shantytown.events import FilesEvents
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent, WorkItem
from shantytown.tier import Reason
from shantytown.untracked import (ESCALATE, ESCALATE_AFTER_S,
                                  ESCALATE_AFTER_STRIKES, POLL_S, SILENT, WARN,
                                  WARN_COOLDOWN_S, Governor, plate_reader)


class _Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def _crew(root, **who):
    """Write cards. who = {name: (role, reports_to)}."""
    reg = FilesRegistry(root / "crew")
    for name, (role, up) in who.items():
        reg.set(Agent(name=name, role=role, reports_to=up, pane=f"p-{name}"))
    return reg


def _gov(tmp_path, plate, *, clock=None, **who):
    who = who or {"weaver": ("worker", "sattler"), "sattler": ("administrator", None)}
    reg = _crew(tmp_path, **who)
    return Governor(tmp_path, reg, plate, FilesEvents(tmp_path / "events"),
                    now=clock or _Clock())


EMPTY = lambda _who: None                                          # noqa: E731
HELD = lambda who: WorkItem(id="st-1", title="real work",          # noqa: E731
                            status="in_progress", assignee=who)


def _boom(_who):
    raise RuntimeError("bd: connection refused")


# --- the exemptions and the silences -----------------------------------------

def test_administrator_is_exempt():
    """The directive is explicit: coordinators legitimately act with an empty
    hook — dispatching, triage, draining IS the job."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        g = _gov(Path(d), EMPTY, sattler=("administrator", None))
        v = g.check("sattler")
    assert v.action == SILENT and "exempt" in v.why


def test_unknown_agent_is_silent(tmp_path):
    """A session the tier does not own is not governed by it."""
    g = _gov(tmp_path, EMPTY)
    assert g.check("nobody").action == SILENT


def test_work_on_the_hook_is_silent(tmp_path):
    g = _gov(tmp_path, HELD)
    v = g.check("weaver")
    assert v.action == SILENT and v.why == "work is on the hook"


def test_could_not_look_never_warns(tmp_path):
    """THE ONE THAT MATTERS. A store outage must not be rendered as untracked
    work — an agent scolded through a Dolt flap learns to ignore the warning,
    and then it is worth nothing when it is right."""
    g = _gov(tmp_path, _boom)
    v = g.check("weaver")
    assert v.action == SILENT and "could not read" in v.why
    assert v.text == ""


def test_could_not_look_does_not_reset_a_stretch(tmp_path):
    """A flap mid-stretch costs the ladder NOTHING: strikes are neither counted
    nor forgiven while we cannot see."""
    clock = _Clock()
    plate = {"fn": EMPTY}
    g = _gov(tmp_path, lambda who: plate["fn"](who), clock=clock)
    for _ in range(5):
        g.check("weaver")
        clock.advance(POLL_S + 1)
    before = json.loads((tmp_path / "untracked" / "weaver.json").read_text())
    plate["fn"] = _boom
    g.check("weaver")
    after = json.loads((tmp_path / "untracked" / "weaver.json").read_text())
    assert after["strikes"] == before["strikes"] == 5


# --- the warn rung ------------------------------------------------------------

def test_empty_hook_warns_in_band(tmp_path):
    g = _gov(tmp_path, EMPTY)
    v = g.check("weaver")
    assert v.action == WARN
    assert "UNTRACKED WORK" in v.text
    # It must say WHAT TO DO, and name the lead it would escalate to.
    assert "bd create" in v.text and "sattler" in v.text
    # ...and be honest that it is not a block (the directive: nudge first).
    assert "NUDGE, not a block" in v.text


def test_warning_is_rate_limited(tmp_path):
    """50 identical paragraphs in one turn teaches an agent exactly one thing:
    skip anything starting with a warning glyph."""
    clock = _Clock()
    g = _gov(tmp_path, EMPTY, clock=clock)
    assert g.check("weaver").action == WARN
    warned = [g.check("weaver").action for _ in range(5)]
    assert warned == [SILENT] * 5
    clock.advance(WARN_COOLDOWN_S + 1)
    assert g.check("weaver").action == WARN


def _led(tmp_path) -> dict:
    return json.loads((tmp_path / "untracked" / "weaver.json").read_text())


def test_getting_hooked_clears_the_strike_record(tmp_path):
    """A new unhooked stretch deserves the full ladder from the top."""
    clock = _Clock()
    plate = {"fn": EMPTY}
    g = _gov(tmp_path, lambda who: plate["fn"](who), clock=clock)
    g.check("weaver")
    assert _led(tmp_path)["strikes"] == 1
    plate["fn"] = HELD
    clock.advance(POLL_S + 1)
    assert g.check("weaver").action == SILENT
    led = _led(tmp_path)
    assert "strikes" not in led and "escalated" not in led and "since" not in led


def test_a_hooked_agent_does_not_pay_a_store_read_per_tool_call(tmp_path):
    """The poll cache must SURVIVE the reset, or the cost falls entirely on the
    agents doing the right thing: a properly-hooked worker re-reading the tracker
    on every Edit/Write/Bash, forever, at 0.17s and 306KB a time."""
    calls = []

    def counting(who):
        calls.append(1)
        return HELD(who)

    clock = _Clock()
    g = _gov(tmp_path, counting, clock=clock)
    for _ in range(20):
        g.check("weaver")
        clock.advance(1)
    assert len(calls) == 1, f"a hooked agent polled {len(calls)}x in 20 tool calls"


def test_an_escalated_stretch_that_ends_can_escalate_again(tmp_path):
    """The `escalated` marker must not survive the reset — a second stretch of
    untracked work is a second thing the lead needs to know about."""
    clock = _Clock()
    plate = {"fn": EMPTY}
    g = _gov(tmp_path, lambda who: plate["fn"](who), clock=clock)
    per = ESCALATE_AFTER_S / ESCALATE_AFTER_STRIKES + 1
    _grind(g, clock, ESCALATE_AFTER_STRIKES + 1, per)
    assert len(FilesEvents(tmp_path / "events").pending("sattler")) == 1

    plate["fn"] = HELD                       # got hooked: the stretch ended
    clock.advance(POLL_S + 1)
    g.check("weaver")

    plate["fn"] = EMPTY                      # ...and a NEW stretch begins
    clock.advance(POLL_S + 1)
    _grind(g, clock, ESCALATE_AFTER_STRIKES + 1, per)
    assert len(FilesEvents(tmp_path / "events").pending("sattler")) == 2


# --- the escalate rung --------------------------------------------------------

def _grind(g, clock, calls, seconds_each):
    out = []
    for _ in range(calls):
        out.append(g.check("weaver"))
        clock.advance(seconds_each)
    return out


def test_a_fast_burst_does_not_escalate(tmp_path):
    """COUNT ALONE IS NOT DRIFT. 30 tool calls in 30 seconds is an agent
    orienting itself; escalating on that wakes a lead for setup. This is why the
    threshold is strikes AND elapsed, not the bead's literal 'or'."""
    clock = _Clock()
    g = _gov(tmp_path, EMPTY, clock=clock)
    got = _grind(g, clock, ESCALATE_AFTER_STRIKES * 3, 1)
    assert ESCALATE not in [v.action for v in got]
    assert FilesEvents(tmp_path / "events").pending("sattler") == []


def test_sustained_untracked_work_escalates_to_the_lead(tmp_path):
    clock = _Clock()
    g = _gov(tmp_path, EMPTY, clock=clock)
    per = ESCALATE_AFTER_S / ESCALATE_AFTER_STRIKES + 1
    got = _grind(g, clock, ESCALATE_AFTER_STRIKES + 1, per)
    esc = [v for v in got if v.action == ESCALATE]
    assert len(esc) == 1, "escalates, and exactly once per stretch"
    assert esc[0].to == "sattler"
    assert "ESCALATED to sattler" in esc[0].text

    pending = FilesEvents(tmp_path / "events").pending("sattler")
    assert len(pending) == 1
    ev = pending[0]
    assert ev.frm == "weaver"
    assert ev.reason == Reason.UNTRACKED_WORK.value
    # item=None + item_status=None is "the plate was EMPTY" — the fact being
    # escalated. status "?" would mean could-not-look and would be a lie here.
    assert ev.item is None and ev.item_status is None


def test_escalation_does_not_repeat_within_a_stretch(tmp_path):
    """One alert per stretch. The lead has it; re-persisting every 12 calls
    would bury them in the same fact."""
    clock = _Clock()
    g = _gov(tmp_path, EMPTY, clock=clock)
    per = ESCALATE_AFTER_S / ESCALATE_AFTER_STRIKES + 1
    _grind(g, clock, ESCALATE_AFTER_STRIKES * 4, per)
    assert len(FilesEvents(tmp_path / "events").pending("sattler")) == 1


def test_a_worker_with_no_lead_escalates_to_the_administrator(tmp_path):
    """Q4 routing, reused rather than re-derived — route_stop already answers
    'reports_to first, administrator otherwise'."""
    clock = _Clock()
    g = _gov(tmp_path, EMPTY, clock=clock,
             weaver=("worker", None), sattler=("administrator", None))
    per = ESCALATE_AFTER_S / ESCALATE_AFTER_STRIKES + 1
    got = _grind(g, clock, ESCALATE_AFTER_STRIKES + 1, per)
    assert [v.to for v in got if v.action == ESCALATE] == ["sattler"]


def test_nowhere_to_escalate_tells_the_agent_instead(tmp_path):
    """An escalation with nowhere to go is a real misconfiguration. The agent is
    the only party still reachable, so it is the one told — never swallowed."""
    clock = _Clock()
    g = _gov(tmp_path, EMPTY, clock=clock, weaver=("worker", None))
    per = ESCALATE_AFTER_S / ESCALATE_AFTER_STRIKES + 1
    got = _grind(g, clock, ESCALATE_AFTER_STRIKES + 1, per)
    stranded = [v for v in got if "could NOT be escalated" in v.text]
    assert stranded, "the agent must be told its tier has no escalation path"
    assert "reports_to" in stranded[0].text


# --- the poll (this hook runs before EVERY acting tool call) ------------------

def test_the_tracker_is_polled_not_read_every_call(tmp_path):
    """`bd list --json` is 0.15s and 306KB on the deployment that needs this.
    Paying that per Edit/Write/Bash forever is not acceptable, so the verdict is
    cached for POLL_S while strikes keep counting on every call."""
    calls = []

    def counting(_who):
        calls.append(1)
        return None

    clock = _Clock()
    g = _gov(tmp_path, counting, clock=clock)
    for _ in range(20):
        g.check("weaver")
        clock.advance(1)
    assert len(calls) == 1, f"polled {len(calls)}x in 20s; POLL_S is {POLL_S}"
    clock.advance(POLL_S + 1)
    g.check("weaver")
    assert len(calls) == 2


def test_strikes_count_every_call_not_every_poll(tmp_path):
    clock = _Clock()
    g = _gov(tmp_path, EMPTY, clock=clock)
    for _ in range(9):
        g.check("weaver")
        clock.advance(1)
    led = json.loads((tmp_path / "untracked" / "weaver.json").read_text())
    assert led["strikes"] == 9


# --- the plate reader honours the DEPLOYMENT's backend ------------------------

def test_plate_reader_follows_the_deployment_backend(tmp_path, monkeypatch):
    """A files-only reader on a beads-backed fleet finds an empty <root>/items
    every time and warns EVERY agent forever, while never once looking at the
    store the work is in. Wrong-by-construction is worse than absent."""
    monkeypatch.delenv("SHANTY_BACKEND", raising=False)
    (tmp_path / "env.json").write_text(json.dumps(
        {"SHANTY_BACKEND": "beads", "SHANTY_BEADS_REPO": "/some/store"}))
    read = plate_reader(tmp_path)
    assert "beads" in repr(read.__closure__[0].cell_contents.__module__)


def test_plate_reader_refuses_a_backend_it_cannot_wire(tmp_path, monkeypatch):
    """forgejo needs coordinates a hook has no way to supply. It RAISES, which
    check() reads as could-not-look and stays silent — it does not guess."""
    monkeypatch.delenv("SHANTY_BACKEND", raising=False)
    (tmp_path / "env.json").write_text(json.dumps({"SHANTY_BACKEND": "forgejo"}))
    with pytest.raises(RuntimeError):
        plate_reader(tmp_path)("weaver")


def test_an_unwireable_backend_is_silent_not_a_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_BACKEND", raising=False)
    (tmp_path / "env.json").write_text(json.dumps({"SHANTY_BACKEND": "forgejo"}))
    g = _gov(tmp_path, plate_reader(tmp_path))
    assert g.check("weaver").action == SILENT


# --- the hook entry: fail open, always ----------------------------------------

def test_main_is_silent_without_an_identity(monkeypatch, capsys):
    from shantytown import untracked
    monkeypatch.delenv("SHANTY_AGENT", raising=False)
    assert untracked.main([]) == 0
    assert capsys.readouterr().out == ""


def test_main_fails_open_on_any_backend_explosion(tmp_path, monkeypatch, capsys):
    """A governance nudge must NEVER be the reason an agent could not edit a
    file. aegis-w1nd shipped one fail-closed PreToolUse hook and it hard-blocked
    every Write by every worker on the fleet."""
    from shantytown import untracked
    monkeypatch.setenv("SHANTY_AGENT", "weaver")
    monkeypatch.setattr(untracked, "plate_reader",
                        lambda _root: (_ for _ in ()).throw(OSError("boom")))
    assert untracked.main(["--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""


def test_main_emits_additional_context_never_a_permission_decision(tmp_path,
                                                                   monkeypatch,
                                                                   capsys):
    """The hook only ever ADDS WORDS. permissionDecision:"allow" would SUPPRESS
    the user's own prompt and silently downgrade the agent's permission posture;
    "deny" would make this a block, which the directive forbids."""
    from shantytown import untracked
    _crew(tmp_path, weaver=("worker", "sattler"), sattler=("administrator", None))
    monkeypatch.setenv("SHANTY_AGENT", "weaver")
    monkeypatch.setattr(untracked, "plate_reader", lambda _root: EMPTY)
    assert untracked.main(["--root", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert "UNTRACKED WORK" in hso["additionalContext"]
    assert "permissionDecision" not in json.dumps(out)
