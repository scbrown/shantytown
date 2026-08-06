"""st role set — the CLI wiring for the tier.

Was `_not_yet` (refused: not built). Now real. Tests the exit-code contract:
0 did it, 1 refused. Measured directly, not through a pipe — the pipe-masks-$?
bug is why the exit codes went unverified the first time by hand.
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
    rc = main(["--root", str(root), "roles", "set", "malcolm", "lead", "--reports", "ellie"])
    assert rc == OK
    d = json.loads((root / "crew" / "malcolm.json").read_text())
    assert d["role"] == "lead" and d["reports_to"] == "arnold"  # auto-wired
    assert json.loads((root / "crew" / "ellie.json").read_text())["reports_to"] == "malcolm"


def test_role_set_dry_run_writes_nothing(tmp_path):
    root = crew(tmp_path, arnold={"role": "administrator"}, malcolm={"role": "worker"})
    rc = main(["--root", str(root), "roles", "set", "malcolm", "lead", "-n"])
    assert rc == OK
    assert json.loads((root / "crew" / "malcolm.json").read_text())["role"] == "worker"


class _NonBlockingHarness:
    # Not named codex: codex declares blocking stop hooks now (harness.py). What
    # this double stands for is the CAPABILITY GAP, not any one program.
    name = "stopless-test"

    def hooks(self, card):
        from shantytown.runtime import HookSpec
        return HookSpec(blocking_stop=False)


def test_role_set_refuses_a_lead_the_harness_cannot_host_and_writes_nothing(tmp_path, monkeypatch):
    """aegis-w5l9 verify recipe: `st role set <it> lead` on a card whose harness
    lacks blocking stop hooks exits 1, the card on disk still reads worker, and
    NO lead settings.json was emitted — the gate fires before any write, not at
    `st new` launch after the card already landed."""
    monkeypatch.setattr("shantytown.harness.for_card", lambda card, root=None: _NonBlockingHarness())
    root = crew(tmp_path, arnold={"role": "administrator"}, malcolm={"role": "worker"})

    rc = main(["--root", str(root), "roles", "set", "malcolm", "lead"])

    assert rc == REFUSED, "an unhostable lead must exit 1"
    assert json.loads((root / "crew" / "malcolm.json").read_text())["role"] == "worker", \
        "the card must be untouched — the gate fired before the write"
    assert not (root / "settings" / "lead.settings.json").exists(), \
        "no lead settings.json may be emitted for a refused role set"


def test_role_set_refuses_lead_under_lead(tmp_path):
    root = crew(tmp_path, arnold={"role": "administrator"},
                malcolm={"role": "lead", "reports_to": "arnold"},
                ellie={"role": "worker", "reports_to": "malcolm"})
    rc = main(["--root", str(root), "roles", "set", "ellie", "lead"])
    assert rc == REFUSED   # exit 1, measured — not through a pipe


def test_role_set_refuses_unknown_agent(tmp_path):
    root = crew(tmp_path, arnold={"role": "administrator"})
    rc = main(["--root", str(root), "roles", "set", "ghost", "lead"])
    assert rc == REFUSED
