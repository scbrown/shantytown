"""The durable-inbox refusal must name the part of the overrun st wrote itself.

Measured twice in a row while closing aegis-ftmfn. `st inbox -d dearing '<491
chars>'` refused with "durable message is 505 chars; this inbox carries at most
493". Both numbers are true and the pair is unactionable: the sender counted 491,
was told 505, and the obvious repair — trim to 493 — fails again, because the cap
is measured AFTER cli attributes a `[from arnold] ` signature onto the text.

Nothing was lying. The refusal reported exactly what it measured, and it did not
answer the question the caller had, which is "how long may MY text be". That is
the same shape as the traps this repo keeps finding: a true report of the wrong
quantity.

The contract:
  · the refusal still says everything it said before (the cap, the pointer-channel
    advice) — this is an added line, not a replacement
  · it names the signature VERBATIM and states the sender's own budget
  · an unattributed send has no overhead and gets no extra line. A note that
    always prints is not a note.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shantytown.cli import main, REFUSED
from shantytown.inbox import MessageTooLong, TrackerInbox
from shantytown.protocols import WorkItem


class _CappedTracker:
    """A tracker that caps its title the way the beads backend does."""
    _TITLE_MAX = 500

    def __init__(self):
        self.created = []

    def create(self, title, **fields):
        self.created.append(title)
        return WorkItem(id="x-1", title=title)


def test_the_exception_carries_the_budget_as_a_number():
    """Not only as prose. A caller subtracting its own overhead must not have to
    parse the sentence back apart — that would be a second source for one number."""
    inbox = TrackerInbox(_CappedTracker(), items=lambda: [])

    with pytest.raises(MessageTooLong) as ei:
        inbox.deliver("dearing", "x" * 600)

    assert ei.value.budget == 493
    assert "493" in str(ei.value)


def _store(tmp_path: Path) -> Path:
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "dearing.json").write_text(json.dumps(
        {"role": "lead", "reports_to": "sattler", "pane": "shanty-dearing"}))
    return tmp_path


def _capped(monkeypatch, sender="arnold"):
    """Drive the CLI over a CAPPED inbox. The files backend caps nothing, so a
    test written against it SKIPS — and a skip is not evidence. This is the
    aegis-6ei8 rule applied to our own test: a check whose output is identical
    whether the feature works or not has not checked anything."""
    from shantytown import cli
    monkeypatch.setattr(cli, "_inbox",
                        lambda a, default="files": TrackerInbox(_CappedTracker(),
                                                                items=lambda: []))
    monkeypatch.setattr(cli, "_me", lambda a: sender)
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: _NoSend())


class _NoSend:
    def exists(self, pane):
        return False

    def send(self, pane, text):
        raise AssertionError("an inbox unit test must never call real send-keys")


def test_refusal_names_the_signature_and_the_senders_own_budget(tmp_path, capsys,
                                                                monkeypatch):
    """THE MEASURED CASE. 491 typed chars is under the advertised 493 and is
    refused anyway, because `[from arnold] ` is 14 more."""
    root = _store(tmp_path)
    _capped(monkeypatch)

    rc = main(["--root", str(root), "inbox", "-d", "dearing", "y" * 491])

    assert rc == REFUSED
    err = capsys.readouterr().err
    # everything it always said
    assert "carries at most 493" in err and "thin pointer channel" in err
    # and the part that makes it actionable
    assert "'[from arnold] '" in err
    assert "budget of 479" in err and "you typed 491" in err


def test_the_named_budget_is_one_that_actually_fits(tmp_path, capsys, monkeypatch):
    """The number the refusal prints has to be TRUE, not merely present — a
    refusal that hands you a second wrong budget is worse than one that hands you
    none. Send exactly the advertised budget and it must go through."""
    root = _store(tmp_path)
    _capped(monkeypatch)

    rc = main(["--root", str(root), "inbox", "-d", "dearing", "y" * 479])

    assert rc != REFUSED, capsys.readouterr().err


def test_one_char_over_the_named_budget_is_refused(tmp_path, capsys, monkeypatch):
    """The other side of the same boundary — otherwise 479 passing proves only
    that something under the cap passes."""
    root = _store(tmp_path)
    _capped(monkeypatch)

    rc = main(["--root", str(root), "inbox", "-d", "dearing", "y" * 480])

    assert rc == REFUSED


def test_an_unattributed_send_gets_no_extra_line(tmp_path, capsys, monkeypatch):
    """POSITIVE CONTROL. With no known sender, `attribute` returns the text bare,
    so there is no overhead to name and the added line must stay silent. A note
    that always prints is not a note."""
    root = _store(tmp_path)
    _capped(monkeypatch, sender=None)

    rc = main(["--root", str(root), "inbox", "-d", "dearing", "y" * 600])

    assert rc == REFUSED
    err = capsys.readouterr().err
    assert "carries at most 493" in err, "it still refuses, and still says why"
    assert "signature st adds for you" not in err


# --- aegis-2bjel: the cap is measured in BYTES, and bd's error says "characters"

class _ByteCappedTracker(_CappedTracker):
    """bd's real shape: 500 UTF-8 BYTES, reported as "characters"."""
    _TITLE_MAX_UNIT = "bytes"


def test_the_budget_is_measured_in_the_unit_the_tracker_ENFORCES():
    """Measured against bd 2026-08-24 with an em dash in the title:
    498 chars/500 bytes CREATES; 499 chars/501 bytes fails "(got 501)";
    500 chars/502 bytes fails "(got 502)". `got N` tracks bytes exactly — bd is
    Go, where len(s) on a string is bytes.

    A message of 493 CHARACTERS containing em dashes is 533 bytes and bd refuses
    it, while a character-counting pre-flight waves it through. That is not a
    cosmetic miscount: the refusal then arrives from bd instead of from here, and
    the caller is told the store failed rather than that their message is long.
    """
    inbox = TrackerInbox(_ByteCappedTracker(), items=lambda: [])

    # 493 characters — exactly the character budget — but 513 bytes.
    body = "K" * 473 + "—" * 20
    assert len(body) == 493
    assert len(body.encode("utf-8")) == 533   # 20 em dashes cost 3 bytes each

    with pytest.raises(MessageTooLong) as ei:
        inbox.deliver("dearing", body)
    assert ei.value.unit == "bytes"
    assert "533" in str(ei.value), "the refusal must quote the size that failed"
    assert "BYTES" in str(ei.value), (
        "a byte budget quoted to someone counting characters is unreconcilable")

    # The counterpart: the SAME character count in ASCII fits and must NOT be
    # refused, or this check has simply become stricter rather than correct.
    inbox.deliver("dearing", "K" * 493)


def test_an_ascii_message_is_unaffected_by_the_unit_change():
    """chars == bytes for ASCII, so every previously-accepted message still is.
    Without this, "measure in bytes" could have silently tightened the cap for
    everyone."""
    inbox = TrackerInbox(_ByteCappedTracker(), items=lambda: [])
    inbox.deliver("dearing", "K" * 493)
    with pytest.raises(MessageTooLong):
        inbox.deliver("dearing", "K" * 494)
