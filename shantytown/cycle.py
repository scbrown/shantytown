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
import json
import time
from dataclasses import dataclass, field
from pathlib import Path


# The stop reason prefix that marks a deliberate cycle, so a drain can tell one
# from a crash or a retirement. `st tend` matches on it.
CYCLE_REASON = "cycle-requested"


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

    def render(self) -> str:
        if self.ok:
            return f"{self.agent}: safe to cycle"
        lines = [f"{self.agent}: REFUSED — {self.reason}"]
        lines += [f"    {r.render()}" for r in self.risks]
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
    for tree in trees:
        s = staleness(tree)
        if getattr(s, "error", None):
            # CANNOT TELL IS NOT CLEAN — the same rule tree_staleness itself keeps
            # for a failed `git status`. A tree we could not read is a tree that
            # might hold the only copy of something, and this guard exists for
            # precisely that case.
            risks.append(TreeRisk(str(tree), note=f"could not read: {s.error}"))
            continue
        if s.dirty or s.unpushed:
            risks.append(TreeRisk(str(tree), dirty=s.dirty, unpushed=s.unpushed))

    if risks and not allow_loss:
        return Verdict(
            agent, False,
            "work would be lost or stranded. Commit and `st push` first, or pass "
            "--allow-loss if you have decided it is expendable (NOT --dry-run and "
            "NOT a general --force: this override is named on its own so that "
            "reaching past some other refusal cannot disarm it).",
            risks=risks, checkpoint=checkpoint)

    return Verdict(agent, True, risks=risks, checkpoint=checkpoint)


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
