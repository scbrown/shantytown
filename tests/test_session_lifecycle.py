"""Panes session surface + st stop/log — shantytown #5 / aegis-qdal.qdal.1.

Arnold's ruling (his mail): new_session creates an EMPTY named session and RAISES
if it already exists (never clobber a live agent); kill_session is idempotent;
`st log` is capture() on the session pane. The launch of the agent-with-hooks is
a runtime send() OUTSIDE Panes — so `st new` (which needs that launch) is NOT
built here; it waits on arnold's launch-command contract. This covers the
session primitives + stop + log, both outcomes for each, as he specified.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import cli
from shantytown.tmux import NullPanes


# --- the Panes session primitives, both outcomes each -----------------------

def test_new_session_succeeds_on_a_free_name():
    p = NullPanes(live=set())
    addr = p.new_session("aegis-crew-ellie")
    assert addr == "aegis-crew-ellie"
    assert p.exists("aegis-crew-ellie") is True


def test_new_session_RAISES_over_a_live_session():
    """The clobber guard — never silently replace a running agent."""
    p = NullPanes(live={"aegis-crew-ellie"})
    with pytest.raises(RuntimeError, match="already exists"):
        p.new_session("aegis-crew-ellie")


def test_kill_session_removes_a_present_one():
    p = NullPanes(live={"aegis-crew-ellie"})
    p.kill_session("aegis-crew-ellie")
    assert p.exists("aegis-crew-ellie") is False


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

def _world(tmp_path: Path, pane="aegis-crew-ellie"):
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
    monkeypatch.setattr(cli, "Tmux", lambda: NullPanes(live=set()))   # nothing live
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.OK
    assert "was not running" in capsys.readouterr().out


def test_stop_kills_and_verifies_when_present(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    panes = NullPanes(live={"aegis-crew-ellie"})
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.OK
    assert "stopped ellie" in capsys.readouterr().out
    assert not panes.exists("aegis-crew-ellie"), "stop said done but session lives"


def test_stop_returns_2_if_the_kill_did_not_take(tmp_path, monkeypatch, capsys):
    """NEGATIVE CONTROL: a kill that leaves the session alive must NOT report
    success. A stop that exits 0 over a live agent is the defect this repo is
    against."""
    class _StubbornPanes(NullPanes):
        def kill_session(self, name):     # pretends to kill, session stays
            pass
    panes = _StubbornPanes(live={"aegis-crew-ellie"})
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_stop(_Args(root=root))
    assert rc == cli.CANNOT_TELL
    assert "still there" in capsys.readouterr().err


# --- st log = capture, both outcomes ----------------------------------------

def test_log_reads_the_session_pane(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    panes = NullPanes(screen="… agent is working on aegis-x", live={"aegis-crew-ellie"})
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_log(_Args(root=root))
    assert rc == cli.OK
    assert "agent is working" in capsys.readouterr().out


def test_log_says_not_running_when_no_session(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux", lambda: NullPanes(live=set()))
    rc = cli._cmd_log(_Args(root=root))
    assert rc == cli.OK
    assert "not running" in capsys.readouterr().out
