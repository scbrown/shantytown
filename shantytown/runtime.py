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
import os
import json
import shutil
import subprocess
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


def asks_a_question(rt, screen: str) -> bool:
    """Does `rt` say a BLOCKING picker is up on this screen? (internal-ref)

    Tolerates a runtime that cannot answer. Pane-reading is an OPTIONAL capability
    here — CodexRuntime implements neither this nor shows_ready_ui — and the two
    consumers are `st crew` and the tend supervisor, so a hard AttributeError would
    crash a watchdog over a runtime that simply reads no panes.

    FALSE HERE MEANS "COULD NOT ASK", AND THAT IS SAFE ONLY BECAUSE OF WHERE IT
    LANDS. Normally collapsing "I could not look" into "there is nothing there" is
    the exact bug this codebase keeps paying for (None is not zero; `?` is not
    idle). It is sound in this one spot: the flag can only ever UPGRADE a `?` to
    `waiting`, so failing to ask leaves the verdict at `?` — the honest
    could-not-tell — instead of inventing one. It can never turn a real
    busy/idle/wedged into something else.
    """
    ask = getattr(rt, "awaiting_answer", None)
    return bool(ask(screen)) if callable(ask) else False


def auth_expired(rt, screen: str) -> bool:
    """Does `rt` say its LOGIN EXPIRED banner is on this screen? (internal-ref)

    The auth twin of asks_a_question, with the identical tolerance and the
    identical safety argument: a runtime that reads no panes (codex) cannot
    answer, and False-because-could-not-ask is safe ONLY because of where it
    lands — the flag can only ever convert some other verdict INTO `auth-dead`,
    so failing to ask leaves the verdict as it was. It can never manufacture
    auth-death, and it can never hide a busy/wedged agent behind it.
    """
    ask = getattr(rt, "auth_dead", None)
    return bool(ask(screen)) if callable(ask) else False


@runtime_checkable
class Runtime(Protocol):
    """An agent runtime does three things (adapters.md). start() is this ruling."""
    name: str
    def start(self, card: Agent, pane: str) -> None: ...   # compose + send
    def hooks(self, card: Agent) -> HookSpec: ...          # capability declaration


def require_capability(program, card: Agent) -> None:
    """Refuse a card whose ROLE needs a capability the launched PROGRAM lacks.

    `program` is the object that ACTUALLY runs the agent — the Harness the card
    selects (harness.for_card), not a hardcoded runtime. That distinction is the
    whole of internal-ref: the CLI only ever builds ClaudeRuntime (blocking_stop=
    True), so asking `self` rubber-stamped every card while the program that ran
    came from card.harness. Ask the harness and the gate sees what it is gating.

    This is the capability gate adapters.md sketches:
        role 'lead' requires on_report_stop delivery; a harness that does not
        declare blocking stop hooks -> malcolm stays worker, nothing written.
    Keyed on the DECLARED hooks(), never a name (adapters.md:86-87), so a third
    capable program passes without editing here — the declaration is the source of
    truth. Duck-typed on `.hooks()`/`.name`: a Harness satisfies it, and so does a
    self-contained runtime that is its own program (CodexRuntime, the test double).
    """
    if card.role in _ROLES_NEEDING_STOP and not program.hooks(card).blocking_stop:
        raise CapabilityError(
            f"harness {program.name!r} does not declare blocking stop hooks; "
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
# ...BUT "by construction" was an ASSUMPTION, and it is false in the case that
# matters. sys.executable is the interpreter that RAN THE EMITTER, which is only
# the interpreter that can import shantytown when the emitter was invoked through
# the installed entry point. Emit from a source checkout with the system python —
# `python3 -m shantytown.cli role set ...`, which is exactly how one regenerates
# settings while developing — and you bake in `/usr/bin/python3`, which cannot
# import shantytown at all.
#
# MEASURED on the live store, 2026-07-20: lead.settings.json carried
#     /usr/bin/python3 -m shantytown.stop_event send|drain
# and `/usr/bin/python3 -c "import shantytown"` is a ModuleNotFoundError. So the
# lead's hooks were dead — the identical silent outcome as the `python: not found`
# bug above, reintroduced through a different door, in the file the whole
# stop-event route depends on. The comment above asserted the property; nothing
# checked it.
#
# So CHECK it. A hook interpreter that cannot import the package is not a hook.
def _usable(py: str) -> bool:
    """Can this interpreter actually import shantytown? Asked, not assumed."""
    if not py:
        return False
    try:
        # cwd="/" ON PURPOSE. Python prepends the CWD to sys.path for -c, so
        # running this from a source checkout imports the LOCAL shantytown/ dir
        # and every interpreter looks usable — my first version of this check
        # returned True for /usr/bin/python3, which cannot import shantytown at
        # all, because I ran it from the worktree. The emitted hook executes in
        # the AGENT'S workspace, which has no shantytown/, so "/" is the honest
        # model of where it will actually run.
        return subprocess.run([py, "-c", "import shantytown"], cwd="/",
                              capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _hook_interpreter() -> str:
    """The interpreter to bake into emitted Stop hooks.

    Prefers the one running us (correct when invoked via the installed `st`), then
    the interpreter beside the installed console script — which is what a
    dev-shell invocation must fall back to, since the settings it writes are for
    the DEPLOYED agents, not for the shell that happened to emit them.

    RAISES rather than emitting a dead hook: this repo refuses settings-less
    launches for the same reason, and a hook that cannot start is indistinguishable
    from a tier with no hooks at all.
    """
    if _usable(sys.executable):
        return sys.executable
    st = shutil.which("st")
    if st:
        cand = str(Path(st).resolve().parent / "python")
        if _usable(cand):
            return cand
    raise SettingsError(
        "no interpreter available that can import shantytown — refusing to emit a "
        f"Stop hook that cannot run (tried {sys.executable!r} and the interpreter "
        "beside the installed `st`). Install shantytown, or run this through the "
        "installed entry point.")


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
    cmd = f"{_hook_interpreter()} -m shantytown.stop_event {mode}"
    if root is not None:
        cmd += f" --root {Path(root).resolve()}"
    return {"type": "command", "command": cmd}


def _feed_check_cmd(root=None) -> dict:
    """The administrator's Rule Zero feed-check Stop hook (internal-ref). Same shape
    and same baked-in --root as _stop_cmd, so it resolves the real store from the
    admin's own workspace. Fail-open lives inside the module, not here."""
    cmd = f"{_hook_interpreter()} -m shantytown.feed_check"
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


def settings_for_role(role: str, root=None, harness_name: str | None = None) -> dict:
    """The settings file a role needs, IN ITS HARNESS'S FORMAT — the CONTENT
    `role set` emits and `st new`'s launch reads via --settings (#6, arnold
    gt-wisp-w4j2af).

    This is now a THIN DISPATCH to harness.get(harness_name).settings(); the Claude
    Code schema below it is claude_settings_for_role, which is what ClaudeHarness
    returns. The indirection is the point of the harness split: the file format and
    the argv that reads it are one decision, and they now live in one class. The
    default is unchanged and is Claude Code, so every existing caller gets exactly
    what it got before.
    """
    from . import harness as harness_mod
    return harness_mod.get(harness_name).settings(role, root=root)


def claude_settings_for_role(role: str, root=None) -> dict:
    """The Claude Code settings.json a role needs. CLAUDE-CODE-SPECIFIC (its hooks
    schema) — owned by ClaudeHarness, which is the only thing that should call it.

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
        # send FIRST (the stop event persists whatever happens), then the HAUL
        # advance — the self-feed for a worker whose queue is already assigned
        # (anchor closed -> block-with-next; fail-open, self-terminating).
        stop = [_stop_cmd("send", root), _stop_cmd("haul", root)]
    elif role == "lead":
        stop = [_stop_cmd("send", root), _stop_cmd("drain", root)]
    elif role == "administrator":
        # The drain delivers received stop events; the feed-check is the Rule Zero
        # HARD GATE (internal-ref) — it BLOCKS the admin's own stop while free
        # feedable workers AND dispatchable beads coexist, so the coordinator
        # cannot go idle with the fleet idle. Fail-open, self-terminating when fed.
        stop = [_stop_cmd("drain", root), _feed_check_cmd(root)]
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
        "env": _settings_env(role, root),
    }


# Deployment-supplied environment for emitted settings. NOT a list of values —
# a list of NAMES to carry through, so no internal hostname ever lives in this
# repo (that is what the public scrub was for).
_CARRIED_ENV = ("QUIPU_SERVER", "SHANTY_ONTO_NS")


def _settings_env(role: str, root=None) -> dict:
    """The env block an emitted settings file carries.

    BOBBIN_ROLE, plus any deployment config this install was given. The scrub that
    made the graph's URL and namespace env-configurable did NOT teach the emitter
    to emit them, so the live values survived only in two hand-maintained settings
    files — and the next `role set` silently dropped them. That is exactly what
    happened: a lead.settings.json emitted today came out with no QUIPU_SERVER and
    no SHANTY_ONTO_NS, so that lead would launch pointed at the public default,
    which is a dead localhost and a namespace holding none of this crew's facts.

    The failure is quiet in the worst way. `QuipuRegistry.all()` RAISES on an
    unreachable graph rather than returning [], so the agent gets an honest
    "could not tell" — but a wrong-but-reachable namespace would answer "nobody
    exists" with a straight face. Carrying the config is what keeps that from
    being a coin flip.

    Source order: <root>/env.json (deployment config, gitignored), then the
    ambient environment. Absent both, the key is OMITTED and the agent falls back
    to the library default — never a placeholder written into a live settings file.
    """
    env = {"BOBBIN_ROLE": role}
    supplied: dict[str, str] = {}
    if root is not None:
        p = Path(root) / "env.json"
        try:
            loaded = json.loads(p.read_text())
            if isinstance(loaded, dict):
                supplied = {k: str(v) for k, v in loaded.items()}
        except (OSError, ValueError):
            supplied = {}
    for key in _CARRIED_ENV:
        val = supplied.get(key) or os.environ.get(key)
        if val:
            env[key] = val
    return env


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
    return stop_directions_in(Path(root) / "settings" / f"{role}.settings.json")


def stop_directions_in(path) -> set[str] | None:
    """Parse ONE settings file -> the stop directions it carries, or None.

    Factored out of emitted_stop_directions so the artifact reader and the LIVE
    reader (live_stop_directions) apply the identical parse. If they diverged,
    a mismatch between them would be unattributable: you could not tell a real
    runtime drift from two parsers disagreeing.
    """
    try:
        data = json.loads(Path(path).read_text())
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


def settings_path_in_cmdline(cmdline: str) -> str | None:
    """Pull the `--settings <path>` argument out of a launch command line."""
    if not cmdline:
        return None
    toks = cmdline.split()
    for i, t in enumerate(toks):
        if t == "--settings" and i + 1 < len(toks):
            return toks[i + 1]
        if t.startswith("--settings="):
            return t.split("=", 1)[1]
    return None


@dataclass(frozen=True)
class LiveWiring:
    """What a RUNNING process actually carries, and where it came from.

    settings_path is part of the finding, not decoration (dearing, internal-ref):
    a report that names only what is MISSING reads as "this agent has no hooks",
    and the reader reasonably concludes internal-ref — respawn dropped --settings,
    the rm -rf and force-push guards are gone. That is a real emergency and it
    was NOT what we measured. Naming the path says what the agent DOES have and
    makes the foreign launcher self-evident in the output.
    """
    directions: set[str]
    settings_path: str | None   # None = no --settings on the launch line at all


def live_stop_directions(pane: str, cmdline_reader) -> set[str] | None:
    """Back-compat shim: just the directions. Prefer live_wiring()."""
    w = live_wiring(pane, cmdline_reader)
    return None if w is None else w.directions


def live_wiring(pane: str, cmdline_reader) -> LiveWiring | None:
    """What stop directions is the process ACTUALLY RUNNING in `pane` carrying?

    THE GAP THIS CLOSES (internal-ref). emitted_stop_directions answers "does the
    ROLE's artifact carry drain?". That is not the question the tier needs. The
    tier needs "will this lead drain?", and the artifact cannot answer it,
    because nothing guarantees the live process was launched from that artifact.

    Measured on the live store, 2026-07-20: dearing is role=lead, lead.settings
    .json emits [send, drain], and `st roles --check` said `hooks: ok` — while
    the process in its pane had been launched by a FOREIGN launcher (gt-crew-up)
    with gastown settings carrying no stop_event hook at all. Seven workers
    routed to it; every one of their stop events was write-only, and the checker
    was green throughout.

    tmux.py already states this rule for the kill path: a pane NAME match must
    never be sufficient permission to reap. Same rule, liveness edition: a pane
    name match must never be sufficient evidence of DRAIN. `st` does not own
    every process that answers to a name it knows.

    None = CANNOT TELL (no such pane, unreadable cmdline, no --settings on it,
    unparseable settings). Never rendered as a pass — same contract as
    emitted_stop_directions.
    """
    cmdline = cmdline_reader(pane)
    if not cmdline:
        return None
    p = settings_path_in_cmdline(cmdline)
    if p is None:
        # A live process with no --settings at all is the hookless zombie this
        # repo refuses to compose. It is not "cannot tell" that it lacks hooks
        # — there is no settings file, so it carries no stop directions. That
        # is an EMPTY SET (a measurement), not None (a failure to measure).
        return LiveWiring(directions=set(), settings_path=None)
    d = stop_directions_in(p)
    if d is None:
        return None
    return LiveWiring(directions=d, settings_path=p)


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
    # THE FOLDER-TRUST GATE, measured 2026-07-20 and bigger than it looks: a
    # FRESH workspace makes Claude Code ask "Do you trust the files in this
    # folder?" and that dialog BLOCKS the ready UI until a human answers. It is
    # not covered by --dangerously-skip-permissions (verified: a card with
    # dangerous=True stalls on it exactly the same), and it is not the MCP
    # consent screen. So EVERY agent launched into a newly cloned directory sat
    # on this prompt, unusable, while `st new` reported could-not-tell — the
    # symptom recorded against harding's first launch, and the same one a
    # tend-respawn hits. A provisioning story that stops at files does not
    # produce a working agent.
    TRUST_MARKERS = ("Do you trust the files in this folder",
                     "1. Yes, I trust this folder")
    # An interactive picker BLOCKING the pane (internal-ref). See awaiting_answer().
    # Read off live panes 2026-07-20, both the unanswered and the answered shape.
    QUESTION_MARKERS = ("Enter to select", "Ready to submit your answers?")
    # AUTH EXPIRED (internal-ref). MEASURED, not guessed: read verbatim off 8 live
    # auth-dead crew panes, 2026-07-22 —
    #     ● Login expired · Please run /login
    # rendered at column 0 as the runtime's own response line, with the ready UI
    # still up and the input box empty underneath. That combination is exactly why
    # the state was invisible: every such pane read `idle`. The banner appears
    # when an API call FAILS — an agent whose auth expired while idle shows
    # nothing until something (a dispatch, a tend prompt) makes it try.
    # Line-anchored via auth_dead(), never a bare substring: this repo's own
    # source, the bead that asked for this, and any grep over a dead pane's
    # scrollback all CONTAIN the string (a `grep -n` hit renders as
    # "sess: 1484:● Login expired · …" — measured in the very session that wrote
    # this), and a substring match on chrome an agent can quote is the trap every
    # marker in this file documents.
    AUTH_MARKERS = ("● Login expired",)
    # Matched in the tail only — see awaiting_answer(). 8 lines, same window every
    # text predicate in triage.py uses; the answered shape sits ~5 lines up.
    _QUESTION_TAIL_LINES = 8

    def __init__(self, panes, resolve_settings: SettingsResolver, root=None) -> None:
        self._panes = panes
        self._resolve = resolve_settings
        self._root = root

    def hooks(self, card: Agent) -> HookSpec:
        # FORWARDS to the card's harness — the single source of truth for this
        # capability (internal-ref). ClaudeRuntime used to answer blocking_stop=True
        # for itself, which is exactly what let the gate rubber-stamp a card whose
        # harness was not claude. Forwarding keeps one literal declaration (on the
        # harness) and makes this answer honest for a card naming another program.
        from . import harness as harness_mod
        return harness_mod.for_card(card).hooks(card)

    def compose(self, card: Agent) -> str:
        """Build the launch string, or RAISE. Never returns a settings-less launch.

        Order matters. Resolve the harness the CARD names FIRST — an unknown one
        raises UnknownHarness, a clean refusal — then gate on ITS declared
        capability (internal-ref: the gate must ask the program that actually
        launches, not this hardcoded ClaudeRuntime), then materialize settings.
        Capability still precedes settings: a lead the program cannot host refuses
        before we bother writing anything.
        """
        # THE ARGV, THE SETTINGS FORMAT, AND THE CAPABILITY ARE ALL THE HARNESS'S
        # (harness.py). This method keeps only what is the RUNTIME's — the
        # settings-or-nothing invariant and the assert below — and asks the harness
        # the CARD names for everything program-specific. One resolve, so the gate
        # and the launch cannot disagree about which program this is.
        from . import harness as harness_mod
        program = harness_mod.for_card(card)           # UnknownHarness -> refuse
        require_capability(program, card)              # CapabilityError -> refuse
        settings_path = self._resolve(card)
        if not settings_path:
            raise SettingsError(
                f"could not materialize settings for {card.name} "
                f"(role {card.role!r}); refusing to launch a settings-less agent."
            )
        launch = program.launch(card, settings_path, root=self._root)
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

    def trust_prompt(self, screen: str) -> bool:
        """Is the folder-trust dialog up? Distinguished from waiting_for_human
        because this one the LAUNCHER may answer: the card already elected this
        workspace, so trusting it re-affirms a decision that is already made
        rather than making a new one. Every other first-run prompt still needs a
        person."""
        return any(m in screen for m in self.TRUST_MARKERS)

    def trust_answer(self) -> str:
        """The keystroke that accepts. Kept next to the markers so a runtime that
        renders a different dialog answers its own, and nobody hardcodes a "1"
        into the launcher."""
        return "1"

    def waiting_for_human(self, screen: str) -> bool:
        """A THIRD state between live and failed: a first-run prompt (e.g. the
        Chrome-extension consent) is up and blocking the ready UI. Neither "live"
        nor "crashed" — it needs a person. st new surfaces this specifically so a
        could-not-tell (2) reads as 'go answer the prompt', not 'it died'."""
        return any(c in screen for c in self.CONSENT_MARKERS)

    def awaiting_answer(self, screen: str) -> bool:
        """Is an interactive option-picker up and BLOCKING this pane? (internal-ref)

        MEASURED 2026-07-20 (sattler): 7 of 10 workers were sitting on pickers at
        once, every one of them printing `?` in `st crew` — which was honest and
        useless. `?` means "I could not tell"; it does not tell a coordinator that
        an agent is stalled on a question only they can answer. Two agents sat on
        ANSWERED pickers for over an hour because a by-hand pane sweep missed them.

        These strings are runtime chrome, so they live here beside CONSENT_MARKERS
        and not in triage — triage stays runtime-blind by construction, and a
        second runtime draws its pickers differently.

        Both shapes were read off live panes with `capture-pane -p -e`, not quoted
        from a doc:
          unanswered  "Enter to select · ↑/↓ to navigate · n to add notes · Esc…"  (lowery)
          answered    "Ready to submit your answers?" over "❯ 1. Submit answers"   (billy, kelly)
        The second matters as much as the first: an answered picker is still
        blocking, and it is the one that silently ate an hour.

        Deliberately broad. A permission prompt or any other blocking chooser is
        also "this pane is waiting on a person", which is exactly the fact the
        coordinator needs; narrowing it to AskUserQuestion would re-hide the rest.

        Caller must hand this the STRIPPED view: the runtime colours these footers
        per WORD, so `Enter to select` arrives as
        `\x1b[38;5;246mEnter\x1b[39m \x1b[38;5;246mto\x1b[39m …` and a substring
        match silently stops matching — the same trap documented on READY_MARKERS.

        TAIL-ONLY, and not as a micro-optimisation. A picker's chrome is a FOOTER;
        the same words further up are an agent TALKING about pickers, not sitting
        on one. That is not hypothetical here — the bead that asked for this
        predicate quotes the marker string verbatim, so any agent reading it would
        match on a whole-screen search and report itself stalled. Every text
        predicate in triage.py is tail-only for this reason, one of them after a
        healthy agent was classified wedged for printing a traceback.

        TRAILING BLANKS ARE DROPPED FIRST. A fixed window off the raw bottom is
        fragile: kelly's real pane carried five blank lines under the picker, which
        pushed "Ready to submit your answers?" out of an 8-line tail and read as `?`
        — the exact agent this predicate exists to catch, missed by padding. Blank
        padding is not content, so it does not get to spend the window.

        TRUST_MARKERS COUNT TOO. The folder-trust dialog is a different dialog
        with the same consequence — a blocking chooser, before the ready UI, that
        `st new` normally auto-answers. If one is still up when `st crew` looks,
        the launcher did not answer it and that agent is stopped dead waiting for
        a person. Reporting it as `?` would be the very bug this predicate closes,
        one dialog over.
        """
        lines = screen.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        tail = "\n".join(lines[-self._QUESTION_TAIL_LINES:])
        return any(m in tail for m in self.QUESTION_MARKERS + self.TRUST_MARKERS)

    def auth_dead(self, screen: str) -> bool:
        """Is this pane showing the runtime's LOGIN EXPIRED banner? (internal-ref)

        The state this names: the operator's shared credential expired (or was
        rotated by a re-login), so every API call this agent makes fails with
        `● Login expired · Please run /login` — while the ready UI stays up and
        the input box stays empty, which is `idle` to every other predicate.
        Measured 2026-07-22: one operator re-login left all 9 crew exactly like
        this; they were counted feedable, prompted, and dispatched into, and
        every send died against the same banner.

        Caller hands this the STRIPPED view, same contract as awaiting_answer:
        the runtime colours its chrome, and a substring under a colour run stops
        matching.

        TAIL-ONLY with trailing blanks dropped first (kelly's blank-padding
        lesson, awaiting_answer): the banner is the FAILED TURN's output, so on a
        live auth-dead pane it sits a few lines above the input box — measured at
        6 lines up with blanks dropped. The same words deeper in scrollback are
        history (possibly from an expiry already healed), or an agent TALKING
        about the banner — this predicate's own bead quotes it verbatim.

        LINE-ANCHORED, not a substring: only a line that BEGINS with the marker
        counts. The runtime renders the banner as its own response line at column
        0; a quoted copy almost never lands there — a `grep -n` over a dead
        pane's scrollback emits "sess: 1484:● Login expired · …", measured in the
        session that wrote this, and a substring match would have called the
        grepping agent auth-dead on the spot.
        """
        lines = screen.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        return any(ln.strip().startswith(m)
                   for ln in lines[-self._QUESTION_TAIL_LINES:]
                   for m in self.AUTH_MARKERS)


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
