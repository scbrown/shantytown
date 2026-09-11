"""The HAUL advance: a worker's assigned queue feeds ITSELF at its own stop.

The design bead's core contract, each clause a test: advance only on evidence
the anchor finished (a stop is a turn boundary, not an idle agent); the next
bead arrives as the Stop-hook block reason (the same model-reaching protocol
drain and the Rule Zero gate use); the 600k handoff line (60% of the window)
stops the feed and instructs the reset instead; everything fails OPEN — a
broken advance must never trap a worker at its own stop.
"""
from __future__ import annotations

from shantytown.answer import Answer
import json

import pytest

from shantytown import stop_event
from shantytown.protocols import Agent


class _Reg:
    def __init__(self, cards):
        self._c = {a.name: a for a in cards}
    def get(self, name):
        return self._c[name]
    def all(self):
        return Answer.complete_read(list(self._c.values()), how="test registry")


class _Panes:
    def __init__(self, screens=None):
        self._screens = screens or {}
    def exists(self, pane):
        return pane in self._screens
    def capture(self, pane, history=0, attrs=False):
        return self._screens.get(pane, "")


WORKER = Agent(name="billy", role="worker", pane="p-b")
SATURATED_PANE = ("❯ \n"
                  "                  new task? /clear to save 650.0k tokens\n"
                  "  ⏵⏵ bypass permissions on (shift+tab to cycle)")


def _bd(monkeypatch, ready=None, in_progress=None, fail=False, claims=None):
    """Stub the two bd reads + the claim write the advance makes."""
    # **kw, not fixed arity: _bd_json gained root/reg when the stop-hook haul
    # path was routed to br (aegis-mxgzh). A fixed-arity double raises
    # TypeError into the caller's fail-open, so the haul silently produces
    # no block and the assertion fails on None rather than on the behaviour.
    def fake(args, cwd, **kw):
        if fail:
            raise RuntimeError("bd unreachable")
        if args[0] == "ready":
            return ready or []
        if args[0] == "list":
            return in_progress or []
        if args[0] == "update":
            (claims if claims is not None else []).append(args[1])
            return []
        raise AssertionError(args)
    monkeypatch.setattr(stop_event, "_bd_json", fake)


def _run(monkeypatch, capsys, reg=None, panes=None, **bd):
    _bd(monkeypatch, **bd)
    rc = stop_event._haul(reg or _Reg([WORKER]), panes or _Panes({"p-b": "❯ "}),
                          "billy", None)
    out = capsys.readouterr().out.strip()
    return rc, (json.loads(out) if out else None)


def test_anchor_closed_and_queue_ready_feeds_the_next_bead(monkeypatch, capsys):
    claims = []
    rc, block = _run(monkeypatch, capsys, claims=claims,
                     ready=[{"id": "aegis-2", "title": "next up",
                             "assignee": "beads_aegis/crew/billy"}])
    assert rc == 0
    assert block["decision"] == "block"
    assert "aegis-2" in block["reason"] and "HAUL" in block["reason"]
    assert "Yours to work" in block["reason"]
    assert claims == ["aegis-2"], "the fed bead is claimed in_progress"


def test_an_active_anchor_is_a_turn_boundary_allow_silently(monkeypatch, capsys):
    """Claude owns its turn loop, so its mid-work stops remain silent."""
    rc, block = _run(monkeypatch, capsys,
                     ready=[{"id": "aegis-2", "assignee": "billy"}],
                     in_progress=[{"id": "aegis-1", "assignee": "billy"}])
    assert rc == 0 and block is None


def test_a_codex_active_anchor_blocks_with_resume_not_new_work(monkeypatch, capsys):
    """Codex otherwise exits after the turn and its active bead hides it from tend."""
    codex = Agent(name="billy", role="worker", pane="p-b", harness="codex")
    rc, block = _run(
        monkeypatch, capsys, reg=_Reg([codex]),
        in_progress=[{"id": "aegis-1", "title": "finish me", "assignee": "billy"}],
    )
    assert rc == 0
    assert block["decision"] == "block"
    assert "HAUL RESUME" in block["reason"] and "aegis-1" in block["reason"]


def test_codex_resume_backs_off_identical_standing_anchor(tmp_path, monkeypatch,
                                                           capsys):
    """First continuation is immediate; a stop storm cannot re-serve it."""
    codex = Agent(name="billy", role="worker", pane="p-b", harness="codex")
    clock = [1_000.0]
    monkeypatch.setattr(stop_event.time, "time", lambda: clock[0])
    kwargs = {"reg": _Reg([codex]),
              "in_progress": [{"id": "aegis-1", "title": "drain",
                               "assignee": "billy"}]}
    _rc, first = _haul_at(monkeypatch, capsys, tmp_path, **kwargs)
    _rc, repeated = _haul_at(monkeypatch, capsys, tmp_path, **kwargs)
    assert first and "HAUL RESUME" in first["reason"]
    assert repeated is None

    clock[0] += 60
    _rc, second = _haul_at(monkeypatch, capsys, tmp_path, **kwargs)
    assert second and "HAUL RESUME" in second["reason"]
    clock[0] += 119
    _rc, early = _haul_at(monkeypatch, capsys, tmp_path, **kwargs)
    assert early is None, "the second identical prompt doubles the cooldown"


def test_codex_resume_new_anchor_bypasses_old_anchor_backoff(tmp_path, monkeypatch,
                                                             capsys):
    codex = Agent(name="billy", role="worker", pane="p-b", harness="codex")
    clock = [1_000.0]
    monkeypatch.setattr(stop_event.time, "time", lambda: clock[0])
    common = {"reg": _Reg([codex])}
    _rc, first = _haul_at(monkeypatch, capsys, tmp_path, **common,
                          in_progress=[{"id": "aegis-1", "assignee": "billy"}])
    _rc, changed = _haul_at(monkeypatch, capsys, tmp_path, **common,
                            in_progress=[{"id": "aegis-2", "assignee": "billy"}])
    assert first and changed
    assert "aegis-2" in changed["reason"]


def test_a_codex_lead_active_anchor_resumes_too(monkeypatch, capsys):
    """Leads own beads; drain must not strand their own active anchor."""
    lead = Agent(name="billy", role="lead", pane="p-b", harness="codex")
    rc, block = _run(
        monkeypatch, capsys, reg=_Reg([lead]),
        in_progress=[{"id": "aegis-1", "title": "lead work", "assignee": "billy"}],
    )
    assert rc == 0
    assert "HAUL RESUME" in block["reason"] and "aegis-1" in block["reason"]


def test_a_claude_lead_remains_silent(monkeypatch, capsys):
    lead = Agent(name="billy", role="lead", pane="p-b", harness="claude")
    rc, block = _run(
        monkeypatch, capsys, reg=_Reg([lead]),
        in_progress=[{"id": "aegis-1", "assignee": "billy"}],
    )
    assert rc == 0 and block is None


def test_codex_is_resolved_from_deployment_default_for_resume(tmp_path, monkeypatch,
                                                              capsys):
    """Live cards leave harness unset; the deployment default is authoritative."""
    (tmp_path / "shantytown.toml").write_text(
        '[harness]\ndefault = "codex"\n', encoding="utf-8")
    rc, block = _haul_at(
        monkeypatch, capsys, tmp_path,
        in_progress=[{"id": "aegis-1", "title": "finish me", "assignee": "billy"}],
    )
    assert rc == 0
    assert "HAUL RESUME" in block["reason"] and "aegis-1" in block["reason"]


def test_an_empty_queue_is_normal_idle_flow(monkeypatch, capsys):
    rc, block = _run(monkeypatch, capsys, ready=[])
    assert rc == 0 and block is None


def test_someone_elses_beads_are_not_my_haul(monkeypatch, capsys):
    rc, block = _run(monkeypatch, capsys,
                     ready=[{"id": "aegis-9", "assignee": "crew/kelly"}])
    assert rc == 0 and block is None


def test_past_the_600k_line_the_advance_instructs_handoff_not_food(monkeypatch, capsys):
    """Stiwi's line: 60% of the 1M window. Between beads the context is
    disposable by construction — so past the line the block instructs
    checkpoint + /clear and feeds NOTHING; the haul resumes on fresh context."""
    claims = []
    rc, block = _run(monkeypatch, capsys, claims=claims,
                     panes=_Panes({"p-b": SATURATED_PANE}),
                     ready=[{"id": "aegis-2", "assignee": "billy"}])
    assert rc == 0
    assert "HANDOFF" in block["reason"] and "650" in block["reason"]
    assert "aegis-2" not in block["reason"], "past the line, nothing is fed"
    assert claims == [], "nothing is claimed either"


def test_unknown_context_depth_never_triggers_the_handoff(monkeypatch, capsys):
    """None is not over-the-line — unknown never blocks the feed (the
    None-is-not-zero house rule, handoff edition)."""
    rc, block = _run(monkeypatch, capsys,
                     ready=[{"id": "aegis-2", "assignee": "billy"}])
    assert "HAUL:" in block["reason"] and "HANDOFF" not in block["reason"]


def test_a_non_hauling_role_never_hauls(monkeypatch, capsys):
    """Was `test_a_non_worker_never_hauls`, with a LEAD as its fixture. aegis-rvxcf1
    changed that contract deliberately — leads self-feed now, because measuring
    the old behaviour showed a lead's haul advancing in the tracker while the
    pane sat idle awaiting a manual `st go`. The rule this test protects is
    still real, so it is re-pointed at the role that genuinely does not haul
    rather than deleted: an administrator's assigned beads are not a haul, and
    feeding them would put the person who dispatches work head-down in a bead."""
    rc, block = _run(monkeypatch, capsys,
                     reg=_Reg([Agent(name="billy", role="administrator",
                                     pane="p-b")]),
                     ready=[{"id": "aegis-2", "assignee": "billy"}])
    assert rc == 0 and block is None


def test_bd_failure_fails_open_never_traps_the_stop(monkeypatch, capsys):
    rc, block = _run(monkeypatch, capsys, fail=True)
    assert rc == 0 and block is None


def test_a_failed_claim_still_feeds(monkeypatch, capsys):
    """The claim is best-effort: the instruction tells the agent to read the
    bead either way, and a feed that dies on a tracker hiccup would stall the
    haul over bookkeeping."""
    # **kw, not fixed arity: _bd_json gained root/reg when the stop-hook haul
    # path was routed to br (aegis-mxgzh). A fixed-arity double raises
    # TypeError into the caller's fail-open, so the haul silently produces
    # no block and the assertion fails on None rather than on the behaviour.
    def fake(args, cwd, **kw):
        if args[0] == "ready":
            return [{"id": "aegis-2", "assignee": "billy"}]
        if args[0] == "list":
            return []
        raise RuntimeError("update refused")
    monkeypatch.setattr(stop_event, "_bd_json", fake)
    stop_event._haul(_Reg([WORKER]), _Panes({"p-b": "❯ "}), "billy", None)
    out = capsys.readouterr().out
    assert "aegis-2" in out, "feed survives a failed claim"


# --- the session ceiling, WIRED (aegis-xxae9) -------------------------------
#
# The module's own tests live in test_session_budget.py. These drive the real
# `_haul` because the usage governor taught this exact lesson the expensive way:
# every tier passed in isolation while the COMPOSITION was wrong, and it took
# running the real thing to see it. A ceiling that computes correctly and is
# never consulted is the same as no ceiling, and it looks identical from here.

import sqlite3
import time

from shantytown import session_budget as sb
from shantytown import stats


def _armed_root(tmp_path, hours=3.0, items=4, risk=2, spend_hours=None,
                spend_items=0, spend_risk=0, agent="billy"):
    """A shanty root with the budget armed and a stats store showing a spend."""
    lines = ["[session_budget]"]
    if hours is not None:
        lines.append(f"max_hours = {hours}")
    if items is not None:
        lines.append(f"max_items = {items}")
    if risk is not None:
        lines.append(f"max_risk = {risk}")
    (tmp_path / "shantytown.toml").write_text("\n".join(lines) + "\n",
                                              encoding="utf-8")
    now = time.time()
    conn = sqlite3.connect(tmp_path / "stats.sqlite")
    conn.executescript(stats._SCHEMA)
    rows = []
    if spend_hours:
        # working density, so it reads as ONE stretch
        n = max(2, int(spend_hours * 3600 / 300))
        rows += [(now - spend_hours * 3600 + i * 300, agent, "tool", None)
                 for i in range(n)]
    rows.append((now - 30, agent, "tool", None))
    rows += [(now - 60 - i, agent, "haul", None) for i in range(spend_items)]
    rows += [(now - 120 - i, agent, "tool", "deploy") for i in range(spend_risk)]
    conn.executemany("INSERT INTO events(ts, agent, kind, session, risk)"
                     " VALUES (?,?,?,?,?)",
                     [(t, a, k, "s1", r) for t, a, k, r in rows])
    conn.commit()
    conn.close()
    return tmp_path


def _haul_at(monkeypatch, capsys, root, panes=None, reg=None, **bd):
    _bd(monkeypatch, **bd)
    rc = stop_event._haul(reg or _Reg([WORKER]), panes or _Panes({"p-b": "❯ "}),
                          "billy", root)
    out = capsys.readouterr().out.strip()
    return rc, (json.loads(out) if out else None)


READY = [{"id": "aegis-2", "title": "next up", "assignee": "billy"}]


def test_over_the_ceiling_the_haul_STOPS_instead_of_serving(tmp_path, monkeypatch,
                                                            capsys):
    root = _armed_root(tmp_path, spend_hours=7.0)
    claims = []
    rc, block = _haul_at(monkeypatch, capsys, root, ready=READY, claims=claims)
    assert rc == 0
    assert block["decision"] == "block"
    assert "SESSION CEILING" in block["reason"]
    assert "aegis-2" not in block["reason"], "must not hand over the withheld bead"
    assert claims == [], "an unserved bead must not be claimed in_progress"


def test_the_ceiling_blocks_once_then_lets_the_session_END(tmp_path, monkeypatch,
                                                           capsys):
    """The failure mode that would be WORSE than the bug: a Stop hook that blocks
    while the ceiling is over can never let the agent stop at all."""
    root = _armed_root(tmp_path, spend_hours=7.0)
    _rc, first = _haul_at(monkeypatch, capsys, root, ready=READY)
    assert first and "SESSION CEILING" in first["reason"]
    _rc, second = _haul_at(monkeypatch, capsys, root, ready=READY)
    assert second is None, "the second stop must be ALLOWED through"


def test_the_ceiling_outranks_the_context_handoff(tmp_path, monkeypatch, capsys):
    """Order matters: the handoff is a RECYCLE (shed context, haul resumes), so
    asking it first would send an over-ceiling session through /clear and
    straight back into the queue."""
    root = _armed_root(tmp_path, spend_hours=7.0)
    _rc, block = _haul_at(monkeypatch, capsys, root, ready=READY,
                          panes=_Panes({"p-b": SATURATED_PANE}))
    assert "SESSION CEILING" in block["reason"]
    assert "HAUL HANDOFF" not in block["reason"]


def test_under_the_ceiling_it_feeds_normally_WITH_the_headroom(tmp_path,
                                                               monkeypatch, capsys):
    root = _armed_root(tmp_path, spend_hours=1.0, spend_items=1)
    claims = []
    _rc, block = _haul_at(monkeypatch, capsys, root, ready=READY, claims=claims)
    assert "aegis-2" in block["reason"] and "HAUL" in block["reason"]
    assert "session budget" in block["reason"]
    assert "before you stop and report" in block["reason"]
    assert claims == ["aegis-2"]


def test_serving_an_item_COUNTS_it(tmp_path, monkeypatch, capsys):
    """Otherwise max_items can never trip — the counter the ceiling reads is
    written by the advance itself."""
    root = _armed_root(tmp_path, spend_hours=0.5)
    before = sb.read_spend(root, "billy").items
    _haul_at(monkeypatch, capsys, root, ready=READY)
    assert sb.read_spend(root, "billy").items == before + 1


def test_the_fourth_item_trips_a_four_item_ceiling(tmp_path, monkeypatch, capsys):
    """The incident's own number: four haul items back to back, unremarked."""
    root = _armed_root(tmp_path, hours=None, risk=None, items=4,
                       spend_hours=1.0, spend_items=4)
    _rc, block = _haul_at(monkeypatch, capsys, root, ready=READY)
    assert "SESSION CEILING" in block["reason"] and "haul items" in block["reason"]


def test_production_actions_trip_it_sooner_than_ordinary_work(tmp_path,
                                                              monkeypatch, capsys):
    """Bead item 2. Two deploys is over; the same session's clock and item count
    are nowhere near their ceilings."""
    root = _armed_root(tmp_path, spend_hours=0.5, spend_items=1, spend_risk=2)
    _rc, block = _haul_at(monkeypatch, capsys, root, ready=READY)
    assert "SESSION CEILING" in block["reason"]
    assert "production actions" in block["reason"]


def test_a_repeat_of_the_same_bead_is_FLAGGED_when_it_is_re_served(tmp_path,
                                                                   monkeypatch,
                                                                   capsys):
    """Bead item 4: the signal that was over-read twice."""
    root = _armed_root(tmp_path, spend_hours=0.5)
    _rc, first = _haul_at(monkeypatch, capsys, root, ready=READY)
    assert "SAME bead" not in first["reason"]
    _rc, again = _haul_at(monkeypatch, capsys, root, ready=READY)
    assert "SAME bead" in again["reason"]


def test_an_UNARMED_deployment_behaves_exactly_as_before(tmp_path, monkeypatch,
                                                         capsys):
    """Default-off by omission: a deployment that has never heard of this is
    untouched, including its message text."""
    claims = []
    _rc, block = _haul_at(monkeypatch, capsys, tmp_path, ready=READY, claims=claims)
    assert "aegis-2" in block["reason"] and "Yours to work." in block["reason"]
    assert "session budget" not in block["reason"]
    assert claims == ["aegis-2"]


def test_an_armed_budget_with_NO_signal_feeds_and_says_it_is_blind(tmp_path,
                                                                   monkeypatch,
                                                                   capsys):
    """A probe bug must never stop the crew — but it must never be silent either
    (the aegis-jrax3 rule). Armed + blind = fed, with an alarm on stderr."""
    (tmp_path / "shantytown.toml").write_text(
        "[session_budget]\nmax_hours = 3.0\n", encoding="utf-8")
    _bd(monkeypatch, ready=READY)
    stop_event._haul(_Reg([WORKER]), _Panes({"p-b": "❯ "}), "billy", tmp_path)
    cap = capsys.readouterr()
    assert "aegis-2" in cap.out, "blind must not block the haul"
    assert "SIGNAL LOST" in cap.err and "UNMEASURED" in cap.err


# --- a LEAD's haul must self-feed too (aegis-rvxcf1) -------------------------
#
# MEASURED three times in one afternoon: dearing (a CLAUDE lead) closed a bead,
# the next ready item sat in her own queue, and the pane went idle with an empty
# buffer until a coordinator ran `st go` by hand. Every haul advance cost one
# coordinator turn — exactly the per-bead dispatch a haul exists to remove.
#
# The cause was this function's role gate, which admitted only workers and codex
# leads. Its comment read "Claude leads keep their autonomous turn loop", and
# that premise is what the measurement falsifies: once the Stop hook allows the
# stop, there is no turn loop left to keep. A claude WORKER is fed precisely
# because nothing else would continue it, and a claude lead is no different.

def test_a_claude_lead_with_a_closed_anchor_IS_FED(monkeypatch, capsys):
    lead = Agent(name="billy", role="lead", pane="p-b", harness="claude")
    claims = []
    rc, block = _run(
        monkeypatch, capsys, reg=_Reg([lead]), claims=claims,
        ready=[{"id": "aegis-2", "title": "next up", "assignee": "billy"}],
    )
    assert rc == 0
    assert block is not None, "a lead with ready assigned work was left idle"
    assert "aegis-2" in block["reason"] and "HAUL" in block["reason"]
    assert claims == ["aegis-2"], "the fed bead is claimed in_progress"


def test_a_claude_lead_mid_work_is_still_silent(monkeypatch, capsys):
    """NEGATIVE CONTROL, and the reason the fix is a role gate change and not a
    harness one: an ACTIVE anchor is a turn boundary, not an idle agent. Claude
    owns its own loop mid-work, so that case must stay silent for leads exactly
    as it does for workers."""
    lead = Agent(name="billy", role="lead", pane="p-b", harness="claude")
    rc, block = _run(
        monkeypatch, capsys, reg=_Reg([lead]),
        ready=[{"id": "aegis-2", "assignee": "billy"}],
        in_progress=[{"id": "aegis-1", "assignee": "billy"}],
    )
    assert rc == 0 and block is None


# --- the CEILING vs the HAUL, composed (aegis-yeu49c) ------------------------
#
# MEASURED on malcolm, live, 2026-09-10. Its 4.0h ceiling tripped at 4.6h and
# said, correctly, "stop cleanly; do NOT pick up more work". It complied —
# committed, pushed, wrote the bead trail — and THE COMPLIANCE IS AN IDLE GAP,
# so the stretch rolled and `verdict` went quiet. The marker that should have
# carried the trip across that roll (aegis-hqbwci) had been written by a build
# that predated its `ceiling` key, so `held_ceiling` could not name what tripped
# and returned None, fail-open, as designed. The haul then served the next plate
# item on EVERY subsequent stop attempt, 32 beads deep, 7 of them P1. The only
# way to satisfy the haul was to do more work and the only way to satisfy the
# ceiling was to stop: the agent was required to violate one.
#
# WHY THESE LIVE AT THE _haul LEVEL AND NOT IN test_session_budget. Both halves
# pass their own tests today and did the night this happened. The nearest
# existing test — test_the_ceiling_blocks_once_then_lets_the_session_END — keeps
# the spend OVER the limit between stops, so it exercises `already_reported` and
# never reaches `held_ceiling` at all. A regression in `held_ceiling` is
# therefore invisible to it, and the second test below is what that regression
# actually looks like from here.

def _roll_the_stretch(root, agent="billy"):
    """The agent OBEYED: it stopped taking actions for long enough that the
    stretch rolled. Same store, same session id — only the old events fall out,
    which is what compliance with the stop instruction looks like in the stats
    store."""
    import sqlite3
    now = time.time()
    conn = sqlite3.connect(root / "stats.sqlite")
    conn.execute("DELETE FROM events WHERE ts < ?", (now - 120,))
    conn.execute("INSERT INTO events(ts, agent, kind, session, risk)"
                 " VALUES (?,?,?,?,?)", (now - 20, agent, "tool", "s1", None))
    conn.commit()
    conn.close()


def _drop_the_ceiling_key(root, agent="billy"):
    """Rewrite the marker into the shape malcolm's was ON DISK: session named,
    cause not recorded. This is what every marker written before the aegis-hqbwci
    fix looks like, and a marker is written once per session — so a session
    already running when a marker FORMAT changes carries the old shape for its
    whole life, and the new code never gets to read a marker it wrote itself."""
    m = root / "session_budget" / f"{agent}.json"
    d = json.loads(m.read_text(encoding="utf-8"))
    d.pop("ceiling", None)
    d.pop("spend", None)
    m.write_text(json.dumps(d), encoding="utf-8")


PLATE = [{"id": f"aegis-p{i}", "title": f"plate item {i}", "assignee": "billy"}
         for i in range(8)]                    # malcolm's condition is >5


def test_a_ROLLED_stretch_does_not_re_open_the_haul(tmp_path, monkeypatch,
                                                    capsys):
    """malcolm's falsifiable re-run: over the ceiling, a plate of 8, and the
    stretch rolls between stops because the agent did as it was told. It must
    terminate without the agent working past the ceiling AND without anything
    being falsely parked."""
    root = _armed_root(tmp_path, hours=4.0, items=None, risk=None,
                       spend_hours=4.6)
    claims = []
    _rc, first = _haul_at(monkeypatch, capsys, root, ready=PLATE, claims=claims)
    assert first and "SESSION CEILING" in first["reason"]

    _roll_the_stretch(root)
    assert sb.verdict(sb.limits_for(root), sb.read_spend(root, "billy")) is None, \
        "fixture check: the roll must silence the LIVE verdict, or this is vacuous"

    _rc, second = _haul_at(monkeypatch, capsys, root, ready=PLATE, claims=claims)
    _rc, third = _haul_at(monkeypatch, capsys, root, ready=PLATE, claims=claims)
    assert second is None and third is None, \
        "the haul re-served a plate item to an agent that was told to stop"
    assert claims == [], "nothing on the plate may be claimed after the ceiling"


def test_a_marker_that_cannot_name_its_cause_releases_but_SAYS_SO(tmp_path,
                                                                  monkeypatch,
                                                                  capsys):
    """NEGATIVE CONTROL for the test above — malcolm's exact on-disk artifact —
    and the one place the composed failure is still reachable.

    The release is deliberate and stays: inventing a measure for a marker that
    does not record one is worse than releasing one agent once. What changes is
    that it is no longer SILENT. A ceiling that has evaporated used to be
    indistinguishable from a session with room to spare, which is the aegis-jrax3
    rule one layer in, and it cost a whole night to find by reading two block
    reasons side by side."""
    root = _armed_root(tmp_path, hours=4.0, items=None, risk=None,
                       spend_hours=4.6)
    _rc, first = _haul_at(monkeypatch, capsys, root, ready=PLATE)
    assert first and "SESSION CEILING" in first["reason"]

    _drop_the_ceiling_key(root)
    _roll_the_stretch(root)
    claims = []
    _bd(monkeypatch, ready=PLATE, claims=claims)
    stop_event._haul(_Reg([WORKER]), _Panes({"p-b": "❯ "}), "billy", root)
    cap = capsys.readouterr()
    assert "HAUL" in cap.out, "fixture check: this arm is the one that DOES feed"
    assert claims == ["aegis-p0"]
    assert "ALREADY told to stop" in cap.err and "NOT in force" in cap.err, \
        "an evaporated ceiling must never be silent"


# --- the CODEX resume prompt is not exempt from the ceiling (aegis-yeu49c) ---
#
# The resume branch was exempt on the reasoning that it continues work already
# admitted rather than admitting a new item. That holds for the pane HANDOFF and
# does not hold for the ceiling, which bounds the SESSION rather than admission
# of a bead. The gate lives below the active-anchor branch, so an over-ceiling
# Codex worker mid-bead never reached it: it was told "Do not stop merely
# because the previous model turn ended", on a backoff, for the rest of the
# session, and was never told about its ceiling at all.

CODEX = Agent(name="billy", role="worker", pane="p-b", harness="codex")
ANCHOR = [{"id": "aegis-anchor", "title": "the bead in hand", "assignee": "billy"}]


def test_an_over_ceiling_codex_worker_is_told_to_STOP_not_to_resume(tmp_path,
                                                                    monkeypatch,
                                                                    capsys):
    root = _armed_root(tmp_path, hours=4.0, items=None, risk=None,
                       spend_hours=4.6)
    claims = []
    _rc, block = _haul_at(monkeypatch, capsys, root, reg=_Reg([CODEX]),
                          in_progress=ANCHOR, claims=claims)
    assert block and "SESSION CEILING" in block["reason"]
    assert "HAUL RESUME" not in block["reason"]
    assert "aegis-anchor stays claimed" in block["reason"], \
        "the anchor is already claimed — say so, or the plate reads as abandoned"
    assert claims == []

    _rc, second = _haul_at(monkeypatch, capsys, root, reg=_Reg([CODEX]),
                           in_progress=ANCHOR)
    assert second is None, "block-once: the session must be able to END"


def test_an_under_ceiling_codex_worker_still_gets_its_resume(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    """NEGATIVE CONTROL: without this the test above passes on a resume prompt
    that was simply deleted."""
    root = _armed_root(tmp_path, hours=4.0, items=None, risk=None,
                       spend_hours=1.0)
    _rc, block = _haul_at(monkeypatch, capsys, root, reg=_Reg([CODEX]),
                          in_progress=ANCHOR)
    assert block and "HAUL RESUME" in block["reason"]
    assert "aegis-anchor" in block["reason"]


def test_the_ceiling_does_not_spend_the_resume_BACKOFF(tmp_path, monkeypatch,
                                                       capsys):
    """Asked before _allow_haul_resume, so a session that is later released
    (the operator raises the limit) still has its full resume allowance."""
    root = _armed_root(tmp_path, hours=4.0, items=None, risk=None,
                       spend_hours=4.6)
    _haul_at(monkeypatch, capsys, root, reg=_Reg([CODEX]), in_progress=ANCHOR)
    assert not (root / "haul_resume" / "billy.json").exists()
