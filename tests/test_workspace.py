"""ensure_workspace — the workspace leg of #5.

Three outcomes, all with a positive AND a negative control: present -> untouched,
absent -> cloned, cannot -> REFUSED. The one that matters most is the refusal: a
launcher that cannot refuse launches agents into directories that do not exist,
and the failure shows up as shell noise inside a pane that already came up.
"""
from __future__ import annotations
import subprocess
from pathlib import Path

import pytest

from shantytown.protocols import Agent
from shantytown.workspace import (WorkspaceError, ensure_workspace, git_clone,
                                  git_origin)


def _fake_clone(marker="cloned"):
    """A cloner that records its calls and produces a real directory."""
    calls = []

    def clone(source, dest):
        calls.append((source, Path(dest)))
        Path(dest).mkdir(parents=True)
        (Path(dest) / marker).write_text(source)

    clone.calls = calls
    return clone


def _fake_origin(marker="cloned"):
    """An origin reader that PAIRS with _fake_clone: it reports what was cloned.

    Deliberately consistent with the fake cloner rather than hardcoded, so the
    fake pair models the real one — real `git clone` leaves an origin that real
    `git remote get-url` reads back. A fake origin that always matched would make
    the idempotence tests pass without exercising the check at all.
    """
    def origin(path):
        f = Path(path) / marker
        return f.read_text() if f.exists() else None
    return origin


# --- no workspace elected: nothing to ensure, and that is not a failure -------

def test_no_workspace_returns_none_and_clones_nothing():
    clone = _fake_clone()
    assert ensure_workspace(Agent(name="ellie"), clone=clone) is None
    assert clone.calls == []


# --- present: idempotent, and the contents are NEVER touched -----------------

def test_present_workspace_is_returned_untouched(tmp_path):
    ws = tmp_path / "crew" / "ellie"
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("uncommitted work")
    clone = _fake_clone()

    card = Agent(name="ellie", workspace=str(ws), workspace_source="git@x:repo.git")
    # The tree IS the card's source (aegis-8p0j gap 2 now checks this), so the
    # adopt is legitimate and the original property holds unchanged.
    ok_origin = lambda p: "git@x:repo.git"          # noqa: E731
    assert ensure_workspace(card, clone=clone, origin=ok_origin) == str(ws)
    # A source on the card must not tempt it into re-cloning or syncing: an
    # agent's workspace holds uncommitted work.
    assert clone.calls == []
    assert (ws / "CLAUDE.md").read_text() == "uncommitted work"


def test_ensure_is_idempotent_across_runs(tmp_path):
    ws = tmp_path / "ellie"
    card = Agent(name="ellie", workspace=str(ws), workspace_source="src")
    clone = _fake_clone()
    origin = _fake_origin()
    first = ensure_workspace(card, clone=clone, origin=origin)
    second = ensure_workspace(card, clone=clone, origin=origin)
    assert first == second == str(ws)
    assert len(clone.calls) == 1, "the second run re-cloned an existing workspace"


# --- absent + source: cloned, and the path returned is real ------------------

def test_absent_workspace_is_cloned(tmp_path):
    ws = tmp_path / "nested" / "ellie"
    clone = _fake_clone()
    card = Agent(name="ellie", workspace=str(ws), workspace_source="git@x:repo.git")

    got = ensure_workspace(card, clone=clone)

    assert got == str(ws)
    assert ws.is_dir(), "returned a path that does not exist"
    assert (ws / "cloned").read_text() == "git@x:repo.git"
    assert len(clone.calls) == 1
    src, dest = clone.calls[0]
    assert src == "git@x:repo.git"
    assert dest != ws, "cloned straight onto the final path — a partial clone would stick"


def test_workspace_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    card = Agent(name="ellie", workspace="~/ws", workspace_source="src")
    assert ensure_workspace(card, clone=_fake_clone()) == str(tmp_path / "ws")


# --- cannot: REFUSE, and leave nothing behind --------------------------------

def test_absent_with_no_source_refuses(tmp_path):
    ws = tmp_path / "ellie"
    card = Agent(name="ellie", workspace=str(ws))
    with pytest.raises(WorkspaceError) as e:
        ensure_workspace(card, clone=_fake_clone())
    assert "workspace_source" in str(e.value)
    assert not ws.exists(), "refused and still created something"


def test_workspace_path_that_is_a_file_refuses(tmp_path):
    ws = tmp_path / "ellie"
    ws.write_text("not a directory")
    card = Agent(name="ellie", workspace=str(ws), workspace_source="src")
    with pytest.raises(WorkspaceError) as e:
        ensure_workspace(card, clone=_fake_clone())
    assert "not a directory" in str(e.value)


def test_failed_clone_leaves_no_partial_workspace(tmp_path):
    """The trap this guards: a half-clone at the final path would make the NEXT
    run take the present-and-idempotent branch and launch into a broken tree."""
    ws = tmp_path / "ellie"

    def dies_partway(source, dest):
        Path(dest).mkdir(parents=True)
        (Path(dest) / "half").write_text("")
        raise WorkspaceError("network died")

    card = Agent(name="ellie", workspace=str(ws), workspace_source="src")
    with pytest.raises(WorkspaceError):
        ensure_workspace(card, clone=dies_partway)
    assert not ws.exists(), "a partial clone was left at the workspace path"
    assert list(tmp_path.iterdir()) == [], "staging debris left behind"


def test_cloner_that_lies_about_success_refuses(tmp_path):
    """A cloner that exits 0 and produces nothing must not yield a path."""
    ws = tmp_path / "ellie"
    card = Agent(name="ellie", workspace=str(ws), workspace_source="src")
    with pytest.raises(WorkspaceError) as e:
        ensure_workspace(card, clone=lambda s, d: None)
    assert "produced no directory" in str(e.value)
    assert not ws.exists()


# --- the real cloner, against a real local git repo (no network) --------------

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_git_clone_clones_a_real_repo(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", cwd=origin)
    (origin / "README.md").write_text("hello")
    _git("add", "-A", cwd=origin)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=origin)

    card = Agent(name="ellie", workspace=str(tmp_path / "ws"),
                 workspace_source=str(origin))
    got = ensure_workspace(card)

    assert Path(got, "README.md").read_text() == "hello"
    assert Path(got, ".git").exists()


def test_git_clone_failure_raises_workspace_error(tmp_path):
    with pytest.raises(WorkspaceError) as e:
        git_clone(str(tmp_path / "nope"), tmp_path / "dest")
    assert "failed" in str(e.value)


# --- present but the WRONG TREE: refuse, never silently adopt (aegis-8p0j) ----
#
# The negative controls are the deliverable here. Before this, `if path.is_dir():
# return` adopted anything shaped like a directory, so an agent could be launched
# into a clone of a different repo and nothing downstream could tell. A happy-path
# test cannot catch that — the happy path was already green while the bug was live.

def test_present_but_clone_of_another_repo_is_refused(tmp_path):
    ws = tmp_path / "ellie"
    ws.mkdir()
    card = Agent(name="ellie", workspace=str(ws), workspace_source="git@x:right.git")
    clone = _fake_clone()

    with pytest.raises(WorkspaceError) as e:
        ensure_workspace(card, clone=clone,
                         origin=lambda p: "git@x:WRONG.git")

    msg = str(e.value)
    # Name BOTH sides. "wrong repo" without the two URLs makes the operator go
    # find them, and the whole point is that the two are hard to tell apart.
    assert "git@x:right.git" in msg and "git@x:WRONG.git" in msg
    assert clone.calls == [], "refused, but cloned anyway"


def test_present_with_unreadable_origin_is_refused_as_UNVERIFIABLE(tmp_path):
    """No origin is CANNOT-TELL, and it must not be reported as 'wrong repo'."""
    ws = tmp_path / "ellie"
    ws.mkdir()
    card = Agent(name="ellie", workspace=str(ws), workspace_source="git@x:right.git")

    with pytest.raises(WorkspaceError) as e:
        ensure_workspace(card, clone=_fake_clone(), origin=lambda p: None)

    msg = str(e.value)
    assert "no readable git origin" in msg
    # We do NOT know it is a different repo, so we must not say it is.
    assert "WRONG" not in msg


def test_no_workspace_source_still_adopts_and_never_reads_origin(tmp_path):
    """Absent a source there is nothing to check AGAINST — inventing one is the
    guessed-remote failure the module already refuses below."""
    ws = tmp_path / "ellie"
    ws.mkdir()
    card = Agent(name="ellie", workspace=str(ws))     # no workspace_source
    looked = []

    got = ensure_workspace(card, clone=_fake_clone(),
                           origin=lambda p: looked.append(p) or "anything")

    assert got == str(ws)
    assert looked == [], "checked an origin with nothing to check it against"


@pytest.mark.parametrize("expected,found", [
    ("git@x:repo.git", "git@x:repo"),        # .git suffix
    ("git@x:repo",     "git@x:repo.git"),
    ("git@x:repo",     "git@x:repo/"),       # trailing slash
    ("git@x:repo.git", "git@x:repo.git/"),   # both
])
def test_cosmetic_url_spellings_are_the_same_repo(tmp_path, expected, found):
    """A false refusal is cheap but not free — don't refuse over a `.git`."""
    ws = tmp_path / "ellie"
    ws.mkdir()
    card = Agent(name="ellie", workspace=str(ws), workspace_source=expected)
    assert ensure_workspace(card, clone=_fake_clone(),
                            origin=lambda p: found) == str(ws)


def test_normalizer_does_NOT_equate_different_transports(tmp_path):
    """ssh and https CAN be the same repo — or a fork on another host. This
    answer decides whether we refuse a launch, so it stays conservative."""
    ws = tmp_path / "ellie"
    ws.mkdir()
    card = Agent(name="ellie", workspace=str(ws),
                 workspace_source="ssh://git@host/x/repo.git")
    with pytest.raises(WorkspaceError):
        ensure_workspace(card, clone=_fake_clone(),
                         origin=lambda p: "https://host/x/repo.git")


# --- the REAL origin reader, against real git (no network) --------------------
#
# Every test above injects a fake origin. That validates the REFUSAL LOGIC and
# nothing about git_origin itself — and an unverified reader that always returned
# None would refuse every existing workspace in production while the suite stayed
# green. Validate the instrument, not just the code that consumes it.

def _real_repo(tmp_path, name="origin"):
    origin = tmp_path / name
    origin.mkdir()
    _git("init", "-q", cwd=origin)
    (origin / "README.md").write_text("hello")
    _git("add", "-A", cwd=origin)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=origin)
    return origin


def test_git_origin_reads_a_real_clone_and_is_None_off_a_repo(tmp_path):
    origin = _real_repo(tmp_path)
    card = Agent(name="ellie", workspace=str(tmp_path / "ws"),
                 workspace_source=str(origin))
    ws = Path(ensure_workspace(card))

    assert git_origin(ws) == str(origin), "cannot read back an origin it just cloned"
    # The cannot-tell arm, on the real reader: a plain directory has no origin.
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_origin(plain) is None


def test_real_clone_is_idempotent_through_the_real_origin_check(tmp_path):
    """The end-to-end property gap 2 must not break: clone once, adopt after.

    This is the test that would have caught an over-strict check. The fakes are
    self-consistent by construction; real git has to actually agree with them.
    """
    origin = _real_repo(tmp_path)
    card = Agent(name="ellie", workspace=str(tmp_path / "ws"),
                 workspace_source=str(origin))
    first = ensure_workspace(card)
    second = ensure_workspace(card)          # real git_origin, real clone
    assert first == second


def test_real_clone_of_the_WRONG_repo_is_refused_end_to_end(tmp_path):
    right = _real_repo(tmp_path, "right")
    wrong = _real_repo(tmp_path, "wrong")
    ws = tmp_path / "ws"
    ensure_workspace(Agent(name="ellie", workspace=str(ws),
                           workspace_source=str(wrong)))          # clone the WRONG one
    assert ws.is_dir()

    card = Agent(name="ellie", workspace=str(ws), workspace_source=str(right))
    with pytest.raises(WorkspaceError) as e:
        ensure_workspace(card)               # no fakes anywhere in this path
    assert str(right) in str(e.value) and str(wrong) in str(e.value)
