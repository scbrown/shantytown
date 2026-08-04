"""Panes session surface + st stop/log — shantytown #5.

Arnold's ruling (his mail): new_session creates an EMPTY named session and RAISES
if it already exists (never clobber a live agent); kill_session is idempotent;
`st log` is capture() on the session pane. The launch of the agent-with-hooks is
a runtime send() OUTSIDE Panes — so `st new` (which needs that launch) is NOT
built here; it waits on arnold's launch-command contract. This covers the
session primitives + stop + log, both outcomes for each, as he specified.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from shantytown import cli
from shantytown.tmux import NullPanes, Tmux


# --- the Panes session primitives, both outcomes each -----------------------

def test_new_session_succeeds_on_a_free_name():
    p = NullPanes(live=set())
    addr = p.new_session("crew-ellie")
    assert addr == "crew-ellie"
    assert p.exists("crew-ellie") is True


def test_new_session_RAISES_over_a_live_session():
    """The clobber guard — never silently replace a running agent."""
    p = NullPanes(live={"crew-ellie"})
    with pytest.raises(RuntimeError, match="already exists"):
        p.new_session("crew-ellie")


def test_kill_session_removes_a_present_one():
    p = NullPanes(live={"crew-ellie"})
    p.kill_session("crew-ellie")
    assert p.exists("crew-ellie") is False


def test_kill_session_is_a_noop_on_absent():
    """Idempotent: 'gone' is the desired end state either way — not an error."""
    p = NullPanes(live=set())
    p.kill_session("never-existed")          # must not raise
    assert p.exists("never-existed") is False


def test_new_then_kill_round_trip():
    p = NullPanes(live=set())
    p.new_session("x")
    assert p.exists("x")
    p.kill_session("x")
    assert not p.exists("x")
    # and after a kill, the name is free to create again
    p.new_session("x")
    assert p.exists("x")


# --- st stop, both outcomes -------------------------------------------------

def _world(tmp_path: Path, pane="crew-ellie"):
    crew = tmp_path / "crew"; crew.mkdir()
    card = {"role": "worker"}
    if pane is not None:
        card["pane"] = pane
    (crew / "ellie.json").write_text(json.dumps(card))
    return tmp_path


class _Args:
    def __init__(self, **kw):
        self.root = kw.pop("root")
        self.agent = kw.pop("agent", "ellie")
        self.dry_run = kw.pop("dry_run", False)
        self.backend = "files"; self.repo = None
        for k, v in kw.items():
            setattr(self, k, v)


def test_stop_reports_not_running_when_absent(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(live=set()))   # nothing live
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.OK
    assert "was not running" in capsys.readouterr().out


def test_stop_kills_and_verifies_when_present(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    # owned: a session st launched — the only kind st stop acts on.
    panes = NullPanes(live={"crew-ellie"}, owned={"crew-ellie"})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.OK
    assert "stopped ellie" in capsys.readouterr().out
    assert not panes.exists("crew-ellie"), "stop said done but session lives"


def test_stop_returns_2_if_the_kill_did_not_take(tmp_path, monkeypatch, capsys):
    """NEGATIVE CONTROL: a kill that leaves the session alive must NOT report
    success. A stop that exits 0 over a live agent is the defect this repo is
    against."""
    class _StubbornPanes(NullPanes):
        def kill_session(self, name):     # pretends to kill, session stays
            pass
    panes = _StubbornPanes(live={"crew-ellie"}, owned={"crew-ellie"})
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.CANNOT_TELL
    assert "still there" in capsys.readouterr().err


# --- st log = capture, both outcomes ----------------------------------------

def test_log_reads_the_session_pane(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    panes = NullPanes(screen="… agent is working on st-x", live={"crew-ellie"})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    rc = cli._cmd_log(_Args(root=root))
    assert rc == cli.OK
    assert "agent is working" in capsys.readouterr().out


def test_log_says_not_running_when_no_session(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(live=set()))
    rc = cli._cmd_log(_Args(root=root))
    assert rc == cli.OK
    assert "not running" in capsys.readouterr().out


# --- the ownership guard (dearing's safety requirement) ----------
# st stop must NEVER reap a session it did not launch. The registry pane names
# COLLIDE with a session somebody else already started under the same name,
# so on a shared socket a name match must not be
# permission to kill. Proven at three levels: the marker mechanism (owns), the
# CLI policy (st stop refuses), and real tmux (a foreign session survives).

def test_new_session_marks_ownership_kill_clears_it():
    p = NullPanes(live=set())
    p.new_session("crew-ellie")
    assert p.owns("crew-ellie")           # st launched it -> owned
    p.kill_session("crew-ellie")
    assert not p.owns("crew-ellie")


def test_a_live_session_st_did_not_launch_is_not_owned():
    p = NullPanes(live={"crew-ellie"})    # live, but st did not create it
    assert p.exists("crew-ellie")
    assert not p.owns("crew-ellie")


def test_stop_REFUSES_a_live_session_st_did_not_launch(tmp_path, monkeypatch, capsys):
    """THE SAFETY POSITIVE CONTROL. A live pane st never launched (a real crew
    member behind the colliding name) must be REFUSED, not reaped."""
    root = _world(tmp_path)
    panes = NullPanes(live={"crew-ellie"})   # live, NOT owned
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.REFUSED
    assert "not launched by st" in capsys.readouterr().err
    assert panes.exists("crew-ellie")        # still alive — not reaped


def test_stop_dry_run_also_refuses_an_unowned_session(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(live={"crew-ellie"}))
    rc = cli._cmd_stop(_Args(root=root, dry_run=True))
    assert rc == cli.REFUSED                        # the guard runs before dry-run


# --- real tmux: the marker actually distinguishes ours from foreign -----------

pytestmark_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


def _reap_socket(name: str) -> None:
    """Remove the socket file after the server is gone. See the fixture."""
    import os
    pathlib_Path = __import__("pathlib").Path
    try:
        (pathlib_Path(f"/tmp/tmux-{os.getuid()}") / name).unlink()
    except OSError:
        pass


def _server_pid(sock: str) -> int | None:
    """The tmux server's OS pid, or None if there is no server to ask."""
    r = subprocess.run(
        ["tmux", "-L", sock, "display-message", "-p", "#{pid}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _await_server_exit(pid: int | None, timeout: float = 5.0) -> None:
    """Block until the tmux server process is really gone (aegis-nwjby).

    `tmux kill-server` returns when the request is DELIVERED, not when the server
    has exited. On a loaded host the next `list-sessions` can still be answered by
    the dying server, so `sessions()` returns the session name and an assertion
    about the dead-server classification fails for a reason that has nothing to do
    with what it pins. Measured 2026-08-02 by dearing: FAILED inside a 124s full
    run, PASSED in isolation and on all 4 CI legs.

    THE WAIT DELIBERATELY WATCHES A DIFFERENT CHANNEL THAN THE ASSERTION. Polling
    `sessions()` until it returns `[]` and then asserting `sessions() == []` is a
    tautology: it could only ever fail by timeout, and it would keep passing if the
    classification it exists to pin regressed. The OS process table is not tmux's
    own reporting, so establishing the precondition here leaves the assertion still
    having to earn its pass.

    `os.kill(pid, 0)` rather than `/proc`: same POSIX answer, no Linux-only path.
    The server is a daemon, not our child, so init reaps it and there is no zombie
    to mistake for a live process.
    """
    if pid is None:                       # already gone before we could look
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"tmux server pid {pid} was still alive {timeout}s after kill-server"
            )
        time.sleep(0.01)


@pytest.fixture()
def sock():
    name = "st-test-" + uuid.uuid4().hex[:8]
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True, text=True)
    # kill-server ends the SERVER; the socket FILE stays. Hundreds of these had
    # accumulated in /tmp — harmless individually, but they make identifying the
    # fleet's real socket harder, and identifying the right socket is exactly the
    # reasoning a wrong-socket fault depends on. A suite that leaves litter will
    # eventually leave a collision.
    _reap_socket(name)


@pytestmark_tmux
def test_real_new_session_is_owned_and_reapable(sock):
    t = Tmux(socket=sock)
    t.new_session("st-owned")
    assert t.exists("st-owned")
    assert t.owns("st-owned")                       # SHANTY_OWNED marker on the real session
    t.kill_session("st-owned")
    assert not t.exists("st-owned")


@pytestmark_tmux
def test_real_foreign_session_is_refused_by_st_stop_and_survives(sock, tmp_path, monkeypatch):
    """The proof dearing required, on real tmux: a session st did NOT launch (no
    marker) — the stand-in for the live crew behind the colliding name — is
    refused by `st stop` and is still alive after the refusal."""
    foreign = "crew-ellie"
    subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-s", foreign, "sleep 300"],
                   check=True)
    t = Tmux(socket=sock)
    assert t.exists(foreign) and not t.owns(foreign)

    root = _world(tmp_path)
    monkeypatch.setenv("SHANTY_TMUX_SOCKET", sock)   # cli builds Tmux() from the env
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.REFUSED
    assert t.exists(foreign)                         # REFUSED, and still alive


@pytestmark_tmux
def test_real_kill_session_stays_idempotent_and_tree_killing(sock):
    """The guard is at the st stop POLICY layer; the kill_session adapter contract
    (idempotent, orphan-proof) is unchanged — a second reap does not raise."""
    t = Tmux(socket=sock)
    t.new_session("st-idem")
    t.kill_session("st-idem")
    t.kill_session("st-idem")                        # idempotent: no raise
    assert not t.exists("st-idem")


# --- the second factor: SHANTY_OWNED alone is not management (aegis-wn7g) ----
# Proven live by the pilot's negative control: tend's pre-gate respawns CREATED
# another orchestrator's crew sessions, so those panes carry the marker while
# the other fleet operates them — owns() said yes and dry-run reached "would
# kill" against a live foreign agent. Management = the launch STAMP.

def test_stop_refuses_an_owned_session_whose_agent_has_no_stamp(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    (root / "launched").mkdir()
    (root / "launched" / "weaver.json").write_text("{}")   # store non-empty
    panes = NullPanes(live=set())
    panes.new_session("crew-ellie")                        # owned by marker...
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    rc = cli._cmd_stop(_Args(root=root))                   # ...but ellie unstamped
    assert rc == cli.REFUSED
    assert "NO launch stamp" in capsys.readouterr().err
    assert panes.exists("crew-ellie"), "created-but-not-managed must survive"


def test_stop_reaps_an_owned_session_when_the_store_is_empty(tmp_path, monkeypatch):
    """CANNOT-TELL: no stamps at all -> the marker alone decides, as before —
    a fresh deployment must still be able to reap what it launched."""
    root = _world(tmp_path)
    panes = NullPanes(live=set())
    panes.new_session("crew-ellie")
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.OK
    assert not panes.exists("crew-ellie")


def test_stop_reaps_an_owned_and_stamped_session(tmp_path, monkeypatch):
    root = _world(tmp_path)
    (root / "launched").mkdir()
    (root / "launched" / "ellie.json").write_text(
        '{"settings": "/s.json", "sha256": "abc"}')
    panes = NullPanes(live=set())
    panes.new_session("crew-ellie")
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.OK
    assert not panes.exists("crew-ellie")


# --- real tmux: enumeration, and the two ways it can return nothing ----------

@pytestmark_tmux
def test_real_sessions_enumerates_names_st_never_launched(sock):
    """The aegis-np4x1 capability, on real tmux. Everything else in this adapter
    needs the name first; this is the only call that can find a session nobody
    told us about — which is what six agents under a retired naming scheme were."""
    t = Tmux(socket=sock)
    t.new_session("st-ours")
    subprocess.run(["tmux", "-L", sock, "new-session", "-d",
                    "-s", "aegis-crew-goldblum", "sleep 300"], check=True)
    assert sorted(t.sessions()) == ["aegis-crew-goldblum", "st-ours"]


@pytestmark_tmux
def test_a_dead_server_is_a_REAL_zero_and_an_unreachable_one_is_NOT(sock):
    """The failures tmux distinguishes and a returncode does not. All exit 1.

      socket exists, server gone  -> "no server running on ..."   -> [] , a real zero
      socket absent               -> "error connecting to ..."    -> None, we never asked
      server mid-shutdown         -> "server exited unexpectedly" -> None, we never asked

    Collapsing them is how a detector comes to report all-clear because its probe
    broke — the exact shape of the bug this whole enumeration exists to catch. So
    the strings are pinned here against the real binary rather than assumed.

    THE THIRD LINE IS WHY THIS TEST WAS FLAKY (aegis-nwjby), and it was not in this
    docstring because nobody had seen it. Measured here 2026-08-04 over 800
    unguarded iterations: ~4-8% of them, `list-sessions` issued between kill-server
    returning and the server actually exiting gets "server exited unexpectedly" —
    a THIRD string, which is not "no server running", so sessions() correctly says
    None and the `== []` below fails. The bead hypothesised the opposite (that the
    assert saw the session still present); it never does. The window is real but it
    is a window into `None`, not into a stale enumeration.
    """
    t = Tmux(socket=sock)
    t.new_session("st-briefly")
    assert t.sessions() == ["st-briefly"]

    pid = _server_pid(sock)
    subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)
    _await_server_exit(pid)               # kill-server is async — see the helper

    # The socket FILE outliving the server is not incidental, it is the whole
    # precondition: it is what makes [] the right answer here and None the right
    # answer below. Measured, and the sock fixture says the same.
    assert (Path(f"/tmp/tmux-{os.getuid()}") / sock).exists(), \
        "socket vanished with the server — this no longer tests the case it claims to"
    assert t.sessions() == [], "a server that exited is a genuine zero sessions"

    assert Tmux(socket="st-test-absent-" + uuid.uuid4().hex[:8]).sessions() is None, \
        "a socket we could not reach reported an all-clear"


@pytest.mark.parametrize("stderr, expected", [
    ("no server running on /tmp/tmux-1000/st-x", []),
    ("error connecting to /tmp/tmux-1000/st-x (No such file or directory)", None),
    ("server exited unexpectedly", None),
])
def test_sessions_classifies_each_stderr_the_real_binary_emits(monkeypatch, stderr, expected):
    """Pin the string -> classification map, including the one nobody had seen.

    The race above is a window; this is the contract behind it, testable without
    one. "server exited unexpectedly" was observed from the real binary during
    kill-server shutdown (aegis-nwjby) and is pinned to None DELIBERATELY, not
    incidentally:

    the server died WHILE ANSWERING, so we hold a truncated answer, not a zero.
    Mapping it to [] would be the precise thing the docstring above forbids —
    claiming "nothing is running" on the strength of a probe that broke. A caller
    that gets None re-probes; a caller that gets [] acts on it. If this is ever
    changed to [], change it because someone argued the case, not because a
    shutdown race made it look tidier.
    """
    import shantytown.tmux as tmux_mod

    class _Result:
        returncode, stdout = 1, ""
        def __init__(self, err): self.stderr = err

    monkeypatch.setattr(tmux_mod.subprocess, "run", lambda *a, **k: _Result(stderr))
    got = Tmux(socket="st-x").sessions()
    if expected is None:
        assert got is None, f"{stderr!r} must read as unreachable, not as a zero"
    else:
        assert got == expected, f"{stderr!r} must read as a genuine zero"
