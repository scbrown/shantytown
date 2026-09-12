"""The handoff that lands BEFORE compaction (aegis-902vnu).

Stiwi, 2026-09-03: "you should be handing off before compaction same with all st
agents". st already had handoff messages at 400k/600k; what it did not have was
any relationship between those lines and the moment a harness actually compacts.
These tests pin the three things that close it:

  * the Claude-side PreCompact hook MEASURES the boundary and WRITES a checkpoint
    there, and never blocks compaction;
  * the codex-side `st cycle` gate refuses a relaunch when the held bead carries
    no handoff since the last launch — and, crucially, does NOT refuse when it
    merely could not tell;
  * the hook is delivered through provision (self-healing on every launch) and is
    ABSENT from --settings, so it cannot fire twice.

The most important assertions in this file are the negative ones. A hook that
blocks compaction takes away the agent's only relief valve, and a gate that
refuses on a cannot-tell strands a saturated agent — both failures are the shape
of the bug being fixed, one level up.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shantytown import cycle as cycle_mod
from shantytown import precompact as P
from shantytown import provision as PR
from shantytown import runtime as R


def _assistant(text: str = "", **usage) -> dict:
    return {"type": "assistant", "timestamp": "2026-09-04T01:00:00Z",
            "message": {"model": "claude-opus-5", "usage": usage or {},
                        "content": [{"type": "text", "text": text}] if text else []}}


# ---------------------------------------------------------------- measurement

def test_depth_is_the_last_assistant_usage_input_plus_both_cache_legs():
    """Claude Code's own accounting of what it just SENT — which is the number a
    compaction threshold is compared against."""
    recs = [_assistant("early", input_tokens=10),
            _assistant("late", input_tokens=1000, cache_read_input_tokens=900_000,
                       cache_creation_input_tokens=5_000, output_tokens=777)]
    assert P.depth_tokens(recs) == 906_000, "output_tokens must not be counted"


def test_depth_is_None_not_zero_when_the_transcript_says_nothing():
    """UNKNOWN and SHALLOW must not render the same. A measurement log that
    reports an unreadable transcript as 0k would put a fabricated point below
    every threshold — the flattering direction, which is the one that stops you
    looking."""
    assert P.depth_tokens([]) is None
    assert P.depth_tokens([{"type": "user"}]) is None


def test_a_torn_transcript_line_is_skipped_not_fatal(tmp_path):
    """The transcript is being appended to while the hook reads it."""
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps(_assistant("ok", input_tokens=5)) + "\n{\"type\": \"assis")
    recs = P._transcript_records(str(t))
    assert len(recs) == 1 and P.depth_tokens(recs) == 5


def test_the_tail_carries_assistant_TEXT_oldest_first_and_no_tool_noise():
    recs = [_assistant("first"), {"type": "user", "message": {"content": "noise"}},
            _assistant("second")]
    assert P.transcript_tail(recs) == "first\n\nsecond"


# ------------------------------------------------------------------- the gate

def test_any_comment_by_the_agent_counts_as_a_checkpoint():
    """Not only the machine's own marker. The directive wants the AGENT to hand
    off; a gate that accepted only its own writes would refuse the behaviour it
    exists to produce."""
    comments = [{"author": "dearing", "created_at": "2026-09-04T02:00:00Z",
                 "text": "hand-written handoff"}]
    assert cycle_mod.checkpoint_since(comments, "dearing", "2026-09-04T01:00:00Z")


def test_a_comment_from_BEFORE_the_boundary_does_not_count():
    comments = [{"author": "dearing", "created_at": "2026-09-04T00:30:00Z"}]
    assert not cycle_mod.checkpoint_since(comments, "dearing", "2026-09-04T01:00:00Z")


def test_somebody_elses_comment_is_not_your_checkpoint():
    comments = [{"author": "sattler", "created_at": "2026-09-04T02:00:00Z"}]
    assert not cycle_mod.checkpoint_since(comments, "dearing", "2026-09-04T01:00:00Z")


def test_durable_gate_has_THREE_states_and_cannot_tell_is_not_a_refusal():
    """The load-bearing negative. An unreadable tracker must not be able to
    strand a saturated agent: an agent that cannot cycle keeps filling, which is
    the exact failure this bead is about."""
    unreadable = cycle_mod.durable_gate("dearing", "aegis-1", "2026-09-04T01:00:00Z",
                                        [], error="store down")
    assert unreadable.ok is None and "store down" in unreadable.render()

    nothing = cycle_mod.durable_gate("dearing", "aegis-1", "2026-09-04T01:00:00Z", [])
    assert nothing.ok is False

    present = cycle_mod.durable_gate(
        "dearing", "aegis-1", "2026-09-04T01:00:00Z",
        [{"author": "dearing", "created_at": "2026-09-04T02:00:00Z"}])
    assert present.ok is True


def test_no_launch_stamp_is_cannot_tell_not_a_pass():
    """Without a launch time there is no 'since the last relaunch' to measure
    against. Reporting that as ok=True would be a green light derived from a
    missing instrument."""
    g = cycle_mod.durable_gate("dearing", "aegis-1", "", [])
    assert g.ok is None and "launch stamp" in g.note


def test_the_refusal_names_the_remedy_and_the_override():
    g = cycle_mod.durable_gate("dearing", "aegis-902vnu", "2026-09-04T01:00:00Z", [])
    text = g.render()
    assert "br comments add aegis-902vnu" in text
    assert "st cycle --self --checkpoint-file" in text
    assert "--allow-loss" in text


# ------------------------------------------------------------- the hook itself

def _run_hook(root: Path, payload: dict, env_extra=None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("SHANTY_AGENT", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "shantytown.precompact", "--root", str(root)],
        input=json.dumps(payload), capture_output=True, text=True, env=env,
        cwd=str(Path(__file__).resolve().parents[1]), timeout=60)


def test_the_hook_NEVER_blocks_compaction_even_on_garbage(tmp_path):
    """MEASURED from claude 2.1.260: a blocking PreCompact hook leaves the
    session `continuing uncompacted`, i.e. walking into the hard context wall. A
    refusal here does not buy time, it removes the relief valve. Exit 0, always —
    on a good payload, on garbage, and with no store at all."""
    for payload in ({}, {"trigger": "auto"}, {"transcript_path": "/nope"}):
        r = _run_hook(tmp_path, payload)
        assert r.returncode == 0, f"{payload} -> rc={r.returncode}: {r.stderr}"


def test_stdout_steers_the_compaction_summary(tmp_path):
    """A PreCompact hook's stdout is joined into `newCustomInstructions`
    (executePreCompactHooks, claude 2.1.260) — so the summary itself can be told
    what to preserve. This is the half that works even when the tracker is down,
    which is when a checkpoint is hardest to land."""
    r = _run_hook(tmp_path, {"trigger": "auto"})
    assert "LANDED" in r.stdout and "rollback" in r.stdout


def test_the_boundary_is_MEASURED_to_compaction_jsonl(tmp_path):
    """Deliverable 1: where compaction actually fires, per agent, per model. A
    constant in a source file cannot answer it — the threshold is
    `window - min(maxOut, 20000) - 13000`, so it moves with the MODEL, and the
    fleet does not run one model."""
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps(_assistant("x", input_tokens=1000,
                                       cache_read_input_tokens=960_000)) + "\n")
    _run_hook(tmp_path, {"trigger": "auto", "session_id": "s1",
                         "transcript_path": str(t)})
    rows = [json.loads(l) for l in
            (tmp_path / P.MEASUREMENT_FILE).read_text().splitlines() if l.strip()]
    # The ledger now carries TWO kinds of record on one line-oriented file: the
    # boundary, and the outcome stamp saying which branch the checkpoint half
    # took. Select the boundary rather than asserting the file has one line —
    # the "exactly one" property being checked here is one BOUNDARY per run, and
    # it stays worth checking; what changed is that the file is no longer a
    # single-record file, so an unfiltered read tests the file's shape instead.
    bounds = [r for r in rows if r.get("kind") != P.OUTCOME_KIND]
    assert len(bounds) == 1
    assert bounds[0]["depth_tokens"] == 961_000
    assert bounds[0]["model"] == "claude-opus-5"
    assert bounds[0]["trigger"] == "auto"
    # And the outcome is stamped: _run_hook pops SHANTY_AGENT, so this run takes
    # the no-agent branch. Asserting the CODE, not merely that a record exists —
    # a stamp that cannot be wrong about which branch ran would be pointless.
    outs = [r for r in rows if r.get("kind") == P.OUTCOME_KIND]
    assert len(outs) == 1
    assert outs[0]["checkpoint"] == P.OUTCOME_NO_AGENT


def test_a_measurement_is_recorded_even_with_no_agent_and_no_bead(tmp_path):
    """The two jobs are separable and the measurement is the cheaper one. An
    agent with nothing on its plate still tells us where its harness compacts."""
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps(_assistant("x", input_tokens=42)) + "\n")
    r = _run_hook(tmp_path, {"trigger": "manual", "transcript_path": str(t)})
    assert r.returncode == 0
    rows = [json.loads(l) for l in
            (tmp_path / P.MEASUREMENT_FILE).read_text().splitlines() if l.strip()]
    bounds = [r for r in rows if r.get("kind") != P.OUTCOME_KIND]
    assert len(bounds) == 1 and bounds[0]["depth_tokens"] == 42
    # The separability this test is named for now has a witness: the measurement
    # landed AND the hook said why no checkpoint followed it.
    assert [r["checkpoint"] for r in rows
            if r.get("kind") == P.OUTCOME_KIND] == [P.OUTCOME_NO_AGENT]


def test_the_checkpoint_body_says_it_was_machine_written(tmp_path):
    """A reader must never mistake a tail-scrape for the agent's own handoff.
    The alternative — a checkpoint that reads as considered prose — is worse than
    none, because it is trusted."""
    body = P.checkpoint_body("dearing", "aegis-1", 906_000, "auto", "some reasoning")
    assert P.CHECKPOINT_MARKER in body.splitlines()[0]
    assert "AUTO-WRITTEN" in body and "not composed by the agent" in body.lower()
    assert "some reasoning" in body


def test_an_empty_tail_says_the_reasoning_is_gone_rather_than_inventing_one():
    body = P.checkpoint_body("dearing", "aegis-1", None, "auto", "")
    assert "No assistant text" in body and "depth unknown" in body


# ------------------------------------------------------------------- delivery

def test_the_hook_is_delivered_through_provision_for_every_role(tmp_path):
    """Including the administrator. The untracked nudge exempts admins because
    being scolded for dispatching is role-specific; losing your reasoning to a
    summary is not, and the directive says "all st agents"."""
    for role in ("worker", "lead", "administrator"):
        out = PR._with_precompact_hook(json.dumps({}), role, tmp_path)
        cmds = [h["command"] for e in json.loads(out)["hooks"]["PreCompact"]
                for h in e["hooks"]]
        assert any("shantytown.precompact" in c for c in cmds), role
        assert any(str(tmp_path) in c for c in cmds), f"{role}: --root not baked in"


def test_reprovisioning_cannot_stack_two_checkpoints_per_boundary(tmp_path):
    once = PR._with_precompact_hook(json.dumps({}), "worker", tmp_path)
    twice = PR._with_precompact_hook(once, "worker", tmp_path)
    entries = json.loads(twice)["hooks"]["PreCompact"]
    assert sum("shantytown.precompact" in h["command"]
               for e in entries for h in e["hooks"]) == 1


def test_a_template_with_its_own_PreCompact_entry_keeps_it(tmp_path):
    theirs = {"hooks": {"PreCompact": [{"hooks": [
        {"type": "command", "command": "echo theirs"}]}]}}
    out = json.loads(PR._with_precompact_hook(json.dumps(theirs), "worker", tmp_path))
    cmds = [h["command"] for e in out["hooks"]["PreCompact"] for h in e["hooks"]]
    assert "echo theirs" in cmds and any("shantytown.precompact" in c for c in cmds)


def test_a_non_json_template_passes_through_verbatim(tmp_path):
    """Provisioning must never fail because of a hook injector."""
    assert PR._with_precompact_hook("not json", "worker", tmp_path) == "not json"


def test_the_hook_is_ABSENT_from_role_settings(tmp_path):
    """ONE HOME. Claude Code merges hooks from every settings source it reads, so
    a command wired in both --settings and the consent file fires TWICE per
    boundary — two checkpoints on every bead. Pinned as an absence, the way
    test_role_emit pins the capture hook's."""
    for role in ("worker", "lead", "administrator"):
        blob = json.dumps(R.claude_settings_for_role(role, root=tmp_path))
        assert "shantytown.precompact" not in blob, role


def test_the_registration_is_matcher_free(tmp_path):
    """PreCompact matches on its TRIGGER ("auto"|"manual"), not a tool name. A
    matcher here would be the aegis-ac5x failure in a new event: a registration
    that looks specific and fires half the time — and the half it would miss is
    a hand-typed /compact, which destroys the same reasoning."""
    entry = R._precompact_hook(tmp_path)
    assert "matcher" not in entry
    assert entry["hooks"][0]["timeout"] > 0, "an unbounded hook can wedge compaction"


# ------------------------------------------------- the write/skip decision

class _FakeTracker:
    def __init__(self): self.written = []


@pytest.fixture
def hooked(tmp_path, monkeypatch):
    """main() with its three I/O seams replaced — the decision under test is
    'write or skip', not whether br is reachable."""
    trk = _FakeTracker()
    monkeypatch.setenv("SHANTY_AGENT", "dearing")
    monkeypatch.setattr(P, "_held_bead", lambda root, me: "aegis-902vnu")
    monkeypatch.setattr(P, "_tracker", lambda root: trk)
    import shantytown.br as br
    monkeypatch.setattr(br, "append_comment",
                        lambda t, bead, body: trk.written.append((bead, body)))
    return trk


def _drive(monkeypatch, tmp_path, comments, tail_ts="2026-09-04T01:00:00Z"):
    import shantytown.br as br
    monkeypatch.setattr(br, "comments", lambda t, bead: comments)
    t = tmp_path / "t.jsonl"
    rec = _assistant("mid-refactor: cycle.py durable gate", input_tokens=900_000)
    rec["timestamp"] = tail_ts
    t.write_text(json.dumps(rec) + "\n")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(
        {"trigger": "auto", "session_id": "s1", "transcript_path": str(t)})))
    return P.main(["--root", str(tmp_path)])


def test_a_checkpoint_is_written_when_the_bead_has_none_since_the_boundary(
        hooked, tmp_path, monkeypatch):
    assert _drive(monkeypatch, tmp_path, []) == 0
    assert len(hooked.written) == 1
    bead, body = hooked.written[0]
    assert bead == "aegis-902vnu"
    assert "mid-refactor: cycle.py durable gate" in body


def _ago(minutes):
    """A real timestamp N minutes before now.

    The fixture here used to be a hardcoded date, which silently became "eight
    days stale" as the calendar moved and so tested the recency bound by
    accident rather than on purpose (aegis-ugztez). Age is the variable these
    two tests are about, so it is now stated in the call.
    """
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_an_agents_RECENT_OWN_handoff_is_not_overwritten_by_a_machine_one(
        hooked, tmp_path, monkeypatch):
    """The directive asks the agent to hand off. If it already did, adding a
    tail-scrape on top buries the better artifact under the worse one.

    RECENT is load-bearing since aegis-ugztez — see the companion below."""
    assert _drive(monkeypatch, tmp_path,
                  [{"author": "dearing", "created_at": _ago(10)}]) == 0
    assert hooked.written == []


def test_a_STALE_own_handoff_no_longer_suppresses_the_checkpoint(
        hooked, tmp_path, monkeypatch):
    """The companion, and the actual behaviour change (aegis-ugztez).

    A comment written early in a 3-16h window used to suppress the checkpoint at
    a fill many hours later, so the reasoning about to be summarised away was
    preserved by nothing — measured on 5 of 13 real suppressions, up to 11.1h.
    Beyond the bound the hook now writes, accepting a duplicate comment as the
    cheaper error than silence."""
    assert _drive(monkeypatch, tmp_path,
                  [{"author": "dearing", "created_at": _ago(200)}]) == 0
    assert len(hooked.written) == 1, "a stale handoff must not suppress"


def test_an_unreadable_tracker_WRITES_rather_than_assuming_a_checkpoint_exists(
        hooked, tmp_path, monkeypatch):
    """Cannot-tell goes the opposite way here from the cycle gate, and both are
    right: a duplicate comment costs a comment, a missed one costs the session's
    reasoning."""
    import shantytown.br as br
    def boom(t, bead): raise RuntimeError("store down")
    monkeypatch.setattr(br, "comments", boom)
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps(_assistant("reasoning", input_tokens=5)) + "\n")
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(
        {"trigger": "auto", "session_id": "s1", "transcript_path": str(t)})))
    assert P.main(["--root", str(tmp_path)]) == 0
    assert len(hooked.written) == 1


# ----------------------------------------- has it REACHED the running fleet?

class _Card:
    def __init__(self, name, workspace, harness="claude"):
        self.name, self.workspace, self.harness = name, workspace, harness


def _agent_with(tmp_path, name, hooks):
    ws = tmp_path / name; (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "settings.local.json").write_text(json.dumps({"hooks": hooks}))
    return _Card(name, str(ws))


def _wired_ws(tmp_path, name):
    return _agent_with(tmp_path, name, {"PreCompact": [{"hooks": [
        {"type": "command", "command": "python -m shantytown.precompact --root /x"}]}]})


def test_doctor_reports_agents_still_UNPROTECTED_after_the_deploy(tmp_path):
    """The fix is NOT retroactive and this is the population that matters most:
    the hook reaches an agent at LAUNCH, so the sessions deep enough to be near a
    boundary are exactly the ones that have not relaunched since it landed. That
    interval is invisible from the repo, which is the whole reason for the row."""
    from shantytown import stats as S
    agents = [_wired_ws(tmp_path, "arnold"), _agent_with(tmp_path, "muldoon", {})]
    verdict, why = S.precompact_wiring(agents)
    assert verdict == S.WIRING_BROKEN
    assert "muldoon" in why and "relaunch" in why


def test_all_wired_reads_green(tmp_path):
    from shantytown import stats as S
    verdict, why = S.precompact_wiring([_wired_ws(tmp_path, "arnold")])
    assert verdict == S.WIRING_OK and "1" in why


def test_an_unreadable_workspace_is_UNKNOWN_never_ok(tmp_path):
    from shantytown import stats as S
    verdict, _ = S.precompact_wiring(
        [_wired_ws(tmp_path, "arnold"), _Card("ghost", str(tmp_path / "nope"))])
    assert verdict == S.WIRING_UNKNOWN


def test_a_codex_card_is_SKIPPED_not_counted_as_broken(tmp_path):
    """codex has no PreCompact event at all. Counting one as missing would be a
    permanent false alarm about a hook that cannot exist — and an alarm that can
    never clear is an alarm that gets ignored, including the day it is real."""
    from shantytown import stats as S
    codex = _agent_with(tmp_path, "gennaro", {})
    codex.harness = "codex"
    verdict, why = S.precompact_wiring([_wired_ws(tmp_path, "arnold"), codex])
    assert verdict == S.WIRING_OK, why
    assert "gennaro" not in why


def test_an_outcome_record_is_not_mistaken_for_the_boundary(tmp_path):
    """The outcome stamp shares its session with the boundary it describes and is
    written a moment LATER. `_last_boundary` feeds `has_checkpoint_since`, so
    taking the outcome as the boundary would move that floor forward by that
    moment — which silently narrows the window in which an agent's own
    checkpoint counts as recent, and makes the hook overwrite a handoff the agent
    had just written. Small, wrong, and exactly the kind of thing that is found
    long afterwards, so it is pinned here rather than left to the reader of the
    `continue`."""
    log = tmp_path / P.MEASUREMENT_FILE
    log.write_text(
        json.dumps({"at": "2026-09-08T10:00:00Z", "session_id": "s9"}) + "\n" +
        json.dumps({"at": "2026-09-08T10:00:07Z", "session_id": "s9",
                    "kind": P.OUTCOME_KIND, "checkpoint": P.OUTCOME_WRITTEN}) + "\n")
    assert P._last_boundary(log, "s9") == "2026-09-08T10:00:00Z"


def test_every_exit_branch_after_the_measurement_stamps_an_outcome(tmp_path):
    """The point of the field is that NO branch is silent — a branch that forgets
    to stamp is indistinguishable from the total failure the stamp exists to rule
    out, which is the ambiguity that cost a forensic pass on aegis-902vnu. Read
    the source rather than trusting the six constants to be wired up: each
    OUTCOME_* constant must be referenced somewhere beyond its own definition."""
    src = Path(P.__file__).read_text()
    for name in ("OUTCOME_NO_AGENT", "OUTCOME_NO_BEAD", "OUTCOME_NO_TRACKER",
                 "OUTCOME_SKIPPED", "OUTCOME_WRITTEN", "OUTCOME_WRITE_FAILED"):
        assert src.count(name) >= 2, f"{name} is defined but never stamped"


# ── RECENCY BOUND ON THE SUPPRESSION (aegis-ugztez) ──────────────────────────
#
# The window used to be "since the last boundary" alone, which on this fleet runs
# 3-16h, so a comment written early in a long window suppressed the checkpoint at
# a fill hours later. Measured over all 15 real boundaries: 13 suppressed, 5 with
# a last own comment >= 2h old (2.1, 2.7, 2.9, 8.8, 11.1h), median 0.9h.

def test_the_floor_tightens_a_long_window_to_the_recency_bound():
    """An old boundary must NOT be the floor — `now - bound` is later, so it wins.
    This is the whole fix: a 16h window stops admitting a 15h-old comment."""
    floor = P._checkpoint_floor("2026-09-08T00:00:00Z", now="2026-09-08T16:00:00Z")
    assert floor == "2026-09-08T14:30:00Z", floor


def test_the_floor_keeps_a_RECENT_boundary_and_does_not_loosen_it():
    """THE CONTROL, and it is the one that matters: the bound must never WIDEN a
    window. A boundary 10 minutes ago stays the floor — taking `now - 90m` there
    would admit comments from BEFORE the boundary and suppress checkpoints the
    old code correctly wrote."""
    floor = P._checkpoint_floor("2026-09-08T15:50:00Z", now="2026-09-08T16:00:00Z")
    assert floor == "2026-09-08T15:50:00Z", floor


def test_each_MEASURED_stale_suppression_now_crosses_the_bound():
    """Pinned to the real distribution, not to a round number. Every one of the
    five measured gaps must fall outside the floor, and the MEDIAN case must
    still fall inside it — a bound that also caught the median would write a
    checkpoint on every ordinary boundary and become noise."""
    now = "2026-09-08T12:00:00Z"
    floor = P._checkpoint_floor("2026-09-08T00:00:00Z", now=now)
    def comment(hours_old):
        t = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - timedelta(hours=hours_old)
        return [{"author": "a", "created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ")}]
    for stale in (2.1, 2.7, 2.9, 8.8, 11.1):          # the five gaps
        assert not P.has_checkpoint_since(comment(stale), "a", floor), \
            f"{stale}h comment still suppresses the checkpoint"
    assert P.has_checkpoint_since(comment(0.9), "a", floor), \
        "the MEDIAN case must still suppress, or the hook writes on every boundary"


def test_an_unparseable_now_falls_back_to_the_window_rather_than_failing():
    """Fails toward the old behaviour, never toward a crash in a hook that runs
    while the transcript is being destroyed."""
    assert P._checkpoint_floor("2026-09-08T00:00:00Z", now="not-a-time") == \
        "2026-09-08T00:00:00Z"


def test_no_window_still_yields_a_floor_so_absence_is_not_taken_as_a_handoff():
    """No boundary is not evidence that a handoff exists. Returning None here
    would make `has_checkpoint_since` return False and write — which is the safe
    direction — but returning the age floor is the same direction and says why."""
    assert P._checkpoint_floor(None, now="2026-09-08T16:00:00Z") == \
        "2026-09-08T14:30:00Z"


def test_the_SHARED_predicate_is_untouched_by_the_bound():
    """THE CONSTRAINT aegis-ugztez names explicitly: `cycle.checkpoint_since` is
    the ONE predicate, shared with the codex `st cycle` gate, where a recency
    bound would refuse a cycle for a merely-old handoff and strand a saturated
    agent. The bound must therefore live in the CALLER's floor, never in the
    predicate."""
    import inspect
    from shantytown import cycle
    src = inspect.getsource(cycle.checkpoint_since)
    assert "MAX_AGE" not in src and "timedelta" not in src, \
        "a recency bound leaked into the shared predicate"


def test_the_staleness_of_a_suppressing_comment_is_recorded():
    """Remedy 3, kept alongside remedy 1: the gap was invisible because the
    ledger recorded a clean boundary and nothing carried the age. One field makes
    the next regression readable from the ledger instead of re-resolving every
    held bead by hand."""
    now = "2026-09-08T12:00:00Z"
    cs = [{"author": "a", "created_at": "2026-09-08T11:00:00Z"},
          {"author": "b", "created_at": "2026-09-08T11:59:00Z"}]
    assert P._own_comment_age_s(cs, "a", now=now) == 3600
    assert P._own_comment_age_s(cs, "nobody", now=now) is None
