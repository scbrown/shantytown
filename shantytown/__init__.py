"""shantytown — a small harness for running a crew of coding agents."""
# 0.4.0+dev, NOT 0.4.0 — DELIBERATE (aegis-sb706d).
#
# THE DECISION: shantytown does not cut releases on a cadence. The editable
# install off the canonical checkout IS the delivery mechanism — pulling that
# repo is what deploys new `st` behaviour — so a tag is stale the moment it is
# cut and answers no question anyone actually asks. "Nobody looked" was the
# previous state and was not a decision; this is one.
#
# WHY THE MARKER IS NOT COSMETIC. main is 858 commits past v0.4.0 (measured
# 2026-09-02 against the GitHub API, not inferred), and a bare "0.4.0" reads as
# a RELEASED 0.4.0. That string is what an agent quotes when answering "which st
# found this bug?" and "is my st current?" — questions this fleet has answered
# wrong before. The `+dev` local-version segment makes the honest answer
# unmissable, and deployed_sha() beside it carries the identity that actually
# distinguishes two builds.
#
# The Release workflow is KEPT, not deleted: it gates a tag on the suite, and a
# deliberate milestone tag should still be possible. Its own "tag matches the
# declared version" step reads pyproject, so cutting one means setting a clean
# version in the release commit — normal flow, already enforced.
__version__ = "0.4.0+dev"


def deployed_sha() -> str:
    """The git SHA of the code actually running, or 'unknown'.

    The static ``__version__`` sat at 0.0.1 through every reinstall attempt
    while the code changed under it — "same output, two worlds" in the tool
    whose own checkers exist to catch that. A checker that cannot say WHICH
    checker it is cannot be believed; this is the which.

    Under the editable era (see selfcheck) the package dir IS the checkout, so
    the answer is its git HEAD, with ``-dirty`` when the running code is not
    any commit. For a non-editable install there is no repo to ask; a deploy
    step may leave a ``_deployed_sha`` file next to the package instead.
    """
    import subprocess
    from pathlib import Path

    pkg = Path(__file__).parent
    try:
        sha = subprocess.run(
            ["git", "-C", str(pkg), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(pkg), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return (pkg / "_deployed_sha").read_text().strip() or "unknown"
    except OSError:
        return "unknown"
