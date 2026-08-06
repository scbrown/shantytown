"""beads — the first-class tracker.

Two functions. If this needs more, the tracker is driving the harness.

Deliberately shells out to `bd` rather than opening its own Dolt connection.
That is not laziness, it is the finding: `gt sling --dry-run` makes 63 sequential
Dolt connections during resolution and takes 51s, while `bd show` makes 3 and
takes 0.20s. Connections predict latency. The cheapest correct thing
is to make ONE `bd` call per operation and let bd own its pool.

If a future version opens its own connection, the budget test is what will catch
it — count the connections, don't hold a stopwatch.
"""
from __future__ import annotations
import json
import re
import subprocess
from pathlib import Path

from . import stores
from .inbox import is_message, is_unworkable
from .protocols import WorkItem


# The deployment key naming stores BEYOND the primary. One name, declared once,
# because three construction sites read it (cli._tracker, stop_event._plate_reader,
# untracked.plate_reader) and three copies of a parse is the duplicated constant
# this repo refuses everywhere else.
EXTRA_REPOS_KEY = "SHANTY_BEADS_REPOS_EXTRA"


def parse_extra_repos(value: "str | list | None") -> list[str]:
    """Parse the extra-store config into a list of paths.

    Accepts a JSON array or a separated string (os.pathsep, comma, or newline),
    because env.json carries JSON while an exported env var carries text and the
    same key has to survive both. Blank entries are dropped.

    NEVER raises on a malformed value — it returns []. That is deliberate and it
    is the conservative direction: a bad config here must degrade to TODAY'S
    single-store behaviour, not take down the plate reader for every agent on
    the fleet. A store you forgot to add is a bug someone notices; a stop-event
    drain that crashes fleet-wide because one path had a stray comma is an
    outage.
    """
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        parts = [str(v) for v in value]
    else:
        text = str(value).strip()
        if text.startswith("["):
            try:
                loaded = json.loads(text)
                parts = [str(v) for v in loaded] if isinstance(loaded, list) else []
            except json.JSONDecodeError:
                parts = []
        else:
            parts = re.split(r"[,\n" + re.escape(__import__("os").pathsep) + r"]", text)
    return [p.strip() for p in parts if p and p.strip()]


class BeadsTracker:
    # bd validates a title at <= 500 characters (measured 2026-07-20: a 500-char
    # title creates, 501 fails "title must be 500 characters or less"). Declared
    # here, on the concrete tracker, because the limit is bd's — not every Tracker
    # has one (FilesTracker does not). TrackerInbox reads it to refuse a too-long
    # message CLEANLY, before the store round-trip, instead of letting bd reject it
    # with a leaked validation string the caller then misreads as a store outage
    # (aegis-csuo).
    _TITLE_MAX = 500

    def __init__(self, repo: str | None = None, timeout: int = 30,
                 extra_repos: "list[str] | None" = None):
        # RESOLVED TO ABSOLUTE (GitHub #31). `repo` is handed to bd as `-C <dir>`,
        # so a RELATIVE value silently means "relative to whatever cwd invoked
        # st" — the same store read from the checkout and from an agent's
        # workspace are then two different stores, and the narrower read returns
        # a partial answer at exit 0. Resolve once, here, at the only place the
        # value enters the adapter.
        self.repo = str(Path(repo).expanduser().resolve()) if repo else None
        self.timeout = timeout
        # EXTRA STORES (aegis-qmfa1). An agent whose work lives in a repo's own
        # embedded store could never SELF-FEED: `hauls()` decides who is
        # self-feeding, and a self-feeding worker is EXCLUDED from the Rule Zero
        # feedable list — so an embedded-store agent got both halves wrong at
        # once. Its haul was invisible, so it never advanced; and it correctly
        # read as having no queue, so it landed back on the coordinator every
        # cycle. Measured: ellie required a hand `st go` for every single na item
        # while the rest of the fleet self-fed.
        #
        # Deliberately a LIST on the tracker and not a second tracker: `hauls()`
        # and `plate()` are already store-agnostic (they take rows), so the union
        # belongs at the one seam where rows are fetched. Everything above it is
        # unchanged.
        self.extra_repos = [
            str(Path(p).expanduser().resolve()) for p in (extra_repos or []) if p
        ]
        # Primary first: it is the tie-break for an id that somehow resolves in
        # two stores, and it keeps single-store behaviour bit-identical.
        self.repos = ([self.repo] if self.repo else []) + [
            p for p in self.extra_repos if p != self.repo
        ]

    def _bd_in(self, repo: "str | None", *args: str) -> subprocess.CompletedProcess:
        """One `bd` invocation against ONE store. The only place bd is spawned."""
        cmd = ["bd"]
        if repo:
            cmd += ["-C", repo]
        cmd += list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)

    def _bd(self, *args: str) -> subprocess.CompletedProcess:
        """The PRIMARY store. Unchanged: writes and single-store reads land here."""
        return self._bd_in(self.repo, *args)

    def _bd_for(self, item_id: str, *args: str) -> subprocess.CompletedProcess:
        """Run against whichever configured store actually holds `item_id`.

        Primary first, then extras, returning the first success. On a total miss
        the PRIMARY's failure is returned — so the error a caller sees still
        names the store it would have expected, rather than an arbitrary extra.

        The primary hop goes through `_bd`, NOT `_bd_in`, and that is load-bearing
        rather than stylistic: `_bd` is the seam every existing test and the
        conftest sentinel patch. Reaching past it to `_bd_in` made a test that
        fakes a failing `bd show` silently execute the REAL bd instead — caught by
        test_a_broken_enumeration_never_replaces_the_real_error, which passes on a
        clean tree and failed here. Single-store behaviour must be bit-identical,
        including which method a fake has to intercept.
        """
        first = self._bd(*args)
        if first.returncode == 0 or not self.extra_repos:
            return first
        for repo in self.extra_repos:
            r = self._bd_in(repo, *args)
            if r.returncode == 0:
                return r
        return first

    def get(self, item_id: str) -> WorkItem:
        r = self._bd_for(item_id, "show", item_id, "--json")
        if r.returncode != 0:
            # exit 2 territory: could not tell vs does not exist. Say which.
            #
            # ABSENCE WITH A NAMED BOUNDARY (aegis-81zyb). This used to report only
            # bd's stderr, so a miss read as "that id does not exist" when all it
            # ever meant was "not in the ONE store this tracker was pointed at" —
            # and there are 125 bd stores on this host. That gap is not academic:
            # an agent generalised a single-store miss into "it exists in no store"
            # and published it as confidence:extracted. So the store searched is
            # named, and the unsearched remainder is counted. The enumeration is
            # best-effort and never raises: a diagnostic that fails must not
            # replace the real error with its own.
            msg = f"bd show {item_id} failed: {r.stderr.strip()[:120]}"
            try:
                msg += f" — {stores.not_found_here(self.repo, item_id)}"
            except Exception:
                pass
            raise LookupError(msg)
        d = json.loads(r.stdout)
        if isinstance(d, list):
            d = d[0]
        return WorkItem(
            id=d.get("id", item_id),
            title=d.get("title", ""),
            status=d.get("status", "open"),
            assignee=d.get("assignee"),
            # bd's own numbering, 0 = highest. _priority never invents one: a
            # bead with no priority arrives as None so a governed dispatch can
            # say "nobody stated how important this is" instead of guessing.
            priority=_priority(d),
            # From the SAME `bd show --json` read — no extra round trip, so the
            # module's one-tracker-read budget is unchanged. Only `blocks`-type
            # deps count: a `relates-to` link is context, not a gate. Only
            # not-closed ones count: a closed blocker holds nothing.
            open_blockers=tuple(
                dep.get("id")
                for dep in (d.get("dependencies") or [])
                if dep.get("dependency_type") == "blocks"
                and (dep.get("status") or "") != "closed"
                and dep.get("id")
            ),
            # bd COUNTS every dependency row and LISTS only issue-targeted ones,
            # so the difference is exactly the rows this view does not render
            # (aegis-kt7jr). Measured on aegis-8y80: `dependency_count: 3`,
            # `len(dependencies): 1` — the two omitted rows include a `blocks`
            # edge to gt-9h8wbq8.
            #
            # gt-9h8wbq8 IS NOT UNRESOLVABLE, and this comment said it was.
            # Migration 0041 split `depends_on_id` into three typed columns and
            # classified it `depends_on_external` — recorded, typed, preserved.
            # A join for unresolvable ISSUE targets returns ZERO: there are no
            # dangling issue refs at all. So this is a serialisation difference
            # over a deliberate classification, NOT a dropped edge.
            #
            # ⚠ DO NOT "FIX" bd BY ADDING EXTERNAL DEPS TO THE `dependencies`
            # ARRAY. `open_blockers` above takes every blocks-type entry with an
            # id, so those rows would make `st go` REFUSE those beads — the
            # blocking that was explicitly ruled against — and `unreadable_deps`
            # below would go to 0, reporting ALL-CLEAR while detecting nothing.
            # Safe direction: make the COUNT agree with the LIST, or carry
            # external deps in a SEPARATE field. Never list-to-count.
            #
            # This costs NOTHING: both numbers are in the `bd show --json` this
            # method already parses, so the one-tracker-read budget is unchanged.
            # That matters — it is why the gap can be closed here rather than
            # filed against a second round trip. Every OTHER bd view is blind:
            # `bd dep list` omits the row and `bd dep tree` prints [READY].
            #
            # max(0, ...) because a tracker that reports fewer than it returns is
            # saying something we have no model for, and a NEGATIVE count would
            # render as a nonsense warning. Clamp, do not invent.
            unreadable_deps=max(
                0, int(d.get("dependency_count") or 0)
                - len(d.get("dependencies") or [])),
        )

    def create(self, title: str, **fields) -> WorkItem:
        """`bd create`. Returns the item, because the caller needs the new id.

        bd prints the id on stdout; we parse it rather than re-query, so create
        costs one bd invocation and not two. bd update is ~3.9s against bd show's
        0.18s — every avoided round trip is real money here.
        """
        args = ["create", title, "--json"]
        for k, v in fields.items():
            if v is None:
                continue
            args.append(f"--{k.replace('_', '-')}={v}")
        r = self._bd(*args)
        if r.returncode != 0:
            raise RuntimeError(f"bd create failed: {r.stderr.strip()[:120]}")
        try:
            d = json.loads(r.stdout)
            if isinstance(d, list):
                d = d[0]
            item_id = d.get("id", "")
        except json.JSONDecodeError:
            # bd's human output: "✓ Created issue: st-x1y2 — title"
            m = re.search(r"\b([a-z][a-z0-9_]*-[a-z0-9]+)\b", r.stdout)
            item_id = m.group(1) if m else ""
        if not item_id:
            # Never invent an id. A create that cannot name what it made did not
            # create anything the caller can use.
            raise RuntimeError(f"bd create gave no id: {r.stdout.strip()[:120]}")
        return WorkItem(id=item_id, title=title, status="open", assignee=fields.get("assignee"))

    def update(self, item_id: str, **fields) -> None:
        args = ["update", item_id]
        for k, v in fields.items():
            if v is None:
                continue
            args.append(f"--{k.replace('_', '-')}={v}")
        r = self._bd_for(item_id, *args)
        if r.returncode != 0:
            raise RuntimeError(f"bd update {item_id} failed: {r.stderr.strip()[:120]}")


def _priority(d: dict) -> int | None:
    """bd's `priority` field, or None if it did not say.

    NEVER a default. `int(d.get("priority", 2))` would be the obvious line and it
    is the bug: the usage governor refuses an item whose importance nobody
    stated, and a manufactured 2 would sail through a P2 floor on the strength of
    a value no human ever chose. A non-integer is also None — a value we cannot
    compare is a value we did not read.
    """
    v = d.get("priority")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


# Plate precedence, shared verbatim with files.plate so the two backends order a
# plate identically (the two-implementation equivalence). "In-hand"
# work outranks "not-started"; anything not listed (open, etc.) sorts last, then id.
_PLATE_RANK = {"hooked": 0, "in_progress": 1}


def rows(tracker: "BeadsTracker") -> list[dict]:
    """Every row across EVERY store the tracker is configured for, unioned.

    A MODULE FUNCTION, not a tracker method, and that is not cosmetic. The
    Tracker interface is pinned at get/update/create by test_swap, whose own
    words are the argument: "a shared contract widened by its owner is a
    decision; widened at 2am to unblock yourself it is a bug." I am not that
    contract's owner, and a fourth method here to make one command work is the
    precise defect that guard already caught once (mine()). So this follows the
    same shape arnold ruled for plate(): a per-backend reader taking the tracker,
    injected where it is needed.

    RAISES if any single store cannot be read, and never returns the partial
    union. A reader that quietly drops one store returns a SHORTER answer at
    exit 0 — indistinguishable from "that agent has no work", which is the exact
    bug this exists to fix. Half a haul is not a haul; it is a wrong haul that
    looks right.
    """
    extra = getattr(tracker, "extra_repos", None)
    if not extra:
        # BIT-IDENTICAL to the single-store path that came before, deliberately:
        # one bd call through the same seam, so every existing caller and fake
        # behaves exactly as it did.
        r = tracker._bd("list", "--json", "--limit", "0")
        if r.returncode != 0:
            raise RuntimeError(f"bd list failed: {r.stderr.strip()[:120]}")
        return json.loads(r.stdout) if r.stdout.strip() else []
    out: list[dict] = []
    for repo in tracker.repos:
        r = tracker._bd_in(repo, "list", "--json", "--limit", "0")
        if r.returncode != 0:
            raise RuntimeError(
                f"bd list failed for store {repo or '(default)'}: "
                f"{r.stderr.strip()[:120]}")
        out.extend(json.loads(r.stdout) if r.stdout.strip() else [])
    return out


def plate(tracker: "BeadsTracker", agent: str) -> "WorkItem | None":
    """The ONE thing on an agent's plate, or None. A module function, not a method.

    Pays the debt files.plate() names: prime against the beads tracker used to
    show an empty plate because only the files backend had a reader. Now both do.

    THE RULING (arnold): "what's on my plate" is NOT a third Tracker
    method — the two-function Tracker (get/update) is load-bearing; ellie's test
    and the BeadsTracker swap both depend on it, and malcolm's mine() broke both.
    It is a per-backend PLATE READER, injected into prime. malcolm's files
    implementation was the right pattern; this is its beads sibling.

    Returns AT MOST ONE item, by construction — cli.md: "one item, or none; a
    primer that prints a backlog is a dashboard." A function that cannot return
    two things cannot grow into a query API, which is what keeps the tracker from
    driving the harness. Ties broken deterministically (hooked before in_progress,
    then by id) so two runs agree.
    """
    # UNIONED across every configured store (aegis-qmfa1). list_rows() raises
    # rather than returning a partial union, preserving this reader's rule that
    # could-not-look must never render as empty-plate.
    rows_ = rows(tracker)
    mine = [
        x for x in rows_
        if x.get("assignee") in (agent, agent.split("/")[-1])
        and x.get("status") != "closed"
        # A MESSAGE is not work (inbox.py). On THIS backend the exclusion is not
        # structural — a tracker-backed inbox lives on the same store by design —
        # so the marker is the mechanism, and it is the same predicate files.plate
        # uses. Measured need: `st mail -d` items titled "mail: ..." are open and
        # assigned on the live aegis store today, i.e. sitting on real plates.
        and not is_message(x.get("title", ""))
        # BLOCKED and DEFERRED are not workable, so serving either cycles the
        # agent: it burns a turn discovering that, stops, and the next stop hands
        # it back. Measured 2026-08-02: 16 blocked beads, all 16 assigned, all
        # served. Measured 2026-08-04: 41 deferred, 38 assigned, and `st anchor
        # ellie` served a deferred bead over eight workable ones. ONE composed
        # predicate, shared with files.plate, so a third such status cannot land
        # on one backend and not the other.
        and not is_unworkable(x.get("status"))
    ]
    if not mine:
        return None
    # OPEN-ASSIGNED belongs on the plate (malcolm): returning None
    # while 3 beads are assigned to you reports "nothing" when it means "nothing
    # STARTED" — the same silent-degradation class this reader is built to refuse.
    # This used to filter to hooked/in_progress and DIVERGED from files.plate's
    # "not closed"; the two-implementation rule exists to catch exactly that. Now
    # both include not-closed, with one shared precedence: in-hand outranks
    # not-started, then lowest id (deterministic across runs and across backends).
    mine.sort(key=lambda x: (_PLATE_RANK.get(x.get("status"), 2), x.get("id", "")))
    top = mine[0]
    return WorkItem(
        id=top.get("id", ""),
        title=top.get("title", ""),
        status=top.get("status", "open"),
        assignee=top.get("assignee"),
        priority=_priority(top),
    )


def items(tracker: "BeadsTracker") -> list[WorkItem]:
    """EVERY item on the store — the beads sibling of files.items, injected into
    TrackerInbox. ONE `bd` call (the connection budget is the test, not a
    stopwatch — see this module's docstring), and it RAISES rather than returning
    [] when bd could not answer: an inbox that reports "no messages" because it
    could not look is the could-not-tell-rendered-as-fine bug this repo names in
    every other reader.

    DELIBERATELY NOT UNIONED across extra stores, unlike plate() (aegis-qmfa1).
    The two answer different questions: a plate asks "what work is mine", which
    genuinely spans stores; an inbox asks "what was DELIVERED to me", and
    delivery has one destination by construction — `st inbox -d` writes to the
    configured repo and nowhere else. Unioning here would spend one bd call per
    extra store, against this module's stated connection budget, to look for
    messages that cannot be there. Revisit only if delivery itself gains a
    second destination."""
    r = tracker._bd("list", "--json", "--limit", "0")
    if r.returncode != 0:
        raise RuntimeError(f"bd list failed: {r.stderr.strip()[:120]}")
    rows = json.loads(r.stdout) if r.stdout.strip() else []
    return [WorkItem(id=x.get("id", ""), title=x.get("title", ""),
                     status=x.get("status", "open"), assignee=x.get("assignee"),
                     priority=_priority(x))
            for x in rows]
