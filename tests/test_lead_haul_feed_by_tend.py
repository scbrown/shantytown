"""tend must feed an idle LEAD its own ready haul — on every harness.

aegis-n9f2pc, the twin of aegis-rvxcf1 one trigger over.

rvxcf1 widened the STOP-hook advance from "workers and CODEX leads" to
worker+lead on every harness, on measurements showing a claude lead does not
keep an autonomous turn loop past its Stop hook. The TEND advance carried the
identical premise and was not widened with it — so the fix closed the stop path
and left the BACKUP path with the same hole, which is worse, because the backup
exists for precisely the case the stop path cannot cover:

  1. a non-empty haul EXCLUDES an agent from the coordinator's feedable list;
  2. the stop advance runs only AT a stop;
  3. an already-idle agent never stops again.

A haul that becomes ready AFTER the last stop is therefore unreachable from
every direction at once.

MEASURED 2026-09-02: dearing idle at 19:31Z with aegis-2b2tti ready and assigned;
the 19:33:45Z tend pass reported "13 agent(s) · acted on 0" and the bead stayed
OPEN until a coordinator typed `st go`. Confirmed by walking feed_check's six
gates per agent on the live fleet: `wu`, an idle lead, failed **only** the role
gate — wired, launch-stamped, not retired, not dark, verdict idle.
"""
from __future__ import annotations
from types import SimpleNamespace

from shantytown.answer import Answer
from shantytown import feed_check, input_box, triage
from shantytown.notify import IdleFleetAlerter
from shantytown.protocols import Agent


class _Reg:
    def __init__(self, agents):
        self._a = {x.name: x for x in agents}

    def all(self):
        return Answer.complete_read(list(self._a.values()), how="test registry")

    def get(self, name):
        return self._a[name]


class _Panes:
    def __init__(self, live):
        self._live = set(live)
        self.sent = []

    def exists(self, pane):
        return pane in self._live

    def send(self, pane, text):
        self.sent.append((pane, text))

    def capture(self, pane, attrs=False):
        return "❯ \n"                      # idle: UI up, nothing in flight

    def cmdline(self, pane):
        return "claude --settings x"


class _Runtime:
    def shows_ready_ui(self, plain):
        return True


# --- the gate itself ---------------------------------------------------------

def _roster():
    return _Reg([Agent(name="sattler", role="administrator", pane="p-admin"),
                 Agent(name="wu", role="lead", pane="p-wu"),
                 Agent(name="billy", role="worker", pane="p-billy")])


def _gate_world(monkeypatch):
    monkeypatch.setattr(feed_check, "dark_agents", lambda: set())
    monkeypatch.setattr(feed_check, "st_launched_agents", lambda root: None)
    monkeypatch.setattr("shantytown.tend.is_retired", lambda ag: False)
    monkeypatch.setattr("shantytown.runtime.asks_a_question", lambda rt, p: False)
    monkeypatch.setattr("shantytown.runtime.auth_expired", lambda rt, p: False)
    monkeypatch.setattr("shantytown.runtime.live_wiring", lambda pane, cmdline:
                        SimpleNamespace(directions={"send", "haul"},
                                        settings_path="x"))
    monkeypatch.setattr(triage, "work_state",
                        lambda *a, **k: triage.IDLE)
    return _roster(), _Panes({"p-admin", "p-wu", "p-billy"}), _Runtime()


def test_idle_haulable_leads_returns_the_lead_and_only_the_lead(monkeypatch):
    reg, panes, rt = _gate_world(monkeypatch)
    assert feed_check.idle_haulable_leads(reg, panes, rt) == ["wu"], (
        "an idle lead must be reachable by the haul feed")


def test_free_feedable_workers_is_byte_for_byte_UNCHANGED(monkeypatch):
    """The control that keeps this scoped. `free` drives the Rule Zero
    coordinator alert and the hard dispatch gate; a lead appearing there would
    start telling the coordinator to hand a lead somebody ELSE's unassigned
    bead, which is not what a haul is."""
    reg, panes, rt = _gate_world(monkeypatch)
    assert feed_check.free_feedable_workers(reg, panes, rt) == ["billy"]


def test_an_administrator_is_never_haul_fed(monkeypatch):
    """Same reasoning as rvxcf1: the coordinator's assigned beads are not a
    haul, and feeding them puts the person who dispatches work head-down in a
    bead."""
    reg, panes, rt = _gate_world(monkeypatch)
    assert "sattler" not in feed_check.idle_haulable_leads(reg, panes, rt)
    assert "sattler" not in feed_check.free_feedable_workers(reg, panes, rt)


def test_the_other_five_gates_still_apply_to_a_lead(monkeypatch):
    """A lead is admitted by ROLE, not exempted from everything else. Without
    this, 'let leads through' could be satisfied by a gate that lets a retired
    or unwired lead through too."""
    reg, panes, rt = _gate_world(monkeypatch)
    monkeypatch.setattr("shantytown.tend.is_retired",
                        lambda ag: ag.name == "wu")
    assert feed_check.idle_haulable_leads(reg, panes, rt) == []


# --- the sweep: the bead's acceptance shape ---------------------------------

def _lead_hauling_world(tmp_path, monkeypatch, claims):
    """A claude LEAD, idle, with one ready bead assigned to it — dearing at
    19:31Z, exactly."""
    reg = _Reg([Agent(name="sattler", role="administrator", pane="p-admin"),
                Agent(name="dearing", role="lead", pane="p-dearing")])
    panes = _Panes({"p-admin", "p-dearing"})
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers",
                        lambda *a, **k: [])          # workers-only: finds nobody
    monkeypatch.setattr("shantytown.feed_check.idle_resumable_codex",
                        lambda *a, **k: [])          # codex-only: finds nobody
    monkeypatch.setattr("shantytown.feed_check.idle_haulable_leads",
                        lambda *a, **k: ["dearing"])
    monkeypatch.setattr("shantytown.feed_check.bd_cwd", lambda reg: None)
    monkeypatch.setattr("shantytown.feed_check.bd_claim",
                        lambda cwd, nid, **kw: claims.append(nid))
    ready = [{"id": "aegis-2b2tti", "title": "the bead that sat OPEN",
              "assignee": "beads_aegis/crew/dearing"}]
    return IdleFleetAlerter(
        tmp_path, reg, panes, runtime=None,
        bd_ready=lambda: ready, bd_in_progress=lambda cwd: [],
        context_k=lambda w: None,
        input_preflight=lambda _w: SimpleNamespace(verdict=input_box.EMPTY,
                                                   detail="test"),
        log=lambda m: None), panes


def test_an_idle_claude_LEAD_is_fed_its_ready_haul(tmp_path, monkeypatch):
    """The regression. Before this, `free` (workers-only) and
    idle_resumable_codex (codex-only) were tend's ONLY two inputs, so this lead
    was in neither and tend reported 'acted on 0'."""
    claims = []
    alerter, panes = _lead_hauling_world(tmp_path, monkeypatch, claims)
    assert alerter.sweep([]) == ["dearing"]
    targets = [p for p, _ in panes.sent]
    assert "p-dearing" in targets, "the lead must be fed its own next bead"
    assert "p-admin" not in targets, "no coordinator turn — that is the point"
    (_, msg), = [x for x in panes.sent if x[0] == "p-dearing"]
    assert "aegis-2b2tti" in msg
    assert claims == ["aegis-2b2tti"], "the fed bead is claimed in_progress"


def test_the_lead_feed_is_once_per_idle_episode(tmp_path, monkeypatch):
    """Same self-termination as the worker path: a 5-minute timer must not
    re-type into a lead's pane every cycle."""
    alerter, panes = _lead_hauling_world(tmp_path, monkeypatch, [])
    alerter.sweep([]); alerter.sweep([]); alerter.sweep([])
    assert len(panes.sent) == 1


# --- tend must say the same thing the worker's own stop says (aegis-yeu49c) --
#
# The ceiling gate above refuses to feed a worker that is over budget, and says
# so in the log. It cannot refuse when the ceiling has EVAPORATED — a marker
# that names this session but does not record what tripped releases the worker,
# fail-open and deliberately (see session_budget.unholdable_note). What must not
# happen is that the release is silent HERE while the worker's own stop hook
# announces it on stderr: two surfaces disagreeing about whether a ceiling is in
# force is the aegis-qviejh failure, and it is how this cost a whole night.

def _legacy_marker_world(tmp_path, monkeypatch, claims, spend_hours=1.0):
    """A lead under its ceiling whose marker says it was ALREADY told to stop in
    this session, in the shape every marker written before aegis-hqbwci has."""
    import json as _json
    import sqlite3
    import time as _time
    from shantytown import stats
    (tmp_path / "shantytown.toml").write_text(
        "[session_budget]\nmax_hours = 4.0\n", encoding="utf-8")
    now = _time.time()
    conn = sqlite3.connect(tmp_path / "stats.sqlite")
    conn.executescript(stats._SCHEMA)
    n = max(2, int(spend_hours * 3600 / 300))
    rows = [(now - spend_hours * 3600 + i * 300, "dearing", "tool", "s1", None)
            for i in range(n)]
    rows.append((now - 30, "dearing", "tool", "s1", None))
    conn.executemany("INSERT INTO events(ts, agent, kind, session, risk)"
                     " VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    m = tmp_path / "session_budget" / "dearing.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(_json.dumps({"started": now - spend_hours * 3600,
                              "at": now - 60, "session": "s1"}),
                 encoding="utf-8")
    alerter, panes = _lead_hauling_world(tmp_path, monkeypatch, claims)
    return alerter, panes


def test_tend_ANNOUNCES_a_ceiling_it_can_no_longer_hold(tmp_path, monkeypatch):
    logged = []
    claims = []
    alerter, panes = _legacy_marker_world(tmp_path, monkeypatch, claims)
    alerter._log = logged.append
    assert alerter.sweep([]) == ["dearing"], "the release is real — it IS fed"
    assert claims == ["aegis-2b2tti"]
    assert any("ALREADY told to stop" in m and "NOT in force" in m
               for m in logged), \
        "tend fed a worker whose ceiling had evaporated and said nothing"


def test_tend_says_NOTHING_when_the_marker_is_for_another_session(tmp_path,
                                                                  monkeypatch):
    """NEGATIVE CONTROL. Without it the assertion above is satisfied by a note
    that fires on every feed, which would be noise rather than a signal."""
    import json as _json
    logged = []
    alerter, panes = _legacy_marker_world(tmp_path, monkeypatch, [])
    m = tmp_path / "session_budget" / "dearing.json"
    d = _json.loads(m.read_text(encoding="utf-8"))
    d["session"] = "a-different-session"
    m.write_text(_json.dumps(d), encoding="utf-8")
    alerter._log = logged.append
    assert alerter.sweep([]) == ["dearing"]
    assert not any("ALREADY told to stop" in m for m in logged)
