"""cycle — clearing an agent's context WITHOUT destroying its runtime.

`/clear` is the wrong primitive and the fleet has paid for it repeatedly. Three
facts, each measured (aegis-3laza):

1. **An agent cannot clear itself.** `/clear` is user-invoked. The agent with the
   best signal about its own degradation has the least power to act on it, so it
   stalls holding live work and waits for a human to notice. Two agents stood by
   on exactly that in one session.
2. **`/clear` LOSES THE RUNTIME.** It drops the session out of bypass into MANUAL.
   Measured on malcolm: clearing a saturated agent fixed the context and created a
   second blocker, and `st crew` then correctly reported it not-reliably-
   dispatchable. The remedy needed its own remedy.
3. **The depth signal that should trigger it is unreliable in the flattering
   direction** — `st crew` showed one agent clean at 6.0h and 400k+, and reported
   `ok` for another that was self-reporting saturation.

The sequence that DOES work was found by hand five times in one session:

    st stop <agent> --reason '<checkpoint>'
    st new <agent>

`st new` restores in one step what `/clear` destroys — bypass permissions, the MCP
kit, skills, journaling wiring, and it verifies stop hooks on the live process.
This module makes that a verb instead of tribal knowledge held by whoever last
debugged it.

WHAT THIS MODULE IS AND IS NOT. It is the POLICY — may this agent be cycled, and
what would be lost. It performs no tmux mutation and spawns no process; the CLI
composes it with the existing `stop` and `_launch` seams. That split is deliberate:
the guard is the part that must be testable without a live fleet, because it is the
part whose failure destroys work.
"""
from __future__ import annotations
import fnmatch
import json
import posixpath
import time
from dataclasses import dataclass, field
from pathlib import Path


# The stop reason prefix that marks a deliberate cycle, so a drain can tell one
# from a crash or a retirement. `st tend` matches on it.
CYCLE_REASON = "cycle-requested"


# Untracked names that may carry a credential. Deliberately OVER-broad: a false
# positive costs one line of output, a false negative costs a live bearer token in
# public history. This list does not decide anything — nothing is ever blocked or
# hidden on it — it only chooses which line gets the "do NOT add this" marker.
SECRET_NAME_PATTERNS = (
    # `*mcp.json*`, not `.mcp.json*`: the leading dot is not reliably there. The
    # file that started this was `.mcp.json.bak-mcpfix` at a clone root, but the
    # same kit gets copied to `mcp.json.bak` and dropped inside an untracked
    # directory, where the basename has no dot at all.
    "*mcp.json*", "*.env", ".env*", "*token*", "*secret*", "*credential*",
    "*.pem", "*.key", "id_rsa*", "id_ed25519*", ".netrc", "*.p12", "*.kdbx",
)


def looks_secret(path: str) -> str:
    """The pattern `path` matches, or "". Matched on the BASENAME and on the whole
    path, so `.playwright-mcp/mcp.json.bak` is caught as well as a stray at the
    root, and case-insensitively, because `.MCP.json.BAK` leaks exactly as well."""
    whole = path.strip("/").lower()
    name = posixpath.basename(whole)
    for pattern in SECRET_NAME_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(whole, pattern):
            return pattern
    return ""


@dataclass
class TreeUntracked:
    """Untracked files in one tree — REPORTED, never blocking, and never with an
    instruction to commit them (aegis-4hwpdb).

    This is a separate type from TreeRisk on purpose. A cycle stops the session
    and relaunches into the SAME clone, so untracked files are not touched by it
    and are at risk from nothing it does; folding them into TreeRisk is what made
    the guard refuse on strays. The refusal was survivable. Its stated remedy was
    not: "commit and `st push` first" answered with `git add .` commits whatever
    the stray happened to be, and on the night this was filed one agent's stray
    was a `.mcp.json.bak-mcpfix` holding a live bearer token (aegis-3v10dt) — the
    guard's own exit path was the leak.

    So this type may state a count and name files, and may say what NOT to do
    with them. It has no wording for committing them at all.
    """
    path: str
    files: list = field(default_factory=list)
    total: int = 0

    #: How many ordinary files to name before summarising. Every secret-pattern
    #: match is named regardless of this cap — the cap exists to keep an
    #: untracked build directory from burying the one line that matters.
    SAMPLE = 6

    def secrets(self) -> list:
        """[(path, pattern)] for the files whose NAME says do not commit them."""
        out = []
        for f in self.files:
            pattern = looks_secret(f)
            if pattern:
                out.append((f, pattern))
        return out

    def render(self) -> list:
        """Lines for the report. Plural, because the secret markers each need one."""
        secrets = self.secrets()
        head = (f"{self.path}: {self.total} untracked file(s) — NOT blocking; a "
                f"cycle relaunches this same clone and leaves them untouched")
        lines = [head]
        for f, pattern in secrets:
            lines.append(f"    {f}  ⚠ do NOT `git add` this — it matches "
                         f"{pattern} and may hold a live credential")
        plain = [f for f in self.files if not looks_secret(f)]
        for f in plain[:self.SAMPLE]:
            lines.append(f"    {f}")
        hidden = self.total - len(secrets) - min(len(plain), self.SAMPLE)
        if hidden > 0:
            lines.append(f"    (+{hidden} more)")
        return lines


@dataclass
class TreeRisk:
    """One tree that would lose work, and WHICH KIND — they are not the same risk.

    `dirty` is uncommitted: it dies with the session and exists nowhere else.
    `unpushed` is committed but on no remote ref known locally: it survives the
    cycle on disk, so it is the weaker of the two — but a cycle is exactly when a
    tree stops being looked at, and this fleet has already lost commits that way.
    """
    path: str
    dirty: bool = False
    unpushed: int = 0
    note: str = ""

    def render(self) -> str:
        bits = []
        if self.dirty:
            bits.append("uncommitted changes")
        if self.unpushed:
            bits.append(f"{self.unpushed} commit(s) on no remote ref")
        detail = " and ".join(bits) or "unreadable"
        return f"{self.path}: {detail}{(' — ' + self.note) if self.note else ''}"


@dataclass
class Verdict:
    """May we cycle, and what does the operator need to know first."""
    agent: str
    ok: bool
    reason: str = ""
    risks: list = field(default_factory=list)
    checkpoint: str = ""
    #: Untracked files found while judging. NOT risks and never a reason to
    #: refuse — see TreeUntracked. Carried on the verdict so the report is the
    #: same whether the cycle went ahead or was refused for something else.
    untracked: list = field(default_factory=list)

    def notice_lines(self) -> list:
        """The non-blocking untracked report, or []."""
        lines = []
        for u in self.untracked:
            lines += u.render()
        return lines

    def render(self) -> str:
        notice = self.notice_lines()
        if self.ok:
            lines = [f"{self.agent}: safe to cycle"]
        else:
            lines = [f"{self.agent}: REFUSED — {self.reason}"]
            lines += [f"    {r.render()}" for r in self.risks]
        lines += [f"    {n}" for n in notice]
        return "\n".join(lines)


def assess(agent: str, trees, checkpoint: str, staleness,
           allow_loss: bool = False) -> Verdict:
    """Decide whether `agent` may be cycled now.

    `trees` are the paths this agent could hold work in — its crew clone and every
    per-agent worktree. `staleness` is injected (workspace.tree_staleness) so this
    is testable without a git tree; the CLI passes fetch=True.

    TWO GATES, IN THIS ORDER.

    **The checkpoint gate is first and it is not a formality.** A cycle destroys
    exactly one thing: context that was never written down. Everything else —
    bypass, the MCP kit, skills, the plate — the relaunch restores. So the one
    precondition worth enforcing is that the agent said what it was mid-way
    through. Refusing here costs a sentence; not refusing costs the reason the
    next session cannot pick the work up.

    **The loss gate is second**, and it FETCHES before judging. This is the trap
    the bead calls out by name: a tree read against a stale remote-tracking ref
    reports commits as stranded when they are already on origin, and a cycle verb
    that refuses on a phantom will be routed around and then trusted by nobody.
    workspace.tree_staleness(fetch=True) already fetches with --prune, which also
    closes the opposite and worse error — a deleted upstream ref laundering an
    orphaned commit into "safe".

    **UNTRACKED FILES ARE NOT A GATE AT ALL** (aegis-4hwpdb). They were, until
    2026-09-03, because `Staleness.dirty` folded them in with tracked
    modifications. Three cycles were refused in one night on strays — a
    `.playwright-mcp/` directory, a stale png, a `server.pid` — and the refusal
    handed each agent "commit and `st push` first" as the way out. An agent that
    does that with `git add .` commits the stray, and one of those strays was a
    `.mcp.json.bak-mcpfix` carrying a live bearer token. A guard whose exit path
    is "commit everything" is a leak mechanism when the dirt is a secret backup.
    They are REPORTED instead, as a `TreeUntracked` notice that names anything
    matching a credential pattern and never mentions committing.

    `allow_loss` is a SEPARATE, NAMED override and deliberately not folded into a
    general --force. arnold's ruling on the roles guard (aegis-ftmfn) is the
    precedent and the argument is the same: when --force is the only gate, the
    flag people reach for to get past an unrelated nuisance also silently disarms
    the guard that was protecting their work.
    """
    if not checkpoint.strip():
        return Verdict(
            agent, False,
            "no checkpoint. A cycle destroys unwritten context and nothing else — "
            "say what this agent is mid-way through, in its own words, and that "
            "loss goes to zero. Pass --reason '<what you are mid-task on, "
            "decisions already made, the exact next step>'.")

    risks: list[TreeRisk] = []
    notices: list[TreeUntracked] = []
    for tree in trees:
        s = staleness(tree)
        if getattr(s, "error", None):
            # CANNOT TELL IS NOT CLEAN — the same rule tree_staleness itself keeps
            # for a failed `git status`. A tree we could not read is a tree that
            # might hold the only copy of something, and this guard exists for
            # precisely that case.
            risks.append(TreeRisk(str(tree), note=f"could not read: {s.error}"))
            continue
        # getattr, not attribute access: `staleness` is an injected callable and
        # older stand-ins predate these fields. A reporting nicety may not be the
        # thing that makes the loss gate raise.
        count = int(getattr(s, "untracked_count", 0) or 0)
        if count:
            notices.append(TreeUntracked(
                str(tree), files=list(getattr(s, "untracked", ()) or ()),
                total=count))
        if s.dirty or s.unpushed:
            risks.append(TreeRisk(str(tree), dirty=s.dirty, unpushed=s.unpushed))

    if risks and not allow_loss:
        return Verdict(
            agent, False,
            "work would be lost or stranded. Commit and `st push` first, or pass "
            "--allow-loss if you have decided it is expendable (NOT --dry-run and "
            "NOT a general --force: this override is named on its own so that "
            "reaching past some other refusal cannot disarm it).",
            risks=risks, checkpoint=checkpoint, untracked=notices)

    return Verdict(agent, True, risks=risks, checkpoint=checkpoint,
                   untracked=notices)


def _parse_ts(value):
    """An ISO-8601 timestamp, or None. `Z` accepted (br writes it)."""
    from datetime import datetime
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def checkpoint_since(comments, who: str, since) -> bool:
    """Has `who` written anything on this bead since `since`?

    THE ONE PREDICATE, shared by the two mechanisms that need it — the codex-side
    `st cycle` gate below and the Claude-side PreCompact hook (precompact.py).
    Two spellings of "is there a checkpoint" would be two answers, and the one
    that decides is whichever ran; keeping it here also means the Claude hook
    borrows the harness-neutral policy rather than inventing a parallel one.

    ANY comment by the agent counts, not only a marked one. The directive
    (Stiwi 2026-09-03) asks for a handoff written by the agent; a gate that
    accepted only its own machine-written marker would refuse the exact behaviour
    it exists to produce.

    `since` unparseable or absent -> False = "no checkpoint found". The two
    callers take that in OPPOSITE directions on purpose, and both are right:
    the hook WRITES one (a duplicate costs a comment), the gate REFUSES with the
    remedy printed (an unnecessary refusal costs a sentence). Neither silently
    proceeds as though a checkpoint had been seen.
    """
    floor = _parse_ts(since)
    if floor is None:
        return False
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        if who and (c.get("author") or "") != who:
            continue
        at = _parse_ts(c.get("created_at"))
        if at is not None and at >= floor:
            return True
    return False


@dataclass
class DurableGate:
    """Is there a DURABLE handoff on the held bead, written since the last
    relaunch? (aegis-902vnu, Stiwi 2026-09-03: "you should be handing off before
    compaction same with all st agents".)

    Separate from `assess`'s checkpoint gate, and the difference is the whole
    point. `assess` requires a checkpoint STRING — a `--reason` line, which dies
    with the operator's terminal. This requires a comment ON THE BEAD, which is
    what a fresh session can actually read. The reason line was never intended to
    be the handoff; it is the stop record's label.

    THREE STATES. `ok=True` (a checkpoint is there), `ok=False` (there is none —
    refuse and say how), and `ok=None` = COULD NOT TELL, which does NOT refuse.
    A tracker that will not answer must not be able to strand a saturated agent:
    an agent that cannot cycle keeps filling, and the failure this bead is about
    is precisely context that fills past a boundary. Cannot-tell is reported out
    loud instead — the launched.py rule, one module over.
    """
    agent: str
    bead: str = ""
    since: str = ""
    ok: "bool | None" = None
    note: str = ""

    def render(self) -> str:
        if self.ok:
            return f"{self.agent}: durable checkpoint on {self.bead} ✓"
        if self.ok is None:
            return f"{self.agent}: durable checkpoint COULD NOT BE CHECKED — {self.note}"
        return (
            f"no durable handoff on {self.bead} since this session launched "
            f"({self.since or 'unknown'}). The --reason line dies with this "
            f"terminal; the next session reads the BEAD. Write one first:\n"
            f"    br comments add {self.bead} --file <notes>\n"
            f"  (state, landed-vs-local, exact next step, rollback). Or "
            f"`st cycle --self --checkpoint-file <notes>`, which posts it for "
            f"you. --allow-loss overrides, and spends the reasoning.")


def durable_gate(agent: str, bead: str, since, comments, error: str = "") -> DurableGate:
    """The policy half — no I/O, so it is testable without a fleet or a store.

    The CLI supplies `comments` (br.comments) and `since` (the launch stamp's
    mtime); an `error` from either read produces the could-not-tell verdict
    rather than a refusal.
    """
    if error:
        return DurableGate(agent, bead, str(since or ""), None, error)
    if not bead:
        return DurableGate(agent, bead, str(since or ""), None,
                           "no held bead — nothing to checkpoint onto")
    if not since:
        return DurableGate(agent, bead, "", None,
                           "no launch stamp — cannot date 'since the last relaunch'")
    ok = checkpoint_since(comments, agent, since)
    return DurableGate(agent, bead, str(since), ok, "")


class Requests:
    """Durable cycle REQUESTS — the `--self` half, and the important one.

    An agent cannot cycle itself in-process: the stop kills the session, which
    kills the `st` invocation doing the stopping. So `--self` cannot BE a cycle; it
    can only be a request that something outside the session honours. `st tend`
    is that something, and it already runs on a timer.

    This removes three of the five measured failures without touching the hard
    measurement problem at all — the agent that KNOWS it is degrading gets a way to
    say so that does not require a coordinator to be watching.

    A JSON map agent -> checkpoint record. Not a queue: a second request from the same
    agent REPLACES the first, because the newer checkpoint is the better one and a
    backlog of stale self-reports is worse than none.
    """

    def __init__(self, root):
        self.path = Path(root) / "notify" / "cycle-requests.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            # NEVER raises. A malformed ledger must degrade to "no requests
            # pending", not wedge the supervisor for the whole fleet — the same
            # conservative direction beads.parse_extra_repos takes.
            return {}

    def _save(self, data: dict) -> None:
        from .files import write_json_atomic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, data)

    def request(self, agent: str, checkpoint: str, checkpoint_bead: str = "",
                quipu_nodes: list | None = None) -> None:
        data = self._load()
        data[agent] = {"checkpoint": checkpoint,
                       "checkpoint_bead": checkpoint_bead,
                       # aegis-x6yoq: graph references carried across the cycle, so
                       # the fresh session queries quipu instead of re-deriving the
                       # context it just shed. Always a list, never absent, so the
                       # resume path never has to branch on presence.
                       "quipu_nodes": list(quipu_nodes or []),
                       # aegis-7xptd5: a NEW request re-arms. The old refusal
                       # described a tree state the agent has since had a chance to
                       # fix, and carrying it forward would report a stall that may
                       # already be resolved — the same cannot-tell-read-as-fact
                       # error in the other direction.
                       "refused": None}
        self._save(data)

    def mark_refused(self, agent: str, reason: str, risks=()) -> bool:
        """Record WHY a pending cycle did not happen (aegis-7xptd5).

        Until this existed, a refused request and a cycle in flight were the same
        observable: both are simply "a record in this file", and `st crew` printed
        `cycling` for each. Measured 2026-09-01: six agents sat refused on dirty
        trees for over an hour while the summary read "6 planned context cycle(s)"
        and a coordinator read that as progress.

        NO-OP WITHOUT A PENDING REQUEST, and that is the point rather than a
        convenience: an operator's ad-hoc `st cycle <agent>` on an agent that never
        asked must not mint a request record. Only a refusal of something already
        pending is a stall. Returns whether anything was recorded.
        """
        data = self._load()
        record = data.get(agent)
        if not isinstance(record, dict):
            # Bare-string records predate the dict form. Upgrade in place rather
            # than refusing to annotate them — a legacy request can stall too.
            if agent not in data:
                return False
            record = {"checkpoint": str(data[agent]), "checkpoint_bead": "",
                      "quipu_nodes": []}
        paths = [getattr(r, "path", "") or "" for r in risks]
        record["refused"] = {"reason": reason,
                             "paths": [p for p in paths if p],
                             "at": time.time()}
        data[agent] = record
        self._save(data)
        return True

    def pending(self) -> dict:
        # Old string entries remain readable after the record upgrade.
        def norm(value):
            # Records predating the quipu_nodes field, and the even older bare
            # strings, must both keep reading. A handoff record is the LAST thing
            # that may break on an upgrade: it is read exactly when an agent has
            # already shed the context needed to reconstruct it.
            d = (value if isinstance(value, dict)
                 else {"checkpoint": str(value), "checkpoint_bead": ""})
            d.setdefault("checkpoint_bead", "")
            d.setdefault("quipu_nodes", [])
            # None, never absent — so every reader tests one thing (is there a
            # refusal?) and none of them has to branch on the record's vintage.
            d.setdefault("refused", None)
            return d
        return {agent: norm(value) for agent, value in self._load().items()}

    def clear(self, agent: str) -> None:
        """Drop a request. Called AFTER the cycle is performed, never before — a
        request cleared on intent rather than on completion is a request that
        vanishes when the relaunch refuses, and the agent waits forever for a
        cycle nobody is going to do."""
        data = self._load()
        if data.pop(agent, None) is not None:
            self._save(data)


def refusal_summary(record) -> tuple[str, str]:
    """(first blocking path, reason) for a pending request, or ("", "") if none.

    FIRST path, not all of them: `st crew` is a one-line-per-agent table, and an
    agent with eight dirty trees would otherwise wrap the roster. The first is
    enough to act on — the operator runs `st cycle <agent>` for the full list, and
    the refusal itself already names every one.
    """
    refused = (record or {}).get("refused") or {}
    if not refused:
        return "", ""
    paths = refused.get("paths") or []
    return (paths[0] if paths else ""), refused.get("reason", "")


def requires_checkpoint_bead(role: str, checkpoint_bead: str) -> bool:
    """Whether this cycle must name a durable bead checkpoint.

    An administrator is the sole coordinator tier during a handoff.  Free text
    cannot restore that responsibility after a relaunch, so its bead pointer is
    mandatory.  Other roles keep the compatible request form while the fleet
    migrates to named checkpoints.
    """
    return role == "administrator" and not checkpoint_bead.strip()
