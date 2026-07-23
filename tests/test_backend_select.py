"""--backend routes prime/dispatch to the right tracker #3.

arnold landed beads.plate() (the reader) but the CLI wired FilesTracker
unconditionally, so `st --backend beads` did not exist and the beads plate was
unreachable. These tests prove the SELECTOR: --backend beads builds a
BeadsTracker and routes the plate to beads.plate; --backend files stays files.
They assert the ROUTING, not the plate's content (that is beads.plate's job).
"""
from __future__ import annotations
from types import SimpleNamespace

from shantytown import cli
from shantytown.beads import BeadsTracker
from shantytown.files import FilesTracker


def test_backend_files_is_the_default():
    a = SimpleNamespace(root=cli.Path("/tmp/x"), backend="files", repo=None)
    assert isinstance(cli._tracker(a), FilesTracker)


def test_backend_beads_builds_a_beads_tracker():
    a = SimpleNamespace(root=cli.Path("/tmp/x"), backend="beads", repo="/some/repo")
    t = cli._tracker(a)
    assert isinstance(t, BeadsTracker)
    assert t.repo == "/some/repo"


def test_plate_routes_to_the_selected_backend():
    """The reader must match the tracker. A files tracker with a beads plate (or
    vice versa) is the exact mismatch that made prime report empty on beads."""
    import shantytown.beads as beads_mod
    import shantytown.files as files_mod

    beads_calls, files_calls = [], []

    def fake_beads_plate(trk, who):
        beads_calls.append(who); return None

    def fake_files_plate(trk, who):
        files_calls.append(who); return None

    orig_b, orig_f = beads_mod.plate, files_mod.plate
    beads_mod.plate, files_mod.plate = fake_beads_plate, fake_files_plate
    # cli imported files.plate by name at module load; patch that binding too.
    orig_cli_fp = cli.files_plate
    cli.files_plate = fake_files_plate
    try:
        cli._plate(SimpleNamespace(root=cli.Path("/tmp/x"), backend="beads",
                                   repo="/r"))("malcolm")
        cli._plate(SimpleNamespace(root=cli.Path("/tmp/x"), backend="files",
                                   repo=None))("ellie")
    finally:
        beads_mod.plate, files_mod.plate = orig_b, orig_f
        cli.files_plate = orig_cli_fp

    assert beads_calls == ["malcolm"], "beads backend did not route to beads.plate"
    assert files_calls == ["ellie"], "files backend did not route to files.plate"


# ---------------------------------------------------------------------------
# Deployment default (internal-ref): SHANTY_BACKEND / SHANTY_BEADS_REPO.
#
# The shanty status-bar segment and the session picker call PLAIN
# `st anchor <agent> --short` by design — a public repo must not embed a
# tracker path. On a fleet whose plates live in beads that meant BOTH surfaces
# rendered empty, consistently, with exit 0. These tests prove the deployment
# can declare its tracker ONCE (<root>/env.json, then env) and that the
# declaration sits BETWEEN the explicit flag and the per-command default.
# ---------------------------------------------------------------------------
import json as _json

import pytest


def _scrub(monkeypatch):
    monkeypatch.delenv("SHANTY_BACKEND", raising=False)
    monkeypatch.delenv("SHANTY_BEADS_REPO", raising=False)


def test_env_json_backend_selects_beads_without_a_flag(tmp_path, monkeypatch):
    _scrub(monkeypatch)
    (tmp_path / "env.json").write_text(_json.dumps(
        {"SHANTY_BACKEND": "beads", "SHANTY_BEADS_REPO": "/the/store"}))
    a = SimpleNamespace(root=tmp_path, backend=None, repo=None)
    t = cli._tracker(a)
    assert isinstance(t, BeadsTracker)
    assert t.repo == "/the/store"


def test_ambient_env_backend_when_no_env_json(tmp_path, monkeypatch):
    _scrub(monkeypatch)
    monkeypatch.setenv("SHANTY_BACKEND", "beads")
    monkeypatch.setenv("SHANTY_BEADS_REPO", "/env/store")
    a = SimpleNamespace(root=tmp_path, backend=None, repo=None)
    t = cli._tracker(a)
    assert isinstance(t, BeadsTracker)
    assert t.repo == "/env/store"


def test_explicit_flag_beats_the_deployment_default(tmp_path, monkeypatch):
    """--backend files must still force local on a beads-declared fleet."""
    _scrub(monkeypatch)
    (tmp_path / "env.json").write_text(_json.dumps({"SHANTY_BACKEND": "beads"}))
    a = SimpleNamespace(root=tmp_path, backend="files", repo=None)
    assert isinstance(cli._tracker(a), FilesTracker)


def test_explicit_repo_beats_the_deployment_repo(tmp_path, monkeypatch):
    _scrub(monkeypatch)
    (tmp_path / "env.json").write_text(_json.dumps(
        {"SHANTY_BACKEND": "beads", "SHANTY_BEADS_REPO": "/the/store"}))
    a = SimpleNamespace(root=tmp_path, backend=None, repo="/flag/store")
    assert cli._tracker(a).repo == "/flag/store"


def test_nothing_declared_keeps_the_per_command_default(tmp_path, monkeypatch):
    _scrub(monkeypatch)
    a = SimpleNamespace(root=tmp_path, backend=None, repo=None)
    assert isinstance(cli._tracker(a), FilesTracker)          # default files
    assert cli._backend(a, default="beads") == "beads"        # inbox -d's default


def test_a_typo_refuses_instead_of_silently_meaning_files(tmp_path, monkeypatch):
    """A misspelled SHANTY_BACKEND falling through to files IS the blank-plate
    bug this knob exists to fix — it must refuse, loudly."""
    _scrub(monkeypatch)
    (tmp_path / "env.json").write_text(_json.dumps({"SHANTY_BACKEND": "bead"}))
    a = SimpleNamespace(root=tmp_path, backend=None, repo=None)
    with pytest.raises(SystemExit):
        cli._backend(a)


def test_env_json_wins_over_ambient_env(tmp_path, monkeypatch):
    """Same source order as the launch side (runtime.py): the store's config
    beats the shell's — the answer must not change with which pane asked."""
    _scrub(monkeypatch)
    monkeypatch.setenv("SHANTY_BACKEND", "files")
    (tmp_path / "env.json").write_text(_json.dumps({"SHANTY_BACKEND": "beads"}))
    a = SimpleNamespace(root=tmp_path, backend=None, repo="/r")
    assert isinstance(cli._tracker(a), BeadsTracker)
