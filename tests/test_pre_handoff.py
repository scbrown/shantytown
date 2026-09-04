"""The nudge that arrives ONE STEP BEFORE the cycle prompt (aegis-902vnu).

Stiwi, 2026-09-03: "you should be handing off before compaction same with all st
agents". Claude Code fires a PreCompact hook at the true boundary and
shantytown.precompact writes a checkpoint there. **Codex has no such event.** On
that harness the only warning is this sweep — and a warning that arrives AT the
cycle line arrives at the moment the agent is being told to stop, which is one
step too late to write anything considered.

The two assertions that matter here are not "the nudge fires". They are:

  * a nudged agent is nudged ONCE, not once per sweep — the re-arm bug this
    file's neighbour (test_cycle_rearm) exists for, which a second prompt at a
    second threshold reintroduces for free if the re-arm keeps one fixed line;
  * a `prehandoff` ledger entry does NOT suppress the later cycle prompt. A
    warning that swallows the remedy is worse than no warning.
"""
from __future__ import annotations

from tests.test_cycle import (_driver, _world, _saturated_pane, IDLE, BUSY)

from shantytown import handoff_text
from shantytown import triage as triage_mod


def _ledger(driver):
    return driver._load()


def test_the_lines_are_ordered_and_the_pre_line_is_below_the_cycle_line():
    assert triage_mod.PRE_CYCLE_THRESHOLD_K < triage_mod.CYCLE_THRESHOLD_K


def test_an_agent_between_the_lines_is_nudged_to_write_its_handoff(tmp_path):
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(340.0)})
    d = _driver(tmp_path, reg, panes)
    assert d.sweep(reg.all().exact(), rt) == [], "not a cycle prompt — a warning"
    assert _ledger(d).get("gennaro") == "prehandoff"
    sent = "\n".join(m for _, m in panes.sent)
    assert "HANDOFF SOON" in sent
    assert "br comments add" in sent
    assert "st cycle" not in sent, (
        "the pre-line asks for the WRITE, not the stop — telling it to cycle "
        "here delivers the remedy at the same moment as the problem")


def test_the_nudge_fires_ONCE_not_once_per_sweep(tmp_path):
    """The re-arm line must follow the prompt that fired. A `prehandoff` entry is
    by construction BELOW the cycle line, so retiring every entry under
    CYCLE_THRESHOLD_K would delete it on the next sweep — the aegis-rfz1b loop
    reintroduced by adding a second threshold."""
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(340.0)})
    d = _driver(tmp_path, reg, panes)
    d.sweep(reg.all().exact(), rt)
    before = len(panes.sent)
    panes._screens["shanty-gennaro"] = _saturated_pane(350.0)
    d.sweep(reg.all().exact(), rt)
    assert len(panes.sent) == before, "re-nudged an agent that was already asked"


def test_crossing_the_cycle_line_still_delivers_the_cycle_prompt(tmp_path):
    """The warning must not look like "already handled"."""
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(340.0)})
    d = _driver(tmp_path, reg, panes)
    d.sweep(reg.all().exact(), rt)
    assert _ledger(d).get("gennaro") == "prehandoff"

    panes._screens["shanty-gennaro"] = _saturated_pane(690.0)
    assert d.sweep(reg.all().exact(), rt) == ["gennaro"]
    assert _ledger(d).get("gennaro") == "saturated"
    assert "CONTEXT HIGH" in panes.sent[-1][1]


def test_dropping_below_the_pre_line_re_arms_the_nudge(tmp_path):
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(340.0)})
    d = _driver(tmp_path, reg, panes)
    d.sweep(reg.all().exact(), rt)
    panes._screens["shanty-gennaro"] = _saturated_pane(10.0)
    d.sweep(reg.all().exact(), rt)
    assert "gennaro" not in _ledger(d)


def test_an_unreadable_depth_never_nudges(tmp_path):
    """Cannot-tell is not a number. This file's neighbour documents the two
    times promoting it to a verdict cost this fleet a prompt loop."""
    for screen in (IDLE, BUSY):
        reg, panes, rt = _world({"shanty-gennaro": screen})
        d = _driver(tmp_path, reg, panes)
        d.sweep(reg.all().exact(), rt)
        assert panes.sent == [], f"nudged on an unreadable pane ({screen[:12]!r})"


def test_a_saturated_agent_gets_the_cycle_prompt_and_not_both(tmp_path):
    """The two sets are disjoint: past the cycle line is not "nearing" it."""
    reg, panes, rt = _world({"shanty-gennaro": _saturated_pane(690.0)})
    d = _driver(tmp_path, reg, panes)
    assert d.sweep(reg.all().exact(), rt) == ["gennaro"]
    assert len(panes.sent) == 1 and "CONTEXT HIGH" in panes.sent[0][1]


# --- the coordinator's own line ---------------------------------------------

def test_the_coordinator_line_names_checkpoint_bead_which_workers_do_not_need():
    """An administrator cycle is REFUSED without --checkpoint-bead
    (cycle.requires_checkpoint_bead). A root handed the worker recipe is refused
    at the moment it is least able to debug a refusal."""
    text = handoff_text.coordinator_self_handoff(612, 400)
    assert "--checkpoint-bead" in text
    assert "612k" in text and "400k" in text
    assert "/clear" in text, "the prohibition stays inline everywhere"


def test_the_coordinator_line_is_silent_below_the_threshold(tmp_path):
    """It rides the block payload the root reads EVERY turn. Firing below the
    line would train it to skip the message that also carries its wakes."""
    from shantytown import stop_policy

    class _Reg:
        def get(self, who):
            class C: pane = "shanty-sattler"
            return C()

    class _Panes:
        def __init__(self, screen): self._s = screen
        def capture(self, pane): return self._s

    quiet = stop_policy._own_context_line(
        tmp_path, "sattler", {"reg": _Reg(), "panes": _Panes(_saturated_pane(120.0))})
    assert quiet == ""

    loud = stop_policy._own_context_line(
        tmp_path, "sattler", {"reg": _Reg(), "panes": _Panes(_saturated_pane(612.0))})
    assert "YOUR OWN CONTEXT" in loud and "612k" in loud


def test_an_unreadable_pane_gives_the_coordinator_no_line(tmp_path):
    from shantytown import stop_policy

    class _Reg:
        def get(self, who): raise LookupError("gone")

    assert stop_policy._own_context_line(
        tmp_path, "sattler", {"reg": _Reg(), "panes": None}) == ""
