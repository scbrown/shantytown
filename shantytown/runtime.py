"""runtime — the launcher seam. Claude Code first-class, swappable.

This is the SECOND HALF of the anti-handoff seam (arnold's #5 launch ruling). #5a gave Panes no handoff verb; this gives the launcher no
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
import json
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
# set / #6; #5 owns the launch SEAM and its invariant. The default
# resolver expects the role's settings file to already exist and refuses if not
# — that refusal IS the invariant working (no settings -> no launch).
SettingsResolver = Callable[[Agent], "str | None"]


# The internal entry the emitted Stop hooks call (arnold's #6 ruling). NOT an st
# subcommand — plumbing, so the command-count test never sees it.

# The interpreter RUNNING shantytown, never the bare name "python". Stock Ubuntu
# ships python3 with NO unversioned `python`, so the hardcoded name made EVERY
# emitted Stop hook die with `/bin/sh: 1: python: not found` — found in live use
# on the first real launch of the pilot. The hook failed on every
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
# 2026-07-19 (window, kelly): the "hank never exits 2" contract was
# FALSIFIED IN PRODUCTION and this line hard-blocked the whole fleet. Installed
# hank 0.1.0 implements only `post-edit`; `hank hook pre-edit` is a clap USAGE
# error, and clap exits 2 — Claude Code's one blocking code. Every Write/Edit by
# every shantytown worker was refused with:
#   PreToolUse:Write hook error: invalid value 'pre-edit' for '<EVENT>'
# The old fail-open test only ever ran with hank ABSENT (127), so it stayed green
# through the outage. Fail-open cannot be delegated to someone else's exit codes.
#
# The wrapper below launders the exit code WITHOUT the hazard the pinned contract
# warned about: stdout is captured, and it is echoed ONLY when hank exited 0. A
# crashed or stale hank therefore contributes exactly nothing to stdout, so no
# partial output can ever be read as a forged permission decision.
_HANK_GUARD = 'out=$(hank hook pre-edit) || exit 0; printf %s "$out"'


def _guard_hook() -> dict:
    """Emitted EXACTLY as hank pinned it (hank#20 contract, weaver 2026-07-19).

    Deliberately a bare command with no shell wrapper. An earlier version wrapped
    it in `command -v hank ... || exit 0` to force fail-open; that is both
    redundant and harmful under the pinned contract:

      - Exit 2 is Claude Code's ONLY blocking channel, and hank never exits 2.
        So no hank crash — and no missing binary (127) — can hard-block an agent.
        Fail-open is a property of the contract, not of a wrapper we bolt on.
      - ALLOW IS SILENCE: allow is exit 0 with EMPTY stdout. The guard must never
        emit permissionDecision:"allow", because that value SUPPRESSES the user's
        own permission prompt — a guard that emitted it would silently downgrade
        every agent's permission posture. The guard only ever SUBTRACTS permission.
        A `|| exit 0` wrapper risks passing through partial stdout from a crashed
        hank, which is exactly the thing that must never be forged.

    DENY is exit 0 + hookSpecificOutput{permissionDecision:"deny", ...}.
    """
    return {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{"type": "command", "command": _HANK_GUARD, "timeout": 5}],
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
        # BOBBIN_ROLE in the SETTINGS env, per hank's shipped spec — not only as a
        # launch-string export. The launch export sets it for the agent PROCESS;
        # a hook is re-exec'd by the harness, and settings.env is what the shipped
        # contract names as the place hank reads its tenant from. Without it the
        # guard resolves no scope and decides nothing — running, wired, and inert,
        # which is the failure mode this repo keeps naming.
        "env": {"BOBBIN_ROLE": role},
    }


def emitted_stop_directions(root, role: str) -> set[str] | None:
    """READ BACK which stop directions a role's EMITTED settings file actually
    carries: a subset of {"send", "drain"}, or None if it could not be read.

    settings_for_role WRITES this artifact; this READS it. They are deliberately
    separate — a check that asks the writer what it would write proves nothing
    about what is on disk, which is the whole complaint in GitHub #6: `roles
    --check` printed "hooks: ok" for every agent while never opening a hook file.

    None is NOT an empty set. Missing file / unparseable JSON means CANNOT TELL,
    and the caller must not render that as a pass — a role whose hooks we failed
    to read is not a role with no hooks.
    """
    p = Path(root) / "settings" / f"{role}.settings.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    found: set[str] = set()
    try:
        for block in data["hooks"]["Stop"]:
            for hook in block["hooks"]:
                cmd = hook.get("command", "")
                if "shantytown.stop_event" not in cmd:
                    continue
                for mode in ("send", "drain"):
                    # Match the token, not a substring: "send" must be the
                    # stop_event subcommand, not a stray word in a path.
                    if mode in cmd.split():
                        found.add(mode)
    except (KeyError, TypeError, AttributeError):
        # The file exists but is not shaped like settings we emitted. That is a
        # cannot-tell, not "no hooks" — see above.
        return None
    return found


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
    # CONFIRMED by live-fire against real claude v2.1.214 (probe,
    # 2026-07-18): the ready UI carries the version banner "Claude Code v" AND the
    # persistent status line "? for shortcuts". The earlier "Welcome to Claude
    # Code" was a GUESS and never appears — a marker never observed passing is not
    # a marker (my validate-the-instrument rule); it is now replaced with two that
    # were watched to match a real ready pane.
    # "shift+tab to cycle" added 2026-07-20, and it is not a
    # widening — it is a MISS the earlier measurement could not see. Swept all 9
    # live crew panes on this fleet: ZERO carried "? for shortcuts". Every one
    # showed the mode line instead —
    #     ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
    # — because that line REPLACES the shortcuts hint whenever a permission mode
    # is on, and every crew agent here runs with one. So the pinned markers
    # matched nothing in production: `st new` could only ever return
    # could-not-tell (2) for a launch that was fine, and the work column read `?`
    # for eight genuinely idle agents. The lesson from the line above it applies
    # to itself: a marker validated once, on one configuration, is evidence about
    # that configuration. Re-swept, both are kept — "? for shortcuts" is still
    # what a default-mode pane shows.
    READY_MARKERS = ("? for shortcuts", "Claude Code v", "shift+tab to cycle")
    # Definitely-not-live signals — a failed launch, seen as loudly as possible.
    DEAD_MARKERS = ("command not found", "no such file", "not found", "Traceback")
    # A first-run consent screen (e.g. "Claude in Chrome extension detected") is a
    # THIRD state: not live, not failed — WAITING FOR A HUMAN. It blocks the ready
    # UI, so is_live correctly returns False and st new reports could-not-tell (2).
    # The real fix is to launch past it (a settings/config that pre-answers), which
    # is entangled with what role-set emits — tracked separately, not guessed here.
    CONSENT_MARKERS = ("Claude in Chrome extension detected", "keep browser tools off")

    def __init__(self, panes, resolve_settings: SettingsResolver, root=None) -> None:
        self._panes = panes
        self._resolve = resolve_settings
        self._root = root

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
        # Live-fire confirmed: `claude --no-chrome` goes straight to
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
        # card; nobody else inherits it (the pilot).
        if card.dangerous:
            flags += " --dangerously-skip-permissions"
        # BOBBIN_ROLE is how hank's policy guard resolves WHICH scope applies
        # (hank#20: tenant is resolved --tenant, then BOBBIN_ROLE; scopes live in
        # .bobbin/config.toml under [hank.policy.scopes.<role>]). Exporting it per
        # agent is what lets ONE hook registration serve every role — without it
        # the guard has no scope to enforce and every agent is ungoverned.
        # SHANTY_ROOT is the BELT to --settings' braces, and it exists because of a
        # measured incident (sattler 2026-07-19). --settings is read ONCE,
        # at launch: when the Stop hook was later corrected on disk to carry an
        # absolute --root (c3fb472), every ALREADY-RUNNING agent kept the old unrooted
        # command forever. kelly's own pane showed it —
        #     stop_event send: no such agent: kelly (looked in
        #     <agent-workspace>/.shanty/crew/<agent>.json)
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
        # settings file detectable — items 1-2 (a doctor check, and role-set
        # naming the live agents it did NOT reach) are still open, and this must not be
        # mistaken for them.
        root_env = f"SHANTY_ROOT={Path(self._root).resolve()} " if self._root else ""
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

        LIVE-FIRE CONFIRMED (real claude v2.1.214): the READY_MARKERS
        were watched matching a real ready pane, and "Welcome to Claude Code" (the
        old guess) was watched NEVER appearing and removed. A consent screen is
        deliberately NOT live here — see waiting_for_human().
        """
        if any(bad in screen for bad in self.DEAD_MARKERS):
            return False                       # a failed launch is never live
        if self.waiting_for_human(screen):
            return False                       # blocked on consent — not up yet
        return self.shows_ready_ui(screen)

    def shows_ready_ui(self, screen: str) -> bool:
        """The POSITIVE half of is_live, on its own: is this runtime's UI on the
        screen at all? Separated out for triage.work_state, which
        must NOT use is_live: is_live also fails on DEAD_MARKERS, and one of those
        is "Traceback" — which healthy agents print constantly. Asking is_live
        "can I dispatch to this agent" would answer no for a free agent that had
        just run a failing test. Runtime-specific by construction: a second
        runtime knows its own ready markers, and triage knows none of them."""
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
