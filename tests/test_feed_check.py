"""feed_check — the administrator's Rule Zero HARD GATE (aegis-hfta).

BLOCK the coordinator's own stop while free FEEDABLE workers AND dispatchable beads
coexist; ALLOW (fail open) on any error, when nobody is free, or when there is no
dispatchable work. These tests pin every branch of that, plus the two constraints
that keep it from false-trapping: dark workers are not "free", and dark-assigned
beads are not "dispatchable".
"""
from __future__ import annotations

from shantytown.answer import Answer
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
    """INVERTED by the haul reinterpretation (aegis-wjgt, Stiwi's call): an
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


def test_queue_state_uses_br_without_spawning_retired_bd(monkeypatch, tmp_path):
    from shantytown.br import BrTracker

    tracker = BrTracker(repo=str(tmp_path))
    monkeypatch.setattr("shantytown.br.ready",
                        lambda trk: [{"id": "aegis-ready"}])
    monkeypatch.setattr("shantytown.br.in_progress",
                        lambda trk: [{"id": "aegis-active"}])
    monkeypatch.setattr(feed_check, "_bd_ready",
                        lambda cwd=None: (_ for _ in ()).throw(AssertionError("bd called")))
    monkeypatch.setattr(feed_check, "bd_in_progress",
                        lambda cwd=None: (_ for _ in ()).throw(AssertionError("bd called")))

    ready, active = feed_check.queue_state(tmp_path, _Reg([]), tracker)

    assert ready == [{"id": "aegis-ready"}]
    assert active == [{"id": "aegis-active"}]


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
        return Answer.complete_read(self._a, how="test registry")


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
    # The respawn case (aegis-b686, measured 2026-07-23): a gastown agent comes back
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
    out = capsys.readouterr()
    assert out.out == "", "bd error -> ALLOW the stop, never block"
    assert "feed-path read FAILED" in out.err and "ALLOWING" in out.err


def test_FAILS_OPEN_when_the_registry_setup_raises(monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("no store")
    monkeypatch.setattr("shantytown.files.FilesRegistry", boom)
    assert feed_check.main(["--root", "/x"]) == 0
    out = capsys.readouterr()
    assert out.out == "", "any error -> allow"
    assert "feed-path read FAILED" in out.err and "ALLOWING" in out.err


def test_self_terminates_when_free_hits_zero(monkeypatch, capsys):
    # The self-termination proof: same store, but free drops to 0 (all dispatched)
    # -> the stop is now ALLOWED. It terminates on the fleet being fed, not a counter.
    _wire_main(monkeypatch, free=[], ready_beads=[{"id": "aegis-9"}])
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == ""


# --- bd resolves from the ADMIN's workspace, never the ambient cwd -----------

def test_bd_cwd_walks_up_from_the_admins_workspace_to_the_rig_root(tmp_path):
    """MEASURED (aegis-arma follow-up), twice over: the live tend loop ran
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


def test_bd_cwd_without_an_admin_workspace_or_store_is_None_not_a_guess(beadsless_tmp):
    assert feed_check.bd_cwd(_Reg([Agent(name="w", role="worker")])) is None
    assert feed_check.bd_cwd(
        _Reg([Agent(name="a", role="administrator")])) is None
    # A workspace with NO .beads anywhere above it: still None, never a guess.
    # `beadsless_tmp`, not tmp_path — bd_cwd walks ALL the way up, and on this
    # host pytest's basetemp is under $HOME, which has a .beads (aegis-rig9vu).
    ws = beadsless_tmp / "lonely" / "crew" / "a"
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
    """The hard-gate side of the haul exclusion (aegis-wjgt): an idle worker
    whose queue is already assigned must not hold the coordinator's stop
    hostage — self-feeding is not a coordinator-stall."""
    _wire_main(monkeypatch, free=["billy"],
               ready_beads=[{"id": "aegis-9", "title": "queued",
                             "assignee": "billy"}])
    assert feed_check.main(["--root", "/x"]) == 0
    assert capsys.readouterr().out == "", "self-feeding fleet -> stop allowed, silence"


# --- the launch-stamp ownership gate (aegis-2j2r) ---------------------------

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


# --- haul feed message: the RELEASE affordance (aegis-tgvtg) ------------------
# The haul re-serves any open+assigned+ready bead until its assignee is cleared,
# and setting status alone does NOT release it. The trap the bead documents is
# that the exit is undiscoverable, so the message MUST name it at the moment of
# the re-serve. These pin that it does, and pin the specific correction (status
# alone is not enough) so a future edit can't quietly drop the load-bearing half.

def test_haul_feed_message_names_the_release_exit_with_the_actual_id():
    msg = feed_check.haul_feed_message("aegis-9z9z", "some title", 2)
    # the working mechanism, spelled with THIS bead's id so it is copy-pasteable
    assert "st defer aegis-9z9z" in msg      # the structured truly-park exit
    assert "bead|human|access|external|parked" in msg
    assert "br close aegis-9z9z" in msg
    # and the correction that the whole bug turned on
    assert "re-pool" in msg.lower()          # clearing assignee only re-pools


def test_haul_feed_message_still_carries_the_core_advance_instruction():
    # the release line is ADDITIVE — it must not have displaced the advance itself
    msg = feed_check.haul_feed_message("aegis-9z9z", "t", 0)
    assert "aegis-9z9z" in msg
    assert "close to advance" in msg
    assert "0 more" in msg


# --- retired cards are not capacity (aegis-w4k8n) ----------------------------

def test_a_RETIRED_worker_with_a_LIVE_IDLE_pane_is_not_free(tmp_path):
    """THE aegis-w4k8n BUG, as a controlled pair.

    Retiring a card stops `st tend` RESPAWNING that agent; it does not kill a
    pane already up. So a retired agent mid-turn stays live and stays idle, and
    every gate this function had — worker, pane exists, not dark, stamped, IDLE,
    send-wired — passed it straight through as dispatchable capacity.

    The two cards below differ in EXACTLY ONE field. If the assertion on the
    retired one ever passes because the function broke rather than because the
    gate works, `weaver` fails alongside it and says so.
    """
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="zia", role="worker", pane="shanty-zia", retired=True),
                Agent(name="weaver", role="worker", pane="shanty-weaver")])
    panes = _Panes({"shanty-zia": IDLE, "shanty-weaver": IDLE},
                   {"shanty-zia": f"claude --settings {settings}",
                    "shanty-weaver": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == ["weaver"]


def test_retired_is_TRI_STATE_and_only_true_excludes(tmp_path):
    """`retired` is bool|None: None means NOT EXPRESSED and must read as
    not-retired (protocols.py is explicit that absence is representable and that
    every consumer tests truthiness). A gate that treated None as retired would
    silently empty the feed list for every card written by a source that does not
    model retirement — the aegis-6hfmi failure, inverted."""
    settings = _send_settings(tmp_path)
    cards = [Agent(name="a", role="worker", pane="p-a", retired=None),
             Agent(name="b", role="worker", pane="p-b", retired=False),
             Agent(name="c", role="worker", pane="p-c", retired=True)]
    panes = _Panes({f"p-{n}": IDLE for n in "abc"},
                   {f"p-{n}": f"claude --settings {settings}" for n in "abc"})
    assert feed_check.free_feedable_workers(_Reg(cards), panes, _Runtime()) == ["a", "b"]


def test_the_whole_fleet_retired_yields_ZERO_free_not_a_full_roster(tmp_path):
    """MEASURED on the live fleet 2026-08-02, and the reason this is a P2 and not
    a cosmetic count: after the roster cut, ALL SIX names this returned were
    retired. The alert was not partly wrong, it was entirely wrong — every name
    it handed the coordinator was one `st go` would refuse."""
    settings = _send_settings(tmp_path)
    names = ["billy", "franklin", "lowery", "tim", "weaver", "zia"]
    cards = [Agent(name=n, role="worker", pane=f"shanty-{n}", retired=True)
             for n in names]
    panes = _Panes({f"shanty-{n}": IDLE for n in names},
                   {f"shanty-{n}": f"claude --settings {settings}" for n in names})
    assert feed_check.free_feedable_workers(_Reg(cards), panes, _Runtime()) == []


def test_the_haul_feed_legs_read_through_br_not_bd(monkeypatch, tmp_path):
    """aegis-mxgzh: the regression that made alerts fire while nothing was fed.

    `bd` is retired post-cutover and exits 3, so any leg that still spawns it
    raises. queue_state was migrated to br; `_bd_ready`/`bd_in_progress` were
    NOT — and notify injects those two FUNCTIONS as default arguments, so the
    Rule Zero alert path recovered while the haul feed stayed dead. The split is
    invisible to a test that only exercises queue_state, which is why this one
    asserts on the helpers themselves.
    """
    from shantytown import feed_check

    calls = []
    monkeypatch.setattr(feed_check.subprocess, "run",
                        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
                            AssertionError("spawned bd on a br deployment")))

    class _Tracker:  # stands in for BrTracker
        pass
    monkeypatch.setattr(feed_check, "_br_tracker", lambda root, reg: _Tracker())
    import shantytown.br as br
    monkeypatch.setattr(br, "ready", lambda t: [{"id": "aegis-1"}])
    monkeypatch.setattr(br, "in_progress", lambda t: [{"id": "aegis-2"}])

    assert feed_check._bd_ready(None, root=tmp_path, reg=None) == [{"id": "aegis-1"}]
    assert feed_check.bd_in_progress(None, root=tmp_path, reg=None) == [{"id": "aegis-2"}]
    assert not calls, "a br deployment must never shell out to the retired bd"


def test_notify_injects_the_root_so_its_legs_can_reach_br():
    """The fix is only live if notify PASSES root — the helpers cannot resolve
    the backend without it, and a silent fallback to bd is the whole bug."""
    import inspect
    from shantytown import notify
    src = inspect.getsource(notify)
    for leg in ("_bd_ready(feed_check.bd_cwd(reg)", "bd_in_progress(feed_check.bd_cwd(reg)"):
        for line in src.splitlines():
            if leg in line:
                assert "root=root" in line, f"notify leg does not pass root: {line.strip()}"


def test_every_feed_check_leg_reaches_br_not_bd(monkeypatch, tmp_path):
    """aegis-mxgzh remainder: ready/in_progress were fixed and FOUR legs were not.

    Enumerated rather than fixed one-at-a-time, because that is how this bug kept
    coming back: arnold migrated queue_state, I migrated ready/in_progress, and
    the tend journal then found blocked/show/claim still spawning retired `bd` and
    failing loud every pass. This asserts the whole surface at once.
    """
    from shantytown import feed_check
    import shantytown.br as br

    monkeypatch.setattr(feed_check.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("spawned retired bd on a br deployment")))

    class _T:
        pass
    monkeypatch.setattr(feed_check, "_br_tracker", lambda root, reg: _T())
    monkeypatch.setattr(br, "ready", lambda t: [{"id": "r"}])
    monkeypatch.setattr(br, "in_progress", lambda t: [{"id": "a"}])
    monkeypatch.setattr(br, "blocked", lambda t: [{"id": "b"}])
    monkeypatch.setattr(br, "show", lambda t, i: {"id": i})
    claimed = []
    monkeypatch.setattr(br, "claim", lambda t, i: claimed.append(i))

    R = dict(root=tmp_path, reg=None)
    assert feed_check._bd_ready(None, **R) == [{"id": "r"}]
    assert feed_check.bd_in_progress(None, **R) == [{"id": "a"}]
    assert feed_check.bd_blocked(None, **R) == [{"id": "b"}]
    assert feed_check.bd_show(None, "aegis-1", **R) == {"id": "aegis-1"}
    feed_check.bd_claim(None, "aegis-1", **R)
    assert claimed == ["aegis-1"], "the dispatcher's WRITE must reach br, not bd"


def test_notify_threads_root_into_every_leg_it_calls():
    """A leg wired but not THREADED is inert — the whole reason this recurred.

    notify calls the module-level helpers directly on its production branch (the
    `self._bd_*` attributes are the TEST injection seam). If such a call omits
    root, the helper cannot resolve the backend and silently falls back to the
    retired binary — wired, deployed, and doing nothing.
    """
    import inspect, re
    from shantytown import notify

    legs = re.compile(r"\b(?:feed_check\.)?(bd_claim|bd_blocked|bd_show|_bd_ready|bd_in_progress)\(")
    offenders = []
    for line in inspect.getsource(notify).splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("def ") or "self._bd" in stripped:
            continue                      # comment, definition, or the injection seam
        # `root=root` inside __init__ and `root=self._root` inside a sweep are
        # both correct threadings — the difference is only which scope holds it.
        if legs.search(stripped) and not re.search(r"root=(root|self\._root)\b", stripped):
            offenders.append(stripped)
    assert not offenders, ("notify legs not threaded with root:\n  "
                           + "\n  ".join(offenders))


def test_the_stop_hook_haul_path_reaches_br(monkeypatch, tmp_path):
    """THE FIFTH LEG (aegis-mxgzh) — the one that mattered most.

    feed_check's five were migrated and stop_event's `_bd_json` was not. This is
    the AGENT'S OWN stop path: the thing that hands a worker its next haul item.
    While it spawned retired `bd` it raised into a fail-open, so agents stopped
    idle holding ready, assigned work and read as indecisive rather than
    unserved. Three of arnold's stops in one evening were this.
    """
    from shantytown import stop_event, feed_check

    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("stop hook spawned retired bd")))

    class _T:
        def _bd(self, *args):
            class R:
                returncode = 0
                stdout = '{"issues": [{"id": "aegis-1"}]}'
                stderr = ""
            return R()
    monkeypatch.setattr(feed_check, "_br_tracker", lambda root, reg: _T())

    rows = stop_event._bd_json(["ready", "--limit", "0"], None, root=tmp_path, reg=None)
    assert rows == [{"id": "aegis-1"}], "the stop hook must read the haul through br"


def test_stop_hook_threads_root_into_every_tracker_read():
    """A leg wired but not threaded is inert — the failure mode that made this
    bug survive three separate fixes."""
    import inspect, re
    from shantytown import stop_event
    offenders = [l.strip() for l in inspect.getsource(stop_event).splitlines()
                 if "_bd_json(" in l and "def _bd_json" not in l
                 and not l.strip().startswith("#")
                 and "root=root" not in l]
    assert not offenders, "stop_event legs not threaded:\n  " + "\n  ".join(offenders)


# --- at its ceiling is NOT free (aegis-qviejh) -------------------------------
#
# THE MEASURED DEFECT. On 2026-09-09 franklin showed `ceiling (items)` in
# `st crew` — verified twice — while stop_policy raised "1 free feedable worker
# (franklin)" THREE times in ten minutes, each demanding dispatch-or-say-why.
# gennaro was flagged twice the same morning at `ceiling (hours)`. The alert is
# unactionable by construction: the agent has been told to stop, the haul feed
# already refuses to feed it, and a dispatch there is at best wasted.
#
# THREE SURFACES, ONE FACT, AND ONLY TWO OF THEM ASKED. `st crew` renders the
# ceiling (aegis-9cobou) and the haul feed refuses it — but this function, the
# one Rule Zero and the idle-fleet alert SHARE, asked nothing. Its own docstring
# forbids a second opinion; it had a MISSING opinion instead.
#
# AND IT STOPPED BEING SELF-LIMITING. Before the ceiling was made sticky per
# session (aegis-hqbwci), a ceilinged agent's stretch rolled after 45 idle
# minutes and the false alert cleared itself — which is most of why this read as
# noise. With the ceiling held for the session, the false alert is now permanent
# until the agent is relaunched. The two fixes need each other.

def _budget_root(tmp_path, agent, *, items):
    """A root whose stats store puts `agent` at `items` haul items in one
    stretch, and a [session_budget] that ceilings at 4."""
    import sqlite3
    import time as _time
    from shantytown import stats
    (tmp_path / "shantytown.toml").write_text(
        "[session_budget]\nmax_hours = 4.0\nmax_items = 4\nmax_risk = 3\n",
        encoding="utf-8")
    now = _time.time()
    rows = [(now - 3.0 * 3600 + i * 120, agent, "tool", "sess-C", None)
            for i in range(91)]
    rows += [(now - (2.5 - i * 0.5) * 3600, agent, "haul", "sess-C", None)
             for i in range(items)]
    conn = sqlite3.connect(tmp_path / "stats.sqlite")
    conn.executescript(stats._SCHEMA)
    conn.executemany(
        "INSERT INTO events(ts, agent, kind, session, risk) VALUES (?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()
    return tmp_path


def _idle_wired_worker(tmp_path, name="franklin"):
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name=name, role="worker", pane=f"shanty-{name}")])
    panes = _Panes({f"shanty-{name}": IDLE},
                   {f"shanty-{name}": f"claude --settings {settings}"})
    return reg, panes


def test_a_worker_AT_ITS_CEILING_is_not_free(tmp_path):
    """franklin's case: idle pane, send wiring, launch-stamp irrelevant — every
    other gate says feedable, and it must still not be offered as free."""
    reg, panes = _idle_wired_worker(tmp_path)
    root = _budget_root(tmp_path, "franklin", items=4)
    # CONTROL: without the budget root this is exactly the "idle wired worker is
    # free" case above, so the exclusion below is the ceiling and nothing else.
    assert feed_check.free_feedable_workers(reg, panes, _Runtime()) == ["franklin"]
    assert feed_check.free_feedable_workers(
        reg, panes, _Runtime(), root=root) == []


def test_the_HOURS_ceiling_counts_too(tmp_path):
    """sattler's addendum: gennaro was flagged free at `ceiling (hours)`, so the
    exclusion must not be keyed on the items measure it was first seen with."""
    import sqlite3
    import time as _time
    from shantytown import stats
    reg, panes = _idle_wired_worker(tmp_path, "gennaro")
    (tmp_path / "shantytown.toml").write_text(
        "[session_budget]\nmax_hours = 4.0\nmax_items = 4\nmax_risk = 3\n",
        encoding="utf-8")
    now = _time.time()
    rows = [(now - 5.0 * 3600 + i * 120, "gennaro", "tool", "sess-D", None)
            for i in range(151)]           # 5h of dense work, ZERO haul items
    conn = sqlite3.connect(tmp_path / "stats.sqlite")
    conn.executescript(stats._SCHEMA)
    conn.executemany(
        "INSERT INTO events(ts, agent, kind, session, risk) VALUES (?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()
    from shantytown import session_budget as sb
    assert sb.at_ceiling(tmp_path, "gennaro").measure == "hours"
    assert feed_check.free_feedable_workers(
        reg, panes, _Runtime(), root=tmp_path) == []


def test_an_UNDER_ceiling_worker_is_STILL_FREE(tmp_path):
    """THE CONTROL THAT MATTERS. An exclusion that never releases would empty
    the feedable list and stop the fleet — a far more expensive bug than the
    nagging alert it fixes. Same store, same agent, one fewer item."""
    reg, panes = _idle_wired_worker(tmp_path)
    root = _budget_root(tmp_path, "franklin", items=3)
    assert feed_check.free_feedable_workers(
        reg, panes, _Runtime(), root=root) == ["franklin"]


def test_an_UNREADABLE_budget_never_withholds_an_agent(tmp_path):
    """Fail-open, like every other path in session_budget: the cost of a missed
    exclusion is one wasted dispatch, the cost of a wrong one is a worker
    silently kept from work with nothing saying so."""
    reg, panes = _idle_wired_worker(tmp_path)
    (tmp_path / "stats.sqlite").write_text("not a database", encoding="utf-8")
    (tmp_path / "shantytown.toml").write_text(
        "[session_budget]\nmax_items = 4\n", encoding="utf-8")
    assert feed_check.free_feedable_workers(
        reg, panes, _Runtime(), root=tmp_path) == ["franklin"]


def test_crew_and_feed_check_ask_THE_SAME_helper(tmp_path):
    """The two surfaces disagreeing IS the bug, so this asserts they share one
    implementation rather than asserting they happen to agree today."""
    import inspect
    import pathlib as _pl
    from shantytown import session_budget as sb

    src = inspect.getsource(feed_check.free_feedable_workers)
    assert "_sb.at_ceiling(root" in src, "feed_check must ask the shared helper"
    assert "sb.gate(" not in src, "…and must NOT re-derive it from gate()"

    # cli's crew table is a generator inside a long function, so read the module
    # file rather than fishing for the frame: the assertion is about there being
    # ONE implementation, and a second `gate()[2]` call site is what would break
    # it regardless of which function holds it.
    cli_src = _pl.Path(inspect.getfile(sb)).with_name("cli.py").read_text()
    assert "_sb.at_ceiling(budget_root" in cli_src, \
        "st crew must ask the shared helper too"
    assert "_sb.gate(Path(budget_root)" not in cli_src, \
        "the old private copy in cli must be gone, not merely bypassed"


def test_a_HELD_ceiling_also_withholds_the_agent(tmp_path):
    """THE COMPOSITION WITH aegis-hqbwci, proven rather than argued.

    Before the ceiling was made sticky, this false alert was self-limiting: a
    ceilinged agent's stretch rolled after 45 idle minutes, `gate` went quiet,
    and the disagreement closed on its own — which is most of why it read as
    noise rather than a defect. With the trip held for the session, `st crew`
    keeps rendering `ceiling (...)` indefinitely, so an unfixed
    free_feedable_workers would nag the coordinator about that agent for the
    rest of its session. The two fixes need each other and this asserts the
    join, not each half separately.
    """
    import json
    import sqlite3
    import time as _time
    from shantytown import session_budget as sb, stats

    reg, panes = _idle_wired_worker(tmp_path, "arnold")
    (tmp_path / "shantytown.toml").write_text(
        "[session_budget]\nmax_hours = 4.0\nmax_items = 4\nmax_risk = 3\n",
        encoding="utf-8")
    now = _time.time()
    trip_at = now - 81.3 * 60          # arnold's measured compliant idle
    rows = [(trip_at - 3.5 * 3600 + i * 120, "arnold", "tool", "sess-E", None)
            for i in range(106)]
    rows += [(trip_at - (3.0 - i) * 3600, "arnold", "haul", "sess-E", None)
             for i in range(4)]
    conn = sqlite3.connect(tmp_path / "stats.sqlite")
    conn.executescript(stats._SCHEMA)
    conn.executemany(
        "INSERT INTO events(ts, agent, kind, session, risk) VALUES (?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()

    trip = sb.read_spend(tmp_path, "arnold", trip_at)
    ceiling = sb.verdict(sb.limits_for(tmp_path), trip)
    assert ceiling is not None
    sb.mark_reported(tmp_path, "arnold", trip, ceiling)

    # the agent COMPLIES, idles past STRETCH_GAP_S, then stirs in the SAME session
    conn = sqlite3.connect(tmp_path / "stats.sqlite")
    conn.execute("INSERT INTO events(ts, agent, kind, session) VALUES (?,?,?,?)",
                 (now, "arnold", "tool", "sess-E"))
    conn.commit()
    conn.close()

    # the stretch really did roll — the LIVE verdict is clean ...
    limits, spend, _c = sb.gate(tmp_path, "arnold", now)
    assert spend.items == 0 and sb.verdict(limits, spend) is None
    # ... the ceiling is nonetheless HELD ...
    held = sb.at_ceiling(tmp_path, "arnold")
    assert held is not None and held.held
    # ... and that is enough to keep the agent out of the feedable list.
    assert feed_check.free_feedable_workers(
        reg, panes, _Runtime(), root=tmp_path) == []
    assert json.loads(
        (tmp_path / "session_budget" / "arnold.json").read_text())["session"] == "sess-E"


# ── aegis-uejki1: the feed list offered work NOBODY handed it could do ────────
#
# Measured against the live store 2026-09-12: `dispatchable` returned 12 beads
# and 4 were unactionable by any agent. The resulting `st tend` "12 ready" was
# read by an administrator as an idle agent having work, and fed a stop/relaunch
# decision. These arms encode the four real beads, by shape.

def test_a_human_blocked_bead_is_NOT_dispatchable():
    """aegis-z4y0w0 (a hypervisor-root resize, human-only) and aegis-btp8uc (a
    disk reclaim owned by a human). No agent it is handed to can clear it."""
    ready = [{"id": "aegis-z4y0w0", "title": "hypervisor root: resize a volume",
              "labels": ["automation", "blocked:human", "disk"]}]
    assert feed_check.dispatchable({"weaver"}, ready) == []


def test_blocked_external_is_treated_as_human_blocked():
    """0 in the ready pool today, included deliberately: it is the same KIND of
    fact as blocked:human and the pair is how the store spells one idea, so
    matching one half under-matches the moment somebody uses the other."""
    ready = [{"id": "aegis-1", "title": "waiting on a vendor",
              "labels": ["blocked:external"]}]
    assert feed_check.dispatchable({"weaver"}, ready) == []


def test_a_handoff_RECORD_is_NOT_dispatchable():
    """aegis-jyvtts, a kelly session handoff. A report someone wrote has no
    completion state, so "execute and close" is not a thing that can be done."""
    ready = [{"id": "aegis-jyvtts", "title": "kelly session handoff 2026-09-05",
              "labels": ["handoff"]}]
    assert feed_check.dispatchable({"weaver"}, ready) == []


def test_an_ANCHOR_bead_is_NOT_dispatchable():
    """aegis-9l283s says "do not close" in its own title: it exists to be
    referenced. Matched on the TITLE because it carries no distinguishing label
    (its labels are the ordinary infra,monitoring)."""
    ready = [{"id": "aegis-9l283s",
              "title": "ANCHOR (do not close): permanent inventory exception",
              "labels": ["infra", "monitoring"]}]
    assert feed_check.dispatchable({"weaver"}, ready) == []


def test_gt_escalation_is_STILL_dispatchable_and_that_is_deliberate():
    """The 304-bead escalation glut distorts the OPEN pool, not this one —
    measured 0 of 93 ready beads carry gt:escalation. Excluding it here would
    imply a hazard this path never had. If escalations ever DO reach ready this
    test is the thing that fails and forces the decision, rather than a silent
    guard that was never exercised."""
    ready = [{"id": "aegis-1", "title": "some check failed",
              "labels": ["gt:escalation", "severity:medium"]}]
    assert [b[0] for b in feed_check.dispatchable({"weaver"}, ready)] == ["aegis-1"]


def test_ordinary_unassigned_work_SURVIVES_the_new_filters():
    """The control. Four exclusions are easy to over-apply; these are the real
    shapes of the 8 beads that remained dispatchable after the fix, and they
    must not be caught by a label or prefix match."""
    ready = [
        {"id": "aegis-9rep3c", "title": "quipu /metrics is a full-store scan",
         "labels": ["quipu", "scaling", "upstream"]},
        {"id": "aegis-2og1jx", "title": "a unit stuck 'starting' not 'running'",
         "labels": ["infra", "host", "monitoring", "needs-triage"]},
        # "ANCHOR" must anchor at the START of the title, not match anywhere.
        {"id": "aegis-3", "title": "rewrite the ANCHOR handling in foo",
         "labels": ["infra"]},
    ]
    got = [b[0] for b in feed_check.dispatchable({"weaver"}, ready)]
    assert got == ["aegis-9rep3c", "aegis-2og1jx", "aegis-3"]


def test_an_UNASSIGNED_message_is_NOT_dispatchable():
    """The assignee test alone hid this by accident: messages are normally
    assigned to their recipient. Re-pooling clears an assignee while leaving the
    bead ready (hauls' docstring documents that path), and an unassigned message
    would then be offered to a worker as work. 59 of 93 ready beads were messages
    when this was measured (aegis-uejki1), so the population is not marginal."""
    ready = [{"id": "aegis-1", "title": "inbox: [from wu] some pointer"},
             {"id": "aegis-2", "title": "mail: a legacy-prefixed one"}]
    assert feed_check.dispatchable({"weaver"}, ready) == []
