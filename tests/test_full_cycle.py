"""THE DONE GATE — a full crew cycle on shantytown, ZERO gt.

dearing's gate for the epic: "one crew member runs a FULL cycle on st with ZERO
gt — that's 'done'." Built SHIM-ONLY per her directive — every pane is a NullPanes,
so the loop NEVER launches a real agent (an earlier live-fire probe proved a real
`claude` can survive teardown; the loop must never spawn one). The cycle:

    role set (emit hooks) -> prime -> task -> prime(plate) -> new -> go(dispatch)
    -> log -> inbox -d(durable, survives, readable) -> stop -> stop-event routes + drains

Each step asserts its MEASURED outcome (exit code + the state it changed), and the
final check asserts NO real claude process was spawned anywhere in the cycle — the
safety guardrail as an executable assertion, not a hope.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import pytest

from shantytown import cli, stop_event
from shantytown.cli import main, OK, CANNOT_TELL
from shantytown.events import FilesEvents
from shantytown.tmux import NullPanes


READY = " ▐▛███▜▌   Claude Code v2.1.214\n  ⏸ manual mode on · ? for shortcuts"


class _CrewPanes(NullPanes):
    """One shared shim for the whole cycle: session-mode (new/stop manage the live
    set) with the real ready banner as its screen (so new's verify sees 'live' and
    go's verify sees the echoed dispatch). Purely in-memory — no tmux, no claude."""
    def __init__(self, live):
        super().__init__(screen=READY, live=live)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    # a worker (ellie) reporting to a lead (maldoon) under an admin (hammond)
    (root / "crew" / "ellie.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "maldoon", "pane": "crew-ellie"}))
    (root / "crew" / "maldoon.json").write_text(json.dumps(
        {"role": "lead", "reports_to": "hammond", "pane": "crew-maldoon"}))
    (root / "crew" / "hammond.json").write_text(json.dumps(
        {"role": "administrator", "pane": "crew-hammond"}))
    return root


def test_full_crew_cycle_on_st_zero_gt(workspace, monkeypatch, capsys):
    root = workspace
    live: set[str] = set()                     # nobody up yet
    panes = _CrewPanes(live)
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    monkeypatch.setattr(stop_event, "Tmux", lambda: panes)
    monkeypatch.setattr(cli, "_LIVE_ATTEMPTS", 1)
    monkeypatch.setattr(cli, "_LIVE_DELAY", 0)

    def run(*argv):
        rc = main(["--root", str(root), *argv])
        return rc

    # 1. role set — emits the per-role settings.json (the hooks st new reads)
    assert run("role", "set", "ellie", "worker") == OK
    assert (root / "settings" / "worker.settings.json").is_file()

    # 2. prime — identity + EMPTY plate
    assert run("anchor", "ellie") == OK
    assert "nothing" in capsys.readouterr().out.lower()

    # 3. task — create work assigned to ellie
    assert run("task", "fix", "the", "widget", "-a", "ellie") == OK

    # 4. prime — the item now APPEARS on the plate (measured transition)
    assert run("anchor", "ellie") == OK
    assert "fix the widget" in capsys.readouterr().out

    # 5. new — bring ellie up: session created, --settings composed, verify live
    assert run("new", "ellie") == OK
    assert panes.exists("crew-ellie"), "new did not create the session"
    _, launch = panes.sent[-1]
    assert "SHANTY_AGENT=ellie" in launch and "--settings" in launch

    # 6. go — dispatch the item to the live pane; verify it landed -> in_progress
    #    (find the created item id from the tracker)
    item_id = next(p.stem for p in (root / "items").glob("*.json"))
    assert run("go", item_id, "ellie") == OK
    assert json.loads((root / "items" / f"{item_id}.json").read_text())["status"] == "in_progress"

    # 7. log — capture the pane (reads the ready UI back)
    assert run("log", "ellie") == OK
    assert "Claude Code" in capsys.readouterr().out

    # 8. inbox -d — durable message to a DOWN agent (maldoon not live): survives
    #    in maldoon's INBOX, and — the half that was missing until the inbox
    #    existed — is READABLE back. It must also stay OFF the plate: `st anchor`
    #    for maldoon still says "nothing", because a message is not work.
    #    --backend files is EXPLICIT and load-bearing: `-d` defaults to BEADS, and
    #    this cycle is the files world end to end. A test that took the default
    #    would write a real bead into the shared store on every run — which it did
    #    once, before this flag was added.
    assert run("--backend", "files", "inbox", "-d", "maldoon", "HANDOFF", "the", "epic") == OK
    capsys.readouterr()
    assert run("--backend", "files", "inbox", "--count", "maldoon") == OK
    assert capsys.readouterr().out == "1\n", "durable message did not reach the inbox"
    assert run("anchor", "maldoon") == OK
    assert "nothing" in capsys.readouterr().out.lower(), "a message reached the plate"

    # 9. stop — kill ellie's session, VERIFIED gone
    assert run("stop", "ellie") == OK
    assert not panes.exists("crew-ellie"), "stop left the session alive"

    # 10. stop-event routing: ellie stops. maldoon (lead) is DOWN (not in live) ->
    #     the event RISES to the admin, durably, then the admin DRAINS it once.
    monkeypatch.setenv("SHANTY_AGENT", "ellie")
    assert stop_event.main(["send", "--root", str(root)]) == 0
    # the event ROSE to the admin (lead down) and survived on the store
    assert [e.rose for e in FilesEvents(root / "events").drain("hammond")] == [True]
    # re-persist a fresh one so the admin's drain path has something to deliver
    monkeypatch.setenv("SHANTY_AGENT", "ellie")
    assert stop_event.main(["send", "--root", str(root)]) == 0
    capsys.readouterr()                        # clear accumulated stdout first
    monkeypatch.setenv("SHANTY_AGENT", "hammond")
    assert stop_event.main(["drain", "--root", str(root)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "block" and "ellie stopped" in payload["reason"]
    assert "lead-unreachable" in payload["reason"], "a rise must carry its reason"

    # SAFETY GUARDRAIL (dearing): the entire cycle spawned NO real claude. Assert
    # it — the loop is shim-only by construction, and this proves it.
    probe = subprocess.run(["pgrep", "-af", "claude --settings"],
                           capture_output=True, text=True)
    assert str(root) not in probe.stdout, \
        f"a real claude was spawned in the cycle: {probe.stdout!r}"
