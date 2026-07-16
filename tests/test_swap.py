"""The swap: same dispatch code, different tracker.

vision.md item 2. If dispatch changes to accommodate a tracker, the abstraction
leaked and THAT is the finding — report it, don't patch around it.
"""
from __future__ import annotations
import inspect

from shantytown import dispatch
from shantytown.beads import BeadsTracker
from shantytown.files import FilesTracker
from shantytown.protocols import Tracker


def test_both_trackers_satisfy_the_protocol():
    assert isinstance(FilesTracker.__new__(FilesTracker), Tracker)
    assert isinstance(BeadsTracker(), Tracker)


def test_dispatch_imports_no_tracker():
    """The swap is real only if dispatch cannot tell which tracker it has.

    Check IMPORTS via the AST, not prose. An earlier version grepped the source
    and fired on the word "Dolt" in a docstring — a RED for the wrong reason,
    which is the one kind of red that is worth nothing. The rule it broke is our
    own: the red must be a TRUE negative.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(dispatch.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.level:  # relative: from .protocols import ...
                imported.add(node.module)

    for bad in ("beads", "files", "tmux", "json", "subprocess", "sqlite3"):
        assert bad not in imported, f"dispatch imports {bad!r} — the abstraction is decorative"


def test_dispatch_calls_only_protocol_methods():
    """It may only call get/update/send/exists. Anything else is a tracker leak."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(dispatch.__file__).read_text())
    allowed = {"get", "update", "send", "exists", "render", "join", "items", "split"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            v = node.func.value
            if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "self":
                assert node.func.attr in allowed, (
                    f"dispatch calls self.{v.attr}.{node.func.attr}() — outside the protocol"
                )


def test_tracker_interface_is_three_functions():
    """Anything more and the tracker is driving the harness.

    Was two. Widened to three on 2026-07-16 by Stiwi's direction: `st task`
    creates work, and creation cannot go through get/update — update() needs an
    id that does not exist yet.

    This guard is NOT relaxed, it is re-pinned. It caught a real defect once
    (a third method, mine(), added unilaterally to make one command work) and it
    would catch a fourth today. The difference is who asked: a shared contract
    widened by its owner is a decision; widened at 2am to unblock yourself it is
    a bug. The number is not the point — the point is that the number can only
    move on purpose.
    """
    for impl in (FilesTracker, BeadsTracker):
        public = {m for m in dir(impl) if not m.startswith("_")}
        assert public == {"get", "update", "create"}, f"{impl.__name__} exposes {public}"
