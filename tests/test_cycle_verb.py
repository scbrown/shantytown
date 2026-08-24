"""`st cycle` — the guard that decides whether clearing an agent is safe (aegis-3laza).

SIBLING FILE, NOT A REPLACEMENT. tests/test_cycle.py pins the automatic driver
(notify.CycleDriver) that prompts a saturated agent on its own pane. This file
pins the VERB: the policy behind `st cycle <agent>`, which stops and relaunches
instead of asking the agent to `/clear`.

The verb exists because `/clear` is the wrong primitive three ways over, each
measured in one session: an agent cannot invoke it on itself, it drops the session
out of bypass into MANUAL, and the depth signal that should trigger it read `ok`
for an agent that was self-reporting saturation.

These tests pin the POLICY, not the tmux — the policy is the half whose failure
destroys work, and the half that must be checkable without a live fleet.

The refusal directions are asymmetric on purpose: refusing a safe cycle costs a
sentence, allowing an unsafe one costs the only copy of somebody's work. But a
guard that cries wolf gets routed around and then protects nothing, which is why
the stale-ref case below is a test and not a footnote.
"""
from __future__ import annotations
import json
from dataclasses import dataclass

from shantytown.cycle import CYCLE_REASON, Requests, assess


@dataclass
class _Stale:
    """Stands in for workspace.Staleness — the three fields the guard reads."""
    dirty: bool = False
    unpushed: int = 0
    error: str | None = None


def _clean(_tree):
    return _Stale()


CHECKPOINT = "mid-way through the read-back fix; budget test still to update"


# --- the checkpoint gate ------------------------------------------------------

def test_a_cycle_without_a_checkpoint_is_refused():
    """THE ONE THING A CYCLE DESTROYS is context nobody wrote down. Everything else
    the relaunch restores, so this is the only precondition worth enforcing."""
    v = assess("malcolm", ["/tmp/x"], "", _clean)
    assert not v.ok
    assert "no checkpoint" in v.reason
    # The refusal must say what a good checkpoint CONTAINS. "Provide a reason"
    # produces "cycling", which is not a handoff.
    assert "next step" in v.reason


def test_whitespace_is_not_a_checkpoint():
    """The cheapest way past a required field is the space bar."""
    assert not assess("malcolm", ["/tmp/x"], "   \n\t ", _clean).ok


def test_the_checkpoint_rides_the_verdict():
    """It becomes the stop reason — the only durable record that this shutdown was
    deliberate. A cycle indistinguishable from a crash is how a deliberate stop
    silently became permanent (aegis-k9068)."""
    v = assess("malcolm", [], CHECKPOINT, _clean)
    assert v.ok and v.checkpoint == CHECKPOINT


# --- the loss gate ------------------------------------------------------------

def test_uncommitted_work_refuses_the_cycle():
    """Dirty is the strong case: it dies with the session and exists nowhere else."""
    v = assess("ellie", ["/w/ellie"], CHECKPOINT, lambda t: _Stale(dirty=True))
    assert not v.ok
    assert "would be lost" in v.reason
    assert "/w/ellie" in v.render() and "uncommitted" in v.render()


def test_unpushed_commits_refuse_the_cycle_and_are_counted():
    """Weaker than dirty — it survives on disk — but a cycle is exactly when a tree
    stops being looked at."""
    v = assess("ellie", ["/w/ellie"], CHECKPOINT, lambda t: _Stale(unpushed=3))
    assert not v.ok
    assert "3 commit(s) on no remote ref" in v.render()


def test_every_tree_is_checked_not_just_the_first():
    """An agent holds work in its crew clone AND every per-agent worktree. A guard
    that stops at the first clean tree misses the one with the work in it."""
    trees = ["/w/a", "/w/b", "/w/c"]
    seen = []

    def look(t):
        seen.append(t)
        return _Stale(dirty=(t == "/w/c"))

    v = assess("ellie", trees, CHECKPOINT, look)
    assert seen == trees, "the guard stopped early"
    assert not v.ok and len(v.risks) == 1 and v.risks[0].path == "/w/c"


def test_an_unreadable_tree_is_a_risk_not_a_pass():
    """CANNOT TELL IS NOT CLEAN — the same rule tree_staleness keeps for a failed
    `git status`. A tree we could not read might hold the only copy of something,
    which is the case this guard exists for."""
    v = assess("ellie", ["/w/ellie"], CHECKPOINT,
               lambda t: _Stale(error="not a git repository"))
    assert not v.ok
    assert "could not read" in v.render()


def test_a_clean_agent_with_a_checkpoint_may_cycle():
    """The common case has to actually pass, or the verb gets routed around."""
    v = assess("ellie", ["/w/ellie", "/w/ellie-wt"], CHECKPOINT, _clean)
    assert v.ok and v.risks == []
    assert "safe to cycle" in v.render()


def test_the_guard_does_not_manufacture_a_risk_the_measurement_did_not_find():
    """THE TRAP THE BEAD NAMES. A tree read against a STALE remote-tracking ref
    reports commits as stranded when they are already on origin — one agent showed
    '1 unpushed' for a commit that was already on origin/main.

    The fix is NOT a second heuristic here. workspace.tree_staleness(fetch=True)
    already fetches with --prune, which also closes the opposite and worse error: a
    DELETED upstream ref laundering an orphaned commit into 'safe'. So the guard
    takes the reading it is given and the CLI passes fetch=True. A guard that
    invented its own answer would disagree with `st crew` and `st doctor`, and
    three instruments giving three numbers is how a fleet learns to ignore all
    three."""
    v = assess("grant", ["/w/grant"], CHECKPOINT, _clean)
    assert v.ok


def test_allow_loss_is_a_separate_named_override():
    """NOT folded into a general --force, following arnold's ruling on the roles
    guard (aegis-ftmfn): when --force is the only gate, the flag someone reaches
    for to get past an unrelated nuisance also disarms the guard protecting their
    work.

    The risks are still REPORTED when overridden — proceeding is a decision, and a
    decision made without seeing its cost is not one."""
    def look(_t):
        return _Stale(dirty=True)

    assert not assess("ellie", ["/w/e"], CHECKPOINT, look).ok
    v = assess("ellie", ["/w/e"], CHECKPOINT, look, allow_loss=True)
    assert v.ok
    assert v.risks and v.risks[0].dirty, "an override must not hide what it overrode"


def test_the_refusal_names_the_override_without_teaching_force():
    """The message must not teach the habit it exists to prevent."""
    r = assess("ellie", ["/w/e"], CHECKPOINT, lambda t: _Stale(dirty=True)).reason
    assert "--allow-loss" in r
    assert "NOT a general --force" in r


def test_the_checkpoint_gate_runs_before_the_loss_gate():
    """Order matters for the message, not just the outcome: an agent with BOTH
    problems should be told to write its checkpoint first, because that is the
    thing only it can do. The tree can be pushed by anyone."""
    seen = []

    def look(t):
        seen.append(t)
        return _Stale(dirty=True)

    v = assess("ellie", ["/w/e"], "", look)
    assert not v.ok and "no checkpoint" in v.reason
    assert seen == [], "the loss gate ran before the cheaper checkpoint gate"


# --- the --self request path --------------------------------------------------

def test_a_self_request_is_durable(tmp_path):
    """An agent CANNOT cycle itself in-process: the stop kills the session, which
    kills the `st` doing the stopping. So --self can only be a request that
    something outside the session honours."""
    r = Requests(tmp_path)
    assert r.pending() == {}
    r.request("gennaro", CHECKPOINT)
    assert Requests(tmp_path).pending() == {
        "gennaro": {"checkpoint": CHECKPOINT, "checkpoint_bead": ""}}


def test_a_second_request_replaces_the_first(tmp_path):
    """Not a queue. The newer checkpoint is the better one, and a backlog of stale
    self-reports is worse than none."""
    r = Requests(tmp_path)
    r.request("gennaro", "old")
    r.request("gennaro", "newer and more accurate")
    assert r.pending() == {"gennaro": {
        "checkpoint": "newer and more accurate", "checkpoint_bead": ""}}


def test_a_request_is_cleared_only_after_the_cycle(tmp_path):
    """Cleared on COMPLETION, never on intent. A request dropped when the cycle is
    merely attempted vanishes if the relaunch refuses, and the agent waits forever
    for a cycle nobody is going to perform."""
    r = Requests(tmp_path)
    r.request("gennaro", CHECKPOINT)
    r.clear("malcolm")                       # someone else's — must not touch it
    assert r.pending() == {
        "gennaro": {"checkpoint": CHECKPOINT, "checkpoint_bead": ""}}
    r.clear("gennaro")
    assert r.pending() == {}


def test_a_malformed_ledger_reads_as_no_requests_not_as_a_crash(tmp_path):
    """A broken ledger must degrade to 'nothing pending', not wedge the supervisor
    for the whole fleet — the conservative direction beads.parse_extra_repos takes,
    for the same reason: one stray byte must not take down every agent."""
    r = Requests(tmp_path)
    r.path.parent.mkdir(parents=True, exist_ok=True)
    r.path.write_text("{not json at all")
    assert r.pending() == {}
    r.request("gennaro", CHECKPOINT)         # and it recovers on the next write
    assert r.pending() == {
        "gennaro": {"checkpoint": CHECKPOINT, "checkpoint_bead": ""}}


def test_a_json_non_object_also_reads_as_empty(tmp_path):
    """`[1,2,3]` parses fine and is not a map. Valid JSON of the wrong SHAPE is the
    variant a bare try/except around json.loads does not catch."""
    r = Requests(tmp_path)
    r.path.parent.mkdir(parents=True, exist_ok=True)
    r.path.write_text("[1, 2, 3]")
    assert r.pending() == {}


def test_the_cycle_reason_is_a_stable_marker():
    """`st tend` and any drain match on this to tell a deliberate cycle from a
    crash or a retirement. A constant precisely so two spellings cannot drift
    apart in two files."""
    assert CYCLE_REASON == "cycle-requested"


def test_requests_are_written_atomically(tmp_path):
    """The supervisor reads this on a timer while agents write it. A torn write
    reads as a malformed ledger — recoverable, but it would silently drop every
    pending request in the file."""
    r = Requests(tmp_path)
    r.request("a", "one")
    r.request("b", "two")
    assert json.loads(r.path.read_text()) == {
        "a": {"checkpoint": "one", "checkpoint_bead": ""},
        "b": {"checkpoint": "two", "checkpoint_bead": ""},
    }
