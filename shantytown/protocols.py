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
    """Three functions, and the third one is load-bearing on purpose.

    It was two. `shanty prime` has to answer "what's on my plate" and could not:
    get() needs an id you do not have yet. The options were a query API (which is
    how a tracker starts driving the harness), or prime reaching past the
    protocol into the files layout (which is how the second impl stops being a
    leak detector). Neither.

    So: mine() returns AT MOST ONE item — Optional, not a list. cli.md says "One
    item, or none. A primer that prints a backlog is a dashboard", and this
    signature makes that structurally true instead of a rule someone has to
    remember. A function that cannot return two things cannot grow a dashboard.
    That is the whole justification for the third method; a fourth needs its own.
    """
    def get(self, item_id: str) -> WorkItem: ...
    def mine(self, agent: str) -> WorkItem | None: ...
    def update(self, item_id: str, **fields) -> None: ...


@runtime_checkable
class Panes(Protocol):
    def send(self, pane: str, text: str) -> None: ...
    def exists(self, pane: str) -> bool: ...
