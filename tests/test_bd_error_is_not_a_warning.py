"""bd's WARNINGS share stderr with its ERRORS — a warning is not a cause.

aegis-2bjel, found by ellie. A durable send was refused with:

    could not tell: durable persist FAILED for sattler (RuntimeError: bd create
    failed: warning: beads.role not configured (GH#2950).  Fix: git config
    beads.role maintai

which reads as "your bd write was rejected because beads.role is unset". It was
not. With `beads.role` UNSET, `bd create` prints that warning to stderr, creates
the issue, and exits 0 — measured, controlled. The warning is not a gate and
never was; it merely occupied the first 120 characters of stderr, which is what
the adapter quoted.

WHAT IT NEARLY COST, which is why this is a test and not a comment: ellie
surveyed the fleet on the strength of that string and found 17 of 23 crew clones
with `beads.role` unset — one step from remediating all 17 to fix a problem that
does not exist. The survey number was real; its implication was manufactured
entirely by a truncation.

Both directions in every case below: the real error must survive, and the
innocent warning must not be named as the reason.
"""
from __future__ import annotations

import pytest

from shantytown.beads import BeadsValidationError, _bd_error_text, _bd_failure


class _R:
    returncode = 1
    stdout = ""
    stderr = ""

    def __init__(self, stdout="", stderr="", rc=1):
        self.stdout, self.stderr, self.returncode = stdout, stderr, rc


_WARNING = ("warning: beads.role not configured (GH#2950).  "
            "Fix: git config beads.role maintainer")
_REAL = ("Error: validation failed for issue : title must be 500 characters "
         "or less (got 513)")


def test_a_leading_warning_does_not_become_the_reported_cause():
    detail = _bd_error_text(_R(stderr=f"{_WARNING}\n{_REAL}"))
    assert "validation failed" in detail, f"the real error was lost: {detail!r}"
    assert "beads.role" not in detail, (
        f"an unrelated advisory was named as the cause: {detail!r}")


def test_bds_structured_error_is_preferred_over_any_stderr_line():
    detail = _bd_error_text(_R(
        stdout='{"error": "validation failed for issue : title must be 500 '
               'characters or less (got 513)"}',
        stderr=_WARNING))
    assert "validation failed" in detail
    assert "beads.role" not in detail


def test_a_failure_with_ONLY_warnings_says_so_rather_than_quoting_one():
    """The honest answer when bd fails and says nothing substantive. Quoting the
    warning here is what sends someone to fix `beads.role`."""
    detail = _bd_error_text(_R(stderr=_WARNING))
    assert "only advisories" in detail, detail
    assert "exited 1" in detail


def test_a_validation_failure_is_a_REFUSAL_not_an_outage():
    """Permanent vs transient decides whether a caller retries. Retrying an
    over-long title reproduces the failure exactly."""
    err = _bd_failure("bd create", _R(stderr=f"{_WARNING}\n{_REAL}"))
    assert isinstance(err, BeadsValidationError)

    # And the counterpart: a genuine outage must NOT be classed as a refusal, or
    # a transient store problem would stop being retried.
    outage = _bd_failure("bd create", _R(stderr="Error: dial tcp 127.0.0.1:3306: "
                                                "connect: connection refused"))
    assert not isinstance(outage, BeadsValidationError)
    assert isinstance(outage, RuntimeError)


def test_no_output_at_all_is_reported_as_such():
    detail = _bd_error_text(_R(rc=2))
    assert "no output" in detail and "2" in detail
