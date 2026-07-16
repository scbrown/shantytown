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


def test_dispatch_never_names_a_tracker():
    """The swap is real only if dispatch cannot tell which tracker it has."""
    src = inspect.getsource(dispatch).lower()
    for name in ("beads", "files", "bd ", "dolt", "json"):
        assert name not in src, f"dispatch.py mentions {name!r} — it knows its tracker"


def test_tracker_interface_is_two_functions():
    """Anything more and the tracker is driving the harness."""
    for impl in (FilesTracker, BeadsTracker):
        public = {m for m in dir(impl) if not m.startswith("_")}
        assert public == {"get", "update"}, f"{impl.__name__} exposes {public}"
