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


def _show_update_stores(monkeypatch, by_repo, fuzzy_primary=None):
    """show/update fakes; `fuzzy_primary` makes the primary answer ANY show with that
    row (br's fuzzy resolution, aegis-m6t0oi) and accept any update."""
    writes = []

    def fake(self, repo, *args):
        if args[:1] == ("show",):
            want = args[1]
            if repo == self.repo and fuzzy_primary is not None:
                return _cp(stdout=json.dumps([fuzzy_primary]))
            rows = [r for r in by_repo.get(repo, []) if r["id"] == want]
            return _cp(stdout=json.dumps(rows)) if rows else _cp(rc=3, stderr="not found")
        if args[:1] == ("update",):
            writes.append((repo, args[1]))
            return _cp()
        return _cp(stdout=json.dumps({"issues": []}))
    monkeypatch.setattr(BrTracker, "_bd_in", fake, raising=True)
    monkeypatch.setattr(BrTracker, "_bd", lambda self, *a: fake(self, self.repo, *a),
                        raising=True)
    return writes


def test_a_claim_lands_in_the_extra_store_that_holds_the_bead(monkeypatch, tmp_path):
    t = _tracker(tmp_path)
    writes = _show_update_stores(monkeypatch, {t.extra_repos[0]: [_row("goldblum-1")]})
    br_mod.claim(t, "goldblum-1")
    assert writes == [(t.extra_repos[0], "goldblum-1")]
    assert br_mod.show(t, "goldblum-1")["id"] == "goldblum-1"


def test_a_fuzzy_primary_answer_never_routes_the_claim(monkeypatch, tmp_path):
    t = _tracker(tmp_path)
    writes = _show_update_stores(monkeypatch, {t.extra_repos[0]: [_row("goldblum-1")]},
                                 fuzzy_primary=_row("aegis-2gold1"))
    br_mod.claim(t, "goldblum-1")
    assert writes == [(t.extra_repos[0], "goldblum-1")], writes


def test_a_claim_held_by_no_store_raises_and_writes_nothing(monkeypatch, tmp_path):
    t = _tracker(tmp_path)
    # the primary prefix-resolves a missing id to a DIFFERENT bead with rc 0
    writes = _show_update_stores(monkeypatch, {}, fuzzy_primary=_row("aegis-krkddi"))
    with pytest.raises(RuntimeError, match="no store holds exactly"):
        br_mod.claim(t, "aegis-krkdd")
    assert writes == []
