"""`st agent cycle --self` must actually cycle (aegis-oj2z7m).

Measured on an administrator: four attempts to get a request accepted, each
refusal naming ONE requirement; then the request waited for a fleet-wide tend
that the host never ran, and a human cleared the session by hand.
"""
from __future__ import annotations

import argparse
import itertools
from types import SimpleNamespace

import pytest

from shantytown import br as br_mod, cli, cycle, triage


def _args(tmp_path, **kw):
    base = dict(root=tmp_path, agent="hammond", self_=True, reason="",
                checkpoint_file="", checkpoint_bead="", dry_run=False,
                no_in_place=False, quipu_node=["n"], no_graph_context="",
                allow_loss=False, despite_hold="", serve_request=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def admin(tmp_path, monkeypatch):
    card = SimpleNamespace(role="administrator", name="hammond", pane="p-h")
    monkeypatch.setattr(cli, "_registry", lambda _a: SimpleNamespace(get=lambda _n: card))
    monkeypatch.setattr(cli, "_cycle_stop_refusal", lambda _a, _n: "")
    monkeypatch.setattr(cli, "_graph_context",
                        lambda _a: (SimpleNamespace(nodes=["n"], exemption=""), ""))
    monkeypatch.setattr(cli.graph_adoption, "record", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_tracker", lambda _a: SimpleNamespace(get=lambda _i: {}))
    plate = {"bead": ""}
    monkeypatch.setattr(cli, "_cycle_anchor_bead", lambda _a, _n: plate["bead"])
    return tmp_path, plate


def test_one_refusal_names_every_missing_requirement(admin, capsys):
    root, plate = admin
    assert cli._cmd_cycle(_args(root)) == cli.REFUSED
    err = capsys.readouterr().err
    assert "your checkpoint" in err and "--checkpoint-bead" in err
    assert err.count("refused:") == 1
    assert cycle.Requests(root).pending() == {}


def test_an_administrators_plate_bead_is_its_durable_checkpoint(admin):
    root, plate = admin
    plate["bead"] = "aegis-plate"
    assert cli._cmd_cycle(_args(root, reason="mid-task on X")) == cli.OK
    assert cycle.Requests(root).pending()["hammond"]["checkpoint_bead"] == "aegis-plate"


def test_the_request_starts_its_own_waiter(admin, monkeypatch, capsys):
    root, plate = admin
    plate["bead"] = "aegis-plate"
    spawned = []
    monkeypatch.setattr(cli, "_spawn_self_cycle_server",
                        lambda a, who: spawned.append(who) or "/log")
    assert cli._cmd_cycle(_args(root, reason="r")) == cli.OK
    assert spawned == ["hammond"]
    assert "next idle turn" in capsys.readouterr().out


# --- the waiter ----------------------------------------------------------------

def _waiter_world(tmp_path, monkeypatch, screens):
    card = SimpleNamespace(name="hammond", pane="p-h")
    monkeypatch.setattr(cli, "_registry", lambda _a: SimpleNamespace(get=lambda _n: card))
    shots = iter(screens)
    monkeypatch.setattr(cli, "_panes", lambda _a: SimpleNamespace(
        exists=lambda p: True, capture=lambda p, **k: next(shots)))
    monkeypatch.setattr(cli, "_runtime", lambda _a, _p: SimpleNamespace(
        shows_ready_ui=lambda s: "shift+tab to cycle" in s,
        awaiting_answer=lambda s: False))
    served = []
    monkeypatch.setattr(cli, "_serve_cycle_request",
                        lambda a, who, req: served.append((who, req)) or cli.OK)
    return served


IDLE = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
BUSY = "✻ Envisioning… (12s · 4.1k tokens · esc to interrupt)"


def test_the_waiter_waits_out_the_turn_then_serves(tmp_path, monkeypatch):
    cycle.Requests(tmp_path).request("hammond", "ckpt", "aegis-plate")
    served = _waiter_world(tmp_path, monkeypatch, [BUSY, BUSY, IDLE])
    sleeps = []
    rc = cli._serve_own_request(argparse.Namespace(root=tmp_path), "hammond",
                                sleep=sleeps.append, clock=itertools.count().__next__)
    assert rc == cli.OK and len(served) == 1 and len(sleeps) == 2
    assert served[0][1]["checkpoint_bead"] == "aegis-plate"


def test_the_waiter_stops_when_tend_served_it_first(tmp_path, monkeypatch):
    served = _waiter_world(tmp_path, monkeypatch, [IDLE])
    rc = cli._serve_own_request(argparse.Namespace(root=tmp_path), "hammond",
                                sleep=lambda s: None, clock=itertools.count().__next__)
    assert rc == cli.OK and served == []


def test_the_waiter_gives_up_and_leaves_the_request(tmp_path, monkeypatch):
    cycle.Requests(tmp_path).request("hammond", "ckpt")
    served = _waiter_world(tmp_path, monkeypatch, itertools.repeat(BUSY))
    t = itertools.count(0, 60)
    rc = cli._serve_own_request(argparse.Namespace(root=tmp_path), "hammond",
                                sleep=lambda s: None, clock=t.__next__)
    assert rc == cli.REFUSED and served == []
    assert "hammond" in cycle.Requests(tmp_path).pending()


def test_the_spawn_is_detached_and_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_SELF_CYCLE_SERVE", "1")
    calls = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)))
    log = cli._spawn_self_cycle_server(argparse.Namespace(root=tmp_path), "hammond")
    argv, kw = calls[0]
    assert argv[-3:] == ["cycle", "hammond", "--serve-request"]
    assert kw["start_new_session"] is True and log.endswith("self-cycle-hammond.log")


# --- the checkpoint crosses a relay ---------------------------------------------

def test_a_missing_temp_path_on_the_far_side_retries_on_stdin():
    calls = []

    class _T:
        repo = None

        def _bd_for(self, bead, *args):
            calls.append(("for", args))
            return SimpleNamespace(returncode=1, stderr="I/O error: No such file or directory")

        def _bd_in(self, repo, *args, stdin=None):
            calls.append(("in", args, stdin))
            return SimpleNamespace(returncode=0, stderr="")

    br_mod.append_comment(_T(), "aegis-x", "body $(not run)")
    assert calls[-1] == ("in", ("comments", "add", "aegis-x", "--file", "/dev/stdin"),
                         "body $(not run)")


def test_any_other_failure_is_not_retried():
    class _T:
        repo = None

        def _bd_for(self, bead, *args):
            return SimpleNamespace(returncode=1, stderr="issue not found")

        def _bd_in(self, *a, **k):
            raise AssertionError("retried a failure that was not the relay case")

    with pytest.raises(RuntimeError):
        br_mod.append_comment(_T(), "aegis-x", "body")
