"""st — the CLI. Thirteen commands, and the count is load-bearing: each earns its slot.

    prime · go · mail · task · crew · roles [--check] · role set · new · stop · log
    · context · doctor [--install] · project

The binary is `st`, not `shanty`: `shanty` is Stiwi's tmux command and ours would
shadow it on PATH. A harness that steals the operator's own command name has
already made itself the centre of the world.

Gas Town ships ~110. This is not a smaller version of that list; it is the short
set we measurably use, and the discipline is the point (docs/cli.md). The surface
grew past the original ten by two, each on a specific ask — not drift:
  · context — the bobbin Context protocol
  · doctor  — out-of-box tool detect/install, Stiwi's direct ask
  · project — materialize the crew cards from the graph
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
from . import roles as roles_mod
from . import triage as triage_mod
from .dispatch import Dispatcher, TriageRefused, SendUnverified, AlreadyAssigned
from .triage import Action
from .files import FilesRegistry, FilesTracker, plate as files_plate
from .launched import FilesLaunches, CURRENT, STALE, UNKNOWN
from .quipu import QuipuRegistry
from .prime import Unreachable, prime as do_prime
from .runtime import (ClaudeRuntime, CapabilityError, SettingsError,
                      emitted_stop_directions, live_stop_directions, live_wiring,
                      settings_for_role)
from .tmux import Tmux
from .workspace import WorkspaceError, ensure_workspace

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

    pr = sub.add_parser("prime", help="who am I, what's on my plate")
    pr.add_argument("me", nargs="?", help="defaults to $SHANTY_AGENT")

    go = sub.add_parser("go", help="dispatch an item to an agent")
    go.add_argument("item")
    go.add_argument("agent")
    note = go.add_mutually_exclusive_group()
    note.add_argument("--note", default=None,
                      help="a caveat delivered IN the same payload as the "
                           "dispatch — it rides the triage gate with the work, "
                           "so it cannot arrive after the worker has acted. "
                           "Flattened to one line (the transport submits on "
                           "newline). aegis-8013")
    note.add_argument("--note-file", type=Path, default=None,
                      help="read the note from a file (or - for stdin). Use this "
                           "for anything long or containing quotes/backticks — "
                           "shell expansion in a --note string is the aegis-0214 "
                           "footgun.")
    go.add_argument("-n", "--dry-run", action="store_true")
    go.add_argument("--reassign", action="store_true",
                    help="take an item another agent already holds. Without this, "
                         "dispatching an assigned item REFUSES rather than silently "
                         "stealing it (aegis-uvw5)")

    sub.add_parser("crew", help="who exists, what state, what role")

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

    ml = sub.add_parser("mail", help="send a message to an agent (tmux send-keys; -d persists)")
    ml.add_argument("agent")
    ml.add_argument("message", nargs="+")
    ml.add_argument("-d", "--durable", action="store_true",
                    help="must-survive: persist to the tracker (beads-parity on "
                         "the shared store with --backend beads), then best-effort "
                         "live send. Default is ephemeral send-keys.")
    ml.add_argument("-n", "--dry-run", action="store_true")

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

    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)

    if a.cmd == "prime":
        return _cmd_prime(a)
    if a.cmd == "go":
        return _cmd_go(a)
    if a.cmd == "crew":
        return _cmd_crew(a)
    if a.cmd == "roles":
        return _cmd_roles(a)
    if a.cmd == "mail":
        return _cmd_mail(a)
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
    for _ in range(_LIVE_ATTEMPTS):
        if runtime.is_live(panes.capture(session)):
            return True
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
                       "(this one IS the hookless-zombie case, cf. aegis-05up)")
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

    if not a.install:
        print(doc.report(healths))
        return doc.exit_code(healths)

    plans = [doc.plan_install(h) for h in healths]
    print(doc.report(healths, plans=plans))
    if a.dry_run:
        return doc.exit_code(healths)  # planned only — nothing ran

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
    print(doc.report(doc.detect_all(specs, check_latest=not a.no_latest)))
    return doc.exit_code(doc.detect_all(specs, check_latest=not a.no_latest))


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
    return OK


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


def _cmd_mail(a) -> int:
    """mail is send-keys by default; -d/--durable persists first (#7).

    ROUTINE (default) — Stiwi, 2026-07-16: "st mail should just be tmux send keys."
    There is no bus, no queue, no store — nothing between the sender and the pane.
    We measured what wrapping it costs: 47 nudges sat queued for a mayor that does
    not exist, oldest 25 days, across FIVE spellings of the recipient — a queue
    accepts a message for a reader that will never come. send-keys cannot: the
    pane is there or it is not, and you are told which. Failure modes stay the two
    honest ones — REFUSED (no agent / no pane), CANNOT_TELL (pane named but gone).

    DURABLE (-d) — the gap #7 closes. Routine send-keys VANISHES if the recipient
    is down, which is wrong for a must-survive message (a handoff, a protocol
    step). gt mail's durability is a bead+Dolt commit; parity here is: PERSIST to
    the tracker FIRST (the survival guarantee), THEN best-effort live send for
    immediacy. Persist-first is deliberate — the store is the source of truth, the
    live send is a bonus; if we persisted but the pane is gone, the message still
    survives and the recipient picks it up on their next `st prime`.

    dearing's ruling: beads-parity on the SHARED store, NOT a dedicated
    store. So durable reuses the SELECTED tracker — run `--backend beads --repo
    <repo>` for a real bead (survives cross-session, cross-host); the portable
    files backend gives a lesser-but-real local durability. We PRINT where it
    landed so the durability is never ambiguous.

    Durable exit codes:
      REFUSED (1)      no such agent
      CANNOT_TELL (2)  could NOT persist (tracker unreachable) — the survival
                       guarantee failed, so we do NOT downgrade to a silent
                       routine send and report success
      OK (0)           persisted (+ delivered live if the pane was there)
    """
    msg = " ".join(a.message)
    try:
        agent = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    panes = Tmux()

    if getattr(a, "durable", False):
        return _mail_durable(a, agent, msg, panes)

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


def _mail_durable(a, agent, msg: str, panes) -> int:
    """Persist-then-deliver. The tracker write is the guarantee; the send is speed."""
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
        print(f"  would: persist a durable message for {agent.name} via {backend}")
        print(f"  would: {'+ live send-keys -> ' + agent.pane if live else 'no live send (recipient down); survives for prime'}")
        print("\n  1 durable write." + (" 1 send-keys." if live else " 0 send-keys."))
        return OK
    # PERSIST FIRST — the survival guarantee. If this cannot be done, the durable
    # promise cannot be kept; say so (2) rather than silently downgrade to routine.
    tracker = _tracker(a, default="beads")
    try:
        item = tracker.create(f"mail: {msg}", assignee=a.agent)
    except Exception as e:                       # bd/store unreachable, etc.
        print(f"  could not tell: durable persist FAILED for {agent.name} "
              f"({type(e).__name__}: {str(e)[:100]}). Nothing guaranteed to "
              f"survive; not downgrading to an ephemeral send.", file=sys.stderr)
        return CANNOT_TELL
    # Persisted. Now best-effort immediacy — never fatal to the durable result.
    if live:
        panes.send(agent.pane, msg)
        print(f"  -> {agent.name}    persisted as {item.id} ({backend}) + delivered live to {agent.pane}")
    else:
        print(f"  -> {agent.name}    persisted as {item.id} ({backend}); "
              f"recipient not live — will pick it up on `st prime`.")
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


def _cmd_prime(a) -> int:
    """prime is a PURE READ. Note what is NOT here: no _wire(), because the
    Dispatcher exists to write. prime resolves its own reads and nothing else."""
    import os
    me = a.me or os.environ.get("SHANTY_AGENT")
    if not me:
        print("  refused: no agent. `shanty prime <you>` or set $SHANTY_AGENT.",
              file=sys.stderr)
        return REFUSED
    try:
        p = do_prime(me, _registry(a), Tmux(), plate=_plate(a))
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except Unreachable as e:
        # NOT success, NOT failure. "I could not look" must never say "fine".
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
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
    if not agents:
        print("  no agents. `shanty new <agent>`.")
        return OK
    launches = _launches(a)
    runtime = _runtime(a, panes)
    stale, unknown, free, busy, queued, shelled = [], [], [], [], [], []
    print()
    for ag in sorted(agents, key=lambda x: x.name):
        if ag.pane:
            state = "up" if panes.exists(ag.pane) else "down"
        else:
            state = "no pane"          # not "down" — we did not look
        # WORK: only a live pane has one. A down agent is not idle-and-available,
        # it is not there — printing `idle` for it would put it on the free list
        # and send work into a session that does not exist.
        if state == "up":
            # attrs=True: work_state needs dim to tell a placeholder suggestion
            # from queued-unsubmitted text (aegis-x6xh). shows_ready_ui matches
            # PLAIN substrings and its markers arrive colour-split word by word,
            # so it gets the stripped view of the very same capture.
            screen = panes.capture(ag.pane, attrs=True)
            work = triage_mod.work_state(
                screen, runtime.shows_ready_ui(triage_mod.strip_attrs(screen)))
            # Background shells outlive the turn (aegis-q73g). An agent whose turn
            # ended with a build/test/`gh run watch` still live is NOT finished,
            # and every surface the administrator has was silent about it. Shown
            # ON the work verdict, because "idle" is exactly the word that would
            # otherwise mislead — `idle+1sh` is idle AND carrying live work.
            shells = triage_mod.running_shells(screen)
            if shells:
                work = f"{work}+{shells}sh"
                shelled.append(f"{ag.name}({shells})")
            if work.startswith(triage_mod.IDLE):
                free.append(ag.name)
            elif work.startswith(triage_mod.BUSY):
                busy.append(ag.name)
            elif work.startswith(triage_mod.QUEUED):
                queued.append(ag.name)
        else:
            work = "—"
        # Only a LIVE agent can be running stale settings. A down agent has no
        # loaded settings to be stale, and will read the current file when it
        # next starts, so reporting on it would be noise that hides the real hits.
        if state == "up":
            verdict = launches.verdict(ag.name)
            if verdict == STALE:
                stale.append(ag.name)
            elif verdict == UNKNOWN:
                unknown.append(ag.name)
        else:
            verdict = "—"
        print(f"  {ag.name:<11} {ag.role:<14} {state:<8} {verdict:<8} "
              f"{work:<11} {ag.pane or '—'}")
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


if __name__ == "__main__":
    raise SystemExit(main())
