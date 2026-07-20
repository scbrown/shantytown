"""Where is the store, and which tmux server is the fleet on? — the shanty cutover.

Both questions had the same shape of wrong answer: resolved from AMBIENT state
(the cwd; the $TMUX of whatever pane you happened to run in), so the same command
meant different things in different panes, and the failure was silent both times.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import pytest

from shantytown import cli, doctor as doc
from shantytown.tmux import declared_socket


# --- the root: $SHANTY_ROOT, the same precedence the Stop hook uses ----------

def test_default_root_honours_the_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path / "store"))
    assert cli._default_root() == tmp_path / "store"


def test_default_root_falls_back_to_the_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli._default_root() == tmp_path / ".shanty"


def test_the_parser_resolves_the_root_at_PARSE_time_not_import(monkeypatch, tmp_path):
    """A module-level default freezes whatever the environment was at import, so
    a shell that exports the root before running st would still be ignored —
    which is the bug, one layer deeper."""
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path / "late"))
    a = cli.build_parser().parse_args(["crew"])
    assert a.root == tmp_path / "late"


def test_an_explicit_root_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path / "env"))
    a = cli.build_parser().parse_args(["--root", str(tmp_path / "flag"), "crew"])
    assert a.root == tmp_path / "flag"


def test_the_cli_and_the_stop_hook_agree(monkeypatch, tmp_path):
    """They did NOT, and a comment in the hook asserted they did — which is what
    kept the disagreement invisible."""
    from shantytown import stop_event
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path / "store"))
    assert stop_event._root([]) == cli._default_root()


# --- the socket: declared by the store, never inferred from the ambient $TMUX -

def test_the_store_declares_the_socket(tmp_path):
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "tmux-socket").write_text("gt-fleet\n")
    assert declared_socket(tmp_path) == "gt-fleet"


def test_the_file_beats_the_environment(tmp_path, monkeypatch):
    """An env var is whatever the operator's shell happens to hold, and the whole
    defect is a command meaning different things in different panes."""
    monkeypatch.setenv("SHANTY_TMUX_SOCKET", "from-env")
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "tmux-socket").write_text("from-store")
    assert declared_socket(tmp_path) == "from-store"


def test_the_environment_is_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_TMUX_SOCKET", "from-env")
    assert declared_socket(tmp_path) == "from-env"


def test_no_declaration_means_the_default_server(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_TMUX_SOCKET", raising=False)
    assert declared_socket(tmp_path) is None


# --- doctor FAILS on a wrong socket rather than reporting a dead fleet -------

def test_seeing_the_fleet_is_ok():
    v, why = doc.socket_health(18, 18, 0, "gt-fleet")
    assert v == doc.SOCKET_OK and "18/18" in why


def test_fleet_visible_ELSEWHERE_is_a_WRONG_SOCKET_fault():
    """THE case. Bare tmux on a host whose agents live on a named socket reports
    every agent DOWN, confidently, with exit 0 — `st crew` says the fleet is dead
    and `st go` refuses to dispatch to a pane that is right there."""
    v, why = doc.socket_health(18, 0, 18, "shanty")
    assert v == doc.SOCKET_WRONG
    assert "report the fleet DEAD" in why and "tmux-socket" in why


def test_a_wrong_socket_makes_doctor_exit_ACTIONABLE():
    assert cli._fold_socket(cli.OK, doc.SOCKET_WRONG, doc) == cli.REFUSED


def test_nothing_visible_ANYWHERE_is_unknown_not_a_socket_fault():
    """The fleet may really be down. Claiming a config fault it cannot
    distinguish is how a dead fleet gets reported as a misconfiguration — and
    the reverse, which is worse."""
    v, why = doc.socket_health(18, 0, 0, "gt-fleet")
    assert v == doc.SOCKET_UNKNOWN
    assert "may really be down" in why
    assert cli._fold_socket(cli.OK, v, doc) == cli.CANNOT_TELL


def test_an_empty_registry_claims_nothing():
    v, _ = doc.socket_health(0, 0, 0, None)
    assert v == doc.SOCKET_UNKNOWN


def test_the_cli_builds_its_panes_on_the_declared_socket(tmp_path, monkeypatch):
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "tmux-socket").write_text("gt-fleet")

    class _A:
        root = tmp_path
    assert cli._panes(_A()).socket == "gt-fleet", \
        "the CLI built a BARE tmux — from any named-socket pane that reports the " \
        "whole fleet down"
