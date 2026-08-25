"""Regression coverage for the re-dispatch leg of ``st cycle`` (aegis-wjnf3)."""
from __future__ import annotations

import json
from argparse import Namespace

import shantytown.cli as cli
from shantytown.dispatch import Dispatcher
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


def test_cycle_redispatch_writes_the_item_assignment(tmp_path, monkeypatch):
    """A successful return is insufficient: the item must actually be reassigned.

    The live failure called a nonexistent ``_dispatcher`` factory, swallowed the
    NameError into a note, and returned success after clearing the agent. Pin the
    tracker state so that success-shaped non-delivery cannot regress unnoticed.
    """
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "malcolm.json").write_text(json.dumps({
        "role": "worker", "pane": "shanty-malcolm",
    }))
    (root / "items").mkdir()
    item_path = root / "items" / "aegis-fbrr0.json"
    item_path.write_text(json.dumps({
        "title": "standing list", "status": "open", "assignee": "malcolm",
    }))

    tracker = FilesTracker(root / "items")
    panes = NullPanes(screen="")
    dispatcher = Dispatcher(FilesRegistry(root / "crew"), tracker, panes)
    args = Namespace(root=root, backend="files", repo=None)

    # The production beads backend supplies its own plate reader. Inject the
    # equivalent files-backend read while retaining the real tracker + dispatch.
    monkeypatch.setattr(cli.beads_mod, "plate",
                        lambda _tracker, _agent: tracker.get("aegis-fbrr0"))
    monkeypatch.setattr(cli, "_wire", lambda _args: dispatcher)

    cli._redispatch_after_cycle(args, "malcolm", "aegis-checkpoint")

    written = json.loads(item_path.read_text())
    assert written["status"] == "in_progress"
    assert written["assignee"] == "malcolm"
    assert len(panes.sent) == 1
    assert "aegis-fbrr0" in panes.sent[0][1]
