"""The pre-edit guard's three timeouts must be ORDERED, and one place must own
all three (aegis-wiv6ih).

THE DEFECT. The budget for the hook's work and the timeout that KILLS that work
were set in two repositories by two people. Emitted parent: 5s. yupana's
per-call ceiling: 10s. So not even ONE query could fit — incoherent, not
mis-sized, and there is no middle ground to tune toward.

WHY IT WAS LATENT, AND WHY THAT IS THE DANGEROUS PART. All 16 deployed
registrations carried NO timeout and were safe; the 5s existed only in the
emitter. This fleet drills "committed is not deployed, go make it live" at every
turn, so an agent noticing the disagreement and pushing a re-emission to
reconcile it would have ARMED this while believing they were closing a gap. The
committed-vs-installed ladder is reversed here and the trained reflex is the
delivery mechanism.

On arming: orphaned quipu reads jump toward ~100% (measured 26.4% of 424 on
yupana-hook, against 0.0% for yupana-daemon), and since the guard degrades to
cache-or-fail-open when it cannot project, edits go UNGUARDED FLEET-WIDE.
"""
from __future__ import annotations

import re

from shantytown import runtime


def test_the_ladder_is_ORDERED_per_call_then_total_then_parent():
    """The invariant, checkable rather than arguable. All THREE rungs: the
    original filing framed it as 30s-vs-5s, which implies a tuning gap — but
    per-call (10s at the time) already exceeded the parent, which is the rung
    that makes it incoherent."""
    assert (
        runtime.PRE_EDIT_PER_CALL_SECS
        < runtime.PRE_EDIT_TOTAL_BUDGET_SECS
        < runtime.PRE_EDIT_PARENT_TIMEOUT_SECS
    ), "per-call < total < parent, all three"


def test_the_EMITTED_hook_carries_the_same_numbers_as_the_constants():
    """Read the EMITTED settings, not the source constants. That is the only
    artifact where the parent and the child are visible together, and it is what
    the acceptance on aegis-wiv6ih asks for."""
    hook = runtime._guard_hook()["hooks"][0]
    assert hook["timeout"] == runtime.PRE_EDIT_PARENT_TIMEOUT_SECS

    cmd = hook["command"]
    per_call = int(re.search(r"YUPANA_PROJECTION_HTTP_TIMEOUT_SECS=(\d+)", cmd).group(1))
    total = int(re.search(r"YUPANA_PROJECTION_TOTAL_BUDGET_SECS=(\d+)", cmd).group(1))

    assert per_call == runtime.PRE_EDIT_PER_CALL_SECS
    assert total == runtime.PRE_EDIT_TOTAL_BUDGET_SECS
    # the invariant, re-asserted on the emitted values themselves
    assert per_call < total < hook["timeout"], (
        f"emitted ladder is incoherent: {per_call} < {total} < {hook['timeout']}")


def test_the_child_budget_is_SENT_so_yupana_need_not_guess_it():
    """yupana cannot see the harness timeout that kills it, so it cannot check
    the ordering itself. The emitter owns both and must pass the child down."""
    cmd = runtime.pre_edit_guard_command()
    assert "YUPANA_PROJECTION_TOTAL_BUDGET_SECS=" in cmd
    assert "YUPANA_PROJECTION_HTTP_TIMEOUT_SECS=" in cmd


def test_the_yupana_20_FAIL_OPEN_CONTRACT_survives_the_env_prefix():
    """CONTROL. `VAR=x cmd` is a plain command prefix, not a shell wrapper, so
    the pinned contract is unchanged: stdout is captured and echoed ONLY on exit
    0, and any yupana failure becomes silence rather than a forged decision.

    This matters more than it looks: on 2026-07-19 a guard that hard-blocked on
    a non-zero exit refused every Write/Edit by every worker on the fleet.
    """
    cmd = runtime.pre_edit_guard_command()
    assert cmd.startswith("out=$("), "stdout must still be CAPTURED, not streamed"
    assert "|| exit 0" in cmd, "any failure must still become allow"
    assert 'printf %s "$out"' in cmd, "output is echoed only after a zero exit"


def test_an_INCOHERENT_ladder_cannot_even_be_IMPORTED():
    """The assertion lives at import, not only in this file, so a bad ladder
    cannot be emitted even once. A test can be skipped; what this prevents is a
    settings file that disables the edit guard fleet-wide."""
    src = (runtime.__file__ or "")
    assert src, "runtime must have a source file to check"
    text = open(src, encoding="utf8").read()
    assert "PRE_EDIT_PER_CALL_SECS" in text and "assert (" in text, (
        "the ordering must be asserted at import in runtime.py, not only here")
