#!/usr/bin/env python3
"""Audit crew cards against [harness.required_by_role]."""
from __future__ import annotations

import argparse
from pathlib import Path

from shantytown import config, harness
from shantytown.files import FilesRegistry


def check(root: Path) -> list[str]:
    cfg = config.load(root)
    bad: list[str] = []
    for card in FilesRegistry(root / "crew").all().exact():
        required = cfg.harness_required_by_role.get(card.role)
        if required is None:
            continue
        actual = harness.name_for(card, root=root)
        if actual != required:
            bad.append(
                f"BAD {card.name}: role={card.role} harness={actual} "
                f"required={required}")
    return bad


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    bad = check(args.root)
    if bad:
        print("\n".join(bad))
        return 1
    print("card harness consistency: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
