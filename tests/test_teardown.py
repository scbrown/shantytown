"""kill_session kills the PROCESS TREE, not just the tmux session (aegis-84z1).

A real agent survived a plain kill-session once (claude ignored SIGHUP and
orphaned). The stand-in child MUST model that: a plain `sleep` dies fine on
kill-session (it does not ignore SIGHUP), so it would NOT detect the survival bug
— a green test that proves nothing (validate-the-instrument). So the child here
IGNORES SIGHUP (`trap '' HUP`), exactly like claude: it SURVIVES a plain
kill-session and is only reaped by the hardened tree-kill. Real tmux, no claude,
CI-safe.
"""
from __future__ import annotations
import os
import shutil
import signal
import subprocess
import time

import pytest

from shantytown.tmux import Tmux

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

SOCK = "st-teardown-test"


def _cleanup():
    subprocess.run(["tmux", "-L", SOCK, "kill-server"],
                   capture_output=True)


def _child_pid_in(session: str) -> int | None:
    """The foreground child of the pane shell (our `sleep`), via its pgid."""
    r = subprocess.run(["tmux", "-L", SOCK, "display-message", "-t", session,
                        "-p", "#{pane_pid}"], capture_output=True, text=True)
    return int(r.stdout.strip()) if r.stdout.strip().isdigit() else None


def _alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_kill_session_reaps_the_pane_child_process():
    _cleanup()
    try:
        t = Tmux(socket=SOCK)
        t.new_session("victim")
        # a child that IGNORES SIGHUP, modelling claude: it survives the pty close
        # that kill-session triggers, and is only reaped by the tree-kill.
        t.send("victim", "trap '' HUP; sleep 300")
        time.sleep(0.5)
        pane_pid = _child_pid_in("victim")
        assert pane_pid and _alive(pane_pid), "setup: pane process should be alive"

        t.kill_session("victim")

        assert not t.exists("victim"), "session should be gone"
        # the hardened kill_session must have reaped the process group too — a
        # plain kill-session would leave this SIGHUP-ignoring child orphaned (the
        # exact survival bug from the 84z1 real-claude incident).
        deadline = time.time() + 3
        while _alive(pane_pid) and time.time() < deadline:
            time.sleep(0.1)
        assert not _alive(pane_pid), \
            "kill_session left the pane's process tree alive (the survival bug)"
    finally:
        _cleanup()


def test_kill_session_is_idempotent_on_absent():
    _cleanup()
    try:
        Tmux(socket=SOCK).kill_session("never-existed")   # must not raise
    finally:
        _cleanup()
