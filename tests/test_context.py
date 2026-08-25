"""The two facts that must never be the same bytes.

    "a none adapter returning nothing and a bobbin that is DOWN returning
     nothing are the same bytes and opposite facts."     — ellie

So the tests that matter are not "does it search". They are:
  - unreachable  -> ContextUnavailable -> exit 2   (I could not look)
  - no hits      -> []                 -> exit 0   (I looked; nothing there)
and the assertion that those two never converge. A check that cannot fail is not
a check; a check whose two outcomes are indistinguishable is worse, because it
looks like one.
"""
from __future__ import annotations

import shutil

import pytest

from shantytown.bobbin import BobbinContext, NoContext
from shantytown.cli import main
from shantytown.protocols import Context, ContextUnavailable, Snippet

DEAD = "http://127.0.0.1:9"          # nothing listens on discard/9
LIVE = "http://bobbin.example.com"


# --- the protocol holds, with two impls ---------------------------------------


def test_both_impls_satisfy_the_protocol():
    """Two implementations or it isn't an interface (protocols.py)."""
    assert isinstance(BobbinContext(), Context)
    assert isinstance(NoContext(), Context)


def test_context_interface_is_one_function():
    """ONE method. If this fails, someone grew the contract — that is the finding,
    and it belongs on the bead, not in the file. (Mirrors test_swap's guard on
    Tracker, which caught a third method inside a day.)"""
    for impl in (BobbinContext, NoContext):
        public = {m for m in vars(impl) if not m.startswith("_")}
        assert public == {"relevant"}, f"{impl.__name__} exposes {public}"


# --- the none-adapter: the leak detector --------------------------------------


def test_none_adapter_returns_empty_and_does_not_raise():
    """[] is an ANSWER here: nothing is configured, so there is nothing to look at.
    It must NOT raise — a `none` context is a valid deployment, not a failure."""
    assert NoContext().relevant("anything", 5).exact() == []


def test_none_adapter_needs_no_backend():
    """The leak test in miniature: no server, no CLI, no network."""
    answer = NoContext().relevant("triage", 1)
    assert answer.exact() == []
    assert "did not look" in answer.how


# --- the hard half: unreachable is NOT empty ----------------------------------


def test_unreachable_raises_rather_than_returning_empty():
    """THE test. A downed bobbin must never look like a quiet one."""
    with pytest.raises(ContextUnavailable):
        BobbinContext(server=DEAD, timeout=10).relevant("triage", 2)


@pytest.mark.skipif(shutil.which("bobbin") is None,
                    reason="needs the bobbin CLI on PATH: without it the call "
                           "fails at the PATH check and never reaches a connect "
                           "attempt, so there are no connection words to carry")
def test_unreachable_error_says_which_failure():
    """'unavailable' alone is a shrug. Carry bobbin's own words out.

    SKIPPED WITHOUT THE BINARY, deliberately, rather than loosened (aegis-qx8hn).
    This asserts on WHICH failure occurred, and which failure occurs depends on
    the environment: with bobbin installed the call reaches DEAD and fails to
    connect; without it, it fails earlier at the PATH check with a different —
    and equally correct — message. Relaxing the assertion to accept both would
    turn a test about specificity into one that cannot fail.

    Nothing is lost in a bare environment: the invariant that actually matters
    (`test_unreachable_raises_rather_than_returning_empty` — a downed bobbin must
    never look like a quiet one) holds on BOTH paths and runs everywhere, and the
    no-binary case has its own test immediately below.
    """
    with pytest.raises(ContextUnavailable) as e:
        BobbinContext(server=DEAD, timeout=10).relevant("triage", 2)
    assert "connect" in str(e.value).lower()


def test_missing_binary_is_unavailable_not_empty(monkeypatch):
    monkeypatch.setattr("shantytown.bobbin.shutil.which", lambda _: None)
    with pytest.raises(ContextUnavailable):
        BobbinContext().relevant("triage", 2)


def test_unparseable_reply_is_unavailable_not_empty(monkeypatch):
    """Exit 0 with garbage is still 'I could not tell'. Guessing [] invents an answer."""
    class R:
        returncode, stdout, stderr = 0, "not json at all", ""
    monkeypatch.setattr("shantytown.bobbin.shutil.which", lambda _: "/x/bobbin")
    monkeypatch.setattr("shantytown.bobbin.subprocess.run", lambda *a, **k: R())
    with pytest.raises(ContextUnavailable):
        BobbinContext().relevant("triage", 2)


def test_reply_without_results_key_is_unavailable(monkeypatch):
    class R:
        returncode, stdout, stderr = 0, '{"count": 0}', ""
    monkeypatch.setattr("shantytown.bobbin.shutil.which", lambda _: "/x/bobbin")
    monkeypatch.setattr("shantytown.bobbin.subprocess.run", lambda *a, **k: R())
    with pytest.raises(ContextUnavailable):
        BobbinContext().relevant("triage", 2)


def test_answered_with_nothing_is_empty_not_an_error(monkeypatch):
    """The mirror image, and it must NOT raise: bobbin answered, count 0."""
    class R:
        returncode, stdout, stderr = 0, '{"count": 0, "results": []}', ""
    monkeypatch.setattr("shantytown.bobbin.shutil.which", lambda _: "/x/bobbin")
    monkeypatch.setattr("shantytown.bobbin.subprocess.run", lambda *a, **k: R())
    assert BobbinContext().relevant("triage", 2).exact() == []


def test_empty_query_refuses_rather_than_reporting_nothing():
    """A precondition failure is not evidence about the codebase."""
    with pytest.raises(ValueError):
        BobbinContext().relevant("   ", 2)


def test_zero_budget_refuses_rather_than_reporting_nothing():
    with pytest.raises(ValueError, match="greater than zero"):
        BobbinContext().relevant("triage", 0)


def test_parses_a_hit(monkeypatch):
    class R:
        returncode = 0
        stdout = (
            '{"count":1,"results":[{"file_path":"a/b.py","start_line":1,'
            '"end_line":9,"score":0.5,"repo":"r","name":"f"}]}'
        )
        stderr = ""
    monkeypatch.setattr("shantytown.bobbin.shutil.which", lambda _: "/x/bobbin")
    monkeypatch.setattr("shantytown.bobbin.subprocess.run", lambda *a, **k: R())
    got = BobbinContext().relevant("x", 1)
    assert got.at_least() == [
        Snippet(path="a/b.py", lines="1-9", score=0.5, repo="r", name="f")
    ]
    assert got.complete is False
    assert "requested budget of 1" in (got.caveat or "")


def test_fewer_hits_than_budget_is_a_complete_answer(monkeypatch):
    class R:
        returncode = 0
        stdout = (
            '{"count":1,"results":[{"file_path":"a/b.py","start_line":1,'
            '"end_line":9,"score":0.5,"repo":"r","name":"f"}]}'
        )
        stderr = ""
    monkeypatch.setattr("shantytown.bobbin.shutil.which", lambda _: "/x/bobbin")
    monkeypatch.setattr("shantytown.bobbin.subprocess.run", lambda *a, **k: R())
    got = BobbinContext().relevant("x", 2)
    assert len(got.exact()) == 1
    assert got.note() is None


def test_cli_renders_budget_caveat_next_to_partial_results(monkeypatch, capsys):
    class R:
        returncode = 0
        stdout = (
            '{"count":1,"results":[{"file_path":"a/b.py","start_line":1,'
            '"end_line":9,"score":0.5,"repo":"r","name":"f"}]}'
        )
        stderr = ""
    monkeypatch.setattr("shantytown.bobbin.shutil.which", lambda _: "/x/bobbin")
    monkeypatch.setattr("shantytown.bobbin.subprocess.run", lambda *a, **k: R())
    assert main(["context", "x", "-b", "1"]) == 0
    captured = capsys.readouterr()
    assert "a/b.py:1-9" in captured.out
    assert "INCOMPLETE" in captured.err
    assert "requested budget of 1" in captured.err


# --- the exit-code contract, which is the actual deliverable -------------------


def test_exit_2_against_a_really_dead_port(monkeypatch, capsys):
    """No mocks: a real subprocess against a port nothing listens on.

    The monkeypatched cases below pin the logic; this one pins that the logic is
    wired to reality. ellie's instruction was "point the unreachable case at a
    dead port rather than waiting for an outage".
    """
    monkeypatch.setenv("BOBBIN_SERVER", DEAD)
    assert main(["context", "triage", "-b", "2"]) == 2
    assert "could not tell" in capsys.readouterr().err


def test_exit_codes_do_not_collapse(monkeypatch, capsys):
    """0-with-nothing and 2 must differ in CODE and in WORDS.

    This is the whole bead. If these two ever return the same code, the
    integration is a lie.
    """
    monkeypatch.setattr("shantytown.bobbin.shutil.which", lambda _: "/x/bobbin")

    class Empty:
        returncode, stdout, stderr = 0, '{"count":0,"results":[]}', ""

    class Down:
        returncode, stdout, stderr = 1, "", "Error: Failed to connect to bobbin server"

    monkeypatch.setattr("shantytown.bobbin.subprocess.run", lambda *a, **k: Empty())
    assert main(["context", "zz"]) == 0
    said_empty = capsys.readouterr().out

    monkeypatch.setattr("shantytown.bobbin.subprocess.run", lambda *a, **k: Down())
    assert main(["context", "zz"]) == 2
    said_down = capsys.readouterr().err

    assert said_empty != said_down
    assert "nothing matched" in said_empty
    assert "could not tell" in said_down


def test_none_adapter_does_not_claim_it_looked(capsys):
    """The none-adapter never asked, so it must not say 'nothing matched'.

    I wrote exactly that sentence for both cases first. It was a lie for `none`
    — the same conflation this command exists to prevent, inside the command.
    """
    assert main(["context", "triage", "--none"]) == 0
    out = capsys.readouterr().out
    assert "did not look" in out
    assert "nothing matched" not in out


def test_refuse_is_exit_1(capsys):
    assert main(["context", " "]) == 1
    assert "refused" in capsys.readouterr().err
