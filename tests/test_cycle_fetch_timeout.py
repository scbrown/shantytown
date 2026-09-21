"""Real Git transport preflights, with only pane lifecycle calls replaced."""
from argparse import Namespace
import json
import os
import time

import pytest

from shantytown import cli, config
from shantytown.cycle import DurableGate, Requests
from shantytown.files import FilesRegistry
from shantytown.workspace import tree_staleness
from test_staleness import _repo, _clone, _run, _commit


class _NoPane:
    """No pane for this fixture's session, so cycle can only take the relaunch path.

    These tests are about the Git transport preflight, not about which mechanism
    a cycle picks. Reporting the session as absent is what keeps them that way:
    it is the one answer that leaves no in-place option to weigh, so the assertion
    below still reads stop/launch/dispatch for the reason it always did.
    """

    def exists(self, session: str) -> bool:
        return False


def world(tmp_path, monkeypatch):
    upstream = _repo(tmp_path / "upstream")
    clone = _clone(upstream, tmp_path / "clone", remote="tracked")
    root = tmp_path / "deployment"
    (root / "crew").mkdir(parents=True)
    card = root / "crew" / "worker.json"
    card.write_text(json.dumps({"role": "worker", "workspace": str(clone),
                                "pane": "fixture-worker"}))
    (root / "shantytown.toml").write_text(
        "[keep_current]\nfetch_timeout_seconds = 0.3\n")
    requests = Requests(root)
    requests.request("worker", "fixture checkpoint")
    monkeypatch.setattr(cli, "_registry", lambda a: FilesRegistry(root / "crew"))
    monkeypatch.setattr(cli, "_agent_trees", lambda *a, **kw: [clone])
    monkeypatch.setattr(cli, "_durable_checkpoint_gate",
                        lambda *a: DurableGate("worker", ok=True))
    actions = []
    monkeypatch.setattr(cli, "_cmd_stop", lambda a: actions.append("stop") or cli.OK)
    monkeypatch.setattr(cli, "_panes", lambda a: _NoPane())
    monkeypatch.setattr(cli, "_runtime", lambda *a: object())
    monkeypatch.setattr(cli, "_launch", lambda *a: actions.append("launch") or cli.OK)
    monkeypatch.setattr(cli, "_redispatch_after_cycle", lambda *a: actions.append("dispatch"))
    args = Namespace(root=root, agent="worker", reason="fixture checkpoint",
                     allow_loss=False, dry_run=False)
    return args, upstream, clone, card, requests, actions


@pytest.mark.parametrize("already_refused", [False, True])
def test_hanging_fetch_refuses_without_mutating_state(tmp_path, monkeypatch, capsys,
                                                     already_refused):
    args, _, clone, card, requests, actions = world(tmp_path, monkeypatch)
    if already_refused:
        requests.mark_refused("worker", "prior refusal")
    card_before, request_before = card.read_bytes(), requests.path.read_bytes()
    _run(clone, "remote", "set-url", "tracked", "ssh://fixture.invalid/repo")
    pidfile = tmp_path / "transport.pid"
    transport = tmp_path / "transport"
    transport.write_text(f'#!/bin/sh\necho $$ > "{pidfile}"\nexec sleep 120\n')
    transport.chmod(0o700)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(transport))
    monkeypatch.setenv("GIT_SSH_VARIANT", "ssh")
    started = time.monotonic()
    assert cli._cmd_cycle(args) == cli.REFUSED
    assert time.monotonic() - started < 10
    output = capsys.readouterr()
    assert not output.out
    assert len(output.err.strip().splitlines()) == 1
    assert "fetch timed out after 0.3s" in output.err
    assert "Traceback" not in output.err
    assert card.read_bytes() == card_before
    assert requests.path.read_bytes() == request_before
    assert actions == []
    pid = int(pidfile.read_text())
    # A timed-out fetch must not leave its transport running in the background.
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("timed-out transport survived")


def test_normal_cycle_skips_broken_untracked_remote(tmp_path, monkeypatch):
    args, _, clone, card, requests, actions = world(tmp_path, monkeypatch)
    _run(clone, "remote", "add", "unused", str(tmp_path / "does-not-exist"))
    before = card.read_bytes()
    assert cli._cmd_cycle(args) == cli.OK
    assert actions == ["stop", "launch", "dispatch"]
    assert requests.pending() == {}
    assert card.read_bytes() == before


def test_skipped_remote_cannot_hide_an_orphan(tmp_path):
    upstream = _repo(tmp_path / "upstream")
    clone = _clone(upstream, tmp_path / "clone")
    other = _clone(upstream, tmp_path / "other")
    _run(clone, "remote", "add", "unused", str(other))
    _commit(other, "only-on-other")
    _run(clone, "fetch", "unused")
    _run(clone, "merge", "--ff-only", "unused/main")
    _run(other, "checkout", "--detach", "HEAD~1")
    _run(other, "branch", "-D", "main")
    assert tree_staleness(clone).unpushed == 0  # stale ref falsely reassures
    assert tree_staleness(clone, fetch=True, tracked_only=True).unpushed == 1


@pytest.mark.parametrize("value", ["0", "-1", "true", '"slow"', "inf", "nan"])
def test_invalid_fetch_budget_refuses(tmp_path, value):
    (tmp_path / "shantytown.toml").write_text(
        f"[keep_current]\nfetch_timeout_seconds = {value}\n")
    with pytest.raises(config.ConfigError, match="finite positive"):
        config.load(tmp_path)


def test_default_fetch_budget(tmp_path):
    assert config.load(tmp_path).keep_current_fetch_timeout_seconds == 180


def test_scoped_publication_refusal_names_what_was_checked(tmp_path):
    from shantytown.cycle import assess
    upstream = _repo(tmp_path / "upstream")
    clone = _clone(upstream, tmp_path / "clone")
    _commit(clone, "not-published")
    verdict = assess("worker", [clone], "checkpoint",
                     lambda t: tree_staleness(t, fetch=True, tracked_only=True),
                     reachable=lambda t: True)
    assert not verdict.ok
    assert "not verified on remote origin" in verdict.render()
    assert "on no remote ref" not in verdict.render()
