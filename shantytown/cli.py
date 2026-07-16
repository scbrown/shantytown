"""shanty — the CLI. Eight commands. Adding a ninth requires deleting one."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .dispatch import Dispatcher
from .files import FilesRegistry, FilesTracker
from .tmux import Tmux

# 0 did it | 1 refused (precondition) | 2 could not tell (backend unreachable)
OK, REFUSED, CANNOT_TELL = 0, 1, 2


def _wire(root: Path) -> Dispatcher:
    return Dispatcher(FilesRegistry(root / "crew"), FilesTracker(root / "items"), Tmux())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shanty")
    ap.add_argument("--root", type=Path, default=Path.cwd() / ".shanty")
    sub = ap.add_subparsers(dest="cmd", required=True)

    go = sub.add_parser("go", help="dispatch an item to an agent")
    go.add_argument("item")
    go.add_argument("agent")
    go.add_argument("-n", "--dry-run", action="store_true")

    a = ap.parse_args(argv)
    if a.cmd == "go":
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
    return REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
