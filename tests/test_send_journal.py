"""Every keystroke st puts into a pane leaves a line — aegis-apz9.

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


def test_no_store_elected_warns_loudly_and_never_raises(tmp_path, monkeypatch, capsys):
    """No SHANTY_ROOT = nowhere to journal — but the skip MUST be LOUD, not
    silent (aegis-tdesp). A silently-unjournaled st send is indistinguishable
    from a raw tmux injection and manufactures the apz9 'not in sends.log =>
    injector' signature. So it warns, carries the send's identifying fields as a
    greppable breadcrumb, and still never raises (delivery is never blocked)."""
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    monkeypatch.setenv("SHANTY_AGENT", "gennaro")
    tmux_mod._journal_send("%1", "Work is on your hook: aegis-XXXX")   # must not raise
    err = capsys.readouterr().err
    assert "UNJOURNALED" in err and "SHANTY_ROOT unset" in err
    assert "pane=%1" in err and f"pid={os.getpid()}" in err
    assert "sender=gennaro" in err
    assert "text=Work is on your hook: aegis-XXXX" in err
    # and it must NOT have guessed a cwd/.shanty location to write into
    assert not (tmp_path / ".shanty").exists()


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
        # SEND-KEYS ONLY. send() now also asks tmux what the pane is running
        # before it types, so that the dead-pane guard can refuse a shell
        # (aegis-ikj4t) — a read-only query that puts no keystroke in the pane.
        # The contract this test pins is about KEYSTROKES: by the time any text
        # is typed, the attempt is already on disk. Counting every subprocess
        # call instead would make the assertion about how many times st talks to
        # tmux, which is not the claim.
        if "send-keys" in cmd:
            assert os.path.exists(
                os.path.join(str(tmp_path), "logs", "sends.log")), \
                "journal must be written before send-keys"
            calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
        return R()

    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    monkeypatch.setenv("SHANTY_AGENT", "tim")
    monkeypatch.setattr(tmux_mod.subprocess, "run", fake_run)
    tmux_mod.Tmux().send("aegis-crew-arnold", "probe text")
    assert len(calls) == 2, "literal text + Enter"
    assert "text=probe text" in _read_log(tmp_path)


# --- aegis-0681f6: a CRON send (no SHANTY_ROOT) journals into the store the CLI FOUND ---

def _cron_env(tmp_path, monkeypatch):
    """Cron's shape: no SHANTY_ROOT, cwd in an unrelated directory with no .shanty,
    and the box's pointer naming the deployment."""
    store = tmp_path / "deploy" / ".shanty"
    store.mkdir(parents=True)
    xdg = tmp_path / "xdg"
    (xdg / "shantytown").mkdir(parents=True)
    (xdg / "shantytown" / "root").write_text(str(store) + "\n")
    elsewhere = tmp_path / "cron-cwd"
    elsewhere.mkdir()
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.chdir(elsewhere)
    return store, elsewhere


def test_a_cron_send_resolved_by_pointer_is_journaled(tmp_path, monkeypatch, capsys):
    from shantytown.deployment import BY_POINTER, command_environment, resolve_root
    store, _ = _cron_env(tmp_path, monkeypatch)
    root, how = resolve_root(None)
    assert how == BY_POINTER          # the premise: this is the cron population
    with command_environment(root, how):
        tmux_mod._journal_send("shanty-dearing", "detector finding for cron")
    assert "text=detector finding for cron" in _read_log(store)
    assert "UNJOURNALED" not in capsys.readouterr().err
    # restored at the CLI boundary, like every other carried variable
    assert "SHANTY_ROOT" not in os.environ


def test_the_cwd_guess_is_never_carried_into_the_journal(tmp_path, monkeypatch, capsys):
    """CONTROL. With no pointer, the CLI falls back to cwd/.shanty — a guess against
    wherever cron runs. It must stay unjournaled and loud, never fragment the
    journal into a random directory (the nipg mode)."""
    from shantytown.deployment import BY_CWD, command_environment, resolve_root
    _, elsewhere = _cron_env(tmp_path, monkeypatch)
    (tmp_path / "xdg" / "shantytown" / "root").unlink()
    (elsewhere / ".shanty").mkdir()   # even a real dir there is not a FOUND store
    root, how = resolve_root(None, discover=False)
    assert how == BY_CWD
    with command_environment(root, how):
        tmux_mod._journal_send("shanty-dearing", "guessed root")
    assert "UNJOURNALED" in capsys.readouterr().err
    assert not (elsewhere / ".shanty" / "logs" / "sends.log").exists()


def test_an_explicit_shanty_root_is_never_overridden(tmp_path, monkeypatch):
    from shantytown.deployment import BY_POINTER, command_environment
    store, _ = _cron_env(tmp_path, monkeypatch)
    mine = tmp_path / "mine"
    mine.mkdir()
    monkeypatch.setenv("SHANTY_ROOT", str(mine))
    with command_environment(store, BY_POINTER):
        assert os.environ["SHANTY_ROOT"] == str(mine)
