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

THE SECOND HARNESS LANDED (codex), and it moved the seam. `launch()` +
`settings()` was enough while Claude Code was the only program, because the rest
of st quietly agreed with it: the emitter wrote `<role>.settings.json` and JSON
bytes, the compose invariant asserted the literal string `--settings`, the live
reader grepped a cmdline for `--settings`, and the artifact reader parsed Claude
Code's hook schema. Four more places that were Claude Code wearing a generic
name. codex has NO settings flag at all — it reads config.toml out of
$CODEX_HOME — so every one of those four would have quietly answered for the
wrong program. They are now the harness's too:

    settings_name()/agent_settings_name()   WHAT the artifact is called
    render()                                WHAT BYTES it holds (json / toml)
    carries_settings()                      the compose invariant, in ITS argv
    settings_in_cmdline()                   finding it on a RUNNING process
    read_stop_directions()                  reading the routing back off disk
    provision()                             anything the file alone cannot do

The rule the codex work should be judged against, and the reason for the reading
list in codex.py: a guess about somebody else's CLI flags is exactly the kind of
thing that looks shipped and has never run (this repo's `python` vs `python3`,
the unmeasured ready markers, "Welcome to Claude Code"). Every codex fact here
was read out of codex's own source, with the file named beside it.

WHAT IS STILL CLAUDE-SHAPED, named rather than pretended away: the PANE-READING
predicates (READY_MARKERS, TRUST_MARKERS, CONSENT_MARKERS, the auth-dead banner)
live on ClaudeRuntime and are matched against a captured pane. They are the same
KIND of per-program fact as the argv — but a marker is a claim about a UI's
literal text, and this repo's own rule (runtime.READY_MARKERS' comment) is that a
marker never observed passing is not a marker. There is no codex on this host to
watch, so none are written. The honest consequence: `st new` on a codex card
verifies liveness with Claude Code's markers, matches nothing, and reports
could-not-tell (2) for an agent that may be fine. That is a WRONG-BUT-LOUD
answer, which is the one this repo prefers to a confident wrong one.

A card with no `harness` field means "claude" — every card that never said
otherwise, and the default is the answer for an UNSET field, never a fallback for
an unrecognised one.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
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


class Unsupported(Exception):
    """A card asks for something ITS harness's program cannot express.

    The sibling of UnknownHarness, one level in: the program is one we host, but
    the card sets a field it has no way to honour (`chrome` on codex — there is
    no browser integration to turn on or off). Dropping the field silently would
    launch an agent that reads as configured and is not, which is the same class
    of failure as substituting a different program: it succeeds at being the
    wrong thing. Raised at COMPOSE time, so nothing is launched."""


@dataclass(frozen=True)
class Usage:
    """Absolute token totals from one transcript; absence is ``None``, not zero."""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0


def _count(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


@runtime_checkable
class Harness(Protocol):
    """One agent program, and everything that is specific to it."""
    name: str

    def launch(self, card: Agent, settings_path: str, root=None) -> str:
        """The full command line for this card. MUST reference settings_path —
        the launcher's invariant (runtime.py) is that a composed launch always
        carries its settings or is not composed at all. HOW it references it is
        the harness's: a flag for Claude Code, an env export for codex, which is
        why the invariant is checked by carries_settings() below rather than by
        the launcher grepping for a flag name it invented."""
        ...

    def settings(self, role: str, root=None) -> dict:
        """The CONTENT of the settings file `launch` points at, for a ROLE. This
        is the file format half — Claude Code's hooks schema is Claude Code's,
        and a second harness emits its own."""
        ...

    def settings_name(self, role: str) -> str:
        """The artifact's path RELATIVE TO <root>/settings — name and extension
        both, because a harness that writes TOML must not be handed a `.json`
        filename by a caller that assumed. May contain directories."""
        ...

    def agent_settings_name(self, agent: str) -> str:
        """Same, for the PER-AGENT override that wins over the role's file
        (GitHub #17). Two names rather than one parameterised one because they
        are two different questions and a harness may answer them differently."""
        ...

    def render(self, settings: dict, existing: str = "") -> str:
        """The BYTES to write, given what is already on disk.

        Serialization AND merge together, because they are one decision: st owns
        the hook events it emits and replaces those wholesale (a stale stop
        direction surviving a rewrite is exactly the drift `role set` exists to
        remove); everything else in the file is the operator's and survives. A
        harness that could serialize but not merge would force the caller to
        parse a format it does not know."""
        ...

    def read_stop_directions(self, text: str) -> "set[str] | None":
        """The stop directions an emitted artifact carries — a subset of
        {"send", "drain"} — or None for CANNOT TELL.

        None is NOT an empty set, and every implementation owes that contract: a
        file we could not parse is not a file with no hooks. The reader is the
        harness's because the format is."""
        ...

    def read_usage(self, session_path: str | Path) -> Usage | None:
        """Read absolute session token totals, or UNKNOWN when no record exists.

        This belongs to the harness: Claude records deltas per assistant
        message, while Codex records cumulative snapshots.  A generic parser
        would either miss one or multiply the other (aegis-8c1cv).
        """
    def read_bash_guard(self, text: str) -> "str | None":
        """The deployment Bash guard COMMAND an emitted artifact carries, "" for
        none, or None for CANNOT TELL.

        THREE STATES, NOT TWO, and the middle one is the whole reason this
        exists (aegis-610jv). Before it, `roles --check` reported `hooks: ok` on
        the stop routing alone, so a codex agent with NO bd-store-guard and NO
        crew-only-guard checked out green — the surface whose job is catching
        unwired governance could not see this guard at all. "" says READ IT,
        THERE IS NONE; None says COULD NOT READ. Rendering None as "" would be
        a false clear, which is the failure this repo keeps paying for.

        It must read the MATCHER and not merely the presence of a PreToolUse
        block: a group scoped to another tool name leaves Bash unguarded, and a
        presence-only reader would call that wired — aegis-ac5x's own defect,
        committed by the checker built to catch it."""
        ...

    # The ENVIRONMENT VARIABLE this program's settings ride on, or None for a
    # program that takes a flag instead.
    #
    # It is declared because ARGV IS NOT THE LAUNCH LINE (internal-ref).
    # `CODEX_HOME=<dir> codex …` is a SHELL ASSIGNMENT: the shell consumes it into
    # the child's environment and it never appears in argv. So a reader that
    # scrapes a live process's command line sees `node …/codex --model …` with no
    # pointer in it at all, and concludes the agent carries no hooks — the
    # hookless-zombie verdict, delivered against a perfectly wired agent.
    #
    # Naming the var HERE rather than in the process reader is the point: the
    # reader must not have to know which harness it is looking at, or it becomes
    # the thing it is checking — one program's mechanism, applied to everybody.
    settings_env_var: "str | None"

    # BLOCKING-PICKER CHROME for this program — the footer text that says "this
    # pane is stopped, waiting for a person" (see runtime.awaiting_answer).
    #
    # EMPTY IS A LEGITIMATE VALUE and means NOBODY HAS WATCHED ONE, never "this
    # program has no pickers". A marker never observed matching is not a marker
    # (READY_MARKERS' rule); the honest state for an unwatched program is a loud
    # could-not-tell, not a confident guess.
    #
    # Matched TAIL-ONLY by the caller, which is what makes it safe for these to
    # be ordinary English: a picker's chrome is a FOOTER, and the same words
    # further up a pane are an agent TALKING about pickers, not sitting on one.
    picker_markers: "tuple[str, ...]"

    # Measured persistent chrome that proves this program's ready UI is present.
    # Patterns are matched against the pane TAIL by runtime.shows_ready_ui; an
    # empty tuple means unmeasured, never "this program has no ready UI".
    ready_patterns: "tuple[str, ...]"

    # CHROME THAT MEANS "WHAT I JUST SENT IS STILL IN THE INPUT BOX, UNSUBMITTED".
    #
    # A send is two facts, and st has always reported the first as if it were the
    # second: the KEYSTROKES went to the pane, and the message was SUBMITTED. On a
    # TUI that absorbs a large write as a paste, the first is true and the second
    # is false — the body sits in the box, the agent never sees it, and the sender
    # is told it was delivered. Same false-success family as the other checks this
    # fleet has paid for: the tool reported exactly what it did, and what it did
    # was not what the caller asked.
    #
    # Empty means NOBODY HAS MEASURED THIS PROGRAM'S SIGNATURE — never "this
    # program always submits". Matched tail-only by the caller.
    stranded_markers: "tuple[str, ...]"

    def settings_in_cmdline(self, cmdline: str) -> "str | None":
        """The settings artifact a RUNNING process was launched with, read off
        its command line — or None if this launch is not one of ours. This is
        what makes the foreign-launcher check (runtime.live_wiring) work for a
        program whose settings ride an env export instead of a flag.

        `cmdline` here means the RECONSTRUCTED launch line — argv with the
        harnesses' settings_env_var assignments folded back in (tmux.cmdline) —
        precisely because argv alone cannot answer this for an env-pointer
        program."""
        ...

    def carries_settings(self, launch: str, settings_path: str) -> bool:
        """Does this composed launch actually point at that artifact? The
        compose invariant, asked of the program that owns the syntax."""
        ...

    def provision(self, settings_path: str, root=None,
                  workspaces=()) -> "list[str]":
        """Anything the artifact alone cannot do, run after it is written.
        Returns human-readable notes for the operator (empty is the normal
        answer). It exists because codex needs one — its home directory holds
        credentials as well as config — and inventing that seam at the call site
        would have made the emitter know which harness it was serving.

        `workspaces` are the workspace directories of the cards this artifact was
        just written for. codex needs them because it gates on a per-(home,
        directory) TRUST DIALOG, and an agent launched into that dialog is not
        running at all (see codex.trust_projects). A harness that does not gate
        on the directory ignores them."""
        ...

    def hooks(self, card: Agent) -> "HookSpec":
        """The CAPABILITY declaration the gate keys on: can the program this
        harness launches deliver a blocking stop hook to the MODEL?

        It lives HERE, on the harness, because it is a property of the PROGRAM —
        and the program is what the card selects (for_card), NOT the Runtime the
        CLI happens to construct. That mismatch was the whole of aegis-85ox: the
        gate asked a hardcoded ClaudeRuntime while the launched program came from
        card.harness. A capability declared on the object the card cannot pick is
        a gate that cannot see what it is gating."""
        ...


class ClaudeHarness:
    """Claude Code. First-class, and — for now — the only one.

    Both halves moved here verbatim from runtime.py; every comment explaining WHY
    a flag or a hook is there moved with it, because those comments are measured
    incidents (the --no-chrome consent screen, the SHANTY_ROOT belt, the yupana
    guard's fail-open) and separating a rule from its reason is how the rule gets
    "cleaned up" by the next reader.
    """

    name = "claude"
    # A FLAG, not an export — so there is nothing to recover from the environment
    # and the reconstructed launch line is just argv.
    settings_env_var = None
    # Claude Code's picker chrome is NOT repeated here. It lives on ClaudeRuntime
    # (QUESTION_MARKERS / TRUST_MARKERS), where it is also used by the consent and
    # trust auto-answer paths, and awaiting_answer already checks it directly.
    # Copying it across would create a second definition of the same strings, and
    # the failure mode of a rotted marker is silence — CONSENT_MARKERS rotted once
    # already and the test that caught it exists for that reason.
    picker_markers = ()
    ready_patterns = ()
    # Claude Code submits what st types; no stranded-paste signature has been
    # observed. Empty means UNMEASURED, not "cannot happen" — if a long send is
    # ever seen sitting in its box, the marker goes here and the check below
    # starts covering it with no other change.
    stranded_markers = ()

    def launch(self, card: Agent, settings_path: str, root=None) -> str:
        # --no-chrome: crew agents do not use the Chrome integration, and WITHOUT
        # this a first-run claude stops at a "Claude in Chrome extension detected"
        # consent prompt that BLOCKS the ready UI — so st new's verify never sees
        # live and returns could-not-tell (2) for an agent that would be fine.
        # Live-fire confirmed (aegis-84z1): `claude --no-chrome` goes straight to
        # the ready UI, is_live True. This is the prod 0-path fix.
        # Remote Control ON BY DEFAULT (Stiwi 2026-07-19). A fleet you cannot reach
        # is a fleet you cannot run: this session sat unreachable for a day with an
        # unsubmitted prompt in its input line and no way to drive it from outside
        # (the gastown weaver stall). Naming the session after the agent is what
        # makes a 6-agent fleet addressable rather than a wall of anonymous panes.
        # Default, not opt-in — an agent you forgot to enable it on is exactly the
        # one you will need to reach.
        # OPT-IN PER CARD (aegis-neffw, Stiwi asked for the capability). The
        # DEFAULT stays --no-chrome, so every agent that does not ask for a
        # browser keeps the aegis-84z1 fix: flipping this globally re-breaks
        # `st new`'s liveness verify fleet-wide, which is the exact 0-path
        # failure 84z1 was filed to repair. `--chrome` is one agent's decision on
        # one card, never a default anybody inherits.
        chrome = "--chrome" if card.chrome else "--no-chrome"
        flags = f"{chrome} --remote-control {card.name}"
        # --dangerously-skip-permissions is OPT-IN per agent (card.dangerous), never
        # global — a crew worker that must act without prompts sets it on its own
        # card; nobody else inherits it (the pilot, aegis-qdal.5).
        if card.dangerous:
            flags += " --dangerously-skip-permissions"
        # HONOUR THE CARD'S MODEL AT LAUNCH (GitHub #17, the other half of #9).
        # The field was persisted so a restart would not silently revert to the
        # default — and then the launcher never read it, so it reverted anyway.
        # A card that names a model and an agent that ignores it is worse than no
        # field: it reads as configured. Now resolved card -> role -> fleet
        # (resolve_model), so a deployment can set one model for a whole tier
        # without stamping it onto every card — and a card still beats both.
        model = resolve_model(card, root)
        if model:
            flags += f" --model {model}"
        # BOBBIN_ROLE is how yupana's policy guard resolves WHICH scope applies
        # (yupana#20: tenant is resolved --tenant, then BOBBIN_ROLE; scopes live
        # in .bobbin/config.toml under [yupana.policy.scopes.<role>] — the
        # section was [hank.policy.*] before the v0.6.0 rename, and a config
        # under the old section name projects NO scopes rather than erroring).
        # Exporting it per
        # agent is what lets ONE hook registration serve every role — without it
        # the guard has no scope to enforce and every agent is ungoverned.
        # SHANTY_ROOT is the BELT to --settings' braces, and it exists because of a
        # measured incident (aegis-nipg, sattler 2026-07-19). --settings is read ONCE,
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
        # settings file DETECTABLE, and must not be mistaken for the parts that do:
        # nipg items 1-2 are the launch stamp (launched.py, surfaced as the `settings`
        # column in `st crew`) and `role set` naming the live agents its rewrite did
        # NOT reach (_report_who_the_rewrite_did_not_reach). Both have since landed —
        # all three legs of that bead are closed. This one is the belt, not the detector.
        root_env = f"SHANTY_ROOT={Path(root).resolve()} " if root else ""
        # BEADS_ACTOR is WHO the tracker records for a create/close/reassign
        # (GitHub #24). Without it every agent's bd events are written as $USER —
        # `ubuntu` for the whole fleet — so an assignment that flipped between two
        # agents could not say who did it. The card already knows the name; the
        # audit trail just never got told.
        # THE ROLE SET, CARRIED (GitHub #37). st passes it through OPAQUELY: no
        # trait logic here, and none anywhere on the launch path. That is the
        # architectural line the whole trait model rests on — quipu DESCRIBES a
        # role, st CARRIES the set, the admin INTERPRETS it. The moment st starts
        # deciding what a role set MEANS in order to launch it, the closed enum
        # grows back one layer down, in the place hardest to see.
        #
        # ST_ROLES is emitted even when the set is just the tree position, so an
        # agent's own view of itself does not depend on whether its card has been
        # migrated yet. ST_REPORTS_TO/ST_ROLE_DOMAIN are omitted when absent rather
        # than emitted empty: an empty env var reads as a declared empty answer.
        st_roles = f"ST_ROLES={','.join(card.effective_roles())} "
        st_domain = f"ST_ROLE_DOMAIN={card.domain} " if card.domain else ""
        st_reports = f"ST_REPORTS_TO={card.reports_to} " if card.reports_to else ""
        launch = (
            f"{root_env}SHANTY_AGENT={card.name} BOBBIN_ROLE={card.role} "
            f"BEADS_ACTOR={card.name} {st_roles}{st_domain}{st_reports}"
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

    def settings_name(self, role: str) -> str:
        return f"{role}.settings.json"

    def agent_settings_name(self, agent: str) -> str:
        # PER-AGENT FIRST (GitHub #17). All workers sharing one file meant nothing
        # could differ per agent — so a card's own model, permissions or hooks had
        # nowhere to land.
        return f"agent-{agent}.settings.json"

    def render(self, settings: dict, existing: str = "") -> str:
        # indent=2, sort_keys=True and NO trailing newline: the bytes nine live
        # agents' hook files are already written in. This moved here from
        # cli._emit_role_settings unchanged, and changing it would rewrite every
        # settings file in every store on the next `role set` for no reason.
        return json.dumps(merge_one_level(_load_json(existing), settings),
                          indent=2, sort_keys=True)

    def read_stop_directions(self, text: str) -> "set[str] | None":
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return None
        found: set[str] = set()
        try:
            for block in data["hooks"]["Stop"]:
                for hook in block["hooks"]:
                    cmd = hook.get("command", "")
                    # The unified entry PROVIDES the drain direction (rank 4
                    # delivers through the same stop_event drain). Without this
                    # line every administrator on the new chain reads as DEAF to
                    # `roles --check` and `st tend` — a checker that cannot see
                    # the thing it is checking for is the exact defect those
                    # surfaces exist to catch.
                    if "shantytown.stop_policy" in cmd:
                        found.add("drain")
                        continue
                    if "shantytown.stop_event" not in cmd:
                        continue
                    for mode in ("send", "drain"):
                        # Match the token, not a substring: "send" must be the
                        # stop_event subcommand, not a stray word in a path.
                        if mode in cmd.split():
                            found.add(mode)
        except (KeyError, TypeError, AttributeError):
            # The file exists but is not shaped like settings we emitted. That is
            # a cannot-tell, not "no hooks" — see the protocol's contract.
            return None
        return found

    def read_usage(self, session_path: str | Path) -> Usage | None:
        """Claude stores a usage delta on each assistant message; sum deltas."""
        inp = cached = written = out = reasoning = 0
        seen = False
        try:
            with Path(session_path).open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        usage = (json.loads(line).get("message") or {}).get("usage") or {}
                    except (ValueError, AttributeError):
                        continue
                    if not isinstance(usage, dict) or not usage:
                        continue
                    seen = True
                    inp += _count(usage.get("input_tokens"))
                    cached += _count(usage.get("cache_read_input_tokens"))
                    written += _count(usage.get("cache_creation_input_tokens"))
                    out += _count(usage.get("output_tokens"))
                    reasoning += _count(usage.get("reasoning_output_tokens"))
        except OSError:
            return None
        if not seen:
            return None
        return Usage(inp, cached, written, out, reasoning,
                     inp + cached + written + out + reasoning)
    def read_bash_guard(self, text: str) -> "str | None":
        from .runtime import BASH_MATCHER
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return None
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return None
        groups = hooks.get("PreToolUse")
        if not isinstance(groups, list):
            # Readable settings of ours, carrying no PreToolUse at all: an
            # observation ("" = no guard), not a failure to look (None).
            return "" if ("Stop" in hooks or "SessionStart" in hooks) else None
        from .runtime import is_trace_command
        try:
            for group in groups:
                if group.get("matcher") != BASH_MATCHER:
                    continue
                for hook in group["hooks"]:
                    cmd = hook.get("command", "")
                    # yupana's action trace shares this matcher and is NOT a
                    # guard: it never denies. Returning it here would report
                    # coverage to `roles --check` for a deployment that
                    # configured none — a false clear of exactly the kind the
                    # three-state contract above exists to prevent.
                    if cmd and not is_trace_command(cmd):
                        return cmd
        except (KeyError, TypeError, AttributeError):
            return None
        return ""

    def settings_in_cmdline(self, cmdline: str) -> "str | None":
        toks = cmdline.split()
        for i, t in enumerate(toks):
            if t == "--settings" and i + 1 < len(toks):
                return toks[i + 1]
            if t.startswith("--settings="):
                return t.split("=", 1)[1]
        return None

    def carries_settings(self, launch: str, settings_path: str) -> bool:
        return "--settings" in launch

    def provision(self, settings_path: str, root=None,
                  workspaces=()) -> list[str]:
        # Nothing. Claude Code reads the file the flag names and needs no second
        # artifact beside it — the workspace-level wiring (.mcp.json, the consent
        # settings) is provision.py's job and predates the harness split.
        # `workspaces` is ignored: Claude Code's folder-trust dialog is answered
        # at LAUNCH by the launcher (TRUST_MARKERS), not recorded in the settings
        # file, so there is nothing to write here.
        return []

    def hooks(self, card: Agent) -> "HookSpec":
        # Claude Code delivers blocking stop hooks — measured, load-bearing: a
        # lead/administrator's reports' stop events reach the MODEL via a blocking
        # Stop hook's `reason` (a non-blocking hook's stdout is discarded). This is
        # the SINGLE literal declaration of the capability now; ClaudeRuntime.hooks
        # forwards here rather than restating it, so the two cannot drift apart
        # (which is how the gate came to rubber-stamp a non-claude card, aegis-85ox).
        # Call-time import, same one-directional reason as settings() above.
        from .runtime import HookSpec
        return HookSpec(blocking_stop=True)


class CodexHarness:
    """OpenAI's Codex CLI. The second implementation — and, per docs/adapters.md,
    the thing that proves the first did not leak.

    EVERY FACT ABOUT CODEX IS IN codex.py, WITH ITS SOURCE FILE NAMED. Read that
    docstring before changing anything here; the four numbered facts are what
    this class is made of, and each one was read out of openai/codex `main` on
    2026-08-06 because there is no codex on the host that wrote it.

    The shape of the difference, which is the whole reason the seam widened:
    codex has no `--settings`. It reads config.toml from its home directory, so
    the launch points at the artifact by exporting CODEX_HOME to the directory
    holding it. Everything else — the stop routing, the roles, the capability
    gate — is unchanged, because none of it was ever Claude Code's.
    """

    name = "codex"

    # MEASURED off a live codex pane on this host, 2026-08-06, with
    # `tmux capture-pane -p` — not read from source and not guessed. This is the
    # gap the module docstring above names ("there is no codex on this host to
    # watch, so none are written"); there is one now, and it was watched.
    #
    #   /model picker, verbatim:
    #       Select Model and Effort
    #       Access legacy models by running codex -m <model_name> or in your …
    #     › 1. gpt-5.6-sol (current)  Latest frontier agentic coding model.
    #       …
    #       Press enter to confirm or esc to go back
    #
    #   directory-trust dialog, verbatim:
    #       Do you trust the contents of this directory? …
    #     › 1. Yes, continue
    #       2. No, quit
    #       Press enter to continue
    #
    # Note the picker marks its SELECTED OPTION with `›` — codex's analogue of the
    # `❯` that made an answered picker read as TYPED input on a claude pane. Same
    # trap, different glyph.
    #
    # WHY THE FOOTER AND NOT THE TITLE: "Select Model and Effort" is one picker;
    # the footer is the widget's, so it covers pickers nobody has enumerated. The
    # attribute capture shows the footer dimmed as ONE run (`ESC[2m` at the start
    # of the line) rather than coloured per word, so unlike Claude Code's footers
    # it survives stripping intact — checked, because a per-word-coloured marker
    # silently stops matching and that trap is documented on READY_MARKERS.
    #
    # ⚠ THE RATE-LIMIT / MODEL-SWITCH PROMPT IS **NOT** IN THIS LIST AS A MEASURED
    # FACT. It could not be reproduced without exhausting the account's quota. It
    # is very likely the same picker widget and therefore already covered by the
    # footer above — but that is INFERRED, and this file's whole rule is that an
    # unobserved marker is not a marker. To settle it, capture a pane the moment
    # one appears and add what is actually on it.
    picker_markers = ("Press enter to confirm or esc to go back",
                      "Do you trust the contents of this directory",
                      "1. Yes, continue")

    # MEASURED on live idle AND busy Codex 0.146.1 panes. The model/status/cwd
    # line persists in both states and is absent from the directory-trust picker.
    # The prompt glyph alone is deliberately not used: the picker draws it too.
    ready_patterns = (
        r"(?m)^\s*gpt-\S+\s+\S+\s+[·.]\s+(?:~|/).+\s*$",
    )

    # MEASURED on this host: a single `send-keys -l` of >1000 chars is absorbed
    # as a paste and rendered in the input box as `[Pasted Content N chars]`,
    # which the trailing Enter does NOT commit. The count varies with the body,
    # so the marker is the stable prefix.
    stranded_markers = ("[Pasted Content",)

    @property
    def settings_env_var(self) -> str:
        # codex offers no --settings flag (codex.py fact 1), so the pointer is an
        # export, and this is the ONLY place a process reader can recover it from.
        # A property, not a class attribute, so the NAME has one definition:
        # codex.HOME_VAR. A second copy here would be a constant that can drift
        # from the launcher that writes it, and the drift would present as an
        # agent silently reported hookless — the defect this exists to fix.
        return codex_mod().HOME_VAR

    def launch(self, card: Agent, settings_path: str, root=None) -> str:
        # A card asking for a browser gets a REFUSAL, not a dropped flag. codex
        # has no chrome integration; `--no-chrome`/`--chrome` are Claude Code's
        # answer to a Claude Code consent screen. Honouring the field by ignoring
        # it would launch an agent whose card says browser and whose process has
        # none — configured on paper, absent in fact.
        if card.chrome:
            raise Unsupported(
                f"card {card.name!r} sets chrome=True, and harness 'codex' has no "
                f"browser integration to enable. Refusing to launch an agent whose "
                f"card claims a capability its program does not have."
            )
        # ABSOLUTE, always. The launch `cd`s into the workspace before the env
        # takes effect, so a relative CODEX_HOME (what `st --root .shanty …`
        # resolves to) would name a directory relative to the AGENT's cwd — and
        # codex does not fail on a home that is not there, it quietly uses an
        # empty one: no hooks, no auth, an agent that starts and is governed by
        # nothing. Claude Code's `--settings` has the same exposure and errors
        # loudly instead; this one had to be closed here.
        home = Path(settings_path).resolve().parent
        # --dangerously-bypass-hook-trust IS A DEFAULT HERE, and it is the one
        # `dangerously-` flag in this repo that is not opt-in per card. The
        # reason it is different in kind: it does not widen what the MODEL may
        # do (that is --dangerously-bypass-approvals-and-sandbox, below, still
        # per-card). It says "run the hooks in the home directory I wrote
        # myself" — and without it codex declines to run any hook it has no
        # persisted trust record for (codex.py fact 4). The trust record is a
        # hash we cannot compute from outside, so the alternative is emitting a
        # role's whole stop routing into a program that will not run it: hooks
        # present, wired, and inert, which is the failure this repo keeps
        # naming. An agent whose stop events vanish is the aegis-nipg incident.
        flags = "--dangerously-bypass-hook-trust"
        # The codex spelling of the per-card permission opt-in. Same rule as
        # Claude Code's --dangerously-skip-permissions: one agent's decision on
        # one card, never a default anybody inherits (the pilot, aegis-qdal.5).
        if card.dangerous:
            flags += " --dangerously-bypass-approvals-and-sandbox"
        # HONOUR THE CARD'S MODEL AT LAUNCH (GitHub #17/#9), same as claude.
        # `--model` is codex's long form of -m (shared_options.rs). Same
        # card -> role -> fleet ladder as ClaudeHarness: resolving in ONE helper
        # is what keeps the two programs from drifting into different answers for
        # the same card, which is the aegis-85ox mismatch class.
        model = resolve_model(card, root)
        if model:
            flags += f" --model {model}"
        # Identical env contract to ClaudeHarness — SHANTY_ROOT the belt for a
        # stale settings snapshot (aegis-nipg), BOBBIN_ROLE for yupana's scope,
        # BEADS_ACTOR so the tracker records WHO (GitHub #24), ST_ROLES carrying
        # the role set OPAQUELY (GitHub #37). None of that is Claude Code's; it
        # is how a shantytown agent knows who it is, whatever program it runs.
        root_env = f"SHANTY_ROOT={Path(root).resolve()} " if root else ""
        st_roles = f"ST_ROLES={','.join(card.effective_roles())} "
        st_domain = f"ST_ROLE_DOMAIN={card.domain} " if card.domain else ""
        st_reports = f"ST_REPORTS_TO={card.reports_to} " if card.reports_to else ""
        launch = (
            f"{root_env}{codex_mod().HOME_VAR}={home} SHANTY_AGENT={card.name} "
            f"BOBBIN_ROLE={card.role} BEADS_ACTOR={card.name} "
            f"{st_roles}{st_domain}{st_reports}codex {flags}"
        )
        # Launch IN the agent's workspace, same as claude and for the same
        # reason: codex reads AGENTS.md and project config relative to its cwd.
        # A cd prefix, so the single send-keys still delivers one line. (codex
        # also has --cd; the prefix is used so both harnesses put the agent in
        # the same place by the same mechanism, and one shell quoting story
        # covers both.)
        if card.workspace:
            launch = f"cd {card.workspace} && {launch}"
        return launch

    def settings(self, role: str, root=None) -> dict:
        return codex_mod().settings_for_role(role, root=root)

    def settings_name(self, role: str) -> str:
        # A DIRECTORY PER ROLE, because CODEX_HOME names a directory and codex
        # reads a fixed filename inside it. Nested under codex/ so a store that
        # runs both programs has two artifacts that cannot collide, and so
        # `ls <root>/settings` still says which harness each one is for.
        return f"codex/{role}/{codex_mod().CONFIG_FILE}"

    def agent_settings_name(self, agent: str) -> str:
        return f"codex/agent-{agent}/{codex_mod().CONFIG_FILE}"

    def render(self, settings: dict, existing: str = "") -> str:
        return codex_mod().render(settings, existing)

    def read_stop_directions(self, text: str) -> "set[str] | None":
        return codex_mod().stop_directions(text)

    def read_usage(self, session_path: str | Path) -> Usage | None:
        """Codex stores cumulative snapshots; take the greatest total once."""
        best: Usage | None = None
        try:
            with Path(session_path).open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        usage = (((json.loads(line).get("payload") or {}).get("info") or {})
                                 .get("total_token_usage") or {})
                    except (ValueError, AttributeError):
                        continue
                    if not isinstance(usage, dict) or "total_tokens" not in usage:
                        continue
                    candidate = Usage(
                        _count(usage.get("input_tokens")),
                        _count(usage.get("cached_input_tokens")),
                        _count(usage.get("cache_write_input_tokens")),
                        _count(usage.get("output_tokens")),
                        _count(usage.get("reasoning_output_tokens")),
                        _count(usage.get("total_tokens")),
                    )
                    if best is None or candidate.total_tokens > best.total_tokens:
                        best = candidate
        except OSError:
            return None
        return best
    def read_bash_guard(self, text: str) -> "str | None":
        return codex_mod().bash_guard(text)

    def settings_in_cmdline(self, cmdline: str) -> "str | None":
        # The env export IS the pointer, so this reads an assignment rather than
        # a flag — and returns the config file, not the directory, so a caller
        # comparing it against an emitted path is comparing like with like.
        var = codex_mod().HOME_VAR
        for tok in cmdline.split():
            if tok.startswith(f"{var}="):
                home = tok.split("=", 1)[1]
                if home:
                    return str(Path(home) / codex_mod().CONFIG_FILE)
        return None

    def carries_settings(self, launch: str, settings_path: str) -> bool:
        # Resolved on both sides: launch() absolutises the home (see there), so a
        # caller that passed a relative path is still pointing at the same file
        # and the invariant must not read that as a settings-less launch.
        return self.settings_in_cmdline(launch) == str(Path(settings_path).resolve())

    def provision(self, settings_path: str, root=None,
                  workspaces=()) -> list[str]:
        """Link the operator's credentials, and record the workspaces as TRUSTED.

        TWO THINGS THE FILE ALONE CANNOT DO, and both fail silently-but-fatally.

        THE TRAP THIS ANSWERS, and it would be silent: CODEX_HOME is not only
        where config.toml lives, it is where codex keeps auth.json. Pointing an
        agent at a home the store owns therefore points it at a home with no
        login, and the failure surfaces as an agent that starts, looks live, and
        cannot call a model.

        A SYMLINK, NEVER A COPY. The token stays in the one place the operator
        already manages, a `codex login` refreshes every agent at once, and the
        store never holds a credential — which matters because this store is a
        git repo in every deployment we know of.

        TRUST is the second, and it is the more dangerous of the two. codex gates
        a directory it has not seen behind a blocking two-option dialog whose
        option 2 is `No, quit`. An agent launched into it is NOT running, while
        every check that reads the pane's PROCESS says it is. A dispatch then
        types into that picker, Enter resolves it, codex exits, and the pane
        falls back to a login shell that executes all subsequent fleet traffic.
        Recording the workspace here — where the home is being written anyway —
        means the dialog never appears. See codex.trust_projects for the
        measurement and for why this looked nondeterministic for so long.

        Best-effort and never fatal: it reports rather than raises, because the
        artifact it is finishing has already been written, and an emitter that
        turned a successful `role set` into a traceback over a symlink would be
        a worse bug than the one it warns about.
        """
        notes: list[str] = []
        home = Path(settings_path).parent

        # TRUST FIRST: an unauthenticated agent is visible and recoverable, an
        # agent that quits on its dialog leaves an executing shell behind. Done
        # before the early `return` the credentials branch can take, so a store
        # with no codex login still gets its trust records written.
        if workspaces:
            try:
                cfg = Path(settings_path)
                before = cfg.read_text() if cfg.exists() else ""
                after = codex_mod().trust_projects(before, workspaces)
                if after != before:
                    cfg.write_text(after)
            except OSError as e:
                notes.append(
                    f"could not record trusted workspaces in {settings_path} "
                    f"({e}); an agent launched there may stop on codex's "
                    f"'Do you trust the contents of this directory?' dialog, "
                    f"which a dispatch can answer as 'No, quit'.")

        source = _codex_credentials()
        if source is None:
            return notes + [
                f"no codex auth.json found — agents using {home} will launch "
                f"UNAUTHENTICATED. Run `codex login` (or set CODEX_HOME to a "
                f"logged-in home before emitting) and re-run `st roles set`."]
        link = home / "auth.json"
        try:
            if link.is_symlink() and link.readlink() == source:
                return notes
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(source)
        except OSError as e:
            notes.append(
                f"could not link {link} -> {source} ({e}); agents using {home} "
                f"will launch UNAUTHENTICATED.")
        return notes

    def hooks(self, card: Agent) -> "HookSpec":
        """codex DELIVERS BLOCKING STOP HOOKS, and this reverses a claim this
        repo shipped.

        docs/adapters.md and the old CodexRuntime both said codex could not —
        that a codex card was worker-only and the capability gate's negative
        control. That was true of the codex those were written against, and it
        is not true now: codex's Stop hook parses `{"decision":"block",
        "reason":"…"}` (or exit 2 with the reason on stderr) and turns the reason
        into a continuation prompt for the MODEL, which is the whole of the
        capability (codex.py fact 3, codex-rs/hooks/src/events/stop.rs).

        SO THE COST OF BEING WRONG MOVED, and it is worth being plain about it.
        While this said False, the failure mode was a refusal — a lead you could
        not create. Saying True means a lead on a codex OLDER than the hooks
        system would be accepted and would absorb nothing: stop events routed
        into a program that never runs the hook, silently. That is the failure
        the gate exists to prevent, so it is not hidden here — it is a version
        floor we cannot check from inside st (`codex --version` at role-set time
        would be measuring a binary the agent may not even launch with), and it
        belongs in `st doctor` as a tool row, not in a guess made here.
        """
        from .runtime import HookSpec
        return HookSpec(blocking_stop=True)


def _codex_credentials() -> Path | None:
    """The auth.json of the operator's own codex home: $CODEX_HOME if set, else
    ~/.codex (codex.py fact 1). None when there is no login to link."""
    import os
    base = os.environ.get(codex_mod().HOME_VAR) or (Path.home() / ".codex")
    p = Path(base) / "auth.json"
    return p if p.is_file() else None


def codex_mod():
    """The codex module, imported at CALL TIME — the same one-directional import
    rule the settings()/hooks() halves follow. harness must stay importable
    without dragging in every program it can host."""
    from . import codex
    return codex


def merge_one_level(existing: dict, emitted: dict) -> dict:
    """Emitted keys win; everything else the operator wrote survives.

    ONE LEVEL DEEP for dict values, and that depth is the whole rule. The keys st
    emits are dicts the operator also has a legitimate claim on:

      hooks   st replaces the EVENTS it emits — a stale stop direction surviving a
              rewrite is exactly the drift `roles set` exists to remove — but an
              event st does not emit (a Notification hook, a SessionStart prime,
              codex's own `[hooks.state]` trust ledger) is left as found.
      env     st sets BOBBIN_ROLE; an operator's own variables beside it are theirs.

    Deeper than one level would start merging st's hook LISTS with an operator's,
    which is how a removed hook comes back. Shallower is the wholesale clobber this
    fixes. So: one level, emitted wins per sub-key.

    Lives here, shared by both harnesses, because it is not a fact about either
    format — it is st's rule about whose file this is. (It was cli's until codex
    needed the identical rule for TOML; two copies of a merge rule is how two
    formats come to disagree about what an operator is allowed to keep.)
    """
    out = dict(existing)
    for key, value in emitted.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


def _load_json(text: str) -> dict:
    try:
        data = json.loads(text) if text.strip() else {}
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


_HARNESSES = {h.name: h for h in (ClaudeHarness(), CodexHarness())}


def all_harnesses() -> tuple[Harness, ...]:
    """Every harness this build implements, DEFAULT FIRST.

    Order is load-bearing for the sniffing readers (runtime.settings_path_in_
    cmdline, stop_directions_in): they ask each harness in turn and take the
    first that recognises what it is looking at. Claude Code first keeps the
    common case first, and every implementation is format-anchored — it answers
    None rather than guessing — so first-match is a decision, not a coin toss.
    """
    return (_HARNESSES[DEFAULT],) + tuple(
        h for n, h in _HARNESSES.items() if n != DEFAULT)


def settings_env_vars() -> tuple[str, ...]:
    """Every environment variable some harness carries its settings pointer in.

    Exists so a PROCESS READER can reconstruct a launch line without knowing
    which program it is looking at. tmux.cmdline reads argv out of `ps`, and argv
    is not the launch line for a program whose pointer is a shell assignment —
    `CODEX_HOME=<dir> codex …` reaches the process as ENVIRONMENT, never as an
    argument. Without this the reader sees a codex agent's argv, finds no
    pointer, and reports a fully-wired agent as a hookless zombie (aegis-506x9).

    Derived from the registry rather than listed, so a third harness with an
    env-borne pointer is covered by declaring `settings_env_var` on itself — the
    reader does not get a third special case, which is how the second one got
    missed.
    """
    return tuple(dict.fromkeys(
        v for h in all_harnesses()
        if (v := getattr(h, "settings_env_var", None))))


def picker_markers() -> tuple[str, ...]:
    """Blocking-picker chrome across EVERY harness this build implements.

    A union rather than a per-card lookup, and that is a fact about the current
    deployment rather than a preference: ClaudeRuntime is instantiated for every
    card regardless of its harness (four call sites, none of them conditional),
    so the pane predicates run Claude Code's markers against codex panes. A codex
    agent stopped on a picker therefore matched nothing and reported `?` — honest,
    unactionable, and precisely the state aegis-qxc2 forbids an agent to sit in.

    The union is safe for the same reason the existing markers are: matching is
    TAIL-ONLY, so the cost of carrying another program's footer strings is that an
    agent which prints one at the very bottom of its pane is called `waiting`. The
    asymmetry is worth stating plainly — a false `waiting` costs a coordinator one
    glance, while the false negative it replaces cost an agent an entire session.
    """
    return tuple(dict.fromkeys(
        m for h in all_harnesses() for m in getattr(h, "picker_markers", ())))


def ready_patterns() -> tuple[str, ...]:
    """Measured ready-UI patterns across registered harnesses.

    The runtime currently receives a pane but not its card, so this is a union
    for the same reason as picker_markers. Matching is tail-only at the caller.
    """
    return tuple(dict.fromkeys(
        p for h in all_harnesses() for p in getattr(h, "ready_patterns", ())))


def stranded_markers() -> tuple[str, ...]:
    """Chrome meaning "the body I just sent is still in the input box".

    Union across the registry, same reason as picker_markers(): the pane readers
    are handed no harness, so a per-program check would only cover the program
    that happens to be the default. Tail-only matching keeps the cost of carrying
    another program's chrome to a false "not delivered" — which is the safe
    direction here, because the failure it replaces is a false "delivered".
    """
    return tuple(dict.fromkeys(
        m for h in all_harnesses() for m in getattr(h, "stranded_markers", ())))


def get(name: str | None) -> Harness:
    """The harness by name. None -> the default, which is every card today.

    RAISES UnknownHarness for a name we do not implement. It does NOT fall back to
    the default: a card that asks for `opencode` and silently gets `claude` is a
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


def for_card(card: Agent, root=None) -> Harness:
    return get(name_for(card, root=root))


def name_for(card: Agent, root=None) -> str:
    """What harness IS this card's, as a string — including for a card that never
    said. This is what `st anchor --harness` prints, and it answers with the
    DEFAULT rather than blank: "claude" is the true answer for an unset field, and
    an empty status-bar segment would read as "no harness".

    MOST SPECIFIC WINS: the card, then the deployment's rule for its ROLE, then
    the deployment's fleet-wide default, then "claude"
    (config._harness). Same shape as the trait model's precedence, because it is
    the same question — who decided this, and how narrowly?

    `root` is where the deployment's answer lives, so a caller that has no root
    gets the card's own answer and the built-in default. That is a degradation to
    the previous behaviour, never to a wider one — but it does mean the root has
    to be THREADED to every surface that asks, not just the launcher: a gate that
    resolves `claude` while the launcher resolves `codex` is the aegis-85ox
    mismatch with a config file in the middle. The call sites are cli's resolver
    and emitter, ClaudeRuntime (which holds its own root), and tier.role_set's
    capability gate.
    """
    if card.harness:
        return card.harness
    return _deployment_harness(card.role, root) or DEFAULT


def resolve_model(card, root=None) -> str | None:
    """Which MODEL this card runs, or None to let the harness choose its own.

    MOST SPECIFIC WINS: the card, then the deployment's rule for its ROLE, then
    the deployment's fleet-wide default, then None — the same ladder as
    resolve_harness, because it is the same question one axis over.

    None is a REAL answer here and not a failure, which is where this differs
    from harness: `claude` is a sane fallback program, but there is no sane
    fallback MODEL — shantytown does not know which slugs a given harness can
    reach, and inventing one would pin every card in the fleet to a guess that
    goes stale on the provider's schedule. None means "say nothing", the flag is
    omitted, and the harness applies its own default. That is the behaviour every
    deployment predating this table already has, so adding the table changes
    nothing for anyone who does not write it.
    """
    if getattr(card, "model", None):
        return card.model
    return _deployment_model(getattr(card, "role", None), root)


def _deployment_model(role: str | None, root) -> str | None:
    """The deployment's [model] answer for a role, or None if it never said.

    load_or_default for the same reason _deployment_harness uses it: this runs on
    the LAUNCH path, and a typo elsewhere in the config must not silently move a
    card onto a different model — it surfaces as the config error at the top of
    the command, while an unreadable file means "the deployment did not say".
    """
    if root is None:
        return None
    from .config import load_or_default
    cfg, _err = load_or_default(root)
    if role and cfg.model_by_role.get(role):
        return cfg.model_by_role[role]
    return cfg.model_default


def _deployment_harness(role: str | None, root) -> str | None:
    """The deployment's [harness] answer for a role, or None if it never said.

    load_or_default, never load: this resolves on the LAUNCH path and inside
    hooks, and a config typo must surface as the config error at the top of a
    command — not as a fleet that mysteriously reverted to Claude Code. An
    unreadable file therefore means "the deployment did not say", which is what a
    deployment with no file gets.
    """
    if root is None:
        return None
    from .config import load_or_default
    cfg, _err = load_or_default(root)
    if role and cfg.harness_by_role.get(role):
        return cfg.harness_by_role[role]
    return cfg.harness_default
