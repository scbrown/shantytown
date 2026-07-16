"""shanty — the CLI. Eight commands. Adding a ninth requires deleting one.

    prime · go · crew · roles [--check] · role set · new · stop · log

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
    ap = argparse.ArgumentParser(prog="shanty")
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

    a = ap.parse_args(argv)

    if a.cmd == "prime":
        return _cmd_prime(a)
    if a.cmd == "go":
        return _cmd_go(a)
    if a.cmd == "crew":
        return _cmd_crew(a)
    if a.cmd == "roles":
        return _cmd_roles(a)
    return _not_yet(a.cmd)


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
