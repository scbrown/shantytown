"""stores — WHICH bd store, said out loud (aegis-81zyb).

A dispatch used to name an ITEM and never its STORE:

    Work is on your hook: na-2mn — <title>

An id and a title. On a host with more than one bd store that is not a dispatch,
it is a riddle, and the receiving agent has no signal that the question is even
open. Measured 2026-08-01 (malcolm): dispatched an item, could not find it in the
default store, swept every database on the Dolt server with validated positive
controls, got zero rows, and concluded — with `confidence:extracted`, which a
lead then reinforced — that the item existed in NO store. It existed. It was in
an EMBEDDED store that is not on that server at any level of thoroughness.

HOW MANY STORES ARE THERE, REALLY. The bead said "at least four". Measured here
2026-08-01 by `discover()` below, over $HOME: **125**, of which **11 are
EMBEDDED** — i.e. reachable by no amount of thoroughness against the Dolt server,
which is exactly the trap that produced the wrong conclusion. That is the number
that makes this a bug rather than a nicety: with four you might guess; with 125 a
bare id is not underspecified, it is unanswerable.

WHY NOT COMPARE PATHS. Because the same store has many paths, and not rarely —
**16 of those 125 paths resolve to the one database `db.invalid:3306/beads_aegis`**
(measured, same run). `~/gt/beads_aegis/.beads` holds no metadata at all; it holds
a one-line `redirect` to `mayor/rig/.beads`, and THAT is where
`dolt_database: beads_aegis` lives. So a path-equality check would have shouted
"different store" at 15 of 16 CORRECT dispatches — crying wolf on the fleet's most
common path, until the warning meant nothing and the real one went past unread.
identity() therefore resolves through the redirect and compares what bd would
actually TALK to: server host:port + database, or the embedded database's path.

WHY THE TAG IS UNCONDITIONAL. The bead proposed naming the store only "when it is
not the default", and that is the tempting half-measure. It requires us to be
right about the recipient's default — but bd resolves from the AMBIENT CWD with a
clone-boundary rule, so "their default" is a function of where the agent happens
to be standing when it types, which is not a property this process can compute.
A conditional built on a guess is silent in exactly the case that costs a day.
Naming it always has no failure mode and costs one short tag, so the tag is
always there and the cross-store WARNING is the conditional part.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

BEADS = ".beads"

# Bounds on discover(). The scan runs on a FAILURE path, so it must be fast and it
# must never wander. Measured on this host: the full $HOME walk visits well under
# 8000 directories and finds all 125 stores in 113ms, complete=True. So these are
# ~2.5x measured headroom rather than a guess at a safe-looking number, and if a
# host ever does exceed them the caller is TOLD (complete=False) instead of being
# handed a truncated list dressed as a total.
_MAX_DEPTH = 4
_MAX_DIRS = 20000


@dataclass(frozen=True)
class Store:
    """One bd store: the directory you hand `bd -C`, and what it resolves TO.

    `path` is what an agent types. `identity` is what bd talks to. They are
    different things and conflating them is the redirect bug above.
    """

    path: str
    database: str | None = None   # metadata's dolt_database — the store's real name
    mode: str | None = None       # "server" | "embedded" | None (did not say)
    host: str | None = None
    port: int | None = None

    @property
    def identity(self) -> str:
        """What bd would actually TALK TO, as a comparable string.

        NEVER falls back to the path when the store did say who it is: two paths
        onto one database must compare EQUAL, that being the whole point. When the
        store did NOT say, the path is all we have and it is returned tagged as
        unresolved — an honest "could not tell", not a silent claim of difference.
        """
        if self.mode == "server" and self.database:
            where = f"{self.host or 'localhost'}:{self.port or 3306}"
            return f"{where}/{self.database}"
        if self.mode == "embedded" and self.database:
            return f"embedded:{self.path}/{self.database}"
        if self.database:
            return f"{self.database}@{self.path}"
        return f"unresolved:{self.path}"

    @property
    def resolved(self) -> bool:
        """Did the store tell us who it is? An UNRESOLVED store must never be
        compared for inequality — 'I could not read its metadata' is not evidence
        that it is a different store, and treating it as such is how a warning
        becomes noise the fleet learns to skip."""
        return self.database is not None

    def describe(self) -> str:
        return f"{self.path} ({self.identity})"


def _read_metadata(beads_dir: Path) -> dict:
    """metadata.json for a `.beads`, FOLLOWING a `redirect` if one is there.

    `redirect` holds a path — relative to the .beads' PARENT, as measured on the
    live crew store, where `~/gt/beads_aegis/.beads/redirect` reads
    `mayor/rig/.beads`. Followed with a hop limit because a redirect cycle would
    otherwise hang a diagnostic, and a diagnostic that hangs is worse than one
    that admits it cannot tell.
    """
    seen: set[str] = set()
    for _hop in range(8):
        try:
            key = str(beads_dir.resolve())
        except OSError:
            return {}
        if key in seen:
            return {}                      # cycle: could not tell, and say nothing
        seen.add(key)
        redirect = beads_dir / "redirect"
        if redirect.is_file():
            try:
                target = redirect.read_text().strip()
            except OSError:
                return {}
            if not target:
                return {}
            nxt = Path(target)
            if not nxt.is_absolute():
                nxt = beads_dir.parent / nxt
            beads_dir = nxt
            continue
        try:
            return json.loads((beads_dir / "metadata.json").read_text())
        except (OSError, ValueError):
            return {}
    return {}


def describe(path: str | os.PathLike) -> Store:
    """The Store for a directory you would hand to `bd -C`.

    Takes the directory ABOVE `.beads` (what bd wants), not the `.beads` itself,
    because that is the form that appears in a dispatch and in an error message.
    """
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        pass
    md = _read_metadata(p / BEADS)
    port = md.get("dolt_server_port")
    return Store(
        path=str(p),
        # `database` in metadata is the BACKEND ("dolt") on every store measured
        # here — it is not the store's name. `dolt_database` is the name. Reading
        # the obvious key would label all 125 stores "dolt" and make the whole
        # comparison vacuous while looking like it worked.
        database=md.get("dolt_database"),
        mode=md.get("dolt_mode"),
        host=md.get("dolt_server_host"),
        port=int(port) if isinstance(port, (int, str)) and str(port).isdigit() else None,
    )


def resolve_from(start: str | os.PathLike | None) -> str | None:
    """Walk UP from `start` to the nearest directory holding a `.beads`.

    This is the same walk feed_check.bd_cwd does for the administrator, hoisted so
    the dispatcher can ask it about the RECIPIENT. It must walk past the git
    boundary bd itself respects: a crew workspace is its own clone and holds no
    `.beads` (measured — `~/gt/beads_aegis/crew/muldoon` has none), so stopping at
    the clone boundary would answer "no store" for every crew member on the rig.
    """
    if not start:
        return None
    p = Path(start).expanduser()
    try:
        p = p.resolve()
    except OSError:
        return None
    for anc in (p, *p.parents):
        if (anc / BEADS).is_dir():
            return str(anc)
    return None


def store_of(tracker) -> str | None:
    """The store a tracker was constructed against, or None if it has no notion
    of one. FilesTracker has no `.repo` and cannot be cross-store; ForgejoTracker's
    `.repo` is `owner/name`, which is a coordinate and not a path — both fall out
    correctly because this only reports what is there and never interprets it."""
    repo = getattr(tracker, "repo", None)
    return str(repo) if repo else None


def discover(roots: list[str] | None = None) -> tuple[list[Store], bool]:
    """Every bd store under `roots` (default: $HOME). Returns (stores, complete).

    `complete=False` means the walk hit its bound and the list is PARTIAL. It is
    returned rather than logged because the caller is about to print a count, and
    "N stores exist" read off a truncated scan is a lower bound rendered as a
    fact — the same could-not-tell-as-fine class this whole module is about.
    """
    seeds = [Path(r).expanduser() for r in roots] if roots else [Path.home()]
    found: list[Store] = []
    seen: set[str] = set()
    visited = 0
    complete = True
    for seed in seeds:
        stack: list[tuple[Path, int]] = [(seed, 0)]
        while stack:
            d, depth = stack.pop()
            if visited >= _MAX_DIRS:
                complete = False
                break
            visited += 1
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue                    # unreadable dir is not a missing store
            for e in entries:
                if not e.is_dir(follow_symlinks=False):
                    continue
                if e.name == BEADS:
                    key = str(Path(e.path).parent)
                    if key not in seen:
                        seen.add(key)
                        found.append(describe(key))
                elif depth < _MAX_DEPTH and not e.name.startswith("."):
                    # Do NOT descend into dotdirs — `.git`/`.venv` are thousands of
                    # directories that cannot contain a store we care about, and
                    # they are what would burn the budget before reaching a real one.
                    stack.append((Path(e.path), depth + 1))
        if not complete:
            break
    return sorted(found, key=lambda s: s.path), complete


def hook_tag(tracker, workspace: str | None) -> str | None:
    """The `[st store: ...]` tag for a dispatch hook line, or None.

    None ONLY when the tracker has no store concept at all (the files backend).
    Otherwise the tag is always produced — see the module docstring on why this is
    unconditional. The tag is written as the COMMAND the agent would type, not as
    a bare path, so it is actionable rather than merely informative.
    """
    repo = store_of(tracker)
    if not repo:
        return None
    if not Path(repo).expanduser().is_dir():
        # A coordinate, not a directory (forgejo's `owner/name`). Still worth
        # naming — it is the same question — but there is no -C and nothing to
        # compare it against.
        return f"[st store: {repo}]"
    dispatched = describe(repo)
    theirs_path = resolve_from(workspace)
    theirs = describe(theirs_path) if theirs_path else None
    if (theirs is not None and theirs.resolved and dispatched.resolved
            and theirs.identity != dispatched.identity):
        # THE EXPENSIVE CASE, and the only one that gets shouted at. Both sides
        # resolved and they disagree, so this item is genuinely not where the
        # recipient's workspace points and `-C` is not optional.
        return (f"[st store: bd -C {dispatched.path} — DIFFERENT STORE from your "
                f"workspace's ({dispatched.identity} vs {theirs.identity}); "
                f"-C is REQUIRED, the id will NOT resolve without it]")
    return f"[st store: bd -C {dispatched.path}]"


def not_found_here(repo: str | None, item_id: str, roots: list[str] | None = None) -> str:
    """The sentence to append when an id did not resolve: absence WITH A NAMED
    BOUNDARY (the bead's item 2).

    Bare absence is what invited the generalisation that cost a day — "it is in no
    store" is a conclusion a stranger cannot re-check, while "not in THIS store,
    and there are 106 others" is one they can. The count is deliberately of OTHER
    stores, because the useful quantity is how much unsearched space remains.
    """
    where = repo or "the ambient-cwd store"
    try:
        stores, complete = discover(roots)
    except Exception:
        return f"searched {where}; could not enumerate the other bd stores on this host"
    others = [s for s in stores if s.path != (repo and str(Path(repo).expanduser().resolve()))]
    n = len(others)
    atleast = "at least " if not complete else ""
    return (f"{item_id} did not resolve in {where} — that is absence in ONE store, "
            f"not on this host: {atleast}{n} other bd store(s) exist here. If it was "
            f"dispatched to you, the dispatch names its store; ask for it rather "
            f"than concluding the id does not exist.")
