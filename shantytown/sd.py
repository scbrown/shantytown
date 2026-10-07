"""sd — the seeds tracker backend (aegis-w3k75d.15).

seeds speaks br's verbs and JSON closely enough that BrTracker's methods work
unchanged once the transport is `sd`, with two exceptions this module owns:

1. WHICH STORE. sd ignores `.beads`: run from a br repo it falls back to a
   DEFAULT local store (`.seeds/seeds.db`, prefix `sd`) and answers every read
   with an EMPTY board at exit 0. That is the silent blank plate this repo
   exists to refuse. So SdTracker names its store explicitly on every call
   (`--store <path>` or `--quipu <url>`) and, before its first command, proves
   with `sd where --json` that sd resolved exactly that store. Unproven means
   REFUSE, naming the store, never "an empty board".
2. `comments <id>`: br lists comments with the bare form; sd needs
   `comments list <id>`.

Exact ids: an unknown id exits nonzero in sd (`no seed with id ...`, rc 3), and
SdTracker passes ids through untouched, so nothing fuzzy-resolves.

Selected by SHANTY_BACKEND=seeds (opt-in). Configuration, from the deployment
env like the other SHANTY_* keys:
  SHANTY_SD_STORE  path to a local seeds store, or
  SHANTY_SD_QUIPU  base URL of the quipu server holding the board
  SHANTY_SD_PREFIX expected id prefix (e.g. aegis); checked when set
  SHANTY_SD_BIN    the sd binary (default: sd)
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from .br import BrTracker, _failure_reason

#: Backends served by a br-compatible CLI. A site that asks "is this a tracker
#: with plates, comments and hauls" asks THIS, so a new name cannot be missed in
#: one place and silently fall through to files in another.
BR_LIKE = ("beads", "br", "seeds")


class StoreUnproven(RuntimeError):
    """sd could not be shown to read the configured store."""


@dataclass(frozen=True)
class StoreProof:
    """What `sd where --json` showed for the configured store. Only ever built
    from a check that PASSED; a failed check raises StoreUnproven instead."""

    location_source: str
    database_path: "str | None"
    quipu_url: "str | None"
    prefix: "str | None"


class SdTracker(BrTracker):
    """BrTracker over `sd`, pinned to one proven store."""

    _tool = "sd"

    def __init__(self, repo: "str | None" = None, timeout: int = 30, *,
                 store: "str | None" = None, quipu: "str | None" = None,
                 prefix: "str | None" = None):
        # One store, no fan-out: extra br repos are br stores, not seeds.
        super().__init__(repo=repo, timeout=timeout, extra_repos=None)
        self.store = store or None
        self.quipu = quipu or None
        self.prefix = prefix or None
        self._proof: "StoreProof | None" = None

    @property
    def _bin(self) -> str:
        return os.environ.get("SHANTY_SD_BIN", "sd")

    def _location(self) -> list[str]:
        if self.store and self.quipu:
            raise StoreUnproven(
                "seeds backend: both SHANTY_SD_STORE and SHANTY_SD_QUIPU are set; "
                "name exactly one store")
        if self.store:
            return ["--store", self.store]
        if self.quipu:
            return ["--quipu", self.quipu]
        raise StoreUnproven(
            "seeds backend: no store configured (set SHANTY_SD_STORE or "
            "SHANTY_SD_QUIPU). Refusing: sd would fall back to a default local "
            "store and report an empty board.")

    def prove_store(self) -> StoreProof:
        """`sd where --json` for the configured store, or raise StoreUnproven.

        Run once per tracker (one st invocation) and before any other command,
        so every read this tracker returns, empty or not, sits behind it.
        """
        if self._proof is not None:
            return self._proof
        loc = self._location()
        named = loc[1]
        try:
            r = subprocess.run([self._bin, *loc, "where", "--json"], cwd=self.repo,
                               capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise StoreUnproven(f"seeds backend: `sd where` for {named} failed: {e}") from e
        if r.returncode != 0:
            raise StoreUnproven(f"seeds backend: `sd where` for {named} exited "
                                f"{r.returncode}: {_failure_reason(r)[:160]}")
        try:
            w = json.loads(r.stdout)
        except ValueError as e:
            raise StoreUnproven(f"seeds backend: `sd where` for {named} returned "
                                f"no JSON: {r.stdout[:120]!r}") from e
        source = w.get("location_source")
        expect_source = loc[0]
        if source != expect_source:
            raise StoreUnproven(
                f"seeds backend: sd resolved its store from {source!r}, not from "
                f"{expect_source} {named}; refusing to read an unproven store")
        if self.store and w.get("database_path") != os.path.abspath(self.store):
            raise StoreUnproven(
                f"seeds backend: sd reads {w.get('database_path')!r}, configured "
                f"store is {self.store!r}")
        if self.quipu and (w.get("quipu_url") or "").rstrip("/") != self.quipu.rstrip("/"):
            raise StoreUnproven(
                f"seeds backend: sd reads {w.get('quipu_url')!r}, configured "
                f"quipu is {self.quipu!r}")
        if self.prefix and w.get("prefix") != self.prefix:
            raise StoreUnproven(
                f"seeds backend: store {named} has prefix {w.get('prefix')!r}, "
                f"expected {self.prefix!r}")
        self._proof = StoreProof(location_source=source,
                                 database_path=w.get("database_path"),
                                 quipu_url=w.get("quipu_url"), prefix=w.get("prefix"))
        return self._proof

    @staticmethod
    def _translate(args: "tuple[str, ...]") -> "tuple[str, ...]":
        # br: `comments <id> [--json]`; sd: `comments list <id> [--json]`.
        if len(args) >= 2 and args[0] == "comments" and args[1] not in (
                "add", "list", "delete", "edit", "--help", "-h"):
            return ("comments", "list", *args[1:])
        return args

    def _bd_in(self, repo: "str | None", *args: str) -> subprocess.CompletedProcess:
        self.prove_store()
        cmd = [self._bin, *self._location(), *self._translate(args)]
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                              timeout=self.timeout)

    def _get_failure(self, item_id, result) -> str:
        where = self.store or self.quipu
        return (f"sd show {item_id} failed: {_failure_reason(result)} — "
                f"seeds store {where}")


def br_like_tracker(get, backend: "str | None" = None, *, repo: "str | None" = None,
                    timeout: "int | None" = None) -> BrTracker:
    """The tracker for a br-like backend, from a deployment-default getter.

    `get(key)` returns a SHANTY_* deployment value or None. `backend` overrides
    SHANTY_BACKEND (an explicit --backend). Every site that builds a br-like
    tracker goes through here, so `seeds` cannot reach one site and miss another.
    """
    from .beads import EXTRA_REPOS_KEY, parse_extra_repos
    backend = backend or get("SHANTY_BACKEND") or "files"
    repo = repo or get("SHANTY_BR_REPO") or get("SHANTY_BEADS_REPO")
    kw = {"timeout": timeout} if timeout is not None else {}
    if backend == "seeds":
        return SdTracker(repo=repo, store=get("SHANTY_SD_STORE"),
                         quipu=get("SHANTY_SD_QUIPU"), prefix=get("SHANTY_SD_PREFIX"),
                         **kw)
    return BrTracker(repo=repo, extra_repos=parse_extra_repos(get(EXTRA_REPOS_KEY)), **kw)
