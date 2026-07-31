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


def test_the_root_is_resolved_at_RUN_time_not_import(monkeypatch, tmp_path):
    """A module-level default freezes whatever the environment was at import, so a
    shell that exports the root before running st would still be ignored — which is
    the bug, one layer deeper.

    The seam is main(), not the parser: `--root` defaults to None so that `init`
    (which must never adopt a store it merely FOUND) can resolve differently from
    every other command. What must not change is that a late-set environment is
    still honoured, which is what this pins.
    """
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path / "late"))
    seen = {}

    def spy(a):
        seen["root"] = a.root
        return 0

    monkeypatch.setattr(cli, "_cmd_crew", spy)
    cli.main(["crew"])
    assert seen["root"] == tmp_path / "late"


def test_the_parser_leaves_an_unset_root_as_None_for_main_to_resolve():
    a = cli.build_parser().parse_args(["crew"])
    assert a.root is None


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
    # It must name WHERE to declare the socket. That home moved into the config
    # file when env.json/tmux-socket were folded in; the requirement did not.
    assert "report the fleet DEAD" in why and "[tmux]" in why


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


# --- the guard: supervision must do NOTHING on a wrong socket ----------------

def test_tend_REFUSES_on_a_wrong_socket_and_respawns_nothing(tmp_path, monkeypatch, capsys):
    """The fleet-destroying interaction, prevented. `tend --install` runs from a
    systemd timer with no $TMUX, so an undeclared socket makes every agent look
    DOWN — and a supervisor that sees the whole fleet dead respawns the whole
    fleet, onto the wrong server, duplicating every agent."""
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps({"role": "worker", "pane": "p-ellie"}))

    monkeypatch.setattr(cli, "_socket_check",
                        lambda a: (doc.SOCKET_WRONG, "0/18 visible here, 18 elsewhere"))
    started = []
    monkeypatch.setattr(cli, "_runtime", lambda a, p: started.append(p))

    class _A:
        root = tmp_path; registry = "files"; backend = None; repo = None
        install = uninstall = status = False
        retire = unretire = None; interval = "5min"; dry_run = False

    assert cli._cmd_tend(_A()) == cli.REFUSED
    err = capsys.readouterr().err
    assert "refused" in err and "respawned onto the wrong server" in err
    assert started == [], "built a runtime — it got past the guard"


# --- the POINTER leg: the only one that reaches a store outside the tree ------
#
# Measured (aegis-d94vb): CLAUDE.md tells every crew member to run `st anchor
# <you>` from their own workspace, and it refused with "no such agent: <their own
# name>". Crew workspaces are SIBLINGS of the checkout (~/gt/<rig>/crew/<agent> vs
# ~/gt/shantytown/.shanty), so the walk-up cannot reach the store from one at any
# depth. The pointer is the leg that covers that shape, it was the fix, and it had
# no test at all until this block.

def _point_at(monkeypatch, tmp_path, store):
    """Write a pointer in this test's isolated $XDG_CONFIG_HOME (see conftest)."""
    from shantytown.deployment import write_pointer
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return write_pointer(store)


def test_the_pointer_answers_when_nothing_else_does(monkeypatch, tmp_path):
    """THE case: a directory with no .shanty anywhere above it."""
    from shantytown.deployment import BY_POINTER, resolve_root
    store = tmp_path / "deployment" / ".shanty"
    store.mkdir(parents=True)
    sibling = tmp_path / "rig" / "crew" / "malcolm"
    sibling.mkdir(parents=True)
    _point_at(monkeypatch, tmp_path, store)
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    monkeypatch.chdir(sibling)
    root, how = resolve_root()
    assert root == store and how == BY_POINTER


def test_the_WALK_UP_still_beats_the_pointer(monkeypatch, tmp_path):
    """A directory that has its own .shanty keeps it. The pointer is the fallback
    for boxes where nothing local answered — it must never adopt a deployment out
    from under a checkout that owns one."""
    from shantytown.deployment import BY_WALKUP, resolve_root
    pointed = tmp_path / "elsewhere" / ".shanty"
    pointed.mkdir(parents=True)
    here = tmp_path / "checkout"
    (here / ".shanty").mkdir(parents=True)
    _point_at(monkeypatch, tmp_path, pointed)
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    monkeypatch.chdir(here)
    root, how = resolve_root()
    assert root == here / ".shanty" and how == BY_WALKUP


def test_SHANTY_ROOT_still_beats_the_pointer(monkeypatch, tmp_path):
    from shantytown.deployment import BY_ENV, resolve_root
    pointed = tmp_path / "elsewhere" / ".shanty"
    pointed.mkdir(parents=True)
    _point_at(monkeypatch, tmp_path, pointed)
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path / "chosen"))
    root, how = resolve_root()
    assert root == tmp_path / "chosen" and how == BY_ENV


def test_a_pointer_to_a_MISSING_directory_is_not_an_answer(monkeypatch, tmp_path):
    """A stale pointer must read as 'keep looking', not as 'your crew is empty' —
    the second is a store that answers confidently with nobody in it."""
    from shantytown.deployment import BY_CWD, resolve_root
    _point_at(monkeypatch, tmp_path, tmp_path / "gone" / ".shanty")
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    here = tmp_path / "somewhere"
    here.mkdir()
    monkeypatch.chdir(here)
    root, how = resolve_root()
    assert how == BY_CWD and root == here / ".shanty"


def test_init_never_adopts_the_pointed_deployment(monkeypatch, tmp_path):
    """`st init` answers cwd/.shanty even with a pointer set. Creating is not the
    same act as finding: init in a new project must not resolve to somebody else's
    deployment and then refuse as 'already a deployment'."""
    from shantytown.deployment import BY_CWD, resolve_root
    store = tmp_path / "deployment" / ".shanty"
    store.mkdir(parents=True)
    _point_at(monkeypatch, tmp_path, store)
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    fresh = tmp_path / "new-project"
    fresh.mkdir()
    monkeypatch.chdir(fresh)
    root, how = resolve_root(discover=False)
    assert root == fresh / ".shanty" and how == BY_CWD
