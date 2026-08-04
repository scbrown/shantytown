"""A dispatch must name its STORE, not just its item (aegis-81zyb).

`st go` composed `Work is on your hook: <id> — <title>` and stopped. On a host
with one bd store that is complete; this host has **125**, of which **11 are
EMBEDDED** (measured 2026-08-01 by `stores.discover()`, 113ms, complete). With
125, an id is not merely underspecified — a cross-store dispatch and a phantom id
are the SAME OBSERVATION to the agent receiving it, and nothing tells it the
question is even open.

What that cost, measured: an agent was dispatched an item, could not find it in
its default store, swept every database on the Dolt server with validated
positive controls, got zero rows, and concluded the item existed in NO store. It
existed — in an EMBEDDED store, which is not on that server at any level of
thoroughness. The wrong conclusion shipped as `confidence:extracted` and a lead
reinforced it before both were retracted.

These tests pin the two halves of the fix and, as much as either, the two ways it
could become NOISE — because a tag the fleet learns to skip is not a fix:

  * the tag is UNCONDITIONAL, so no dispatch is ever silent about its store;
  * the WARNING is conditional, and specifically it must NOT fire for one store
    reached by two paths (the live crew store is exactly that — a `redirect`),
    nor for a store whose metadata we merely could not read.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from shantytown import stores
from shantytown.dispatch import Dispatcher
from shantytown.files import FilesRegistry, FilesTracker
from shantytown.protocols import WorkItem
from shantytown.tmux import NullPanes


# --- doubles -----------------------------------------------------------------

class StoreTracker:
    """A tracker with a `.repo`, as BeadsTracker has. The suite's FilesTracker has
    none — which is why 1553 existing tests passed against the silent dispatch and
    could never have caught this: the only tracker under test could not BE
    cross-store."""

    def __init__(self, repo: str, item: WorkItem):
        self.repo = repo
        self.item = item
        self.updates: list[tuple] = []

    def get(self, item_id: str) -> WorkItem:
        return self.item

    def update(self, item_id: str, **fields) -> None:
        self.updates.append((item_id, fields))
        # AND IT APPLIES THEM (aegis-8xc5w). This double used to record the call
        # and change nothing, which is now indistinguishable from the swallowed
        # write `go` exists to catch — the read-back rejected it, correctly. A
        # double standing in for a HEALTHY tracker in a test about something else
        # has to behave like one; a tracker that accepts and does not apply is a
        # subject, not a stand-in, and it has its own tests.
        self.item = replace(self.item, **{
            k: v for k, v in fields.items() if hasattr(self.item, k)})


def _store(path: Path, database: str | None = "beads_aegis", mode: str = "server",
           host: str = "db.invalid", port: int = 3306) -> Path:
    """A bd store on disk: a `.beads` with the metadata bd actually writes."""
    beads = path / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    md: dict = {"database": "dolt", "backend": "dolt", "dolt_mode": mode}
    if database is not None:
        md["dolt_database"] = database
    if mode == "server":
        md |= {"dolt_server_host": host, "dolt_server_port": port}
    (beads / "metadata.json").write_text(json.dumps(md))
    return path


def _redirect(path: Path, target: str) -> Path:
    """A `.beads` that is a POINTER, the shape the live crew store has:
    `~/gt/beads_aegis/.beads/redirect` holds `mayor/rig/.beads` and no metadata."""
    beads = path / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "redirect").write_text(target)
    return path


def _crew(root: Path, workspace: str | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    card: dict = {"role": "worker", "pane": "%5"}
    if workspace:
        card["workspace"] = workspace
    (root / "ellie.json").write_text(json.dumps(card))
    return root


def _dispatch(tmp_path: Path, repo: Path, workspace: str | None, panes=None):
    panes = panes or NullPanes(screen="")
    reg = FilesRegistry(_crew(tmp_path / ".shanty" / "crew", workspace))
    trk = StoreTracker(str(repo), WorkItem(id="na-2mn", title="Restore the den",
                                           status="open", assignee=None))
    return Dispatcher(reg, trk, panes), panes


# --- the defect: the hook line said nothing about WHERE ----------------------

def test_the_hook_line_names_the_store(tmp_path):
    """The whole bug in one assertion."""
    repo = _store(tmp_path / "rig")
    d, _ = _dispatch(tmp_path, repo, workspace=None)
    text = d.go("na-2mn", "ellie", dry_run=True).text

    assert str(repo) in text, (
        f"a dispatch that names an item but not its store is unanswerable on a "
        f"host with 125 bd stores: {text!r}"
    )


def test_the_store_is_named_as_a_command_not_a_bare_path(tmp_path):
    """`bd -C <path>` is actionable; a bare path still leaves a step to guess."""
    repo = _store(tmp_path / "rig")
    d, _ = _dispatch(tmp_path, repo, workspace=None)
    assert f"bd -C {repo}" in d.go("na-2mn", "ellie", dry_run=True).text


def test_the_tag_rides_the_same_send_as_the_work(tmp_path):
    """One send-keys carrying both — the aegis-8013 property, applied to the store.

    This is the compounding half of the measured incident: the dispatcher DID send
    the store path, twice, explicitly. It went to a DIFFERENT PANE than the hook
    line that started the session doing the work. Context sent separately from
    work arrives at whoever is standing in that pane, which may be nobody.
    """
    repo = _store(tmp_path / "rig")
    d, panes = _dispatch(tmp_path, repo, workspace=None)
    d.go("na-2mn", "ellie")

    assert len(panes.sent) == 1, f"the store must not need a second send: {panes.sent}"
    _pane, text = panes.sent[0]
    assert "na-2mn" in text and str(repo) in text


def test_the_preview_shows_the_store_too(tmp_path):
    """--dry-run is what an operator reads to decide whether a dispatch is right,
    and `render()` shows the note but never the payload — so a store carried only
    in `text` would reach the AGENT and not the person authorising the send."""
    repo = _store(tmp_path / "rig")
    d, panes = _dispatch(tmp_path, repo, workspace=None)
    p = d.go("na-2mn", "ellie", dry_run=True)
    assert str(repo) in p.text
    assert str(repo) in p.render(), (
        f"the operator's preview must name the store, not just the payload: "
        f"{p.render()!r}"
    )
    assert panes.sent == [], "--dry-run must still send nothing"


def test_a_files_backed_preview_gains_no_store_line(tmp_path):
    """No store, no line — render() must not grow an empty field."""
    root = tmp_path / ".shanty"
    (root / "items").mkdir(parents=True)
    (root / "items" / "item-1.json").write_text(
        json.dumps({"title": "Restore the den", "status": "open"}))
    d = Dispatcher(FilesRegistry(_crew(root / "crew", None)),
                   FilesTracker(root / "items"), NullPanes(screen=""))
    assert "name store" not in d.go("item-1", "ellie", dry_run=True).render()


# --- the expensive case: genuinely cross-store -------------------------------

def test_cross_store_dispatch_is_shouted_and_names_both_sides(tmp_path):
    """The measured incident: work in an EMBEDDED store, recipient's workspace on
    the server rig. Naming the store is not enough here — the agent must be told
    its own default is the wrong place to look, or it will search there first,
    find nothing, and generalise."""
    theirs = _store(tmp_path / "rig", database="beads_aegis")
    ws = theirs / "crew" / "ellie"
    ws.mkdir(parents=True)
    mine = _store(tmp_path / "NeuralAmplifier", database="na", mode="embedded")

    d, _ = _dispatch(tmp_path, mine, workspace=str(ws))
    text = d.go("na-2mn", "ellie", dry_run=True).text

    assert "DIFFERENT STORE" in text, f"a cross-store dispatch must say so: {text!r}"
    assert "na" in text and "beads_aegis" in text, (
        f"name BOTH identities — 'this is elsewhere' without saying elsewhere-than-"
        f"what is not re-checkable: {text!r}"
    )
    assert str(mine) in text


def test_a_same_store_dispatch_does_not_shout(tmp_path):
    """The common case stays quiet. A warning on every dispatch is a warning the
    fleet stops reading, and then the cross-store one goes past unnoticed too."""
    repo = _store(tmp_path / "rig")
    ws = repo / "crew" / "ellie"
    ws.mkdir(parents=True)
    d, _ = _dispatch(tmp_path, repo, workspace=str(ws))
    text = d.go("na-2mn", "ellie", dry_run=True).text

    assert "DIFFERENT STORE" not in text
    assert str(repo) in text, "quiet still means NAMED — silence is the bug"


def test_one_store_reached_by_two_paths_does_not_shout(tmp_path):
    """THE REDIRECT CASE, and the reason identity is compared and not paths.

    The live crew store is exactly this: `~/gt/beads_aegis/.beads` holds no
    metadata at all, only a `redirect` to `mayor/rig/.beads`, where
    `dolt_database: beads_aegis` lives. A path-equality check would flag the
    fleet's single most common dispatch as cross-store — crying wolf on the one
    path that is always correct.
    """
    real = _store(tmp_path / "rig" / "mayor" / "rig", database="beads_aegis")
    front = _redirect(tmp_path / "rig", "mayor/rig/.beads")
    ws = front / "crew" / "ellie"
    ws.mkdir(parents=True)

    assert stores.describe(front).identity == stores.describe(real).identity, (
        "two paths onto one database must compare EQUAL"
    )
    d, _ = _dispatch(tmp_path, real, workspace=str(ws))
    assert "DIFFERENT STORE" not in d.go("na-2mn", "ellie", dry_run=True).text


def test_unreadable_metadata_never_shouts_difference(tmp_path):
    """'I could not read its metadata' is not evidence of a different store.

    Rendering could-not-tell as a positive finding is the same class of error the
    incident itself was — a miss generalised into a fact.
    """
    theirs = _store(tmp_path / "rig", database=None, mode="")
    ws = theirs / "crew" / "ellie"
    ws.mkdir(parents=True)
    mine = _store(tmp_path / "other", database="na", mode="embedded")

    d, _ = _dispatch(tmp_path, mine, workspace=str(ws))
    text = d.go("na-2mn", "ellie", dry_run=True).text
    assert "DIFFERENT STORE" not in text
    assert str(mine) in text, "still named, just not asserted to differ"


def test_a_files_backed_dispatch_gets_no_tag(tmp_path):
    """No store concept, no tag. The files backend cannot be cross-store, so a
    tag there would be pure noise on the path that has no bug."""
    root = tmp_path / ".shanty"
    (root / "items").mkdir(parents=True)
    (root / "items" / "item-1.json").write_text(
        json.dumps({"title": "Restore the den", "status": "open"}))
    d = Dispatcher(FilesRegistry(_crew(root / "crew", None)),
                   FilesTracker(root / "items"), NullPanes(screen=""))
    assert "st store:" not in d.go("item-1", "ellie", dry_run=True).text


def test_a_forge_coordinate_is_named_without_a_minus_C(tmp_path):
    """ForgejoTracker's `.repo` is `owner/name` — a coordinate, not a directory.
    Worth naming (same question), but there is no -C and nothing to compare."""
    d, _ = _dispatch(tmp_path, Path("scbrown/shantytown"), workspace=None)
    text = d.go("na-2mn", "ellie", dry_run=True).text
    assert "scbrown/shantytown" in text
    assert "bd -C" not in text


# --- resolution + identity ---------------------------------------------------

def test_resolve_from_walks_past_the_clone_boundary(tmp_path):
    """A crew workspace is its OWN git clone and holds no `.beads` — measured:
    `~/gt/beads_aegis/crew/muldoon` has none, the store is at the rig root above
    it. Stopping where bd stops would answer 'no store' for every crew member."""
    rig = _store(tmp_path / "rig")
    ws = rig / "crew" / "ellie"
    ws.mkdir(parents=True)
    assert stores.resolve_from(str(ws)) == str(rig.resolve())


def test_resolve_from_reports_could_not_tell_rather_than_guessing(tmp_path):
    assert stores.resolve_from(None) is None
    assert stores.resolve_from(str(tmp_path / "nope" / "deeper")) is None


def test_identity_reads_dolt_database_not_the_backend_key(tmp_path):
    """metadata's `database` is "dolt" — the BACKEND — on every store measured on
    this host. `dolt_database` is the name. Reading the obvious key would label
    all 125 stores "dolt" and make every comparison vacuously equal while looking
    like it worked."""
    s = stores.describe(_store(tmp_path / "rig", database="beads_aegis"))
    assert s.database == "beads_aegis"
    assert s.identity == "db.invalid:3306/beads_aegis"
    assert s.resolved


def test_an_embedded_store_is_identified_by_its_path(tmp_path):
    """An embedded store is not on the Dolt server at any level of thoroughness —
    which is precisely why sweeping all 42 server databases returned zero rows and
    read as proof of non-existence."""
    s = stores.describe(_store(tmp_path / "na", database="na", mode="embedded"))
    assert s.identity.startswith("embedded:")
    assert "na" in s.identity


def test_a_redirect_cycle_does_not_hang(tmp_path):
    """A diagnostic that hangs is worse than one that admits it cannot tell."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    _redirect(a, str(b / ".beads"))
    _redirect(b, str(a / ".beads"))
    assert stores.describe(a).resolved is False


# --- item 2: absence with a NAMED BOUNDARY -----------------------------------

def test_not_found_names_the_store_searched(tmp_path):
    """Bare absence is what invited the generalisation. 'Not in THIS store' is
    re-checkable by a stranger in two minutes; 'not found' is not."""
    repo = _store(tmp_path / "rig")
    msg = stores.not_found_here(str(repo), "na-2mn", roots=[str(tmp_path)])
    assert str(repo) in msg
    assert "na-2mn" in msg


def test_not_found_counts_the_unsearched_remainder(tmp_path):
    """The useful quantity is how much space remains unsearched — that is the
    number that stops 'zero rows here' from becoming 'it exists nowhere'."""
    for name in ("one", "two", "three"):
        _store(tmp_path / name, database=name)
    msg = stores.not_found_here(str(tmp_path / "one"), "na-2mn", roots=[str(tmp_path)])
    assert "2 other bd store(s)" in msg, msg


def test_not_found_refuses_to_conclude_nonexistence(tmp_path):
    """The message must not merely inform — it must name the wrong inference,
    because the incident was a careful agent reasoning carefully to it."""
    msg = stores.not_found_here(str(_store(tmp_path / "rig")), "na-2mn",
                                roots=[str(tmp_path)])
    assert "not on this host" in msg
    assert "absence in ONE store" in msg


def test_the_beads_tracker_lookup_carries_the_boundary(tmp_path):
    """Wired where it is actually hit: BeadsTracker.get's LookupError."""
    from shantytown.beads import BeadsTracker

    repo = _store(tmp_path / "rig")
    trk = BeadsTracker(repo=str(repo))
    trk._bd = lambda *a: type("R", (), {"returncode": 1, "stderr": "issue not found",
                                        "stdout": ""})()
    with pytest.raises(LookupError) as e:
        trk.get("na-2mn")
    assert "absence in ONE store" in str(e.value)
    assert str(repo) in str(e.value)


def test_a_broken_enumeration_never_replaces_the_real_error(tmp_path):
    """A diagnostic that fails must not eat the error it was decorating."""
    from shantytown import beads as beads_mod
    from shantytown.beads import BeadsTracker

    trk = BeadsTracker(repo=str(_store(tmp_path / "rig")))
    trk._bd = lambda *a: type("R", (), {"returncode": 1, "stderr": "the real cause",
                                        "stdout": ""})()

    def boom(*a, **k):
        raise RuntimeError("scan exploded")

    orig, beads_mod.stores.not_found_here = beads_mod.stores.not_found_here, boom
    try:
        with pytest.raises(LookupError) as e:
            trk.get("na-2mn")
        assert "the real cause" in str(e.value)
    finally:
        beads_mod.stores.not_found_here = orig


# --- discovery ---------------------------------------------------------------

def test_discover_finds_stores_and_reports_completeness(tmp_path):
    _store(tmp_path / "a", database="a")
    _store(tmp_path / "b" / "nested", database="b")
    found, complete = stores.discover([str(tmp_path)])
    assert complete
    assert {s.database for s in found} == {"a", "b"}


def test_discover_skips_dotdirs(tmp_path):
    """`.git`/`.venv` are thousands of directories that cannot hold a store we
    care about, and they are what would burn the walk budget before reaching one."""
    _store(tmp_path / ".venv" / "pkg", database="noise")
    _store(tmp_path / "real", database="real")
    found, _ = stores.discover([str(tmp_path)])
    assert {s.database for s in found} == {"real"}
