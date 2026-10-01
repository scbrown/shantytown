"""The haul feed reads EVERY configured store, degrading loudly on an extra.

Before this, br.ready() and br.in_progress() read only the primary store, and
feed_check built its tracker without extra_repos. Work living in a repo-local
store was therefore on an agent's plate (the plate reader unions stores) but was
never self-fed by the haul, and Rule Zero could not see it either.
"""
import json
import subprocess

import pytest

from shantytown import br as br_mod
from shantytown.br import BrTracker


def _cp(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess(args=["br"], returncode=rc,
                                       stdout=stdout, stderr=stderr)


def _row(id_, status="open", assignee="worker"):
    return {"id": id_, "title": "t", "status": status, "assignee": assignee,
            "priority": 2}


def _stores(monkeypatch, by_repo, fail=()):
    seen = []

    def fake(self, repo, *args):
        seen.append((repo, args[:3]))
        if repo in fail:
            return _cp(stdout=json.dumps({"error": {"code": "X", "message": "boom"}}), rc=6)
        rows = by_repo.get(repo, [])
        if args[:3] == ("list", "--status", "in_progress"):
            rows = [r for r in rows if r["status"] == "in_progress"]
        elif args[:1] == ("ready",):
            rows = [r for r in rows if r["status"] == "open"]
        return _cp(stdout=json.dumps({"issues": rows}))
    monkeypatch.setattr(BrTracker, "_bd_in", fake, raising=True)
    monkeypatch.setattr(BrTracker, "_bd", lambda self, *a: fake(self, self.repo, *a),
                        raising=True)
    return seen


def _tracker(tmp_path):
    return BrTracker(repo=str(tmp_path / "aegis"), extra_repos=[str(tmp_path / "gold")])


def test_ready_and_in_progress_union_the_extra_store(monkeypatch, tmp_path):
    t = _tracker(tmp_path)
    _stores(monkeypatch, {
        t.repo: [_row("aegis-1"), _row("aegis-2", "in_progress")],
        t.extra_repos[0]: [_row("goldblum-1"), _row("goldblum-2", "in_progress")],
    })
    assert {r["id"] for r in br_mod.ready(t)} == {"aegis-1", "goldblum-1"}
    assert {r["id"] for r in br_mod.in_progress(t)} == {"aegis-2", "goldblum-2"}


def test_an_unreadable_extra_degrades_loudly_and_keeps_the_primary(monkeypatch, tmp_path, capsys):
    t = _tracker(tmp_path)
    _stores(monkeypatch, {t.repo: [_row("aegis-1")]}, fail=(t.extra_repos[0],))
    assert [r["id"] for r in br_mod.ready(t)] == ["aegis-1"]
    err = capsys.readouterr().err
    assert "SKIPPED extra store" in err and t.extra_repos[0] in err


def test_an_unreadable_primary_still_raises(monkeypatch, tmp_path):
    t = _tracker(tmp_path)
    _stores(monkeypatch, {t.extra_repos[0]: [_row("goldblum-1")]}, fail=(t.repo,))
    with pytest.raises(RuntimeError, match="br ready failed"):
        br_mod.ready(t)


def test_single_store_reads_only_the_primary(monkeypatch, tmp_path):
    t = BrTracker(repo=str(tmp_path / "aegis"))
    seen = _stores(monkeypatch, {t.repo: [_row("aegis-1")]})
    assert [r["id"] for r in br_mod.ready(t)] == ["aegis-1"]
    assert [repo for repo, _ in seen] == [t.repo]


def test_feed_check_tracker_carries_the_deployment_extras(monkeypatch, tmp_path):
    from shantytown import feed_check
    values = {"SHANTY_BACKEND": "br", "SHANTY_BEADS_REPO": str(tmp_path / "aegis"),
              "SHANTY_BEADS_REPOS_EXTRA": f"{tmp_path / 'na'},{tmp_path / 'gold'}"}
    monkeypatch.setattr("shantytown.deployment.deployment_default",
                        lambda root, key: values.get(key))
    t = feed_check._br_tracker(tmp_path, None)
    assert t.extra_repos == [str((tmp_path / "na").resolve()),
                             str((tmp_path / "gold").resolve())]
