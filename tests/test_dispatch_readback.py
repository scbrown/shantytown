"""st go reads its TRACKER write back before reporting success (aegis-8xc5w).

The fault this closes, measured 2026-08-03 against an embedded store:

    st --backend beads --repo <na> go na-1gl ellie
      -> "na-1gl -> ellie          in progress"      <- printed
      -> "sent to pane shanty-ellie"                 <- and the agent got it
    bd -C <na> show na-1gl
      -> ○ na-1gl ... [P2 · OPEN]   Owner: ellie     <- NO assignee, still OPEN

Nothing failed. bd exited 0, `update()` raises only on a non-zero bd, so go()
returned a Plan the CLI printed as a completed dispatch. The item stayed open and
unassigned, re-entered `bd ready`, and was dispatched AGAIN to someone else —
duplicate work arriving underneath the assignee guard that exists to prevent it.
And it was INTERMITTENT: the same command shape against the same store minutes
later stuck. So a single successful dispatch could never have verified a fix,
which is why every test here models the loss explicitly instead of hoping to
catch one.

THE FIXTURE IS THE POINT. _LosingTracker reproduces the measured signature and
not a convenient one: the write is ACCEPTED (no exception, no non-zero rc) and
the row does not change. A tracker that raised would have been caught by the code
that was already here.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown.dispatch import (Dispatcher, DispatchedButUntracked,
                                 TrackerWriteLost, _applied)
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.tmux import NullPanes


class _LosingTracker(FilesTracker):
    """Swallows the first `lose` writes: reports success, changes nothing.

    `lose=None` means lose them ALL — the permanently-broken store, which the
    intermittent fault does not look like but which the failure path has to
    handle, because after N verified losses we cannot tell the two apart and must
    stop claiming a dispatch was recorded.
    """

    def __init__(self, root, lose=0):
        super().__init__(root)
        self.lose = lose
        self.updates = 0
        self.gets = 0

    def update(self, item_id, **fields):
        self.updates += 1
        if self.lose is None or self.updates <= self.lose:
            return None                      # exit 0, row untouched. The fault.
        return super().update(item_id, **fields)

    def get(self, item_id):
        self.gets += 1
        return super().get(item_id)


@pytest.fixture
def world(tmp_path: Path):
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))

    def build(lose=0):
        # SETUP IS NOT THE SUBJECT. The item is created with losing switched off,
        # then the fault is armed — otherwise `lose=None` eats the fixture and
        # every test fails on a missing item instead of on the behaviour.
        trk = _LosingTracker(tmp_path / "items", lose=0)
        trk.update("item-1", title="Restore the den", status="open")
        trk.lose = lose
        trk.updates = 0; trk.gets = 0
        panes = NullPanes(screen="")                  # healthy pane -> NUDGE
        return Dispatcher(FilesRegistry(crew), trk, panes), trk, panes

    return build


def test_a_swallowed_write_is_no_longer_reported_as_a_dispatch(world):
    """THE BEAD. A store that accepts the write and does not apply it must not
    yield a Plan — which is what the CLI prints as '-> in progress'.

    Positive-controlled by construction: lose=None means the write can never
    land, so if this ever passes by returning a Plan, the read-back did not run.
    """
    d, trk, panes = world(lose=None)

    with pytest.raises(DispatchedButUntracked) as ei:
        d.go("item-1", "ellie")

    assert isinstance(ei.value.__cause__, TrackerWriteLost), (
        "the cause must name WHICH tracker failure this was — a silent loss is "
        "not the same finding as a store that said no")
    assert trk.get("item-1").status == "open", "control: the write really was lost"
    # The send is a FACT by this point and the exception has to say so, or a
    # coordinator re-runs `st go` and delivers the work twice.
    assert len(panes.sent) == 1
    assert "DELIVERED" in str(ei.value) and "Do NOT re-run" in str(ei.value)


def test_an_intermittent_loss_is_retried_and_the_dispatch_survives(world):
    """The measured shape: ONE write vanishes, the next sticks.

    A retry here is not the blind retry the fleet forbids. We have READ THE ROW
    and know nothing landed, and status/assignee are idempotent — there is no
    second object to double-apply. Compare create, where a retry mints a
    duplicate.
    """
    d, trk, _ = world(lose=1)

    p = d.go("item-1", "ellie")

    after = trk.get("item-1")
    assert after.status == "in_progress" and after.assignee == "ellie"
    assert p.track_attempts == 2, "the dispatch must COUNT what it had to survive"


def test_a_healthy_dispatch_reports_one_attempt_and_costs_one_extra_read(world):
    """No behaviour change on the path that already worked, and the counter says
    so. track_attempts == 1 is what lets the CLI stay quiet; anything else means
    the fault is live and the operator gets told."""
    d, trk, _ = world(lose=0)
    p = d.go("item-1", "ellie")
    assert p.track_attempts == 1
    assert trk.updates == 1, "a healthy write must not be re-attempted"
    # TWO reads: plan()'s resolution read, and ONE read-back. The read-back is a
    # single confirming read, not a poll — the fault it detects is a row that did
    # not change, and a row that did not change does not change while you watch.
    assert trk.gets == 2, "the read-back must cost exactly one extra read"


def test_dry_run_still_writes_nothing_and_counts_nothing(world):
    """The read-back must not leak into the preview. --dry-run is non-negotiable
    and track_attempts==0 is how it reports that nothing was written at all —
    distinct from 1, which asserts a confirmed write."""
    d, trk, panes = world(lose=None)
    p = d.go("item-1", "ellie", dry_run=True)
    assert trk.updates == 0 and panes.sent == []
    assert p.track_attempts == 0


def test_a_loud_tracker_failure_is_not_retried(world):
    """Only a VERIFIED loss earns a second attempt. A tracker that RAISES has
    already told us something — unknown id, store unreachable — and re-running it
    asks the same question again. This is the boundary between the retry that is
    correct here and the one the fleet forbids everywhere else."""
    d, trk, _ = world(lose=0)

    def boom(item_id, **fields):
        trk.updates += 1
        raise RuntimeError("bd update failed: connection refused")

    trk.update = boom
    with pytest.raises(DispatchedButUntracked) as ei:
        d.go("item-1", "ellie")

    assert trk.updates == 1, "a loud failure was retried — that is the forbidden retry"
    assert isinstance(ei.value.__cause__, RuntimeError)


def test_the_loss_report_names_what_the_store_actually_says(world):
    """A could-not-tell that does not say what it saw teaches nothing. The
    message must carry BOTH sides — what we asked for and what the row still
    holds — because the reader's next move is to go look at that row."""
    d, trk, _ = world(lose=None)
    with pytest.raises(DispatchedButUntracked) as ei:
        d.go("item-1", "ellie")
    cause = str(ei.value.__cause__)
    assert "in_progress" in cause and "'open'" in cause, cause
    assert "ellie" in cause


# _applied is the predicate that decides what counts as a loss. It gates an
# exit-2 on an ALREADY-DELIVERED dispatch, so its false negatives cost more than
# its false positives — these pin that asymmetry rather than leaving it to a
# comment.

def test_applied_accepts_a_tracker_that_namespaces_a_name():
    """`aegis/ellie` for `ellie` is the SAME assignment. Calling it a lost write
    would report a healthy dispatch as broken and send someone hand-repairing a
    record that is already correct."""
    assert _applied("aegis/ellie", "ellie")
    assert _applied("ellie", "aegis/ellie")
    assert _applied(" in_progress ", "in_progress")


def test_applied_rejects_exactly_the_measured_failure():
    """Unset and unchanged — the two faces of the row that never moved."""
    assert not _applied(None, "ellie")
    assert not _applied("", "ellie")
    assert not _applied("open", "in_progress")
