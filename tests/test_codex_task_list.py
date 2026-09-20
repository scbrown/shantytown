"""New-task chrome must never receive a message meant for an existing task."""
import json

import pytest

from shantytown import cli, harness, input_box, tmux
from shantytown.runtime import ClaudeRuntime

# Fixture from the reported footer in aegis-4f0zje, not a full live capture.
TASK_LIST = "New task\n› Describe a new task\n  esc tasks"
TURN = "Working on the task\n› \n  gpt-6 high · /workspace"


@pytest.mark.parametrize("screen", [TASK_LIST, TASK_LIST + "\n" * 25,
    "\x1b[2m" + TASK_LIST + "\x1b[0m",
    "New task\n› already typed\n  esc tasks"])
def test_task_screen_cannot_receive_or_edit_input(screen):
    panes = tmux.NullPanes(screen=screen)
    with pytest.raises(tmux.PaneTaskList, match="task list"):
        panes.send("%1", "continue")
    for action in (input_box.show, input_box.clear, input_box.dismiss):
        assert action(panes, "%1").verdict == input_box.TASK_LIST
    assert panes.sent == []
    assert panes.controls == []
    rt = ClaudeRuntime(panes, lambda _: None)
    assert not rt.shows_ready_ui(TURN.splitlines()[-1] + "\n" + screen)
    assert not rt.is_live(screen)


def test_scrollback_does_not_hide_normal_turn():
    screen = TASK_LIST + "\n" + "\n".join(["output"] * 15) + "\n" + TURN
    assert not harness.task_list_evidence(screen)
    panes = tmux.NullPanes(screen=screen)
    panes.send("%1", "continue")
    assert panes.sent == [("%1", "continue")]
    assert ClaudeRuntime(panes, lambda _: None).shows_ready_ui(screen)


@pytest.mark.parametrize("screen,refused", [(TASK_LIST, True), (TURN, False)])
def test_real_transport_refuses_before_any_keystroke(monkeypatch, screen, refused):
    calls = []
    panes = tmux.Tmux()
    monkeypatch.setattr(panes, "foreground", lambda _: "codex")
    monkeypatch.setattr(panes, "capture", lambda _: screen)
    monkeypatch.setattr(tmux, "_journal_send", lambda *_: None)
    monkeypatch.setattr(tmux.time, "sleep", lambda _: None)
    monkeypatch.setattr(tmux.subprocess, "run", lambda argv, **kw: calls.append(argv))
    if refused:
        with pytest.raises(tmux.PaneTaskList):
            panes.send("%1", "continue")
        assert calls == []
    else:
        panes.send("%1", "continue")
        assert any("send-keys" in c for c in calls)
        assert calls[-1][-1] == "Enter"


def _root(tmp_path):
    root = tmp_path / "root"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "worker.json").write_text(json.dumps(
        {"role": "worker", "harness": "codex", "pane": "%1"}))
    return root


def test_inbox_refuses_and_durable_preserves_pointer(tmp_path, monkeypatch, capsys):
    from shantytown.inbox import Message
    class Box:
        delivered = []
        marked = []
        def deliver(self, to, body, frm=None):
            self.delivered.append(body)
            return Message(id="st-1", to=to, body=body, frm=frm)
        def mark_read(self, *args, **kwargs):
            self.marked.append(args)
    box = Box()
    panes = tmux.NullPanes(screen=TASK_LIST)
    monkeypatch.setattr(cli, "Tmux", lambda *a, **kw: panes)
    monkeypatch.setattr(cli, "_inbox", lambda *a, **kw: box)
    root = _root(tmp_path)
    args = ["--root", str(root), "inbox"]
    assert cli.main(args + ["worker", "continue"]) == cli.REFUSED
    assert "task list" in capsys.readouterr().err
    assert cli.main(args + ["-d", "worker", "continue"]) == cli.OK
    assert len(box.delivered) == 1
    assert box.marked == []
    assert panes.sent == []


def test_agent_input_exposes_task_list(tmp_path, monkeypatch, capsys):
    panes = tmux.NullPanes(screen=TASK_LIST)
    monkeypatch.setattr(cli, "Tmux", lambda *a, **kw: panes)
    root = _root(tmp_path)
    args = ["--root", str(root), "agent", "input", "worker"]
    assert cli.main(args) == cli.OK
    assert "TASK-LIST" in capsys.readouterr().out
    assert cli.main(args + ["--clear"]) == cli.REFUSED
    assert cli.main(args + ["--dismiss"]) == cli.REFUSED
    assert panes.controls == []
