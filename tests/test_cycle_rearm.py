"""The cycle episode must be retired by a MEASURED depth, never by IDLE (aegis-rfz1b).

WHAT WENT WRONG, measured 2026-08-03: the coordinator received the checkpoint-then-
clear instruction FOUR times in one session, having checkpointed after the first. The
ledger was correct and armed the whole time; the RE-ARM was deleting it.

`CycleDriver.sweep` retired an episode on `state == IDLE`. But `work_state` returns
IDLE in two very different situations: the depth was read and found low, AND the depth
could not be read at all — `context_tokens_k` returns None whenever the "/clear to save
Nk" footer is missing or replaced by the spinner. So a pane caught mid-transition read
IDLE, the entry was deleted, the next sweep saw SATURATED again, and the agent was
re-prompted. Once per turn boundary, indefinitely.

That is the same defect `sweep`'s own comment says it already fixed: it stopped trusting
"absent from the saturated set" precisely BECAUSE absence could mean unreadable, then
trusted IDLE — which is the bucket unreadable falls into. Cannot-tell promoted to a
verdict twice, in two different fields.

The distinction these tests pin is the one the bug could not make: `IDLE` (no footer at
all) and `_saturated_pane(120.0)` (footer present, reads 120k) BOTH give state IDLE.
Only the second is evidence of anything.
"""
from __future__ import annotations

from tests.test_cycle import (_driver, _world, _saturated_pane, IDLE, BUSY)


def _ledger(driver):
    return driver._load()


# --- the regression: an unreadable depth must NOT retire the episode ---------

def test_an_unreadable_depth_does_not_re_arm_the_prompt(tmp_path):
    """The exact loop. Prompt once, then show a pane whose depth cannot be read,
    then show it saturated again — it must NOT be prompted a second time."""
    panes_map = {"shanty-sattler": _saturated_pane(687.0)}
    reg, panes, rt = _world(panes_map)
    d = _driver(tmp_path, reg, panes)

    assert d.sweep(reg.all().exact(), rt) == ["sattler"], "first prompt should fire"
    assert _ledger(d).get("sattler") == "saturated"

    # A pane with the ready UI and NO "/clear to save" footer: work_state says
    # IDLE, context_tokens_k says None. This is the mid-transition read.
    panes._screens["shanty-sattler"] = IDLE
    assert d.sweep(reg.all().exact(), rt) == []
    assert _ledger(d).get("sattler") == "saturated", (
        "an UNREADABLE depth is cannot-tell and must leave the episode armed")

    # Back to saturated. Under the bug this re-prompted; it must not.
    panes._screens["shanty-sattler"] = _saturated_pane(690.0)
    assert d.sweep(reg.all().exact(), rt) == [], (
        "re-prompted an agent that already checkpointed — the aegis-rfz1b loop")


def test_a_busy_pane_also_does_not_re_arm(tmp_path):
    """Same argument, the other unreadable shape: mid-turn the footer is replaced
    by the spinner, so the depth is equally unknown."""
    reg, panes, rt = _world({"shanty-sattler": _saturated_pane(687.0)})
    d = _driver(tmp_path, reg, panes)
    d.sweep(reg.all().exact(), rt)

    panes._screens["shanty-sattler"] = BUSY
    d.sweep(reg.all().exact(), rt)
    assert _ledger(d).get("sattler") == "saturated"

    panes._screens["shanty-sattler"] = _saturated_pane(700.0)
    assert d.sweep(reg.all().exact(), rt) == []


# --- the other direction: a REAL recovery must still re-arm ------------------

def test_a_measured_low_depth_DOES_re_arm(tmp_path):
    """The fix must not weld the ledger shut. A pane whose footer is present and
    reads UNDER the threshold is a genuine cycle — the next saturation is a new
    episode and must prompt again."""
    reg, panes, rt = _world({"shanty-sattler": _saturated_pane(687.0)})
    d = _driver(tmp_path, reg, panes)
    assert d.sweep(reg.all().exact(), rt) == ["sattler"]

    # Footer PRESENT, reads 120k — measured, and under the 400k threshold. This
    # is what an actual /clear looks like, and it is state IDLE just like the
    # unreadable pane above. The depth is the only thing telling them apart.
    panes._screens["shanty-sattler"] = _saturated_pane(120.0)
    assert d.sweep(reg.all().exact(), rt) == []
    assert "sattler" not in _ledger(d), (
        "a measured under-threshold depth is a real recovery and must re-arm")

    panes._screens["shanty-sattler"] = _saturated_pane(510.0)
    assert d.sweep(reg.all().exact(), rt) == ["sattler"], (
        "a LATER saturation is a new episode and must prompt again")


def test_an_agent_whose_pane_vanishes_stays_armed(tmp_path):
    """No pane at all is absent from the map — also cannot-tell, also not
    recovery. A restart must not silently retire an unfinished episode."""
    reg, panes, rt = _world({"shanty-sattler": _saturated_pane(687.0)})
    d = _driver(tmp_path, reg, panes)
    d.sweep(reg.all().exact(), rt)

    del panes._screens["shanty-sattler"]
    d.sweep(reg.all().exact(), rt)
    assert _ledger(d).get("sattler") == "saturated"
