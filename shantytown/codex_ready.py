"""Bounded readiness for the per-card Remote Control daemon before TUI attach.

`remote-control start --json` is idempotent and reports the live relay status.
A spawned daemon is not necessarily connected yet. In particular, two immediate
starts can both see the transient errored state and strand the pane at a shell.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

TIMEOUT = 20.0
INTERVAL = 0.5
ATTEMPT_TIMEOUT = 5.0


def _attempt(command: list[str], timeout: float) -> str:
    # Files avoid pipe inheritance keeping communicate() alive after its child
    # timed out. Never echo provider output: it can carry remote identity/auth.
    with tempfile.TemporaryFile() as output:
        try:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
        except OSError:
            return "command unavailable"
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            return "command timed out"
        output.seek(0)
        try:
            payload = json.loads(output.read(65537))
        except (ValueError, UnicodeError):
            return "invalid status response"
    if not isinstance(payload, dict):
        return "invalid status response"
    status = payload.get("status")
    if status not in ("disabled", "connecting", "connected", "errored"):
        return "unknown status"
    if proc.returncode != 0:
        return f"{status} (command failed)"
    if payload.get("timedOut") is not False:
        return f"{status} (readiness unconfirmed)"
    return status


def wait_ready(command: list[str], *, timeout: float = TIMEOUT,
               interval: float = INTERVAL) -> bool:
    deadline = time.monotonic() + timeout
    last = "not checked"
    print("  waiting for Codex Remote Control to connect "
          f"(up to {timeout:g}s)", file=sys.stderr, flush=True)
    while (remaining := deadline - time.monotonic()) > 0:
        last = _attempt(command, min(ATTEMPT_TIMEOUT, remaining))
        if last == "connected":
            print("  Codex Remote Control connected; attaching TUI",
                  file=sys.stderr, flush=True)
            return True
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval, remaining))
    print(f"  refused: Codex Remote Control did not connect within {timeout:g}s "
          f"(last status: {last}); TUI not started. Inspect the per-card "
          "daemon/authentication with `st agent log <agent>`; after recovery "
          "use `st agent stop <agent>` then `st agent new <agent>`.",
          file=sys.stderr, flush=True)
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a daemon start command is required after --")
    return 0 if wait_ready(command) else 1


if __name__ == "__main__":
    raise SystemExit(main())
