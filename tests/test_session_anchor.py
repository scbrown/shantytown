"""SessionStart anchor (aegis-7edci3): every session starts holding its work.

Before: `st anchor` sat in the SessionStart group of 0 of 3 emitted role files,
and `st ops hooks check` was green regardless, because it only checked hooks
other tools registered. These tests pin both halves: the hook is EMITTED for
every role on both harnesses, and the check goes RED when it is removed.
"""
from __future__ import annotations

import json

import pytest

from shantytown import hook_bundles as hb
from shantytown import runtime
from shantytown import session_anchor as sa
from tests.test_hook_bundles import commands, emit

ROLES = ("administrator", "lead", "worker")


@pytest.fixture
def root(tmp_path):
    r = tmp_path / ".shanty"
    (r / "settings").mkdir(parents=True)
    return r


def _anchor_cmds(cmds):
    return [c for c in cmds if "shantytown.session_anchor" in c]


def test_the_anchor_is_second_in_session_start_after_the_resume_brief(root):
    group = runtime.session_start_hooks(root)[0]["hooks"]
    mods = [h["command"].split(" -m ")[1].split()[0] for h in group
            if " -m " in h["command"]]
    assert mods[:2] == ["shantytown.resume_brief", "shantytown.session_anchor"]
    anchor = group[1]
    assert f"--root {root.resolve()}" in anchor["command"]
    assert anchor["timeout"] == 15


@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize("role", ROLES)
def test_every_role_file_carries_the_anchor(root, harness, role):
    path = emit(root, harness, role)
    assert len(_anchor_cmds(commands(harness, path, "SessionStart"))) == 1


def test_check_is_green_when_emitted(root):
    for r in ROLES:
        emit(root, "claude", r)
        emit(root, "codex", r)
    res = hb.check(root, builtin=runtime.builtin_hook_bundles(root))
    mine = [i for i in res.items if i["bundle"] == "st-session-anchor"]
    assert len(mine) == 6 and all(i["configured"] == "ok" for i in mine), mine
    assert res.exit_code == 0


def test_POSITIVE_CONTROL_check_goes_red_when_the_anchor_is_removed_by_hand(root):
    for r in ROLES:
        emit(root, "claude", r)
    p = root / "settings" / "lead.settings.json"
    data = json.loads(p.read_text())
    for g in data["hooks"]["SessionStart"]:
        g["hooks"] = [h for h in g["hooks"] if "session_anchor" not in h["command"]]
    p.write_text(json.dumps(data))
    res = hb.check(root, builtin=runtime.builtin_hook_bundles(root))
    missing = [(i["role"], i["harness"]) for i in res.items
               if i["bundle"] == "st-session-anchor" and i["configured"] == "missing"]
    assert missing == [("lead", "claude")]
    assert res.exit_code != 0


def test_without_builtin_the_old_check_cannot_see_it(root):
    # The before-state, kept as a test so a refactor that drops `builtin` from
    # the CLI call is visible: the registry alone knows nothing of st's hooks.
    emit(root, "claude", "lead")
    assert not [i for i in hb.check(root).items if i["bundle"] == "st-session-anchor"]


# --- the hook itself --------------------------------------------------------------

PLATE = ("\n  You are ana — lead, reports to boss.\n\n  ON YOUR PLATE\n"
         "    ▶ x-1  do the thing        (open)\n\n  YOUR LEAD\n    boss — up.\n")
EMPTY = ("\n  You are wes — worker, reports to ana.\n\n  ON YOUR PLATE\n"
         "    nothing. `st go <item> <you>` or ask your lead.\n")


def _env(monkeypatch, agent="ana", root="/r"):
    monkeypatch.setenv("SHANTY_AGENT", agent)
    monkeypatch.setenv("SHANTY_ROOT", root)


def test_outside_st_it_prints_nothing(monkeypatch, capsys):
    monkeypatch.delenv("SHANTY_AGENT", raising=False)
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    assert sa.main([]) == 0
    assert capsys.readouterr().out == ""


def test_it_injects_the_anchor_render_and_role_startup(monkeypatch, capsys):
    _env(monkeypatch)
    monkeypatch.setattr(sa, "_run_anchor", lambda root, agent: (0, PLATE))
    assert sa.main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith(sa.BANNER)
    assert "x-1  do the thing" in out and "STARTUP" in out
    assert "sweep YOUR OWN plate" in out          # lead line
    assert sa._EMPTY not in out                    # plate is not empty


def test_an_empty_plate_says_where_to_look(monkeypatch, capsys):
    _env(monkeypatch, agent="wes")
    monkeypatch.setattr(sa, "_run_anchor", lambda root, agent: (0, EMPTY))
    sa.main([])
    out = capsys.readouterr().out
    assert sa._EMPTY in out and "sweep YOUR OWN plate" not in out


@pytest.mark.parametrize("behaviour", ["exit2", "raises", "blank"])
def test_could_not_look_is_said_never_silent(monkeypatch, capsys, behaviour):
    _env(monkeypatch)

    def run(root, agent):
        if behaviour == "raises":
            raise RuntimeError("backend down")
        return (2, "") if behaviour == "exit2" else (0, "   ")
    monkeypatch.setattr(sa, "_run_anchor", run)
    assert sa.main([]) == 0
    out = capsys.readouterr().out
    assert "could not read your plate" in out and "st anchor" in out


def test_root_flag_wins_over_env(monkeypatch):
    _env(monkeypatch, root="/env")
    seen = {}
    monkeypatch.setattr(sa, "_run_anchor",
                        lambda root, agent: (seen.setdefault("root", root), (0, PLATE))[1])
    sa.main(["--root", "/flag"])
    assert seen["root"] == "/flag"
