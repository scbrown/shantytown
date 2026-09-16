"""A bare `--until DATE` must verify as a DAY, or every plain defer cries wolf.

MEASURED 2026-09-16 on the live store, deferring the aegis-izh4 P0:

    st defer aegis-izh4 human --reason-file … --until 2026-09-23
    ⚠ COULD NOT CONFIRM: tracker reported success and the read-back disagrees
      after 1 attempt(s) — defer_until: wanted '2026-09-23',
      store still says '2026-09-23T13:00:00Z'

The write had LANDED and was correct. `--until 2026-09-23` parses to 00:00Z,
br normalises a date-only value to 13:00Z (the same shape recorded in the
aegis-vyc3aa field note, so it is the backend's convention and not a one-off),
and the exact-instant comparison read that as a lost write.

Why this is worth a test rather than a one-line fix: the false direction is the
expensive one. It raises TrackerWriteLost, so EVERY defer naming a plain date
returned exit 2 (could-not-confirm) and told the caller to go re-read the bead.
A confirmation that is wrong on the ordinary path is one people learn to skim —
which destroys the read-back this code exists to provide. Same family as the
detector whose findings nobody reads.
"""
import json
import subprocess

import pytest

from shantytown.br import BrTracker
from shantytown.dispatch import Dispatcher, TrackerWriteLost
from shantytown.files import FilesRegistry
from shantytown.tmux import NullPanes

REASON = "blocked on a human; resume_when date:2026-09-23"


class NormalisingBr(BrTracker):
    """br as it actually behaves: a date-only defer comes back with a time."""

    def __init__(self, hour="13:00:00"):
        super().__init__(repo=None)
        self.hour = hour
        self.row = {"id": "item-1", "title": "t", "status": "open", "labels": []}

    def _bd_for(self, item_id, *args):
        if args[0] == "show":
            return subprocess.CompletedProcess(args, 0, json.dumps([self.row]), "")
        for arg in args[2:]:
            key, _, value = arg.partition("=")
            if key == "--status":
                self.row["status"] = value
            elif key == "--notes":
                self.row["notes"] = value
            elif key == "--defer":
                # THE BEHAVIOUR UNDER TEST: a bare day gains a time of day.
                self.row["defer_until"] = (
                    f"{value}T{self.hour}Z" if "T" not in value else value)
            elif key == "--add-label":
                self.row["labels"].append(value)
            elif key == "--remove-label":
                self.row["labels"] = [x for x in self.row["labels"] if x != value]
        return subprocess.CompletedProcess(args, 0, "", "")


def _dispatcher(tmp_path, tracker):
    return Dispatcher(FilesRegistry(tmp_path / "crew"), tracker, NullPanes())


def test_a_bare_date_confirms_even_though_the_store_adds_a_time(tmp_path):
    """The regression. Before the fix this raised TrackerWriteLost."""
    tracker = NormalisingBr()
    _dispatcher(tmp_path, tracker).defer("item-1", "human", REASON, until="2026-09-23")
    assert tracker.row["defer_until"] == "2026-09-23T13:00:00Z"
    assert tracker.row["status"] == "deferred"


@pytest.mark.parametrize("hour", ["00:00:00", "13:00:00", "23:59:59"])
def test_any_time_of_day_on_the_REQUESTED_day_confirms(tmp_path, hour):
    """The backend may pick any hour it likes; the promise was the DAY."""
    tracker = NormalisingBr(hour=hour)
    _dispatcher(tmp_path, tracker).defer("item-1", "human", REASON, until="2026-09-23")
    assert tracker.row["defer_until"].startswith("2026-09-23T")


def test_THE_CONTROL_a_wrong_DAY_is_still_caught(tmp_path):
    """The fix must not be "stop checking".

    Without this, relaxing the comparison to a date would be indistinguishable
    from deleting it — and a defer that silently lands on the wrong day hides
    work for longer than anyone agreed to.
    """
    class WrongDay(NormalisingBr):
        def _bd_for(self, item_id, *args):
            args = tuple(("--defer=2026-10-01" if a.startswith("--defer=") else a)
                         for a in args)
            return super()._bd_for(item_id, *args)

    with pytest.raises(TrackerWriteLost):
        _dispatcher(tmp_path, WrongDay()).defer(
            "item-1", "human", REASON, until="2026-09-23")


def test_THE_CONTROL_a_full_timestamp_still_compares_exactly(tmp_path):
    """An exact instant asked for is an exact instant promised."""
    class ShiftsTheHour(NormalisingBr):
        def _bd_for(self, item_id, *args):
            args = tuple(("--defer=2026-09-23T09:00:00Z"
                          if a.startswith("--defer=") else a) for a in args)
            return super()._bd_for(item_id, *args)

    with pytest.raises(TrackerWriteLost):
        _dispatcher(tmp_path, ShiftsTheHour()).defer(
            "item-1", "human", REASON, until="2026-09-23T13:00:00Z")
