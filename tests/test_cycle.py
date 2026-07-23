"""st tend DRIVES the cycle — a saturated agent is prompted to checkpoint+/clear
on its OWN pane, automatically (internal-ref).

h562 detected + refused a saturated agent but had no delivery path for the remedy,
so a coordinator raw-tmux'd checkpoint+/clear to three agents by hand. These tests
pin the delivery: the prompt lands on the agent itself, it CHECKPOINTS BEFORE
CLEARING (never a bare /clear), and it fires once per saturation episode.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import triage
from shantytown.notify import CycleDriver, saturated_agents, _cycle_message
from shantytown.protocols import Agent
from shantytown.runtime import LiveWiring


# Wired by default: these tests are about the CYCLE mechanics, so their agents
# carry send wiring unless a test is specifically about darkness. The dark gate
# has its own section below.
_WIRED = lambda agent: LiveWiring(directions={"send"}, settings_path="/w.json")


def _driver(root, reg, panes, **kw):
    kw.setdefault("wiring", _WIRED)
    return CycleDriver(root, reg, panes, **kw)


def _saturated_pane(tokens: float) -> str:
    return ("❯ \n"
            f"                  new task? /clear to save {tokens}k tokens\n"
            "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents")


IDLE = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
BUSY = "✻ Envisioning… (12s · 4.1k tokens · esc to interrupt)"


class _Runtime:
    def shows_ready_ui(self, screen):
        return "shift+tab to cycle" in screen

    def awaiting_answer(self, screen):
        return "Enter to select" in screen


class _Panes:
    def __init__(self, screens):
        self._screens = screens
        self.sent = []

    def exists(self, pane):
        return pane in self._screens

    def capture(self, pane, history=0, attrs=False):
        return self._screens.get(pane, "")

    def send(self, pane, text):
        self.sent.append((pane, text))


class _Reg:
    def __init__(self, cards):
        self._c = {a.name: a for a in cards}

    def get(self, name):
        return self._c[name]

    def all(self):
        return list(self._c.values())


def _world(panes_map):
    reg = _Reg([Agent(name=n.replace("shanty-", "").replace("aegis-crew-", ""),
                      role="worker", pane=n) for n in panes_map])
    return reg, _Panes(panes_map), _Runtime()


# --- detection: SATURATED only (idle + over threshold) ----------------------

def test_saturated_idle_agent_is_detected():
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(687.0)})
    assert saturated_agents(reg.all(), panes, rt) == ["gennaro"]


def test_a_busy_agent_past_the_threshold_is_NOT_cycled():
    # Its footer is unreadable mid-turn; it reads busy, and we never interrupt a
    # working agent with a cycle prompt.
    reg, panes, rt = _world({"shanty-tim": BUSY})
    assert saturated_agents(reg.all(), panes, rt) == []


def test_an_under_threshold_idle_agent_is_not_cycled():
    reg, panes, rt = _world({"shanty-ellie": _saturated_pane(120.0)})
    assert saturated_agents(reg.all(), panes, rt) == []


# --- the delivery: checkpoint BEFORE clear, to the agent's OWN pane ----------

def test_the_prompt_lands_on_the_agents_own_pane(tmp_path):
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(687.0)})
    prompted = _driver(tmp_path, reg, panes).sweep(reg.all(), rt)

    assert prompted == ["gennaro"]
    assert len(panes.sent) == 1
    pane, text = panes.sent[0]
    assert pane == "shanty-gennaro", "the cycle prompt must go to the agent itself"


def test_the_prompt_checkpoints_BEFORE_clearing():
    msg = _cycle_message()
    # It is an INSTRUCTION, not a bare keystroke: there is no standalone `/clear`
    # at the start, and CHECKPOINT is named before /clear appears.
    assert not msg.strip().startswith("/clear")
    assert "CHECKPOINT" in msg
    assert msg.index("CHECKPOINT") < msg.index("/clear"), "checkpoint must precede clear"
    assert "BEFORE /clear" in msg


# --- dedup: once per episode, re-armed on recovery --------------------------

def test_a_still_saturated_agent_is_not_re_prompted(tmp_path):
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(687.0)})
    d = _driver(tmp_path, reg, panes)
    assert d.sweep(reg.all(), rt) == ["gennaro"]
    assert d.sweep(reg.all(), rt) == []          # still saturated -> silent
    assert d.sweep(reg.all(), rt) == []
    assert len(panes.sent) == 1, "a heartbeat re-spammed a still-saturated agent"


def test_dedup_survives_the_process_restarting(tmp_path):
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(687.0)})
    assert _driver(tmp_path, reg, panes).sweep(reg.all(), rt) == ["gennaro"]
    # A fresh driver (the sweeper restarted) reads the durable ledger and stays quiet.
    assert _driver(tmp_path, reg, panes).sweep(reg.all(), rt) == []


def test_recovery_re_arms_the_prompt(tmp_path):
    reg = _Reg([Agent(name="gennaro", role="worker", pane="shanty-gennaro")])
    sat = _Panes({"shanty-gennaro": _saturated_pane(687.0)})
    assert _driver(tmp_path, reg, sat).sweep(reg.all(), _Runtime()) == ["gennaro"]

    # It cycled and dropped below the threshold -> ledger forgets it.
    recovered = _Panes({"shanty-gennaro": _saturated_pane(30.0)})
    assert _driver(tmp_path, reg, recovered).sweep(reg.all(), _Runtime()) == []

    # It saturates AGAIN -> a fresh episode, prompt again.
    assert _driver(tmp_path, reg, sat).sweep(reg.all(), _Runtime()) == ["gennaro"]


def test_an_unreachable_pane_is_not_swallowed(tmp_path):
    # card names a pane that is not live -> push fails -> stays pending, retried.
    reg = _Reg([Agent(name="gennaro", role="worker", pane="shanty-gennaro")])
    panes = _Panes({})            # no live pane
    logs = []
    d = _driver(tmp_path, reg, panes, log=logs.append)
    assert d.sweep(reg.all(), _Runtime()) == []
    # once it comes back saturated-and-live, the retry fires.
    live = _Panes({"shanty-gennaro": _saturated_pane(687.0)})
    d2 = _driver(tmp_path, reg, live, log=logs.append)
    assert d2.sweep(reg.all(), _Runtime()) == ["gennaro"]


# --- the dark gate (internal-ref follow-up): not st's agents, not st's to drive

def test_a_DARK_saturated_agent_is_never_prompted(tmp_path):
    """MEASURED: the live tend loop typed cycle prompts into foreign
    gastown-launched panes — sessions st did not launch, no stop wiring on the
    process, one of them auth-dead so the prompts piled onto a login banner.
    Dark is feed_check's definition, applied to the cycle driver."""
    reg, panes, rt = _world({"aegis-crew-ellie": _saturated_pane(576.0)})
    logs = []
    d = _driver(tmp_path, reg, panes, wiring=lambda a: None, log=logs.append)
    assert d.sweep(reg.all(), rt) == []
    assert panes.sent == [], "typed into a foreign agent's pane"
    assert any("DARK" in m for m in logs)


def test_empty_wiring_is_dark_too(tmp_path):
    # A live process whose launch line carries --settings with no stop_event
    # hooks: measurable, and measurably not ours.
    reg, panes, rt = _world({"aegis-crew-ellie": _saturated_pane(576.0)})
    d = _driver(tmp_path, reg, panes,
                wiring=lambda a: LiveWiring(directions=set(), settings_path="/g.json"))
    assert d.sweep(reg.all(), rt) == []
    assert panes.sent == []


def test_the_dark_skip_is_said_once_not_every_sweep(tmp_path):
    reg, panes, rt = _world({"aegis-crew-ellie": _saturated_pane(576.0)})
    logs = []
    d = _driver(tmp_path, reg, panes, wiring=lambda a: None, log=logs.append)
    d.sweep(reg.all(), rt)
    d.sweep(reg.all(), rt)
    d.sweep(reg.all(), rt)
    assert sum("DARK" in m for m in logs) == 1, "a 30s heartbeat would spam this"


def test_a_dark_agent_that_gets_wired_while_still_saturated_is_prompted(tmp_path):
    """The verdict is re-checked every sweep, not frozen in the ledger: a
    relaunch that wires the agent must not stay stuck behind an old 'dark'."""
    reg, panes, rt = _world({"aegis-crew-ellie": _saturated_pane(576.0)})
    d = _driver(tmp_path, reg, panes, wiring=lambda a: None)
    assert d.sweep(reg.all(), rt) == []
    d2 = _driver(tmp_path, reg, panes)              # now wired (default stub)
    assert d2.sweep(reg.all(), rt) == ["ellie"]


def test_positive_control_the_wired_twin_is_prompted(tmp_path):
    """Same world, wiring present: the prompt fires. Without this, the dark gate
    could be a constant skip and every test above would still pass."""
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(687.0)})
    assert _driver(tmp_path, reg, panes).sweep(reg.all(), rt) == ["gennaro"]
    assert len(panes.sent) == 1
