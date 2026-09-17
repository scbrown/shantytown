"""st fleet dashboard — a live, tier-scoped view that REUSES the state verdicts
(aegis-h4qe, Part A).

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
    assert "need capture" in out and "st agent stats" in out
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
    (tmp_path / "shantytown.toml").write_text(
        '[tmux]\nsocket = "gt-ae5f35"\n')
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


def test_title_is_retained_and_viewport_can_reach_both_ends():
    title = 'START ' + 'long assigned work ' * 12 + 'FINISH'
    agents = _agents()
    d = dash.gather('sattler', agents, [(agents[0], 'up', triage.BUSY)],
                    lambda _: WorkItem(id='st-9', title=title), {}, at=1000)
    assert d.rows[0].title == title
    first = dash.render(d, 1000, width=80)
    middle = dash.render(d, 1000, width=80, offset=20)
    last = dash.render(d, 1000, width=80, offset=10000)
    assert 'START' in first and 'FINISH' not in first and '…' in first
    row = next(x for x in middle.splitlines() if x.startswith(' sattler'))
    assert '‹' in row and row.endswith('…')
    row = next(x for x in last.splitlines() if x.startswith(' sattler'))
    assert '‹' in row and row.endswith('FINISH') and not row.endswith('…')
    assert title in dash.render(d, 1000)


def test_windows_mark_overflow_fit_terminal_cells_and_keep_combining_marks():
    text = 'a\u0301界' * 10 + 'END'
    for width in range(1, 30):
        for offset in (0, 1, 4, 999):
            out = dash.text_window(text, width, offset)
            assert sum(w for _, w in dash._cells(out)) <= width
            assert not out.startswith('\u0301')
    assert dash.text_window('short', 20, 999) == 'short'
    assert dash.text_window('abc', 3) == 'abc'
    assert dash.text_window('abcdef', 3) == 'ab…'
    assert dash.text_window('abcdef', 3, 999) == '‹ef'
    assert '\x1b' not in dash.text_window('a\x1b[2J\nb', None)
    assert '\n' not in dash.text_window('a\nb', None)


class _Screen:
    def __init__(self, keys):
        self.keys = iter(keys)
        self.frames = []
        self.lines = {}

    def keypad(self, enabled):
        assert enabled

    def getmaxyx(self):
        return 20, 80

    def erase(self):
        self.lines = {}

    def addstr(self, y, x, text):
        self.lines[y] = text

    def refresh(self):
        self.frames.append('\n'.join(self.lines.values()))

    def timeout(self, value):
        assert value > 0

    def getch(self):
        return next(self.keys)


def test_keyboard_scrolls_cached_snapshot_home_end_and_left():
    import curses
    title = 'START ' + 'work ' * 50 + 'FINISH'
    d = dash.Dashboard('lead', [dash.Row('worker', 'worker', 'up', 'busy',
                                        'st-1', 'open', None, title)], at=1000)
    calls = []
    def snapshot():
        calls.append(1)
        return 0, d
    screen = _Screen([curses.KEY_RIGHT, curses.KEY_END, curses.KEY_LEFT,
                      curses.KEY_HOME, ord('q')])
    assert dash._watch(screen, snapshot, 3600) == 0
    assert len(calls) == 1, 'keypresses must not requery the tracker'
    rows = [next(l for l in frame.splitlines() if l.startswith(' worker'))
            for frame in screen.frames]
    assert 'START' in rows[0] and rows[0].endswith('…')
    assert rows[1] != rows[0] and '‹' in rows[1]
    assert rows[2].endswith('FINISH') and '‹' in rows[2]
    assert rows[3] != rows[2]
    assert rows[4] == rows[0]


def test_dashboard_interactive_dispatch_and_interrupt(tmp_path, monkeypatch):
    import io
    from shantytown import cli
    a = _Args(tmp_path)
    a.once = False
    monkeypatch.setattr(cli, '_registry', lambda a: object())
    monkeypatch.setattr(cli, '_panes', lambda a: object())
    monkeypatch.setattr(cli, '_runtime', lambda a, p: object())
    class TTY(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(cli.sys, 'stdin', TTY())
    monkeypatch.setattr(cli.sys, 'stdout', TTY())
    def interrupted(snapshot, interval):
        raise KeyboardInterrupt
    monkeypatch.setattr(dash, 'watch', interrupted)
    assert cli._cmd_dashboard(a) == cli.OK
