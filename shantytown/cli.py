"""st — the CLI. Twelve commands, and the count is load-bearing: each earns its slot.

    prime · go · mail · task · crew · roles [--check] · role set · new · stop · log
    · context · doctor [--install]

The binary is `st`, not `shanty`: `shanty` is Stiwi's tmux command and ours would
shadow it on PATH. A harness that steals the operator's own command name has
already made itself the centre of the world.

Gas Town ships ~110. This is not a smaller version of that list; it is the short
set we measurably use, and the discipline is the point (docs/cli.md). The surface
grew past the original ten by two, each on a specific ask — not drift:
  · context — the bobbin Context protocol (aegis-rhhw)
  · doctor  — out-of-box tool detect/install, Stiwi's direct ask (aegis-q9eh)
The count is PINNED by a test (tests/test_command_count.py): the next command
either updates this number or fails CI. This docstring used to say "ten" while the
code had eleven (context landed unannounced) — a count nobody enforces is a
comment, and in a repo whose whole thesis is the exact count, that is the bug.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from . import beads as beads_mod
from . import roles as roles_mod
from .dispatch import Dispatcher, TriageRefused
from .files import FilesRegistry, FilesTracker, plate as files_plate
from .prime import Unreachable, prime as do_prime
from .tmux import Tmux

# 0 did it | 1 refused (precondition) | 2 could not tell (backend unreachable)
OK, REFUSED, CANNOT_TELL = 0, 1, 2


def _registry(root: Path) -> FilesRegistry:
    return FilesRegistry(root / "crew")


def _tracker(a):
    """The tracker for this invocation, selected by --backend (aegis-kbuz #3).

    arnold added beads.plate() (the reader) but the CLI still wired FilesTracker
    unconditionally, so `st --backend beads` did not exist and his plate was
    unreachable. This wires it: --backend beads reaches BeadsTracker; --repo is
    bd's -C. Identity (registry) stays files — work lives in beads, identity does
    not.
    """
    if getattr(a, "backend", "files") == "beads":
        return beads_mod.BeadsTracker(repo=getattr(a, "repo", None))
    return FilesTracker(a.root / "items")


def _plate(a):
    """The plate reader matching the selected tracker — uses arnold's beads.plate
    for the beads backend (his is canonical; my duplicate was dropped)."""
    trk = _tracker(a)
    if getattr(a, "backend", "files") == "beads":
        return lambda who: beads_mod.plate(trk, who)
    return lambda who: files_plate(trk, who)


def _wire(a) -> Dispatcher:
    return Dispatcher(_registry(a.root), _tracker(a), Tmux())


def build_parser() -> argparse.ArgumentParser:
    """The full `st` parser. Exposed so tests/test_command_count.py can introspect
    the command surface and pin it to the docstring — the count is the thesis."""
    ap = argparse.ArgumentParser(prog="st")
    ap.add_argument("--root", type=Path, default=Path.cwd() / ".shanty")
    ap.add_argument("--backend", choices=["files", "beads"], default="files",
                    help="tracker backend (identity is always files). #3")
    ap.add_argument("--repo", default=None,
                    help="bd -C <dir> when --backend beads")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("prime", help="who am I, what's on my plate")
    pr.add_argument("me", nargs="?", help="defaults to $SHANTY_AGENT")

    go = sub.add_parser("go", help="dispatch an item to an agent")
    go.add_argument("item")
    go.add_argument("agent")
    go.add_argument("-n", "--dry-run", action="store_true")

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

    ml = sub.add_parser("mail", help="send a message to an agent (tmux send-keys)")
    ml.add_argument("agent")
    ml.add_argument("message", nargs="+")
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
    return _not_yet(a.cmd)


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
    """role set <agent> <role> [--reports a,b] — GENERATIVE (aegis-rpo1).

    Writes the card AND emits the stop-hook routing in one operation, so a lead
    card and its routing cannot disagree. Refuses (exit 1) on any rule violation
    — orphaned reports, a lead under a lead (depth 2), an unknown agent — BEFORE
    writing anything, so a bad hierarchy never half-lands.
    """
    from . import tier
    reports = [r.strip() for r in a.reports.split(",") if r.strip()]
    try:
        plan = tier.role_set(_registry(a.root), a.agent, a.role,
                             reports=reports, dry_run=a.dry_run)
    except (LookupError, ValueError) as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    print(("  would write:" if a.dry_run else "  wrote:"))
    print(plan.render())
    if a.dry_run:
        print("\n  --dry-run: nothing written.")
    return OK


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
    """mail IS send-keys. That is the whole implementation, and it is the point.

    Stiwi, 2026-07-16: "st mail should just be tmux send keys."

    There is no bus, no queue, no store, no delivery guarantee — because there is
    nothing between the sender and the pane. `gt nudge --mode immediate` says the
    same thing in its own help ("Send directly via tmux send-keys"); Gas Town just
    wrapped it in a mail command, a queue, and a router first. We measured what
    that wrapping costs: 47 nudges sat queued for a mayor that does not exist,
    oldest 25 days, across FIVE spellings of the recipient — because a queue
    accepts a message for a reader that will never come. send-keys cannot: the
    pane is either there or it is not, and you are told which.

    So the only failure modes are the two honest ones:
      REFUSED (1)      no such agent, or the agent has no pane
      CANNOT_TELL (2)  the pane is named but the multiplexer says it is gone
    """
    msg = " ".join(a.message)
    try:
        agent = _registry(a.root).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if agent.pane is None:
        print(f"  refused: {agent.name} has no pane in the registry", file=sys.stderr)
        return REFUSED
    panes = Tmux()
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
        p = do_prime(me, _registry(a.root), Tmux(), plate=_plate(a))
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


def _cmd_go(a) -> int:
    d = _wire(a)
    if a.dry_run:
        try:
            decision = d.triage(a.item, a.agent)
            p = d.go(a.item, a.agent, dry_run=True)
        except LookupError as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        print(p.render()); print("\n  triage: " + decision.render())
        print("  0 writes. 1 tracker call, 1 send-keys.")
        return OK
    try:
        p = d.go(a.item, a.agent)
    except TriageRefused as e:
        # #1: pane not ready (in-flight/wedged/high-context). No write, no send.
        print(f"  refused: pane not ready — {e.decision.render()}", file=sys.stderr)
        return REFUSED
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    print(f"  {p.item_id} -> {p.agent}          in progress")
    print(f"  sent to pane {p.pane}")
    return OK


def _cmd_crew(a) -> int:
    panes = Tmux()
    try:
        agents = _registry(a.root).all()
    except Exception as e:
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    if not agents:
        print("  no agents. `shanty new <agent>`.")
        return OK
    print()
    for ag in sorted(agents, key=lambda x: x.name):
        if ag.pane:
            state = "up" if panes.exists(ag.pane) else "down"
        else:
            state = "no pane"          # not "down" — we did not look
        print(f"  {ag.name:<11} {ag.role:<14} {state:<8} {ag.pane or '—'}")
    print()
    return OK


def _cmd_roles(a) -> int:
    if not a.check:
        try:
            agents = _registry(a.root).all()
        except Exception as e:
            print(f"  could not tell: {e}", file=sys.stderr)
            return CANNOT_TELL
        print()
        for ag in sorted(agents, key=lambda x: x.name):
            print(f"  {ag.name:<11} {ag.role:<14} "
                  f"reports_to: {ag.reports_to or '—'}")
        print()
        return OK

    rep = roles_mod.check(_registry(a.root))
    print()
    print(rep.render())
    print()
    return {roles_mod.OK: OK,
            roles_mod.BROKEN: REFUSED,
            roles_mod.CANNOT_TELL: CANNOT_TELL}[rep.verdict]


def _not_yet(cmd: str) -> int:
    """role set / new / stop / log are specified but not built.

    Refusing is the honest answer. The alternative — a stub that prints
    something plausible and exits 0 — is the exact defect this repo was built in
    reaction to: a command that reports success for work it did not do. It
    exits 1 (refused: a precondition failed — the command does not exist yet).

    role set / new / stop additionally need protocol surface that does not exist:
    Registry has no write, and Panes has no session lifecycle (only send/exists).
    Adding either is a design decision, not an implementation detail. See the
    note on aegis-gqr8.
    """
    print(f"  refused: `shanty {cmd}` is specified in docs/cli.md but not built "
          f"yet. It is not a stub and will not pretend to work.", file=sys.stderr)
    return REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
