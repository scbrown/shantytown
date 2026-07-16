"""The interfaces. Every one of these has two implementations or it isn't an interface.

The second implementation is not charity — it is the leak detector. If a second
impl is hard to write, the first one has leaked into the core.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Agent:
    """Identity. The truth lives in the registry, not in this object."""
    name: str
    role: str = "worker"          # worker | lead | administrator
    reports_to: str | None = None
    pane: str | None = None


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str = ""
    status: str = "open"
    assignee: str | None = None


@runtime_checkable
class Registry(Protocol):
    """Identity: who exists, who reports to whom, what role.

    REQUIRED. There is no `none` registry — you cannot start an agent whose
    identity you cannot read. quipu is first-class; files is the second impl,
    and it exists to prove quipu hasn't leaked into the core.
    """
    def get(self, name: str) -> Agent: ...
    def all(self) -> list[Agent]: ...


@runtime_checkable
class Tracker(Protocol):
    """Two functions. Anything more and the tracker is driving the harness.

    UNRESOLVED, and deliberately not resolved here (aegis-gqr8): `shanty prime`
    must answer "what's on my plate", and it CANNOT through this protocol —
    get() needs an id you do not have yet. I briefly added a third method,
    mine(), and it broke test_swap's two-function assertion, which exists to
    enforce exactly this line. The test was right to stop me: a shared contract
    is not mine to widen at 2am.

    For now prime reads the plate through a per-adapter helper (files.plate),
    so this protocol is unchanged and the beads swap keeps working. That is a
    holding position, not an answer — it means every new tracker owes a plate
    reader that the protocol does not describe.

    The real question for arnold: does prime's need justify a third method? If
    yes, the shape that preserves the design is `mine(agent) -> WorkItem | None`
    — Optional, not a list, so a primer structurally CANNOT print a backlog
    ("a primer that prints a backlog is a dashboard"). A function that cannot
    return two things cannot grow a dashboard.

    THREE functions as of 2026-07-16, by Stiwi's direction: `st task` creates
    work, and creation cannot be expressed through get/update — update() needs an
    id that does not exist yet. This is an OWNER-DIRECTED widening, which is a
    different act from the one test_swap caught: that was a shared contract
    widened unilaterally at 2am to make one command work. The guard still pins the
    surface; it now pins it at three, and a fourth method still fails the test.
    """
    def get(self, item_id: str) -> WorkItem: ...
    def update(self, item_id: str, **fields) -> None: ...
    def create(self, title: str, **fields) -> WorkItem: ...


@runtime_checkable
class Panes(Protocol):
    def send(self, pane: str, text: str) -> None: ...
    def exists(self, pane: str) -> bool: ...
