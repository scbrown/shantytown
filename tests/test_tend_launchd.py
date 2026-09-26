"""st fleet tend --install on macOS: a launchd agent (docs/jobs.md).

The Mac ran no supervision, because --install only knew systemd — so the crew
and every patrol it hosted lived on session crons. The plist must carry the two
lessons the systemd unit already paid for: an ABSOLUTE st (a bare name failed
203/EXEC 687 times while the timer looked healthy) and SHANTY_ROOT in the
environment (sends from a unit without it were never journaled). Nothing here
loads anything: `run` is a recorder and the agents dir is a temp dir.
"""
from __future__ import annotations

import plistlib
from types import SimpleNamespace

import pytest

from shantytown import cli, supervisor


ST_BIN = "/opt/homebrew/bin/st"


def test_the_plist_runs_the_absolute_st_with_SHANTY_ROOT_every_five_minutes(tmp_path):
    ran = []
    changed, msg = supervisor.install_launchd(
        ST_BIN, tmp_path / "root", agents_dir=tmp_path / "LaunchAgents",
        run=ran.append, path_env="/opt/homebrew/bin:/usr/bin:/bin", uid=501)
    assert changed, msg
    path = tmp_path / "LaunchAgents" / f"{supervisor.LAUNCHD_LABEL}.plist"
    plist = plistlib.loads(path.read_bytes())
    root = str((tmp_path / "root").resolve())
    assert plist["ProgramArguments"] == [ST_BIN, "--root", root, "fleet", "tend"]
    assert plist["ProgramArguments"][0].startswith("/")
    assert plist["EnvironmentVariables"]["SHANTY_ROOT"] == root
    assert plist["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin")
    assert plist["StartInterval"] == 300 and plist["RunAtLoad"] is True
    assert supervisor.ours(path)
    assert ran == [["launchctl", "bootout", "gui/501", str(path)],
                   ["launchctl", "bootstrap", "gui/501", str(path)]]

    again, msg2 = supervisor.install_launchd(
        ST_BIN, tmp_path / "root", agents_dir=tmp_path / "LaunchAgents",
        run=ran.append, path_env="/opt/homebrew/bin:/usr/bin:/bin", uid=501)
    assert not again and "already installed" in msg2


def test_the_plist_REFUSES_a_bare_st(tmp_path):
    changed, msg = supervisor.install_launchd("st", tmp_path / "root",
                                              agents_dir=tmp_path / "LA")
    assert not changed and "REFUSED" in msg and "absolute" in msg
    assert not (tmp_path / "LA").exists()


def test_the_plist_REFUSES_to_overwrite_one_it_did_not_write(tmp_path):
    la = tmp_path / "LA"
    la.mkdir()
    theirs = la / f"{supervisor.LAUNCHD_LABEL}.plist"
    theirs.write_text("<plist>somebody else's</plist>")
    changed, msg = supervisor.install_launchd(ST_BIN, tmp_path / "root", agents_dir=la)
    assert not changed and "REFUSED" in msg
    assert "somebody else's" in theirs.read_text()
    assert not supervisor.uninstall_launchd(agents_dir=la)[0]


def test_uninstall_removes_the_plist_it_wrote(tmp_path):
    la = tmp_path / "LA"
    supervisor.install_launchd(ST_BIN, tmp_path / "root", agents_dir=la, path_env="")
    ran = []
    changed, _ = supervisor.uninstall_launchd(run=ran.append, agents_dir=la, uid=501)
    assert changed and not list(la.iterdir())
    assert ran[0][:2] == ["launchctl", "bootout"]


@pytest.mark.parametrize("platform, want", [("darwin", "launchd"), ("linux", "systemd")])
def test_tend_install_picks_launchd_on_macOS_and_leaves_linux_on_systemd(
        tmp_path, monkeypatch, capsys, platform, want):
    called = []
    monkeypatch.setattr(cli.sys, "platform", platform)
    monkeypatch.setattr(cli.sup_mod, "resolve_st_bin", lambda: ST_BIN)
    monkeypatch.setattr(cli.sup_mod, "install_launchd",
                        lambda *a, **k: (called.append("launchd"), (True, "ok"))[1])
    monkeypatch.setattr(cli.sup_mod, "install",
                        lambda *a, **k: (called.append("systemd"), (True, "ok"))[1])
    a = SimpleNamespace(root=tmp_path, install=True, uninstall=False, status=False,
                        retire=None, unretire=None, interval="5min", dry_run=True)
    assert cli._cmd_tend(a) == cli.OK
    assert called == [want]
