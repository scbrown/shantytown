"""`st go --note` — a caveat must ride IN the dispatch payload (aegis-8013).

`st go <item> <agent>` took an item and an agent and nothing else, so a dispatch
that needed a qualifier had two bad options and no good one:

  * `st mail` after the go — send-keys into a pane that has JUST started work.
    That is the mid-flight garble triage REFUSES for `go`; doing it by hand
    routes around the safety rather than using it.
  * a bead comment — durable, out-of-band, and permanent: the note was about THIS
    dispatch at THIS moment, but it lands on the ITEM for every future reader.
    Measured, sattler 2026-07-19: four beads carrying a pull warning that went
    stale within the week.

The point of --note is ATOMICITY, and that is what these tests pin. The note is
composed into the same payload, so it passes the same triage gate and the same
verify: the work and its caveat are delivered together or refused together. A
caveat that arrives separately can arrive after the worker has already acted.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

import shantytown.cli as cli
from shantytown.cli import main, OK, REFUSED
from shantytown.dispatch import Dispatcher, TriageRefused, flatten_note
from shantytown.tmux import NullPanes
from shantytown.files import FilesRegistry, FilesTracker


def _root(tmp_path: Path) -> Path:
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "%5"}))
    (root / "items").mkdir()
    (root / "items" / "item-1.json").write_text(
        json.dumps({"title": "Restore the den", "status": "open"}))
    return root


def _dispatcher(root: Path, panes: NullPanes) -> Dispatcher:
    return Dispatcher(FilesRegistry(root / "crew"), FilesTracker(root / "items"), panes)


# --- the note travels in the ONE send, not a second one ----------------------

def test_note_rides_in_the_same_send_as_the_work(tmp_path):
    """One send-keys, carrying both. Not two sends, and not a send plus a mail."""
    panes = NullPanes(screen="")
    d = _dispatcher(_root(tmp_path), panes)
    d.go("item-1", "ellie", note="do NOT blind-pull; pull YOUR OWN workspace")

    assert len(panes.sent) == 1, (
        f"the note must ride the dispatch payload, not a second send: {panes.sent}"
    )
    _pane, text = panes.sent[0]
    assert "item-1" in text and "Restore the den" in text
    assert "do NOT blind-pull" in text


def test_a_refused_dispatch_carries_the_note_away_with_it(tmp_path):
    """Atomicity in the failure direction: if triage refuses, NOTHING is sent —
    so the caveat cannot land on a pane that never got the work it qualifies."""
    panes = NullPanes(screen="esc to interrupt")     # mid-flight -> REFUSE
    root = _root(tmp_path)
    d = _dispatcher(root, panes)

    with pytest.raises(TriageRefused):
        d.go("item-1", "ellie", note="a caveat that must not arrive alone")

    assert panes.sent == []
    assert json.loads((root / "items" / "item-1.json").read_text())["status"] == "open"


# --- the transport submits on newline, so the note is flattened --------------

def test_a_multiline_note_is_flattened_to_one_line(tmp_path):
    """Panes.send is `send-keys -l <text>` + a separate Enter. A literal newline
    inside the text is a SUBMIT: an unflattened three-line note would dispatch
    line one and type the remaining two into a pane that has already started
    work — the exact mid-flight garble triage exists to prevent, arriving
    through the gate instead of around it."""
    panes = NullPanes(screen="")
    d = _dispatcher(_root(tmp_path), panes)
    d.go("item-1", "ellie", note="a design doc is landing\npull your own workspace\n\ndo NOT blind-pull")

    _pane, text = panes.sent[0]
    assert "\n" not in text, f"a newline in the payload submits early: {text!r}"
    for fragment in ("a design doc is landing", "pull your own workspace", "do NOT blind-pull"):
        assert fragment in text, "flattening must not DROP note content"


def test_flatten_note_collapses_every_whitespace_run():
    assert flatten_note("a\n\n  b\tc \n") == "a b c"
    assert flatten_note("   ") == ""       # whitespace-only == no note


def test_a_whitespace_only_note_leaves_no_dangling_marker(tmp_path):
    panes = NullPanes(screen="")
    d = _dispatcher(_root(tmp_path), panes)
    p = d.go("item-1", "ellie", note="   \n  ")
    assert "NOTE:" not in p.text
    assert p.note == ""


def test_no_note_is_byte_identical_to_the_old_payload(tmp_path):
    """The flag is additive. A dispatch without one must send what it always sent."""
    panes = NullPanes(screen="")
    d = _dispatcher(_root(tmp_path), panes)
    d.go("item-1", "ellie")
    assert panes.sent[0][1] == "Work is on your hook: item-1 — Restore the den"


def test_the_item_id_precedes_the_note_so_verify_can_still_find_it(tmp_path):
    """verify() reads the pane back looking for the item id. A note composed in
    FRONT of the id could push it out of what we can read — the dispatch would
    then land and report could-not-tell, recording nothing."""
    panes = NullPanes(screen="")
    d = _dispatcher(_root(tmp_path), panes)
    p = d.go("item-1", "ellie", note="x" * 400)
    assert p.text.index("item-1") < p.text.index("NOTE:")


# --- the CLI surface ---------------------------------------------------------

def test_cli_note_reaches_the_pane(tmp_path, monkeypatch, capsys):
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    rc = main(["--root", str(_root(tmp_path)), "go", "item-1", "ellie",
               "--note", "aegis-43ph: no clone can fast-forward"])
    assert rc == OK
    assert "aegis-43ph" in panes.sent[0][1]
    # and the sender is shown what actually went out
    assert "note: aegis-43ph" in capsys.readouterr().out


def test_cli_note_file_reaches_the_pane(tmp_path, monkeypatch):
    """--note-file is the aegis-0214 answer: prose with backticks and $(...) in a
    `--note "..."` string is expanded BY THE SHELL before st sees it. A file is
    inert, so the note that arrives is the note that was written."""
    note = tmp_path / "note.md"
    note.write_text("mind `st go` and $(nothing) — inert\n")
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    rc = main(["--root", str(_root(tmp_path)), "go", "item-1", "ellie",
               "--note-file", str(note)])
    assert rc == OK
    assert "mind `st go` and $(nothing) — inert" in panes.sent[0][1]


def test_cli_refuses_an_unreadable_note_file_rather_than_dispatching_without_it(
        tmp_path, monkeypatch, capsys):
    """The dangerous degradation: send the work WITHOUT the caveat. That is the
    failure aegis-8013 exists to close, so an unreadable note is a refusal (no
    send, no tracker write), never a note-less dispatch."""
    panes = NullPanes(screen="")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: panes)
    root = _root(tmp_path)
    rc = main(["--root", str(root), "go", "item-1", "ellie",
               "--note-file", str(tmp_path / "nope.md")])
    assert rc == REFUSED
    assert panes.sent == []
    assert json.loads((root / "items" / "item-1.json").read_text())["status"] == "open"
    assert "could not read --note-file" in capsys.readouterr().err


def test_note_and_note_file_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        main(["--root", str(_root(tmp_path)), "go", "item-1", "ellie",
              "--note", "a", "--note-file", "b"])


def test_dry_run_previews_the_note_as_it_will_be_sent(tmp_path, monkeypatch, capsys):
    """--dry-run that hides the flattening is not a preview of the dispatch."""
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: NullPanes(screen=""))
    rc = main(["--root", str(_root(tmp_path)), "go", "item-1", "ellie",
               "--note", "line one\nline two", "--dry-run"])
    assert rc == OK
    out = capsys.readouterr().out
    assert "carry note" in out and "line one line two" in out
