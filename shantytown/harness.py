"""harness — WHICH agent program a card runs, and everything that is specific to it.

Stiwi, 2026-07-19: the agent harness should be mappable, *"like claude code"* —
i.e. Claude Code is ONE harness, not the shape of the world. Until now it was the
shape of the world in two separate places, and they were not next to each other:

    runtime.ClaudeRuntime.compose()  hardcoded the `claude` binary and its flags
    runtime.settings_for_role()      emitted Claude Code's hooks schema — its own
                                     docstring said so ("a second runtime emits
                                     its own format") and then there was nowhere
                                     for that second format to live.

Those two are ONE decision wearing two hats: if you launch `codex`, you do not
write a Claude Code settings.json, and if you write a Claude Code settings.json,
`--settings` is the flag that reads it. Splitting them across two functions is how
you get a launch composed for one program pointed at a config file for another. So
a Harness owns BOTH:

    launch()   the argv (env + binary + flags + how the settings file is passed)
    settings() the CONTENT of the file that argv points at

CLAUDE IS THE ONLY IMPLEMENTATION, and this is a PURE REFACTOR of it — the string
it composes for an existing card is byte-identical to what shipped before
(test_harness.py pins it against a literal, and test_new.py's --dry-run output is
unchanged). No second harness is invented here: one would be a guess, and a guess
about somebody else's CLI flags is exactly the kind of thing that looks shipped
and has never run (this repo's `python` vs `python3`, the unmeasured ready
markers, "Welcome to Claude Code"). What this file buys is that adding one later
touches THIS file and the card, and nothing in the tier.

A card with no `harness` field means "claude" — every card in existence today.
"""
from __future__ import annotations
from pathlib import Path
from typing import Protocol, runtime_checkable, TYPE_CHECKING

from .protocols import Agent

if TYPE_CHECKING:
    # Type-only: the capability declaration returns runtime.HookSpec, but harness
    # must not import runtime at module load (the import graph is one-directional —
    # runtime imports harness, never the reverse; see settings() below). The real
    # value is produced by a call-time import inside hooks().
    from .runtime import HookSpec

# The default, and the answer for every card that does not say otherwise.
DEFAULT = "claude"


class UnknownHarness(Exception):
    """A card names a harness we cannot host. It is a REFUSAL, not a fallback:
    launching the default because we did not recognise the card's request would
    start the wrong program with the wrong settings and report success."""


@runtime_checkable
class Harness(Protocol):
    """One agent program, and the two things that are specific to it."""
    name: str

    def launch(self, card: Agent, settings_path: str, root=None) -> str:
        """The full command line for this card. MUST reference settings_path —
        the launcher's invariant (runtime.py) is that a composed launch always
        carries its settings or is not composed at all."""
        ...

    def settings(self, role: str, root=None) -> dict:
        """The CONTENT of the settings file `launch` points at, for a ROLE. This
        is the file format half — Claude Code's hooks schema is Claude Code's,
        and a second harness emits its own."""
        ...

    def hooks(self, card: Agent) -> "HookSpec":
        """The CAPABILITY declaration the gate keys on: can the program this
        harness launches deliver a blocking stop hook to the MODEL?

        It lives HERE, on the harness, because it is a property of the PROGRAM —
        and the program is what the card selects (for_card), NOT the Runtime the
        CLI happens to construct. That mismatch was the whole of internal-ref: the
        gate asked a hardcoded ClaudeRuntime while the launched program came from
        card.harness. A capability declared on the object the card cannot pick is
        a gate that cannot see what it is gating."""
        ...


class ClaudeHarness:
    """Claude Code. First-class, and — for now — the only one.

    Both halves moved here verbatim from runtime.py; every comment explaining WHY
    a flag or a hook is there moved with it, because those comments are measured
    incidents (the --no-chrome consent screen, the SHANTY_ROOT belt, the hank
    guard's fail-open) and separating a rule from its reason is how the rule gets
    "cleaned up" by the next reader.
    """

    name = "claude"

    def launch(self, card: Agent, settings_path: str, root=None) -> str:
        # --no-chrome: crew agents do not use the Chrome integration, and WITHOUT
        # this a first-run claude stops at a "Claude in Chrome extension detected"
        # consent prompt that BLOCKS the ready UI — so st new's verify never sees
        # live and returns could-not-tell (2) for an agent that would be fine.
        # Live-fire confirmed (internal-ref): `claude --no-chrome` goes straight to
        # the ready UI, is_live True. This is the prod 0-path fix.
        # Remote Control ON BY DEFAULT (Stiwi 2026-07-19). A fleet you cannot reach
        # is a fleet you cannot run: this session sat unreachable for a day with an
        # unsubmitted prompt in its input line and no way to drive it from outside
        # (the gastown weaver stall). Naming the session after the agent is what
        # makes a 6-agent fleet addressable rather than a wall of anonymous panes.
        # Default, not opt-in — an agent you forgot to enable it on is exactly the
        # one you will need to reach.
        flags = f"--no-chrome --remote-control {card.name}"
        # --dangerously-skip-permissions is OPT-IN per agent (card.dangerous), never
        # global — a crew worker that must act without prompts sets it on its own
        # card; nobody else inherits it (the pilot, internal-ref.5).
        if card.dangerous:
            flags += " --dangerously-skip-permissions"
        # BOBBIN_ROLE is how hank's policy guard resolves WHICH scope applies
        # (hank#20: tenant is resolved --tenant, then BOBBIN_ROLE; scopes live in
        # .bobbin/config.toml under [hank.policy.scopes.<role>]). Exporting it per
        # agent is what lets ONE hook registration serve every role — without it
        # the guard has no scope to enforce and every agent is ungoverned.
        # SHANTY_ROOT is the BELT to --settings' braces, and it exists because of a
        # measured incident (internal-ref, sattler 2026-07-19). --settings is read ONCE,
        # at launch: when the Stop hook was later corrected on disk to carry an
        # absolute --root (c3fb472), every ALREADY-RUNNING agent kept the old unrooted
        # command forever. kelly's own pane showed it —
        #     stop_event send: no such agent: kelly (looked in
        #     <workspace>/crew/<agent>/.shanty/crew/<agent>.json)
        # — the cwd/.shanty default, resolved against the agent's OWN workspace, which
        # has no .shanty. The agent still looked "up" in `st crew`, still worked, still
        # committed; only its stop events vanished, so the administrator at the root of
        # the tier was silently deaf to it. Reproduced by mechanism from another
        # worker's cwd (rooted -> "persisted ev-2 to sattler", exit 0; unrooted ->
        # the same LookupError, exit 1).
        #
        # stop_event resolves root as `--root`, else $SHANTY_ROOT, else cwd/.shanty. A
        # hook that has lost its --root therefore lands in the RIGHT store anyway once
        # the env carries it, because the env is read at hook-run time, not baked into
        # a settings snapshot. That is the whole point: this makes the NEXT settings
        # change survivable for agents launched before it. It does not make a stale
        # settings file detectable — nipg items 1-2 (a doctor check, and role-set
        # naming the live agents it did NOT reach) are still open, and this must not be
        # mistaken for them.
        root_env = f"SHANTY_ROOT={Path(root).resolve()} " if root else ""
        launch = (
            f"{root_env}SHANTY_AGENT={card.name} BOBBIN_ROLE={card.role} "
            f"claude {flags} --settings {settings_path}"
        )
        # Launch IN the agent's workspace so Claude Code auto-loads its .mcp.json +
        # CLAUDE.md from there — the launcher wires the agent's servers + charter
        # WITHOUT ever reading their (secret-bearing) contents. cd prefix, so the
        # single send-keys still delivers one line.
        if card.workspace:
            launch = f"cd {card.workspace} && {launch}"
        return launch

    def settings(self, role: str, root=None) -> dict:
        # The Claude-Code-specific half, imported at call time to keep the import
        # graph one-directional (runtime imports harness, never the reverse).
        from .runtime import claude_settings_for_role
        return claude_settings_for_role(role, root=root)

    def hooks(self, card: Agent) -> "HookSpec":
        # Claude Code delivers blocking stop hooks — measured, load-bearing: a
        # lead/administrator's reports' stop events reach the MODEL via a blocking
        # Stop hook's `reason` (a non-blocking hook's stdout is discarded). This is
        # the SINGLE literal declaration of the capability now; ClaudeRuntime.hooks
        # forwards here rather than restating it, so the two cannot drift apart
        # (which is how the gate came to rubber-stamp a non-claude card, internal-ref).
        # Call-time import, same one-directional reason as settings() above.
        from .runtime import HookSpec
        return HookSpec(blocking_stop=True)


_HARNESSES = {h.name: h for h in (ClaudeHarness(),)}


def get(name: str | None) -> Harness:
    """The harness by name. None -> the default, which is every card today.

    RAISES UnknownHarness for a name we do not implement. It does NOT fall back to
    the default: a card that asks for `codex` and silently gets `claude` is a
    launch that succeeded at being the wrong thing.
    """
    key = name or DEFAULT
    if key not in _HARNESSES:
        raise UnknownHarness(
            f"card names harness {key!r}; this build implements "
            f"{sorted(_HARNESSES)}. Refusing to launch a different program than "
            f"the card asked for."
        )
    return _HARNESSES[key]


def for_card(card: Agent) -> Harness:
    return get(card.harness)


def name_for(card: Agent) -> str:
    """What harness IS this card's, as a string — including for a card that never
    said. This is what `st anchor --harness` prints, and it answers with the
    DEFAULT rather than blank: "claude" is the true answer for an unset field, and
    an empty status-bar segment would read as "no harness"."""
    return card.harness or DEFAULT
