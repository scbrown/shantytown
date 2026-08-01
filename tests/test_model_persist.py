"""An agent's model persists on its card — shantytown #9.

#9: the per-agent model was not persisted, so a restart silently reverted to the
default. The reason it was lost is that it had no home — Agent/registry never
carried it. This adds the storage half: model on the card, read by get, and
PRESERVED by set (a role change must not wipe it, same as pane).

The honor-at-launch half (new/restart reads agent.model and launches with it) is
gated on #5 (st new is unbuilt), and tracked separately. These tests prove the field
survives, which is the "persisted" in "not persisted, lost on restart".
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown.files import FilesRegistry
from shantytown.protocols import Agent


def _reg(tmp_path: Path) -> FilesRegistry:
    crew = tmp_path / "crew"; crew.mkdir()
    return FilesRegistry(crew)


def test_model_is_read_from_the_card(tmp_path: Path):
    r = _reg(tmp_path)
    (r.root / "ellie.json").write_text(json.dumps(
        {"role": "worker", "pane": "%5", "model": "opus-4.8"}))
    assert r.get("ellie").model == "opus-4.8"


def test_absent_model_is_None_not_a_crash(tmp_path: Path):
    """A card with no model is valid — None means 'use the launcher default'."""
    r = _reg(tmp_path)
    (r.root / "ellie.json").write_text(json.dumps({"role": "worker"}))
    assert r.get("ellie").model is None


def test_model_survives_a_set_get_round_trip(tmp_path: Path):
    """This IS 'persisted', and it is the DETECTOR of set()'s model write: a
    FRESH set (no prior card) that carries a model must write it. Positive-
    controlled — removing set()'s `if agent.model` line makes THIS test fail
    (the role-change test below does not, because set() merges into the existing
    card, so an already-persisted model survives regardless of that line)."""
    r = _reg(tmp_path)
    r.set(Agent(name="ellie", role="worker", pane="%5", model="haiku-4.5"))
    assert r.get("ellie").model == "haiku-4.5"


def test_a_role_change_PRESERVES_the_model(tmp_path: Path):
    """role set rewrites the card for a role change; it must NOT wipe the model,
    exactly as it preserves pane — else every promotion silently reverts the
    agent to the default model. NOTE this passes via set()'s merge-into-existing
    (the model is already on disk), not via the explicit write line; the
    round-trip test above is the detector of that line. Both properties are real
    and worth pinning."""
    r = _reg(tmp_path)
    # agent starts with a model, set by whoever assigned it
    (r.root / "ellie.json").write_text(json.dumps(
        {"role": "worker", "pane": "%5", "model": "opus-4.8"}))
    # the tier promotes ellie to lead — carries no model (it doesn't own one)
    r.set(Agent(name="ellie", role="lead", reports_to="arnold"))
    after = r.get("ellie")
    assert after.role == "lead"
    assert after.model == "opus-4.8", "role change wiped the persisted model"


def test_absent_pane_and_model_both_preserved_across_a_role_set(tmp_path: Path):
    """Symmetry with pane: neither field the tier doesn't own gets clobbered."""
    r = _reg(tmp_path)
    (r.root / "ellie.json").write_text(json.dumps(
        {"role": "worker", "pane": "%5", "model": "opus-4.8"}))
    r.set(Agent(name="ellie", role="lead"))
    after = r.get("ellie")
    assert after.pane == "%5"
    assert after.model == "opus-4.8"


# --- retirement joins the only-when-carried family (aegis-6hfmi) --------------
#
# `retired` was the ONE field written unconditionally while every one of its
# neighbours above was written only-when-carried. Combined with a False default
# that meant a caller which had never heard of retirement silently CLEARED one —
# it did not have to intend it, only to not know. Measured in the live fleet:
# ian.json was rewritten at 18:53 and came back un-retired, and `retired` is the
# fleet's only durable, deliberate spin-down.
#
# The original comment ("written even when False: un-retiring must be
# expressible, and a field that can only ever be set is a one-way door") was
# RIGHT about the requirement and wrong about the mechanism: it made "not
# expressed" and "expressed False" the same value, so the write could not tell
# them apart. None restores the distinction without closing the door.


def test_a_caller_that_never_mentions_retirement_cannot_clear_one(tmp_path: Path):
    """THE regression. A projection carries no retirement — it must not un-retire."""
    r = _reg(tmp_path)
    (r.root / "ian.json").write_text(json.dumps(
        {"role": "worker", "pane": "aegis-crew-ian", "retired": True}))
    r.set(Agent(name="ian", role="worker", reports_to="dearing"))
    assert r.get("ian").retired is True, \
        "a caller that does not model retirement silently un-retired the agent"


def test_un_retiring_is_STILL_expressible_so_this_is_not_a_one_way_door(tmp_path: Path):
    """The requirement the old unconditional write existed to protect. Keep it."""
    r = _reg(tmp_path)
    (r.root / "ian.json").write_text(json.dumps({"role": "worker", "retired": True}))
    r.set(Agent(name="ian", role="worker", retired=False))
    assert r.get("ian").retired is False, "explicit un-retirement stopped working"


def test_not_expressed_and_expressed_False_are_DIFFERENT_writes(tmp_path: Path):
    """The distinction the fix restores — same output for two worlds was the bug."""
    r = _reg(tmp_path)
    (r.root / "a.json").write_text(json.dumps({"role": "worker", "retired": True}))
    (r.root / "b.json").write_text(json.dumps({"role": "worker", "retired": True}))
    r.set(Agent(name="a", role="worker"))                  # not saying
    r.set(Agent(name="b", role="worker", retired=False))   # saying not retired
    assert r.get("a").retired is True
    assert r.get("b").retired is False


def test_a_card_with_no_retired_key_stays_keyless_through_a_round_trip(tmp_path: Path):
    """Reading must not invent a decision the card never recorded."""
    r = _reg(tmp_path)
    (r.root / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))
    assert r.get("ellie").retired is None, "absence read as a decision"
    r.set(r.get("ellie"))
    assert "retired" not in json.loads((r.root / "ellie.json").read_text())


def test_retirement_survives_the_read_write_round_trip(tmp_path: Path):
    """get() then set() is how half the CLI edits a card. It must be lossless."""
    r = _reg(tmp_path)
    (r.root / "ian.json").write_text(json.dumps({"role": "worker", "retired": True}))
    r.set(r.get("ian"))
    assert r.get("ian").retired is True
