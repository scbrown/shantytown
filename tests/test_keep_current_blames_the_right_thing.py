"""keep-current must not blame the TREE for a dead REMOTE (aegis-ghedod).

Found by arnold during the aegis-u6mdxf forge.invalid outage: his workspace had zero
tracked edits and the dispatch note still said "Clean or reconcile <workspace>".
During an outage every dispatched agent gets that note at once, so the wording
sends the whole fleet hunting for local dirt that does not exist — while the one
true consequence (the tree may be stale) is buried under a false instruction.

`git pull` exits 1 for BOTH a refused merge and an unreachable remote, so the
classifier reads git's wording; these tests pin real git/ssh strings.
"""
from __future__ import annotations

import pytest

from shantytown.cli import _pull_failed_on_the_REMOTE


# Verbatim shapes git/ssh actually emit — the outage on this fleet produced the
# "Connection refused" and "Could not read from remote repository" pair.
REMOTE_FAILURES = [
    "ssh: connect to host forge.invalid port 22: Connection refused",
    "ssh: Could not resolve hostname forge.invalid: Name or service not known",
    "ssh: connect to host forge.invalid port 22: Connection timed out",
    "fatal: Could not read from remote repository.",
    "fatal: unable to access 'http://forge.invalid:3000/stiwi/aegis.git/': Failed to connect",
    "Command '['git', 'pull']' timed out after 60 seconds",
    "subprocess.TimeoutExpired: Command '['git','-C','/x','pull']' timed out",
    "fatal: 'origin' does not appear to be a git repository\nssh: connect to host h port 22: Network is unreachable",
]

# LOCAL failures — these must keep the original "clean or reconcile" wording,
# because for these the tree IS the thing to fix.
LOCAL_FAILURES = [
    "fatal: Not possible to fast-forward, aborting.",
    "error: Your local changes to the following files would be overwritten by merge:\n\tCLAUDE.md",
    "error: cannot pull with rebase: You have unstaged changes.",
    "fatal: refusing to merge unrelated histories",
    "CONFLICT (content): Merge conflict in shantytown/cli.py",
]


@pytest.mark.parametrize("err", REMOTE_FAILURES)
def test_remote_failures_are_classified_as_remote(err):
    assert _pull_failed_on_the_REMOTE(err) is True, (
        f"a dead remote must not be reported as local drift: {err!r}")


@pytest.mark.parametrize("err", LOCAL_FAILURES)
def test_local_failures_keep_the_reconcile_wording(err):
    """The CONTROL ARM. Without it, a classifier that always returned True would
    pass every test above while destroying the message for real local drift."""
    assert _pull_failed_on_the_REMOTE(err) is False, (
        f"a local-drift refusal must keep the reconcile wording: {err!r}")


def test_an_unknown_failure_falls_through_to_todays_wording():
    """Unknown -> False -> existing message. This only ever NARROWS a message we
    already print, so a shape we have not seen must not claim a cause."""
    assert _pull_failed_on_the_REMOTE("fatal: something nobody has seen") is False
    assert _pull_failed_on_the_REMOTE("") is False
    assert _pull_failed_on_the_REMOTE(None) is False


# --- the MESSAGE, not just the predicate -----------------------------------
# Testing the classifier alone proves a bool, not what an agent reads. These
# drive _keep_current itself so the wording is pinned where it is consumed.

class _Card:
    def __init__(self, ws): self.workspace = ws


def _drive(monkeypatch, err):
    import shantytown.cli as cli
    monkeypatch.setattr(cli, "_registry",
                        lambda a: type("R", (), {"get": lambda s, n: _Card("/w/agent")})())
    monkeypatch.setattr(cli, "_refresh_clone", lambda p: err)
    return cli._keep_current(object(), "agent")


def test_MESSAGE_for_a_dead_remote_does_not_tell_the_agent_to_clean(monkeypatch):
    msg = _drive(monkeypatch, "ssh: connect to host forge.invalid port 22: Connection refused")
    assert msg is not None
    assert "REMOTE IS UNREACHABLE" in msg
    assert "nothing to reconcile locally" in msg
    # The false instruction that cost arnold the search must be GONE.
    assert "Clean or reconcile" not in msg
    # ...while the one true consequence survives.
    assert "STALE" in msg.upper()


def test_MESSAGE_for_local_drift_still_says_reconcile(monkeypatch):
    msg = _drive(monkeypatch, "fatal: Not possible to fast-forward, aborting.")
    assert msg is not None
    assert "Clean or reconcile /w/agent" in msg
    assert "REMOTE IS UNREACHABLE" not in msg


def test_a_clean_pull_still_returns_None(monkeypatch):
    assert _drive(monkeypatch, None) is None
