"""Tokens: the capture must be WIRED to fire, and the report must not lie about
what it measured (aegis-u5u98).

THE LOAD-BEARING TEST IS `test_the_capture_hook_is_registered_on_BOTH_events`.
Everything else is a specimen; that one is the invariant, and it is written
against `stats.capture`'s own branch structure rather than against a literal
settings dict — a registration that does not reach the branch that writes tokens
is the bug, whatever the JSON looks like.

WHAT ACTUALLY HAPPENED, because the report's shape is why it took twelve days.
`capture` has two branches: PostToolUse writes an events row, and the STOP branch
is the only thing that ever writes a token row. Only PostToolUse was registered.
So no token could be recorded anywhere on the fleet — and the store still looked
healthy, because events kept flowing from the branch that WAS wired.

It presented as a per-agent quirk (2 of 11 agents recording, 9 confident zeros)
for a second, independent reason: `stats_report` bounded the events query by the
window and the token query by NOTHING. The only agents showing totals were the
handful with rows left over from the last day the Stop path ever fired, printed
under a `last 24h` header. The bug report reasonably went looking for what made
those agents special. Nothing did — the window did.

Two halves of one illusion: a capture that could not run, and a display that made
its absence look selective. Both are pinned below, and `test_a_stale_row_is_not
_counted_in_the_window` is the one that would have refused to let the first hide.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import time

from shantytown import provision, stats
from shantytown.runtime import _capture_cmd


# --- the invariant: the write path is reachable ---------------------------------


def test_the_capture_hook_is_registered_on_BOTH_events(tmp_path):
    """THE test. Tokens are written ONLY on the Stop branch, so a capture wired
    to PostToolUse alone can never record one — which is exactly what shipped."""
    out = json.loads(provision._with_capture_hook("{}", tmp_path))
    hooks = out["hooks"]
    cmd = _capture_cmd(tmp_path)["command"]

    assert any(h["command"] == cmd for b in hooks["PostToolUse"]
               for h in b["hooks"]), "the events branch lost its registration"
    assert "Stop" in hooks, (
        "capture is not registered on Stop — the ONLY branch that writes a token "
        "row. This is the whole of aegis-u5u98.")
    assert any(h["command"] == cmd for b in hooks["Stop"]
               for h in b["hooks"]), "Stop is registered to something else"


def test_the_stop_registration_carries_NO_matcher(tmp_path):
    """A Stop payload has no tool name. A matcher on an event with nothing to
    match is the aegis-ac5x failure — a registration that looks specific and
    fires zero times, which is indistinguishable from the bug being fixed here."""
    stop = json.loads(provision._with_capture_hook("{}", tmp_path))["hooks"]["Stop"]
    assert all("matcher" not in b for b in stop), stop


def test_the_two_registrations_are_the_SAME_command(tmp_path):
    """One capture, two events. If these ever drift apart, one branch is being
    fed by a command that resolves a different interpreter or a different store
    root — the failure `_capture_cmd`'s baked-in --root exists to prevent."""
    hooks = json.loads(provision._with_capture_hook("{}", tmp_path))["hooks"]
    post = [h["command"] for b in hooks["PostToolUse"] for h in b["hooks"]
            if "shantytown.stats capture" in h["command"]]
    stop = [h["command"] for b in hooks["Stop"] for h in b["hooks"]]
    assert post == stop


def test_a_non_dict_template_still_passes_through(tmp_path):
    """Provisioning must never fail because of the stats layer."""
    assert provision._with_capture_hook("not json", tmp_path) == "not json"
    assert provision._with_capture_hook("[1,2]", tmp_path) == "[1,2]"


# --- end to end: a stop payload actually lands a token row ----------------------


def _capture(root, payload, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return stats.main(["capture", "--root", str(root)])


def test_a_stop_payload_records_tokens_AND_a_stop_event(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_AGENT", "arnold")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps(
        {"message": {"usage": {"input_tokens": 11, "output_tokens": 22}}}) + "\n")
    assert _capture(tmp_path, {"session_id": "s9", "hook_event_name": "Stop",
                               "transcript_path": str(tp)}, monkeypatch) == 0
    db = sqlite3.connect(tmp_path / "stats.sqlite")
    assert db.execute("SELECT input_toks, output_toks FROM tokens").fetchone() == (11, 22)
    # stops=0 across the fleet was the visible symptom sitting right next to the
    # zeros, and nothing connected them. Both come from this one branch.
    assert db.execute("SELECT COUNT(*) FROM events WHERE kind='stop'").fetchone()[0] == 1


# --- the report must not lie about the window ----------------------------------


def _store(tmp_path, *, ev_age_h, tok_age_h=None, agent="tim"):
    """Events inside the window; optionally a token row at some age."""
    now = time.time()
    db = sqlite3.connect(tmp_path / "stats.sqlite")
    db.executescript(stats._SCHEMA)
    db.execute("INSERT INTO events(ts, agent, kind, session) VALUES (?,?,?,?)",
               (now - ev_age_h * 3600, agent, "tool", "s1"))
    if tok_age_h is not None:
        db.execute("INSERT INTO tokens(session, agent, input_toks, output_toks,"
                   " updated) VALUES (?,?,?,?,?)",
                   ("s1", agent, 5, 500, now - tok_age_h * 3600))
    db.commit()
    db.close()
    return now


def _report(tmp_path, since_h=24.0):
    buf = io.StringIO()
    stats.stats_report(tmp_path, since_h=since_h, out=buf)
    return buf.getvalue()


def test_a_stale_row_is_not_counted_in_the_window(tmp_path):
    """THE specimen. Events 1h old, a token row 300h old, header says 24h.

    Before the fix this printed `tokens_out=500` — an all-time total under a
    24-hour heading — and that is precisely what made a dead pipeline look like a
    handful of agents mysteriously still working.
    """
    _store(tmp_path, ev_age_h=1, tok_age_h=300)
    out = _report(tmp_path)
    assert "tokens_out=500" not in out, out
    assert "tokens=?" in out, out


def test_a_row_INSIDE_the_window_is_reported_normally(tmp_path):
    """Non-vacuity: the fix must not simply suppress every token."""
    _store(tmp_path, ev_age_h=1, tok_age_h=1)
    out = _report(tmp_path)
    assert "tokens_in=5 tokens_out=500" in out, out
    assert "tokens=?" not in out


def test_NO_ROW_is_not_a_zero(tmp_path):
    """`tokens_out=0` is a MEASUREMENT — this agent ran and produced nothing —
    and it rendered identically to 'nothing ever recorded a token'. One invites a
    shrug, the other is a broken pipeline. Same rule as `hooks: ?` and `?/?/?`."""
    _store(tmp_path, ev_age_h=1, tok_age_h=None)
    out = _report(tmp_path)
    assert "tokens=? (none captured)" in out, out
    assert "tokens_out=0" not in out, out


def test_a_measured_ZERO_still_prints_as_zero(tmp_path):
    """The distinction has to cut both ways or it is just a relabelling."""
    now = time.time()
    db = sqlite3.connect(tmp_path / "stats.sqlite")
    db.executescript(stats._SCHEMA)
    db.execute("INSERT INTO events(ts, agent, kind, session) VALUES (?,?,?,?)",
               (now - 3600, "tim", "tool", "s1"))
    db.execute("INSERT INTO tokens(session, agent, input_toks, output_toks,"
               " updated) VALUES (?,?,?,?,?)", ("s1", "tim", 0, 0, now - 3600))
    db.commit(); db.close()
    out = _report(tmp_path)
    assert "tokens_in=0 tokens_out=0" in out, out
    assert "tokens=?" not in out


# --- the tell ------------------------------------------------------------------


def test_a_fleet_wide_absence_says_so_ONCE(tmp_path):
    """Twenty agents each printing `tokens=?` is one fault, not twenty gaps, and
    the reader needs a sentence saying so and naming where to look. This is the
    'ship the way to tell it is alive' half — the outage was silent for twelve
    days precisely because nothing ever said this."""
    now = time.time()
    db = sqlite3.connect(tmp_path / "stats.sqlite")
    db.executescript(stats._SCHEMA)
    for ag in ("tim", "billy", "grant"):
        db.execute("INSERT INTO events(ts, agent, kind, session) VALUES (?,?,?,?)",
                   (now - 3600, ag, "tool", "s" + ag))
    db.commit(); db.close()
    out = _report(tmp_path)
    assert "NO TOKENS CAPTURED FOR ANY AGENT" in out, out
    assert "Stop hook" in out, "the tell must name the branch that writes tokens"


def test_the_tell_stays_QUIET_when_anything_was_captured(tmp_path):
    """A warning that fires when the thing is working gets switched off within a
    day. One agent recording is enough to prove the pipeline is alive."""
    _store(tmp_path, ev_age_h=1, tok_age_h=1, agent="ellie")
    out = _report(tmp_path)
    assert "NO TOKENS CAPTURED" not in out, out


def test_the_tell_does_not_fire_on_an_EMPTY_window(tmp_path):
    """No activity at all is already reported by its own line. Adding a token
    alarm there would alarm about a fleet that simply was not running."""
    db = sqlite3.connect(tmp_path / "stats.sqlite")
    db.executescript(stats._SCHEMA)
    db.commit(); db.close()
    out = _report(tmp_path)
    assert "no activity captured" in out
    assert "NO TOKENS CAPTURED" not in out, out


# --- the tell must point at a command that ANSWERS -----------------------------
#
# `stats_report`'s fleet-wide warning tells the reader to run `st doctor`. When
# that line was first written, doctor said NOTHING about the capture wiring — a
# tell pointing at a dead end, in the exact place it was meant to help. These pin
# the check that made the sentence true.
#
# It reads the ARTIFACT EACH AGENT RUNS WITH, not the emitter. The emitter's
# behaviour is already guaranteed above; the question at 3am is whether the fix
# has REACHED anybody, and only the on-disk consent file answers that — provision
# writes it at launch, so a corrected st reaches an agent only when it relaunches.


def _agent(name, ws):
    import types
    return types.SimpleNamespace(name=name, workspace=str(ws) if ws else None)


def _workspace(tmp_path, name, *, stop_cmds=None):
    ws = tmp_path / name
    (ws / ".claude").mkdir(parents=True)
    cfg = {"hooks": {"PostToolUse": [{"matcher": ".*", "hooks": [
        {"type": "command", "command": "x -m shantytown.stats capture"}]}]}}
    if stop_cmds is not None:
        cfg["hooks"]["Stop"] = [{"hooks": [
            {"type": "command", "command": c} for c in stop_cmds]}]
    (ws / ".claude" / "settings.local.json").write_text(json.dumps(cfg))
    return _agent(name, ws)


def test_wiring_ok_when_every_agent_registers_capture_on_stop(tmp_path):
    ags = [_workspace(tmp_path, n, stop_cmds=["py -m shantytown.stats capture --root /r"])
           for n in ("tim", "billy")]
    v, why = stats.capture_wiring(ags)
    assert v == stats.WIRING_OK, why
    assert "all 2" in why


def test_wiring_BROKEN_names_the_agents_and_says_relaunch(tmp_path):
    """The remedy matters as much as the finding: the code being fixed and the
    fleet being fixed are different events, and a reader who does not know that
    reads a correct deploy as a failed one."""
    ags = [_workspace(tmp_path, "tim", stop_cmds=["py -m shantytown.stats capture"]),
           _workspace(tmp_path, "billy", stop_cmds=None)]
    v, why = stats.capture_wiring(ags)
    assert v == stats.WIRING_BROKEN
    assert "billy" in why and "tim" not in why.split(":")[-1]
    assert "relaunch" in why


def test_a_stop_registered_to_SOMETHING_ELSE_is_not_wired(tmp_path):
    """Stop carries other hooks (stop_event send/drain, the quipu capture). The
    presence of a Stop block proves nothing about the token branch."""
    ags = [_workspace(tmp_path, "tim",
                      stop_cmds=["py -m shantytown.stop_event send --root /r"])]
    assert stats.capture_wiring(ags)[0] == stats.WIRING_BROKEN


def test_an_unreadable_workspace_is_UNKNOWN_not_ok(tmp_path):
    """A workspace we could not read is not a workspace that is wired. Same rule
    as `hooks: ?` — never report a word you did not measure."""
    assert stats.capture_wiring([_agent("x", None)])[0] == stats.WIRING_UNKNOWN
    assert stats.capture_wiring(
        [_agent("y", tmp_path / "nope")])[0] == stats.WIRING_UNKNOWN


def test_an_empty_fleet_is_UNKNOWN_not_a_clean_bill(tmp_path):
    """The empty-report false pass this repo keeps re-finding: 'all 0 agents are
    wired' is a sentence that can only ever be true."""
    assert stats.capture_wiring([])[0] == stats.WIRING_UNKNOWN


def test_render_marks_each_verdict_distinctly():
    marks = {stats.render_wiring(v, "w")[2]
             for v in (stats.WIRING_OK, stats.WIRING_BROKEN, stats.WIRING_UNKNOWN)}
    assert len(marks) == 3


def test_an_UNKNOWN_does_not_swallow_the_BROKEN_beside_it(tmp_path):
    """The verdict stays conservative; the SENTENCE reports both halves.

    Measured live the moment this shipped: 3 unreadable workspaces rendered as
    `? could not read the consent file for 3` and said NOTHING about the
    seventeen that were provably not capturing. Letting the weaker finding hide
    the actionable one is the bug this whole bead is about, one layer up.
    """
    ags = [_workspace(tmp_path, "billy", stop_cmds=None),
           _workspace(tmp_path, "tim", stop_cmds=None),
           _agent("goldblum", None)]
    v, why = stats.capture_wiring(ags)
    assert v == stats.WIRING_UNKNOWN, "a fleet we cannot fully read is never ok"
    assert "do NOT register" in why, why      # the actionable half survives
    assert "billy" in why and "tim" in why
    assert "goldblum" in why                  # ...and so does the unreadable half


def test_wired_agents_are_reported_even_when_some_are_unreadable(tmp_path):
    ags = [_workspace(tmp_path, "tim", stop_cmds=["p -m shantytown.stats capture"]),
           _agent("goldblum", None)]
    v, why = stats.capture_wiring(ags)
    assert v == stats.WIRING_UNKNOWN
    assert "all 1 readable" in why and "goldblum" in why
