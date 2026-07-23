"""st inbox --durable — must-survive messages. shantytown #7.

The ruling: beads-parity on the shared store, durable = must-survive only,
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
from shantytown.inbox import Message, TrackerInbox
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


class _RecordingInbox:
    """Captures deliver() calls without touching bd or the disk. The seam moved
    from the TRACKER to the INBOX (an inbox is a pluggable concept now, selected
    by --backend), so this is what durable mail actually calls."""
    def __init__(self):
        self.delivered = []
    def deliver(self, to, body, frm=None):
        self.delivered.append((to, body, frm))
        return Message(id="st-dur1", to=to, body=body, frm=frm)


class _DeadInbox:
    """A store that cannot persist — the durability guarantee fails."""
    def deliver(self, to, body, frm=None):
        raise RuntimeError("bd create failed: connection refused")


# --- durable persists, both liveness outcomes -------------------------------

def test_durable_persists_when_recipient_is_down(tmp_path, monkeypatch):
    """The gap #7 closes: a routine send would VANISH; durable survives as a
    tracker item the recipient reads on next prime. No live send happens."""
    box = _RecordingInbox()
    monkeypatch.setattr(cli, "_inbox", lambda a, **kw: box)
    class DownTmux:
        def exists(self, pane): return False
        def send(self, pane, text): raise AssertionError("recipient down — no send")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: DownTmux())
    rc = main(["--root", str(_root(tmp_path)), "inbox", "-d", "ian", "HANDOFF: finish the swap"])
    assert rc == OK
    assert len(box.delivered) == 1
    to, body, _frm = box.delivered[0]
    assert body == "HANDOFF: finish the swap"
    assert to == "ian"


def test_durable_persists_AND_sends_when_recipient_is_live(tmp_path, monkeypatch):
    """Persist for survival + send-keys for immediacy — gt mail(bead)+nudge parity."""
    box = _RecordingInbox()
    sent = []
    monkeypatch.setattr(cli, "_inbox", lambda a, **kw: box)
    class LiveTmux:
        def exists(self, pane): return True
        def send(self, pane, text): sent.append((pane, text))
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: LiveTmux())
    rc = main(["--root", str(_root(tmp_path)), "inbox", "-d", "ian", "protocol step 3"])
    assert rc == OK
    assert len(box.delivered) == 1         # survived
    assert sent == [("crew-ian", "protocol step 3")]   # and delivered live


# --- the negative control: persist FAILED must NOT report success -----------

def test_durable_returns_2_when_persist_fails(tmp_path, monkeypatch, capsys):
    """THE one that matters: if the store is unreachable, durability could not be
    guaranteed. That is CANNOT_TELL — never a cheerful 0, and never a silent
    downgrade to an ephemeral send that vanishes."""
    sent = []
    monkeypatch.setattr(cli, "_inbox", lambda a, **kw: _DeadInbox())
    class LiveTmux:
        def exists(self, pane): return True
        def send(self, pane, text): sent.append((pane, text))
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: LiveTmux())
    rc = main(["--root", str(_root(tmp_path)), "inbox", "-d", "ian", "must not be lost"])
    assert rc == CANNOT_TELL
    assert "persist FAILED" in capsys.readouterr().err
    assert sent == [], "a failed durable persist must NOT downgrade to a live send"


# --- too-long is a REFUSAL, not a "could not tell" (internal-ref) --------------

class _CappedTracker:
    """A tracker that caps a title like bd does (TITLE_MAX=500). Records creates so
    a test can prove a too-long message NEVER reached the store."""
    _TITLE_MAX = 500

    def __init__(self):
        self.created = []

    def create(self, title, **fields):
        self.created.append((title, fields))
        return WorkItem(id="st-cap1", title=title, status="open",
                        assignee=fields.get("assignee"))


def test_durable_REFUSES_a_too_long_message_and_does_not_call_it_cannot_tell(tmp_path, monkeypatch, capsys):
    """The bug: a message over the tracker's title cap failed with a leaked bd
    validation string, returned CANNOT_TELL (2, 'store maybe unreachable'), and
    left the agent unable to tell a transient outage from a message that will NEVER
    fit. It is a permanent, actionable REFUSED (1) — and nothing is written."""
    tracker = _CappedTracker()
    box = TrackerInbox(tracker, lambda: [])
    monkeypatch.setattr(cli, "_inbox", lambda a, **kw: box)
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: _NoSend())
    body = "x" * 494                              # title = "inbox: " + 494 = 501 > 500
    rc = main(["--root", str(_root(tmp_path)), "inbox", "-d", "ian", body])
    assert rc == REFUSED, "too-long must be REFUSED (1), not CANNOT_TELL (2)"
    err = capsys.readouterr().err
    assert "refused" in err and "carries at most" in err   # names the real limit
    assert "494" in err, "the refusal states the actual length that overflowed"
    assert "bead" in err, "the refusal must name the remedy (put it in a bead)"
    assert "could not tell" not in err, "must not read as a transient store outage"
    assert tracker.created == [], "a refused message must NOT be written to the store"


def test_durable_delivers_a_message_at_the_cap_boundary(tmp_path, monkeypatch):
    """The other side of the discriminator: a body that exactly fits (title == cap)
    delivers. Proves the refusal is a real boundary, not a blanket rejection."""
    tracker = _CappedTracker()
    box = TrackerInbox(tracker, lambda: [])
    monkeypatch.setattr(cli, "_inbox", lambda a, **kw: box)
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: _NoSend())
    body = "x" * 493                              # title = "inbox: " + 493 = 500 == cap
    rc = main(["--root", str(_root(tmp_path)), "inbox", "-d", "ian", body])
    assert rc == OK
    assert len(tracker.created) == 1


class _NoSend:
    def exists(self, pane): return False
    def send(self, pane, text): raise AssertionError("recipient down — no send")


# --- refusal + dry-run create nothing ---------------------------------------

def test_durable_refuses_unknown_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_inbox",
                        lambda a, **kw: (_ for _ in ()).throw(AssertionError("no persist on refuse")))
    assert main(["--root", str(_root(tmp_path)), "inbox", "-d", "nobody", "hi"]) == REFUSED


def test_durable_can_persist_to_a_recipient_with_no_pane(tmp_path, monkeypatch):
    """Durable does NOT require a pane — an agent with no live session is exactly
    who durable mail is for (routine would REFUSE 'no pane'; durable persists)."""
    box = _RecordingInbox()
    monkeypatch.setattr(cli, "_inbox", lambda a, **kw: box)
    class DownTmux:
        def exists(self, pane): return False
        def send(self, pane, text): raise AssertionError("no pane — no send")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: DownTmux())
    rc = main(["--root", str(_root(tmp_path)), "inbox", "-d", "nopane", "survives"])
    assert rc == OK
    assert len(box.delivered) == 1


def test_durable_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_inbox",
                        lambda a, **kw: (_ for _ in ()).throw(AssertionError("dry-run must not persist")))
    class Boom:
        def exists(self, pane): return False
        def send(self, pane, text): raise AssertionError("dry-run must not send")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: Boom())
    rc = main(["--root", str(_root(tmp_path)), "inbox", "-d", "-n", "ian", "planned"])
    assert rc == OK
