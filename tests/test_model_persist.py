"""An agent's model persists on its card — shantytown #9 / aegis-qdal.

#9: the per-agent model was not persisted, so a restart silently reverted to the
default. The reason it was lost is that it had no home — Agent/registry never
carried it. This adds the storage half: model on the card, read by get, and
PRESERVED by set (a role change must not wipe it, same as pane).

The honor-at-launch half (new/restart reads agent.model and launches with it) is
gated on #5 (st new is unbuilt) — tracked on qdal.1. These tests prove the field
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
