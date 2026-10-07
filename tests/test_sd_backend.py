"""SdTracker: the seeds backend (aegis-w3k75d.15), against a SCRATCH sd store.

Review bar (dearing, aegis-w3k75d.15 [w3k75d15-sdtracker-terms-dearing-1007]):
1. no silent empty board: an unproven store REFUSES, naming the store;
2. an empty read is trusted only behind a passing store-identity check;
3. the default backend is unchanged (seeds is opt-in);
4. parity with BrTracker for the verbs st uses, on a scratch store;
5. exact ids: an unknown id fails, nothing fuzzy-resolves.

Every store here lives under tmp_path. No test reaches a live board: br is NOT
exercised against a scratch store either, because a scratch br command under
~/gt can route to the live board (aegis-i21xff); BrTracker parity is asserted
against the record shapes its methods already consume.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from shantytown import br as br_mod
from shantytown.sd import BR_LIKE, SdTracker, StoreUnproven, br_like_tracker

SD = shutil.which("sd")
pytestmark = [pytest.mark.real_store,
              pytest.mark.skipif(SD is None, reason="sd not installed")]


def _project(tmp_path: Path, prefix: str = "aegis") -> tuple[Path, Path]:
    proj = tmp_path / "proj"
    proj.mkdir()
    store = tmp_path / "board.db"
    subprocess.run([SD, "--store", str(store), "init", "--prefix", prefix],
                   cwd=proj, check=True, capture_output=True, text=True)
    return proj, store


def _sd(proj: Path, store: Path, *args: str) -> str:
    r = subprocess.run([SD, "--store", str(store), "--actor", "tester", *args],
                       cwd=proj, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _tracker(proj, store, prefix="aegis"):
    return SdTracker(repo=str(proj), store=str(store), prefix=prefix)


# 1. NO SILENT EMPTY BOARD ---------------------------------------------------

def test_no_configured_store_refuses_instead_of_reading_a_default(tmp_path):
    t = SdTracker(repo=str(tmp_path), prefix="aegis")
    with pytest.raises(StoreUnproven, match="no store configured"):
        br_mod.ready(t)


def test_the_measured_fallback_is_refused_and_names_the_store(tmp_path, monkeypatch):
    """Reproduce the finding: an sd that IGNORES the store it was given falls back
    to a default local store and would answer with an empty board at exit 0."""
    proj, store = _project(tmp_path)
    shim = tmp_path / "sd-ignores-store"
    shim.write_text("#!/bin/bash\n"
                    "args=(); skip=0\n"
                    "for a in \"$@\"; do\n"
                    "  if [ $skip = 1 ]; then skip=0; continue; fi\n"
                    "  if [ \"$a\" = --store ]; then skip=1; continue; fi\n"
                    "  args+=(\"$a\"); done\n"
                    f"cd {tmp_path} && exec {SD} \"${{args[@]}}\"\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    # CONTROL: the shim really does produce the silent empty board.
    empty = subprocess.run([str(shim), "--store", str(store), "ready", "--json"],
                           capture_output=True, text=True)
    assert empty.returncode == 0 and empty.stdout.strip() in ("[]", "")
    monkeypatch.setenv("SHANTY_SD_BIN", str(shim))
    with pytest.raises(StoreUnproven) as e:
        br_mod.ready(_tracker(proj, store))
    assert "default" in str(e.value) and str(store) in str(e.value)


def test_both_stores_named_refuses(tmp_path):
    t = SdTracker(repo=str(tmp_path), store=str(tmp_path / "a.db"), quipu="http://x",
                  prefix="aegis")
    with pytest.raises(StoreUnproven, match="exactly one"):
        br_mod.ready(t)


def test_a_nonexistent_store_refuses_and_is_not_created(tmp_path):
    """dearing's review: `sd where` reports a MISSING store exactly like a real one,
    and `list` answers it with an empty board at rc 0. A typo in SHANTY_SD_STORE
    must refuse, and must not leave a freshly minted store behind."""
    proj, _ = _project(tmp_path)
    typo = tmp_path / "boardd.db"
    # CONTROL: sd itself reads the missing store as an empty board, rc 0.
    r = subprocess.run([SD, "--store", str(typo), "list", "--json"], cwd=proj,
                       capture_output=True, text=True)
    assert r.returncode == 0 and '"total":0' in r.stdout.replace(" ", "")
    with pytest.raises(StoreUnproven, match="no seeds store at"):
        br_mod.ready(_tracker(proj, typo))
    with pytest.raises(StoreUnproven):
        _tracker(proj, typo).create("would mint a store", assignee="wu")
    assert not typo.exists()


def test_an_unset_prefix_refuses(tmp_path):
    proj, store = _project(tmp_path)
    with pytest.raises(StoreUnproven, match="SHANTY_SD_PREFIX"):
        br_mod.ready(SdTracker(repo=str(proj), store=str(store)))


def test_a_wrong_prefix_is_refused(tmp_path):
    proj, store = _project(tmp_path, prefix="other")
    with pytest.raises(StoreUnproven, match="prefix"):
        br_mod.ready(_tracker(proj, store, prefix="aegis"))


# 2. EMPTY READS ARE IDENTITY-GATED -------------------------------------------

def test_an_empty_proven_board_reads_empty_and_says_where(tmp_path):
    proj, store = _project(tmp_path)
    only = _sd(proj, store, "create", "done already", "--silent")
    _sd(proj, store, "close", only, "--reason", "fixture")
    t = _tracker(proj, store)
    assert br_mod.ready(t) == []
    assert t._proof.location_source == "--store"
    assert t._proof.database_path == str(store)


# 3. DEFAULT UNCHANGED ----------------------------------------------------------

def test_seeds_is_opt_in(tmp_path):
    get = {"SHANTY_BR_REPO": str(tmp_path)}.get
    assert type(br_like_tracker(get)) is br_mod.BrTracker
    assert type(br_like_tracker(get, "br")) is br_mod.BrTracker
    assert "seeds" in BR_LIKE


# 4. PARITY: the verbs st uses, on a scratch store -----------------------------

def test_plate_ready_claim_comments_close_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEDS_ACTOR", "wu")
    proj, store = _project(tmp_path)
    a = _sd(proj, store, "create", "first", "--silent")
    b = _sd(proj, store, "create", "second", "--assignee", "wu", "--silent")
    assert a.startswith("aegis-") and b.startswith("aegis-")
    t = _tracker(proj, store)

    ready = br_mod.ready(t)
    assert {r["id"] for r in ready} == {a, b}
    assert {"id", "title", "assignee", "priority"} <= set(ready[0])

    got = br_mod.show(t, b)
    assert got["id"] == b and got["assignee"] == "wu" and got["status"] == "open"

    br_mod.claim(t, a)
    assert br_mod.show(t, a)["status"] == "in_progress"
    assert a in {r["id"] for r in br_mod.in_progress(t)}

    br_mod.append_comment(t, a, "handoff: where I stopped")
    cs = br_mod.comments(t, a)          # br's bare `comments <id>` -> `comments list`
    assert [c["text"] for c in cs] == ["handoff: where I stopped"]
    assert {"author", "created_at", "text"} <= set(cs[0])

    plate = br_mod.plate(t, "wu")
    assert plate is not None

    item = t.create("third", assignee="wu")
    assert item.id.startswith("aegis-")


# 5. EXACT IDS -------------------------------------------------------------------

def test_an_unknown_id_fails_and_never_resolves_to_another(tmp_path):
    proj, store = _project(tmp_path)
    real = _sd(proj, store, "create", "real", "--silent")
    t = _tracker(proj, store)
    with pytest.raises(Exception):
        br_mod.show(t, real[:-1] + ("x" if real[-1] != "x" else "y"))
    with pytest.raises(Exception):
        br_mod.show(t, real.split("-")[0] + "-")
