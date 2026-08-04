"""AN UNSIGNED PANE MESSAGE READS AS THE OPERATOR — the rest of the send paths.

aegis-5vxmz. `st inbox` was signed first (eb26be0); this covers the two classes
the bead deliberately left open — DISPATCH (`st go`) and the automated `st tend`
PUSHES — plus the one format function they now share.

EVERY TEST HERE IS HALF OF A DIFFERENTIAL. A test that only asserts the prefix
appears cannot tell "attributed correctly" from "prefixes unconditionally", and
unconditional prefixing is not a milder bug than none: it would stamp a name onto
a send whose sender we could not establish, which is authority laundering — the
exact harm the prefix exists to prevent. So each "it signs" has an "it stays
bare" beside it, and neither is trusted without the other.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import notify as notify_mod
from shantytown.attribution import ST_TEND, attribute
from shantytown.dispatch import Dispatcher
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


# --- the format function: one marker, and it never invents a name ------------

def test_attribute_signs_when_the_sender_is_known():
    assert attribute("hello", "sattler") == "[from sattler] hello"


def test_attribute_stays_BARE_when_the_sender_is_not():
    """The control. None/"" mean "could not establish", and cannot-tell must never
    be resolved into a plausible-looking name."""
    assert attribute("hello", None) == "hello"
    assert attribute("hello", "") == "hello"


def test_attribute_does_not_double_sign():
    """Attribution happens at whichever layer owns the FORMAT, and those layers
    were not written with each other in view. A message crossing two of them must
    not come out '[from a] [from a] …' — a marker that renders differently on
    different paths is a marker readers stop reading."""
    once = attribute("hello", "sattler")
    assert attribute(once, "arnold") == once


# --- st go: the payload that tells an agent to START WORKING -----------------

@pytest.fixture
def world(tmp_path: Path):
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))
    trk = FilesTracker(tmp_path / "items")
    trk.update("item-1", title="Restore the den", status="open")
    return FilesRegistry(crew), trk


def test_a_dispatch_names_the_coordinator_who_sent_it(world):
    reg, trk = world
    panes = NullPanes(screen="")
    Dispatcher(reg, trk, panes, sender="arnold").go("item-1", "ellie")
    assert panes.sent == [
        ("%5", "[from arnold] Work is on your hook: item-1 — Restore the den")]


def test_an_unattributable_dispatch_stays_BARE(world):
    """The control — and the reason Dispatcher takes an INJECTED sender rather
    than reading $SHANTY_AGENT itself: a Dispatcher built without one must not
    silently inherit whatever name the surrounding process happens to carry."""
    reg, trk = world
    panes = NullPanes(screen="")
    Dispatcher(reg, trk, panes).go("item-1", "ellie")
    assert panes.sent == [("%5", "Work is on your hook: item-1 — Restore the den")]


def test_the_prefix_is_on_the_payload_plan_hands_to_triage_and_the_send(world):
    """WHY plan() SIGNS AND NOT go(). plan()'s text is what triage judges and what
    the send delivers; signing at the send would mean the payload reviewed is not
    the payload sent."""
    reg, trk = world
    d = Dispatcher(reg, trk, NullPanes(screen=""), sender="arnold")
    assert d.plan("item-1", "ellie").text.startswith("[from arnold] ")
    assert trk.get("item-1").status == "open", "plan() must not write"


def test_dry_run_TELLS_THE_OPERATOR_who_the_dispatch_will_be_signed_as(world):
    """render() deliberately shows the note and the store but NEVER the payload
    (its own comment says so), so putting the prefix in `text` alone reaches the
    agent and not the person authorising the send. Same workaround the store tag
    already uses: its own line."""
    reg, trk = world
    d = Dispatcher(reg, trk, NullPanes(screen=""), sender="arnold")
    assert "would: sign as -> arnold" in d.plan("item-1", "ellie").render()


def test_dry_run_says_UNSIGNED_OUT_LOUD_rather_than_going_quiet(world):
    """The control, and the case that actually matters. An unsigned dispatch does
    not arrive anonymous — it arrives looking like the OPERATOR handing out work.
    A preview that simply omitted the line when there was no sender would read as
    'nothing to report' about the one thing worth reporting."""
    reg, trk = world
    render = Dispatcher(reg, trk, NullPanes(screen="")).plan("item-1", "ellie").render()
    assert "UNSIGNED" in render, render
    assert "sign as -> arnold" not in render


def test_the_prefix_does_not_break_the_send_verify(world):
    """verify() greps the pane back for the ITEM ID. The bead's stated risk was
    that touching the dispatch payload breaks the contract the fleet rides on —
    this is that contract, exercised end to end with the prefix on. go() raises
    SendUnverified if the read-back fails, so returning at all IS the assertion."""
    reg, trk = world
    Dispatcher(reg, trk, NullPanes(screen=""), sender="arnold").go("item-1", "ellie")
    assert trk.get("item-1").status == "in_progress"


# --- st tend: pushes nobody composed by hand ---------------------------------
# These are the ones most easily mistaken for the operator. They arrive in the
# imperative — "CYCLE NOW", "close-or-release", "<worker> is BLOCKED and needs
# you" — which is exactly the register an operator instruction arrives in, and
# unlike an agent message there is no human anywhere behind them.

@pytest.fixture
def crew(tmp_path: Path):
    d = tmp_path / "crew"; d.mkdir()
    (d / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))
    (d / "arnold.json").write_text(
        json.dumps({"role": "administrator", "pane": "%1"}))
    return FilesRegistry(d)


def test_the_cycle_and_haul_push_names_st_tend(crew):
    panes = NullPanes(screen="")
    assert notify_mod.push_to_own_pane(crew, panes, "ellie", "CYCLE NOW") == "ellie"
    assert panes.sent == [("%5", f"[from {ST_TEND}] CYCLE NOW")]


def test_the_admin_push_names_st_tend(crew):
    panes = NullPanes(screen="")
    assert notify_mod.push_to_admin(crew, panes, "the fleet is idle") == "arnold"
    assert panes.sent == [("%1", f"[from {ST_TEND}] the fleet is idle")]


def test_the_blocked_worker_push_names_st_tend(crew):
    panes = NullPanes(screen="")
    assert notify_mod.wake_recipient(crew, panes, "ellie", "ellie is BLOCKED") == "arnold"
    assert panes.sent == [("%1", f"[from {ST_TEND}] ellie is BLOCKED")]


def test_an_UNDELIVERABLE_tend_push_sends_NOTHING_AT_ALL(crew):
    """The control for this trio, and it is the right one to pick. The failure
    these helpers already guard against is a silent success — reporting a
    delivery into a pane that is not there. Signing must not have introduced a
    send on a path that previously made none, so the assertion is on `sent` being
    EMPTY, not merely on the None return."""
    panes = NullPanes(screen="", live=set())        # no pane exists
    assert notify_mod.push_to_own_pane(crew, panes, "ellie", "CYCLE NOW") is None
    assert notify_mod.push_to_admin(crew, panes, "idle") is None
    assert notify_mod.wake_recipient(crew, panes, "ellie", "BLOCKED") is None
    assert panes.sent == []
