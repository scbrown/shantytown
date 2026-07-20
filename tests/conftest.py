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
