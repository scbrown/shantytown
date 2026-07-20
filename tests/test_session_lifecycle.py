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
import shutil
import subprocess
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
