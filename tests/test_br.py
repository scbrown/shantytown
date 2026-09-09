"""`shantytown.br` — the tracker primitives, and the notes-append contract.

WHY THIS FILE EXISTS (aegis-cchum2). `st defer` wrote `--notes={reason}` as an
unconditional REPLACE, and `br update` refuses to replace non-empty notes with
different content. The two were individually correct and jointly broken: br was
protecting content, st was overwriting it, and neither knew about the other. The
population it broke on is the worst possible one — a bead that has been deferred
before carries its `resume_when:` gate in Notes, and re-deferring is the
commonest defer there is. st tend's self-heal told agents to run that command.
"""
from __future__ import annotations

import subprocess

# --- defer must APPEND to notes, never replace (aegis-cchum2) -----------------

from shantytown.br import merge_notes, NOTES_SEPARATOR


def test_empty_notes_are_written_plainly_and_need_no_force():
    """The common first defer. Nothing to preserve, so nothing to force."""
    assert merge_notes(None, "reason") == ("reason", False)
    assert merge_notes("", "reason") == ("reason", False)
    assert merge_notes("   \n ", "reason") == ("reason", False)


def test_existing_notes_are_PRESERVED_and_the_reason_appended():
    """THE BUG. A previously-deferred bead carries `resume_when:` here, and the
    old code replaced it — which br refused, so defer failed on exactly the
    population it is aimed at."""
    old = "resume_when: date:2026-09-12T00:00:00Z\nRe-test: reconnect and confirm"
    merged, force = merge_notes(old, "deferred again: forge down")
    assert force is True
    assert old in merged, "the resume_when gate must survive the append"
    assert merged.endswith("deferred again: forge down")
    assert merged == old + NOTES_SEPARATOR + "deferred again: forge down"


def test_identical_content_does_not_need_force():
    """br permits a byte-identical write; asking for --force there would be
    reaching for a destructive flag to do nothing."""
    assert merge_notes("same", "same") == ("same", False)
    assert merge_notes("same\n", " same ")[1] is False


def test_an_already_merged_reason_is_not_double_appended():
    """The documented two-step pre-merges before calling. Appending again would
    duplicate the existing notes inside themselves."""
    old = "resume_when: x"
    pre = old + "\n\nand the new reason"
    merged, force = merge_notes(old, pre)
    assert merged == pre and force is True
    assert merged.count("resume_when: x") == 1


def test_force_is_NEVER_returned_without_the_original_surviving():
    """The safety invariant, stated as a property over the cases rather than
    trusting each branch: whenever this asks for --force, the text it asks to
    write CONTAINS what was already there. --force plus a replacement is the
    silent data loss the br guard exists to prevent."""
    for old in ("resume_when: x", "a\nb\nc", "  padded  ", "unicode — em dash"):
        for add in ("new", "resume_when: x\n\nnew", old, "", "  "):
            merged, force = merge_notes(old, add)
            if force:
                assert old in merged, f"forced write drops {old!r} for {add!r}"


def _tracker_with_notes(monkeypatch, existing):
    """A BrTracker whose br calls are captured, with `show` returning `existing`."""
    from shantytown.br import BrTracker
    import json as _json
    calls = []

    def fake(self, item_id, *args):
        calls.append(list(args))
        if args and args[0] == "show":
            body = {"id": item_id}
            if existing is not None:
                body["notes"] = existing
            return subprocess.CompletedProcess([], 0, _json.dumps(body), "")
        return subprocess.CompletedProcess([], 0, "{}", "")

    monkeypatch.setattr(BrTracker, "_bd_for", fake, raising=True)
    return BrTracker(), calls


def _notes_and_force(calls):
    for args in calls:
        if args and args[0] == "update":
            notes = next((a[len("--notes="):] for a in args
                          if a.startswith("--notes=")), None)
            return notes, "--force" in args
    raise AssertionError(f"no update call in {calls}")


def test_update_APPENDS_the_defer_reason_to_existing_notes(monkeypatch):
    """End to end through the tracker, not just the pure helper: the write must
    carry the merged text AND --force, because br refuses a differing replace."""
    old = "resume_when: date:2026-09-12T00:00:00Z"
    tracker, calls = _tracker_with_notes(monkeypatch, old)
    tracker.update("aegis-x", defer_reason="forge is down")
    notes, force = _notes_and_force(calls)
    assert old in notes and notes.endswith("forge is down")
    assert force is True


def test_update_does_not_force_when_there_were_no_notes(monkeypatch):
    """Nothing to preserve — reaching for --force here would normalise a
    destructive flag on the common path."""
    tracker, calls = _tracker_with_notes(monkeypatch, None)
    tracker.update("aegis-x", defer_reason="first defer")
    notes, force = _notes_and_force(calls)
    assert notes == "first defer" and force is False


def test_an_UNREADABLE_item_never_forces(monkeypatch):
    """CANNOT TELL IS NOT EMPTY. If show fails we have not seen the notes, so the
    write must go unforced and let br's own guard refuse it. A refusal costs a
    retry; a blind force costs the resume_when condition permanently."""
    from shantytown.br import BrTracker
    calls = []

    def fake(self, item_id, *args):
        calls.append(list(args))
        if args and args[0] == "show":
            return subprocess.CompletedProcess([], 1, "", "boom")
        return subprocess.CompletedProcess([], 0, "{}", "")

    monkeypatch.setattr(BrTracker, "_bd_for", fake, raising=True)
    BrTracker().update("aegis-x", defer_reason="reason")
    notes, force = _notes_and_force(calls)
    assert notes == "reason" and force is False
