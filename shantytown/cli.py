"""st — the CLI. Ten commands. Adding an eleventh requires deleting one.

    prime · go · mail · task · crew · roles [--check] · role set · new · stop · log

The binary is `st`, not `shanty`: `shanty` is Stiwi's tmux command and ours would
shadow it on PATH. A harness that steals the operator's own command name has
already made itself the centre of the world.

Gas Town ships ~110 and we measurably use nine. This is not a smaller version of
that list; it is the nine, and the discipline is the point (docs/cli.md).
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from . import roles as roles_mod
from .dispatch import Dispatcher
from .files import FilesRegistry, FilesTracker, plate as files_plate
from .prime import Unreachable, prime as do_prime
from .tmux import Tmux

# 0 did it | 1 refused (precondition) | 2 could not tell (backend unreachable)
OK, REFUSED, CANNOT_TELL = 0, 1, 2


def _registry(root: Path) -> FilesRegistry:
    return FilesRegistry(root / "crew")


def _tracker(root: Path) -> FilesTracker:
    return FilesTracker(root / "items")


def _wire(root: Path) -> Dispatcher:
    return Dispatcher(_registry(root), _tracker(root), Tmux())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="st")
    ap.add_argument("--root", type=Path, default=Path.cwd() / ".shanty")
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

    rs = sub.add_parser("role", help="role set <agent> <role>")
    rs.add_argument("set_", metavar="set", choices=["set"])
    rs.add_argument("agent")
    rs.add_argument("role")
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

    a = ap.parse_args(argv)

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
    return _not_yet(a.cmd)


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
        item = _tracker(a.root).create(title, assignee=a.assignee)
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
        trk = _tracker(a.root)
        p = do_prime(me, _registry(a.root), Tmux(),
                     plate=lambda who: files_plate(trk, who))
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
    d = _wire(a.root)
    try:
        p = d.go(a.item, a.agent, dry_run=a.dry_run)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if a.dry_run:
        print(p.render()); print("\n  0 writes. 1 tracker call, 1 send-keys.")
    else:
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
