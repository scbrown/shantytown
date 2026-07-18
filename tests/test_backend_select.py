"""--backend routes prime/dispatch to the right tracker — aegis-kbuz #3.

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
