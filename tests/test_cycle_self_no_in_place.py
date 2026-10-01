"""`st agent cycle --self --no-in-place` must survive the trip through the request.

`--self` cannot cycle in-process, so it records a request that `st fleet tend`
serves later. The request used to store the checkpoint and nothing else, and tend
rebuilt the cycle call from its OWN arguments, which have no --no-in-place. So a
self-requested RELAUNCH always ran as an in-place clear. Measured on a live
administrator: a relaunch requested to pick up new settings was honoured as a
soft clear, the process kept its launch-time settings, and the drift alert it was
meant to clear stayed up.
"""
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from shantytown import cli, cycle


# --- the record ---------------------------------------------------------------

def test_a_request_records_no_in_place(tmp_path):
    cycle.Requests(tmp_path).request("worker", "ckpt", no_in_place=True)
    assert cycle.Requests(tmp_path).pending()["worker"]["no_in_place"] is True


def test_the_default_request_stays_in_place(tmp_path):
    cycle.Requests(tmp_path).request("worker", "ckpt")
    assert cycle.Requests(tmp_path).pending()["worker"]["no_in_place"] is False


def test_a_record_written_before_the_field_reads_as_in_place(tmp_path):
    """A legacy record keeps meaning what it meant when it was written."""
    path = tmp_path / "notify" / "cycle-requests.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"worker": {"checkpoint": "ckpt",
                                           "checkpoint_bead": ""}}))
    assert cycle.Requests(tmp_path).pending()["worker"]["no_in_place"] is False


# --- the --self command persists it -------------------------------------------

def _self_args(tmp_path, **kw):
    base = dict(root=tmp_path, agent="worker", self_=True, reason="ckpt",
                checkpoint_file="", checkpoint_bead="", dry_run=False,
                no_in_place=False, quipu_node=["some-node"],
                no_graph_context="", allow_loss=False, despite_hold="")
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def self_path(tmp_path, monkeypatch):
    card = SimpleNamespace(role="worker", name="worker")
    monkeypatch.setattr(cli, "_registry",
                        lambda _a: SimpleNamespace(get=lambda _n: card))
    monkeypatch.setattr(cli, "_cycle_stop_refusal", lambda _a, _n: "")
    monkeypatch.setattr(cli, "_graph_context",
                        lambda _a: (SimpleNamespace(nodes=["some-node"],
                                                    exemption=""), ""))
    monkeypatch.setattr(cli.graph_adoption, "record", lambda *a, **k: None)
    return tmp_path


@pytest.mark.parametrize("flag", [True, False])
def test_self_request_carries_the_flag(self_path, flag):
    assert cli._cmd_cycle(_self_args(self_path, no_in_place=flag)) == cli.OK
    assert cycle.Requests(self_path).pending()["worker"]["no_in_place"] is flag


# --- tend serves it -----------------------------------------------------------

def _tend_world(tmp_path, monkeypatch):
    from tests.test_tend import _Panes, _roster
    _roster(tmp_path, {"worker": {"role": "worker", "pane": "p-worker"}})
    monkeypatch.setattr(cli, "Tmux",
                        lambda *_a, **_k: _Panes(live={"p-worker"}))

@pytest.mark.parametrize("flag", [True, False])
def test_tend_hands_the_flag_to_the_cycle_it_serves(tmp_path, monkeypatch, flag):
    from tests.test_tend import _Args

    _tend_world(tmp_path, monkeypatch)
    cycle.Requests(tmp_path).request("worker", "ckpt", no_in_place=flag)
    seen = []

    def fake_cycle(ns):
        seen.append(ns)
        return cli.OK

    monkeypatch.setattr(cli, "_cmd_cycle", fake_cycle)
    cli._tend_once(_Args(tmp_path, backend="files"))
    served = [ns for ns in seen if ns.agent == "worker"]
    assert served, "tend did not serve the pending request"
    assert served[0].no_in_place is flag


def test_tend_ignores_its_own_no_in_place_when_the_request_did_not_ask(
        tmp_path, monkeypatch):
    """The REQUEST decides, not whatever tend's namespace happens to carry."""
    from tests.test_tend import _Args

    _tend_world(tmp_path, monkeypatch)
    cycle.Requests(tmp_path).request("worker", "ckpt")
    seen = []
    monkeypatch.setattr(cli, "_cmd_cycle", lambda ns: seen.append(ns) or cli.OK)
    cli._tend_once(_Args(tmp_path, backend="files", no_in_place=True))
    assert [ns.no_in_place for ns in seen if ns.agent == "worker"] == [False]


def test_a_served_no_in_place_request_plans_a_replacement():
    """The end the flag exists for: the plan RESPAWNs instead of clearing."""
    p = cycle.plan(clear_command="/clear", session_live=True, owned=True,
                   idle=True, input_empty=True, dangerous=False,
                   bypass_verifiable=True, prefer_soft=not True)
    assert p.mode == cycle.RESPAWN
