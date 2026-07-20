"""st mail --durable — must-survive messages. shantytown #7.

dearing's ruling: beads-parity on the shared store, durable = must-survive only,
routine = ephemeral. The contract: PERSIST first (the survival guarantee), THEN
best-effort live send. If persist fails, that is CANNOT_TELL — we do NOT silently
downgrade to a routine send and call it success. Routine mail is UNCHANGED (its
own tests still pass); these cover only the -d path.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

import shantytown.cli as cli
from shantytown.cli import main, OK, REFUSED, CANNOT_TELL
from shantytown.protocols import WorkItem


def _root(tmp_path: Path, pane="crew-ian") -> Path:
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    card = {"role": "worker"}
    if pane is not None:
        card["pane"] = pane
    (root / "crew" / "ian.json").write_text(json.dumps(card))
    (root / "crew" / "nopane.json").write_text(json.dumps({"role": "worker"}))
    return root


class _RecordingTracker:
    """Captures create() calls without touching bd or the disk."""
    def __init__(self):
        self.created = []
    def create(self, title, **fields):
        self.created.append((title, fields))
        return WorkItem(id="st-dur1", title=title, status="open",
                        assignee=fields.get("assignee"))


class _DeadTracker:
    """A store that cannot persist — the durability guarantee fails."""
    def create(self, title, **fields):
        raise RuntimeError("bd create failed: connection refused")


# --- durable persists, both liveness outcomes -------------------------------

def test_durable_persists_when_recipient_is_down(tmp_path, monkeypatch):
    """The gap #7 closes: a routine send would VANISH; durable survives as a
    tracker item the recipient reads on next prime. No live send happens."""
    trk = _RecordingTracker()
    monkeypatch.setattr(cli, "_tracker", lambda a, **kw: trk)
    class DownTmux:
        def exists(self, pane): return False
        def send(self, pane, text): raise AssertionError("recipient down — no send")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: DownTmux())
    rc = main(["--root", str(_root(tmp_path)), "mail", "-d", "ian", "HANDOFF: finish the swap"])
    assert rc == OK
    assert len(trk.created) == 1
    title, fields = trk.created[0]
    assert "HANDOFF: finish the swap" in title
    assert fields.get("assignee") == "ian"


def test_durable_persists_AND_sends_when_recipient_is_live(tmp_path, monkeypatch):
    """Persist for survival + send-keys for immediacy — gt mail(bead)+nudge parity."""
    trk = _RecordingTracker()
    sent = []
    monkeypatch.setattr(cli, "_tracker", lambda a, **kw: trk)
    class LiveTmux:
        def exists(self, pane): return True
        def send(self, pane, text): sent.append((pane, text))
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: LiveTmux())
    rc = main(["--root", str(_root(tmp_path)), "mail", "-d", "ian", "protocol step 3"])
    assert rc == OK
    assert len(trk.created) == 1            # survived
    assert sent == [("crew-ian", "protocol step 3")]   # and delivered live


# --- the negative control: persist FAILED must NOT report success -----------

def test_durable_returns_2_when_persist_fails(tmp_path, monkeypatch, capsys):
    """THE one that matters: if the store is unreachable, durability could not be
    guaranteed. That is CANNOT_TELL — never a cheerful 0, and never a silent
    downgrade to an ephemeral send that vanishes."""
    sent = []
    monkeypatch.setattr(cli, "_tracker", lambda a, **kw: _DeadTracker())
    class LiveTmux:
        def exists(self, pane): return True
        def send(self, pane, text): sent.append((pane, text))
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: LiveTmux())
    rc = main(["--root", str(_root(tmp_path)), "mail", "-d", "ian", "must not be lost"])
    assert rc == CANNOT_TELL
    assert "persist FAILED" in capsys.readouterr().err
    assert sent == [], "a failed durable persist must NOT downgrade to a live send"


# --- refusal + dry-run create nothing ---------------------------------------

def test_durable_refuses_unknown_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_tracker",
                        lambda a, **kw: (_ for _ in ()).throw(AssertionError("no persist on refuse")))
    assert main(["--root", str(_root(tmp_path)), "mail", "-d", "nobody", "hi"]) == REFUSED


def test_durable_can_persist_to_a_recipient_with_no_pane(tmp_path, monkeypatch):
    """Durable does NOT require a pane — an agent with no live session is exactly
    who durable mail is for (routine would REFUSE 'no pane'; durable persists)."""
    trk = _RecordingTracker()
    monkeypatch.setattr(cli, "_tracker", lambda a, **kw: trk)
    class DownTmux:
        def exists(self, pane): return False
        def send(self, pane, text): raise AssertionError("no pane — no send")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: DownTmux())
    rc = main(["--root", str(_root(tmp_path)), "mail", "-d", "nopane", "survives"])
    assert rc == OK
    assert len(trk.created) == 1


def test_durable_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_tracker",
                        lambda a, **kw: (_ for _ in ()).throw(AssertionError("dry-run must not persist")))
    class Boom:
        def exists(self, pane): return False
        def send(self, pane, text): raise AssertionError("dry-run must not send")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: Boom())
    rc = main(["--root", str(_root(tmp_path)), "mail", "-d", "-n", "ian", "planned"])
    assert rc == OK


# --- which STORE -d lands in, by default (dearing, qdal.2 follow-up) ---------
#
# The ruling was "beads-parity, store count stays at ONE". The implementation
# honoured it but `--backend` defaulted to files, so a bare `st mail -d` gave the
# LESSER durability: survives the session, but not the host, not a clone being
# cleaned, and invisible to every `bd` query the rest of the crew uses to find
# it. dearing's call: the person who most needs -d is at a session tail and is
# not reading output carefully, so the default has to be the strong one.

def _picked(monkeypatch):
    """Capture which backend the durable path resolved, without touching a store."""
    seen = {}
    trk = _RecordingTracker()

    def fake_tracker(a, default="files"):
        seen["backend"] = cli._backend(a, default)
        return trk
    monkeypatch.setattr(cli, "_tracker", fake_tracker)

    class DownTmux:
        def exists(self, pane): return False
        def send(self, pane, text): raise AssertionError("down — no send")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: DownTmux())
    return seen


def test_durable_defaults_to_BEADS_not_files(tmp_path, monkeypatch):
    seen = _picked(monkeypatch)
    assert main(["--root", str(_root(tmp_path)), "mail", "-d", "ian", "must survive"]) == OK
    assert seen["backend"] == "beads", (
        "a bare `mail -d` fell back to the local files store — the weaker half "
        "of the only guarantee the flag exists to make")


def test_an_EXPLICIT_backend_files_still_wins(tmp_path, monkeypatch):
    """The escape hatch dearing kept: when the store is unreachable,
    local-and-known beats the CANNOT_TELL persist-first would return."""
    seen = _picked(monkeypatch)
    assert main(["--root", str(_root(tmp_path)), "--backend", "files",
                 "mail", "-d", "ian", "local on purpose"]) == OK
    assert seen["backend"] == "files", "an explicit choice was overridden by the default"


def test_ROUTINE_commands_still_default_to_files(tmp_path, monkeypatch):
    """The default changed for -d ONLY. If it leaked to the rest of the CLI,
    every `st prime`/`task` would start shelling out to bd."""
    class A:
        backend = None
    assert cli._backend(A()) == "files"
    assert cli._backend(A(), default="beads") == "beads"
    A.backend = "files"
    assert cli._backend(A(), default="beads") == "files", "explicit files ignored"
