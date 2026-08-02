"""st — the CLI. Twenty-two commands, and the count is load-bearing: each earns its slot.

    anchor [--short|--events|--harness] · go · inbox [--count] · task
    · crew [--count|--governor] · input [--show|--clear|--dismiss] · ask · answer
    · roles [--check|set|sync] · init · new · start [--mode]
    · stop · log · context · doctor [--install]
    · tend [--install|--status|--reauth|--target] · attach [-r|--no-start]
    · dashboard [admin] · subscribe · worktree [--gc] · stats

Six of those flags are MACHINE-READABLE modes, added for an external status bar
(anchor --short/--events/--harness, crew --count, crew --governor, inbox --count).
They are flags and not commands on purpose: the surface is the thesis, and "a
status bar wants this" does not earn a slot. Each prints ONE value and nothing
else — docs/cli.md.

TWO COMMANDS WERE RENAMED, and the count did not move (Stiwi, 2026-07-19):
  · prime -> anchor — an agent's anchor is what holds it to its work. `prime`
    named the HARNESS's act, and we inherited the word from the tool we left.
  · mail  -> inbox  — because it is now a REAL inbox: a pluggable protocol with a
    files and a tracker/beads implementation (shantytown/inbox.py), selected by
    the same --backend switch as the tracker, with a read side. `st mail -d`
    persisted a message nothing ever read back, onto the recipient's PLATE.

The binary is `st`, not `shanty`: `shanty` is Stiwi's tmux command and ours would
shadow it on PATH. A harness that steals the operator's own command name has
already made itself the centre of the world.

Gas Town ships ~110. This is not a smaller version of that list; it is the short
set we measurably use, and the discipline is the point (docs/cli.md). The surface
grew past the original ten by seven, each on a specific ask — not drift:
  · context — the bobbin Context protocol
  · doctor  — out-of-box tool detect/install, Stiwi's direct ask
  · tend    — crew supervision, native. Owner-directed, and it is a COMMAND and
              not a flag on `st crew` for one reason: `crew` is a read, and this
              is the only surface that can create a session and launch an agent.
              A consequence behind a flag on a read is a consequence somebody
              triggers by running the safe-looking thing.
  · attach  — attach to a crew member by name; st resolves the socket + pane so
              the operator never types `tmux -L <sock> attach -t <pane>`. Goes
              THROUGH shanty (themed) when present, bare tmux otherwise — this is
              where "use shanty, not raw tmux" becomes the default view.
  · dashboard — a live, tier-scoped observability panel: roster, current work, the
              REUSED state verdicts, last activity. The always-on sibling of the
              one-shot `crew`; refreshes on an interval in a second pane.
  · subscribe — watch quipu entity events and route governed workflows to the
              admin (the events adapter integrations.md sketched, built first-
              class on Quipu's cursored transaction log). Owner-directed.
  · start   — BOOT the town by mode: `lite` brings up the administrator alone and
              lets it decide who else is needed, `heavy` brings up every card.
              Owner-directed, and it is a command for the reason bootstrap.py
              argues: `new` REFUSES a live session (right for a primitive, wrong
              for a boot, where "already up" is success) and `tend` is a
              timer-driven supervisor that will not touch an agent it has no
              launch stamp for — a cold host has no stamps. Declarative and
              idempotent: it converges the fleet on a named crew set.
  · ask     — print the QUESTION a blocked agent is sitting on: prompt, the command
              being approved, and the numbered options VERBATIM. A read, and it
              exists because the read it replaces was `tmux -L <sock> capture-pane
              -p -t <pane> | tail -12` run by hand six times in one evening.
  · answer   — select an option BY NUMBER. Its own slot rather than a flag on `ask`
              for the reason `tend` and `attach` have theirs: `ask` is a READ, and
              this is the only verb in the repo that acts inside ANOTHER agent's
              decision. A consequence behind a flag on a read is a consequence
              somebody triggers by running the safe-looking thing.
  · init    — scaffold a NEW deployment by asking: the store, the crew cards (with
              generated panes), their hooks, and shantytown.toml. It writes through
              the EXISTING seams — the registry, tier.role_set, the same settings
              emitter `roles set` uses — so it is not a second way to declare a
              crew, it is the first way to get one without hand-authoring JSON.
The count is PINNED by a test (tests/test_command_count.py): the next command
either updates this number or fails CI. This docstring used to say "ten" while the
code had eleven (context landed unannounced) — a count nobody enforces is a
comment, and in a repo whose whole thesis is the exact count, that is the bug.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
# MODULE LEVEL, not function-local. `_retire_card` referenced a bare `replace`
# while the only import of it sat INSIDE `_tend_retire`, so the crash-loop
# give-up raised NameError on every call and had never once written a card. It
# was invisible because the raise landed in that function's `except Exception`,
# which printed "⚠ could not retire <name>" into the middle of a tend pass —
# the one place a warning is guaranteed to scroll past unread. tend reported
# CRASH_LOOP ("RETIRED rather than respawned again") while the card said
# nothing of the kind: the supervisor's own report and the durable state
# disagreed, which is the same shape as the bug this bead is about, one caller
# over. Found by a test written for the provenance work (aegis-6hfmi).
from dataclasses import replace
from pathlib import Path

from . import beads as beads_mod
from . import bootstrap as boot_mod
from . import config
from . import harness as harness_mod
from . import launchable
from . import roles as roles_mod
from . import scaffold
from . import triage as triage_mod
from .deployment import deployment_default, resolve_root, root_note
from .dispatch import (Dispatcher, TriageRefused, SendUnverified,
                       DispatchedButUntracked, AlreadyAssigned, Closed,
                       GovernorRefused)
from . import forgejo as forgejo_mod
from . import governor as gov_mod
from . import guard as guard_mod
from .events import FilesEvents
from .inbox import FilesInbox, MessageTooLong, TrackerInbox
from .triage import Action
from . import supervisor as sup_mod
from . import tend as tend_mod
from . import provision as prov_mod
from . import notify as notify_mod
from .files import FilesRegistry, FilesTracker, plate as files_plate
from .launched import FilesLaunches, CURRENT, STALE, UNKNOWN
from .stopped import FilesStops
from .quipu import QuipuRegistry
from . import selfcheck
from .anchor import Unreachable, anchor as do_anchor
from .runtime import (asks_a_question, auth_expired, ClaudeRuntime, CapabilityError,
                      SettingsError, emitted_stop_directions, live_stop_directions,
                      live_wiring, settings_for_role)
from .tmux import Tmux, declared_socket
from .workspace import (WorkspaceError, cleanup_worktree, ensure_workspace,
                        ensure_worktree, tree_staleness, unlaunchable,
                        upstream_ref, worktree_for)
from .provision import ProvisionError, provision as provision_ws

# `st new` liveness poll: how long to wait for the runtime to appear in the pane
# before returning could-not-tell (2). Module constants so tests can shrink them
# to (1, 0) — a real launch takes a few seconds, a test must not.
_LIVE_ATTEMPTS = 20
_LIVE_DELAY = 0.25

# 0 did it | 1 refused (precondition) | 2 could not tell (backend unreachable)
OK, REFUSED, CANNOT_TELL = 0, 1, 2


def _panes(a):
    """The pane adapter for this invocation, on the FLEET's declared socket.

    Never a bare Tmux(): bare tmux sees only the default server, so from inside
    any pane on a named socket — including the status-bar wrapper's — it reports
    EVERY LIVE AGENT AS DOWN, confidently and with exit 0. tmux.py has warned
    about exactly this since it was written; the CLI just never asked.
    """
    return Tmux(socket=declared_socket(getattr(a, "root", None) or "."))


def _registry(a):
    """Identity backend for this invocation, selected by --registry.

    quipu is the SOURCE OF TRUTH (Stiwi: "quipu should be the source of truth");
    files is the projection/cache and the leak detector. Default stays files so
    an offline invocation still resolves identity locally; --registry quipu reads
    it straight from the graph. Either way the SAME roles.check runs over it.
    """
    chosen = getattr(a, "registry", "files")
    if chosen == "quipu":
        # Pass the root so the ADDRESS and the NAMESPACE both come from the
        # deployment's env.json rather than whatever this shell exported. Without
        # it the client falls back to quipu's stock port — which on a host where
        # another service owns it means quietly querying a stranger — and to the
        # stock namespace, which means truthfully querying the real graph for
        # entities that do not exist. Both used to read as "an empty fleet".
        return QuipuRegistry(root=getattr(a, "root", None))
    if chosen == "toml":
        # GitHub #11: identity declared by hand, in the same file as the modes,
        # for a deployment that wants no ontology and no generated cards.
        return config.TomlRegistry(a.root)
    return FilesRegistry(a.root / "crew")


def _deployment_default(a, key: str) -> str | None:
    """A deployment-declared default for `key`: <root>/env.json (gitignored
    deployment config), then the ambient env — the SAME source order the launch
    side already uses for carried env and SHANTY_BASH_GUARD (runtime.py), so
    "where deployment config lives" has one answer, not two.

    Why this exists (aegis-tisp): the shanty status-bar segment and the session
    picker both call PLAIN `st anchor <agent> --short` — by design, a public
    repo must not embed a tracker path. On a fleet whose plates live in beads,
    plain `st anchor` resolved to the files backend and rendered EMPTY, so both
    surfaces were blank, consistently and with exit 0. The deployment needs to
    say "my tracker is beads at <repo>" ONCE; this is where it says it.

    The READ itself now lives in deployment.deployment_default — the same six
    lines were also in runtime.py twice, and a fourth caller was coming. This
    stays as the CLI's args-shaped door onto it.
    """
    return deployment_default(getattr(a, "root", None), key)


def _backend(a, default="files") -> str:
    """The selected tracker backend: explicit --backend, else the deployment's
    SHANTY_BACKEND (env.json/env), else `default` (per-command).

    ONE resolver, because the sentinel only buys honesty if nothing re-guesses
    it. `--backend` now defaults to None so "the user said files" and "the user
    said nothing" stop being the same value — which they had to stop being for
    `mail -d` to default differently without overriding an explicit choice.
    A deployment default sits BETWEEN those: quieter than a flag, louder than
    the built-in guess — and an unrecognized value REFUSES rather than falling
    through to files, because a silent files fallback is the exact blank-plate
    bug this knob exists to fix (aegis-tisp).
    """
    explicit = getattr(a, "backend", None)
    if explicit:
        return explicit
    declared = _deployment_default(a, "SHANTY_BACKEND")
    if declared:
        if declared not in ("files", "beads", "forgejo"):
            raise SystemExit(f"  refused: SHANTY_BACKEND={declared!r} is not a "
                             "backend (files|beads|forgejo). Fix [env] in "
                             "shantytown.toml (or the environment); a typo must "
                             "not silently mean files.")
        return declared
    return default


def _tracker(a, default="files"):
    """The tracker for this invocation, selected by --backend (#3).

    arnold added beads.plate() (the reader) but the CLI still wired FilesTracker
    unconditionally, so `st --backend beads` did not exist and his plate was
    unreachable. This wires it: --backend beads reaches BeadsTracker; --repo is
    bd's -C. Identity (registry) stays files — work lives in beads, identity does
    not.
    """
    b = _backend(a, default)
    if b == "beads":
        return beads_mod.BeadsTracker(repo=getattr(a, "repo", None)
                                      or _deployment_default(a, "SHANTY_BEADS_REPO")
                                      or _default_bd_repo(a))
    if b == "forgejo":
        # --repo is owner/name here (the forge's coordinates), not a directory.
        from .forgejo import ForgejoTracker
        repo = getattr(a, "repo", None)
        if not repo:
            raise SystemExit("  refused: --backend forgejo needs --repo owner/name")
        return ForgejoTracker(repo)
    return FilesTracker(a.root / "items")


def _default_bd_repo(a) -> str | None:
    """Where bd resolves its store when `--repo` was not given: feed_check's
    bd_cwd walk-up (the admin card's workspace, walked to the nearest .beads) —
    NEVER the ambient cwd (aegis-quvg).

    The ambient-cwd default on bd-backed paths is a measured recurring class:
    the tend loop ran two days with the idle-fleet push dead on it (bd5f55a),
    and `st inbox` read "no beads database found" from any non-store cwd —
    including ~/gt/shantytown, where an operator most naturally stands. ONE
    resolver, shared with feed_check, so the Rule Zero gate and every beads-
    backed read can never disagree about where the store lives. Explicit
    `--repo` always wins; a failed resolution falls back to None (ambient cwd,
    today's behavior) — fail toward the old default, never toward an invented
    path."""
    try:
        from .feed_check import bd_cwd
        return bd_cwd(_registry(a))
    except Exception:
        return None


def _plate(a):
    """The plate reader matching the selected tracker — uses arnold's beads.plate
    for the beads backend (his is canonical; my duplicate was dropped)."""
    trk = _tracker(a)
    if _backend(a) == "beads":
        return lambda who: beads_mod.plate(trk, who)
    return lambda who: files_plate(trk, who)


def _inbox(a, default="files"):
    """The inbox for this invocation, selected by the SAME --backend switch as the
    tracker (Stiwi: "an inbox concept we can map to beads or other ticket
    modules"). No second selection mechanism — one switch, or an operator ends up
    sending on one backend and reading on another.

        files  -> FilesInbox under the .shanty root, beside events/. Structurally
                  off the plate, and the leak detector for the other one.
        beads  -> TrackerInbox over the SELECTED tracker, so a durable message is
                  a real bead on the aegis store (dearing's qdal.2 parity ruling).

    The beads side needs a LISTER, which the three-function Tracker protocol does
    not have and must not grow (aegis-gqr8). It is injected per-backend, exactly
    like the plate reader two functions up.
    """
    # Resolve through _backend, NOT getattr(a,"backend","files"). `--backend`
    # defaults to None now (the sentinel that lets -d default differently), so
    # the old getattr read None and fell to files ALWAYS — including on the
    # durable path, which was printing "(beads)" while writing to files. A
    # command that reports a different store than it wrote to is the exact lie
    # this repo exists to refuse, and it is worse than the missing default:
    # you would go looking in beads for a message that is not there.
    if _backend(a, default) == "beads":
        trk = _tracker(a, default)
        return TrackerInbox(trk, lambda: beads_mod.items(trk))
    return FilesInbox(Path(a.root) / "inbox")


def _me(a) -> str | None:
    """Who am I, for the commands that default to the caller. One resolution —
    the positional if the command has one, else $SHANTY_AGENT (which the launcher
    exports, harness.py). Used by anchor and by the inbox read modes; a status bar
    calls both, and they must agree about whose plate and whose inbox."""
    import os
    return getattr(a, "me", None) or os.environ.get("SHANTY_AGENT")


def _wire(a) -> Dispatcher:
    return Dispatcher(_registry(a), _tracker(a), _panes(a),
                      governor=_dispatch_gate(a))


def _governor(a):
    """The usage governor for this invocation, or None if none is configured.

    None means OFF, and off is the default: with no `[[governor.tier]]` in
    shantytown.toml there is nothing to enforce, and every command behaves
    exactly as it did before this feature existed.

    A MISCONFIGURED GOVERNOR IS NEVER SILENTLY OFF. `config.load_or_default`
    already refuses to raise on this path, but a source we cannot wire (a
    `prometheus` with no url) would leave the fleet running ungoverned at exactly
    the moment the operator believed they had turned the feature ON. So the
    failure is printed, every time, on stderr — the same rule the module's
    fail-safe section applies to a lost signal.
    """
    cfg, err = config.load_or_default(Path(a.root))
    if err:
        print(f"  ⚠ {err} — running on config DEFAULTS", file=sys.stderr)
    if not cfg.governor.active:
        return None
    try:
        reader = gov_mod.reader_for(cfg.governor)
    except gov_mod.GovernorError as e:
        print(f"  ⚠ usage governor DISABLED — {e}. The fleet is running "
              f"UNGOVERNED: no tier will engage at any usage level.",
              file=sys.stderr)
        return None
    return gov_mod.Governor(cfg.governor, reader,
                            gov_mod.FilesGovernorState(Path(a.root)))


def _dispatch_gate(a):
    """The `st go` half of the governor: `item -> "" | refusal`.

    A PURE READ (`persist=False`). Dispatching must not RATCHET fleet policy as a
    side effect of being run — `st tend` is the pass that already decides who
    lives and it is the one writer of the engaged tier. A dispatch that extended
    a hysteresis hold would mean the governor's state depended on how often
    somebody typed `st go`.

    Evaluated once per invocation and closed over, so a dispatch pays at most one
    metric read — not one per plan()/triage()/go() call on the same Dispatcher.
    """
    gov = _governor(a)
    if gov is None:
        return None
    verdict = gov.evaluate(persist=False)
    if verdict.alarm:
        print(f"  ⚠ {verdict.alarm}", file=sys.stderr)
    return verdict.admits


def _default_root() -> Path:
    """Where the store is when nobody said — the shared discovery chain.

    Resolved at CALL time, not at import: a test (and a shell) that sets the env
    after this module is imported must still be honoured, and a module-level
    default would freeze whatever the environment happened to be at import.
    """
    return resolve_root()[0]


def build_parser() -> argparse.ArgumentParser:
    """The full `st` parser. Exposed so tests/test_command_count.py can introspect
    the command surface and pin it to the docstring — the count is the thesis."""
    ap = argparse.ArgumentParser(prog="st")
    # --root, else $SHANTY_ROOT, else cwd/.shanty — the SAME precedence the Stop
    # hook uses, because the two must agree about where the store is. They did
    # NOT: the CLI resolved the root from the CURRENT WORKING DIRECTORY only,
    # while stop_event honoured the env AND its comment claimed "same default as
    # the CLI". An agent's cwd is its own workspace, which has no .shanty, so
    # every `st` call from an agent pane looked in a directory that does not
    # exist — and anything shelling out to `st` without --root (the status bar
    # segments) rendered EMPTY rather than erroring. Measured: the segments
    # produced nothing from any cwd except the checkout itself.
    # version + the deployed git SHA: __version__ is static, so
    # only the SHA distinguishes a fresh install from a stale one.
    from . import __version__, deployed_sha

    ap.add_argument(
        "--version",
        action="version",
        version=f"st {__version__} ({deployed_sha()})",
    )
    # DEFAULT None, resolved in main() — not computed here. Two commands need
    # different answers for "nobody said": every read/launch command DISCOVERS a
    # store (walk up, then the box's pointer), while `init` must answer cwd/.shanty
    # and never adopt a deployment it merely found. A default baked in at parse
    # time cannot tell "unset" from "set to the discovered value", so the choice
    # would be unmakeable.
    ap.add_argument("--root", type=Path, default=None,
                    help="the store. Unset: $SHANTY_ROOT, else a .shanty found by "
                         "walking up from here, else the box's deployment pointer "
                         "(~/.config/shantytown/root), else ./.shanty")
    ap.add_argument("--backend", choices=["files", "beads", "forgejo"], default=None,
                    help="tracker backend (identity is always files). #3. "
                         "Unset means the deployment's SHANTY_BACKEND "
                         "(<root>/env.json, then env), else per-command "
                         "default: files everywhere, EXCEPT `mail -d`, which "
                         "defaults to beads because a must-survive message "
                         "belongs in the shared store (dearing, qdal.2). Pass "
                         "--backend files to force local.")
    ap.add_argument("--repo", default=None,
                    help="bd -C <dir> when --backend beads (unset: deployment's "
                         "SHANTY_BEADS_REPO, else the .beads walk-up)")
    ap.add_argument("--registry", choices=["files", "quipu", "toml"], default="files",
                    help="identity backend: files (generated cards, the default), "
                         "quipu (the graph), or toml ([crew.<name>] in "
                         "<root>/shantytown.toml — hand-authored, read-only, for a "
                         "deployment that wants no ontology).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    an = sub.add_parser("anchor", help="who am I, what's on my plate")
    an.add_argument("me", nargs="?", help="defaults to $SHANTY_AGENT")
    # MACHINE-READABLE (aegis status bar). Each prints ONE value and nothing else
    # — no banner, no label, no prose — because the consumer is a Go program
    # rendering a segment, and "empty" has to mean "nothing to show".
    mr = an.add_mutually_exclusive_group()
    mr.add_argument("--short", action="store_true",
                    help="print ONLY the plate item's id (empty if the plate is empty)")
    mr.add_argument("--events", action="store_true",
                    help="print ONLY the number of UNDELIVERED stop events for me. "
                         "A READ: it never marks anything delivered (see events.py)")
    mr.add_argument("--harness", action="store_true",
                    help="print ONLY this agent's harness name (e.g. claude)")

    go = sub.add_parser("go", help="dispatch an item to an agent")
    go.add_argument("item")
    go.add_argument("agent")
    note = go.add_mutually_exclusive_group()
    note.add_argument("--note", default=None,
                      help="a caveat delivered IN the same payload as the "
                           "dispatch — it rides the triage gate with the work, "
                           "so it cannot arrive after the worker has acted. "
                           "Flattened to one line (the transport submits on "
                           "newline).")
    note.add_argument("--note-file", type=Path, default=None,
                      help="read the note from a file (or - for stdin). Use this "
                           "for anything long or containing quotes/backticks — "
                           "shell expansion in a --note string is a real "
                           "footgun.")
    go.add_argument("-n", "--dry-run", action="store_true")
    go.add_argument("--reassign", action="store_true",
                    help="take an item another agent already holds. Without this, "
                         "dispatching an assigned item REFUSES rather than silently "
                         "stealing it.")
    go.add_argument("--worktree", metavar="REPO", default=None,
                    help="the work touches a SHARED project repo (a path, or a "
                         "bare name under $GT_ROOT): provision this agent an "
                         "ISOLATED worktree off it and deliver its path in the "
                         "dispatch, so two agents on the same repo never share an "
                         "index/HEAD. Refuses if the worktree cannot be made — "
                         "dispatching shared-repo work with no isolation is the "
                         "clobber bug, not a fallback.")

    cr = sub.add_parser("crew", help="who exists, what state, what role")
    cr.add_argument("--count", action="store_true",
                    help="print ONLY `busy/total` — the same verdict the table "
                         "renders, for a status bar. Agents whose busy/idle state "
                         "is unknown are in NEITHER number")
    cr.add_argument("--governor", action="store_true",
                    help="print ONLY the capacity verdict, machine-readable, for a "
                         "status bar: `ok <five_hour> <seven_day> [tier label]`, or "
                         "the bare word `lost` / `off`. Both budgets, because they "
                         "exhaust independently")
    cr.add_argument("--trees", action="store_true",
                    help="also measure each agent's WORKTREE off every shared "
                         "repo, not just its workspace clone. Costs a few git "
                         "calls per agent per repo — this is the whole staleness "
                         "sweep in one command, instead of a hand-rolled loop "
                         "across directories")

    # aegis-fagvi: the roles/role footgun (plural-vs-singular) is consolidated
    # into ONE noun with verbs: `st roles {show|set|sync}`. The old `role` and
    # `project` top-level commands stay as ALIASES below so nothing breaks.
    rl = sub.add_parser("roles", help="the role hierarchy: show | set | sync")
    # `roles --check` / bare `roles` == `roles show [--check]` (back-compat).
    rl.add_argument("--check", action="store_true")
    rl_sub = rl.add_subparsers(dest="roles_sub", required=False)
    rl_show = rl_sub.add_parser("show", help="the hierarchy, and whether it's real")
    # SUPPRESS default so `roles show` (no flag) keeps the parent's --check value.
    rl_show.add_argument("--check", action="store_true", default=argparse.SUPPRESS)
    rl_set = rl_sub.add_parser("set", help="set <agent> <role> [--reports a,b]")
    rl_set.add_argument("agent")
    # NO `choices=` (GitHub #37). argparse would reject a deployment-declared role
    # before any code could look it up, which would make [roles.<name>] decorative:
    # the file would parse, the role would exist, and the command to use it would
    # refuse with "invalid choice". The validation lives in tier.plan_role_set,
    # against the deployment's catalog, and names the roles that DO exist.
    rl_set.add_argument("role", metavar="ROLE",
                        help="worker | lead | administrator, or any role this "
                             "deployment declares under [roles.<name>]")
    rl_set.add_argument("--reports", default="", help="comma-separated reports for a lead/administrator")
    rl_set.add_argument("-n", "--dry-run", action="store_true")
    rl_sync = rl_sub.add_parser("sync", help="materialize the crew cards FROM a source")
    rl_sync.add_argument("-n", "--dry-run", action="store_true", help="show the diff, write nothing")
    rl_sync.add_argument("--force", action="store_true", help="sync even if it restructures LIVE agents")
    # aegis-t4eve: the tier comes FROM a source, and sync says which one answered.
    # Omitted => ontology-first, file-fallback. Named => never silently substituted.
    rl_sync.add_argument("--from", dest="from_source", default=None,
                         metavar="{quipu|file:<path>}",
                         help="where the hierarchy comes from: 'quipu' or "
                              "'file:<path.ttl|yaml|json>'. Default: quipu, "
                              "falling back to a hierarchy file only if the "
                              "graph cannot be read (and it tells you)")

    # The `role set ...` alias lived here and is GONE (deprecated 2026-07-24,
    # removed after the one-week window). `st roles set` is the spelling. Deletion
    # is what lands the count drop: consolidating and keeping an alias held the
    # surface at 19, because an alias IS a top-level command to argparse and to
    # anyone reading `st --help`.

    nw = sub.add_parser("new", help="create an agent from a card")
    nw.add_argument("agent")
    nw.add_argument("-n", "--dry-run", action="store_true")

    it = sub.add_parser("init",
                        help="scaffold a NEW deployment: asks a few questions, "
                             "then writes the store, the crew cards, their hooks "
                             "and shantytown.toml")
    it.add_argument("--admin", default=None,
                    help="the administrator's name (skips that question)")
    it.add_argument("--crew", default=None,
                    help="comma-separated worker names (skips that question)")
    it.add_argument("--workspaces", default=None, metavar="DIR",
                    help="parent directory for agent workspaces — each agent gets "
                         "DIR/<name>. Omitted: agents launch in the current dir")
    it.add_argument("--mode", default=None, choices=["lite", "heavy"],
                    help="the startup mode to write into the config (default lite)")
    it.add_argument("--hibernate", action="store_true",
                    help="let the administrator go quiet when there is nothing "
                         "to dispatch (default: off)")
    it.add_argument("-y", "--yes", action="store_true",
                    help="ask NOTHING: take the flags and the defaults. Required "
                         "when stdin is not a terminal, so a scripted init can "
                         "never block on a prompt")
    it.add_argument("--force", action="store_true",
                    help="scaffold into a store that already has crew cards or a "
                         "config (never overwrites a card; only fills gaps)")
    it.add_argument("-n", "--dry-run", action="store_true",
                    help="ask the questions, show every path it would write, "
                         "write nothing")

    sr = sub.add_parser("start",
                        help="bring the town UP by MODE: lite (the administrator "
                             "alone) or heavy (every card). Idempotent; never attaches")
    sr.add_argument("agent", nargs="*",
                    help="start exactly these agents (a card name each). The "
                         "mode's crew when omitted.")
    sr.add_argument("--mode", default=None,
                    help="which mode's crew to bring up. Unset: startup.mode from "
                         "<root>/shantytown.toml, else `lite`. Built-in modes are "
                         "lite (administrator only) and heavy (every non-retired "
                         "card); a config may define more.")
    sr.add_argument("-n", "--dry-run", action="store_true",
                    help="say who WOULD start, and who is already up. Launches "
                         "nothing, clones nothing.")

    st = sub.add_parser("stop", help="stop it")
    st.add_argument("agent")
    st.add_argument("-n", "--dry-run", action="store_true")
    st.add_argument("--reason", default="", metavar="TEXT",
                    help="why, recorded with the stop (GitHub #29). A deliberate "
                         "stop is INTENT, not a fault: `st crew` and the "
                         "administrator's drain report it as such instead of "
                         "demanding a re-dispatch. It does NOT retire the card — "
                         "`st tend` still respawns it; use `st tend --retire` for "
                         "\"and do not bring it back\".")

    ss = sub.add_parser("stats", help="what the crew actually did: files, "
                                      "skills, tokens, activity (local store)")
    ss.add_argument("agent", nargs="?",
                    help="one agent's numbers; the whole crew if omitted")
    ss.add_argument("--files", action="store_true",
                    help="list the files an agent touched (needs agent)")
    ss.add_argument("--since", type=float, default=24.0, metavar="HOURS",
                    help="window in hours (default 24)")

    lg = sub.add_parser("log", help="what happened")
    lg.add_argument("agent", nargs="?")

    ib = sub.add_parser("inbox",
                        help="put a message in an agent's inbox (send-keys; -d persists), "
                             "or read your own")
    ib.add_argument("agent", nargs="?",
                    help="the recipient when sending; whose inbox when reading "
                         "(defaults to $SHANTY_AGENT)")
    ib.add_argument("message", nargs="*")
    ib.add_argument("-d", "--durable", action="store_true",
                    help="must-survive: deliver to the recipient's INBOX (a bead "
                         "on the aegis store with --backend beads), then "
                         "best-effort live send. Default is ephemeral send-keys.")
    ib.add_argument("--count", action="store_true",
                    help="print ONLY the number of unread messages. A READ: it "
                         "marks nothing read")
    ib.add_argument("--read", action="store_true",
                    help="ACK: mark my unread messages read. The explicit act — "
                         "listing and counting never do this")
    ib.add_argument("-n", "--dry-run", action="store_true")

    tk = sub.add_parser("task", help="create a work item")
    tk.add_argument("title", nargs="+")
    tk.add_argument("-a", "--assignee")
    tk.add_argument("-n", "--dry-run", action="store_true")

    cx = sub.add_parser("context", help="what code should I be looking at?")
    cx.add_argument("query", nargs="+")
    cx.add_argument("-b", "--budget", type=int, default=5)
    cx.add_argument("--repo", help="restrict to one indexed repo")
    cx.add_argument("--mode", default="hybrid", choices=["hybrid", "semantic", "keyword"])
    cx.add_argument("--none", action="store_true",
                    help="use the none-adapter (the leak test: harness works without bobbin)")

    dr = sub.add_parser("doctor", help="what tools are installed, what's stale, what's missing")
    dr.add_argument("tool", nargs="?", help="check one tool; all if omitted")
    dr.add_argument("--install", action="store_true",
                    help="install/upgrade the missing or stale tools (refuses if a toolchain is absent)")
    dr.add_argument("-n", "--dry-run", action="store_true",
                    help="with --install: show the plan, run nothing")
    dr.add_argument("--no-latest", action="store_true",
                    help="skip the release check (offline/fast) — detect local state only")

    # The `project` alias lived here and is GONE — see the `role` note above.
    # `st roles sync` is the spelling.

    td = sub.add_parser("tend", help="supervise the crew: respawn what DIED, "
                                     "never what was RETIRED")
    td.add_argument("--install", action="store_true",
                    help="install the systemd --user timer that runs a pass")
    td.add_argument("--uninstall", action="store_true",
                    help="remove the timer (only if st tend wrote it)")
    td.add_argument("--status", action="store_true",
                    help="is it installed, and when did a pass last run?")
    td.add_argument("--retire", metavar="AGENT",
                    help="mark an agent DELIBERATELY stopped — tend will never "
                         "respawn it (durable: it lives on the card)")
    td.add_argument("--unretire", metavar="AGENT",
                    help="undo --retire; the agent is tended again. REFUSES if "
                         "the card could not be launched into its own tree, or "
                         "would launch in MANUAL MODE — un-retiring arms it for "
                         "unattended respawn, and this is the last moment "
                         "anyone is watching")
    td.add_argument("--force", action="store_true",
                    help="with --unretire: arm the card anyway, past a "
                         "launchability refusal. The reason is still printed")
    td.add_argument("--interval", default="5min",
                    help="with --install: how often a pass runs (default 5min)")
    td.add_argument("--target", type=int, metavar="N",
                    help="respawn only toward N LIVE agents instead of the whole "
                         "roster. Scale-UP on loss and nothing else: it never "
                         "stops a surplus, because choosing who a fleet should "
                         "consist of is judgment and belongs to the "
                         "administrator, not to a supervisor. Fills the tier from "
                         "the root down, and reports the agents it held back.")
    td.add_argument("--loop", type=int, metavar="SECS",
                    help="run a pass every SECS forever — the blocked-worker "
                         "heartbeat. A blocked worker is pushed to "
                         "its coordinator within one interval, on its own.")
    td.add_argument("--reauth", action="store_true",
                    help="relaunch every AUTH-DEAD agent (login expired) in one "
                         "command — run it AFTER the operator re-logs in; a "
                         "relaunch re-reads the refreshed shared credential")
    td.add_argument("-n", "--dry-run", action="store_true",
                    help="say what would be respawned; touch NOTHING")

    at = sub.add_parser("attach", help="attach to a crew member by name — "
                                       "STARTING them first if they are down "
                                       "(socket + pane resolved for you)")
    at.add_argument("agent", nargs="?",
                    help="whose pane; defaults to the administrator (the coordinator)")
    at.add_argument("-r", "--read-only", action="store_true",
                    help="observe only — no keystroke can land in their work")
    at.add_argument("--no-start", action="store_true",
                    help="do NOT launch a down agent: refuse instead. The pure "
                         "observer's flag — for a script that wants to attach to "
                         "whatever is already running and must never create a "
                         "session as a side effect.")

    ib2 = sub.add_parser("input",
                         help="what is in an agent's input box: EMPTY | TYPED | "
                              "GHOST — and clear or dismiss it. NEVER submits.")
    ib2.add_argument("agent", help="whose input box")
    ib2_g = ib2.add_mutually_exclusive_group()
    ib2_g.add_argument("--show", action="store_true",
                       help="classify the box and print the SGR evidence (default)")
    ib2_g.add_argument("--clear", action="store_true",
                       help="clear TYPED text. REFUSES on a ghost-only box — a "
                            "suggestion's buffer is already empty, and "
                            "'clearing' one teaches the wrong model")
    ib2_g.add_argument("--dismiss", action="store_true",
                       help="dismiss the suggestion (Escape). Also the way to "
                            "CANCEL a tool call an agent is stopped on — Escape "
                            "is that key too, so there is no `st cancel`")

    ak = sub.add_parser("ask",
                        help="print the QUESTION an agent is blocked on: the "
                             "prompt, the command being approved, and the "
                             "numbered options verbatim. Read-only.")
    ak.add_argument("agent", help="who is blocked")

    aw = sub.add_parser("answer",
                        help="select option N on an agent's blocking picker, by "
                             "NUMBER. Refuses on a pane that is not on one.")
    aw.add_argument("agent", help="who to answer")
    aw.add_argument("n", type=int, metavar="N",
                    help="the option number, as `st ask` printed it")

    db = sub.add_parser("dashboard", help="a live, self-refreshing view of an "
                                          "admin's tier (roster/state/work)")
    db.add_argument("admin", nargs="?",
                    help="whose tier; defaults to the administrator")
    db.add_argument("--interval", type=int, default=5, metavar="SECS",
                    help="refresh every SECS (default 5)")
    db.add_argument("--once", action="store_true",
                    help="render one snapshot and exit (no refresh loop)")

    sb = sub.add_parser("subscribe",
                        help="watch quipu entity events; route assigned workflows to the admin")
    sb.add_argument("--once", action="store_true",
                    help="poll one batch and exit (default: loop)")
    sb.add_argument("--interval", type=float, default=10.0,
                    help="poll interval in seconds when looping")
    sb.add_argument("--server", default=None,
                    help="quipu server (default $QUIPU_SERVER)")

    wt = sub.add_parser("worktree",
                        help="provision (or gc) an agent's isolated worktree off "
                             "a SHARED project repo — so agents never share an "
                             "index/HEAD")
    wt.add_argument("repo",
                    help="the shared checkout: a path, or a bare name under "
                         "$GT_ROOT (~/gt), e.g. `quipu` -> ~/gt/quipu")
    wt.add_argument("agent", nargs="?",
                    help="whose worktree; defaults to $SHANTY_AGENT")
    wt.add_argument("--gc", action="store_true",
                    help="remove the worktree IFF unchanged — never discards "
                         "uncommitted or unpushed work")

    return ap


def _parse_args(argv: list[str] | None):
    """parse_args, but tolerant of a flag that appears BEFORE a variadic positional
    in a subcommand — e.g. `inbox ian -n hi`. Plain argparse strands the trailing
    positional (`unrecognized arguments: hi`): a `nargs="*"` positional, once
    matched, does not reopen after an optional. argparse's parse_intermixed_args
    handles exactly this, but it does NOT support subparsers — so detect the
    stranded case (parse_known_args leaves extras) and re-run the CHOSEN subparser
    with intermixed parsing. The fast path (no extras) is unchanged.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    ns, extra = parser.parse_known_args(argv)
    if not extra:
        return ns
    subs = next((ac for ac in parser._actions
                 if isinstance(ac, argparse._SubParsersAction)), None)
    cmd = getattr(ns, "cmd", None)
    if subs is None or cmd not in subs.choices:
        return parser.parse_args(argv)          # not our case — let argparse report
    sub_argv = argv[argv.index(cmd) + 1:]
    # Re-parse the subcommand's args into a FRESH namespace (a populated one makes
    # parse_intermixed_args warn), then copy the subcommand values onto `ns`, which
    # already carries the globals + cmd from the top parse_known_args.
    fresh = subs.choices[cmd].parse_intermixed_args(sub_argv)
    for key, value in vars(fresh).items():
        setattr(ns, key, value)
    return ns


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv)
    # RESOLVE THE STORE ONCE, here, so every handler downstream sees a real Path
    # and none of them re-derives one. `how` rides along so a surface that needs
    # to explain an empty or surprising store can say which leg answered.
    a.root, a.root_how = resolve_root(a.root, discover=(a.cmd != "init"))
    _warn_if_no_store(a)

    if a.cmd == "anchor":
        return _cmd_anchor(a)
    if a.cmd == "go":
        return _cmd_go(a)
    if a.cmd == "crew":
        return _cmd_crew(a)
    if a.cmd == "roles":
        # aegis-fagvi: one noun, three verbs. `set`/`sync` reuse the same
        # handlers as the `role`/`project` aliases so behaviour is identical.
        roles_sub = getattr(a, "roles_sub", None)
        if roles_sub == "set":
            return _cmd_role(a)
        if roles_sub == "sync":
            return _cmd_project(a)
        return _cmd_roles(a)  # bare `roles`, `roles --check`, or `roles show`
    if a.cmd == "inbox":
        return _cmd_inbox(a)
    if a.cmd == "task":
        return _cmd_task(a)
    if a.cmd == "context":
        return _cmd_context(a)
    if a.cmd == "doctor":
        return _cmd_doctor(a)
    if a.cmd == "stop":
        return _cmd_stop(a)
    if a.cmd == "log":
        return _cmd_log(a)
    if a.cmd == "stats":
        from . import stats as stats_mod
        if a.files:
            if not a.agent:
                print("st stats --files needs an agent", file=sys.stderr)
                return 2
            return stats_mod.stats_files(a.root, a.agent, since_h=a.since)
        return stats_mod.stats_report(a.root, a.agent, since_h=a.since)
    if a.cmd == "new":
        return _cmd_new(a)
    if a.cmd == "init":
        return _cmd_init(a)
    if a.cmd == "start":
        return _cmd_start(a)
    if a.cmd == "tend":
        return _cmd_tend(a)
    if a.cmd == "attach":
        return _cmd_attach(a)
    if a.cmd == "input":
        return _cmd_input(a)
    if a.cmd == "ask":
        return _cmd_ask(a)
    if a.cmd == "answer":
        return _cmd_answer(a)
    if a.cmd == "dashboard":
        return _cmd_dashboard(a)
    if a.cmd == "subscribe":
        return _cmd_subscribe(a)
    if a.cmd == "worktree":
        return _cmd_worktree(a)
    return _not_yet(a.cmd)


def _default_settings(root: Path):
    """Resolve a card -> the settings file that wires its ROLE's hooks.

    The file is EMITTED by `role set` / #6; #5 owns the launch seam,
    not the hook-file content. So this resolver READS: it returns the path if the
    role's settings file exists, else None -> compose refuses. That refusal IS the
    invariant working — no settings, no launch, never a settings-less fallback.
    """
    def resolve(card):
        # PER-AGENT FIRST (GitHub #17). All workers sharing one file meant nothing
        # could differ per agent — so a card's own model, permissions or hooks had
        # nowhere to land. An agent file is used when it EXISTS; otherwise the
        # role file, which is what every card uses today. No file for either is
        # None, and compose REFUSES: no settings, no launch, never a fallback.
        for name in (f"agent-{card.name}.settings.json", f"{card.role}.settings.json"):
            p = Path(root) / "settings" / name
            if p.is_file():
                return str(p)
        return None
    return resolve


def _warn_if_no_store(a) -> bool:
    """Say NOTHING ANSWERED, once, before the command runs. Returns whether it did.

    THE MEASURED MISDIAGNOSIS (aegis-d94vb). CLAUDE.md tells every crew member to
    run `st anchor <you>` from their own workspace. There, st refused with:

        refused: no such agent: malcolm
          (looked in <workspace>/.shanty/crew/malcolm.json)

    which reads as a broken identity or a bad clone — so the natural recovery is to
    stop using st, one agent at a time. The actual cause is that NOTHING answered
    the where-is-the-store question: no --root, no $SHANTY_ROOT, no `.shanty` on the
    way up, and no pointer file. The agent's own name was never the problem.

    WHY THE WALK-UP CANNOT COVER THIS, and why the answer is the pointer: crew
    workspaces are SIBLINGS of the checkout, not children of it
    (~/gt/<rig>/crew/<agent> vs ~/gt/shantytown/.shanty). Walking up from a sibling
    tree never reaches the store, at any depth. The pointer exists for exactly this
    shape and `st init` writes it — a deployment that predates init has none, which
    is the state this fires in.

    A WARNING, NOT A REFUSAL, and before the command rather than after: commands
    that need no store still work, and the ones that do now fail with the cause
    already on screen instead of an identity error to be misread first.
    """
    from .deployment import BY_CWD, pointer_path
    if getattr(a, "cmd", None) == "init":
        return False                       # init is how a store comes to exist
    if a.root_how != BY_CWD or Path(a.root).is_dir():
        return False
    print(f"  ⚠ no store found. Nothing answered where it is: no --root, no "
          f"$SHANTY_ROOT, no .shanty walking up from here, and no pointer at "
          f"{pointer_path()}. Falling back to {a.root}, which does not exist — so "
          f"anything needing the crew will say 'no such agent: <name>', and the "
          f"name is not the problem. Fix with any one of: `st --root "
          f"<path>/.shanty ...`, `export SHANTY_ROOT=<path>/.shanty`, or write that "
          f"path into {pointer_path()} (one line). NOTE a crew workspace is a "
          f"SIBLING of the checkout, so the walk-up can never find it from there.",
          file=sys.stderr)
    return True


def _catalog(a):
    """The deployment's ROLE CATALOG (traits.py, GitHub #37) — the built-in three
    plus whatever `[roles.<name>]` declares, with `[precedence.<axis>]`'s ranks.

    load_or_default, not load: this feeds a REFUSAL path (`role set` validating a
    role), and a config typo must produce the config error at the top of a command,
    not a role that mysteriously stopped existing. An unreadable file therefore
    yields the built-in three, which is exactly what a deployment that declares
    nothing gets — a degradation to the previous behaviour, never to a wider one.
    """
    cfg, err = config.load_or_default(getattr(a, "root", None) or ".")
    if err:
        print(f"  ⚠ {err} — using the built-in roles", file=sys.stderr)
    return cfg.catalog()


def _stops(a) -> FilesStops:
    """The deliberate-stop store (stopped.py). Beside `launched/` because it is the
    same KIND of thing: per-launch runtime state, not identity."""
    return FilesStops(Path(a.root) / "stopped")


def _launched_now(a, card_name: str, settings_path=None) -> None:
    """ONE place that records 'this agent is now running'.

    Both halves must happen together and neither is allowed to be forgotten at a
    new launch site: stamp what it launched on, and DROP any deliberate-stop record
    (the stop is over — a record left behind would make its next real crash read as
    intentional, which is a fabricated decision and worse than no record).
    """
    if settings_path:
        _launches(a).record(card_name, settings_path)
    _stops(a).forget(card_name)


def _launches(a) -> FilesLaunches:
    """The launch-stamp store for this invocation. Beside events/."""
    return FilesLaunches(Path(a.root) / "launched")


# Not looked at. A down agent's settings verdict is not "current" and not
# "stale" — we did not ask, and rounding that to either is the bug nipg is about.
NOT_LIVE = "—"


def _settings_verdict(launches, name: str, live: bool) -> str:
    """THE definition of one agent's settings verdict: is it running the file we
    currently believe is deployed?

    Only a LIVE agent can be running stale settings. A down agent has no loaded
    settings to be stale and reads the current file when it next starts, so its
    verdict is NOT_LIVE — reporting on it would be noise that buries the real hits.

    `live` is passed IN rather than probed here on purpose: `crew` has already
    established it while building its table, and re-probing would double the tmux
    calls on the command an operator runs most.
    """
    return launches.verdict(name) if live else NOT_LIVE


def _reach_buckets(verdicts) -> tuple[list[str], list[str]]:
    """(stale, unknown) from an iterable of (name, verdict). Pure, and it is the
    ONLY place a verdict becomes a bucket.

    This exists because `crew` (which reports when asked) and `role set` (which
    reports when it CAUSES the drift) must never disagree. Two copies of this
    could, and the first symptom would be one surface calling an agent healthy
    while the other called it stale — the exact ambiguity aegis-nipg is about.
    That divergence was not hypothetical: this rule was written twice, once here
    and once inline in `_cmd_crew`, and unifying them is what this change is.
    """
    v = list(verdicts)
    return ([n for n, x in v if x == STALE],
            [n for n, x in v if x == UNKNOWN])


def _settings_reach(a, panes, agents) -> tuple[list[str], list[str]]:
    """(stale, unknown) among the LIVE agents — who is NOT on the current file.

    For callers that have NOT already computed liveness (`role set`). `crew`
    builds the same buckets from the verdicts it is already rendering, via
    _reach_buckets, so both surfaces share the verdict rule AND the bucket rule.
    """
    launches = _launches(a)
    return _reach_buckets(
        (ag.name,
         _settings_verdict(launches, ag.name,
                           bool(ag.pane and panes.exists(ag.pane))))
        for ag in sorted(agents, key=lambda x: x.name))


def _runtime(a, panes):
    """The runtime for this invocation. Claude Code is first-class; a second
    runtime (codex/opencode) would be selected here and its capability gate
    (runtime.require_capability) would refuse a lead it cannot host."""
    return ClaudeRuntime(panes, _default_settings(a.root), root=a.root)


def _observe_live(runtime, panes, session) -> bool:
    """Poll capture() until the runtime is OBSERVED live, or give up (-> 2).

    This proves the PROCESS came up — NOT that hooks fired. The hooks guarantee is
    enforced at COMPOSITION (the string provably carried --settings), not by pane
    inspection (arnold: that is GT's unanswerable 'did I get primed?'). A green
    verify here must never be read as 'hooks registered'."""
    answered = False
    for _ in range(_LIVE_ATTEMPTS):
        screen = panes.capture(session)
        if runtime.is_live(screen):
            return True
        # THE FOLDER-TRUST GATE (measured 2026-07-20). A fresh workspace blocks on
        # "Do you trust the files in this folder?" — before the ready UI, and NOT
        # bypassed by --dangerously-skip-permissions. Answer it ONCE, and say so:
        # the card already elected this workspace, so this re-affirms a decision
        # the operator made when they wrote the card; it does not make a new one.
        # Answering silently would be the wrong trade — the point is that a human
        # can see the launcher did it.
        if not answered and getattr(runtime, "trust_prompt", None) and \
                runtime.trust_prompt(screen):
            print(f"  first-run TRUST prompt in {session} — accepting the "
                  f"workspace the card already elected.", file=sys.stderr)
            panes.send(session, runtime.trust_answer())
            answered = True
        if _LIVE_DELAY:
            time.sleep(_LIVE_DELAY)
    return False


def _session_for(card) -> str:
    """The session an agent launches into.

    Fallback name when the card names no pane. Deliberately prefixed `st-`: a
    session st creates must never collide with one somebody else's tooling already
    launched under a name we'd also pick.

    ONE resolver because THREE surfaces now need the same answer — `new` launches
    into it, `attach` has to attach to the session it just asked for, and
    `bootstrap` reports it. Two of those computing it separately is a bug where
    st launches an agent and then cannot find it.
    """
    return card.pane or f"st-{card.name}"


def _cmd_new(a) -> int:
    """new <agent> — bring up a HOOKED agent session (#5).

    The single-agent PRIMITIVE, and it REFUSES a live session (the clobber guard
    below) — `st new` on a running agent is a mistake, not an idempotent no-op.
    The boot command that wants "make it so, whatever is currently up" is
    `st start`; the difference is written up in bootstrap.py.
    """
    panes = _panes(a)
    try:
        card = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    runtime = _runtime(a, panes)
    return _launch(a, card, panes, runtime, dry_run=a.dry_run)


def _launch(a, card, panes, runtime, *, dry_run: bool = False) -> int:
    """LAUNCH ONE AGENT. The whole seam: refuse-first, then workspace, then kit,
    then session, then verify. Returns 0 (up + hooks verified) / 1 (refused) /
    2 (launched but not verified).

    new_session (empty pane) -> Runtime.start (compose w/ --settings, send) ->
    verify PROCESS live -> 0/1/2. The order is deliberate: everything that can
    REFUSE (unknown agent, capability, settings) runs BEFORE any tmux mutation, so
    a refusal creates nothing (arnold: 'write nothing, launch nothing').

    ONE launcher, shared by `new`, `start` and `attach`, so none of them can
    acquire a cheaper version of it: the pre-flight order above, the workspace
    guard, the equipped-or-not-created refusal and the hooks verification are each
    load-bearing, and a second launcher would have to re-earn all four.
    """
    session = _session_for(card)
    # PRE-FLIGHT: compose refuses capability/settings/unknown-harness BEFORE we
    # touch tmux. UnknownHarness is a REFUSAL by design (harness.py) but was not in
    # this except, so a card naming a harness we cannot host exited with a
    # traceback instead of the `refused:` exit-1 path every other refusal uses
    # (aegis-85ox). It belongs here with the others: same seam, same outcome.
    try:
        launch = runtime.compose(card)
    except (CapabilityError, SettingsError, harness_mod.UnknownHarness) as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if dry_run:
        print(f"  would launch in {session}: {launch}")
        return OK
    # WORKSPACE: the launch string `cd`s into card.workspace, so the
    # directory has to BE there. Ensure it (clone if absent, leave alone if
    # present) or REFUSE — before any tmux mutation, so a refusal still creates
    # nothing. Deliberately AFTER dry-run: dry-run must not clone.
    try:
        ensure_workspace(card)
    except WorkspaceError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    # KEEP CURRENT ON RELAUNCH (aegis-4zld): a fresh session must start on a
    # current tree — launch is the other safe pull moment (nothing is running
    # in the workspace yet). ff-only + kit-preserving; a refused pull is LOUD
    # and never blocks the launch (stale-but-working beats no agent).
    if card.workspace:
        if err := _refresh_clone(card.workspace):
            print(f"  ⚠ workspace not brought current (ff-only pull refused: "
                  f"{err.splitlines()[0]}) — launching on the existing tree.",
                  file=sys.stderr)
    # EQUIPPED OR NOT CREATED. A workspace is not a provisioned agent: a fresh
    # clone has no .mcp.json (it is uncommitted BY DESIGN — it carries a bearer
    # token), so an agent launched from one has no code search, no graph and no
    # ops tools, and looks identical to a healthy one on every surface. Five
    # agents worked P1 beads for a night that way. Refuse instead.
    try:
        servers = provision_ws(card, Path(a.root))
    except ProvisionError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if servers:
        # Say what it CAN reach, parsed back out of the file we wrote — "the file
        # is there" was true for every broken render.
        print(f"  provisioned {len(servers)} MCP server(s): {', '.join(servers)}")
    elif card.workspace:
        # No template = this store defines no kit. Say it, every time: silence
        # here is indistinguishable from the bug (an agent that launches with no
        # tools and looks fine), and a deleted template would restore it.
        print(f"  note: no provisioning template at {prov_mod.provision_dir(a.root)}"
              f" — launching {card.name} with NO MCP kit.")
    if card.workspace:
        # Skills, same claim shape as the servers: names read back out of the
        # links we just made, not "the directory is there". A crew that shipped
        # 22 correct skills to a runtime loading none had every other signal green.
        linked = prov_mod.skills_linked(card.workspace)
        if linked:
            print(f"  linked {len(linked)} skill(s): {', '.join(linked)}")
    # Clobber guard: never replace a live agent (RAISES if the session exists).
    # cwd=card.workspace so the SESSION starts in the agent's own directory, not
    # the launcher's (GitHub #18) — the launch string also cd's, but a pane whose
    # own cwd is the checkout misleads every later shell opened in it.
    try:
        panes.new_session(session, cwd=card.workspace)
    except RuntimeError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    # Deliver through the seam. Panes stays runtime-blind — sees a finished string.
    runtime.start(card, session)
    # STAMP WHAT IT LAUNCHED ON, before we report anything. The
    # agent has now read its --settings and will never read them again; this
    # records which bytes that was, so a later rewrite of the file is DETECTABLE
    # rather than silently unapplied. Best-effort on purpose: a stamp that cannot
    # be written leaves the agent reporting `unknown`, which is the truth. It must
    # never turn a successful launch into a failure.
    _launched_now(a, card.name, _default_settings(a.root)(card))
    if _observe_live(runtime, panes, session):
        return _verify_live_hooks(a, card, runtime, panes, session)
    # Not observed live. Distinguish "waiting for a human" (a first-run consent
    # prompt) from "unknown" — both are could-not-tell (2), but they need
    # different human actions (live-fire found the consent case).
    final = panes.capture(session)
    if getattr(runtime, "waiting_for_human", None) and runtime.waiting_for_human(final):
        print(f"  could not tell: {card.name} ({session}) is WAITING ON A PROMPT "
              f"(first-run consent), not up yet. Answer it: `st log {card.name}` to "
              f"see it, then `st attach {card.name}`.", file=sys.stderr)
        return CANNOT_TELL
    print(f"  could not tell: launched {card.name} but the runtime was not observed "
          f"live in {session} within the timeout. It may still be coming up; "
          f"check `st log {card.name}`.", file=sys.stderr)
    return CANNOT_TELL


def _verify_live_hooks(a, card, runtime, panes, session: str) -> int:
    """The launch is live — but is it HOOKED? (aegis-8p0j gap 1, aegis-05up.)

    THE GAP THIS CLOSES. runtime.py already states the boundary honestly:
    compose() guarantees --settings was REQUESTED; it does not guarantee hooks
    FIRED, and _observe_live only proves the PROCESS is up. So `st new` could
    print "started" and exit 0 for an agent that came up carrying no stop hooks
    at all. That is not a hypothetical shape of bug — measured 2026-07-20
    (aegis-0v97), all 8 gastown-launched crew were running RIGHT THEN with no
    stop hooks; they could not even SEND, and nothing detected it for the entire
    time it was true. `st roles --check` finds it, but only if someone runs it.
    Here it is caught at the moment of launch, by the process's own cmdline.

    Three outcomes, and the middle one is the whole point:

      hooks match the graph   -> OK. Say what was verified, not just "started".
      MEASURED missing        -> REFUSED. Loud, naming the missing direction.
      could not look (None)   -> CANNOT_TELL. Never rendered as a pass.

    WHY THIS DOES NOT KILL THE SESSION. A defective agent is left RUNNING and the
    operator is told to remove it. Two reasons: the pane is the evidence (killing
    it destroys the cmdline that proves what went wrong, which is exactly what
    made aegis-0v97 hard to see), and a launcher that reaps on a verdict is one
    bad verdict away from killing healthy agents. `st stop` already exists and is
    one command. If arnold rules teardown belongs here, it is a small change —
    but it should be a ruling, not a side effect of adding a check.
    """
    need = roles_mod.required_stop_directions(card, _registry(a).all())
    if not need:
        # NOTHING REQUIRED -> nothing to verify, and we must not manufacture
        # doubt about a requirement that does not exist. An isolated agent (no
        # lead above, no reports below) has no stop event to route in either
        # direction; reporting could-not-tell here would be a false alarm on
        # every leaf agent, and false alarms are what teach an operator to stop
        # reading the output.
        print(f"  started {card.name} ({session}) — runtime live; the graph "
              f"requires no stop directions of this agent.")
        return OK
    # cmdline is deliberately NOT a Panes protocol method (arnold's non-goal for
    # this bead: Panes gains nothing). We read it off the adapter if it has one;
    # an adapter that cannot show a process cmdline genuinely cannot answer the
    # question, and that is a cannot-tell, not a pass.
    reader = getattr(panes, "cmdline", None)
    wiring = live_wiring(session, reader) if reader else None
    if wiring is None:
        print(f"  could not tell: {card.name} ({session}) is live, but its stop "
              f"hooks could NOT be read from the running process, so it is "
              f"UNVERIFIED — not confirmed hooked. Check `st roles --check`.",
              file=sys.stderr)
        return CANNOT_TELL
    missing = need - wiring.directions
    if missing:
        # SAY WHAT IT HAS, NOT ONLY WHAT IT LACKS — dearing's aegis-0v97
        # correction (205e492), which landed on roles.py while this was in
        # flight and applies verbatim here. "NO stop hooks at all" is false as
        # English and false in the expensive direction: a process launched by a
        # foreign launcher DOES carry hooks, just not a `stop_event` direction.
        # Read literally, that string is aegis-05up — "respawn dropped
        # --settings, the rm -rf and force-push guards are gone" — a real
        # emergency that is NOT what we measured. Naming the settings path makes
        # the foreign launcher self-evident instead of alarming.
        carries = (f"stop directions {sorted(wiring.directions)}"
                   if wiring.directions else "no `stop_event` hook")
        whence = (f", its --settings is {wiring.settings_path}"
                  if wiring.settings_path
                  else ", and its launch line carries NO --settings at all "
                       "(this one IS the hookless-zombie case)")
        print(f"  FAILED: {card.name} ({session}) came up WITHOUT the stop hooks "
              f"its position requires. The live process carries {carries}"
              f"{whence}, but this agent needs {sorted(need)} — missing "
              f"{sorted(missing)}. It is running and it is broken: remove it "
              f"with `st stop {card.name}`, fix the settings it launches with, "
              f"and start it again.", file=sys.stderr)
        return REFUSED
    verified = sorted(need) if need else "none required by the graph"
    print(f"  started {card.name} ({session}) — runtime live, stop hooks VERIFIED "
          f"on the live process: {verified}.")
    return OK


# The mode label `st start <agent>...` reports: agents named on the command line
# came from no mode, and printing one would credit a config that was never read.
EXPLICIT = "explicit"


def _prompt(prompt: str, default: str) -> str:
    """One wizard question on a real terminal.

    The QUESTION goes on its own line and the cursor sits on a short `>` below it.
    Several of these questions carry a clause explaining the choice, and a long
    question with the answer typed onto its right-hand end wraps into an unreadable
    paragraph — worse when stdin is piped, where nothing echoes to break the line.

    `default` is shown in brackets; Enter accepts it. EOF (a piped stdin that ran
    out) takes the default rather than raising: an init that dies on ^D after six
    answered questions throws away work the operator already did.
    """
    suffix = f" [{default}]" if default else ""
    print(f"  {prompt}{suffix}")
    try:
        return input("  > ")
    except EOFError:
        print()
        return ""


def _cmd_init(a, *, ask=_prompt, isatty=None) -> int:
    """init — scaffold a deployment: the store, the cards, the hooks, the config.

    It WRITES THROUGH THE EXISTING SEAMS and adds no new ones: cards go in via the
    registry (which is where a card gets its generated pane), roles and stop-hook
    routing via tier.role_set, hook files via the same emitter `roles set` uses.
    Nothing here is a second way to declare a crew — every artifact is one the
    operator would otherwise have hand-written, in the same place and format.

    0 wrote it · 1 refused (a bad name, an existing store, no terminal to ask in).
    """
    import sys as _sys
    root = Path(a.root)
    isatty = isatty if isatty is not None else _sys.stdin.isatty
    crew_dir = root / "crew"

    # AN EXISTING STORE IS A REFUSAL, not a merge. A second init over a live
    # deployment is far more likely to be a mistyped --root than an intention, and
    # the cost of guessing wrong is a crew card set nobody expected. --force is the
    # way to say it on purpose; even then no existing card is overwritten.
    existing = sorted(crew_dir.glob("*.json")) if crew_dir.is_dir() else []
    cfg_exists = config.config_path(root).is_file()
    if (existing or cfg_exists) and not a.force:
        what = []
        if existing:
            what.append(f"{len(existing)} crew card(s)")
        if cfg_exists:
            what.append(config.CONFIG_NAME)
        print(f"  refused: {root} is already a deployment ({', '.join(what)}). "
              f"`st crew` to see it. To add an agent to an existing store use "
              f"`st roles set <name> <role>`; pass --force if you really mean to "
              f"scaffold over this one (no existing card is overwritten).",
              file=_sys.stderr)
        return REFUSED

    # FLAGS PRE-ANSWER QUESTIONS; --yes skips the asking entirely. A non-tty with
    # no --yes REFUSES rather than calling input() — a wizard that blocks forever
    # inside a script or a hook is worse than one that says it cannot ask.
    defaults = scaffold.Answers(
        admin=a.admin or scaffold.DEFAULT_ADMIN,
        workers=tuple(w.strip() for w in (a.crew or "").split(",") if w.strip()),
        workspaces=a.workspaces,
        mode=a.mode or config.DEFAULT_MODE,
        hibernate=bool(a.hibernate))
    try:
        if a.yes:
            answers = scaffold.make_answers(
                admin=defaults.admin, workers=defaults.workers,
                workspaces=defaults.workspaces, mode=defaults.mode,
                hibernate=defaults.hibernate)
        elif not isatty():
            print(f"  refused: stdin is not a terminal, so `st init` cannot ask "
                  f"its questions. Pass -y/--yes to take the flags and defaults "
                  f"(`st init -y --admin <name> --crew a,b`).", file=_sys.stderr)
            return REFUSED
        else:
            print("\n  st init — a few questions. Enter accepts the [default].\n")
            answers = scaffold.ask_all(ask, defaults=defaults)
    except scaffold.ScaffoldError as e:
        print(f"  refused: {e}", file=_sys.stderr)
        return REFUSED

    plan = scaffold.plan(root, answers)
    # PREVIEW THEN CONFIRM, but only for the operator who is being asked. Under
    # --yes the answers came from flags and the write log below says the same
    # thing with real paths, so a preview there is noise ahead of the truth.
    if a.dry_run or not a.yes:
        print()
        print(plan.render())
        print()
    if a.dry_run:
        print("  --dry-run: nothing written.\n")
        return OK
    if not a.yes:
        if ask("Write this?", "yes").strip().lower() in ("n", "no"):
            print("  nothing written.")
            return REFUSED
        print()
    return _init_apply(a, root, plan, answers)


def _init_apply(a, root: Path, plan, answers) -> int:
    """Write the plan. Cards first (so role_set has agents to wire), then roles +
    hooks in ONE generative operation, then the config."""
    from . import tier
    from .protocols import Agent

    for d in plan.dirs:
        (root / d).mkdir(parents=True, exist_ok=True)

    reg = FilesRegistry(root / "crew")
    for name, _role, _pane in plan.cards:
        if (root / "crew" / f"{name}.json").is_file():
            # --force reached an existing card. Leave it EXACTLY as it is: init
            # scaffolds what is missing and is never a card rewriter.
            print(f"  kept     {name:<12} (card already exists — not touched)")
            continue
        ws = f"{answers.workspaces.rstrip('/')}/{name}" if answers.workspaces else None
        reg.set(Agent(name=name, role="worker", workspace=ws))
        print(f"  card     {name:<12} {root / 'crew' / f'{name}.json'}")

    # ROLES + ROUTING through the generative op, so the cards and the stop hooks
    # cannot disagree — the reason this does not write the hierarchy itself.
    try:
        rplan = tier.role_set(reg, answers.admin, "administrator",
                             reports=list(answers.workers))
    except (LookupError, ValueError, CapabilityError) as e:
        print(f"  refused: could not wire the tier: {e}", file=sys.stderr)
        return REFUSED
    for ag in rplan.writes:
        print(f"  role     {ag.name:<12} {ag.role}"
              f"{f' -> reports_to {ag.reports_to}' if ag.reports_to else ''}")

    for path in _emit_role_settings(root, {ag.role for ag in rplan.writes}):
        print(f"  hooks    {path}")

    cfg_path = config.config_path(root)
    if cfg_path.is_file():
        print(f"  kept     {cfg_path} (already there — not overwritten)")
    else:
        cfg_path.write_text(plan.config_text)
        print(f"  config   {cfg_path}")

    # PROVE IT PARSES. The file was just generated, and a config this command
    # writes but `st start` would refuse is the worst possible handoff.
    try:
        config.load(root)
    except config.ConfigError as e:
        print(f"  ⚠ the config just written does NOT parse: {e}", file=sys.stderr)
        return CANNOT_TELL

    print()
    print(f"  ready. {len(plan.cards)} card(s), mode {answers.mode!r}.")
    print(f"    st start          # bring up mode {answers.mode!r}")
    print(f"    st attach         # the admin's pane (starts it if it is down)")
    print(f"    st crew           # who exists, who is up")
    print()
    return OK


def _boot_launcher(a, panes, runtime):
    """launch(card) -> (bootstrap verdict, why), over the ONE launcher.

    The `why` deliberately POINTS AT the launcher's own output rather than
    restating it. _launch already prints the specific refusal (a missing settings
    file, an unequipped workspace, a harness we cannot host) and a summary written
    here would be a second, shorter account of the same event — which is how a
    report ends up disagreeing with the lines directly above it.
    """
    def launch(card):
        rc = _launch(a, card, panes, runtime, dry_run=False)
        if rc == OK:
            return boot_mod.STARTED, f"launched into {_session_for(card)!r}, hooks verified"
        if rc == REFUSED:
            return boot_mod.REFUSED, "launch REFUSED — the reason is printed above"
        return (boot_mod.UNVERIFIED,
                f"session {_session_for(card)!r} exists but the runtime was NOT "
                f"observed live — see above; `st log {card.name}`")
    return launch


def _start_roster(a, cfg, agents) -> tuple[list[str], str, list[str], str | None]:
    """(names, mode label, skipped-retired, refusal) — WHO `st start` will bring up.

    Pure resolution, separated from the launching so a test can pin the selection
    rules (retired exclusion, tier order, unknown names) without a runtime. The
    refusal string is returned rather than printed for the same reason.
    """
    if a.agent:
        # EXPLICIT NAMES WIN, AND THE MODE IS NOT CONSULTED. Naming both is
        # REFUSED rather than silently resolved: `st start --mode heavy sattler`
        # has two readings ("heavy, plus sattler" / "sattler, from the heavy set")
        # and picking one quietly means the operator who meant the other brings up
        # a fleet they did not ask for. Ambiguity about how many agents to bill is
        # not the place to guess.
        if a.mode:
            return [], "", [], (
                f"--mode {a.mode!r} and explicit agents ({', '.join(a.agent)}) are "
                f"two different asks — a mode IS a crew list. Pick one: "
                f"`st start --mode {a.mode}` for the mode's crew, or "
                f"`st start {' '.join(a.agent)}` for exactly these.")
        selectors, label = list(a.agent), EXPLICIT
    else:
        try:
            selectors = cfg.selectors(a.mode)
        except config.ConfigError as e:
            return [], "", [], str(e)
        label = a.mode or cfg.mode

    roster = config.resolve_crew(selectors, agents)
    if roster.unknown:
        return [], label, [], (
            f"no card for: {', '.join(sorted(roster.unknown))}. A selector is `*`, "
            f"a role ({', '.join(config.ROLE_SELECTORS)}) or an agent name; "
            f"`st crew` lists the cards that exist.")
    if not roster.names:
        # SELECTED NOBODY. The common shape is a `lite` boot on a store with no
        # administrator card — and the useful message names the store, not the
        # abstraction, because the fix is `st roles set <agent> administrator`.
        detail = (f" (the roster has {len(agents)} card(s), and none of them "
                  f"matched)" if agents else " (the roster is EMPTY — no cards)")
        retired_note = (f" {len(roster.skipped_retired)} matching card(s) are "
                        f"RETIRED and are never started: "
                        f"{', '.join(sorted(roster.skipped_retired))}."
                        if roster.skipped_retired else "")
        return [], label, roster.skipped_retired, (
            f"mode {label!r} selects {selectors} and that matched NO agent to "
            f"start{detail}.{retired_note} An administrator is what `lite` boots: "
            f"`st roles set <agent> administrator` writes one.")
    return roster.names, label, roster.skipped_retired, None


def _cmd_start(a) -> int:
    """start [agent...] [--mode M] — bring the town up. The boot command.

    Why this is a command and not `st new` in a loop, or a flag on `st tend`, is
    argued in bootstrap.py. Here, the three things the exit code has to mean:

      0  every selected agent is UP (launched now, or already running)
      1  REFUSED before launching anything — a bad mode, a bad config, a name
         with no card. A refusal launches NOTHING; it is not a partial boot.
      2  the pass ran and some agent is NOT known to be up (refused mid-flight,
         or launched-but-unverified). A boot you cannot script on is not a boot.
    """
    panes = _panes(a)
    try:
        agents = _registry(a).all()
    except Exception as e:                       # noqa: BLE001 — registry unreachable
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL

    # CONFIG FIRST, and a malformed file REFUSES (config.load, not
    # load_or_default): this command launches agents, and starting the wrong SET
    # because a key was misspelled is worse than starting nothing.
    try:
        cfg = config.load(a.root)
    except config.ConfigError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED

    names, label, skipped, refusal = _start_roster(a, cfg, agents)
    if refusal:
        print(f"  refused: {refusal}", file=sys.stderr)
        return REFUSED

    reg = _registry(a)
    cards = [reg.get(n) for n in names]
    if label == EXPLICIT:
        print(f"  {len(cards)} agent(s) named on the command line: {', '.join(names)}")
    else:
        where = f"{cfg.path}" if cfg.path else "the built-in defaults (no config file)"
        print(f"  mode {label!r} from {where} — {len(cards)} agent(s): "
              f"{', '.join(names)}")
    runtime = _runtime(a, panes)
    boot = boot_mod.Bootstrapper(
        panes, launch=_boot_launcher(a, panes, runtime),
        log=lambda msg: print(f"  {msg}", file=sys.stderr))
    rep = boot.bring_up(cards, mode="" if label == EXPLICIT else label,
                        dry_run=a.dry_run, skipped_retired=skipped)
    print()
    print(rep.render())
    print()
    # WHERE THE OPERATOR GOES NEXT. A boot whose whole point is "start the admin"
    # should not make them look up how to reach it.
    if not a.dry_run and rep.up():
        head = rep.up()[0]
        print(f"  attach: `st attach {head}`   ·   roster: `st crew`")
    _report_hibernate(cfg)
    return OK if rep.healthy() else CANNOT_TELL


def _report_hibernate(cfg) -> None:
    """Say the hibernate policy in force, on the command that just applied the
    rest of the config. An operator reading `st start` output is holding the
    config in their head at that exact moment, and a policy that only ever
    manifests as "the admin went quiet at 3am" is one nobody connects to a file."""
    h = cfg.hibernate
    if not h.enabled:
        return
    valve = (f", or after {h.max_quiet_minutes} min of quiet"
             if h.max_quiet_minutes else "")
    print(f"  hibernate ON: the administrator goes quiet when nothing is urgent "
          f"and there is nothing to dispatch. Rule Zero still overrides it. It "
          f"wakes on a push{valve}.")


def _cmd_stop(a) -> int:
    """stop <agent> — kill the agent's session (#5).

    kill_session is idempotent, so this is honest about the two states: an agent
    that is not running is ALREADY the desired end state (exit 0, "was not
    running"); a running one is killed and VERIFIED gone (exit 0, "stopped") or,
    if it is somehow still there after the kill, exit 2 — never a cheerful "done"
    over a session that is still alive.
    """
    panes = _panes(a)
    try:
        agent = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    session = agent.pane      # the address; None/absent = not running
    if not session or not panes.exists(session):
        print(f"  {a.agent} was not running.")
        return OK
    # OWNERSHIP GUARD. The session is live — but st only reaps what
    # st launched. The registry pane names can COLLIDE with sessions somebody
    # else already started under the same name, so on a shared socket
    # `st stop ellie` would kill a session st never launched.
    # A name match is not permission to kill: refuse unless st owns the session.
    if not panes.owns(session):
        print(f"  refused: {a.agent} ({session}) was not launched by st — refusing "
              f"to stop a session st does not own. A name match is not permission "
              f"to kill (the registry pane names collide with the live crew).",
              file=sys.stderr)
        return REFUSED
    # SECOND FACTOR: the LAUNCH STAMP. SHANTY_OWNED alone lied once, live
    # (wn7g pilot's negative control): tend's pre-gate respawns created
    # another orchestrator's crew sessions, so those panes carry the marker
    # while the OTHER fleet operates them — `st stop ellie` dry-ran straight
    # to "would kill" against the live foreign session. An st-MANAGED agent
    # has a launch stamp (st new writes it; st stop forgets it); a session st
    # merely created once does not. No stamp while other stamps exist =
    # created-but-not-managed = refuse. Empty store = cannot tell = the env
    # marker alone decides, as before (fresh deployments must still reap).
    try:
        launches = _launches(a)
        unstamped = (launches.get(a.agent) is None
                     and any(launches.root.glob("*.json")))
    except Exception:  # noqa: BLE001 — a broken store must not wedge the reap
        unstamped = False
    if unstamped:
        print(f"  refused: {a.agent} ({session}) carries st's session marker but "
              f"has NO launch stamp — st created this session once but does not "
              f"manage the agent in it (another orchestrator does). Refusing.",
              file=sys.stderr)
        return REFUSED
    if a.dry_run:
        print(f"  would: kill-session {session}")
        return OK
    panes.kill_session(session)
    if panes.exists(session):
        print(f"  could not tell: killed {session} but it is still there",
              file=sys.stderr)
        return CANNOT_TELL
    # The stamp described a LIVE launch; that launch is now gone. Leaving it would
    # let `st crew` report `current` for the settings of a process that no longer
    # exists — a clean bill of health for nobody.
    _launches(a).forget(a.agent)
    # RECORD THE INTENT (GitHub #29). This is the only place st learns that a pane
    # went down BECAUSE SOMEBODY DECIDED SO. Without it the drain and the roster
    # read a deliberate shutdown and a crash identically, and the measured
    # consequence was nine agents an operator had just been told to stop coming
    # back as `re-dispatch <agent> — STOPPED`.
    _stops(a).record(a.agent, time.time(),
                     by=os.environ.get("SHANTY_AGENT", ""),
                     reason=getattr(a, "reason", "") or "")
    print(f"  stopped {a.agent} ({session}) — recorded as DELIBERATE. "
          f"`st tend` will still respawn it; `st tend --retire {a.agent}` is how "
          f"you say do not bring it back.")
    return OK


def _cmd_log(a) -> int:
    """log [agent] — what happened, = capture() on the session pane (arnold's #5
    ruling: log needs NOTHING new, it rides capture). Read-only."""
    panes = _panes(a)
    if not a.agent:
        print("  refused: log <agent> — whose log?", file=sys.stderr)
        return REFUSED
    try:
        agent = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    session = agent.pane
    if not session or not panes.exists(session):
        print(f"  {a.agent} is not running — no session to read.")
        return OK
    print(panes.capture(session))
    return OK


def _cmd_doctor(a) -> int:
    """st doctor [tool] [--install] [--dry-run] [--no-latest].

    Detect is the default and touches nothing. --install mutates; --dry-run makes
    even --install touch nothing (it prints the plan). Exit: 0 all present &
    current, 1 something absent/stale, 2 something could-not-tell (quipu's broken
    --version, or an unreachable release source)."""
    from . import doctor as doc

    specs = doc.SPECS
    if getattr(a, "tool", None):
        specs = tuple(s for s in doc.SPECS if s.name == a.tool)
        if not specs:
            known = ", ".join(s.name for s in doc.SPECS)
            print(f"unknown tool {a.tool!r}. known: {known}", file=sys.stderr)
            return REFUSED

    healths = doc.detect_all(specs, check_latest=not a.no_latest)

    # ...and ask the question about ITSELF (aegis-daoh, dearing's ruling). doctor
    # reported installed-vs-available for four tools and never once about `st`.
    # The tool that audits deployment drift was the only one exempt, and it is the
    # one whose staleness silently corrupts every other row it prints. Only
    # rendered for a full run: `st doctor bobbin` asked about bobbin.
    # remote= rides --no-latest: both mean "no network lookups on this run". The
    # behind-upstream fetch is the same class of question as "0.6.0 available".
    self_h = (selfcheck.check_self(remote=not a.no_latest)
              if len(specs) == len(doc.SPECS) else None)

    sock_v, sock_why = _socket_check(a)

    if not a.install:
        print(doc.report(healths))
        if self_h is not None:
            print(selfcheck.render(self_h))
        print(_render_socket(sock_v, sock_why))
        code = _fold_socket(_doctor_exit(doc, healths, self_h), sock_v, doc)
        # The untracked-hook liveness leg (aegis-06ue4): out-of-band answer to
        # "has the fail-open governance nudge actually run?" Only on a full run —
        # `st doctor bobbin` asked about bobbin, not the fleet's hooks.
        if len(specs) == len(doc.SPECS):
            from . import untracked_health as uh
            uh_rows, uh_text = _untracked_health(a)
            if uh_text is not None:
                print("\n" + uh_text)
                code = _fold_generic(code, uh.worst_exit(uh_rows))
            # SHARED-CHECKOUT GUARD COVERAGE (aegis-xig5m). st assists the
            # worktree protocol; this is the leg that says whether it is also
            # ENFORCED. Discovered per run, never a constant — a hardcoded repo
            # list is the failure this check exists to catch (the deployment's
            # own installer defaulted to ONE repo while twelve were in play).
            g_rows = guard_mod.survey()
            print("\n" + guard_mod.render(g_rows))
            code = _fold_generic(code, guard_mod.worst_exit(g_rows))
        return code

    plans = [doc.plan_install(h) for h in healths]
    print(doc.report(healths, plans=plans))
    if self_h is not None:
        print(selfcheck.render(self_h))
    if a.dry_run:
        return _doctor_exit(doc, healths, self_h)  # planned only — nothing ran

    failures = []
    for p in plans:
        try:
            doc.run_install(p)
        except RuntimeError as e:
            failures.append(str(e))
    if failures:
        for f in failures:
            print(f, file=sys.stderr)
        return CANNOT_TELL
    # re-detect so the post-install report is the observed state, not the intent
    observed = doc.detect_all(specs, check_latest=not a.no_latest)
    print(doc.report(observed))
    # Re-run the self-check too: --install can have just replaced `st` itself.
    self_after = selfcheck.check_self() if len(specs) == len(doc.SPECS) else None
    if self_after is not None:
        print(selfcheck.render(self_after))
    return _doctor_exit(doc, observed, self_after)


def _socket_check(a):
    """Ask tmux TWICE: on the declared socket, and on every socket present.

    Two questions, because one cannot separate "the fleet is down" from "we are
    looking at the wrong server", and those need different actions from a human.
    """
    from . import doctor as doc
    try:
        agents = _registry(a).all()
    except Exception:
        return doc.SOCKET_UNKNOWN, "could not read the registry"
    panes = [ag.pane for ag in agents if ag.pane]
    if not panes:
        return doc.socket_health(0, 0, 0, declared_socket(a.root))

    declared = declared_socket(a.root)
    here = Tmux(socket=declared)
    on_declared = sum(1 for p in panes if here.exists(p))

    anywhere = 0
    if not on_declared:
        for sock in _sockets_present():
            other = Tmux(socket=sock)
            anywhere = max(anywhere, sum(1 for p in panes if other.exists(p)))
            if anywhere:
                break
    return doc.socket_health(len(panes), on_declared, anywhere, declared)


def _sockets_present() -> list[str]:
    """Every tmux server socket this user has. Used only to answer "is the fleet
    somewhere ELSE?" — never to pick one silently. Choosing for the operator is
    how the ambiguity that caused this becomes permanent."""
    d = Path(f"/tmp/tmux-{os.getuid()}")
    try:
        return sorted(p.name for p in d.iterdir() if p.is_socket())
    except OSError:
        return []


def _render_socket(verdict, why) -> str:
    from . import doctor as doc
    mark = {doc.SOCKET_OK: "  socket     ok",
            doc.SOCKET_WRONG: "  socket     WRONG",
            doc.SOCKET_UNKNOWN: "  socket     unknown"}[verdict]
    return f"\n{mark}   {why}\n"


def _untracked_health(a):
    """Rows + rendered block for `st doctor`'s untracked-hook-liveness leg
    (aegis-06ue4). Every input is read here and injected into the pure checker.

    All three readers fail toward cannot-tell, never toward a false pass or a
    false SUSPECT: a launched stamp that cannot be stat'd, a tmux that cannot be
    reached, a consent file that cannot be read all resolve to `None`, which the
    checker renders as "could not tell", not as "fine".
    """
    from . import untracked_health as uh
    import time
    from pathlib import Path
    try:
        agents = _registry(a).all()
    except Exception as e:
        return [uh.Row("(registry)", uh.CANNOT_TELL, str(e))], None

    root = Path(a.root)

    def launch_time(name):
        # The launched/ stamp is written atomically AT launch (launched.record),
        # so the file's own mtime IS the launch time — no new field needed.
        try:
            return (root / "launched" / f"{name}.json").stat().st_mtime
        except OSError:
            return None

    # pane activity via tmux #{pane_activity} (epoch of last output). One
    # list-panes call, socket-aware through the same adapter `st crew` uses.
    # "active" = produced output within the checker's grace window; anything
    # older is an idle pane, which makes an absent ledger benign. Unreadable ->
    # None, which the checker treats as cannot-tell rather than guessing.
    panes = _panes(a)
    activity = {}
    try:
        argv = panes._cmd("list-panes", "-a", "-F",
                          "#{session_name} #{pane_activity}")
        import subprocess
        r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit():
                    activity[parts[0]] = float(parts[1])
        else:
            activity = None
    except Exception:
        activity = None

    def pane_active(name):
        if activity is None:
            return None
        card = next((c for c in agents if c.name == name), None)
        pane = getattr(card, "pane", None) if card else None
        if not pane or pane not in activity:
            return None
        return (time.time() - activity[pane]) < uh.GRACE_S

    rows = uh.check(agents, root, now=time.time(),
                    launch_time=launch_time, pane_active=pane_active)
    return rows, uh.render(rows)


def _fold_generic(code: int, extra: int) -> int:
    """Fold an extra domain's exit into doctor's. The constants are ordered
    OK(0) < REFUSED/actionable(1) < CANNOT_TELL(2), and that ordering is exactly
    the dominance doctor wants — could-not-tell outranks a fault outranks clean —
    so a plain max is the fold, no special-casing. Same reasoning as _fold_socket,
    which only needs its own branch because a WRONG socket is REFUSED regardless
    of the tool rows."""
    return max(code, extra)


def _fold_socket(code: int, verdict, doc) -> int:
    """A WRONG socket is ACTIONABLE (1), and it outranks a clean tool report: with
    it, every other answer st gives about the fleet is wrong in the confident
    direction. UNKNOWN forces could-not-tell (2) for the same reason the
    self-check does — a report you cannot trust is worse than one that says fix
    this."""
    if verdict == doc.SOCKET_WRONG:
        return REFUSED
    if verdict == doc.SOCKET_UNKNOWN and code == OK:
        return CANNOT_TELL
    return code


def _doctor_exit(doc, healths, self_h) -> int:
    """Fold the self-check into doctor's exit code, keeping its meanings:
    0 clean · 1 actionable · 2 could-not-tell. UNCERTAINTY DOMINATES — a report you
    cannot trust is worse than one that says "fix this" — so a self-check that
    could not read its own metadata forces 2 even when every tool row is green
    (dearing's requirement 2: it must fail toward cannot-tell).
    """
    base = doc.exit_code(healths)
    if self_h is None:
        return base
    if self_h.verdict == selfcheck.CANNOT_TELL or base == CANNOT_TELL:
        return CANNOT_TELL
    if self_h.verdict == selfcheck.BROKEN:
        return max(base, REFUSED)
    return base


def _cmd_role(a) -> int:
    """role set <agent> <role> [--reports a,b] — GENERATIVE.

    Writes the card AND emits the stop-hook routing in one operation, so a lead
    card and its routing cannot disagree. Refuses (exit 1) on any rule violation
    — orphaned reports, a lead under a lead (depth 2), an unknown agent — BEFORE
    writing anything, so a bad hierarchy never half-lands.
    """
    from . import tier
    reports = [r.strip() for r in a.reports.split(",") if r.strip()]
    try:
        plan = tier.role_set(_registry(a), a.agent, a.role,
                             reports=reports, dry_run=a.dry_run,
                             catalog=_catalog(a))
    except (LookupError, ValueError, CapabilityError) as e:
        # CapabilityError (aegis-w5l9): the new role needs a stop capability the
        # card's harness lacks. role_set raised it BEFORE writing, so this refusal
        # genuinely leaves the card and its settings untouched — same shape as the
        # hierarchy refusals above.
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    print(("  would write:" if a.dry_run else "  wrote:"))
    print(plan.render())
    if a.dry_run:
        print("\n  --dry-run: nothing written.")
        return OK
    # GENERATIVE (#6): emit each written role's settings.json in the SAME operation
    # as the card, so "declaring a role emits its stop hooks" is literal — the card
    # and its hooks cannot drift. This is the CONTENT st new's --settings reads.
    emitted = _emit_role_settings(a.root, {ag.role for ag in plan.writes})
    for path in emitted:
        print(f"  hooks   {path}")
    _report_who_the_rewrite_did_not_reach(a, {ag.role for ag in plan.writes})
    return OK


def _report_who_the_rewrite_did_not_reach(a, roles: set[str]) -> None:
    """aegis-nipg item 2: WRITING A SETTINGS FILE IS NOT DEPLOYING IT.

    Emitting settings changes bytes on disk and reaches NOBODY already running —
    `--settings` is read once, at launch. So the operator who just changed the
    hooks has, at this moment, changed nothing about the live fleet, and until now
    the command told them the opposite: it printed the paths it wrote and exited
    0, which reads as done.

    Both halves of the incident that produced this were invisible for exactly this
    reason. A Stop-hook FIX was emitted and two live agents kept the broken hook,
    staying deaf for the next hour. A PreToolUse guard that hard-blocks every edit
    was emitted and the fleet stayed green for half an hour — not because the
    guard was safe, but because nobody had relaunched into it; the first agent
    restarted, for an unrelated reason, found it with its body.

    So this prints at the moment of the change, unprompted. `st crew` can answer
    the same question, but only if you think to ask it, and nobody in that
    incident had any reason to. This is the half that does not require suspicion.

    Best-effort and never fatal: it reports on a mutation that has ALREADY
    succeeded and been printed. If the registry or tmux is unreachable we say we
    could not tell, and still exit 0 — a report that could turn a completed
    `role set` into a failure would be a worse bug than the one it warns about.
    """
    # EVERYTHING that can reach outside this process is inside the try, not just
    # the registry read. The recovered version guarded only `_registry(a).all()`
    # while `_settings_reach` goes on to call `panes.exists()` per agent — so an
    # unreachable tmux raised straight out of a role set that had ALREADY written
    # the cards and emitted the hooks. Caught by test_report_is_never_fatal_when_
    # it_cannot_look. The docstring above promised "best-effort, never fatal"; it
    # was not, and a traceback there would tell an operator their hook emission
    # failed when it had in fact succeeded — the opposite of the reassurance this
    # function exists to give.
    try:
        panes = _panes(a)
        agents = _registry(a).all()
        agents = [ag for ag in agents if ag.role in roles]
        stale, unknown = _settings_reach(a, panes, agents)
    except Exception as e:
        print(f"  ? could not tell which live agents this reached ({e}) — "
              f"check `st crew`.", file=sys.stderr)
        return
    # NOTE on the filter above: `role set franklin worker` emits
    # worker.settings.json and nothing else, so a stale administrator is genuinely
    # stale but was NOT missed by THIS rewrite — and this function claims, by its
    # own name, to report who the rewrite did not reach. Saying "not deployed to
    # sattler" after a write that never touched sattler's file is the same
    # over-claim, one level down, that this whole change is about.
    if not stale and not unknown:
        return
    print()
    if stale:
        print(f"  ⚠ NOT DEPLOYED to {len(stale)} live agent(s): {', '.join(stale)}")
        print(f"    They are still running the settings they launched with. The "
              f"file you just wrote reaches")
        print(f"    them only on relaunch: `st stop <agent> && st new <agent>`.")
    if unknown:
        print(f"  ? {len(unknown)} live agent(s) have no launch stamp, so whether "
              f"this reached them is UNKNOWN:")
        print(f"    {', '.join(unknown)}")
        print(f"    Treat as not-reached until relaunched — unknown is not fine.")


def _emit_role_settings(root: Path, roles: set[str]) -> list[Path]:
    """Write <root>/settings/<role>.settings.json for each role. Idempotent —
    settings are per-role (all workers share one), so re-emitting is a no-op
    rewrite. Returns the paths written."""
    sdir = Path(root) / "settings"
    sdir.mkdir(parents=True, exist_ok=True)
    written = []
    for role in sorted(roles):
        p = sdir / f"{role}.settings.json"
        # Pass the root: the hook must reach THIS store, not cwd/.shanty (the
        # agent's own workspace, which has none) — see _stop_cmd.
        emitted = settings_for_role(role, root=root)
        # MERGE, NEVER CLOBBER (GitHub #15, #16). This was an unconditional full
        # overwrite, so anything an operator added — a permission, an env var, a
        # SessionStart self-prime — was silently dropped on the next `roles set`.
        # cli.md tells the reader to wire their own SessionStart hook; the emitter
        # then erased it, which made the documented escape hatch unkeepable.
        #
        # st OWNS the hook EVENTS it emits and replaces those wholesale (a stale
        # stop direction must never survive a rewrite); every other key, and every
        # hook event st does not emit, is the operator's and is preserved.
        merged = _merge_settings(_read_json(p), emitted)
        p.write_text(json.dumps(merged, indent=2, sort_keys=True))
        written.append(p)
    return written


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _merge_settings(existing: dict, emitted: dict) -> dict:
    """Emitted keys win; everything else the operator wrote survives.

    ONE LEVEL DEEP for dict values, and that depth is the whole rule. Both keys st
    emits are dicts the operator also has a legitimate claim on:

      hooks   st replaces the EVENTS it emits — a stale stop direction surviving a
              rewrite is exactly the drift `roles set` exists to remove — but an
              event st does not emit (a Notification hook, a SessionStart prime) is
              left as found.
      env     st sets BOBBIN_ROLE; an operator's own variables beside it are theirs.

    Deeper than one level would start merging st's hook LISTS with an operator's,
    which is how a removed hook comes back. Shallower is the wholesale clobber this
    fixes. So: one level, emitted wins per sub-key.
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


def _cmd_context(a) -> int:
    """what code should I be looking at?  (docs/adapters.md:89)

    THE EXIT CODES ARE THE FEATURE. Read-only, so there is nothing to --dry-run:
    the reason dispatch needs one is that it writes; this cannot.

        0  did it       — bobbin answered. The list may be EMPTY, and empty is
                          an answer: I asked, nothing matched.
        1  refused      — a precondition failed (no query). We did not ask.
        2  could not tell — bobbin was unreachable / unparseable / absent.

    2 and 0-with-nothing print differently and exit differently ON PURPOSE. They
    are the same bytes and opposite facts: "there is nothing there" vs "I could
    not look". Collapsing them is the defect this command exists to not have —
    a cheerful empty result from a service that is DOWN is how a 429 became 32
    fake findings.
    """
    # Imported here, not at module top: cli is not core, but keeping first-class
    # backends out of the import path until they are actually asked for is the
    # habit the leak test enforces one layer down.
    from .bobbin import BobbinContext, NoContext
    from .protocols import ContextUnavailable

    query = " ".join(a.query)
    ctx = NoContext() if a.none else BobbinContext(repo=a.repo, mode=a.mode)

    try:
        hits = ctx.relevant(query, a.budget)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except ContextUnavailable as e:
        # Say WHICH failure, in bobbin's own words. "unavailable" alone is a shrug.
        print(f"could not tell: {e}", file=sys.stderr)
        return 2

    if not hits:
        # THREE kinds of "nothing", and they must not wear the same sentence.
        # I wrote "asked, nothing matched" for both of these first and it was a
        # lie for the none-adapter, which never asked — the exact conflation this
        # command exists to prevent, in the command that prevents it. Exit code
        # alone is not enough: an operator reading stdout must be able to tell.
        if a.none:
            print(f"no context adapter configured (none) — did not look for {query!r}")
        else:
            print(f"no context found for {query!r} (bobbin answered; nothing matched)")
        return 0

    for h in hits:
        loc = f"{h.path}:{h.lines}" if h.lines else h.path
        tag = f" [{h.repo}]" if h.repo else ""
        print(f"{loc}{tag}  {h.name}".rstrip())
    return 0


def _cmd_inbox(a) -> int:
    """inbox — put a message in an agent's inbox, or read your own. THREE modes,
    and the positional shape tells them apart:

        st inbox <agent> <message...>   SEND (send-keys; -d persists first)
        st inbox [agent]                READ — list the unread. Marks nothing.
        st inbox --count [agent]        the machine-readable count (one integer)
        st inbox --read [agent]         ACK — mark my unread messages read

    Reading and acking are SEPARATE (inbox.py). `st inbox` shows you what is
    there and changes nothing; `--read` is the act. That split is the same one
    events.py makes between pending() and drain(), and it exists for the same
    reason: `--count` is polled by a status bar every few seconds, and a read
    that consumed what it reported would destroy the delivery it was reporting on.

    ROUTINE SEND (default) — Stiwi, 2026-07-16: "st mail should just be tmux send keys."
    There is no bus, no queue, no store — nothing between the sender and the pane.
    We measured what wrapping it costs: 47 nudges sat queued for a mayor that does
    not exist, oldest 25 days, across FIVE spellings of the recipient — a queue
    accepts a message for a reader that will never come. send-keys cannot: the
    pane is there or it is not, and you are told which. Failure modes stay the two
    honest ones — REFUSED (no agent / no pane), CANNOT_TELL (pane named but gone).

    DURABLE (-d) — the gap #7 closes, and the inbox is what CLOSED it. Routine
    send-keys VANISHES if the recipient is down, which is wrong for a must-survive
    message (a handoff, a protocol step). gt mail's durability is a bead+Dolt
    commit; parity here is: PERSIST to the INBOX first (the survival guarantee),
    THEN best-effort live send for immediacy. Persist-first is deliberate — the
    store is the source of truth, the live send is a bonus.

    Until the inbox existed, durable mail persisted a tracker item and NOTHING
    EVER READ IT BACK: the recipient was told "you'll pick it up on your next
    prime", and prime showed it on the PLATE, where it evicted their actual work
    (the plate holds one item). Both halves of that are fixed here — the message
    goes to the inbox, `st inbox` is the read side, and inbox.is_message keeps it
    off the plate.

    The ruling: beads-parity on the SHARED store, NOT a dedicated store.
    Honoured by the BACKEND SWITCH, not by a hardcoded store: `--backend beads
    --repo <repo>` gives TrackerInbox (a real bead, surviving cross-session and
    cross-host); the portable files default gives FilesInbox, a lesser-but-real
    local durability. We PRINT where it landed so the durability is never ambiguous.

    Durable exit codes:
      REFUSED (1)      no such agent — OR the message is too long for the durable
                       inbox. On the beads backend a message maps to a tracker
                       item whose TITLE holds it, and bd caps a title at 500 chars
                       (so ~493 of body). The inbox is a THIN POINTER CHANNEL, not
                       a document store: a real escalation goes in a BEAD, and the
                       inbox carries the pointer. The refusal names the remedy and
                       is a REFUSED (permanent), never a CANNOT_TELL (aegis-csuo).
      CANNOT_TELL (2)  could NOT persist (store unreachable) — the survival
                       guarantee failed, so we do NOT downgrade to a silent
                       routine send and report success
      OK (0)           persisted (+ delivered live if the pane was there)
    """
    # A send flag with nothing to send is a typo, not a request to read somebody's
    # inbox. Say so rather than quietly doing the other thing.
    if not a.message and (getattr(a, "durable", False) or a.dry_run) \
            and not (getattr(a, "count", False) or getattr(a, "read", False)):
        print("  refused: nothing to send. `st inbox <agent> <message...>`.",
              file=sys.stderr)
        return REFUSED
    # READ MODES: they take no message, and the agent defaults to ME.
    if getattr(a, "count", False) or getattr(a, "read", False) or not a.message:
        import os
        me = a.agent or os.environ.get("SHANTY_AGENT")
        if not me:
            print("  refused: no agent. `st inbox <you>` or set $SHANTY_AGENT.",
                  file=sys.stderr)
            return REFUSED
        return _inbox_read(a, me)

    msg = " ".join(a.message)
    try:
        agent = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    panes = _panes(a)

    if getattr(a, "durable", False):
        return _inbox_durable(a, agent, msg, panes)

    # ROUTINE — unchanged. send-keys only, ephemeral.
    if agent.pane is None:
        print(f"  refused: {agent.name} has no pane in the registry", file=sys.stderr)
        return REFUSED
    if a.dry_run:
        print(f"  would: send-keys -> pane {agent.pane}")
        print(f"  would: {msg}")
        print("\n  0 writes. 1 send-keys.")
        return OK
    if not panes.exists(agent.pane):
        # Do NOT send into the void and report success. The pane is named and
        # absent — that is "could not tell", not "delivered".
        print(f"  could not tell: pane {agent.pane} is not there (agent down?)",
              file=sys.stderr)
        return CANNOT_TELL
    panes.send(agent.pane, msg)
    print(f"  -> {agent.name}    sent to pane {agent.pane}")
    return OK


def _inbox_durable(a, agent, msg: str, panes) -> int:
    """Persist-then-deliver. The inbox write is the guarantee; the send is speed."""
    # BEADS BY DEFAULT for -d (dearing, qdal.2 follow-up). `-d` is the flag you
    # reach for when the message MUST survive your session dying. A local files
    # store survives the session but NOT the host, not a clone being cleaned, and
    # is invisible to every `bd` query the rest of the crew uses to find it — so
    # a files default silently delivers the weaker half of the only guarantee the
    # flag exists to make. Printing where it landed is real mitigation, and it is
    # why this was a default worth changing rather than a bug: the person who most
    # needs -d is at a session tail and is not reading output carefully.
    # `--backend files` stays explicit and useful — when the store is unreachable,
    # local-and-known beats the CANNOT_TELL that persist-first would return.
    backend = _backend(a, default="beads")
    live = agent.pane is not None and panes.exists(agent.pane)
    if a.dry_run:
        print(f"  would: deliver a durable message to {agent.name}'s inbox via {backend}")
        print(f"  would: {'+ live send-keys -> ' + agent.pane if live else 'no live send (recipient down); survives in the inbox'}")
        print("\n  1 durable write." + (" 1 send-keys." if live else " 0 send-keys."))
        return OK
    # PERSIST FIRST — the survival guarantee. If this cannot be done, the durable
    # promise cannot be kept; say so (2) rather than silently downgrade to routine.
    try:
        item = _inbox(a, default="beads").deliver(a.agent, msg, frm=_me(a))
    except MessageTooLong as e:                  # PERMANENT: the message will never fit
        # Not a "could not tell" (2) — the store is fine; the message is too long,
        # and retrying it unchanged will fail identically. That is a REFUSED (1)
        # the agent must act on, and the exception says exactly how (aegis-csuo).
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except Exception as e:                        # bd/store unreachable, etc. — TRANSIENT
        print(f"  could not tell: durable persist FAILED for {agent.name} "
              f"({type(e).__name__}: {str(e)[:100]}). Nothing guaranteed to "
              f"survive; not downgrading to an ephemeral send.", file=sys.stderr)
        return CANNOT_TELL
    # Persisted. Now best-effort immediacy — never fatal to the durable result.
    # WRAPPED (GitHub #26): the send was outside the try, so a tmux failure after
    # a SUCCESSFUL persist exited 1 with a traceback. Every consumer reading the
    # exit code then concluded the message was lost, when it was durably stored
    # and would be read. "Best-effort" has to mean it in the code, not only in the
    # comment.
    if live:
        try:
            panes.send(agent.pane, msg)
            print(f"  -> {agent.name}    delivered to inbox as {item.id} ({backend}) + live to {agent.pane}")
        except Exception as e:                    # noqa: BLE001 — never fatal here
            print(f"  -> {agent.name}    delivered to inbox as {item.id} ({backend}); "
                  f"the live nudge FAILED ({type(e).__name__}: {str(e)[:80]}) — "
                  f"the message survives and they read it with `st inbox`.")
    else:
        print(f"  -> {agent.name}    delivered to inbox as {item.id} ({backend}); "
              f"recipient not live — they read it with `st inbox`.")
    return OK


def _inbox_read(a, me: str) -> int:
    """The READ side of the inbox: list, count, or ack. Whose inbox = `me`.

    --count is the machine-readable one and prints ONE integer, nothing else. The
    plain list prints a human table and MARKS NOTHING; --read is the separate,
    explicit ack. An inbox that emptied itself because a status bar looked at it
    would be worse than no inbox: the recipient would never learn what was said.
    """
    try:
        # SAME DEFAULT AS THE DURABLE WRITE. The write side defaults to beads
        # (dearing, qdal.2); if the read side defaulted to files, a bare
        # `st inbox -d` would deliver to beads and a bare `st inbox` would show
        # an empty files inbox — the sender is told it persisted, the recipient
        # is told they have nothing, and BOTH are reading a real answer from the
        # wrong store. That is the send-on-one/read-on-another split this
        # module's own docstring exists to forbid. An inbox you cannot read is
        # not an inbox, so the two defaults move together or not at all.
        box = _inbox(a, default="beads")
        unread = box.unread(me)
    except Exception as e:
        # Could-not-look is never "you have no mail" (the whole reason exit 2
        # exists here). --count stays silent on stdout so a parser sees nothing.
        print(f"  could not tell: inbox unreadable: {e}", file=sys.stderr)
        return CANNOT_TELL

    if getattr(a, "count", False):
        print(len(unread))
        return OK

    if getattr(a, "read", False):
        marked = box.mark_read(me)
        # PRINT WHAT IT CONSUMED (GitHub #14). --read is the ACK, and it is the
        # only thing that consumes a message. A count is not the message: the
        # bodies are gone from the unread set the instant this returns, so a
        # surface that prints "marked 3" and discards them has destroyed the only
        # copy the reader had a right to see.
        for m in marked:
            frm = f" from {m.frm}" if getattr(m, "frm", None) else ""
            print(f"\n  {m.id}{frm}")
            for line in (m.body or "").splitlines() or [""]:
                print(f"    {line}")
        print(f"\n  marked {len(marked)} message(s) read for {me}.")
        return OK

    print()
    if not unread:
        print(f"  {me}: no unread messages.")
    else:
        for m in unread:
            src = f" from {m.frm}" if m.frm else ""
            print(f"  {m.id}{src}  {m.body}")
        print(f"\n  {len(unread)} unread. `st inbox --read` to ack them.")
    print()
    return OK


def _cmd_task(a) -> int:
    """task creates a work item and PRINTS ITS ID, because the id is the product.

    Step 1 of the three steps (create -> send -> fetch). The id is the whole
    reason step 2 has anything to say.
    """
    title = " ".join(a.title)
    if a.dry_run:
        print(f"  would: create {title!r}" + (f" assignee={a.assignee}" if a.assignee else ""))
        print("\n  0 writes.")
        return OK
    try:
        item = _tracker(a).create(title, assignee=a.assignee)
    except Exception as e:
        print(f"  could not tell: tracker create failed: {e}", file=sys.stderr)
        return CANNOT_TELL
    print(f"  {item.id}    {item.title}")
    return OK


def _cmd_anchor(a) -> int:
    """anchor is a PURE READ. Note what is NOT here: no _wire(), because the
    Dispatcher exists to write. anchor resolves its own reads and nothing else.

    --short / --events / --harness are the machine-readable modes (the status
    bar). They take the SAME agent resolution and the SAME backends as the human
    render — a status bar reading a different plate than the anchor would be worse
    than no status bar — and they print the value ALONE. Errors still go to stderr
    with the usual exit codes: an empty stdout means "nothing to show", and a
    caller that cannot distinguish that from "I could not look" has the exit code.
    """
    me = _me(a)
    if not me:
        print("  refused: no agent. `st anchor <you>` or set $SHANTY_AGENT.",
              file=sys.stderr)
        return REFUSED
    if getattr(a, "events", False):
        return _anchor_events(a, me)
    if getattr(a, "harness", False):
        return _anchor_harness(a, me)
    try:
        p = do_anchor(me, _registry(a), _panes(a), plate=_plate(a))
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except Unreachable as e:
        # NOT success, NOT failure. "I could not look" must never say "fine".
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    if getattr(a, "short", False):
        # The id, or nothing. An empty plate prints an empty line's worth of
        # NOTHING — not "nothing.", not a dash: the consumer renders the segment
        # empty, which is what an empty plate looks like.
        if p.item:
            print(p.item.id)
        return OK
    print()
    print(p.render())
    print()
    return OK


def _read_note(a) -> str | None:
    """--note / --note-file -> the note text, or None. Raises OSError on a bad file.

    --note-file exists because a note is prose, and prose typed into a shell as
    `--note "..."` gets `$(...)` and backticks EXPANDED before st ever sees it —
    the aegis-0214 footgun, where the message either runs or is silently deleted
    while the tool reports success. A file (or stdin) is inert.
    """
    if getattr(a, "note_file", None) is not None:
        if str(a.note_file) == "-":
            return sys.stdin.read()
        return a.note_file.read_text()
    return getattr(a, "note", None)

def _anchor_harness(a, me: str) -> int:
    """`st anchor --harness` — which agent program is this card's?

    Answers "claude" for a card with no harness field, because that IS the answer
    (harness.name_for) — not blank. A status-bar segment that went empty for every
    existing card would read as "no harness", which is a different and false claim.
    """
    try:
        card = _registry(a).get(me)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    print(harness_mod.name_for(card))
    return OK


def _anchor_events(a, me: str) -> int:
    """`st anchor --events` — how many stop events am I holding, undelivered?

    THIS MUST NOT DRAIN. drain() answers the same question by CONSUMING (it marks
    each event delivered so the destination can idle — the BLOCK-ONCE rail at the
    top of events.py), so a status bar polling drain() every few seconds would
    deliver the tier's events to a status bar and the administrator would never be
    told it had them. Counting is events.pending(): a read that marks nothing.
    """
    print(len(FilesEvents(Path(a.root) / "events").pending(me)))
    return OK


def _cmd_go(a) -> int:
    d = _wire(a)
    try:
        note = _read_note(a)
    except OSError as e:
        # A note that cannot be read must NOT degrade to a note-less dispatch:
        # the caveat is the reason the caller used the flag, and sending the work
        # without it is the exact failure aegis-8013 is about.
        print(f"  refused: could not read --note-file: {e}", file=sys.stderr)
        return REFUSED
    if a.dry_run:
        try:
            decision = d.triage(a.item, a.agent, note)
            p = d.go(a.item, a.agent, dry_run=True, note=note, reassign=a.reassign)
        except Closed as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        except AlreadyAssigned as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        except GovernorRefused as e:
            # The gate applies to --dry-run TOO. A preview that showed a dispatch
            # the real command would refuse is a preview of the wrong command.
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        except LookupError as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        print(p.render()); print("\n  triage: " + decision.render())
        if a.worktree:
            # Dry-run creates NOTHING (the pure-dry-run rule), so name what a real
            # run would provision without touching disk.
            print(f"  would provision worktree: "
                  f"{worktree_for(_resolve_repo(a.worktree), a.agent)}")
        print("  0 writes. 1 tracker call, 1 send-keys.")
        return OK
    # KEEP CURRENT, MECHANIZED (aegis-4zld). Assignment is a SAFE pull moment —
    # the agent is between items by definition of being dispatched to — so bring
    # its workspace current (ff-only, kit-preserving) BEFORE the item lands and
    # work starts on a stale tree. A refused pull (local dirt, the aegis-43ph
    # condition) does NOT block the dispatch, but it rides INTO the dispatch
    # note so the agent starts knowing its tree may be stale — visible to both
    # ends, silent to neither. Deliberately NOT inside Dispatcher.go: the
    # dispatcher's asserted budget is one read/one write/one send, and a git
    # pull is the launcher's business, not the dispatch protocol's.
    if warn := _keep_current(a, a.agent):
        print(f"  ⚠ {warn}", file=sys.stderr)
        tag = f"[st keep-current: {warn}]"
        note = f"{note} — {tag}" if note else tag
    # ISOLATED WORKTREE for shared-repo work (aegis-h2rr), composed with the
    # keep-current above: the workspace clone is pulled, and if this item touches a
    # SHARED project repo, the agent gets its OWN worktree off it so its index/HEAD
    # cannot be clobbered by another agent (aegis-repg). PROVISION FAILURE REFUSES:
    # dispatching shared-repo work into the shared checkout with no isolation is the
    # exact bug this removes, so a worktree we cannot make is a hard stop, not a
    # degrade. A STALE worktree (rebase refused) is not — it rides into the note
    # like keep-current's does, visible to both ends.
    if a.worktree:
        try:
            wt_path = ensure_worktree(_resolve_repo(a.worktree), a.agent)
        except WorkspaceError as e:
            print(f"  refused: worktree — {e}", file=sys.stderr)
            return REFUSED
        print(f"  worktree: {wt_path}")
        wtag = f"[st worktree: work in {wt_path}"
        if wt_warn := _refresh_worktree(wt_path):
            print(f"  ⚠ {wt_warn}", file=sys.stderr)
            wtag += f" — {wt_warn}"
        else:
            # SAY THE SHA, not just "current" (aegis-ib65p acceptance). "current"
            # is unfalsifiable a day later; a sha lets the agent — or whoever
            # reads the bead afterwards — check what it actually started from.
            ref, _ = upstream_ref(wt_path)
            wtag += f" — current with {ref} @{_short_head(wt_path)}"
        # OPEN PRs RIDE THE DISPATCH TOO (decision 4). This is the half that
        # catches the expensive case: the duplication that prompted this bead was
        # an unmerged DRAFT, so no amount of branch-tip currency would have shown
        # it. A tree can be perfectly current and still be missing the work.
        prs, pr_err = _open_prs_for(wt_path)
        if pr_err:
            # NEVER SILENCE (decision 4, explicit). "We could not look" must not
            # render as "there are none" — that reads as an all-clear the fleet
            # did not earn.
            wtag += f" — open PRs UNKNOWN: {pr_err}"
        elif prs:
            wtag += (f" — {len(prs)} OPEN PR(s) on this repo, check before you "
                     f"build: {'; '.join(prs[:5])}")
        else:
            wtag += " — no open PRs on this repo"
        wtag += "]"
        note = f"{note} — {wtag}" if note else wtag
    try:
        p = d.go(a.item, a.agent, note=note, reassign=a.reassign)
    except Closed as e:
        # Closed is terminal (aegis-vuh33). Nothing written, nothing sent — serving
        # a closed bead reverts it to in_progress and re-does finished work. Reopen
        # deliberately if it must be worked again.
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except AlreadyAssigned as e:
        # Refuse rather than steal. Nothing written, nothing sent — two agents on
        # one item is duplicated effort no tool ever flags (aegis-uvw5 / 7yeb).
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except GovernorRefused as e:
        # The usage governor's tier (aegis-hdqej). Nothing written, nothing sent.
        # The item is untouched and stays dispatchable — this is a NOT NOW, not a
        # rejection, and the message says on what reading and at what threshold.
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except TriageRefused as e:
        # #1: pane not ready (in-flight/wedged/high-context). No write, no send.
        print(f"  refused: pane not ready — {e.decision.render()}", file=sys.stderr)
        # #5: a RESTART verdict used to dead-end here — shantytown could diagnose
        # a wedged session and then say nothing about acting on it, because
        # new/stop did not exist. They do now, so the diagnosis names the remedy.
        # We do NOT relaunch automatically: killing an agent as a side effect of a
        # dispatch is exactly the kind of thing that must stay an explicit act.
        if e.decision.action is Action.RESTART:
            print(f"  remedy: st stop {a.agent} && st new {a.agent}   "
                  f"(launcher-relaunch, never handoff — a handoff drops --settings "
                  f"and produces a hookless agent)", file=sys.stderr)
        return REFUSED
    except DispatchedButUntracked as e:
        # Exit 2: the send is a fact, the record is not. Never 0 — a caller that
        # reads 0 here books the dispatch as complete.
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    except SendUnverified as e:
        # #2: we sent, but reading the pane back did NOT confirm it landed. Its
        # docstring pins this to exit 2, and go() ran verify BEFORE the tracker
        # write, so NOTHING was recorded — a human re-dispatches rather than the
        # tracker claiming an assignment that may never have arrived. This must be
        # a clean could-not-tell, NOT an uncaught traceback (found by the
        # full-cycle validation against a real pane).
        print(f"  could not tell: {e} — recorded nothing; re-dispatch.",
              file=sys.stderr)
        return CANNOT_TELL
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    print(f"  {p.item_id} -> {p.agent}          in progress")
    print(f"  sent to pane {p.pane}")
    if p.note:
        # Echo the note AS SENT. If flattening changed it, the sender finds out
        # here rather than from a confused worker.
        print(f"  note: {p.note}")
    return OK


def _cmd_crew(a) -> int:
    """crew — who exists, what state, what role, WHAT SETTINGS, and WHO IS FREE.

    The settings column exists because `up` was the only health this ever
    reported, and `up` is exactly what a deaf agent looks like: two agents
    sat here reading `up` while their stop hooks resolved against the wrong
    store and every stop event they emitted was discarded. The column answers the
    question `up` cannot — is this agent running the settings we currently
    believe are deployed? — and answers it in three values, because `unknown` is a
    real state and rounding it to `current` would recreate the bug.

    The WORK column answers the only question a dispatcher
    actually has: who can take the next item? `up` is a LAUNCH fact, not a WORK
    fact — an agent three hours into a refactor and an agent sitting at an empty
    prompt both print `up`. The verdict is triage's, unchanged and already
    load-bearing (dispatch.py refuses sends into busy panes); `st crew` simply
    never asked it. Measured cost of not asking: a 5-worker dispatch round fed on
    a handoff's word, with no way to verify it short of `st log` per agent and
    eyeballing the scrape (sattler, 2026-07-19).

    It also answers the roster's OTHER blind spot without inventing anything: the
    work verdict is derived from the PANE, so it is available for the agents that
    have no launch stamp — over half the roster — where the settings column can
    only honestly say `?`. We do NOT backfill a stamp to make that column look
    answered: a stamp records WHICH BYTES an agent launched with, and one we did
    not observe would be a fabricated measurement, which is worse than a blank.
    """
    # --governor answers FIRST, before the registry and the panes are touched.
    # Capacity is a property of the BUDGET, not the roster: a registry that cannot
    # be read must not blank the number that decides whether to dispatch at all.
    # (It is on `crew` rather than a new command because `--count` set the
    # precedent for a status-bar reader here, and the command count is pinned by
    # test_command_count — a new verb is a deliberate widening, this is not.)
    if getattr(a, "governor", False):
        return _crew_governor(a)
    panes = _panes(a)
    try:
        agents = _registry(a).all()
    except Exception as e:
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    runtime = _runtime(a, panes)
    # --count answers BEFORE the empty-roster line: an empty roster is `0/0`, not
    # a sentence telling a status bar to run `st new`.
    if getattr(a, "count", False):
        return _crew_count(agents, panes, runtime)
    if not agents:
        print("  no agents. `st new <agent>`.")
        return OK
    launches = _launches(a)
    stops = _stops(a)
    runtime = _runtime(a, panes)
    free, busy, queued, shelled = [], [], [], []
    deliberate = []
    tree_stale = []
    verdicts = []
    waiting = []
    saturated = []
    authdead = []
    manual = []
    bad_cards = []
    print()
    for ag, state, work, posture in _crew_states(agents, panes, runtime):
        if work.endswith("sh"):
            shelled.append(f"{ag.name}({work.rsplit('+', 1)[1][:-2]})")
        if work.startswith(triage_mod.IDLE):
            free.append(ag.name)
        elif work.startswith(triage_mod.BUSY):
            busy.append(ag.name)
        elif work.startswith(triage_mod.QUEUED):
            queued.append(ag.name)
        elif work.startswith(triage_mod.WAITING):
            waiting.append(ag.name)
        elif work.startswith(triage_mod.SATURATED):
            # A `context_k=NNN` rides in the work cell (see _crew_states), so the
            # coordinator sees HOW over-limit, not just that it is.
            saturated.append(ag.name)
        elif work.startswith(triage_mod.AUTH_DEAD):
            authdead.append(ag.name)
        # Only a LIVE agent can be running stale settings. A down agent has no
        # loaded settings to be stale, and will read the current file when it
        # next starts, so reporting on it would be noise that hides the real hits.
        # Shared with `role set` (aegis-qio0): the verdict rule and the bucket
        # rule each exist ONCE, so the column here and the warning there cannot
        # disagree about the same agent.
        verdict = _settings_verdict(launches, ag.name, state == "up")
        verdicts.append((ag.name, verdict))
        tree_cell, tree_detail = _tree_staleness_cell(
            a, ag, sweep=getattr(a, 'trees', False))
        if tree_detail:
            tree_stale.append((ag.name, tree_detail))
        # A DOWN pane with a stop record is down BY DECISION (#29). Collected here
        # and reported below rather than folded into the state column: `down` is
        # what the pane says and stays what the pane says — this is why.
        if state == "down" and (rec := stops.get(ag.name)) is not None:
            deliberate.append((ag.name, rec))
        # OBSERVED posture, and separately what the CARD lacks.
        # A live agent in manual mode is the running defect; a card with gaps is
        # the one waiting to be re-armed. Both were invisible; they need
        # different sentences because they need different fixes.
        if posture == launchable.MANUAL:
            manual.append(ag.name)
        if gaps := launchable.launch_gaps(ag):
            bad_cards.append((ag.name, gaps))
        print(f"  {ag.name:<11} {ag.role:<14} {state:<8} {verdict:<8} "
              f"{tree_cell:<9} {work:<16} {posture:<7} {ag.pane or '—'}")
    stale, unknown = _reach_buckets(verdicts)
    # THE SWEEP, AS A LINE (aegis-ib65p decision 6). Learning that 12 of 12
    # worktrees were behind took a hand-rolled loop across three directories,
    # which means in practice nobody ever knew. The column answers "is anyone
    # stale"; this answers "which tree, and by how much" — the part an operator
    # would otherwise go and script again.
    if tree_stale:
        print()
        print(f"  {len(tree_stale)} agent(s) on a STALE or DIVERGED tree:")
        for name, detail in tree_stale:
            print(f"    · {name:<11} {detail}")
        print("    -N = commits you do NOT have (duplication risk: someone may "
              "have built it already).")
        print("    +N = local commits nobody else has (loss risk: push them).")
        print("    NOT pulled for you — rebase your own clean tree, never one "
              "with work in it.")
    print()
    # Down on purpose is not a roster hole to plug. Said before the free/busy
    # lines because an operator reading "3 down" needs to know which of those they
    # did themselves — the whole of GitHub #29 is a mechanism that could not tell.
    if deliberate:
        who = ", ".join(
            f"{n}{f' — {r.reason}' if r.reason else ''}" for n, r in deliberate)
        print(f"  {len(deliberate)} stopped ON PURPOSE (`st stop`, not faults): "
              f"{who}")
        print(f"    `st new <agent>` brings one back. Still respawned by "
              f"`st tend` — use `st tend --retire` to make it stay down.")
    # The dispatcher's answer, said out loud. A column still makes the operator
    # scan 14 rows; the question is "who can take this", so print the list.
    if free:
        print(f"  {len(free)} free: {', '.join(free)}")
    elif busy:
        print("  0 free — every live agent is mid-flight. Dispatching now "
              "interrupts work.")
    if busy:
        print(f"  {len(busy)} busy: {', '.join(busy)}")
    # THROTTLED-IDLE IS NOT IDLE (aegis-diasw). `3 free` means "three agents can
    # take work"; under an engaged priority floor it may mean "three agents that
    # nothing is allowed to reach". Those are opposite instructions to a
    # coordinator, and the roster reported them identically — which is half of
    # what made Rule Zero and the governor contradict each other in production.
    # Evaluated ONLY when somebody is free: when the fleet is saturated the
    # answer changes nothing, and `st crew` must not grow a metric read on a path
    # where it cannot matter.
    if free and (note := _throttled_idle_note(a)):
        print(note)
    # Not free, not busy, and the one state an operator will otherwise "fix" by
    # hand (aegis-x6xh). Say what it means and what NOT to do about it: the
    # incident that produced this line was an administrator reading a pane,
    # inferring a stall, and typing into a healthy agent's buffer.
    if queued:
        print(f"  {len(queued)} with UNSUBMITTED text in the input box: "
              f"{', '.join(queued)}")
        print(f"    Not idle and not working. Either a real stalled dispatch "
              f"(text sent, never submitted) or a")
        print(f"    human mid-sentence. A send-keys here APPENDS — do not "
              f"dispatch, and do not press Enter at")
        print(f"    someone else's pane to 'un-stall' it. Look with "
              f"`st log <agent>` and ask its owner.")
        print(f"    To resubmit once confirmed: a bare Enter does NOT submit "
              f"(measured) — use C-u,")
        print(f"    re-send the text with `send-keys -l`, pause ~1s, then Enter.")
    # The whole point of the verdict (aegis-qxc2). A column still makes the reader
    # scan 18 rows, and these agents are in NEITHER the free list nor the busy one
    # — so before this block a coordinator's summary said "5 free, 9 busy" of 18
    # agents and three stalled workers fell silently down the gap between the two
    # numbers. That gap is the original bug one layer up: invisible to the person
    # whose whole job is knowing who needs what.
    if waiting:
        print(f"  ⚠ {len(waiting)} agent(s) BLOCKED ON A QUESTION in their pane: "
              f"{', '.join(waiting)}")
        print(f"    Stopped until a person answers — not busy, not free, and it "
              f"will not time out. An ANSWERED")
        print(f"    picker still blocks until it is submitted; two agents sat on "
              f"those for over an hour.")
        # NAME THE COMMAND (aegis-w30p2). `st log <agent>` dumped the pane and
        # left the reader to eyeball a picker — which is how an option 2 that had
        # changed meaning between two prompts got answered twice in one evening.
        print(f"    Read it:   st ask {waiting[0]}")
        print(f"    Answer it: st answer {waiting[0]} <N>")
        print(f"    ...or tell them to put the decision on the bead with a "
              f"recommendation and carry")
        print(f"    on — a question in a pane reaches nobody and dies with the "
              f"session.")
    # Say the consequence, not just the count (aegis-q73g). The reader who needs
    # this line is the administrator about to book the previous item as done.
    if shelled:
        print(f"  ⚠ {len(shelled)} agent(s) still own live background shells: "
              f"{', '.join(shelled)}")
        print("    A turn that ended is not a task that finished. Whether that "
              "should block a dispatch is")
        print("    unruled — but a build, a test run or a `gh run watch` is "
              "unfinished work, and the next")
        print("    item's output will land on top of it.")
    # The bead this state was built for (aegis-h562). A saturated agent reads as
    # `idle` on every prior version of this command, so it lands on the free list
    # and gets work piled on — three agents sat past the threshold (687k/562k/524k)
    # for fifteen hours exactly that way. It is the fail-SILENT case this whole
    # file exists to convert: the number was on the pane the entire time. 400k is
    # a CYCLE threshold, not the ~1M limit — the depth shows as raw k tokens in the
    # work cell, never as a "% of limit" (that framing was a lie, Stiwi's rule).
    if saturated:
        print(f"  ⚠ {len(saturated)} agent(s) PAST THE 400k CYCLE THRESHOLD — NOT "
              f"free, a dispatch wall: {', '.join(saturated)}")
        print(f"    They read as idle but cannot hold new work: they drop earlier "
              f"context, re-derive settled")
        print(f"    decisions, and miss constraints stated long ago. `st go` "
              f"REFUSES them (the depth is in the")
        print(f"    work cell). Remedy: the agent CHECKPOINTS its state to its "
              f"bead, THEN /clears (or hands off to")
        print(f"    a fresh session), THEN takes the task — do NOT auto-clear, it "
              f"loses whatever was not saved. The")
        print(f"    saturated agent is the LEAST able to notice it must cycle, so "
              f"this is the coordinator's to drive.")
    # The bead this state was built for (aegis-arma). An operator re-login rotates
    # the shared credential, and EVERY live agent's session goes login-expired at
    # once — still rendering a ready UI over an empty box, i.e. `idle` to every
    # earlier version of this command. All 9 crew sat that way: fed, prompted, and
    # dispatched into, with every send dying against the banner. Not idle: DEAD.
    if authdead:
        print(f"  ⚠ {len(authdead)} agent(s) AUTH-DEAD — login expired, every API "
              f"call fails: {', '.join(authdead)}")
        print(f"    They render an idle-looking pane but nothing can run. /login "
              f"in the pane is an interactive")
        print(f"    browser OAuth flow — it cannot be driven for them. Recovery: "
              f"the OPERATOR re-logs in on their")
        print(f"    own session first (refreshing the shared credential), then "
              f"`st tend --reauth` relaunches every")
        print(f"    auth-dead agent in one command. Their frozen context is lost "
              f"either way — it was already")
        print(f"    unreachable the moment auth died.")
    # The incident this state was built for. An agent launched without
    # `dangerous` needs a human keystroke for EVERY bash call, so it reads `up`,
    # `current` and `busy` while being unable to advance a single command on its
    # own. Three agents sat that way through one evening: six coordinator
    # picker-answers across five agents, two blocked at once, one agent dead
    # twice. The mode line was at the bottom of the pane the whole time and no
    # surface read it — this is that read, promoted to the roster.
    if manual:
        print(f"  ⚠ {len(manual)} agent(s) in MANUAL MODE — a human must approve "
              f"EVERY bash call: {', '.join(manual)}")
        print(f"    Not free and not reliably busy: an unattended agent that "
              f"needs a keystroke per command cannot")
        print(f"    make progress by construction, and each approval only "
              f"reveals the next one. Fix is `dangerous`")
        print(f"    on the CARD plus a RELAUNCH (`st stop <agent> && st new "
              f"<agent>`) — the mode is read at launch, so")
        print(f"    editing the card alone changes nothing. Verify by this "
              f"column flipping, never by the card.")
    if free or busy or queued or waiting or saturated or authdead or shelled or manual:
        print()
    # Say the consequence, not just the state. The operator who needs this line is
    # the one who just rewrote a settings file and has no reason to suspect it did
    # not go anywhere.
    if stale:
        print(f"  ⚠ {len(stale)} agent(s) are running settings OLDER than the file "
              f"on disk: {', '.join(stale)}")
        print(f"    Their hooks are whatever the file said AT LAUNCH. Rewriting a "
              f"settings file is not deploying it — only a relaunch")
        print(f"    (`st stop <agent> && st new <agent>`) re-reads it.")
    if unknown:
        print(f"  ? {len(unknown)} agent(s) have no launch stamp, so this cannot "
              f"be answered for them: {', '.join(unknown)}")
        print(f"    Launched before stamping existed, or by something other than "
              f"`st new`. UNKNOWN, not fine.")
    # The CARD half of the same defect, and the one that is dormant rather than
    # burning: these agents are down, so nothing is stalling right now — but the
    # card is what `st tend` re-arms, and re-arming one of these manufactures the
    # incident again. It sat unseen behind `retired=true` for exactly this reason:
    # a retired card is not launched, so its defect never shows up as a symptom.
    if bad_cards:
        print(f"  ⚠ {len(bad_cards)} card(s) would launch an agent that "
              f"CANNOT WORK: {', '.join(n for n, _ in bad_cards)}")
        for name, gaps in bad_cards:
            print(f"      {name}: {', '.join(g.short for g in gaps)}")
        print(f"    Harmless while they stay down — the card is what `st tend` "
              f"re-arms, so this is a trap laid for")
        print(f"    whoever un-retires them next. `st tend --unretire` says the "
              f"full reason, and REFUSES on a workspace")
        print(f"    fault (manual mode it only warns about — that one can be "
              f"deliberate).")
    if stale or unknown or bad_cards:
        print()
    return OK


def _crew_states(agents, panes, runtime):
    """(agent, pane state, work verdict, permission posture) per agent, by name.
    THE code path for the busy/idle judgment — the table renders it and `--count`
    counts it, so the number a status bar shows can never disagree with the roster
    a human just read. Reimplementing the verdict for the counter is how the two
    drift.

    WORK: only a live pane has one. A down agent is not idle-and-available, it is
    not there — printing `idle` for it would put it on the free list and send work
    into a session that does not exist. So its verdict is `—`: not looked at.

    POSTURE: read from the SAME capture as the work verdict, never
    from the card. The card is intent; the running process is truth, and they
    diverge the moment a card is edited without a relaunch. An agent in manual
    mode is neither busy nor free — it is a stall waiting to happen, and until
    this column existed the only way to see it was to capture the pane footer by
    hand, which is why three agents sat that way for a night.
    """
    for ag in sorted(agents, key=lambda x: x.name):
        if ag.pane:
            state = "up" if panes.exists(ag.pane) else "down"
        else:
            state = "no pane"          # not "down" — we did not look
        if state == "up":
            # attrs=True: work_state needs dim to tell a placeholder suggestion
            # from queued-unsubmitted text. shows_ready_ui matches PLAIN
            # substrings and its markers arrive colour-split word by word, so it
            # gets the stripped view of the very same capture.
            screen = panes.capture(ag.pane, attrs=True)
            # One capture, two views of the same instant — a second capture-pane
            # would be a different moment. Both plain-substring readers get the
            # stripped view; only work_state's input-box check needs the attributes.
            plain = triage_mod.strip_attrs(screen)
            # awaiting: a BLOCKING picker (aegis-qxc2). Without it these panes
            # print `?`, which is honest and unactionable — 7 of 10 workers read
            # that way at once while every one of them sat on a question.
            # auth_dead: the login-expired banner (aegis-arma). Without it an
            # auth-dead pane prints `idle` — all 9 crew did, through a whole
            # expiry — and lands on the free list.
            ui_up = runtime.shows_ready_ui(plain)
            work = triage_mod.work_state(
                screen, ui_up,
                awaiting=asks_a_question(runtime, plain),
                auth_dead=auth_expired(runtime, plain))
            posture = launchable.observed_posture(plain, ui_up)
            # Background shells outlive the turn. An agent whose turn ended with a
            # build/test/`gh run watch` still live is NOT finished, and every
            # surface the administrator has was silent about it. Shown ON the work
            # verdict, because "idle" is exactly the word that would otherwise
            # mislead — `idle+1sh` is idle AND carrying live work.
            shells = triage_mod.running_shells(screen)
            if shells:
                work = f"{work}+{shells}sh"
            # The saturation ratio rides in the cell (aegis-h562), so the
            # coordinator sees HOW over-limit — 172% is a different decision from
            # 101%. Only when the verdict is saturated; a healthy pane's token
            # count is not this column's business.
            if work.startswith(triage_mod.SATURATED):
                tokens = triage_mod.context_tokens_k(plain)
                if tokens is not None:
                    work = f"{work}·{int(tokens)}k"
        else:
            work = "—"
            # A down pane has no posture to read. `—` and not MANUAL: the card
            # may well say dangerous=False, but that is what it WILL launch with,
            # not what anything is running, and this column only ever reports what
            # was observed. What the card lacks is launch_gaps()' question.
            posture = "—"
        yield ag, state, work, posture


def _throttled_idle_note(a) -> str:
    """The line that tells a free agent apart from a REACHABLE one, or "".

    FAILS TO SILENCE, always. No governor, an unreadable one, a signal we cannot
    see, no floor in force — every one of those returns "" and the roster reads
    exactly as it did before this existed. A capacity annotation that could break
    `st crew` would be a bad trade: the roster is the command an operator reaches
    for when things are already going wrong.
    """
    try:
        gov = _governor(a)
        if gov is None:
            return ""
        verdict = gov.evaluate(persist=False)
        if verdict.signal_lost or verdict.tier is None:
            return ""
        if verdict.drains:
            return ("    ⚠ THROTTLED-IDLE, not available: the usage governor is "
                    "draining the fleet — nothing dispatches at any priority.")
        if verdict.floor is None:
            return ""
        return (f"    ⚠ THROTTLED-IDLE, not fully available: the governor's "
                f"{verdict.tier.at}% tier is engaged — {verdict.effect()}. Work "
                f"below the floor cannot be dispatched to them; they are idle "
                f"because the throttle is holding, not because nobody fed them.")
    except Exception:      # noqa: BLE001 — the roster never fails on capacity
        return ""


def _crew_governor(a) -> int:
    """`st crew --governor` — the capacity verdict, machine-readable, one line.

    THE CONSUMER IS A STATUS BAR, and that shapes every choice here.

    FORMAT — the first token is a STATUS WORD, and the three cases are
    structurally different so a reader cannot mistake one for another:

        ok 45/50 24/45                              both budgets, no tier engaged
        ok 70/80 24/45 dispatch only P0 and above [five_hour >= 70%]  a tier engaged
        ok 96/- 24/45 ...                           above every five_hour tier
        lost                                        the signal could not be read
        off                                         no governor configured

    Each budget is `current/next-threshold`. The NEXT THRESHOLD is in the output
    because a consumer cannot colour honestly without it: the tiers are per-window
    and asymmetric (five_hour 50/70/80/95, seven_day 45/65/75/90), so a bare 44%
    is six points from engaging one budget and already engaging the other. A bar
    that coloured on the raw number would call those the same, which is precisely
    the "one number is the wrong number" mistake this flag prints two budgets to
    avoid. `-` means no higher tier exists for that window.

    `lost` and `off` carry NO NUMBERS AT ALL. That is deliberate: a bar that
    printed a stale percentage while blind would silently undo the governor's
    whole fail-safe, which is that blindness is LOUD (it alarms every pass on
    purpose). Making the blind case unparseable-as-a-reading is the same rule
    `shantytown.answer` applies to a collection — "could not look" must not be
    representable as an answer.

    BOTH WINDOWS, always. They exhaust independently and a five-hour budget
    refills in hours while the weekly does not refill for days, so one number is
    the wrong number half the time. The tier LABEL is last and may
    contain spaces: a reader takes the first three fields and treats the
    remainder as the label.

    A PURE READ (`persist=False`). A status bar polls every few seconds; if this
    extended a hysteresis hold, merely LOOKING at the bar would ratchet fleet
    policy. `st tend` remains the one writer of the engaged tier.
    """
    gov = _governor(a)
    if gov is None:
        print("off")
        return OK
    try:
        readings = gov.reader.read_all()
        verdict = gov.evaluate(persist=False)
    except Exception:
        # Any failure to READ is `lost`, never a number. The reader already
        # distinguishes its own failure modes for the operator-facing path; a
        # status bar needs exactly one bit and must not guess.
        print("lost")
        return OK
    if verdict.signal_lost:
        print("lost")
        return OK

    def _pct(window: str) -> str:
        r = readings.get(window)
        # A window the producer does not publish is not a zero. Rendering 0 for
        # an absent budget would read as "plenty of headroom" — the most
        # expensive possible direction for this particular wrong answer.
        if r is None or not r.ok or r.pct is None:
            return "?/?"
        now = int(round(r.pct))
        # The lowest tier this window has NOT yet reached. Read off the policy so
        # the consumer never hardcodes thresholds that live in shantytown.toml.
        higher = sorted(t.at for t in gov.policy.tiers_for(window) if t.at > now)
        return f"{now}/{higher[0] if higher else '-'}"

    label = ""
    if verdict.engaged:
        # The TOP engaged tier names the restriction in force. Policy.engaged is
        # cumulative, so the last one is the most restrictive.
        label = verdict.engaged[-1].label()
    print(f"ok {_pct(gov_mod.FIVE_HOUR)} {_pct(gov_mod.SEVEN_DAY)} {label}".rstrip())
    return OK


def _crew_count(agents, panes, runtime) -> int:
    """`st crew --count` — print `busy/total`, nothing else.

    TOTAL IS NOT THE ROSTER SIZE. It is the number of agents we can actually
    answer busy-or-idle for; an agent whose verdict is unknown (down, no pane,
    a pane with no runtime UI, a wedge) is in NEITHER number. Counting the
    unknowns into the denominator would render `3/9` when four of the nine were
    never asked — a made-up capacity figure that reads exactly like a measured
    one, which is the failure this repo keeps naming (`up` for a deaf agent,
    CLEAR for a check that could not reach its target).
    """
    busy = idle = 0
    for _ag, _state, work, _posture in _crew_states(agents, panes, runtime):
        if work == triage_mod.BUSY:
            busy += 1
        elif work == triage_mod.IDLE:
            idle += 1
    print(f"{busy}/{busy + idle}")
    return OK


def _cmd_roles(a) -> int:
    if not a.check:
        try:
            agents = _registry(a).all()
        except Exception as e:
            print(f"  could not tell: {e}", file=sys.stderr)
            return CANNOT_TELL
        print()
        for ag in sorted(agents, key=lambda x: x.name):
            print(f"  {ag.name:<11} {ag.role:<14} "
                  f"reports_to: {ag.reports_to or '—'}")
        print()
        return OK

    # #6.4: hand check the hook READER, so `hooks: ok` reports the settings file
    # `role set` actually emitted instead of naming a column.
    # aegis-0v97: and hand it the LIVE reader too, so the check measures the
    # running process, not only the artifact its role would have emitted. The
    # artifact was green for a lead whose live process had no stop hooks at all.
    panes = _panes(a)
    rep = roles_mod.check(_registry(a),
                          emitted=lambda role: emitted_stop_directions(a.root, role),
                          live=lambda pane: live_wiring(pane, panes.cmdline),
                          catalog=_catalog(a))
    print()
    print(rep.render())
    print()
    return {roles_mod.OK: OK,
            roles_mod.BROKEN: REFUSED,
            roles_mod.CANNOT_TELL: CANNOT_TELL}[rep.verdict]


def _cmd_project(a) -> int:
    """Materialize the crew cards FROM the graph. quipu is the authority;
    the cards are a generated projection — writes go to the graph, reads may come
    from the card, NEVER the reverse. Regenerating is idempotent; hand-edits are
    overwritten on the next project, which is the point.

    Refuses (2) if the graph is unreachable — a projection you could not source
    is not an empty projection. It projects the graph AS-IS, orphans included, so
    `roles --check` still surfaces them rather than project hiding a bad graph.

    IT ALSO SHOWS ITS WORK AND REFUSES TO RESTRUCTURE A LIVE CREW SILENTLY
    (aegis-0v97). "Hand-edits are overwritten, which is the point" is true of a
    clean graph. It is catastrophic against a dirty one, and ours is dirty:
    measured 2026-07-20, the graph declares `a-backup-host` (a HOST — dolt/garage backups
    live on it) and `mayor` (which this fleet has stated does not exist and never
    will) as crew workers, plus two agents with no card and no session. Projecting
    that would have demoted the live administrator to an orphan worker, cut nine
    running agents loose, and materialized cards for a host and a ghost — with no
    preview and no confirmation, because this function used to be a bare
    `for ag in agents: files.set(ag)`.

    So: always print the diff; write nothing on --dry-run; and REFUSE (1) when the
    projection would change the role or supervisor of an agent that is LIVE RIGHT
    NOW, unless --force. Being the declared authority is not the same as being
    right, and a projection that cannot be previewed is a footgun regardless of
    which side of the divergence is correct.
    """
    # aegis-t4eve: the source is chosen here, not hardcoded, and it is PRINTED.
    # Ontology-first with file-fallback by default; an explicit --from is never
    # silently substituted (asking for quipu and getting a stale file without
    # being told is how the file becomes the authority).
    from . import hierarchy as hier_mod
    try:
        spec = getattr(a, "from_source", None)
        # Inject cli's QuipuRegistry rather than letting hierarchy bind its own:
        # the graph source stays patchable at ONE name (tests/test_project_guard
        # monkeypatches `cli.QuipuRegistry`), and the seam stays injectable.
        source, src_info = hier_mod.resolve(
            spec, file_default=hier_mod.default_file(a.root),
            quipu_factory=QuipuRegistry)
        agents = source.all()
    except ValueError as e:                      # a mistyped --from is usage, not outage
        print(f"  {e}", file=sys.stderr)
        return REFUSED
    except Exception as e:
        print(f"  could not project: {e}", file=sys.stderr)
        return CANNOT_TELL
    print(f"  {src_info.render()}")

    # ZERO agents from a REACHABLE graph is almost never "no crew" — it is a
    # wrong namespace (SHANTY_ONTO_NS unset -> the library's example default,
    # which holds none of any real fleet's facts) answering "nobody exists"
    # with a straight face. This used to fall through to "already projected:
    # 0 cards match the graph. Nothing to do." — a false pass ellie documented
    # and aegis-wxrm asked to close. Could-not-tell, not success.
    if not agents:
        ns_hint = ("" if src_info.kind != "quipu" else
                   " — wrong namespace? (SHANTY_ONTO_NS is "
                   f"{'set' if os.environ.get('SHANTY_ONTO_NS') else 'UNSET — using the library example default'})")
        print(f"  could not project: {src_info.detail} answered but returned "
              f"ZERO CrewMembers{ns_hint}",
              file=sys.stderr)
        return CANNOT_TELL

    files = FilesRegistry(a.root / "crew")
    panes = _panes(a)
    dry = getattr(a, "dry_run", False)
    force = getattr(a, "force", False)

    def live(name: str) -> bool:
        """Is this agent RUNNING? Liveness comes from the card's pane, because the
        graph has no idea what is running — which is the whole reason it must not
        be allowed to restructure the crew unsupervised."""
        try:
            card = files.get(name)
        except LookupError:
            return False
        return bool(card.pane) and panes.exists(card.pane)

    changes, harm = [], []
    for ag in sorted(agents, key=lambda x: x.name):
        try:
            cur = files.get(ag.name)
            before = (cur.role, cur.reports_to)
        except LookupError:
            cur, before = None, None
        after = (ag.role, ag.reports_to)
        if before == after:
            continue
        is_live = live(ag.name)
        changes.append((ag.name, before, after, is_live, cur is None))
        if is_live:
            harm.append(ag.name)

    # The subtle one, and the reason a per-agent diff is not enough: an agent that
    # is NOT in the graph is left untouched, so it keeps pointing at a supervisor
    # this projection may just have demoted. Nobody's own row shows that.
    demoted = {n for n, b, af, _l, _new in changes
               if b and b[0] in ("administrator", "lead") and af[0] not in ("administrator", "lead")}
    dangling = []
    if demoted:
        graph_names = {ag.name for ag in agents}
        for p in sorted((a.root / "crew").glob("*.json")):
            nm = p.stem
            if nm in graph_names:
                continue
            try:
                card = files.get(nm)
            except LookupError:
                continue
            if card.reports_to in demoted:
                dangling.append((nm, card.reports_to, live(nm)))

    if not changes:
        print(f"\n  already projected: {len(agents)} cards match the graph. Nothing to do.\n")
        return OK

    print(f"\n  {len(changes)} card(s) would change:\n")
    for name, before, after, is_live, is_new in changes:
        mark = "LIVE " if is_live else "     "
        if is_new:
            print(f"  {mark}+ {name:<10} NEW CARD -> {after[0]}, reports_to {after[1] or '—'}")
        else:
            print(f"  {mark}~ {name:<10} {before[0]} -> {after[0]}, "
                  f"reports_to {before[1] or '—'} -> {after[1] or '—'}")
    if dangling:
        print(f"\n  and {len(dangling)} card(s) NOT in the graph would be left pointing at a "
              f"demoted supervisor:")
        for nm, sup, is_live in dangling:
            print(f"  {'LIVE ' if is_live else '     '}! {nm:<10} still reports_to {sup}")

    if dry:
        print("\n  --dry-run: nothing written.\n")
        return OK

    if harm and not force:
        print(f"\n  REFUSED: {len(harm)} LIVE agent(s) would be restructured: "
              f"{', '.join(sorted(harm))}.", file=sys.stderr)
        print("  They are running right now. Projecting would change their role or "
              "supervisor underneath them.", file=sys.stderr)
        print("  Reconcile the graph first, or re-run with --force if you mean it.\n",
              file=sys.stderr)
        return REFUSED

    for ag in sorted(agents, key=lambda x: x.name):
        files.set(ag)
    print(f"\n  projected {len(agents)} cards from the graph -> {a.root / 'crew'}\n")
    return OK


def _resolve_repo(repo: str) -> Path:
    """A shared repo, as a path OR a bare name under $GT_ROOT (~/gt) — so both
    `st worktree /home/x/gt/quipu` and `st worktree quipu` reach the same tree,
    matching scripts/crew-worktree.sh's `$GT_ROOT/$repo` resolution."""
    p = Path(repo).expanduser()
    if p.is_absolute() or "/" in repo or p.exists():
        return p
    root = Path(os.environ.get("GT_ROOT", Path.home() / "gt"))
    return root / repo


def _cmd_worktree(a) -> int:
    """worktree <repo> [<agent>] [--gc] — st PROVISIONS the isolated worktree so
    the agent never runs `git worktree add` by hand (aegis-h2rr).

    A shared project checkout (~/gt/shantytown, quipu, hank, goldblum) is
    multi-writer: index and HEAD belong to the working copy, so two agents
    committing there corrupt each other silently (aegis-repg/iaef). Each agent
    gets its own worktree off the shared repo instead — <repo>-wt/<agent> on
    branch wt/<agent>. Provision prints the path (the cwd to work in). --gc removes
    it IFF unchanged; it NEVER discards uncommitted or unpushed work.
    """
    agent = a.agent or os.environ.get("SHANTY_AGENT")
    if not agent:
        print("  refused: no agent. `st worktree <repo> <agent>` or set "
              "$SHANTY_AGENT.", file=sys.stderr)
        return REFUSED
    repo = _resolve_repo(a.repo)
    try:
        if a.gc:
            removed = cleanup_worktree(repo, agent)
            dest = worktree_for(repo, agent)
            print(f"  {'removed' if removed else 'kept (holds work, or absent)'}: {dest}")
            return OK
        path = ensure_worktree(repo, agent)
        # ENFORCE, HAVING JUST ASSISTED (aegis-xig5m). Provisioning a worktree is
        # the moment st KNOWS this shared checkout is in play — so it is the
        # moment to install the guard, and it costs nothing. Doing it here is why
        # there is no new command to remember, which is what left coverage at 4
        # of 10 repos when it was a script somebody had to run.
        #
        # NEVER FATAL TO THE PROVISION. The worktree is the thing the caller
        # asked for and it already exists by this line; failing the command over
        # the seatbelt would deny the isolation to punish the absence of the
        # guard. Loud, then carry on.
        try:
            changed, note = guard_mod.install(repo)
            if changed:
                print(f"  {note}")
        except guard_mod.GuardError as e:
            print(f"  ⚠ guard NOT installed — {e}", file=sys.stderr)
        # BRING IT CURRENT BEFORE HANDING IT BACK (aegis-ib65p decision 1).
        # Provisioning was idempotent-and-inert: an EXISTING worktree was returned
        # untouched, so the second and every later call handed back whatever the
        # tree was months ago. Measured: 12 of 12 shantytown worktrees behind, one
        # of them by 155 commits, on the repo the fleet changes hourly — and the
        # coordinator rebuilt from scratch a fix that already existed.
        #
        # This is the right moment because it is the moment st KNOWS the shared
        # repo is in play — the same argument that put the guard install here, and
        # the reason there is no new command to remember.
        #
        # NEVER FATAL, NEVER FORCED. ff/rebase-only on a CLEAN tree; dirty or
        # conflicted is reported and left exactly as it was. A supervisor that
        # discarded an agent's uncommitted work to make a staleness number reach
        # zero would be a worse bug than the staleness (aegis-repg/iaef).
        if warn := _refresh_worktree(path):
            print(f"  ⚠ {warn}", file=sys.stderr)
        else:
            ref, _ = upstream_ref(path)
            print(f"  current with {ref}")
        print(path)
        return OK
    except WorkspaceError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED


def _cmd_subscribe(a) -> int:
    """subscribe — watch quipu entity events and route assigned workflows.

    The events adapter integrations.md sketched (`subscribe(kinds)`), finally built
    first-class on Quipu's cursored transaction log. A WATERMARKED POLL: on new
    transactions it asks quipu which governed workflows the graph assigns
    (`aegis:assignsWorkflow`) and routes each NEW one to the administrator — who
    acts (a bead + a nudge). `--once` polls a single batch (exit 0 reachable / 2
    could-not-tell); default loops every `--interval` seconds. State (watermark +
    handled set) persists under `<root>/events`, so a restart resumes rather than
    re-routing what it already handled.
    """
    import time

    from . import quipu_events as qe
    from . import tier

    events = qe.QuipuEvents(server=a.server)
    registry = _registry(a)
    tracker = _tracker(a)
    panes = Tmux()
    try:
        admin = tier._find_administrator(registry)
    except Exception:                              # registry unreachable — route without a target
        admin = None
    state_path = a.root / "events" / "quipu-subscription.json"
    state = qe.SubscriptionState.load(state_path)

    def route(w) -> None:
        # A governed workflow is assigned -> create the work and hand it to the
        # coordinator. Autonomous is fine (owner directive): shantytown orchestrates.
        title = f"workflow {w.iri}"
        if w.label:
            title += f" — {w.label}"
        if w.target:
            title += f" (targets {w.target})"
        try:
            item = tracker.create(title, assignee=admin)
        except Exception as e:
            print(f"  could not create a bead for {w.iri}: {e}", file=sys.stderr)
            return
        mailed = ""
        if admin:
            try:
                card = registry.get(admin)
                if card.pane and panes.exists(card.pane):
                    panes.send(card.pane, f"governed workflow assigned: {item.id} — {title}")
                    mailed = f", mailed {admin}"
            except LookupError:
                pass
        print(f"  routed {w.iri} -> {item.id}{mailed}")

    def one() -> int:
        report = qe.poll_and_route(events, state, route)
        state.save(state_path)
        print(report.render())
        return OK if report.reachable else CANNOT_TELL

    if a.once:
        return one()
    try:
        while True:                                # a long-running subscriber
            one()
            time.sleep(max(1.0, a.interval))
    except KeyboardInterrupt:
        return OK


def _not_yet(cmd: str) -> int:
    """A guard, not a stub. EVERY command in the surface is now wired
    (new/stop/log were the last three). Nothing routes here anymore; it exists so
    that a subcommand ADDED to the parser without a handler refuses loudly instead
    of silently doing nothing — the honest failure, not a plausible exit 0. If you
    see this, you added a parser entry and forgot to wire it in main().
    """
    print(f"  refused: `st {cmd}` is in the parser but has no handler wired in "
          f"main(). It is not a stub and will not pretend to work.", file=sys.stderr)
    return REFUSED


# --- tend: the only command that RESTARTS things ----------------------------

def _refresh_clone(path) -> str | None:
    """ff-only pull at a SAFE moment: the agent is down, between items, or being
    relaunched — nothing holds the checkout mid-thought. Returns an error
    string (loud) or None. NEVER raises — a failure here must not stop a
    respawn or a dispatch, because trading an outage for a stale checkout is
    the worse deal. And NEVER anything but --ff-only: force/reset against a
    crew clone is the aegis-repg/iaef data-loss class.

    .MCP.JSON SURVIVES THE PULL (aegis-4zld). The provisioned kit carries a
    live bearer token and is uncommitted BY DESIGN — so history can delete or
    replace a tracked template out from under it, and a refused pull can leave
    it half-restored. The kit is copied aside before the pull and put back if
    the pull removed or changed it: an agent must never come out of a
    keep-current pull with its tools stripped (the five-agents-worked-a-night-
    without-tools class, via a new door)."""
    import subprocess
    try:
        mcp = Path(path) / ".mcp.json"
        saved = mcp.read_bytes() if mcp.is_file() else None
        r = subprocess.run(["git", "-C", str(path), "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=60)
        err = None if r.returncode == 0 else (r.stderr or r.stdout).strip()
        if saved is not None and (not mcp.is_file() or mcp.read_bytes() != saved):
            mcp.write_bytes(saved)
        return err
    except Exception as e:                       # not a repo, git absent, timeout
        return str(e)


def _keep_current(a, agent_name: str) -> str | None:
    """Bring `agent_name`'s workspace clone current (ff-only) — the crew 'Keep
    Current' rule as MECHANISM instead of memory (aegis-4zld; Stiwi's ask).

    Returns None when the workspace is current (or the card names none —
    nothing to pull is not a failure), else a one-line WARNING the caller must
    surface: a stale-but-working agent beats a failed dispatch, but staleness
    must be VISIBLE, never silent (the deploy-lag disease, aegis-daoh/ttlr, is
    exactly invisible staleness). Never raises, never blocks."""
    try:
        card = _registry(a).get(agent_name)
    except Exception:
        return None                    # the dispatch path will name the real error
    if not card.workspace:
        return None
    err = _refresh_clone(card.workspace)
    if err is None:
        return None
    first = err.splitlines()[0] if err else "unknown"
    return (f"workspace could not be brought current (ff-only pull refused: "
            f"{first}) — dispatching anyway on the EXISTING tree; it may be "
            f"stale. Clean or reconcile {card.workspace} to restore keep-current.")


def _agent_trees(a, ag, sweep: bool = False) -> list:
    """The trees this agent edits. Its workspace clone always; its worktree off
    every shared repo st has provisioned from only when `sweep` is set.

    THE SWEEP IS OPT-IN, and the reason is cost on the hottest command there is.
    `st crew` is what a coordinator runs constantly. Discovering repos and
    measuring a worktree per agent per repo is ~3 git subprocesses each — on
    this fleet, fifteen agents across twelve containers, which is well over a
    hundred processes for one status read. A status command that got noticeably
    slower would be run less, and a staleness signal nobody looks at is the
    condition this bead is trying to end, reached by a different road.

    So the DEFAULT column costs one tree per agent (its own workspace clone) and
    `st crew --trees` buys the full picture. That is also what makes the default
    hermetic: discovery reads `GT_ROOT`/`~/gt` off the real filesystem, so an
    unconditional sweep made unit tests depend on which worktrees happened to
    exist on the developer's machine — measured, it silently changed three
    existing tests' output.

    When it does sweep, repos are DISCOVERED, never configured — `guard.discover`
    finds the `<repo>-wt` containers st created itself. A hardcoded repo list is
    the failure mode here, measured: the deployment's own installer defaulted to
    ONE repo when twelve were live.
    """
    out = []
    if ag.workspace:
        p = Path(ag.workspace).expanduser()
        if p.is_dir():
            out.append(p)
    if not sweep:
        return out
    try:
        for repo in guard_mod.discover():
            wt = worktree_for(repo, ag.name)
            if wt.is_dir():
                out.append(wt)
    except Exception:
        pass
    return out


def _tree_label(p) -> str:
    """Name a tree by its REPO, not by its directory name.

    A worktree lives at `<repo>-wt/<agent>`, so its basename is the AGENT — which
    means a per-agent sweep rendered by basename says "arnold -5; arnold -3;
    arnold -1/+15" and names the same thing three times while identifying none of
    them. Measured on the live fleet: the detail lines told an operator they were
    stale in four places and not one of which repo, which is most of the value.
    """
    p = Path(p)
    if p.parent.name.endswith("-wt"):
        return p.parent.name[: -len("-wt")]
    return "workspace"


def _tree_staleness_cell(a, ag, sweep: bool = False) -> "tuple[str, str | None]":
    """(column cell, detail line or None) for `st crew`.

    The cell is deliberately TINY — `-3/+1`, `ok`, `?` — because it sits in a row
    an operator scans, and the detail goes on its own line below. `?` is its own
    value and never rounds to `ok`: a tree whose upstream could not be resolved
    is not a healthy tree, and this whole bead exists because invisible
    staleness was read as fine.
    """
    trees = _agent_trees(a, ag, sweep=sweep)
    if not trees:
        return "—", None
    behind = unpushed = 0
    unknown = False
    parts = []
    for t in trees:
        try:
            s = tree_staleness(t, fetch=False)
        except Exception:
            unknown = True
            continue
        if s.error:
            unknown = True
            parts.append(f"{_tree_label(t)}: {s.render()}")
            continue
        behind += s.behind
        unpushed += s.unpushed
        if not s.current():
            bits = []
            if s.behind:
                bits.append(f"-{s.behind}")
            if s.unpushed:
                bits.append(f"+{s.unpushed}")
            parts.append(f"{_tree_label(t)} {'/'.join(bits)} vs {s.ref}")
    if behind or unpushed:
        cell = f"-{behind}/+{unpushed}"
    elif unknown:
        cell = "?"
    else:
        cell = "ok"
    return cell, ("; ".join(parts) if parts else None)


def _short_head(dest) -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(dest), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=15)
        return (r.stdout or "").strip() or "?"
    except Exception:
        return "?"


def _open_prs_for(dest) -> "tuple[list[str] | None, str | None]":
    """Open PRs on the forge behind this tree's upstream. (prs, error)."""
    import subprocess
    try:
        ref, _ = upstream_ref(dest)
        if not ref:
            return None, "no upstream ref resolved for this tree"
        remote = ref.split("/", 1)[0]
        r = subprocess.run(["git", "-C", str(dest), "remote", "get-url", remote],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None, f"no url for remote {remote!r}"
        return forgejo_mod.open_pulls((r.stdout or "").strip())
    except Exception as e:
        return None, str(e)


def _refresh_worktree(dest, base: str | None = None) -> str | None:
    """Bring a project WORKTREE current — by REBASE onto its upstream, not ff-pull.

    The keep-current sibling _refresh_clone ff-pulls a clone on `main`. A worktree
    is on `wt/<agent>`, so an ff-only pull either no-ops (wrong branch) or fails —
    the caveat flagged on aegis-h2rr. The right move is the crew-worktree pattern:
    rebase wt/<agent> onto the upstream. But ONLY when the tree is clean: rebasing
    over uncommitted work is the force/reset data-loss (aegis-repg) this whole line
    removes. A dirty tree, or a rebase that conflicts, is LEFT AS-IS and reported —
    a stale-but-intact worktree beats a mangled one.
    Never raises; returns a one-line warning or None (current).

    `base` IS NO LONGER `origin/main` (aegis-ib65p). It is RESOLVED from the
    repo's own config by workspace.upstream_ref, because on the very repo this
    was written for, `origin` is a public MIRROR and `main` tracks `forge` — and
    which of the two led INVERTED inside three hours. Rebasing onto a literal
    `origin/main` there would have moved twelve worktrees BACKWARD off the
    authority while reporting success, which is this bead's own failure produced
    by its remedy. Passing `base` explicitly still works and still overrides;
    None means "ask the repo".

    It also now reports UNPUSHED work, not just missing work. Both were live in
    one evening and they are different risks: behind = duplication, unpushed =
    LOSS. A tree that is merely ahead used to read as perfectly fine.
    """
    import subprocess
    try:
        if base is None:
            base, note = upstream_ref(dest)
            if base is None:
                return note                      # cannot tell — say so, act not
        else:
            note = None
        subprocess.run(["git", "-C", str(dest), "fetch", "--all", "--quiet"],
                       capture_output=True, text=True, timeout=60)
        stale = tree_staleness(dest)
        extra = f" {note}" if note else ""
        # STRANDED WORK IS REPORTED WHETHER OR NOT THE REBASE SUCCEEDS. It is not
        # an error and must not block, but nobody else can see it, and a reset
        # anywhere near it destroys it silently.
        if stale.unpushed:
            extra += (f" {stale.unpushed} local commit(s) are NOT on {base} — "
                      f"push them; they exist only in this tree.")
        dirty = subprocess.run(["git", "-C", str(dest), "status", "--porcelain"],
                               capture_output=True, text=True)
        if dirty.returncode != 0 or dirty.stdout.strip():
            return ("worktree has local changes — not rebased onto "
                    f"{base}; working on it as-is (may be behind).{extra}")
        r = subprocess.run(["git", "-C", str(dest), "rebase", base],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            # Leave the worktree usable: abort the half-applied rebase.
            subprocess.run(["git", "-C", str(dest), "rebase", "--abort"],
                           capture_output=True, text=True)
            first = (r.stderr or r.stdout).splitlines()
            return (f"worktree rebase onto {base} refused "
                    f"({first[0] if first else 'conflict'}) — on the existing "
                    f"tree.{extra}")
        return extra.strip() or None
    except Exception as e:                       # not a repo, git absent, timeout
        return str(e)


def _systemctl_user_active(unit: str) -> bool:
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return True
        r = subprocess.run(["systemctl", "is-active", "--quiet", unit],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False                             # cannot tell -> do not claim one


def _run_cmd(argv) -> None:
    import subprocess
    subprocess.run(argv, capture_output=True, text=True, timeout=60)


def _attach_argv(pane: str, socket, read_only: bool, has_shanty: bool):
    """Build (argv, env-overlay) for the attach — the pure, testable core.

    THROUGH SHANTY when it is on PATH (the themed bar + segments Stiwi wants the
    attach to be), falling back to bare tmux only when it is absent — the same
    self-hiding discipline the segments already use. Either way the operator never
    types the socket or the `shanty-`/`aegis-crew-` pane prefix: st resolved both.

    The socket is passed to shanty via SHANTY_TMUX_SOCKET (shanty honours it as of
    the companion change), so shanty views the FLEET's real sessions on their
    existing socket — no agent is migrated onto shanty's own server (aegis-f5z4's
    ruling holds; shanty is the VIEW). tmux takes it as `-L`.
    """
    if has_shanty:
        env = {"SHANTY_TMUX_SOCKET": socket} if socket else {}
        argv = ["shanty", "attach"]
        if read_only:
            argv.append("-r")
        argv.append(pane)
        return argv, env
    argv = ["tmux"]
    if socket:
        argv += ["-L", socket]
    argv += ["attach-session", "-t", pane]
    if read_only:
        argv.append("-r")
    return argv, {}


def _exec_attach(argv, env_overlay) -> int:
    """Hand the terminal to the attach. os.execvpe REPLACES this process, so on
    success it never returns; only a failed exec (the binary vanished between the
    PATH check and here) falls through to a could-not-tell."""
    import os as _os
    try:
        _os.execvpe(argv[0], argv, {**_os.environ, **env_overlay})
    except OSError as e:
        print(f"  could not exec {argv[0]}: {e}", file=sys.stderr)
        return CANNOT_TELL
    return OK  # unreachable on success; keeps the type checker happy


def _dashboard_snapshot(a, reg, panes, runtime, now):
    """One dashboard snapshot for `a.admin`'s tier. Pure-ish: reads the registry,
    the REUSED crew-state verdicts, the plate, and the event ledger — composes
    them in dashboard.gather. Separated from the render loop so a test drives it
    without a clock or a foreground loop."""
    from . import dashboard as dash_mod
    from .tier import _find_administrator
    agents = reg.all()
    admin = a.admin or _find_administrator(reg)
    if not admin:
        return None, "no administrator in the registry to show a tier for"
    if admin not in {x.name for x in agents}:
        return None, f"no such agent: {admin}"
    # The dashboard renders the tier, not the posture column — hand it the shape
    # it has always taken rather than widening its contract for a field it does
    # not draw. One capture still, because it is the same generator.
    crew_states = [(ag, state, work)
                   for ag, state, work, _ in _crew_states(agents, panes, runtime)]
    plate = _plate(a)
    last = FilesEvents(Path(a.root) / "events").latest_by_sender()
    return dash_mod.gather(admin, agents, crew_states, plate, last, now), None


def _cmd_dashboard(a) -> int:
    """dashboard [admin] — a live, read-only view of one admin's tier.

    The always-on sibling of `st crew`: scoped to an administrator and its crew,
    it REUSES the same busy/idle/waiting/saturated verdicts (never a second
    opinion) and refreshes on an interval so an operator keeps it in a pane while
    talking to that admin. `--once` renders a single snapshot (for scripting);
    the default loops until interrupted.
    """
    from . import dashboard as dash_mod

    reg = _registry(a)
    panes = _panes(a)
    runtime = _runtime(a, panes)

    def one() -> tuple[int, "Dashboard | None"]:
        try:
            data, err = _dashboard_snapshot(a, reg, panes, runtime, time.time())
        except Exception as e:
            print(f"  could not tell: {e}", file=sys.stderr)
            return CANNOT_TELL, None
        if data is None:
            print(f"  refused: {err}", file=sys.stderr)
            return REFUSED, None
        return OK, data

    if a.once:
        rc, data = one()
        if data is not None:
            print(dash_mod.render(data, time.time()))
        return rc

    # The self-refreshing panel. Clear + redraw each interval; Ctrl-C exits clean.
    print("  st dashboard — refreshing every "
          f"{a.interval}s. Ctrl-C to stop.", file=sys.stderr)
    try:
        while True:
            rc, data = one()
            if data is None:
                return rc                     # a refusal/could-not-tell is terminal
            # \x1b[2J\x1b[H: clear screen + home, so the pane shows ONE live frame
            # rather than an ever-growing scrollback of snapshots.
            print("\x1b[2J\x1b[H", end="")
            print(dash_mod.render(data, time.time()))
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return OK


def _cmd_input(a) -> int:
    """input <agent> [--show|--clear|--dismiss] — the input box as a surface.

    THE COORDINATOR'S QUESTION, answered by the tier instead of by eye.
    `capture-pane -p` strips the one attribute that separates Claude Code's
    ghost-text suggestion from text somebody typed and never submitted, so a
    human reading a pane has been guessing — and on 2026-08-01 guessed wrong and
    ran the stranded-input SOP on a suggestion (aegis-c6hli).

    EVERY VERDICT PRINTS ITS EVIDENCE. The SOP was run on a confident reading of
    a capture that had thrown the deciding bit away; nothing in that output could
    have contradicted it. The raw prompt line is shown so a second pair of eyes
    can check the verdict rather than inherit it.

    NOTHING HERE SUBMITS. Keys go through Panes.control, whose allowlist has no
    Enter and no Tab — Tab as deliberately as Enter, because Tab ACCEPTS the
    suggestion and would inject it into the agent's turn.
    """
    from . import input_box
    reg = _registry(a)
    panes = _panes(a)
    try:
        card = reg.get(a.agent)
    except Exception as e:
        print(f"  no such agent {a.agent!r}: {e}", file=sys.stderr)
        return REFUSED
    if not card.pane or not panes.exists(card.pane):
        print(f"  {a.agent} has no live pane — nothing to read.", file=sys.stderr)
        return REFUSED

    # The picker check is the RUNTIME's answer, passed in exactly as work_state
    # takes `awaiting` — this module knows no runtime's chrome. Without it an
    # agent blocked on a permission prompt reads TYPED, because the picker marks
    # its selected option with the same glyph the input box uses.
    from .runtime import asks_a_question
    runtime = _runtime(a, panes)
    plain = triage_mod.strip_attrs(panes.capture(card.pane, attrs=True))
    awaiting = bool(asks_a_question(runtime, plain))

    if a.clear:
        rep = input_box.clear(panes, card.pane, awaiting=awaiting)
    elif a.dismiss:
        rep = input_box.dismiss(panes, card.pane)
    else:
        rep = input_box.show(panes, card.pane, awaiting=awaiting)

    print(f"  {a.agent}: {rep.verdict}")
    if rep.text:
        print(f"    text     : {rep.text}")
    if rep.evidence:
        # repr() so the escapes are VISIBLE — the bytes are the evidence, and a
        # terminal rendering them would hide the very attribute in question.
        print(f"    evidence : {rep.evidence!r}")
    if rep.detail:
        print(f"    note     : {rep.detail}")
    if rep.verdict == input_box.GHOST and not (a.clear or a.dismiss):
        print("    (a suggestion, not stranded input — the buffer is empty. "
              "Nothing is stalled; do NOT run the stranded-input SOP on this.)")

    if not rep.changed:
        return CANNOT_TELL
    if rep.verdict == input_box.UNKNOWN:
        return CANNOT_TELL
    if a.clear and rep.verdict != input_box.EMPTY:
        return REFUSED
    return OK


def _resolve_pane(a):
    """(panes, card, screen, awaiting) for a live agent pane, or None on refusal.

    Shared by ask/answer/input so the three cannot drift on the one thing they
    must agree about: whether a PICKER is up. `awaiting` is the RUNTIME's verdict,
    passed in exactly as work_state and input_box take it.
    """
    from .runtime import asks_a_question
    reg, panes = _registry(a), _panes(a)
    try:
        card = reg.get(a.agent)
    except Exception as e:
        print(f"  no such agent {a.agent!r}: {e}", file=sys.stderr)
        return None
    if not card.pane or not panes.exists(card.pane):
        print(f"  {a.agent} has no live pane — nothing to read.", file=sys.stderr)
        return None
    screen = panes.capture(card.pane, attrs=True)
    awaiting = bool(asks_a_question(_runtime(a, panes),
                                    triage_mod.strip_attrs(screen)))
    return panes, card, screen, awaiting


def _print_question(agent: str, q) -> None:
    """The block verbatim, then the options NUMBERED — the whole point (w30p2).

    Verbatim because paraphrasing an approval prompt is how an operator approves
    something other than what they read. The options carry their own numbers from
    the screen rather than being re-numbered here, so `st answer <agent> N` and
    what the agent will act on are the same N by construction.
    """
    print(f"  {agent} is blocked on a question:\n")
    for ln in q.context:
        print(f"    │{ln}")
    print()
    for o in q.options:
        mark = "❯" if o.selected else " "
        print(f"    {mark} {o.n}. {o.text}")
        if o.detail:
            print(f"         {o.detail}")
    if q.footer:
        print(f"\n    ({q.footer})")
    print(f"\n  Answer it: st answer {agent} <N>")


def _cmd_ask(a) -> int:
    """ask <agent> — print the question an agent is blocked on. READ-ONLY.

    THE COMMAND THAT REPLACES `capture-pane -p -t <pane> | tail -12`. The
    coordinator ran that six times in one evening across five agents, hand-typing
    a socket name and a pane name every time — and three of those panes were
    `aegis-crew-*` while most of the fleet is `shanty-*`, so the naming era had to
    be recalled per agent, at the moment of deciding whether to approve someone
    else's shell command. The card already knows the pane (aegis-w30p2).

    Reading the options MACHINE-WISE is the safety half. Option 2 is not a
    stable thing: measured across three live pickers on one night it was "Yes,
    and don't ask again for: curl …", "No, exit", and "Spaces". A coordinator
    who has learned what 2 means has learned something false.
    """
    got = _resolve_pane(a)
    if got is None:
        return REFUSED
    panes, card, screen, awaiting = got
    if not awaiting:
        print(f"  {card.name}: no blocking picker is up — nothing is being "
              f"asked. (`st input {card.name} --show` for what is in its box.)")
        return OK

    from .runtime import reads_a_question
    q = reads_a_question(_runtime(a, panes), screen)
    if q is None:
        # NOT "no question" — the runtime says one IS up. Saying otherwise would
        # tell a coordinator a blocked agent is fine, which is the whole class of
        # bug this area keeps closing.
        print(f"  {card.name}: a picker IS up, but its options could not be "
              f"read off this frame — so this is a could-not-tell, not an "
              f"all-clear. `st attach {card.name}` and read it.", file=sys.stderr)
        return CANNOT_TELL
    _print_question(card.name, q)
    return OK


def _cmd_answer(a) -> int:
    """answer <agent> N — select option N on an agent's blocking picker.

    THE ONE COMMAND HERE THAT ACTS ON ANOTHER AGENT'S SESSION, so it echoes what
    it selected and refuses in every direction it can: not a picker, unreadable
    picker, N out of range, N past what a keystroke can address. It also records
    who answered what, before it acts — an approval granted into someone else's
    pane must not rely on anybody remembering they granted it (aegis-6hfmi spent
    a cross-session argument on exactly that question).

    THERE IS NO --yes AND THERE WILL NOT BE. A permission prompt is a DECISION;
    the value of it being a decision is that a person made it. One keystroke is
    the goal, zero is a different and worse thing (aegis-apz9).
    """
    from . import picker
    got = _resolve_pane(a)
    if got is None:
        return REFUSED
    panes, card, _, awaiting = got
    res = picker.answer(panes, card.pane, a.n, awaiting=awaiting, agent=card.name)
    if not res.ok:
        print(f"  {card.name}: {res.detail}", file=sys.stderr)
        return REFUSED
    # ECHO WHAT WAS SELECTED, not the number that was typed. The number is what
    # the operator already knew; the TEXT is the thing they may have got wrong.
    print(f"  {card.name}: selected {res.option.n}. {res.option.text}")
    if not res.changed:
        print(f"    note     : {res.detail}", file=sys.stderr)
        return CANNOT_TELL
    return OK


def _cmd_attach(a, *, execer=_exec_attach, which=None) -> int:
    """attach [agent] [-r] — attach to a crew member, STARTING them if down.

    st already knows the socket (declared_socket) and the pane (the registry), so
    the operator never types `tmux -L gt-ae5f35 attach -t shanty-weaver`. Refuses
    cleanly on an unknown agent — never a raw tmux error — the same discipline as
    go/stop.

    IT LAUNCHES A DOWN AGENT (owner-directed). "weaver is down, run `st crew`" is a
    true sentence that answers a question nobody asked: the operator typing
    `st attach weaver` has already decided they want to be in weaver's pane, and
    making them run a second command to get there is the whole cold-start friction.
    The launch goes through the same `_launch` seam as `st new`, so an agent
    attached-into-existence is provisioned, workspace-checked and hook-verified
    exactly like one `st new` made.

    `--no-start` is for the caller that must promise it creates nothing — a script
    attaching to whatever is already running. That need is real, so it stays
    reachable; it is the FLAG rather than the default because the frequent, human
    case should be the default and the careful, scripted case should be the one
    that says so.
    """
    import shutil
    which = which or shutil.which
    reg = _registry(a)
    panes = _panes(a)
    socket = declared_socket(getattr(a, "root", None) or ".")

    name = a.agent
    if not name:
        # DEFAULT TO THE ADMINISTRATOR (aegis-83w2), not $SHANTY_AGENT. The
        # tier-root coordinator is who the operator most often wants to look at,
        # and st knows it from the registry — so `st attach` with no arg opens the
        # coordinator, and an explicit <agent> still targets that agent. Falling
        # back to $SHANTY_AGENT would open the OPERATOR's own pane, which they are
        # already in; the useful default is the one they'd otherwise type.
        from .tier import _find_administrator
        try:
            name = _find_administrator(reg)
        except Exception as e:
            print(f"  could not tell: {e}", file=sys.stderr)
            return CANNOT_TELL
        if not name:
            # No administrator in the registry — LIST, don't error.
            try:
                agents = reg.all()
            except Exception as e:
                print(f"  could not tell: {e}", file=sys.stderr)
                return CANNOT_TELL
            print("  no administrator to default to — attach to which? name one:")
            for ag in sorted(agents, key=lambda x: x.name):
                print(f"    {ag.name}")
            return OK

    try:
        card = reg.get(name)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if not card.pane:
        # NO PANE ON THE CARD IS STILL A REFUSAL, even though a down agent is not.
        # `_launch` would invent an `st-<name>` session, but a session that is not
        # on the card is invisible to `st crew`, `st stop` and `st tend` — so
        # attaching into one would create an agent only this command can find.
        # The fix is the card, and it is one command.
        print(f"  refused: {name} has NO pane on its card, so there is no session "
              f"to attach to or start. Add a `pane` to "
              f"{Path(a.root) / 'crew' / f'{name}.json'} (e.g. "
              f"\"shanty-{name}\") — the card projection does not assign one.",
              file=sys.stderr)
        return REFUSED

    if not panes.exists(card.pane):
        where = f"socket {socket!r}" if socket else "the default tmux server"
        if a.no_start:
            print(f"  refused: {name} is down — no live pane {card.pane} on "
                  f"{where}, and --no-start says do not launch it. `st crew` to "
                  f"see who is up.", file=sys.stderr)
            return REFUSED
        print(f"  {name} is down (no {card.pane} on {where}) — starting it, then "
              f"attaching.")
        runtime = _runtime(a, panes)
        rc = _launch(a, card, panes, runtime, dry_run=False)
        if rc == REFUSED:
            # The launcher already said WHY. Do not attach into a session that
            # either does not exist or came up broken enough to refuse.
            print(f"  refused: could not start {name} — not attaching.",
                  file=sys.stderr)
            return REFUSED
        if rc == CANNOT_TELL:
            # LAUNCHED BUT UNVERIFIED -> ATTACH ANYWAY, LOUDLY. The session
            # exists; what could not be established is that the runtime came up
            # (or that its hooks are wired). The single most useful next action is
            # to put the human's eyes on that pane — which is what they asked for.
            # Exiting instead would hide the evidence behind a second command.
            print(f"  ⚠ {name} was launched but NOT verified live (see above) — "
                  f"attaching so you can see the pane itself.", file=sys.stderr)
        elif not panes.exists(card.pane):
            print(f"  could not tell: started {name} but {card.pane} still does "
                  f"not exist — nothing to attach to.", file=sys.stderr)
            return CANNOT_TELL

    argv, env = _attach_argv(card.pane, socket, a.read_only,
                             which("shanty") is not None)
    return execer(argv, env)


def _cmd_tend(a) -> int:
    """tend — one supervision pass, or manage the timer that runs them.

    Exit codes carry the finding, not just the run: 0 = looked, nothing wrong
    (respawning something is not "wrong" — it is the job); 1 = REFUSED (an
    install collision, an unknown agent); 2 = the pass found a FAULT it could not
    fix (a retired agent alive, an agent that cannot report, a launch it refused).
    A supervisor that always exits 0 is a supervisor nobody can alert on.
    """
    if a.retire or a.unretire:
        return _tend_retire(a)
    if a.install:
        st_bin = "st"
        changed, msg = sup_mod.install(st_bin, Path(a.root), interval=a.interval,
                                       run=None if a.dry_run else _run_cmd,
                                       is_active=_systemctl_user_active,
                                       dry_run=a.dry_run)
        print(f"  {msg}")
        return OK if changed or "already installed" in msg or a.dry_run else REFUSED
    if a.uninstall:
        changed, msg = sup_mod.uninstall(run=None if a.dry_run else _run_cmd)
        print(f"  {msg}")
        return OK if changed or "not installed" in msg else REFUSED
    if a.status:
        return _tend_status(a)

    # THE SOCKET GUARD, and it prevents a fleet-destroying interaction between two
    # of this repo's own features. `tend --install` runs from a systemd --user
    # timer, which has NO $TMUX at all — so on a host whose fleet lives on a named
    # socket, an undeclared socket makes every agent look DOWN to the supervisor,
    # and a supervisor that sees the whole fleet dead RESPAWNS THE WHOLE FLEET,
    # onto a different server, duplicating every agent. Measured before the
    # declaration existed: `crew --count` from a caller with no $TMUX said 0/0
    # while eighteen agents were up. A wrong socket is the one condition under
    # which this command must do NOTHING.
    from . import doctor as _doc
    sock_v, sock_why = _socket_check(a)
    if sock_v == _doc.SOCKET_WRONG:
        print(f"  refused: {sock_why}", file=sys.stderr)
        print("  Supervision does NOTHING on a wrong socket: every agent would "
              "look dead and be respawned onto the wrong server.", file=sys.stderr)
        return REFUSED

    # --reauth AFTER the socket guard, deliberately: on a wrong socket every
    # auth-dead agent would be invisible (or worse, a foreign fleet's panes would
    # be judged), and this branch KILLS sessions.
    if getattr(a, "reauth", False):
        return _tend_reauth(a)

    # --loop <secs>: run passes on an interval, so blocked-worker delivery is
    # PROMPT ON ITS OWN (aegis-w0kk) rather than "on the coordinator's next stop".
    # This is the heartbeat the bead's option 2 names; without a running st tend
    # timer (its systemd install refuses while gastown-crew-watchdog holds the
    # crew), a foreground/backgrounded `st tend --loop 30` is the runnable one.
    loop = getattr(a, "loop", None)
    if not loop:
        return _tend_once(a)
    import time
    print(f"  tend heartbeat: a pass every {loop}s. Blocked workers are pushed to "
          f"their coordinator within one interval. Ctrl-C to stop.",
          file=sys.stderr)
    # THE LOOP'S OWN STALENESS (aegis-arma follow-up, measured). A long-running
    # loop is a MEMORY image of the code at start: the live one ran for two days
    # while the editable install moved under it — every fix (auth-dead gating,
    # the idle-fleet push's bd cwd) landed on disk and reached nothing, the
    # aegis-ttlr class one level up (disk current, PROCESS stale). So the loop
    # watches its own package fingerprint and RE-EXECS itself when the code
    # changes — same argv, fresh import, loud line in between. A supervisor
    # that must be manually restarted to pick up its own fixes is a supervisor
    # that runs old code exactly when it matters.
    fp = _code_fingerprint()
    while True:
        # A CRASHED PASS IS NOT A DEAD SUPERVISOR (aegis-ey7n). The live loop
        # died to one uncaught OSError (ENOSPC in a ledger write) and nothing
        # restarted it — a supervisor with no supervisor. One bad pass logs its
        # traceback and the next interval tries again; a persistent fault
        # repeats loudly every interval, which is exactly what an operator can
        # see and a dead process is exactly what they cannot. KeyboardInterrupt
        # and SystemExit still pass through — Ctrl-C must keep killing it.
        try:
            _tend_once(a, quiet=True)
        except Exception:  # noqa: BLE001 — survive anything a pass can raise
            import traceback
            print(f"  ⚠ st tend: this pass CRASHED — supervision continues; "
                  f"next pass in {loop}s. Traceback:", file=sys.stderr)
            traceback.print_exc()
        now = _code_fingerprint()
        if fp is not None and now is not None and now != fp:
            print("  st tend: the installed code CHANGED under this loop — "
                  "re-exec to run what is on disk.", file=sys.stderr)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        time.sleep(loop)


def _code_fingerprint(pkg=None) -> str | None:
    """A cheap identity for the package code THIS process would import: name,
    mtime and size of every module file. Editable install: these are the
    checkout's files, so a `git pull` changes it. Non-editable: they are the
    venv copy's, so a reinstall changes it. None = could not look — and the
    caller treats that as 'never re-exec', because a supervisor that exec-loops
    on a stat error is worse than one that runs old code."""
    try:
        pkg = Path(pkg) if pkg else Path(__file__).resolve().parent
        parts = [f"{f.name}:{f.stat().st_mtime_ns}:{f.stat().st_size}"
                 for f in sorted(pkg.glob("*.py"))]
        return "|".join(parts) or None
    except OSError:
        return None


def _tend_once(a, quiet: bool = False) -> int:
    panes = _panes(a)
    try:
        agents = _registry(a).all()
    except Exception as e:
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    runtime = _runtime(a, panes)

    # THE USAGE GOVERNOR (aegis-hdqej). The tend pass is the evaluation point —
    # it is the pass that already decides who lives — so this is the one caller
    # that PERSISTS the engaged tier (hysteresis has to survive a process that
    # exists for five seconds every five minutes). A dry run evaluates and prints
    # but writes nothing, like everything else on a dry run.
    gov = _governor(a)
    verdict = gov.evaluate(persist=not a.dry_run) if gov is not None else None
    if verdict is not None and verdict.alarm:
        # EVERY PASS, LOUDLY. A governor that goes quiet when it cannot see the
        # number is indistinguishable from one with nothing to report, and the
        # fleet is spending the whole time.
        print(f"  ⚠ {verdict.alarm}", file=sys.stderr)

    def _respawn(card, session):
        runtime.start(card, session)
        # It is UP again, so the deliberate-stop record is history (#29). tend
        # respawning an `st stop`ped agent is correct and unchanged — a stop is not
        # a retirement — but the record must not outlive the stop it describes, or
        # this agent's NEXT crash reads as somebody's decision.
        _stops(a).forget(card.name)

    tender = tend_mod.Tender(
        panes, runtime, _launches(a),
        spawn=None if a.dry_run else _respawn,
        refresh=None if a.dry_run else _refresh_clone,
        # Decision 7: worktrees too, at the same safe moment. Dry run
        # touches nothing, exactly like the clone refresh above.
        refresh_trees=(None if a.dry_run
                       else lambda card: _refresh_agent_worktrees(a, card)),
        gaps=lambda card: prov_mod.missing_kit(card, Path(a.root)),
        # Backoff + give-up (GitHub #12): a crash-looping agent must cost one
        # launch per interval, not one per pass, and must eventually be retired
        # rather than thrashed forever.
        crashes=sup_mod.CrashLog(Path(a.root)),
        retire=lambda name: _retire_card(a, name),
        log=lambda msg: print(f"  {msg}", file=sys.stderr),
        target=getattr(a, "target", None),
        governed=(None if verdict is None
                  else lambda card: verdict.excludes(card, _catalog(a))),
    )
    rep = tender.pass_over(agents, dry_run=a.dry_run)
    # DELIVER blocked workers to their coordinator (aegis-w0kk). Not on a dry run
    # — a dry run pushes nothing, same as it launches nothing. Deduped, so a
    # heartbeat does not re-spam a still-blocked worker every interval.
    if not a.dry_run:
        _log = lambda msg: print(f"  {msg}", file=sys.stderr)

        # EACH SWEEP FAILS ALONE, LOUDLY — THE SUPERVISOR SURVIVES (aegis-ey7n).
        # These sweeps write ledgers, and a write can fail for reasons that have
        # nothing to do with supervision: the live loop DIED at 22:37:37 on
        # ENOSPC inside Notifier._save — an uncaught OSError killed the whole
        # supervisor, nothing restarted it, and the fleet ran unsupervised for
        # half an hour on the exact night a full disk was also tearing event
        # files (the ev-172 dam). A notification layer must never take the
        # respawn layer down with it: the pass itself (respawn, PassLog) is the
        # job; the pushes are best-effort on top.
        def _sweep(label, fn):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 — any sweep error is survivable
                print(f"  ⚠ tend: the {label} sweep CRASHED ({e!r}) — "
                      f"supervision continues without it this pass", file=sys.stderr)
                return []

        woke = _sweep("blocked-worker", lambda: notify_mod.Notifier(
            Path(a.root), _registry(a), panes, log=_log).sweep(agents, runtime))
        if woke:
            print(f"  ⚠ pushed {len(woke)} blocked worker(s) to their "
                  f"coordinator: {', '.join(woke)}", file=sys.stderr)
        # DRIVE THE CYCLE (aegis-bik9): a saturated IDLE agent is prompted to
        # checkpoint-then-/clear on its own pane, so it self-heals instead of
        # sitting idle-and-refused until a human raw-tmuxes it. Deduped once per
        # saturation episode; the instruction checkpoints BEFORE clearing.
        cycled = _sweep("saturation-cycle", lambda: notify_mod.CycleDriver(
            Path(a.root), _registry(a), panes, refresh=_refresh_clone,
            log=_log).sweep(agents, runtime))
        if cycled:
            print(f"  ⚠ prompted {len(cycled)} saturated agent(s) to checkpoint "
                  f"+ /clear: {', '.join(cycled)}", file=sys.stderr)
        # ALERT THE IDLE FLEET (aegis-nk0e): the SOFT half of Rule Zero. If free
        # feedable workers and dispatchable beads coexist, push the coordinator —
        # a coordinator forgetting to dispatch is the same invisible failure w0kk
        # fixed for blocked workers. Deduped per idle episode, fail-open, and it
        # reuses the SAME free/dispatchable computation as hfta's hard gate.
        idle = _sweep("idle-fleet", lambda: notify_mod.IdleFleetAlerter(
            Path(a.root), _registry(a), panes, runtime, log=_log).sweep(agents))
        if idle:
            print(f"  ⚠ alerted the coordinator — {len(idle)} newly-idle feedable "
                  f"worker(s) with work ready: {', '.join(idle)}", file=sys.stderr)
        # STALLED (aegis-e01l): the PROGRESS-over-time twin of the point-in-time
        # push above — an agent parked idle HOLDING an in_progress item with no
        # pane/item/shell change across the whole threshold window. The weaver
        # case: hours parked on a bead whose blocker had already resolved.
        stalled = _sweep("stalled", lambda: notify_mod.StalledAlerter(
            Path(a.root), _registry(a), panes, runtime, log=_log).sweep(agents))
        # aegis-es1tt: the stalled sweep now REMEDIATES — a self-heal nudge to the
        # agent first, coordinator escalation only if that goes unanswered. (_sweep
        # returns [] on crash, a dict on success.)
        _n = stalled.get("nudged", []) if isinstance(stalled, dict) else []
        _e = stalled.get("escalated", []) if isinstance(stalled, dict) else []
        if _n:
            print(f"  ⚠ self-heal nudged {len(_n)} agent(s) holding a neglected "
                  f"anchor (close-or-release): {', '.join(_n)}", file=sys.stderr)
        if _e:
            print(f"  ⚠ escalated {len(_e)} still-NEGLECTED anchor(s) to the "
                  f"coordinator (self-heal nudge unanswered): {', '.join(_e)}",
                  file=sys.stderr)
        # THE DRAIN (aegis-hdqej). Ask every live agent the tier excludes to
        # commit, push, report what went up, and stop itself. Deduped per drain
        # EPISODE, so a five-minute heartbeat does not re-broadcast every pass —
        # and re-broadcast in full if the tier relaxes and re-engages, because the
        # agents told the first time are gone by then.
        drained = _sweep("drain", lambda: _drain_sweep(a, verdict, agents, panes))
        if drained:
            print(gov_mod.render_drain(drained), file=sys.stderr)
    if not quiet:
        print()
        if verdict is not None:
            # Printed on EVERY pass a governor is configured for, including the
            # wide-open one. "No tier engaged" is a finding — an operator who
            # cannot see the governor working cannot tell it from a governor that
            # is silently off, which is the whole class of bug this repo keeps
            # paying for.
            print(verdict.render())
        print(rep.render())
        print()
    # The health signal, written even on a dry run — "a pass ran" is the fact
    # somebody needs when the supervisor itself has stopped. Recorded AFTER the
    # pass so it can never claim work that did not happen.
    if not a.dry_run:
        sup_mod.PassLog(Path(a.root)).record(rep)
    return OK if rep.healthy() else CANNOT_TELL


def _drain_sweep(a, verdict, agents, panes):
    """Broadcast the drain and report it. Returns the report rows (possibly []).

    DURABLE DELIVERY, non-negotiable: the message must survive the recipient's
    session dying, because at 95% the recipient dying is the intended outcome.
    `st inbox -d`'s own default backend is used, which on a beads deployment puts
    the instruction in the store where `st inbox` will find it even if the pane is
    gone before the agent reads it.

    ONLY LIVE AGENTS ARE TOLD. A down agent has no session to push from, and a
    durable "stop yourself" waiting for it would fire the moment it next comes
    up — a self-perpetuating shutdown nobody asked for, arriving long after the
    tier relaxed.
    """
    if verdict is None or not verdict.tier:
        # Not draining (or no governor). The sweep still runs so the LEDGER gets
        # cleared when a tier relaxes — otherwise the next episode would be
        # deduped against a stale one and half the fleet would never be told.
        gov_mod.DrainLedger(Path(a.root)).clear()
        return []
    inbox = _inbox(a, default="beads")
    me = _me(a) or "st tend"
    drainer = gov_mod.Drainer(
        Path(a.root),
        deliver=lambda who, body: inbox.deliver(who, body, frm=me),
        stops=_stops(a),
        log=lambda msg: print(f"  {msg}", file=sys.stderr))
    return drainer.sweep(agents, verdict, _governor_episode(a),
                         live=lambda ag: bool(ag.pane) and panes.exists(ag.pane),
                         catalog=_catalog(a))


def _governor_episode(a) -> float:
    """When the CURRENT tier engaged — the key a drain broadcast dedupes on."""
    return gov_mod.FilesGovernorState(Path(a.root)).get().since


def _retire_card(a, name: str) -> None:
    """Mark an agent RETIRED on its card — the same durable mechanism
    `st tend --retire` uses, so a crash-loop retirement is visible to, and
    reversible by, exactly the commands an operator already knows.

    It names ITSELF as the actor, not $SHANTY_AGENT. This runs inside a tend
    pass, so the ambient identity is whoever happens to own the supervisor
    process — which is true and useless: the question a reader has is "what
    decided this", and the answer is the crash-loop rule, not a person. It also
    keeps the one automatic retirement path from being mistaken for a
    deliberate one, which is the distinction `retired` exists to carry.
    """
    try:
        reg = _registry(a)
        card = reg.get(name)
        reg.set(replace(card, retired=True, retired_by="st tend (crash-loop)",
                        retired_at=_now_iso()))
    except Exception as e:                        # noqa: BLE001 — never fatal
        print(f"  ⚠ could not retire {name}: {e}", file=sys.stderr)


def _tend_reauth(a) -> int:
    """tend --reauth — relaunch every AUTH-DEAD agent, in one command (aegis-arma).

    THE INCIDENT THIS REPLACES: an operator re-login rotated the shared
    credential and every live agent's session went login-expired at once — nine
    agents, each needing a by-hand `st stop` + `st new`, while every roster
    surface said `idle`. This is that recovery as ONE command, run at the moment
    only the operator can know: AFTER they have re-logged in. /login inside a
    pane is an interactive browser OAuth flow — it cannot be driven for the
    agent, so relaunch (which re-reads the refreshed credential) is the whole
    remedy.

    Deliberately a flag on tend and not an auto-heal on the default pass: a pass
    cannot know whether the operator has re-logged in yet, and relaunching
    against a still-stale credential kill-loops the fleet — each agent comes up,
    dies on its first call, and is killed again next pass, burning its frozen
    context for nothing. Same shape as the --cycle-stale rule in tend.__doc__:
    the supervisor REPORTS, the explicit flag ACTS.

    The verdict is the SAME work_state `st crew` renders (via _crew_states) —
    never a second opinion — and the respawn goes through the SAME Tender as a
    normal pass, so retirement, workspace-ensure, clone-refresh and the
    appeared-while-we-looked race guard all hold. What this adds around them:
    the kill (tend never kills), the ownership guard on it (a name match is not
    permission to kill — same rule as `st stop`), and a liveness verify after.

    HONEST BOUNDARY: the verify proves the process CAME UP, not that it is
    authed — the banner only appears on the first failed API call, so a launch
    against a still-stale credential looks identical here. If the operator did
    not re-log in first, the next `st crew` will say so.
    """
    panes = _panes(a)
    try:
        agents = _registry(a).all()
    except Exception as e:
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    runtime = _runtime(a, panes)
    dead = [ag for ag, state, work, _ in _crew_states(agents, panes, runtime)
            if state == "up" and work.startswith(triage_mod.AUTH_DEAD)]
    if not dead:
        print("  no auth-dead agents — nothing to relaunch.")
        return OK

    relaunch, refused = [], 0
    for card in dead:
        if tend_mod.is_retired(card):
            # Retired-and-alive is already tend's RESURRECTED alarm; reauth must
            # not use auth-death as a side door to relaunching what was
            # deliberately stopped.
            print(f"  skip {card.name}: RETIRED — auth-dead, and deliberately "
                  f"stopped; not relaunching.")
            continue
        if not panes.owns(card.pane):
            print(f"  refused: {card.name} ({card.pane}) was not launched by st — "
                  f"refusing to kill a session st does not own. A name match is "
                  f"not permission to kill. Its own launcher recovers it, or: "
                  f"kill it by hand, then `st new {card.name}` brings it back "
                  f"st-owned.", file=sys.stderr)
            refused += 1
            continue
        relaunch.append(card)
    if a.dry_run:
        for card in relaunch:
            print(f"  would: kill {card.pane} and relaunch {card.name}")
        return REFUSED if refused else OK
    if not relaunch:
        return REFUSED if refused else OK

    print(f"  relaunching {len(relaunch)} auth-dead agent(s): "
          f"{', '.join(c.name for c in relaunch)}")
    stuck = 0
    for card in relaunch:
        panes.kill_session(card.pane)
        if panes.exists(card.pane):
            print(f"  could not tell: killed {card.pane} but it is still there — "
                  f"not relaunching {card.name} over a live session.",
                  file=sys.stderr)
            stuck += 1
            continue
        # The stamp described the DEAD launch; forget it so the respawn's stamp
        # (below) is the one `st crew` judges staleness against.
        _launches(a).forget(card.name)
    relaunch = [c for c in relaunch if not panes.exists(c.pane)]

    # THE SAME respawn path as a normal tend pass — one launcher, not a second.
    # The spawn also stamps what it launched with (same record `st new` writes),
    # so the relaunched agent's settings verdict is measured, not `unknown`.
    def _spawn(card, session):
        runtime.start(card, session)
        # Best-effort, same contract as `st new`: an unstamped agent reports
        # `unknown`, which is the state it is in — never fail the launch.
        _launched_now(a, card.name, _default_settings(a.root)(card))
    tender = tend_mod.Tender(
        panes, runtime, _launches(a),
        spawn=_spawn,
        refresh=_refresh_clone,
        gaps=lambda card: prov_mod.missing_kit(card, Path(a.root)),
        # Backoff + give-up (GitHub #12): a crash-looping agent must cost one
        # launch per interval, not one per pass, and must eventually be retired
        # rather than thrashed forever.
        crashes=sup_mod.CrashLog(Path(a.root)),
        retire=lambda name: _retire_card(a, name),
        log=lambda msg: print(f"  {msg}", file=sys.stderr),
    )
    rep = tender.pass_over(relaunch, dry_run=False)
    print()
    print(rep.render())

    unverified = []
    for card in relaunch:
        if not _observe_live(runtime, panes, card.pane):
            unverified.append(card.name)
    if unverified:
        print(f"  could not tell: {len(unverified)} relaunched agent(s) not "
              f"observed live within the timeout: {', '.join(unverified)} — "
              f"check `st log <agent>`.", file=sys.stderr)
    else:
        print(f"  {len(relaunch)} agent(s) relaunched and observed live.")
    print("  live is not authed: the login banner only shows on the first API "
          "call. If the operator has not re-logged in, they will die again on "
          "first use — `st crew` will name them auth-dead.")
    if refused or stuck:
        return REFUSED
    if unverified or not rep.healthy():
        return CANNOT_TELL
    return OK


def _actor() -> str:
    """WHO is running this — best-effort, and honestly labelled about which
    kind of answer it is.

    A crew session carries $SHANTY_AGENT (the launcher sets it), so that name
    is the good answer. A human at a shell does not, and their unix login is a
    DIFFERENT kind of identity — the two namespaces are not guaranteed
    disjoint, so `braino` alone would be ambiguous between the crew member and
    the account. The `unix:` prefix costs five characters and removes the
    ambiguity permanently. `unknown` when neither is readable: this is an audit
    line, so it must be able to say it does not know rather than guess.
    """
    who = os.environ.get("SHANTY_AGENT")
    if who:
        return who
    try:
        import getpass
        return f"unix:{getpass.getuser()}"
    except Exception:
        return "unknown"


def _now_iso() -> str:
    """UTC ISO-8601, seconds. Sortable, unambiguous, no local-tz guessing."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _tend_retire(a) -> int:
    """Retirement is a WRITE to the card, because it has to survive everything
    that could undo it: the supervisor restarting, the host rebooting, this
    process dying. That is the whole lesson of the watchdog that reverted a
    considered shutdown in under a minute.

    UN-retirement is the ARMING half, and it is the one that needed a
    pre-flight (aegis-6hfmi). --retire only ever REMOVES a card from the
    supervisor's reach, so it cannot make anything launch and is never gated.
    --unretire hands a card back to a supervisor that will start it unattended,
    which makes it the last moment a human is present to notice the card cannot
    actually be started where it claims to live. It had no check at all: it
    re-armed ian, whose card carried a gt-era pane and NO workspace, and
    nothing said a word. tend then launched it — into the supervisor's cwd,
    not into ian's tree — and the fleet had an agent that read defunct in `st
    crew` and live to the supervisor, which nobody connected for two deaths.
    """
    name = a.retire or a.unretire
    reg = _registry(a)
    try:
        card = reg.get(name)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if not hasattr(reg, "set"):
        print("  refused: this registry is read-only; retirement must be "
              "durable and it cannot be written here.", file=sys.stderr)
        return REFUSED
    want = bool(a.retire)

    # THE PRE-FLIGHT, BEFORE THE DRY-RUN BRANCH. A dry run that reported "would
    # mark retired=False" while the real command refuses would be lying about
    # the thing dry runs exist to answer.
    #
    # TWO FAULTS, ONE GATE — the permission fault joining the workspace one.
    # The workspace half
    # is workspace.unlaunchable(); the permission half is `dangerous`, and the
    # SAME THREE CARDS carried both. That is not a coincidence: it is what
    # `retired = true` conceals. A retired card is never launched, so nothing it
    # would get wrong at launch ever becomes a symptom — un-retirement is where a
    # dormant defect becomes a live one, so it is where every launch fault must
    # be asked at once, not one per incident.
    if not want:
        gaps = launchable.launch_gaps(card)
        blocking = [g for g in gaps if g.blocking]
        if blocking and not getattr(a, "force", False):
            why = "; ".join(g.why for g in blocking)
            print(f"  refused: {why}\n"
                  f"  Un-retiring re-arms {name} for UNATTENDED respawn, and a "
                  f"supervisor cannot notice this the way you can right now. "
                  f"Fix the card and run this again, or `--force` to arm it "
                  f"anyway (the fault above does not go away — you go away).",
                  file=sys.stderr)
            return REFUSED
        for gap in blocking:
            # FORCED IS NOT SILENT. The operator overrode a refusal; the reason
            # still gets said, and still gets said in full. A --force that
            # prints nothing trains everyone to pass it by default.
            print(f"  ⚠ FORCED past a launchability fault: {gap.why}")
        # NOT a refusal, and said anyway. Manual mode is a legitimate election
        # (see launch_gaps) — but choosing it BY ACCIDENT was invisible, and the
        # arming moment is the last one where a person is looking.
        for gap in gaps:
            if not gap.blocking:
                print(f"  ⚠ {gap.why}")

    if a.dry_run:
        print(f"  would mark {name} retired={want} (by {_actor()})")
        return OK
    reg.set(replace(card, retired=want, retired_by=_actor(),
                    retired_at=_now_iso()))
    if want:
        print(f"  {name} is RETIRED. `st tend` will not respawn it, and will "
              f"ESCALATE if it finds it alive.")
    else:
        print(f"  {name} is tended again.")
    print(f"  recorded on the card: retired_by={_actor()} "
          f"retired_at={_now_iso()}")
    return OK


def _tend_status(a) -> int:
    """Installed? Active? And WHEN did a pass last run?

    The age is the point. A supervisor that has stopped does not fail — it just
    stops making things better, and that is invisible from the inside. Printing
    "last pass: 4 days ago" is what makes its absence as loud as a failure.
    """
    d = sup_mod.unit_dir()
    svc, tmr = d / sup_mod.SERVICE, d / sup_mod.TIMER
    print()
    print(f"  units       {'installed' if tmr.exists() else 'NOT installed'}"
          f"{'' if not tmr.exists() else (' (ours)' if sup_mod.ours(tmr) else ' (NOT ours)')}")
    print(f"  timer       {'active' if _systemctl_user_active(sup_mod.TIMER) else 'inactive'}")
    other = sup_mod.foreign_supervisor(_systemctl_user_active)
    if other:
        print(f"  ⚠ conflict  {other} is ALSO supervising this crew")
    log = sup_mod.PassLog(Path(a.root))
    age = log.age_seconds()
    if age is None:
        print("  last pass   NEVER (or unreadable) — this is not 'fine'")
    else:
        rec = log.last() or {}
        print(f"  last pass   {int(age)}s ago · acted on {len(rec.get('acted') or [])}"
              f" · {len(rec.get('faults') or [])} fault(s)")
    # THE GOVERNOR, on the status surface an operator already checks (aegis-hdqej).
    # A pure READ — `persist=False` — because `--status` must never move policy;
    # the reason this feature has a status line at all is that a governor nobody
    # can observe is one nobody can tell from a governor that is off.
    gov = _governor(a)
    if gov is not None:
        verdict = gov.evaluate(persist=False)
        print(verdict.render())
        if verdict.alarm:
            print(f"  ⚠ {verdict.alarm}")
        rows = gov_mod.Drainer(Path(a.root), deliver=None, stops=_stops(a)) \
            .report(_governor_episode(a))
        if rows:
            print(gov_mod.render_drain(rows))
    print()
    return OK if tmr.exists() and age is not None else CANNOT_TELL




if __name__ == "__main__":
    raise SystemExit(main())


def _refresh_agent_worktrees(a, card) -> list:
    """Bring this agent's PROJECT WORKTREES current; return warnings.

    The tend-side counterpart to `_keep_current` (aegis-ib65p decision 7). Only
    ever called from the respawn path, where the agent is provably down.
    """
    out = []
    try:
        for repo in guard_mod.discover():
            wt = worktree_for(repo, card.name)
            if not wt.is_dir():
                continue
            if warn := _refresh_worktree(wt):
                out.append(f"{_tree_label(wt)}: {warn}")
    except Exception as e:
        out.append(f"could not sweep worktrees: {e}")
    return out
