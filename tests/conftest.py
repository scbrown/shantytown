"""Suite-wide guard: a test may not reach a REAL store.

WHY THIS EXISTS, measured 2026-07-20. `st mail -d` was changed to default to the
beads backend (dearing, qdal.2). tests/test_full_cycle.py drives the real CLI, so
it took the new default and wrote FOUR beads into a live store —
"mail: HANDOFF the epic", assigned to maldoon, indistinguishable from real work
from a crew member he has every reason to trust. They sat OPEN in his queue.

The fix to that test (pass `--backend files`) removes the instance. This removes
the CLASS, because the next storeward default will not announce itself.

WHAT WE ASSUMED AND DID NOT CHECK. The other nine `mail -d` tests looked
contained, and the reasoning was "they pass `--root <tmp>`". That reasoning is
WRONG, and it is worth stating plainly because it is what made the write
possible: **`--root` does not scope the beads backend at all.** BeadsTracker is
scoped only by `--repo` (bd's -C), defaulting to the CWD — the string "root"
does not appear in shantytown/beads.py even once. Those nine tests are contained
because they monkeypatch `cli._tracker`, which is INCIDENTAL, not designed. Any
new test that forgets to is a write to production.

"No beads appeared" and "the backend is sandboxed" are not the same finding.
This fixture is the difference: it makes the containment structural and LOUD,
so the answer stops depending on whoever writes the next test remembering.

OPT IN deliberately when a test really means to shell out to `bd`:

    @pytest.mark.real_store
    def test_the_beads_adapter_itself(...): ...

The guard sits on the SUBPROCESS, not on the constructor. Building a
BeadsTracker is harmless — two tests do it to assert `--backend beads` wires to
the right class, and they never touch a store. Running `bd` is the harm, so that
is where the line goes. A guard placed at construction would have forced those
two honest tests to claim an exemption they do not need, and an exemption that
is handed out for a non-reason is one nobody reads later.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from shantytown import beads as beads_mod


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_store: this test may shell out to `bd` against a real store.",
    )


@pytest.fixture(autouse=True)
def _no_real_store(request, monkeypatch):
    """Make a `bd` invocation fail loudly inside the suite.

    Deliberately RAISES rather than returning a plausible-looking result: a stub
    that answered would let a test pass while asserting against a fake store it
    never meant to use — the same green-and-wrong shape the original failure
    had. The point is to be told, not to be smoothed over.
    """
    if "real_store" in request.keywords:
        return

    def _refuse(self, *args, **kw):
        raise AssertionError(
            f"a test shelled out to `bd {' '.join(map(str, args))[:60]}` — that "
            f"runs against whatever store the CWD resolves to, NOT the test's "
            f"--root (--root does not scope the beads backend; only --repo "
            f"does). This is how four phantom 'mail: HANDOFF the epic' beads "
            f"landed in a live queue. Fix: pass `--backend files` explicitly, "
            f"or monkeypatch cli._tracker. If you genuinely mean to reach bd, "
            f"mark the test @pytest.mark.real_store."
        )

    monkeypatch.setattr(beads_mod.BeadsTracker, "_bd", _refuse)


@pytest.fixture(autouse=True)
def _no_real_pointer(tmp_path_factory, monkeypatch):
    """No test may see the DEVELOPER'S deployment pointer.

    Same class as the guard above — a test reaching real state outside its
    tmp_path — and it was latent until somebody's box actually had one. The root
    resolver's last-but-one leg reads ~/.config/shantytown/root (honouring
    $XDG_CONFIG_HOME), so on a box with a pointer, every assertion about the
    cwd fallback answers with that operator's real store instead. Measured the
    moment one was written: test_default_root_falls_back_to_the_cwd went red on a
    change that had nothing to do with it.

    That is the worse direction of this bug, not the better one: the test asserts
    an absent-store path, so a pointer makes it PASS THROUGH a real deployment. It
    failed here only because the expected value is a tmp_path.

    Pointing $XDG_CONFIG_HOME at an empty tmp dir gives every test the same
    answer — "this box has no pointer" — whatever the developer's box is like. A
    test that means to exercise the pointer writes one under this same variable
    (test_socket_and_root.py does).
    """
    monkeypatch.setenv("XDG_CONFIG_HOME",
                       str(tmp_path_factory.mktemp("xdg-isolated")))


@pytest.fixture(autouse=True)
def _no_ambient_store_root(monkeypatch):
    """No test may see the operator's $SHANTY_ROOT — the OTHER leg of the same
    resolver, and the one that was still open.

    `_no_real_pointer` above closes the resolver's last-but-one leg. `resolve_root`
    has four, and the ENV leg sits second, ahead of both the walk-up and the
    pointer:

        --root  ->  $SHANTY_ROOT  ->  walk up for .shanty  ->  pointer  ->  cwd

    So isolating the pointer while leaving $SHANTY_ROOT alone guards the leg that
    is usually empty and leaves the leg that is usually SET. Every crew agent in
    this deployment runs with SHANTY_ROOT exported at the live store, which means
    every agent's local `pytest` had a real deployment one env var away.

    MEASURED, and it is why this exists rather than being a tidy-up (aegis-k7j6u).
    Two tests in test_inbox.py drove the CLI in a SUBPROCESS with no `--root`,
    copying os.environ. They passed for every developer and failed on every CI run
    from the day CI was created — ~20 consecutive red runs on main — because CI has
    no SHANTY_ROOT and no store, so the CLI refused on stderr while the helper read
    stdout, and the assertion compared against ''.

    The first diagnosis of that blamed the pointer file. It was wrong, and it was
    wrong in the way this fixture is about: the repro cleared HOME *and*
    SHANTY_ROOT in one command and credited HOME. Varying one at a time:

        ambient env             -> pass
        SHANTY_ROOT cleared     -> FAIL     <- the cause
        HOME cleared            -> pass     <- the pointer was never it

    Deleting rather than repointing: a test that means to exercise the env leg sets
    it itself (test_socket_and_root.py does, via monkeypatch, which runs after this
    fixture and therefore wins). An absent variable gives every box the same answer
    — "nothing in the environment names a store" — which is CI's answer, and the
    whole point is that a local green should mean what a CI green means.
    """
    monkeypatch.delenv("SHANTY_ROOT", raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_agent(monkeypatch):
    """No test may inherit the RUNNER'S identity. Third instance of the class
    above, and it had already bitten (aegis-5vxmz).

    `st inbox` began signing messages with `$SHANTY_AGENT` (eb26be0). The
    launcher exports that variable into every crew agent's session, so from that
    commit five tests asserted on a message body whose content depended on WHO
    RAN THE SUITE:

        assert sent == [("%1", "go read st-1")]        # what CI sees
        assert sent == [("%1", "[from tim] go read st-1")]   # what a crew agent sees

    Measured 2026-08-04 on origin/main: green in CI and on a developer laptop,
    five red for every one of ~10 crew agents on this host. That is the worst
    orientation for a failure — the people most likely to be editing this code
    are the only ones who see it, and they see it as damage they just caused. It
    cost one investigation to establish it was neither.

    Deleting rather than pinning to a fixed name: absent is the state that makes
    an attribution test's CONTROL meaningful ("an unattributable send stays
    bare"), and a test that wants a sender says so — monkeypatch.setenv in the
    test body runs after this fixture and wins.
    """
    monkeypatch.delenv("SHANTY_AGENT", raising=False)
    # The pane marker is part of the same inherited launch environment.  Leaving
    # it ambient makes an otherwise isolated test claim it is running in the
    # developer's real pane, and identity verification then reaches real tmux.
    monkeypatch.delenv("TMUX_PANE", raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_checkout(monkeypatch):
    """No test's Stop-hook shape may depend on WHICH FILES EXIST in a sibling
    checkout. Fourth instance of the class above, caught before it bit.

    The transcript archiver (aegis-xfmon3) is emitted only when
    `selfcheck.canonical_source()` resolves AND the capture script is present
    there. That is right for production — a hook with a guessed path is worse
    than no hook — but it makes the emitted Stop list a function of the
    filesystem. Measured while writing it: seven tests asserting exact stop-hook
    counts were GREEN on the branch and RED the moment the same code was run with
    the canonical source pointing somewhere the script existed. Nothing about the
    code under test differs between those two runs.

    That is the worst shape a suite can have — it would have passed review,
    passed CI, and then gone red for everyone at once on the deploy that
    installed the script, looking like the deploy broke something. The failure
    would arrive detached from its cause by however long the merge took.

    Pinning to None rather than deleting $SHANTY_CANONICAL_SOURCE: deleting the
    variable is not enough, because canonical_source() falls back to `git
    worktree list` on the running package's own checkout and would resolve
    anyway. Absent is also the state that makes the archiver tests' CONTROL
    meaningful ("no checkout -> no hook, and the role's own machinery is
    untouched"); a test that wants the archiver emitted says so by patching
    runtime.canonical_source itself, which runs after this fixture and wins.
    """
    from shantytown import runtime
    monkeypatch.delenv("SHANTY_CANONICAL_SOURCE", raising=False)
    monkeypatch.setattr(runtime, "canonical_source", lambda *a, **k: None)


@pytest.fixture
def beadsless_tmp(tmp_path):
    """A directory with NO `.beads` in ANY ancestor.

    Two tests assert that store resolution walking upward finds nothing and
    returns None rather than guessing. Both resolvers deliberately walk past the
    git boundary all the way up (feed_check.bd_cwd says why: the rig root lives
    ABOVE the admin's clone). So "nothing above it" is a property of the
    FILESYSTEM, and `tmp_path` does not provide it.

    MEASURED 2026-09-02: on this host pytest's basetemp sits under $HOME, and
    $HOME has a `.beads`. Both tests therefore failed for every crew agent while
    passing in CI — and they failed as a confident assertion about the resolver
    (it printed the resolved HOME path where None was expected) rather than as
    "your TMPDIR has a store above it". `TMPDIR=/tmp` made them pass, which is
    the whole diagnosis.

    This is the same class conftest already guards three times over: a test whose
    verdict depends on the runner's ambient environment. It is the nastiest
    variant, because the ambient thing is the TEMP DIRECTORY that pytest itself
    chose, so nothing in the test looks environmental.

    Rather than pin TMPDIR globally — which would change every other test's
    environment to fix two — this hands back a base that is verified clean, and
    SKIPS with the reason if the host can offer none. A skip that names the cause
    beats a red that misattributes it to the code under test.
    """
    import tempfile

    def _clean(p):
        p = Path(p).resolve()
        return not any((anc / ".beads").exists() for anc in (p, *p.parents))

    if _clean(tmp_path):
        return tmp_path
    for base in ("/tmp", "/var/tmp"):
        if not Path(base).is_dir():
            continue
        cand = Path(tempfile.mkdtemp(prefix="shantytown-beadsless-", dir=base))
        if _clean(cand):
            return cand
    pytest.skip(
        "no writable base without a .beads ancestor on this host — this test "
        "asserts upward store resolution finds NOTHING, which the filesystem "
        "must actually provide (aegis-rig9vu)")
