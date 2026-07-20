"""st — the CLI. Fourteen commands, and the count is load-bearing: each earns its slot.

    anchor [--short|--events|--harness] · go · inbox [--count] · task
    · crew [--count] · roles [--check] · role set · new · stop · log · context
    · doctor [--install] · project · tend [--install|--status]

Five of those flags are MACHINE-READABLE modes, added for an external status bar
(anchor --short/--events/--harness, crew --count, inbox --count). They are flags
and not commands on purpose: the surface is the thesis, and "a status bar wants
this" does not earn a slot. Each prints ONE value and nothing else — docs/cli.md.

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
grew past the original ten by four, each on a specific ask — not drift:
  · context — the bobbin Context protocol
  · doctor  — out-of-box tool detect/install, Stiwi's direct ask
  · project — materialize the crew cards from the graph
  · tend    — crew supervision, native. Owner-directed, and it is a COMMAND and
              not a flag on `st crew` for one reason: `crew` is a read, and this
              is the only surface that can create a session and launch an agent.
              A consequence behind a flag on a read is a consequence somebody
              triggers by running the safe-looking thing.
The count is PINNED by a test (tests/test_command_count.py): the next command
either updates this number or fails CI. This docstring used to say "ten" while the
code had eleven (context landed unannounced) — a count nobody enforces is a
comment, and in a repo whose whole thesis is the exact count, that is the bug.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

from . import beads as beads_mod
from . import harness as harness_mod
from . import roles as roles_mod
from . import triage as triage_mod
from .dispatch import Dispatcher, TriageRefused, SendUnverified, AlreadyAssigned
from .events import FilesEvents
from .inbox import FilesInbox, TrackerInbox
from .triage import Action
from . import supervisor as sup_mod
from . import tend as tend_mod
from . import provision as prov_mod
from .files import FilesRegistry, FilesTracker, plate as files_plate
from .launched import FilesLaunches, CURRENT, STALE, UNKNOWN
from .quipu import QuipuRegistry
from . import selfcheck
from .anchor import Unreachable, anchor as do_anchor
from .runtime import (ClaudeRuntime, CapabilityError, SettingsError,
                      emitted_stop_directions, live_stop_directions, live_wiring,
                      settings_for_role)
from .tmux import Tmux
from .workspace import WorkspaceError, ensure_workspace
from .provision import ProvisionError, provision as provision_ws

# `st new` liveness poll: how long to wait for the runtime to appear in the pane
# before returning could-not-tell (2). Module constants so tests can shrink them
# to (1, 0) — a real launch takes a few seconds, a test must not.
_LIVE_ATTEMPTS = 20
_LIVE_DELAY = 0.25

# 0 did it | 1 refused (precondition) | 2 could not tell (backend unreachable)
OK, REFUSED, CANNOT_TELL = 0, 1, 2


def _registry(a):
    """Identity backend for this invocation, selected by --registry.

    quipu is the SOURCE OF TRUTH (Stiwi: "quipu should be the source of truth");
    files is the projection/cache and the leak detector. Default stays files so
    an offline invocation still resolves identity locally; --registry quipu reads
    it straight from the graph. Either way the SAME roles.check runs over it.
    """
    if getattr(a, "registry", "files") == "quipu":
        return QuipuRegistry()
    return FilesRegistry(a.root / "crew")


def _backend(a, default="files") -> str:
    """The selected tracker backend, or `default` when --backend was not given.

    ONE resolver, because the sentinel only buys honesty if nothing re-guesses
    it. `--backend` now defaults to None so "the user said files" and "the user
    said nothing" stop being the same value — which they had to stop being for
    `mail -d` to default differently without overriding an explicit choice.
    """
    return getattr(a, "backend", None) or default


def _tracker(a, default="files"):
    """The tracker for this invocation, selected by --backend (#3).

    arnold added beads.plate() (the reader) but the CLI still wired FilesTracker
    unconditionally, so `st --backend beads` did not exist and his plate was
    unreachable. This wires it: --backend beads reaches BeadsTracker; --repo is
    bd's -C. Identity (registry) stays files — work lives in beads, identity does
    not.
    """
    if _backend(a, default) == "beads":
        return beads_mod.BeadsTracker(repo=getattr(a, "repo", None))
    return FilesTracker(a.root / "items")


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
    return Dispatcher(_registry(a), _tracker(a), Tmux())


def build_parser() -> argparse.ArgumentParser:
    """The full `st` parser. Exposed so tests/test_command_count.py can introspect
    the command surface and pin it to the docstring — the count is the thesis."""
    ap = argparse.ArgumentParser(prog="st")
    ap.add_argument("--root", type=Path, default=Path.cwd() / ".shanty")
    ap.add_argument("--backend", choices=["files", "beads"], default=None,
                    help="tracker backend (identity is always files). #3. "
                         "Unset means per-command default: files everywhere, "
                         "EXCEPT `mail -d`, which defaults to beads because a "
                         "must-survive message belongs in the shared store "
                         "(dearing, qdal.2). Pass --backend files to force local.")
    ap.add_argument("--repo", default=None,
                    help="bd -C <dir> when --backend beads")
    ap.add_argument("--registry", choices=["files", "quipu"], default="files",
                    help="identity backend: files (projection/default) or quipu "
                         "(the graph, the source of truth).")
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

    cr = sub.add_parser("crew", help="who exists, what state, what role")
    cr.add_argument("--count", action="store_true",
                    help="print ONLY `busy/total` — the same verdict the table "
                         "renders, for a status bar. Agents whose busy/idle state "
                         "is unknown are in NEITHER number")

    rl = sub.add_parser("roles", help="the hierarchy, and whether it's real")
    rl.add_argument("--check", action="store_true")

    rs = sub.add_parser("role", help="role set <agent> <role> [--reports a,b]")
    rs.add_argument("set_", metavar="set", choices=["set"])
    rs.add_argument("agent")
    rs.add_argument("role", choices=["worker", "lead", "administrator"])
    rs.add_argument("--reports", default="", help="comma-separated reports for a lead/administrator")
    rs.add_argument("-n", "--dry-run", action="store_true")

    nw = sub.add_parser("new", help="create an agent from a card")
    nw.add_argument("agent")
    nw.add_argument("-n", "--dry-run", action="store_true")

    st = sub.add_parser("stop", help="stop it")
    st.add_argument("agent")
    st.add_argument("-n", "--dry-run", action="store_true")

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

    pj = sub.add_parser("project", help="materialize the crew cards FROM the graph")
    pj.add_argument("-n", "--dry-run", action="store_true",
                    help="show the diff, write nothing")
    pj.add_argument("--force", action="store_true",
                    help="project even if it restructures LIVE agents")

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
                    help="undo --retire; the agent is tended again")
    td.add_argument("--interval", default="5min",
                    help="with --install: how often a pass runs (default 5min)")
    td.add_argument("-n", "--dry-run", action="store_true",
                    help="say what would be respawned; touch NOTHING")

    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)

    if a.cmd == "anchor":
        return _cmd_anchor(a)
    if a.cmd == "go":
        return _cmd_go(a)
    if a.cmd == "crew":
        return _cmd_crew(a)
    if a.cmd == "roles":
        return _cmd_roles(a)
    if a.cmd == "inbox":
        return _cmd_inbox(a)
    if a.cmd == "task":
        return _cmd_task(a)
    if a.cmd == "context":
        return _cmd_context(a)
    if a.cmd == "role":
        return _cmd_role(a)
    if a.cmd == "doctor":
        return _cmd_doctor(a)
    if a.cmd == "stop":
        return _cmd_stop(a)
    if a.cmd == "log":
        return _cmd_log(a)
    if a.cmd == "new":
        return _cmd_new(a)
    if a.cmd == "project":
        return _cmd_project(a)
    if a.cmd == "tend":
        return _cmd_tend(a)
    return _not_yet(a.cmd)


def _default_settings(root: Path):
    """Resolve a card -> the settings file that wires its ROLE's hooks.

    The file is EMITTED by `role set` / #6; #5 owns the launch seam,
    not the hook-file content. So this resolver READS: it returns the path if the
    role's settings file exists, else None -> compose refuses. That refusal IS the
    invariant working — no settings, no launch, never a settings-less fallback.
    """
    def resolve(card):
        p = Path(root) / "settings" / f"{card.role}.settings.json"
        return str(p) if p.is_file() else None
    return resolve


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


def _cmd_new(a) -> int:
    """new <agent> — bring up a HOOKED agent session (#5).

    new_session (empty pane) -> Runtime.start (compose w/ --settings, send) ->
    verify PROCESS live -> 0/1/2. The order is deliberate: everything that can
    REFUSE (unknown agent, capability, settings) runs BEFORE any tmux mutation, so
    a refusal creates nothing (arnold: 'write nothing, launch nothing').
    """
    panes = Tmux()
    try:
        card = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    runtime = _runtime(a, panes)
    # Fallback session name when the card names no pane. Deliberately prefixed
    # `st-`: a session `st new` creates must never collide with one somebody
    # else's tooling already launched under a name we'd also pick.
    session = card.pane or f"st-{card.name}"
    # PRE-FLIGHT: compose refuses capability/settings BEFORE we touch tmux.
    try:
        launch = runtime.compose(card)
    except (CapabilityError, SettingsError) as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if a.dry_run:
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
    # Clobber guard: never replace a live agent (RAISES if the session exists).
    try:
        panes.new_session(session)
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
    _launches(a).record(card.name, _default_settings(a.root)(card))
    if _observe_live(runtime, panes, session):
        return _verify_live_hooks(a, card, runtime, panes, session)
    # Not observed live. Distinguish "waiting for a human" (a first-run consent
    # prompt) from "unknown" — both are could-not-tell (2), but they need
    # different human actions (live-fire found the consent case).
    final = panes.capture(session)
    if getattr(runtime, "waiting_for_human", None) and runtime.waiting_for_human(final):
        print(f"  could not tell: {a.agent} ({session}) is WAITING ON A PROMPT "
              f"(first-run consent), not up yet. Answer it: `st log {a.agent}` to "
              f"see it, then attach to the pane.", file=sys.stderr)
        return CANNOT_TELL
    print(f"  could not tell: launched {a.agent} but the runtime was not observed "
          f"live in {session} within the timeout. It may still be coming up; "
          f"check `st log {a.agent}`.", file=sys.stderr)
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
        print(f"  started {a.agent} ({session}) — runtime live; the graph "
              f"requires no stop directions of this agent.")
        return OK
    # cmdline is deliberately NOT a Panes protocol method (arnold's non-goal for
    # this bead: Panes gains nothing). We read it off the adapter if it has one;
    # an adapter that cannot show a process cmdline genuinely cannot answer the
    # question, and that is a cannot-tell, not a pass.
    reader = getattr(panes, "cmdline", None)
    wiring = live_wiring(session, reader) if reader else None
    if wiring is None:
        print(f"  could not tell: {a.agent} ({session}) is live, but its stop "
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
        print(f"  FAILED: {a.agent} ({session}) came up WITHOUT the stop hooks "
              f"its position requires. The live process carries {carries}"
              f"{whence}, but this agent needs {sorted(need)} — missing "
              f"{sorted(missing)}. It is running and it is broken: remove it "
              f"with `st stop {a.agent}`, fix the settings it launches with, "
              f"and start it again.", file=sys.stderr)
        return REFUSED
    verified = sorted(need) if need else "none required by the graph"
    print(f"  started {a.agent} ({session}) — runtime live, stop hooks VERIFIED "
          f"on the live process: {verified}.")
    return OK


def _cmd_stop(a) -> int:
    """stop <agent> — kill the agent's session (#5).

    kill_session is idempotent, so this is honest about the two states: an agent
    that is not running is ALREADY the desired end state (exit 0, "was not
    running"); a running one is killed and VERIFIED gone (exit 0, "stopped") or,
    if it is somehow still there after the kill, exit 2 — never a cheerful "done"
    over a session that is still alive.
    """
    panes = Tmux()
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
    print(f"  stopped {a.agent} ({session}).")
    return OK


def _cmd_log(a) -> int:
    """log [agent] — what happened, = capture() on the session pane (arnold's #5
    ruling: log needs NOTHING new, it rides capture). Read-only."""
    panes = Tmux()
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
    self_h = selfcheck.check_self() if len(specs) == len(doc.SPECS) else None

    if not a.install:
        print(doc.report(healths))
        if self_h is not None:
            print(selfcheck.render(self_h))
        return _doctor_exit(doc, healths, self_h)

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
                             reports=reports, dry_run=a.dry_run)
    except (LookupError, ValueError) as e:
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
        panes = Tmux()
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
        p.write_text(json.dumps(settings_for_role(role, root=root), indent=2, sort_keys=True))
        written.append(p)
    return written


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
      REFUSED (1)      no such agent
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
    panes = Tmux()

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
    except Exception as e:                       # bd/store unreachable, etc.
        print(f"  could not tell: durable persist FAILED for {agent.name} "
              f"({type(e).__name__}: {str(e)[:100]}). Nothing guaranteed to "
              f"survive; not downgrading to an ephemeral send.", file=sys.stderr)
        return CANNOT_TELL
    # Persisted. Now best-effort immediacy — never fatal to the durable result.
    if live:
        panes.send(agent.pane, msg)
        print(f"  -> {agent.name}    delivered to inbox as {item.id} ({backend}) + live to {agent.pane}")
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
        print(f"  marked {len(marked)} message(s) read for {me}.")
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
        p = do_anchor(me, _registry(a), Tmux(), plate=_plate(a))
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
        except AlreadyAssigned as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        except LookupError as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        print(p.render()); print("\n  triage: " + decision.render())
        print("  0 writes. 1 tracker call, 1 send-keys.")
        return OK
    try:
        p = d.go(a.item, a.agent, note=note, reassign=a.reassign)
    except AlreadyAssigned as e:
        # Refuse rather than steal. Nothing written, nothing sent — two agents on
        # one item is duplicated effort no tool ever flags (aegis-uvw5 / 7yeb).
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
    panes = Tmux()
    try:
        agents = _registry(a).all()
    except Exception as e:
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    runtime = _runtime(a, panes)
    # --count answers BEFORE the empty-roster line: an empty roster is `0/0`, not
    # a sentence telling a status bar to run `shanty new`.
    if getattr(a, "count", False):
        return _crew_count(agents, panes, runtime)
    if not agents:
        print("  no agents. `shanty new <agent>`.")
        return OK
    launches = _launches(a)
    runtime = _runtime(a, panes)
    free, busy, queued, shelled = [], [], [], []
    verdicts = []
    print()
    for ag, state, work in _crew_states(agents, panes, runtime):
        if work.endswith("sh"):
            shelled.append(f"{ag.name}({work.rsplit('+', 1)[1][:-2]})")
        if work.startswith(triage_mod.IDLE):
            free.append(ag.name)
        elif work.startswith(triage_mod.BUSY):
            busy.append(ag.name)
        elif work.startswith(triage_mod.QUEUED):
            queued.append(ag.name)
        # Only a LIVE agent can be running stale settings. A down agent has no
        # loaded settings to be stale, and will read the current file when it
        # next starts, so reporting on it would be noise that hides the real hits.
        # Shared with `role set` (aegis-qio0): the verdict rule and the bucket
        # rule each exist ONCE, so the column here and the warning there cannot
        # disagree about the same agent.
        verdict = _settings_verdict(launches, ag.name, state == "up")
        verdicts.append((ag.name, verdict))
        print(f"  {ag.name:<11} {ag.role:<14} {state:<8} {verdict:<8} "
              f"{work:<11} {ag.pane or '—'}")
    stale, unknown = _reach_buckets(verdicts)
    print()
    # The dispatcher's answer, said out loud. A column still makes the operator
    # scan 14 rows; the question is "who can take this", so print the list.
    if free:
        print(f"  {len(free)} free: {', '.join(free)}")
    elif busy:
        print("  0 free — every live agent is mid-flight. Dispatching now "
              "interrupts work.")
    if busy:
        print(f"  {len(busy)} busy: {', '.join(busy)}")
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
    if free or busy or queued or shelled:
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
    if stale or unknown:
        print()
    return OK


def _crew_states(agents, panes, runtime):
    """(agent, pane state, work verdict) per agent, by name. THE code path for the
    busy/idle judgment — the table renders it and `--count` counts it, so the
    number a status bar shows can never disagree with the roster a human just
    read. Reimplementing the verdict for the counter is how the two drift.

    WORK: only a live pane has one. A down agent is not idle-and-available, it is
    not there — printing `idle` for it would put it on the free list and send work
    into a session that does not exist. So its verdict is `—`: not looked at.
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
            work = triage_mod.work_state(
                screen, runtime.shows_ready_ui(triage_mod.strip_attrs(screen)))
            # Background shells outlive the turn. An agent whose turn ended with a
            # build/test/`gh run watch` still live is NOT finished, and every
            # surface the administrator has was silent about it. Shown ON the work
            # verdict, because "idle" is exactly the word that would otherwise
            # mislead — `idle+1sh` is idle AND carrying live work.
            shells = triage_mod.running_shells(screen)
            if shells:
                work = f"{work}+{shells}sh"
        else:
            work = "—"
        yield ag, state, work


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
    for _ag, _state, work in _crew_states(agents, panes, runtime):
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
    panes = Tmux()
    rep = roles_mod.check(_registry(a),
                          emitted=lambda role: emitted_stop_directions(a.root, role),
                          live=lambda pane: live_wiring(pane, panes.cmdline))
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
    measured 2026-07-20, the graph declares `luvu` (a HOST — dolt/garage backups
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
    try:
        agents = QuipuRegistry().all()
    except Exception as e:
        print(f"  could not project: quipu unreachable: {e}", file=sys.stderr)
        return CANNOT_TELL

    files = FilesRegistry(a.root / "crew")
    panes = Tmux()
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
    """ff-only pull at the ONE moment it is safe: the agent is down, nothing
    holds the checkout, and no live session can be racing it. Returns an error
    string (loud) or None. NEVER raises — a failure here must not stop a
    respawn, because trading an outage for a stale checkout is the worse deal."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(path), "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=60)
        return None if r.returncode == 0 else (r.stderr or r.stdout).strip()
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

    panes = Tmux()
    try:
        agents = _registry(a).all()
    except Exception as e:
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    runtime = _runtime(a, panes)
    tender = tend_mod.Tender(
        panes, runtime, _launches(a),
        spawn=None if a.dry_run else (lambda card, session: runtime.start(card, session)),
        refresh=None if a.dry_run else _refresh_clone,
        gaps=lambda card: prov_mod.missing_kit(card, Path(a.root)),
        log=lambda msg: print(f"  {msg}", file=sys.stderr),
    )
    rep = tender.pass_over(agents, dry_run=a.dry_run)
    print()
    print(rep.render())
    print()
    # The health signal, written even on a dry run — "a pass ran" is the fact
    # somebody needs when the supervisor itself has stopped. Recorded AFTER the
    # pass so it can never claim work that did not happen.
    if not a.dry_run:
        sup_mod.PassLog(Path(a.root)).record(rep)
    return OK if rep.healthy() else CANNOT_TELL


def _tend_retire(a) -> int:
    """Retirement is a WRITE to the card, because it has to survive everything
    that could undo it: the supervisor restarting, the host rebooting, this
    process dying. That is the whole lesson of the watchdog that reverted a
    considered shutdown in under a minute."""
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
    if a.dry_run:
        print(f"  would mark {name} retired={want}")
        return OK
    from dataclasses import replace
    reg.set(replace(card, retired=want))
    if want:
        print(f"  {name} is RETIRED. `st tend` will not respawn it, and will "
              f"ESCALATE if it finds it alive.")
    else:
        print(f"  {name} is tended again.")
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
    print()
    return OK if tmr.exists() and age is not None else CANNOT_TELL




if __name__ == "__main__":
    raise SystemExit(main())
