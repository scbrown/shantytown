"""StalledAlerter — the PROGRESS-over-time detector (aegis-e01l).

An agent parked idle HOLDING an in_progress item, with no pane change, no item
change and no running shell for the whole threshold window, is STALLED — the
weaver case: hours at a prompt holding a bead whose blocker had resolved in a
comment it never re-read. These tests pin both directions (aegis-mt0r: a
detector that cannot produce a negative is not a detector), the once-per-episode
dedup, the re-arm on progress, and fail-open.
"""
from __future__ import annotations

from shantytown.notify import StalledAlerter
from shantytown.protocols import Agent


class _Reg:
    def __init__(self, agents):
        self._a = {x.name: x for x in agents}

    def all(self):
        return list(self._a.values())

    def get(self, name):
        return self._a[name]


class _Panes:
    def __init__(self, screens, live=None):
        self.screens = screens
        self._live = set(live if live is not None else screens)
        self.sent = []

    def exists(self, pane):
        return pane in self._live

    def capture(self, pane, history=0, attrs=False):
        return self.screens.get(pane, "")

    def send(self, pane, text):
        self.sent.append((pane, text))


class _Runtime:
    def shows_ready_ui(self, screen):
        return "shift+tab" in screen

    def awaiting_answer(self, screen):
        return "Enter to select" in screen


IDLE = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
IDLE_WITH_SHELL = IDLE + "\n  main · 2 shells · 120k tokens"
HELD = [{"id": "aegis-u140", "assignee": "beads_aegis/crew/weaver"}]
HELD_DECISION = [{"id": "aegis-w3bt.3", "assignee": "beads_aegis/crew/weaver",
                  "labels": ["bucket", "decision-stiwi"]}]


def _world(tmp_path, screen=IDLE, held=HELD):
    reg = _Reg([Agent(name="sattler", role="administrator", pane="p-admin"),
                Agent(name="weaver", role="worker", pane="p-weaver")])
    panes = _Panes({"p-admin": "", "p-weaver": screen})
    clock = {"t": 1000.0}
    mk = lambda: StalledAlerter(tmp_path, reg, panes, _Runtime(),
                                bd_in_progress=lambda: held,
                                threshold_min=15, now=lambda: clock["t"],
                                log=lambda m: None)
    return reg, panes, clock, mk


def test_threshold_reached_NUDGES_THE_AGENT_not_the_coordinator(tmp_path):
    """aegis-es1tt stage 1: the neglected anchor is remediated by nudging the
    AGENT holding it to close-or-release — NOT by first bothering the coordinator."""
    reg, panes, clock, mk = _world(tmp_path)
    assert mk().sweep(reg.all()) == {"nudged": [], "escalated": []}   # episode starts
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all()) == {"nudged": ["weaver"], "escalated": []}
    (pane, msg), = panes.sent
    assert pane == "p-weaver"                    # the AGENT's pane, not p-admin
    assert "bd close aegis-u140" in msg          # the exit, both ways...
    assert "bd defer aegis-u140" in msg          # ...close if done, DEFER if blocked (kelly/vuh33)


def test_still_frozen_after_the_nudge_ESCALATES_to_the_coordinator(tmp_path):
    """Stage 2: the self-heal nudge went unanswered -> the coordinator is pushed."""
    reg, panes, clock, mk = _world(tmp_path)
    mk().sweep(reg.all())
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all())["nudged"] == ["weaver"]     # nudged the agent
    clock["t"] += 16 * 60                                    # still frozen a window later
    assert mk().sweep(reg.all()) == {"nudged": [], "escalated": ["weaver"]}
    assert panes.sent[0][0] == "p-weaver"        # first the agent
    assert panes.sent[1][0] == "p-admin"         # then the coordinator
    assert "NEGLECTED" in panes.sent[1][1]


def test_progress_after_the_nudge_PREVENTS_escalation(tmp_path):
    """The whole point: an agent that acts on the nudge is never escalated."""
    reg, panes, clock, mk = _world(tmp_path)
    mk().sweep(reg.all())
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all())["nudged"] == ["weaver"]
    panes.screens["p-weaver"] = IDLE + "\n  (agent acted on the nudge)"  # progress
    clock["t"] += 16 * 60
    r = mk().sweep(reg.all())
    assert r == {"nudged": [], "escalated": []}  # re-armed by progress, no escalation
    assert len(panes.sent) == 1                  # only the one nudge ever fired


def test_a_decision_labeled_anchor_is_NEVER_nudged(tmp_path):
    """aegis-es1tt care note: a bead waiting on an owner decision (decision-stiwi)
    is correctly parked — telling its holder to 'close' is wrong. Leave it alone."""
    reg, panes, clock, mk = _world(tmp_path, held=HELD_DECISION)
    mk().sweep(reg.all())
    clock["t"] += 60 * 60                         # an hour frozen
    assert mk().sweep(reg.all()) == {"nudged": [], "escalated": []}
    assert panes.sent == []                       # never nudged, never escalated


def test_a_live_background_shell_is_progress_not_a_stall(tmp_path):
    """The negative the bead demands: a 30-min legit task with a live shell must
    never nudge or escalate."""
    reg, panes, clock, mk = _world(tmp_path, screen=IDLE_WITH_SHELL)
    assert mk().sweep(reg.all()) == {"nudged": [], "escalated": []}
    clock["t"] += 40 * 60
    assert mk().sweep(reg.all()) == {"nudged": [], "escalated": []}
    assert panes.sent == []


def test_a_changing_pane_is_progress(tmp_path):
    reg, panes, clock, mk = _world(tmp_path)
    mk().sweep(reg.all())
    clock["t"] += 16 * 60
    panes.screens["p-weaver"] = IDLE + "\n  new output line"
    assert mk().sweep(reg.all()) == {"nudged": [], "escalated": []}  # fresh episode
    assert panes.sent == []


def test_holding_nothing_is_neglected_territory_not_stalled(tmp_path):
    reg, panes, clock, mk = _world(tmp_path, held=[])
    mk().sweep(reg.all())
    clock["t"] += 60 * 60
    assert mk().sweep(reg.all()) == {"nudged": [], "escalated": []}
    assert panes.sent == []


def test_bd_hiccup_fails_open(tmp_path):
    reg, panes, clock, _ = _world(tmp_path)
    def boom():
        raise RuntimeError("bd down")
    a = StalledAlerter(tmp_path, reg, panes, _Runtime(), bd_in_progress=boom,
                       threshold_min=15, now=lambda: 0, log=lambda m: None)
    assert a.sweep(reg.all()) == {"nudged": [], "escalated": []} and panes.sent == []


def test_undelivered_escalation_does_not_burn_the_stage(tmp_path):
    """The nudge landed but the COORDINATOR pane is unreachable at escalation
    time -> do not advance the stage; retry next pass rather than lose the alert."""
    reg, panes, clock, mk = _world(tmp_path)
    mk().sweep(reg.all())
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all())["nudged"] == ["weaver"]   # stage 1 delivered
    panes._live.discard("p-admin")                         # coordinator unreachable
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all()) == {"nudged": [], "escalated": []}  # not claimed
    panes._live.add("p-admin")
    assert mk().sweep(reg.all()) == {"nudged": [], "escalated": ["weaver"]}  # retried
