"""StalledAlerter — the PROGRESS-over-time detector (internal-ref).

An agent parked idle HOLDING an in_progress item, with no pane change, no item
change and no running shell for the whole threshold window, is STALLED — the
weaver case: hours at a prompt holding a bead whose blocker had resolved in a
comment it never re-read. These tests pin both directions (internal-ref: a
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
HELD = [{"id": "internal-ref", "assignee": "beads_aegis/crew/weaver"}]


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


def test_parked_idle_holding_an_item_past_threshold_STALLS(tmp_path):
    reg, panes, clock, mk = _world(tmp_path)
    assert mk().sweep(reg.all()) == []          # first sighting starts the episode
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all()) == ["weaver"]  # unchanged past 15m -> STALLED
    (pane, msg), = panes.sent
    assert pane == "p-admin"
    assert "STALLED" in msg and "weaver" in msg and "internal-ref" in msg


def test_a_live_background_shell_is_progress_not_a_stall(tmp_path):
    """The negative the bead demands: a 30-min legit task with a live shell
    (franklin's re-index) must never read STALLED."""
    reg, panes, clock, mk = _world(tmp_path, screen=IDLE_WITH_SHELL)
    assert mk().sweep(reg.all()) == []
    clock["t"] += 40 * 60
    assert mk().sweep(reg.all()) == []
    assert panes.sent == []


def test_a_changing_pane_is_progress(tmp_path):
    reg, panes, clock, mk = _world(tmp_path)
    mk().sweep(reg.all())
    clock["t"] += 16 * 60
    panes.screens["p-weaver"] = IDLE + "\n  new output line"
    assert mk().sweep(reg.all()) == []          # changed -> fresh episode
    assert panes.sent == []


def test_alerts_once_per_episode_and_rearms_on_progress(tmp_path):
    reg, panes, clock, mk = _world(tmp_path)
    mk().sweep(reg.all())
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all()) == ["weaver"]
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all()) == []          # same episode: no re-spam
    panes.screens["p-weaver"] = IDLE + "\n  woke up"    # progress...
    mk().sweep(reg.all())                                # ...new episode starts
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all()) == ["weaver"]  # ...and can stall again
    assert len(panes.sent) == 2


def test_holding_nothing_is_neglected_territory_not_stalled(tmp_path):
    reg, panes, clock, mk = _world(tmp_path, held=[])
    mk().sweep(reg.all())
    clock["t"] += 60 * 60
    assert mk().sweep(reg.all()) == []
    assert panes.sent == []


def test_bd_hiccup_fails_open(tmp_path):
    reg, panes, clock, _ = _world(tmp_path)
    def boom():
        raise RuntimeError("bd down")
    a = StalledAlerter(tmp_path, reg, panes, _Runtime(), bd_in_progress=boom,
                       threshold_min=15, now=lambda: 0, log=lambda m: None)
    assert a.sweep(reg.all()) == [] and panes.sent == []


def test_undelivered_alert_does_not_burn_the_episode(tmp_path):
    reg, panes, clock, mk = _world(tmp_path)
    panes._live.discard("p-admin")              # coordinator pane unreachable
    mk().sweep(reg.all())
    clock["t"] += 16 * 60
    assert mk().sweep(reg.all()) == []          # not delivered -> not claimed
    panes._live.add("p-admin")
    assert mk().sweep(reg.all()) == ["weaver"]  # retried and delivered
