"""runtime — the launcher seam. Claude Code first-class, swappable.

This is the SECOND HALF of the anti-handoff seam (arnold's #5 launch ruling,
aegis-qdal). #5a gave Panes no handoff verb; this gives the launcher no
settings-less code path. The invariant is enforced from BOTH sides:

    Panes cannot express a handoff.  The launcher cannot express a settings-less
    start.  Same invariant, two seams.

WHERE THE WORK SPLITS (arnold, restated):
    Runtime.start(card, pane)  COMPOSES the launch string  ->  this module
    Panes.send(pane, string)   DELIVERS it                 ->  tmux.py
Panes stays runtime-blind; the composition never leaks into it, so a second
runtime (codex/opencode) is a drop-in that composes its own string and declares
its own capabilities.

THE INVARIANT (the whole ruling):
    The composed command ALWAYS carries --settings, or it is NOT COMPOSED AT
    ALL. There is no code path that yields a settings-less launch. --settings is
    what wires the hooks; dropping it is the hookless-zombie handoff bug one
    layer up. So compose() either returns a string containing --settings, or it
    RAISES. It never returns a launch without it.

HONEST BOUNDARY (say it so nobody over-claims):
    compose() guarantees --settings was REQUESTED (the string provably carried
    it). It does NOT guarantee hooks FIRED — that is GT's unanswerable "did I get
    primed?". `st new`'s pane-verify proves the PROCESS is live, not that hooks
    registered. Keep the two claims apart: composition guarantees settings were
    requested; pane-verify guarantees the process came up. A green verify must
    never be read as "hooks registered" — it cannot show that.
"""
from __future__ import annotations
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from .protocols import Agent


@dataclass(frozen=True)
class HookSpec:
    """What stop/start hooks a runtime can declare. A CAPABILITY declaration, not
    metadata (adapters.md). The one that hurts: a runtime whose stop hook cannot
    reach the MODEL cannot host a lead — the whole lead role is "receive your
    reports' stop events". Claude Code declares blocking stop hooks; a runtime
    that does not (measured: codex) can host workers only.

    blocking_stop: can this runtime deliver a message to its agent AT STOP, to the
                   MODEL (not just the user's terminal)? Claude Code's non-blocking
                   stop-hook stdout is discarded; only a blocking hook's `reason`
                   reaches the model. So "notify at stop" is blocking-or-nothing,
                   and that is exactly what a lead needs.
    """
    blocking_stop: bool


# Roles that RECEIVE stop events, so they require blocking_stop delivery. A
# worker does not. adapters.md names the lead; an administrator also absorbs
# risen stop events (tier.route_stop), so it needs the same capability.
_ROLES_NEEDING_STOP = frozenset({"lead", "administrator"})


class CapabilityError(RuntimeError):
    """The card's ROLE needs a capability the RUNTIME cannot declare. REFUSE:
    write nothing, launch nothing. The loud refusal IS the point (adapters.md) —
    a lead on a runtime that cannot deliver stop events is a tier that exists on
    paper and absorbs nothing, and that failure is silent, the one kind we do not
    ship."""


class SettingsError(RuntimeError):
    """The role's --settings could not be materialized. REFUSE: launch nothing.
    A launch without --settings is the hookless zombie; if we cannot produce the
    settings, we do not launch a settings-less fallback — we refuse."""


@runtime_checkable
class Runtime(Protocol):
    """An agent runtime does three things (adapters.md). start() is this ruling."""
    name: str
    def start(self, card: Agent, pane: str) -> None: ...   # compose + send
    def hooks(self, card: Agent) -> HookSpec: ...          # capability declaration


def require_capability(rt: Runtime, card: Agent) -> None:
    """Refuse a card whose role needs a capability its runtime cannot declare.

    This is the capability gate adapters.md sketches:
        role 'lead' requires on_report_stop delivery; runtime 'codex' does not
        declare blocking stop hooks -> malcolm stays worker, nothing written.
    Keyed on the runtime's DECLARED hooks(), not on a hardcoded name check, so a
    third runtime that happens to support blocking stop hooks passes without a
    code change here — the declaration is the source of truth.
    """
    if card.role in _ROLES_NEEDING_STOP and not rt.hooks(card).blocking_stop:
        raise CapabilityError(
            f"runtime {rt.name!r} does not declare blocking stop hooks; "
            f"role {card.role!r} requires stop-event delivery to the model. "
            f"{card.name} stays worker. Nothing written, nothing launched."
        )


# A settings resolver maps a card -> the path to the settings file that wires the
# hooks its ROLE needs. Returns None (or raises) if it cannot be materialized.
# INJECTED, not hardcoded: the actual hook-file CONTENT is emitted by role
# set / #6 (aegis-ct5q); #5 owns the launch SEAM and its invariant. The default
# resolver expects the role's settings file to already exist and refuses if not
# — that refusal IS the invariant working (no settings -> no launch).
SettingsResolver = Callable[[Agent], "str | None"]


# The internal entry the emitted Stop hooks call (arnold's #6 ruling). NOT an st
# subcommand — plumbing, so the command-count test never sees it.

# The interpreter RUNNING shantytown, never the bare name "python". Stock Ubuntu
# ships python3 with NO unversioned `python`, so the hardcoded name made EVERY
# emitted Stop hook die with `/bin/sh: 1: python: not found` — found in live use
# on the first real launch (harding, the qdal pilot). The hook failed on every
# turn, which silently killed the whole stop-event route (send/drain, #6): the
# feature looked shipped and had never once run. sys.executable is by
# construction an interpreter that exists and can import shantytown.
_PY = sys.executable or "python3"


def _stop_cmd(mode: str, root=None) -> dict:
    """One Stop-hook command, with the store's location BAKED IN.

    stop_event resolves its root as `--root`, else $SHANTY_ROOT, else CWD/.shanty
    — and the launcher runs the agent in ITS OWN WORKSPACE, which has no .shanty.
    So an unrooted hook looked for the registry in e.g.
    ~/gt/beads_aegis/crew/gennaro/.shanty, found nothing, and every stop event
    died unpersisted: `events/` was never even created (measured — four live
    workers, zero events). Baking the absolute root is what makes send/drain
    reach the real store no matter where the agent is launched.
    """
    cmd = f"{_PY} -m shantytown.stop_event {mode}"
    if root is not None:
        cmd += f" --root {Path(root).resolve()}"
    return {"type": "command", "command": cmd}


# --- hank policy guard (first-class, Stiwi 2026-07-19) --------------------------
# Every shantytown-launched agent runs its edits past hank's guard: the agent's
# edit tool call IS the change event (hank FR-30), so hank answers with a blast-
# radius advisory and MAY deny. Wired here, once, rather than per-agent — that is
# what "first class" means: you cannot launch an unguarded agent by forgetting a
# flag.
#
# FAIL OPEN, deliberately and non-negotiably. `command -v` short-circuits when
# hank is not installed, and `|| exit 0` swallows ANY hank failure (absent
# subcommand, crashed daemon, timeout) into "allow". A guard that failed CLOSED
# would brick every crew agent the moment hank was down or lagging a release —
# turning a code-intelligence nicety into a fleet outage. hank denies by emitting
# the block JSON on stdout with exit 0, so a real deny is never confused with a
# failure, and this wrapper cannot swallow it.
_HANK_GUARD = (
    "command -v hank >/dev/null 2>&1 && hank hook pre-edit || exit 0"
)


def _guard_hook() -> dict:
    return {
        "matcher": "Edit|Write|NotebookEdit",
        "hooks": [{"type": "command", "command": _HANK_GUARD}],
    }


def settings_for_role(role: str, root=None) -> dict:
    """The Claude Code settings.json a role needs — the CONTENT `role set` emits
    and `st new`'s launch reads via --settings (#6, arnold gt-wisp-w4j2af).

    This is Claude-Code-SPECIFIC (its hooks schema), so it lives with the runtime,
    not in the runtime-agnostic tier — a second runtime emits its own format.

    Every non-root role SENDs its own stop up (route_stop -> persist). Every
    DESTINATION (lead, admin) also DRAINs received events into its model. A Stop
    hook does not carry a 'blocking' flag — the DRAIN command BLOCKS by printing
    decision:block, which is exactly why a destination needs a runtime whose stop
    hook can reach the model (the capability gate refuses a lead on one that
    can't). So:
        worker        -> [send]          (send-only; never receives)
        lead          -> [send, drain]   (sends its own stop up; drains reports')
        administrator -> [drain]         (root: receives only; its stop terminates)
    """
    if role == "worker":
        stop = [_stop_cmd("send", root)]
    elif role == "lead":
        stop = [_stop_cmd("send", root), _stop_cmd("drain", root)]
    elif role == "administrator":
        stop = [_stop_cmd("drain", root)]
    else:
        raise ValueError(f"unknown role {role!r}; expected worker/lead/administrator")
    return {
        "hooks": {
            "Stop": [{"hooks": stop}],
            # hank policy guard on every edit-shaped tool call. See _HANK_GUARD.
            "PreToolUse": [_guard_hook()],
        },
        # Pre-answer the project-MCP consent screen. A FRESH workspace makes Claude
        # Code ask "N new MCP servers found — enable?" and that prompt BLOCKS the
        # ready UI, so is_live sees nothing and st new reports could-not-tell for an
        # agent that is actually fine (observed on harding's first launch: it sat on
        # the picker until a human pressed Enter). Same third-state class the launch
        # already handles for chrome with --no-chrome.
        # Not a widening of trust: the launcher already elects the agent's workspace
        # — and therefore ITS .mcp.json — by putting it on the card. This only stops
        # us asking a human to re-affirm a choice the card already made.
        "enableAllProjectMcpServers": True,
    }


class ClaudeRuntime:
    """Claude Code, first-class. Composes `SHANTY_AGENT=<name> claude --settings
    <path>` and delivers it through the injected Panes.

    SHANTY_AGENT carries IDENTITY: `st prime` defaults `me` to $SHANTY_AGENT, so
    the agent resolves who it is by running prime ITSELF. We do NOT wire prime as
    a mutating SessionStart hook — prime is a pure read (cli.md), and GT's --hook
    prime mutated state and made "did I get primed?" unanswerable. The launcher
    exports identity; the agent runs prime.
    """

    name = "claude"

    # Positive signal that Claude Code has taken over the pane. A capture that
    # contains none of these is NOT live (still a shell, an error, or nothing).
    # CONFIRMED by live-fire against real claude v2.1.214 (aegis-zx7l probe,
    # 2026-07-18): the ready UI carries the version banner "Claude Code v" AND the
    # persistent status line "? for shortcuts". The earlier "Welcome to Claude
    # Code" was a GUESS and never appears — a marker never observed passing is not
    # a marker (my validate-the-instrument rule); it is now replaced with two that
    # were watched to match a real ready pane.
    READY_MARKERS = ("? for shortcuts", "Claude Code v")
    # Definitely-not-live signals — a failed launch, seen as loudly as possible.
    DEAD_MARKERS = ("command not found", "no such file", "not found", "Traceback")
    # A first-run consent screen (e.g. "Claude in Chrome extension detected") is a
    # THIRD state: not live, not failed — WAITING FOR A HUMAN. It blocks the ready
    # UI, so is_live correctly returns False and st new reports could-not-tell (2).
    # The real fix is to launch past it (a settings/config that pre-answers), which
    # is entangled with what role-set emits — tracked on zx7l, not guessed here.
    CONSENT_MARKERS = ("Claude in Chrome extension detected", "keep browser tools off")

    def __init__(self, panes, resolve_settings: SettingsResolver) -> None:
        self._panes = panes
        self._resolve = resolve_settings

    def hooks(self, card: Agent) -> HookSpec:
        # Claude Code declares blocking stop hooks — measured, load-bearing.
        return HookSpec(blocking_stop=True)

    def compose(self, card: Agent) -> str:
        """Build the launch string, or RAISE. Never returns a settings-less launch.

        Order matters: capability first (a lead on a runtime that cannot host it
        must refuse before we bother materializing settings), then settings.
        """
        require_capability(self, card)                 # CapabilityError -> refuse
        settings_path = self._resolve(card)
        if not settings_path:
            raise SettingsError(
                f"could not materialize settings for {card.name} "
                f"(role {card.role!r}); refusing to launch a settings-less agent."
            )
        # --no-chrome: crew agents do not use the Chrome integration, and WITHOUT
        # this a first-run claude stops at a "Claude in Chrome extension detected"
        # consent prompt that BLOCKS the ready UI — so st new's verify never sees
        # live and returns could-not-tell (2) for an agent that would be fine.
        # Live-fire confirmed (aegis-84z1): `claude --no-chrome` goes straight to
        # the ready UI, is_live True. This is the prod 0-path fix.
        flags = "--no-chrome"
        # --dangerously-skip-permissions is OPT-IN per agent (card.dangerous), never
        # global — a crew worker that must act without prompts sets it on its own
        # card; nobody else inherits it (the pilot, aegis-qdal.5).
        if card.dangerous:
            flags += " --dangerously-skip-permissions"
        launch = f"SHANTY_AGENT={card.name} claude {flags} --settings {settings_path}"
        # Launch IN the agent's workspace so Claude Code auto-loads its .mcp.json +
        # CLAUDE.md from there — the launcher wires the agent's servers + charter
        # WITHOUT ever reading their (secret-bearing) contents. cd prefix, so the
        # single send-keys still delivers one line.
        if card.workspace:
            launch = f"cd {card.workspace} && {launch}"
        # The invariant, asserted where it is made. If this ever fails, the bug is
        # here, not downstream — a settings-less string must be UNREACHABLE.
        assert "--settings" in launch, "compose produced a settings-less launch"
        return launch

    def start(self, card: Agent, pane: str) -> None:
        """The seam: compose (may refuse) THEN deliver via Panes. Panes stays
        runtime-blind — it only ever sees a finished string."""
        self._panes.send(pane, self.compose(card))

    def is_live(self, screen: str) -> bool:
        """Is the runtime OBSERVED live in this captured pane? Runtime-specific:
        each runtime knows its own ready signal. Claude Code's ready UI shows its
        version banner and a persistent "? for shortcuts" status line; a pane at a
        bare shell prompt, an error, or a first-run consent screen is NOT live.

        LIVE-FIRE CONFIRMED (aegis-zx7l, real claude v2.1.214): the READY_MARKERS
        were watched matching a real ready pane, and "Welcome to Claude Code" (the
        old guess) was watched NEVER appearing and removed. A consent screen is
        deliberately NOT live here — see waiting_for_human().
        """
        if any(bad in screen for bad in self.DEAD_MARKERS):
            return False                       # a failed launch is never live
        if self.waiting_for_human(screen):
            return False                       # blocked on consent — not up yet
        return any(mark in screen for mark in self.READY_MARKERS)

    def waiting_for_human(self, screen: str) -> bool:
        """A THIRD state between live and failed: a first-run prompt (e.g. the
        Chrome-extension consent) is up and blocking the ready UI. Neither "live"
        nor "crashed" — it needs a person. st new surfaces this specifically so a
        could-not-tell (2) reads as 'go answer the prompt', not 'it died'."""
        return any(c in screen for c in self.CONSENT_MARKERS)


class CodexRuntime:
    """Second implementation. NOT charity — the capability leak detector.

    codex is a real runtime we can host WORKERS on, but it does NOT declare
    blocking stop hooks (measured, adapters.md). So `role set X lead` on codex
    MUST refuse — and this second impl is what makes that refusal testable with a
    positive control: lead-on-claude passes, lead-on-codex refuses. A capability
    gate that only ever sees one runtime is a gate that has never been shown to
    open OR close.
    """

    name = "codex"

    def __init__(self, panes, resolve_settings: SettingsResolver) -> None:
        self._panes = panes
        self._resolve = resolve_settings

    def hooks(self, card: Agent) -> HookSpec:
        return HookSpec(blocking_stop=False)   # the capability it lacks

    def compose(self, card: Agent) -> str:
        require_capability(self, card)                 # refuses lead/administrator
        settings_path = self._resolve(card)
        if not settings_path:
            raise SettingsError(
                f"could not materialize settings for {card.name} "
                f"(role {card.role!r}); refusing to launch a settings-less agent."
            )
        launch = f"SHANTY_AGENT={card.name} codex --settings {settings_path}"
        assert "--settings" in launch, "compose produced a settings-less launch"
        return launch

    def start(self, card: Agent, pane: str) -> None:
        self._panes.send(pane, self.compose(card))
