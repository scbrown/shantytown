"""st dashboard — a live, tier-scoped view that REUSES the state verdicts
(internal-ref, Part A).

The dashboard must never form a second opinion about busy/idle/waiting/saturated:
it is HANDED the crew-state tuples (the same `st crew` renders) and composes. These
tests pin the tier scoping, that the reused verdict is rendered verbatim, that
last-activity comes from the event ledger and is honest about unknowns, and that
the stats it cannot yet capture are NAMED, not faked.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import dashboard as dash, triage
from shantytown.protocols import Agent, WorkItem


def _agents():
    return [
        Agent(name="sattler", role="administrator", pane="p-sattler"),
        Agent(name="weaver", role="worker", reports_to="sattler", pane="p-weaver"),
        Agent(name="ellie", role="worker", reports_to="maldoon", pane="p-ellie"),
        Agent(name="maldoon", role="lead", reports_to="sattler", pane="p-maldoon"),
        Agent(name="outsider", role="worker", reports_to="other-admin", pane="p-out"),
    ]


# --- tier membership: transitive, and only this admin's ---------------------

def test_tier_is_the_admin_plus_its_transitive_reports():
    members = {a.name for a in dash.tier_of("sattler", _agents())}
    # sattler + direct (weaver, maldoon) + maldoon's report (ellie).
    assert members == {"sattler", "weaver", "maldoon", "ellie"}
    assert "outsider" not in members, "another admin's crew must not appear"


def test_a_reports_to_cycle_does_not_hang():
    a = [Agent(name="x", role="worker", reports_to="y"),
         Agent(name="y", role="worker", reports_to="x")]
    # neither reaches admin "z"; the walk terminates rather than looping.
    assert dash.tier_of("z", a) == []


# --- gather REUSES the verdict, does not re-derive --------------------------

def test_gather_renders_the_verdict_it_was_handed():
    agents = _agents()
    crew_states = [
        (agents[0], "up", triage.IDLE),
        (agents[1], "up", "saturated·687k"),      # a suffixed verdict, verbatim
        (agents[3], "up", triage.WAITING),
        (agents[2], "down", "—"),
    ]
    plate = lambda who: WorkItem(id="st-7", title="t", status="in_progress") if who == "weaver" else None
    d = dash.gather("sattler", agents, crew_states, plate, {}, at=1000.0)

    by = {r.name: r for r in d.rows}
    assert by["weaver"].work == "saturated·687k", "the verdict must be passed through unchanged"
    assert by["weaver"].item == "st-7"
    assert by["maldoon"].work == triage.WAITING
    # ellie is down -> no plate lookup, item None.
    assert by["ellie"].item is None and by["ellie"].pane_state == "down"
    # tallies come from the same verdicts.
    assert d.in_state(triage.SATURATED) == ["weaver"]
    assert d.in_state(triage.WAITING) == ["maldoon"]


# --- last activity: from the ledger, honest about unknowns ------------------

def test_last_activity_from_the_event_ledger_and_unknown_is_not_now():
    agents = _agents()
    crew_states = [(agents[0], "up", triage.IDLE), (agents[1], "up", triage.IDLE)]
    d = dash.gather("sattler", agents, crew_states,
                    lambda who: None, {"weaver": 950.0}, at=1000.0)
    out = dash.render(d, now=1000.0)
    assert "50s ago" in out              # weaver's last event, 50s before now
    # sattler has no event -> "—", never a fabricated recency.
    sattler_line = [l for l in out.splitlines() if l.strip().startswith("sattler")][0]
    assert "—" in sattler_line


# --- honest about the Part B gap --------------------------------------------

def test_render_names_the_uncaptured_stats_rather_than_faking_them():
    agents = _agents()
    d = dash.gather("sattler", agents, [(agents[0], "up", triage.IDLE)],
                    lambda who: None, {}, at=1000.0)
    out = dash.render(d, now=1000.0)
    assert "need capture" in out and "st stats" in out
    # it does NOT print a made-up throughput number.
    assert "throughput" in out


def test_age_formats():
    assert dash._age(None, 1000.0) == "—"
    assert dash._age(0, 1000.0) == "—"
    assert dash._age(1000.0, 1000.0) == "0s ago"
    assert dash._age(1000.0 - 120, 1000.0) == "2m ago"
    assert dash._age(1000.0 - 7200, 1000.0) == "2h ago"


# --- the events reader ------------------------------------------------------

def test_latest_by_sender_reads_the_store(tmp_path):
    from shantytown.events import FilesEvents
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="sattler", frm="weaver", reason=None, rose=False)
    # a second, later event from weaver -> latest wins.
    import time as _t
    e2 = ev.persist(to="sattler", frm="weaver", reason=None, rose=False)
    latest = FilesEvents(tmp_path / "events").latest_by_sender()
    assert "weaver" in latest and latest["weaver"] == e2.ts


def test_latest_by_sender_omits_unstamped_events(tmp_path):
    # an event written before timestamps (ts absent -> 0.0) must NOT read as recent.
    root = tmp_path / "events"; root.mkdir()
    (root / "ev-1.json").write_text(json.dumps(
        {"to": "sattler", "frm": "old", "reason": None, "rose": False, "delivered": False}))
    from shantytown.events import FilesEvents
    assert "old" not in FilesEvents(root).latest_by_sender()


# --- the command: resolve admin, refuse cleanly -----------------------------

class _Panes:
    def __init__(self, live):
        self._live = set(live)

    def exists(self, pane):
        return pane in self._live

    def capture(self, pane, history=0, attrs=False):
        return "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"


class _Args:
    def __init__(self, root, admin=None):
        self.root = Path(root)
        self.admin = admin; self.once = True; self.interval = 5
        self.registry = "files"; self.backend = None; self.repo = None


def _world(tmp_path, cards):
    crew = tmp_path / "crew"; crew.mkdir()
    for name, d in cards.items():
        (crew / f"{name}.json").write_text(json.dumps(d))
    (tmp_path / "settings").mkdir(); (tmp_path / "settings" / "tmux-socket").write_text("gt-ae5f35")
    return tmp_path


def test_dashboard_defaults_to_the_administrator(tmp_path, monkeypatch, capsys):
    from shantytown import cli
    root = _world(tmp_path, {
        "sattler": {"role": "administrator", "pane": "p-sattler"},
        "weaver": {"role": "worker", "reports_to": "sattler", "pane": "p-weaver"}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes({"p-sattler", "p-weaver"}))
    rc = cli._cmd_dashboard(_Args(root, admin=None))
    assert rc == cli.OK
    out = capsys.readouterr().out
    assert "TIER OF sattler" in out
    assert "weaver" in out and "sattler" in out


def test_dashboard_refuses_an_unknown_admin(tmp_path, monkeypatch, capsys):
    from shantytown import cli
    root = _world(tmp_path, {"sattler": {"role": "administrator", "pane": "p-s"}})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(set()))
    rc = cli._cmd_dashboard(_Args(root, admin="nobody"))
    assert rc == cli.REFUSED
    assert "no such agent: nobody" in capsys.readouterr().err
