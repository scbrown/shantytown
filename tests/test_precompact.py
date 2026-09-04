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
    assert len(rows) == 1
    assert rows[0]["depth_tokens"] == 961_000
    assert rows[0]["model"] == "claude-opus-5"
    assert rows[0]["trigger"] == "auto"


def test_a_measurement_is_recorded_even_with_no_agent_and_no_bead(tmp_path):
    """The two jobs are separable and the measurement is the cheaper one. An
    agent with nothing on its plate still tells us where its harness compacts."""
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps(_assistant("x", input_tokens=42)) + "\n")
    r = _run_hook(tmp_path, {"trigger": "manual", "transcript_path": str(t)})
    assert r.returncode == 0
    assert json.loads((tmp_path / P.MEASUREMENT_FILE).read_text())["depth_tokens"] == 42


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


def test_an_agents_OWN_handoff_is_not_overwritten_by_a_machine_one(
        hooked, tmp_path, monkeypatch):
    """The directive asks the agent to hand off. If it already did, adding a
    tail-scrape on top buries the better artifact under the worse one."""
    assert _drive(monkeypatch, tmp_path,
                  [{"author": "dearing", "created_at": "2026-09-04T02:00:00Z"}]) == 0
    assert hooked.written == []


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
