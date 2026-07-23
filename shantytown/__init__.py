"""shantytown — a small harness for running a crew of coding agents."""
__version__ = "0.0.1"


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
