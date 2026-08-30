"""An unreadable EXTRA store must not take the plate reader down (aegis-r2isg seam).

THE OUTAGE THESE TESTS PIN. After the clbx2 cutover the embedded NeuralAmplifier
store became unreadable by br. `rows()` refuses a partial union - correctly, for
a QUERY - but `plate()` sits underneath `st anchor`, the stop event, the governor
and the dashboard. So one unreadable store raised a traceback out of step 1 of
every crew session, fleet-wide, while the PRIMARY store (holding essentially
every agent's plate) was perfectly healthy. Measured 2026-08-30 against
sattler/muldoon/gennaro, not just the author.

The fix is deliberately not "catch it" and not "shorten the answer". Both of
those are the aegis-tisp bug wearing a different hat: an agent whose item lives
in the unreadable store would read as a clean empty plate, and a coordinator
would dispatch over the top of live work. The answer is DEGRADE LOUDLY - return
what is readable, and name the store that is not.
"""
import json
import subprocess

import pytest

from shantytown.br import (BrTracker, _failure_reason, plate, rows,
                          rows_partial)


def _cp(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess(args=["br"], returncode=rc,
                                       stdout=stdout, stderr=stderr)


def _row(id_, assignee, status="open", title="t"):
    return {"id": id_, "title": title, "status": status, "assignee": assignee,
            "priority": 2}


# br reports faults as a JSON envelope on STDOUT and leaves STDERR empty. That
# is not a detail: it is why the original message ended at the colon.
_BR_ERR = json.dumps({"error": {"code": "SYNC_CONFLICT",
                                "message": "issue na-clk does not match"}})


def _fake_stores(monkeypatch, tracker, by_repo, fail=()):
    def fake(self, repo, *args):
        if repo in fail:
            return _cp(stdout=_BR_ERR, rc=6, stderr="")
        if args and args[0] == "list":
            return _cp(stdout=json.dumps({"issues": by_repo.get(repo, [])}))
        return _cp()
    monkeypatch.setattr(BrTracker, "_bd_in", fake, raising=True)
    monkeypatch.setattr(BrTracker, "_bd",
                        lambda self, *a: fake(self, self.repo, *a), raising=True)


def _tracker(tmp_path):
    return BrTracker(repo=str(tmp_path / "aegis"),
                     extra_repos=[str(tmp_path / "na")])


def test_plate_survives_an_unreadable_extra_store(monkeypatch, tmp_path):
    """THE regression. Before the fix this raised RuntimeError for every agent."""
    t = _tracker(tmp_path)
    _fake_stores(monkeypatch, t, {t.repo: [_row("aegis-1", "dearing")]},
                 fail=(t.extra_repos[0],))
    item = plate(t, "dearing", warn=lambda _n: None)
    assert item is not None and item.id == "aegis-1"


def test_the_degradation_NAMES_the_store_and_the_reason(monkeypatch, tmp_path):
    """Loud means identifiable: the next one is a finding, not a mystery.

    sattler read hours of stop events carrying a bare "?" as pane noise. A
    warning that does not name the store reproduces exactly that.
    """
    t = _tracker(tmp_path)
    _fake_stores(monkeypatch, t, {t.repo: [_row("aegis-1", "dearing")]},
                 fail=(t.extra_repos[0],))
    notes = []
    plate(t, "dearing", warn=notes.append)
    assert len(notes) == 1
    assert t.extra_repos[0] in notes[0]
    # the REASON, not just the path - the blank-after-the-colon bug
    assert "SYNC_CONFLICT" in notes[0]


def test_silence_must_be_asked_for_explicitly(monkeypatch, tmp_path, capsys):
    """A caller that forgets `warn` is LOUD, not quiet.

    The failure mode this repo keeps re-learning is a degradation nobody sees.
    Defaulting to stderr means omission cannot reintroduce it.
    """
    t = _tracker(tmp_path)
    _fake_stores(monkeypatch, t, {t.repo: [_row("aegis-1", "dearing")]},
                 fail=(t.extra_repos[0],))
    plate(t, "dearing")          # no warn= on purpose
    assert "PLATE INCOMPLETE" in capsys.readouterr().err


def test_rows_STILL_refuses_a_partial_union(monkeypatch, tmp_path):
    """The union-query contract is unchanged - this fix must not weaken it.

    `rows()` answers "everything", where a short answer at exit 0 is a WRONG
    answer. Only `plate()` - which answers "my one item" - degrades.
    """
    t = _tracker(tmp_path)
    _fake_stores(monkeypatch, t, {t.repo: [_row("aegis-1", "dearing")]},
                 fail=(t.extra_repos[0],))
    with pytest.raises(RuntimeError, match="failed for store"):
        rows(t)


def test_a_healthy_union_reports_no_failures(monkeypatch, tmp_path):
    """The control. Without it, a reader that ALWAYS warns would pass above."""
    t = _tracker(tmp_path)
    _fake_stores(monkeypatch, t, {t.repo: [_row("aegis-1", "dearing")],
                                  t.extra_repos[0]: [_row("na-1", "ellie")]})
    seen, failures = rows_partial(t)
    assert failures == []
    assert {r["id"] for r in seen} == {"aegis-1", "na-1"}
    notes = []
    plate(t, "dearing", warn=notes.append)
    assert notes == []


def test_an_item_in_the_unreadable_store_never_reads_as_a_CLEAN_empty_plate(
        monkeypatch, tmp_path):
    """The aegis-tisp property the original raise was protecting.

    ellie's only item lives in the store that is down. The honest answer is "I
    cannot see your plate", NOT "you have no work" - the latter is what makes a
    coordinator dispatch over the top of live work.
    """
    t = _tracker(tmp_path)
    _fake_stores(monkeypatch, t, {t.repo: [_row("aegis-1", "dearing")]},
                 fail=(t.extra_repos[0],))
    notes = []
    assert plate(t, "ellie", warn=notes.append) is None
    assert notes, "an empty plate from a degraded read must still be announced"


def test_failure_reason_prefers_the_stdout_envelope_over_empty_stderr():
    """The blank-message bug, isolated.

    Reading r.stderr renders a real fault as "". That is how a fleet-wide outage
    printed its own cause and still read as a mystery.
    """
    assert "SYNC_CONFLICT" in _failure_reason(_cp(stdout=_BR_ERR, rc=6))
    # falls back to stderr when there is no envelope
    assert "boom" in _failure_reason(_cp(stdout="", rc=1, stderr="boom"))
    # and NEVER returns "" - a caller must always have something to print
    assert _failure_reason(_cp(stdout="", rc=3, stderr="")).strip()
