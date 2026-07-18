"""st role set — the CLI wiring for the tier (aegis-rpo1).

Was `_not_yet` (refused: not built). Now real. Tests the exit-code contract:
0 did it, 1 refused. Measured directly, not through a pipe — the pipe-masks-$?
bug (aegis-eu3s) is why the exit codes went unverified the first time by hand.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown.cli import main, OK, REFUSED


def crew(tmp_path: Path, **agents) -> Path:
    d = tmp_path / "crew"; d.mkdir()
    for n, spec in agents.items():
        (d / f"{n}.json").write_text(json.dumps(spec))
    return tmp_path


def test_role_set_creates_the_tier(tmp_path):
    root = crew(tmp_path, arnold={"role": "administrator"},
                malcolm={"role": "worker"}, ellie={"role": "worker"})
    rc = main(["--root", str(root), "role", "set", "malcolm", "lead", "--reports", "ellie"])
    assert rc == OK
    d = json.loads((root / "crew" / "malcolm.json").read_text())
    assert d["role"] == "lead" and d["reports_to"] == "arnold"  # auto-wired
    assert json.loads((root / "crew" / "ellie.json").read_text())["reports_to"] == "malcolm"


def test_role_set_dry_run_writes_nothing(tmp_path):
    root = crew(tmp_path, arnold={"role": "administrator"}, malcolm={"role": "worker"})
    rc = main(["--root", str(root), "role", "set", "malcolm", "lead", "-n"])
    assert rc == OK
    assert json.loads((root / "crew" / "malcolm.json").read_text())["role"] == "worker"


def test_role_set_refuses_lead_under_lead(tmp_path):
    root = crew(tmp_path, arnold={"role": "administrator"},
                malcolm={"role": "lead", "reports_to": "arnold"},
                ellie={"role": "worker", "reports_to": "malcolm"})
    rc = main(["--root", str(root), "role", "set", "ellie", "lead"])
    assert rc == REFUSED   # exit 1, measured — not through a pipe


def test_role_set_refuses_unknown_agent(tmp_path):
    root = crew(tmp_path, arnold={"role": "administrator"})
    rc = main(["--root", str(root), "role", "set", "ghost", "lead"])
    assert rc == REFUSED
