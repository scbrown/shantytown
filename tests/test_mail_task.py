"""st mail is send-keys. st task creates. Both must be able to REFUSE."""
import json
from pathlib import Path
import pytest
from shantytown.cli import main, OK, REFUSED, CANNOT_TELL
from shantytown.files import FilesTracker


def _root(tmp_path: Path, pane="%1") -> Path:
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "ian.json").write_text(json.dumps({"role": "worker", "pane": pane}))
    (root / "crew" / "nopane.json").write_text(json.dumps({"role": "worker"}))
    return root


def test_mail_is_send_keys_and_nothing_else(tmp_path, monkeypatch):
    """The entire implementation is one send-keys. No bus, no queue, no store."""
    sent = []
    import shantytown.cli as cli
    class FakeTmux:
        def exists(self, pane): return True
        def send(self, pane, text): sent.append((pane, text))
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: FakeTmux())
    rc = main(["--root", str(_root(tmp_path)), "mail", "ian", "go", "read", "st-1"])
    assert rc == OK
    assert sent == [("%1", "go read st-1")]


def test_mail_refuses_unknown_agent(tmp_path):
    assert main(["--root", str(_root(tmp_path)), "mail", "nobody", "hi"]) == REFUSED


def test_mail_refuses_agent_with_no_pane(tmp_path):
    assert main(["--root", str(_root(tmp_path)), "mail", "nopane", "hi"]) == REFUSED


def test_mail_cannot_tell_when_pane_is_gone(tmp_path, monkeypatch):
    """A queue accepts a message for a reader that will never come; send-keys
    cannot. 47 nudges sat queued for a mayor that does not exist.
    An absent pane is CANNOT_TELL — never a cheerful success."""
    import shantytown.cli as cli
    class GoneTmux:
        def exists(self, pane): return False
        def send(self, pane, text): raise AssertionError("must not send into the void")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: GoneTmux())
    assert main(["--root", str(_root(tmp_path)), "mail", "ian", "hi"]) == CANNOT_TELL


def test_mail_dry_run_sends_nothing(tmp_path, monkeypatch):
    import shantytown.cli as cli
    class Boom:
        def exists(self, pane): raise AssertionError("dry-run must not touch tmux")
        def send(self, pane, text): raise AssertionError("dry-run must not send")
    monkeypatch.setattr(cli, "Tmux", lambda *a, **k: Boom())
    assert main(["--root", str(_root(tmp_path)), "mail", "ian", "-n", "hi"]) == OK


def test_task_creates_and_returns_an_id(tmp_path):
    root = _root(tmp_path)
    assert main(["--root", str(root), "task", "fix", "the", "thing"]) == OK
    items = list((root / "items").glob("*.json"))
    assert len(items) == 1
    assert json.loads(items[0].read_text())["title"] == "fix the thing"


def test_task_dry_run_writes_nothing(tmp_path):
    root = _root(tmp_path)
    assert main(["--root", str(root), "task", "-n", "nope"]) == OK
    assert not (root / "items").exists(), "dry-run created the items dir"


def test_task_ids_do_not_collide(tmp_path):
    t = FilesTracker(tmp_path / "items")
    ids = {t.create(f"item {i}").id for i in range(5)}
    assert len(ids) == 5
