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
    model: str | None = None      # the model this agent runs. Persisted on the
                                  # card so a restart honors it instead of
                                  # silently reverting to the default (#9). None
                                  # = use the launcher default.
    workspace: str | None = None  # the cwd to launch the agent in. Claude Code
                                  # auto-loads .mcp.json and CLAUDE.md from here,
                                  # so pointing at the agent's existing dir wires
                                  # its servers + charter WITHOUT the launcher ever
                                  # reading their contents (the pilot.5:
                                  # secrets stay in the agent's own files). None =
                                  # launch in the default cwd.
    workspace_source: str | None = None
                                  # where to CLONE the workspace from if it is
                                  # absent (a git URL or a local path). The card
                                  # carried a workspace PATH and no way to create
                                  # it, so the "clone if absent" half of the
                                  # ensure step was unbuildable.
                                  # SEPARATE from workspace on purpose: the path
                                  # is where the agent LIVES, the source is where
                                  # it CAME FROM, and an agent whose dir already
                                  # exists needs the first and never the second.
                                  # None = the dir must already exist, or the
                                  # launch is refused. We never derive a source
                                  # from a naming convention: a guessed remote is
                                  # how an agent gets launched into the wrong repo.
    dangerous: bool = False       # opt-in --dangerously-skip-permissions for THIS
                                  # agent. Per-agent, never global — a crew worker
                                  # that must act without permission prompts sets
                                  # it on its card; nobody else is affected.


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

    UNRESOLVED, and deliberately not resolved here: `shanty prime`
    must answer "what's on my plate", and it CANNOT through this protocol —
    get() needs an id you do not have yet. I briefly added a third method,
    mine(), and it broke test_swap's two-function assertion, which exists to
    enforce exactly this line. The test was right to stop me: a shared contract
    is not mine to widen at 2am.

    For now prime reads the plate through a per-adapter helper (files.plate),
    so this protocol is unchanged and the beads swap keeps working. That is a
    holding position, not an answer — it means every new tracker owes a plate
    reader that the protocol does not describe.

    RULED (arnold): NO. "What's on my plate" is NOT a Tracker method.
    It stays a per-backend PLATE READER (files.plate, beads.plate) injected into
    prime. Reasons: (1) the two-function contract is load-bearing — ellie's test
    and the BeadsTracker swap both depend on it, and mine() broke both; (2) a
    plate reader is a QUERY, and queries are exactly what this protocol excludes
    to keep the tracker from driving the harness; (3) malcolm's single-item
    instinct was right but the placement was wrong — Optional-not-a-list belongs
    on the reader, not smuggled into the shared surface. The holding position IS
    the answer. The debt it named (every backend owes a plate reader) is paid:
    beads.plate now exists alongside files.plate, both returning at most one item,
    both raising rather than reporting an empty plate when they could not look.

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
    # history=0 (default) returns the VISIBLE pane — what triage must judge on,
    # since a marker in scrollback is an agent TALKING about a state, not being
    # in it. history=N also returns the last N lines of scrollback, which is what
    # VERIFY needs: a fast agent scrolls the echoed dispatch off-screen before we
    # can look, so a visible-only check can never confirm a delivery that worked.
    def capture(self, pane: str, history: int = 0) -> str: ...
                                               # it to decide, #2 verify reads it
                                               # to confirm a send landed. Both
                                               # Tmux and NullPanes implement it;
                                               # it was a de-facto protocol method
                                               # that was never declared.
    # #5 session lifecycle (arnold's ruling). new_session makes an
    # EMPTY pane and RAISES if the name exists (never clobber a live agent);
    # kill_session is idempotent. Launching the agent-with-hooks is a runtime
    # send(), NOT a Panes verb — that boundary keeps handoff from leaking in.
    def new_session(self, name: str) -> str: ...
    def kill_session(self, name: str) -> None: ...
    # Ownership provenance (dearing's safety requirement). new_session
    # marks the session st-owned; owns() reports it. `st stop` refuses to reap a
    # session it does not own, even on an exact name match — the registry pane
    # names collide with the live crew, so a name match is not permission to kill.
    def owns(self, name: str) -> bool: ...


@dataclass(frozen=True)
class Snippet:
    """A place to look. Not the code — the pointer to it.

    The agent reads the file itself, the same way it reads a work item. If this
    ever carries the source text, context has become a cache and the harness has
    grown a job it does not want.
    """
    path: str
    lines: str = ""
    score: float = 0.0
    repo: str = ""
    name: str = ""


class ContextUnavailable(Exception):
    """I could not look. NOT "there is nothing there".

    This exception exists because the designed signature cannot express the
    difference. `relevant() -> list[Snippet]` has exactly one way to say "no
    results", and TWO facts need saying:

        the none-adapter, and a bobbin that is DOWN, return the same bytes
        and mean opposite things.                                — ellie

    An empty list is a FINDING: I asked, and the answer was nothing. This is a
    FAILURE: I never got an answer. Callers map them to different exit codes (0
    vs 2), and they must never collapse. ellie's sweep read a rate-limited 429
    as "metric absent" and manufactured 32 fake findings — "I could not look"
    scored as "there is nothing there". That is this exception's whole job.
    """


@runtime_checkable
class Context(Protocol):
    """Given what an agent is doing, what code should it be looking at?

    Read-only, synchronous, best-effort (docs/adapters.md:89 — Stiwi's ask at :84).

    ONE method. It cannot grow a query API the way a tracker can, and that is
    deliberate: an integration that exists because it is in a table is how you
    get to 110 commands. If you need a second method, THAT IS THE FINDING — put
    it on the bead, not in this file. (the same rule that caught a
    third Tracker method inside a day.)

    Implementations MUST raise ContextUnavailable rather than return [] when
    they could not reach their backend. Returning [] there is the defect.
    """
    def relevant(self, query: str, budget: int) -> list[Snippet]: ...
