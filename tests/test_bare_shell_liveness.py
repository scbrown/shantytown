"""A surviving login shell must not hide behind old UI or a cycle refusal."""
import json
from types import SimpleNamespace

import pytest

from shantytown import cli, stop_event, tend, workflow
from shantytown.events import FilesEvents
from shantytown.protocols import Agent, WorkItem
from shantytown.tmux import NullPanes, SHELL_COMMANDS, shell_foreground
from tests.test_crew_work import _Args, _roster

STALE_UI = "✻ Working… (12s · esc to interrupt)\n❯ \n? for shortcuts"
RUNTIME = SimpleNamespace(name="claude", shows_ready_ui=lambda screen: True)
CARD = Agent(name="probe", role="worker", pane="p-probe")
ITEM = WorkItem("fixture", "held work", "in_progress", "probe")


class Panes(NullPanes):
    def __init__(self, foreground):
        super().__init__(live={"p-probe", "p-chief"},
                         screen=STALE_UI, foreground_cmd=foreground)

    def foreground(self, pane):
        return self.foreground_cmd


def finding(panes, card=CARD, **kwargs):
    return tend.Tender(panes, RUNTIME, SimpleNamespace(verdict=lambda n: "current"),
                       codex_block=lambda c: None, **kwargs).pass_over([card])


@pytest.mark.parametrize("shell", sorted(SHELL_COMMANDS))
def test_positive_shell_beats_stale_runtime_ui_and_cycle_refusal(shell):
    panes = Panes(shell)
    row = list(cli._crew_states([CARD], panes, RUNTIME,
                              cycle_blocked={"probe": ("dirty-tree", "refused")}))[0]
    assert row[1:] == ("down", "—", "—")
    report = finding(panes)
    assert report.findings[0].state == "down"
    assert report.findings[0].verdict == tend.RUNTIME_EXITED
    assert not report.healthy()
    assert report.acted == []
    assert panes.sent == panes.respawned == []
    assert panes.exists("p-probe")
    candidate = workflow.classify([CARD], panes, lambda n: ITEM)[0]
    assert candidate.state == workflow.AgentState.STOPPED
    assert [s.action for s in workflow.prioritize([candidate]).steps] == ["re-dispatch"]


@pytest.mark.parametrize("foreground", ["claude", "codex", "node", "python", None])
def test_runtime_or_unidentified_foreground_is_never_declared_down(foreground):
    panes = Panes(foreground)
    row = list(cli._crew_states([CARD], panes, RUNTIME,
                              cycle_blocked={"probe": ("dirty-tree", "refused")}))[0]
    assert row[1] == "cycle-blocked"
    assert row[2].startswith("busy")
    assert finding(panes).findings[0].state == "up"
    assert workflow.classify([CARD], panes, lambda n: ITEM)[0].state == workflow.AgentState.WORKING


def test_missing_or_failed_optional_reader_cannot_prove_exit():
    panes = NullPanes(live={"p-probe"}, screen=STALE_UI)
    assert shell_foreground(panes, "p-probe") is None
    assert list(cli._crew_states([CARD], panes, RUNTIME))[0][1] == "up"
    class Failed(Panes):
        def foreground(self, pane):
            raise PermissionError("probe unavailable")
    panes = Failed("bash")
    assert list(cli._crew_states([CARD], panes, RUNTIME))[0][1] == "up"
    assert finding(panes).findings[0].state == "up"


def test_genuine_cycle_launch_interval_preserves_cycling():
    assert list(cli._crew_states([CARD], Panes("bash"), RUNTIME,
                                cycling={"probe"}))[0][1] == "cycling"


def test_missing_pane_cannot_remain_cycle_blocked():
    panes = NullPanes(live=set())
    assert list(cli._crew_states([CARD], panes, RUNTIME,
                                cycle_blocked={"probe"}))[0][1] == "down"


def test_crew_json_live_flag_agrees_with_positive_shell_down(tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"probe": "p-probe"})
    monkeypatch.setattr(cli, "Tmux", lambda **kwargs: Panes("bash"))
    args = _Args(root)
    args.json, args.local = True, True
    assert cli._cmd_crew(args) == cli.OK
    row = json.loads(capsys.readouterr().out)["agents"][0]
    assert row["state"] == "down"
    assert row["live"] is False
    assert row["work"] == "—"


def test_admin_drain_reports_shell_exit_even_with_held_work(tmp_path, capsys):
    chief = Agent(name="chief", role="administrator", pane="p-chief")
    cards = {"chief": chief, "probe": CARD}
    registry = SimpleNamespace(get=cards.__getitem__,
                               all=lambda: SimpleNamespace(exact=lambda: list(cards.values())))
    panes = Panes("bash")
    events = FilesEvents(tmp_path / "events")
    events.persist(to="chief", frm="probe", reason="task-finished", rose=False)
    assert stop_event._liveness(registry, panes, lambda screen: True, "probe") == stop_event.DOWN
    assert stop_event._drain(events, "chief", registry, panes, lambda screen: True,
                             plate=lambda name: ITEM, rank=None) == 0
    out = json.loads(capsys.readouterr().out)["reason"]
    assert "re-dispatch probe — STOPPED" in out


def test_operator_stop_and_retirement_are_preserved():
    panes = Panes("bash")
    stopped = SimpleNamespace(get=lambda n: SimpleNamespace(by="operator", at=1))
    report = finding(panes, stops=stopped)
    assert report.findings[0].verdict == tend.STOPPED
    assert report.healthy() and not report.acted
    retired = Agent(name="probe", role="worker", pane="p-probe", retired=True)
    report = finding(panes, card=retired)
    assert report.findings[0].verdict == tend.RETIRED
    assert report.findings[0].state == "down"
    assert report.healthy() and not report.acted
