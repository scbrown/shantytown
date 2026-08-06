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

    def settings_in_cmdline(self, cmdline: str) -> "str | None":
        """The settings artifact a RUNNING process was launched with, read off
        its command line — or None if this launch is not one of ours. This is
        what makes the foreign-launcher check (runtime.live_wiring) work for a
        program whose settings ride an env export instead of a flag."""
        ...

    def carries_settings(self, launch: str, settings_path: str) -> bool:
        """Does this composed launch actually point at that artifact? The
        compose invariant, asked of the program that owns the syntax."""
        ...

    def provision(self, settings_path: str, root=None) -> "list[str]":
        """Anything the artifact alone cannot do, run after it is written.
        Returns human-readable notes for the operator (empty is the normal
        answer). It exists because codex needs one — its home directory holds
        credentials as well as config — and inventing that seam at the call site
        would have made the emitter know which harness it was serving."""
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
        # field: it reads as configured.
        if card.model:
            flags += f" --model {card.model}"
        # BOBBIN_ROLE is how hank's policy guard resolves WHICH scope applies
        # (hank#20: tenant is resolved --tenant, then BOBBIN_ROLE; scopes live in
        # .bobbin/config.toml under [hank.policy.scopes.<role>]). Exporting it per
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

    def provision(self, settings_path: str, root=None) -> list[str]:
        # Nothing. Claude Code reads the file the flag names and needs no second
        # artifact beside it — the workspace-level wiring (.mcp.json, the consent
        # settings) is provision.py's job and predates the harness split.
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
        # `--model` is codex's long form of -m (shared_options.rs).
        if card.model:
            flags += f" --model {card.model}"
        # Identical env contract to ClaudeHarness — SHANTY_ROOT the belt for a
        # stale settings snapshot (aegis-nipg), BOBBIN_ROLE for hank's scope,
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

    def provision(self, settings_path: str, root=None) -> list[str]:
        """Link the operator's codex credentials into the home we just wrote.

        THE TRAP THIS ANSWERS, and it would be silent: CODEX_HOME is not only
        where config.toml lives, it is where codex keeps auth.json. Pointing an
        agent at a home the store owns therefore points it at a home with no
        login, and the failure surfaces as an agent that starts, looks live, and
        cannot call a model.

        A SYMLINK, NEVER A COPY. The token stays in the one place the operator
        already manages, a `codex login` refreshes every agent at once, and the
        store never holds a credential — which matters because this store is a
        git repo in every deployment we know of.

        Best-effort and never fatal: it reports rather than raises, because the
        artifact it is finishing has already been written, and an emitter that
        turned a successful `role set` into a traceback over a symlink would be
        a worse bug than the one it warns about.
        """
        home = Path(settings_path).parent
        source = _codex_credentials()
        if source is None:
            return [f"no codex auth.json found — agents using {home} will launch "
                    f"UNAUTHENTICATED. Run `codex login` (or set CODEX_HOME to a "
                    f"logged-in home before emitting) and re-run `st role set`."]
        link = home / "auth.json"
        try:
            if link.is_symlink() and link.readlink() == source:
                return []
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(source)
        except OSError as e:
            return [f"could not link {link} -> {source} ({e}); agents using {home} "
                    f"will launch UNAUTHENTICATED."]
        return []

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
