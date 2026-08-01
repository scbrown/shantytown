"""The interfaces. Every one of these has two implementations or it isn't an interface.

The second implementation is not charity — it is the leak detector. If a second
impl is hard to write, the first one has leaked into the core.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable

# The shared ancestor for every "I could not look" in this repo. The three
# exceptions below grew independently, at three call sites, with three names and
# the same reasoning written out three times; they keep their names (they are
# caught by name in several places) but now share a base, so a caller that treats
# all of them alike can write ONE except clause.
from shantytown.answer import CouldNotLook


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
    harness: str | None = None    # WHICH agent program this card runs (harness.py).
                                  # None = "claude", which is every card that
                                  # exists today. It is on the CARD and not a
                                  # global because the harness is a property of the
                                  # AGENT — a fleet is allowed to be mixed, and the
                                  # only way to find out that it is not is to have
                                  # somewhere to say so. The launcher hardcoded
                                  # `claude` and its settings FORMAT in two
                                  # separate functions; this field is what selects
                                  # between them (Stiwi: the harness should be
                                  # mappable, "like claude code" — i.e. Claude Code
                                  # is one harness, not the shape of the world).
    # None = NOT EXPRESSED, and it is not the same as False (aegis-6hfmi). A
    # caller that never mentions retirement must not be able to CLEAR one: the
    # default used to be False and `set()` wrote it unconditionally, so any
    # source that did not model retirement silently un-retired every agent it
    # touched. It did not have to intend it, only to not know. Absence is now
    # representable, so "I am not saying" and "I say not retired" are different
    # writes. Every consumer tests truthiness, so None reads as not-retired.
    retired: bool | None = None   # DELIBERATELY stopped. `st tend` must never
                                  # respawn it, and finding it ALIVE is an
                                  # escalation, not a line in a log. It lives on
                                  # the CARD because a retirement held in a
                                  # process ends when that process does — which
                                  # is exactly when a supervisor wakes up and
                                  # undoes it. A watchdog that could not express
                                  # this reverted a considered shutdown of eight
                                  # agents in about a minute, silently.
    retired_by: str | None = None # WHO last moved `retired`, and WHEN. Best-effort
    retired_at: str | None = None # attribution, honestly labelled — the crew name
                                  # from $SHANTY_AGENT, else `unix:<login>`, so a
                                  # reader can always tell which kind of actor it
                                  # was; `unknown` when neither is readable. The
                                  # timestamp is UTC ISO-8601.
                                  #
                                  # THE COST OF NOT HAVING IT (aegis-6hfmi): ian's
                                  # retirement moved, and answering "who?" took two
                                  # agents, a cross-session disagreement, and a
                                  # stat(1) on the card's mtime to establish that
                                  # BOTH measurements had been correct on either
                                  # side of an unrecorded write. mtime says a write
                                  # happened; it cannot say whose, or which field.
                                  # One line on the card answers it in one read.
                                  #
                                  # PROVENANCE IS WRITTEN AS A UNIT WITH `retired`
                                  # AND NEVER SURVIVES IT (see files.set). Stale
                                  # attribution is worse than none: "retired_by:
                                  # sattler" left sitting on a retirement somebody
                                  # ELSE made is a confident, greppable, wrong
                                  # answer to the exact question this field exists
                                  # to answer — the same-output-two-worlds shape
                                  # that made the field necessary, rebuilt inside
                                  # the fix. Absent reads as "nobody recorded it",
                                  # which is true and leads you to look further.
    dangerous: bool = False       # opt-in --dangerously-skip-permissions for THIS
                                  # agent. Per-agent, never global — a crew worker
                                  # that must act without permission prompts sets
                                  # it on its card; nobody else is affected.
    roles: tuple[str, ...] = ()   # the STACKED role set (GitHub #37). `role` above
                                  # is this agent's TREE POSITION and stays the
                                  # field the built-in process reads; this is the
                                  # full set of trait-presets it holds, which the
                                  # tree position cannot express — one agent runs
                                  # {worker, keeper:security, escalation-target}
                                  # and the enum could say only "worker", leaving
                                  # three of its four behaviours invisible to
                                  # every routing and scaling decision.
                                  # EMPTY IS NOT (role,). Empty means NOBODY SAID,
                                  # and the difference is the migration: a card
                                  # that has not been given a set must be
                                  # distinguishable from one whose set happens to
                                  # be its tree position, or "has this been
                                  # migrated?" becomes unanswerable. Consumers
                                  # that want a usable set call effective_roles().
    domain: str | None = None     # WHICH domain a domain-scoped role owns — a
                                  # per-MEMBER parameter, not a property of the
                                  # role, so `keeper` stays reusable and the
                                  # member supplies what it keeps.

    def effective_roles(self) -> tuple[str, ...]:
        """The role set to ACT on: the declared stack, or the tree position alone.

        The fallback lives here rather than at each call site so "an un-migrated
        card behaves exactly as it always did" is one line that cannot drift —
        while `roles` itself stays honest about whether anything was declared.
        """
        return self.roles or (self.role,)


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str = ""
    status: str = "open"
    assignee: str | None = None
    priority: int | None = None   # HOW IMPORTANT, in the tracker's own numbering,
                                  # where 0 is the HIGHEST (bd's convention: P0
                                  # beats P1). Added for the usage governor
                                  # (aegis-hdqej): "dispatch only P1 and above"
                                  # is unanswerable without it, and a dispatch
                                  # gate that cannot read a priority would have
                                  # to either admit everything or refuse
                                  # everything.
                                  # None is NOT a default priority — it means the
                                  # tracker did not say. The difference is
                                  # load-bearing: a governed dispatch REFUSES an
                                  # item whose importance nobody stated rather
                                  # than guessing a middle value for it, so
                                  # "nobody set one" stays visible instead of
                                  # silently becoming P2.


@runtime_checkable
class Registry(Protocol):
    """Identity: who exists, who reports to whom, what role.

    REQUIRED. There is no `none` registry — you cannot start an agent whose
    identity you cannot read. quipu is first-class; files is the second impl,
    and it exists to prove quipu hasn't leaked into the core.
    """
    def get(self, name: str) -> Agent: ...
    def all(self) -> list[Agent]: ...

    def empty_note(self) -> str | None:
        """What an EMPTY `all()` means for this registry. None = it means exactly
        "nobody exists". A string = "it might instead mean I looked in the wrong
        place", and the string says where to look — it is rendered to the operator.

        THIS EXISTS BECAUSE THE ANSWER IS NOT THE SAME FOR BOTH IMPLS, and the one
        place that consumes it (roles.check) must stay registry-agnostic.

        FilesRegistry already draws the line correctly and keeps it: an ABSENT
        directory raises ("I could not look") while a PRESENT-but-empty one returns
        [] ("nobody exists"). That reasoning is sound there because the directory IS
        the whole search space — having read all of it and found nothing is a
        complete observation.

        It does not transfer to quipu, and applying it there is what made
        `roles --check --registry quipu` print a clean bill of health for a crew of
        twelve. A graph is never "absent", so there is no second signal to fall back
        on; and which entities exist is selected by the ONTOLOGY NAMESPACE, which is
        deployment config the client cannot validate. A well-formed query under the
        wrong namespace is answered truthfully with zero rows. So for quipu, empty
        conflates "nobody exists" with "I looked in the wrong place" and nothing
        downstream can separate them.

        THE DEFAULT IS FAIL-SAFE, and the direction is deliberate (the same
        asymmetry argument QuipuRegistry makes about `crewStatus`): a registry that
        does not implement this is treated as NOT vouching for its empty answer.
        Forgetting to say "my empty is real" costs a loud `cannot tell`; forgetting
        to say "my empty is suspect" costs a false clean bill of health, which is
        the bug this whole method is here to prevent.
        """
        ...


@runtime_checkable
class Tracker(Protocol):
    """Two functions. Anything more and the tracker is driving the harness.

    UNRESOLVED, and deliberately not resolved here: `st anchor`
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
    # attrs=True keeps the RENDERING attributes (tmux `-e`), not just the text.
    # Required by triage.input_state (internal-ref): dim is the only thing that
    # separates Claude Code's placeholder suggestion from real queued-unsubmitted
    # input, both of which render as `❯ <text>`, and plain capture strips exactly
    # that bit. An adapter that cannot supply attributes returns plain text and
    # triage answers UNKNOWN — which is a refusal, not a guess.
    def capture(self, pane: str, history: int = 0, attrs: bool = False) -> str: ...
    # The launch command line of the process in the pane, or None if it cannot be
    # read. A READ of the session surface, exactly like exists/capture — it adds
    # no launch or handoff verb, so the #5 invariant (Panes cannot express a
    # handoff) is untouched. Required because "is this lead up" must mean "will
    # it drain", not "does something answer to its name": a pane resurrected by a
    # FOREIGN launcher carries that launcher's wiring, and routing to it on the
    # strength of the name alone loses every event (internal-ref). An adapter that
    # cannot read it returns None, and the caller fails toward RISING.
    # ONE editing key, drawn from the adapter's CONTROL_KEYS allowlist —
    # cursor moves, deletes, Escape.
    # Takes no text and the allowlist holds no Enter and no Tab, so it cannot
    # submit a buffer or ACCEPT a ghost-text suggestion (aegis-c6hli). That
    # keeps invariant #5 intact: this adds a way to EDIT a pane's input box,
    # not a way to express a handoff or inject text.
    def control(self, pane: str, key: str) -> None: ...
    def cmdline(self, pane: str) -> str | None: ...
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


class ContextUnavailable(CouldNotLook):
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


class RankUnavailable(CouldNotLook):
    """A ranker could not compute weights. NOT "nothing to rank" — "I could not
    look". Same shape as ContextUnavailable, and for the same reason: a down
    ranker (Hank/Quipu unreachable) and a ranker that found nothing to weight
    return along different paths. The drain CATCHES this and degrades to the
    rule-based order (workflow.prioritize) — a wrong weight, or a wedged hook, is
    worse than an unweighted one. A ranker must never silently return unweighted
    while pretending it looked."""


@runtime_checkable
class Ranker(Protocol):
    """Weight prioritization candidates by an external signal (structural blast
    radius via Hank, governed policy via Quipu).

    ONE method, like Context. It takes the candidates and returns them with
    `weight`/`why` populated; it MUST raise RankUnavailable rather than return
    them unweighted when it could not reach its backend. The default impl
    (policy.NullRanker) needs no backend and returns them unchanged — the leak
    detector that proves the whole feature runs without Hank/Quipu."""
    def weigh(self, candidates: list) -> list: ...


@dataclass(frozen=True)
class Event:
    """One governed entity event — a Quipu transaction a subscriber cares about.
    `id` is the transaction id (the watermark unit); actor/source/timestamp are the
    provenance the transaction log records."""
    id: int
    actor: str | None = None
    source: str | None = None
    timestamp: str | None = None


class EventsUnavailable(CouldNotLook):
    """Could not reach the event source. NOT "no events" — "I could not look"
    (the same discipline as ContextUnavailable). A subscriber must surface this as
    could-not-tell and KEEP its watermark, never treat it as an empty poll and
    advance past events it never saw."""


@runtime_checkable
class EventSource(Protocol):
    """Subscribe to governed entity events. integrations.md sketched this row
    (events) with reactor as the intended first-class impl; reactor has no honest
    pull surface, so the first real impl is Quipu's cursored transaction log
    (quipu_events.QuipuEvents), with a `none` second impl as the leak detector.

    Honest pull: the watermark advancing is the liveness proof; an unreachable
    source raises EventsUnavailable rather than returning an empty iterator."""
    def subscribe(self, kinds: list[str] | None = None) -> Iterator[Event]: ...


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
