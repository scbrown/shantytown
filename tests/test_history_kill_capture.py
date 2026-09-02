"""Capture transcripts on the KILL paths, where no Stop hook ever fires.

aegis-ay3gv2, the residue of aegis-xfmon3 step 3.

The transcript archiver is a Stop hook, so it fires on a NATURAL turn end. Three
paths take a runtime down without one: `st stop`, the cycle `st tend` performs
THROUGH `st stop`, and the auth-dead relaunch. While the `*/30` capture timer
existed it covered them at a 30-minute worst case. Retiring that timer once the
hook was proven was right for the natural-stop path and left these three with
nothing behind them.

That is survivable for claude (durable disk; a later capture still finds it) and
NOT for codex, whose CODEX_HOME is under /run/user/<uid> -- measured tmpfs. An
uncaptured rollout is destroyed by the next reboot, which is the unrecoverable
session this whole epic exists for.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import cli
from shantytown.tmux import NullPanes


def _world(tmp_path: Path, pane="crew-ellie"):
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps({"role": "worker", "pane": pane}))
    return tmp_path


class _Args:
    def __init__(self, **kw):
        self.root = kw.pop("root")
        self.agent = kw.pop("agent", "ellie")
        self.dry_run = kw.pop("dry_run", False)
        self.backend = "files"; self.repo = None
        for k, v in kw.items():
            setattr(self, k, v)


class _OrderedPanes(NullPanes):
    """Records the kill against a shared timeline, so ORDER is assertable."""
    def __init__(self, order, **kw):
        super().__init__(**kw)
        self._order = order

    def kill_session(self, name):
        self._order.append(f"kill:{name}")
        super().kill_session(name)


def _fake_capture_script(tmp_path, body="#!/bin/sh\nexit 0\n"):
    """A stand-in for the real capture script, at the layout the code expects."""
    src = tmp_path / "canonical"; (src / "scripts").mkdir(parents=True)
    script = src / "scripts" / "st-history-capture.sh"
    script.write_text(body); script.chmod(0o755)
    return src


def test_the_capture_runs_BEFORE_the_session_is_killed(tmp_path, monkeypatch):
    """The whole point. After the kill, a codex rollout on tmpfs is already gone,
    so a capture that runs afterwards archives nothing and reports success."""
    order: list[str] = []
    monkeypatch.setattr(cli, "_capture_history_before_kill",
                        lambda a, name, why: order.append(f"capture:{name}:{why}"))
    panes = _OrderedPanes(order, live={"crew-ellie"}, owned={"crew-ellie"})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)

    assert cli._cmd_stop(_Args(root=_world(tmp_path))) == cli.OK

    assert order == ["capture:ellie:stop", "kill:crew-ellie"], order


def test_a_capture_that_fails_never_changes_the_stop_verdict(
        tmp_path, monkeypatch, capsys):
    """An archiver that can refuse a stop is an archiver holding a shutdown
    hostage. The point of a deliberate stop is that it happens."""
    src = _fake_capture_script(tmp_path, "#!/bin/sh\nexit 3\n")
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(
        live={"crew-ellie"}, owned={"crew-ellie"}))
    from shantytown import runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "canonical_source", lambda *a, **k: str(src))

    root = _world(tmp_path)
    assert cli._cmd_stop(_Args(root=root)) == cli.OK
    assert "stopped ellie" in capsys.readouterr().out
    # and it still recorded the attempt, with the failing rc
    line = (Path(root) / "history" / "kill-capture.log").read_text()
    assert "ellie" in line and "rc=3" in line


def test_an_exploding_capture_never_changes_the_stop_verdict(
        tmp_path, monkeypatch, capsys):
    """Negative control on the one above: not merely a non-zero exit, but the
    call itself raising."""
    src = _fake_capture_script(tmp_path)
    from shantytown import runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "canonical_source", lambda *a, **k: str(src))
    import subprocess

    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="capture", timeout=30)
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(
        live={"crew-ellie"}, owned={"crew-ellie"}))

    assert cli._cmd_stop(_Args(root=_world(tmp_path))) == cli.OK
    assert "TimeoutExpired" in capsys.readouterr().err


def test_the_kill_capture_is_logged_APART_from_hook_log(tmp_path, monkeypatch):
    """st-history-timer-gate.py reads hook.log to decide whether the HOOK is
    live. A kill-capture written there would read as a hook fire and destroy the
    one instrument that can tell 'the hook works' from 'something else covers
    for it' -- which is exactly how the timer was safely retired."""
    src = _fake_capture_script(tmp_path)
    from shantytown import runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "canonical_source", lambda *a, **k: str(src))
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(
        live={"crew-ellie"}, owned={"crew-ellie"}))

    root = _world(tmp_path)
    assert cli._cmd_stop(_Args(root=root)) == cli.OK

    hist = Path(root) / "history"
    assert (hist / "kill-capture.log").is_file(), "the kill capture was not logged"
    assert not (hist / "hook.log").exists(), \
        "a kill-capture was written to hook.log — the timer gate is now blind"


def test_an_unknown_checkout_is_REPORTED_not_silently_skipped(
        tmp_path, monkeypatch, capsys):
    """No guessed path -- same doctrine as the hook. But silence would leave the
    operator believing a capture happened, so the skip is said out loud."""
    # the suite's ambient-checkout guard already pins canonical_source to None
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(
        live={"crew-ellie"}, owned={"crew-ellie"}))
    root = _world(tmp_path)

    assert cli._cmd_stop(_Args(root=root)) == cli.OK

    err = capsys.readouterr().err
    assert "canonical source unknown" in err
    assert "does not survive a reboot" in err, "the consequence is not named"
    assert "skipped" in (Path(root) / "history" / "kill-capture.log").read_text()


def test_the_dry_run_stop_captures_nothing(tmp_path, monkeypatch):
    """`--dry-run` mutates nothing, and an archive write is a mutation."""
    called = []
    monkeypatch.setattr(cli, "_capture_history_before_kill",
                        lambda a, n, w: called.append(n))
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(
        live={"crew-ellie"}, owned={"crew-ellie"}))
    cli._cmd_stop(_Args(root=_world(tmp_path), dry_run=True))
    assert called == []
