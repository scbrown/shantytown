"""feed_check — the administrator's Rule Zero HARD GATE (internal-ref).

BLOCK the coordinator's own stop while free FEEDABLE workers AND dispatchable beads
coexist; ALLOW (fail open) on any error, when nobody is free, or when there is no
dispatchable work. These tests pin every branch of that, plus the two constraints
that keep it from false-trapping: dark workers are not "free", and dark-assigned
beads are not "dispatchable".
"""
from __future__ import annotations
import json

import pytest

from shantytown import feed_check
from shantytown.protocols import Agent


# --- dispatchable: unassigned OR assigned-to-a-free-worker ------------------

def test_unassigned_ready_beads_are_dispatchable():
    ready = [{"id": "aegis-1", "title": "a"}, {"id": "aegis-2", "title": "b"}]
    got = feed_check.dispatchable({"weaver"}, ready)
    assert [b[0] for b in got] == ["aegis-1", "aegis-2"]


def test_a_bead_assigned_to_a_dark_agent_is_NOT_dispatchable():
    # arnold is dark (not in the free set): its bead is stuck, not feedable.
    ready = [{"id": "aegis-1", "title": "a", "assignee": "beads_aegis/crew/arnold"}]
    assert feed_check.dispatchable({"weaver"}, ready) == []


def test_a_bead_assigned_to_a_free_worker_is_the_workers_own_queue_now():
    """INVERTED by the haul reinterpretation (internal-ref, Stiwi's call): an
    assigned bead is its worker's own queue, never coordinator-dispatch
    material. The old reading made the coordinator the delivery mechanism for
    work the worker already owned — N pings + N manual go's, measured."""
    ready = [{"id": "aegis-1", "title": "a", "assignee": "weaver"}]
    assert feed_check.dispatchable({"weaver"}, ready) == []
    assert feed_check.hauls(ready) == {"weaver": ["aegis-1"]}


def test_threaded_parses_crew_paths_and_skips_unassigned():
    ready = [{"id": "aegis-1", "assignee": "beads_aegis/crew/billy"},
             {"id": "aegis-2", "assignee": "billy"},
             {"id": "aegis-3"}]
    assert feed_check.hauls(ready) == {"billy": ["aegis-1", "aegis-2"]}


def test_a_board_of_all_dark_assigned_beads_is_not_dispatchable():
    ready = [{"id": "aegis-1", "assignee": "crew/arnold"},
             {"id": "aegis-2", "assignee": "crew/ellie"}]
    assert feed_check.dispatchable({"weaver"}, ready) == []


# --- free = feedable: dark workers excluded, unreadable excluded ------------

class _Runtime:
    def shows_ready_ui(self, screen):
        return "shift+tab" in screen

    def awaiting_answer(self, screen):
        return "Enter to select" in screen


IDLE = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
BUSY = "✻ Envisioning… (12s · esc to interrupt)"
SEND_CMDLINE = "claude --settings /s.json"     # carries a stop_event send hook


class _Panes:
    def __init__(self, screens, cmdlines):
        self._screens = screens
        self._cmdlines = cmdlines

    def exists(self, pane):
        return pane in self._screens

    def capture(self, pane, history=0, attrs=False):
        return self._screens.get(pane, "")

    def cmdline(self, pane):
        return self._cmdlines.get(pane)


class _Reg:
    def __init__(self, agents):
        self._a = agents

    def all(self):
        return self._a


def _send_settings(tmp_path):
    p = tmp_path / "worker.settings.json"
    p.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "python -m shantytown.stop_event send"}]}]}}))
    return p


def test_an_idle_wired_worker_is_free(tmp_path):
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="weaver", role="worker", pane="shanty-weaver")])
    panes = _Panes({"shanty-weaver": IDLE},
                   {"shanty-weaver": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == ["weaver"]


def test_a_dark_worker_is_not_free(tmp_path):
    # no --settings on the cmdline -> no send wiring -> dark -> not free.
    reg = _Reg([Agent(name="arnold", role="worker", pane="aegis-crew-arnold")])
    panes = _Panes({"aegis-crew-arnold": IDLE},
                   {"aegis-crew-arnold": "claude --no-such-flag"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == []


def test_a_busy_worker_is_not_free(tmp_path):
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="tim", role="worker", pane="shanty-tim")])
    panes = _Panes({"shanty-tim": BUSY}, {"shanty-tim": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == []


def test_unreadable_wiring_excludes_the_worker(tmp_path):
    # cmdline None -> wiring None -> not feedable (the safe direction).
    reg = _Reg([Agent(name="x", role="worker", pane="shanty-x")])
    panes = _Panes({"shanty-x": IDLE}, {})       # no cmdline
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == []


def test_a_named_dark_agent_is_excluded_even_with_full_send_wiring(tmp_path):
    # The respawn case (internal-ref, measured 2026-07-23): a gastown agent comes back
    # re-primed with the shantytown worker settings, so it CARRIES the send wiring the
    # gate keys on — the wiring gate cannot catch it. maldoon is in the default
    # denylist; it must be excluded despite valid send wiring, or Rule Zero traps the
    # coordinator on every stop and dispatch strands beads on a pane it can't reach.
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="maldoon", role="worker", pane="aegis-crew-maldoon")])
    panes = _Panes({"aegis-crew-maldoon": IDLE},
                   {"aegis-crew-maldoon": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == []


def test_SHANTY_DARK_AGENTS_env_overrides_the_default_denylist(tmp_path, monkeypatch):
    # A normally-feedable worker becomes dark when named in the override, so a
    # deployment can name its own dark set without a code change.
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="weaver", role="worker", pane="shanty-weaver")])
    panes = _Panes({"shanty-weaver": IDLE},
                   {"shanty-weaver": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == ["weaver"]
    monkeypatch.setenv("SHANTY_DARK_AGENTS", "weaver, someone-else")
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == []


# --- main(): block only when both hold; allow (fail-open) otherwise ---------

def _wire_main(monkeypatch, free, ready_beads=None, bd_raises=False):
    monkeypatch.setattr(feed_check, "free_feedable_workers", lambda *a, **k: free)
    monkeypatch.setattr(feed_check, "bd_cwd", lambda reg: None)
    if bd_raises:
        def boom(cwd=None):
            raise RuntimeError("bd unreachable")
        monkeypatch.setattr(feed_check, "_bd_ready", boom)
    else:
        monkeypatch.setattr(feed_check, "_bd_ready", lambda cwd=None: ready_beads or [])
    # neutralise the store/tmux setup so main reaches the injected functions.
    import shantytown.files as f
    monkeypatch.setattr(f, "FilesRegistry", lambda *a, **k: object())
    monkeypatch.setattr("shantytown.tmux.Tmux", lambda *a, **k: object())
    monkeypatch.setattr("shantytown.tmux.declared_socket", lambda *a: None)
    monkeypatch.setattr("shantytown.runtime.ClaudeRuntime", lambda *a, **k: object())


def test_blocks_when_free_and_dispatchable_both_exist(monkeypatch, capsys):
    _wire_main(monkeypatch, free=["weaver"],
               ready_beads=[{"id": "aegis-9", "title": "fix the thing"}])
    rc = feed_check.main(["--root", "/x"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block"
    assert "weaver" in out["reason"] and "aegis-9" in out["reason"]
    assert "Rule Zero".upper() in out["reason"].upper()


def test_allows_when_nobody_is_free(monkeypatch, capsys):
    _wire_main(monkeypatch, free=[], ready_beads=[{"id": "aegis-9"}])
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "no free workers -> allow, print nothing"


def test_allows_when_no_dispatchable_work(monkeypatch, capsys):
    # free workers, but the only ready bead is dark-assigned -> not dispatchable.
    _wire_main(monkeypatch, free=["weaver"],
               ready_beads=[{"id": "aegis-9", "assignee": "crew/arnold"}])
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "no dispatchable work -> allow"


def test_FAILS_OPEN_when_bd_is_unreachable(monkeypatch, capsys):
    # THE critical invariant: a bd hiccup must never trap the coordinator.
    _wire_main(monkeypatch, free=["weaver"], bd_raises=True)
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "bd error -> ALLOW the stop, never block"


def test_FAILS_OPEN_when_the_registry_setup_raises(monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("no store")
    monkeypatch.setattr("shantytown.files.FilesRegistry", boom)
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "any error -> allow"


def test_self_terminates_when_free_hits_zero(monkeypatch, capsys):
    # The self-termination proof: same store, but free drops to 0 (all dispatched)
    # -> the stop is now ALLOWED. It terminates on the fleet being fed, not a counter.
    _wire_main(monkeypatch, free=[], ready_beads=[{"id": "aegis-9"}])
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == ""


# --- bd resolves from the ADMIN's workspace, never the ambient cwd -----------

def test_bd_cwd_walks_up_from_the_admins_workspace_to_the_rig_root(tmp_path):
    """MEASURED (internal-ref follow-up), twice over: the live tend loop ran
    `bd ready` from a checkout with no beads store — 'no beads database found'
    on every sweep for two days, eaten by fail-open, and the nk0e idle-fleet
    push never fired once. And the admin's workspace itself does NOT resolve
    either (each crew workspace is its own git clone; bd stops at the clone
    boundary) — the store is at the RIG ROOT above it, so bd_cwd walks up."""
    rig = tmp_path / "rig"
    ws = rig / "crew" / "sattler"
    ws.mkdir(parents=True)
    (rig / ".beads").mkdir()
    reg = _Reg([Agent(name="sattler", role="administrator", workspace=str(ws)),
                Agent(name="weaver", role="worker", pane="p-w")])
    assert feed_check.bd_cwd(reg) == str(rig)


def test_a_workspace_with_its_own_store_wins_over_an_ancestor(tmp_path):
    rig = tmp_path / "rig"
    ws = rig / "crew" / "sattler"
    ws.mkdir(parents=True)
    (rig / ".beads").mkdir()
    (ws / ".beads").mkdir()
    reg = _Reg([Agent(name="sattler", role="administrator", workspace=str(ws))])
    assert feed_check.bd_cwd(reg) == str(ws)


def test_bd_cwd_without_an_admin_workspace_or_store_is_None_not_a_guess(tmp_path):
    assert feed_check.bd_cwd(_Reg([Agent(name="w", role="worker")])) is None
    assert feed_check.bd_cwd(
        _Reg([Agent(name="a", role="administrator")])) is None
    # a workspace with NO .beads anywhere above it: still None, never a guess
    ws = tmp_path / "lonely" / "crew" / "a"
    ws.mkdir(parents=True)
    assert feed_check.bd_cwd(
        _Reg([Agent(name="a", role="administrator", workspace=str(ws))])) is None


def test_bd_ready_runs_bd_in_the_given_cwd(monkeypatch):
    seen = {}

    class _P:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(argv, **kw):
        seen["cwd"] = kw.get("cwd")
        return _P()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert feed_check._bd_ready("/crew/sattler") == []
    assert seen["cwd"] == "/crew/sattler"


def test_a_threaded_worker_never_blocks_the_rule_zero_gate(monkeypatch, capsys):
    """The hard-gate side of the haul exclusion (internal-ref): an idle worker
    whose queue is already assigned must not hold the coordinator's stop
    hostage — self-feeding is not a coordinator-stall."""
    _wire_main(monkeypatch, free=["billy"],
               ready_beads=[{"id": "aegis-9", "title": "queued",
                             "assignee": "billy"}])
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "self-feeding fleet -> stop allowed, silence"


# --- the launch-stamp ownership gate (internal-ref) ---------------------------

def _stamp(root, *names):
    d = root / "launched"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / f"{n}.json").write_text("{}")


def test_an_unstamped_agent_is_not_fed_when_stamps_exist(tmp_path):
    """The structural dark-crew fix: a pane st did not launch (no launch stamp)
    carries full send wiring — the respawner re-primes it with this
    deployment's worker settings — and must still not be counted free. Unlike
    the name denylist this needs no name: ownership is the signal."""
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="notmine", role="worker", pane="other-crew-notmine"),
                Agent(name="weaver", role="worker", pane="shanty-weaver")])
    panes = _Panes({"other-crew-notmine": IDLE, "shanty-weaver": IDLE},
                   {"other-crew-notmine": f"claude --settings {settings}",
                    "shanty-weaver": f"claude --settings {settings}"})
    _stamp(tmp_path, "weaver")
    assert feed_check.free_feedable_workers(
        reg, panes, _Runtime(), root=tmp_path) == ["weaver"]


def test_an_empty_stamp_store_applies_no_gate(tmp_path):
    """CANNOT TELL is honored: with no stamps at all (fresh deployment, or the
    store unreadable) the gate must not starve the fleet — ownership is
    unknowable, so the wiring gate alone decides, as before."""
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="weaver", role="worker", pane="shanty-weaver")])
    panes = _Panes({"shanty-weaver": IDLE},
                   {"shanty-weaver": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(
        reg, panes, _Runtime(), root=tmp_path) == ["weaver"]


def test_no_root_means_no_ownership_gate(tmp_path):
    """Callers that cannot supply a root (legacy paths) keep the old
    behaviour exactly."""
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="weaver", role="worker", pane="shanty-weaver")])
    panes = _Panes({"shanty-weaver": IDLE},
                   {"shanty-weaver": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == ["weaver"]
