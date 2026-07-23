"""Every keystroke st puts into a pane leaves a line — internal-ref.

During the CT-229 incident, three fabricated "recovered — proceed" recovery
instructions were injected into staged agents' panes and the sender could not
be named: the routine send path (st inbox live sends, dispatch, tend prompts —
all of them funnel through Tmux.send) was ephemeral by design, so the
forensics dead-ended at "an unattached process, unlogged channel". The journal
closes exactly that hole. These tests pin its contract, including the half
that must NOT happen: an audit failure never blocks a delivery.
"""
from __future__ import annotations

import os

from shantytown import tmux as tmux_mod


def _read_log(root):
    p = os.path.join(root, "logs", "sends.log")
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_a_send_is_journaled_with_sender_pane_and_text(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    monkeypatch.setenv("SHANTY_AGENT", "tim")
    tmux_mod._journal_send("aegis-crew-dearing", "bucket is back up - proceed")
    line = _read_log(tmp_path)
    assert "sender=tim" in line
    assert f"pid={os.getpid()}" in line
    assert "pane=aegis-crew-dearing" in line
    assert "text=bucket is back up - proceed" in line


def test_an_unnamed_sender_is_recorded_as_dash_with_its_pid(tmp_path, monkeypatch):
    """The apz9 shape: the sender that has no SHANTY_AGENT is exactly the one
    the journal exists for — it must still land, attributed by pid."""
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    monkeypatch.delenv("SHANTY_AGENT", raising=False)
    tmux_mod._journal_send("%1", "rebooted 229, take it from here")
    line = _read_log(tmp_path)
    assert "sender=- " in line
    assert f"pid={os.getpid()}" in line


def test_text_is_one_line_and_capped(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    tmux_mod._journal_send("%1", "a\nb\nc" + "x" * 1000)
    line = _read_log(tmp_path)
    assert "\na" not in line.split("text=", 1)[1]
    assert "a\\nb\\nc" in line
    assert len(line.split("text=", 1)[1]) <= 502  # 500 cap + newline slack


def test_no_store_elected_means_no_journal_and_no_error(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    tmux_mod._journal_send("%1", "hello")   # must simply not raise


def test_journal_failure_never_blocks_and_says_so(tmp_path, monkeypatch, capsys):
    """The inverse invariant: a broken audit trail must not take messaging
    down with it — but it warns rather than going quietly dark."""
    blocker = tmp_path / "logs"
    blocker.write_text("a FILE where the log DIR must be")  # makedirs -> OSError
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    tmux_mod._journal_send("%1", "still delivered")          # must not raise
    assert "send journal write failed" in capsys.readouterr().err


def test_tmux_send_journals_before_delivering(tmp_path, monkeypatch):
    """The seam claim: Tmux.send itself writes the journal line, and writes it
    BEFORE the keystrokes go out — an interrupted delivery still leaves its
    attempt on the record."""
    calls = []

    def fake_run(cmd, **kw):
        # The attempt must already be on disk by the FIRST subprocess call.
        assert os.path.exists(os.path.join(str(tmp_path), "logs", "sends.log")), \
            "journal must be written before send-keys"
        calls.append(cmd)

        class R:
            returncode = 0
        return R()

    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    monkeypatch.setenv("SHANTY_AGENT", "tim")
    monkeypatch.setattr(tmux_mod.subprocess, "run", fake_run)
    tmux_mod.Tmux().send("aegis-crew-arnold", "probe text")
    assert len(calls) == 2, "literal text + Enter"
    assert "text=probe text" in _read_log(tmp_path)
