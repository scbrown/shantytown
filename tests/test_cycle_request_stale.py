"""A pending cycle request that nothing consumes must not read as a cycle in
flight forever (aegis-az0a40.1).

Measured 2026-09-30: on a host with no `st fleet tend` consumer, hammond read
`cycling` from 00:33Z to past 02:55Z while live and working. `st fleet watch`
treats a cycle in flight as cannot-tell, so the peer administrator could not be
watched at all, and the escalation poller held that agent's tier the whole time.

sattler's ruling: NO new state (three consumers enumerate st crew states and
reject or reroute on an unknown one). A stale request leaves `cycling` and the
agent is judged by its pane; what it is rides as additive JSON fields.
"""
from __future__ import annotations

import json
import os
import time

from shantytown import cli, cycle as cycle_mod, fleet, peer_watch
from test_crew_work import _Panes, IDLE_SCREEN
from test_fleet_view import setup


def _ask(root, age_s: float | None, who="local"):
    req = cycle_mod.Requests(root)
    req.request(who, "checkpoint text")
    data = json.loads(req.path.read_text())
    if age_s is None:
        data[who].pop("requested_at")
    else:
        data[who]["requested_at"] = time.time() - age_s
    req.path.write_text(json.dumps(data))
    return req


def _crew_json(args, monkeypatch, capsys):
    args.local = args.json = True
    monkeypatch.setattr(fleet, "collect", lambda _: [])
    assert cli._cmd_crew(args) == 0
    return json.loads(capsys.readouterr().out)["agents"][0]


def test_a_request_is_stamped_and_ages(tmp_path):
    req = cycle_mod.Requests(tmp_path)
    req.request("a", "cp")
    rec = req.pending()["a"]
    assert isinstance(rec["requested_at"], float)
    assert req.age(rec, now=rec["requested_at"] + 90) == 90


def test_a_legacy_record_ages_from_the_ledger_mtime(tmp_path):
    req = _ask(tmp_path, None, who="a")
    old = time.time() - 5000
    os.utime(req.path, (old, old))
    assert 4990 < req.age(req.pending()["a"]) < 5100


def test_the_bound_is_configurable_and_a_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv(cycle_mod.STUCK_AFTER_ENV, "600")
    assert cycle_mod.stuck_after() == 600
    for bad in ("", "-5", "soon"):
        monkeypatch.setenv(cycle_mod.STUCK_AFTER_ENV, bad)
        assert cycle_mod.stuck_after() == cycle_mod.STUCK_AFTER_DEFAULT


def test_a_FRESH_request_still_reads_cycling(tmp_path, monkeypatch, capsys):
    args = setup(tmp_path, monkeypatch)
    _ask(args.root, 60)
    agent = _crew_json(args, monkeypatch, capsys)
    assert agent["state"] == "cycling" and agent["cycle_request_stale"] is False


def test_a_STALE_request_reads_as_its_pane_with_the_age_alongside(tmp_path, monkeypatch, capsys):
    args = setup(tmp_path, monkeypatch)
    _ask(args.root, 2 * 3600)
    agent = _crew_json(args, monkeypatch, capsys)
    assert agent["state"] == "up", agent          # judged by its live pane, NO new state
    assert agent["cycle_request_stale"] is True
    assert 7100 < agent["cycle_request_age"] < 7300


def test_a_stale_request_on_a_DEAD_pane_reads_down(tmp_path, monkeypatch, capsys):
    args = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: _Panes({}))
    _ask(args.root, 2 * 3600)
    assert _crew_json(args, monkeypatch, capsys)["state"] == "down"


def test_the_bound_decides(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cycle_mod.STUCK_AFTER_ENV, str(3 * 3600))
    args = setup(tmp_path, monkeypatch)
    _ask(args.root, 2 * 3600)
    assert _crew_json(args, monkeypatch, capsys)["state"] == "cycling"


def test_the_table_names_the_stale_request(tmp_path, monkeypatch, capsys):
    args = setup(tmp_path, monkeypatch)
    _ask(args.root, 2 * 3600)
    monkeypatch.setattr(fleet, "collect", lambda _: [])
    cli._cmd_crew(args)
    out = capsys.readouterr().out
    assert "NOT consumed within 30m" in out and "local (120m)" in out
    assert "planned context cycle" not in out


# --- the peer watcher: judged, never blind -------------------------------------

def _peer_probe(row, run_ok=True):
    class R:
        returncode = 0
        stderr = ""
    return peer_watch.probe(type("P", (), {"name": "mac", "ssh": "u@h", "root": "/r"})(),
                            None, run=lambda *a, **k: R(),
                            read_peer=lambda p: {"agents": [row], "error": None})


def test_watch_judges_a_stale_request_by_the_pane_and_says_so():
    base = dict(name="hammond", role="administrator", pane="st-hammond", work="idle",
                foreground="claude", cycle_request_stale=True, cycle_request_age=8520)
    up = _peer_probe(dict(base, state="up", live=True))
    assert up.verdict == peer_watch.OK
    assert ("cycle", "stale request, not in flight (142m old)") in up.probes
    down = _peer_probe(dict(base, state="down", live=False))
    assert down.verdict == peer_watch.DOWN and down.repairable
