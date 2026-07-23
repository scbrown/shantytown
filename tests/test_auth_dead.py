"""AUTH-DEAD: login expired, every API call fails, and the pane renders `idle`.

internal-ref. Measured 2026-07-22: an operator re-login rotated the shared
credential and all 9 live crew went `● Login expired · Please run /login` at
once — ready UI up, input box empty, so every roster surface said `idle`. They
were counted feedable (Rule Zero held the coordinator hostage to dead panes),
tend's cycle driver prompted one over and over into the banner, and recovery was
nine by-hand `st stop` + `st new`. Every test here pins one leg of the fix:
the state is NAMED, excluded from feedable, reported by tend, and recovered by
ONE command (`st tend --reauth`).
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import cli, feed_check, tend as tend_mod, triage
from shantytown.protocols import Agent
from shantytown.runtime import ClaudeRuntime, auth_expired
from shantytown.tmux import NullPanes


# The banner as MEASURED — read verbatim off 8 live auth-dead crew panes,
# 2026-07-22, not quoted from a doc. The shape matters: the runtime's own
# response line at column 0, the ready UI still up, the input box empty.
BANNER = "● Login expired · Please run /login"
AUTH_DEAD_PANE = (
    f"{BANNER}\n"
    "\n"
    "✻ Churned for 0s\n"
    "\n"
    "❯ \n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
)
IDLE_PANE = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
BUSY_PANE = "✻ Envisioning… (12s · 4.1k tokens · esc to interrupt)"


def _rt(panes=None):
    return ClaudeRuntime(panes or NullPanes(), lambda _c: None)


# --- part 1: the runtime reads its own banner --------------------------------

def test_the_measured_auth_dead_pane_is_detected():
    assert _rt().auth_dead(AUTH_DEAD_PANE) is True


def test_an_idle_pane_is_not_auth_dead():
    assert _rt().auth_dead(IDLE_PANE) is False


def test_the_banner_deep_in_scrollback_is_history_not_a_state():
    """The banner from an expiry already healed scrolls up; a pane whose TAIL is
    healthy is healthy. Whole-screen matching would re-kill a recovered agent."""
    screen = AUTH_DEAD_PANE + "\n" + "output line\n" * 10 + IDLE_PANE
    assert _rt().auth_dead(screen) is False


def test_a_quoted_banner_is_not_a_state_line_anchor():
    """An agent GREPPING a dead pane's scrollback prints
    `sess: 1484:● Login expired · …` — measured in the session that wrote this
    predicate. A substring match would have called that agent auth-dead on the
    spot; the line anchor is what refuses it."""
    screen = f"aegis-crew-arnold: 1484:{BANNER}\n" + IDLE_PANE
    assert _rt().auth_dead(screen) is False


def test_trailing_blank_lines_do_not_push_the_banner_out():
    """kelly's blank-padding lesson, auth edition: blank padding is not content
    and does not get to spend the tail window."""
    assert _rt().auth_dead(AUTH_DEAD_PANE + "\n" * 6) is True


def test_a_runtime_that_reads_no_panes_answers_false_not_a_crash():
    """auth_expired can only ever CONVERT a verdict into auth-dead, so
    could-not-ask safely leaves the verdict as it was — same argument as
    asks_a_question, and the same tolerance for codex."""
    class NoPanesRuntime:
        pass
    assert auth_expired(NoPanesRuntime(), AUTH_DEAD_PANE) is False


# --- part 2: work_state names it, in the right precedence --------------------

def test_an_auth_dead_pane_is_AUTH_DEAD_not_idle():
    got = triage.work_state(AUTH_DEAD_PANE, ui_up=True, auth_dead=True)
    assert got == triage.AUTH_DEAD


def test_without_the_flag_the_same_pane_reads_idle_the_measured_bug():
    """The negative control IS the incident: every predicate work_state already
    had called this pane idle. If this test ever fails, the banner started
    matching some other marker and the auth flag stopped being load-bearing."""
    assert triage.work_state(AUTH_DEAD_PANE, ui_up=True) == triage.IDLE


def test_busy_beats_auth_dead():
    """A pane genuinely computing has working auth by construction — the flag
    must never convert a busy agent (a stale banner above a live spinner is
    history, not a state)."""
    assert triage.work_state(BUSY_PANE, ui_up=True, auth_dead=True) == triage.BUSY


def test_auth_dead_beats_waiting():
    """A picker on a pane that cannot make an API call is not a question a
    person needs to run answer — the session is dead either way."""
    got = triage.work_state(IDLE_PANE, ui_up=True, awaiting=True, auth_dead=True)
    assert got == triage.AUTH_DEAD


def test_auth_dead_beats_saturated():
    """The measured compound state: arnold was saturated AND auth-dead, and the
    cycle driver prompted the dead pane over and over. AUTH_DEAD outranking
    SATURATED is what stops the prompt loop."""
    sat = ("❯ \n"
           "                  new task? /clear to save 687.8k tokens\n"
           "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents")
    assert triage.work_state(sat, ui_up=True, auth_dead=True) == triage.AUTH_DEAD


# --- part 3: feed_check must not count a dead pane as feedable ---------------

class _Reg:
    def __init__(self, agents):
        self._a = agents

    def all(self):
        return self._a


class _Panes(NullPanes):
    def __init__(self, screens, cmdlines=None, **kw):
        super().__init__(live=set(screens), cmdlines=cmdlines, **kw)
        self._screens = screens

    def capture(self, pane, history=0, attrs=False):
        return self._screens.get(pane, "")


def _send_settings(tmp_path):
    p = tmp_path / "worker.settings.json"
    p.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "python -m shantytown.stop_event send"}]}]}}))
    return p


def test_an_auth_dead_wired_worker_is_NOT_feedable(tmp_path):
    """The Rule Zero interaction: nine feedable-looking corpses BLOCKED the
    coordinator's stop. A dead pane must never hold the coordinator hostage."""
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="weaver", role="worker", pane="p-w")])
    panes = _Panes({"p-w": AUTH_DEAD_PANE},
                   cmdlines={"p-w": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _rt(panes)) == []


def test_the_positive_control_an_idle_wired_worker_still_is(tmp_path):
    settings = _send_settings(tmp_path)
    reg = _Reg([Agent(name="weaver", role="worker", pane="p-w")])
    panes = _Panes({"p-w": IDLE_PANE},
                   cmdlines={"p-w": f"claude --settings {settings}"})
    assert feed_check.free_feedable_workers(reg, panes, _rt(panes)) == ["weaver"]


# --- part 4: st crew names it, distinct from idle ----------------------------

class _Args:
    def __init__(self, root):
        self.root = Path(root)
        self.backend = "files"; self.repo = None; self.registry = "files"


def _roster(tmp_path, cards):
    crew = tmp_path / "crew"; crew.mkdir()
    for name, pane in cards.items():
        (crew / f"{name}.json").write_text(
            json.dumps({"role": "worker", "pane": pane}))
    return tmp_path


def test_crew_shows_auth_dead_as_a_distinct_state_not_idle(tmp_path, monkeypatch, capsys):
    root = _roster(tmp_path, {"ellie": "p-ellie", "ian": "p-ian"})
    panes = _Panes({"p-ellie": AUTH_DEAD_PANE, "p-ian": IDLE_PANE})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)

    assert cli._cmd_crew(_Args(root)) == cli.OK
    out = capsys.readouterr().out
    rows = {ln.split()[0]: ln for ln in out.splitlines()
            if ln.split() and ln.split()[0] in {"ellie", "ian"}}
    assert triage.AUTH_DEAD in rows["ellie"]
    assert "1 free: ian" in out                    # ellie is NOT on the free list
    assert "AUTH-DEAD" in out                      # and the summary names her
    assert "st tend --reauth" in out               # with the remedy


# --- part 5: tend reports it as a FAULT, and does not auto-relaunch ----------

class _TendRuntime:
    """Real banner reading (delegated to ClaudeRuntime's marker logic), fake
    lifecycle, so the tender is driven without tmux."""
    name = "fake"

    def __init__(self):
        self.started = []
        self._probe = _rt()

    def shows_ready_ui(self, screen):
        return "shift+tab to cycle" in screen or "? for shortcuts" in screen

    def auth_dead(self, screen):
        return self._probe.auth_dead(screen)

    def start(self, card, pane):
        self.started.append((card.name, pane))


class _Launches:
    def verdict(self, name):
        return "current"


def test_tend_reports_auth_dead_as_a_fault_and_touches_nothing(tmp_path):
    settings = _send_settings(tmp_path)
    panes = _Panes({"p-w": AUTH_DEAD_PANE},
                   cmdlines={"p-w": f"claude --settings {settings}"})
    runtime = _TendRuntime()
    tender = tend_mod.Tender(panes, runtime, _Launches(),
                             spawn=runtime.start,
                             ensure=lambda card: card.workspace)
    rep = tender.pass_over([Agent(name="weaver", role="worker", pane="p-w")])
    (f,) = rep.findings
    assert f.verdict == tend_mod.AUTH_DEAD
    assert not f.acted                       # reported, never auto-relaunched
    assert "st tend --reauth" in f.why       # the remedy is named
    assert not rep.healthy()                 # exit code 2: alertable
    assert runtime.started == []


# --- part 6: the one command — st tend --reauth ------------------------------

class _ReauthPanes(_Panes):
    """After a relaunch the pane must show a FRESH screen, not the dead one —
    new_session hands out a ready pane, the way a real relaunch reads the
    refreshed credential."""

    def new_session(self, name):
        got = super().new_session(name)
        self._screens[name] = IDLE_PANE
        return got


class _ReauthArgs(_Args):
    dry_run = False


def _reauth_fixture(tmp_path, monkeypatch, screens, owned):
    root = _roster(tmp_path, {n: f"p-{n}" for n in screens})
    panes = _ReauthPanes({f"p-{n}": s for n, s in screens.items()},
                         owned={f"p-{n}" for n in owned})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    monkeypatch.setattr(cli, "_runtime", lambda a, p: _CliRuntime())
    monkeypatch.setattr(cli, "_refresh_clone", lambda path: None)
    monkeypatch.setattr(cli, "_LIVE_ATTEMPTS", 1)
    monkeypatch.setattr(cli, "_LIVE_DELAY", 0)
    return root, panes


class _CliRuntime(_TendRuntime):
    def is_live(self, screen):
        return self.shows_ready_ui(screen) and not self.auth_dead(screen)


def test_reauth_relaunches_every_auth_dead_agent_and_only_them(tmp_path, monkeypatch, capsys):
    root, panes = _reauth_fixture(
        tmp_path, monkeypatch,
        screens={"ellie": AUTH_DEAD_PANE, "ian": AUTH_DEAD_PANE,
                 "malcolm": IDLE_PANE, "sattler": BUSY_PANE},
        owned={"ellie", "ian", "malcolm", "sattler"})
    a = _ReauthArgs(root)
    a.reauth = True
    assert cli._tend_reauth(a) == cli.OK
    out = capsys.readouterr().out
    # both dead agents came back, the idle and busy ones were never touched
    assert panes.capture("p-ellie") == IDLE_PANE
    assert panes.capture("p-ian") == IDLE_PANE
    assert panes.capture("p-malcolm") == IDLE_PANE
    assert panes.capture("p-sattler") == BUSY_PANE
    assert "relaunching 2 auth-dead agent(s): ellie, ian" in out
    assert "2 agent(s) relaunched and observed live" in out
    # the honest boundary is printed: live is not authed
    assert "live is not authed" in out


def test_reauth_with_nothing_dead_does_nothing(tmp_path, monkeypatch, capsys):
    root, panes = _reauth_fixture(tmp_path, monkeypatch,
                                  screens={"ellie": IDLE_PANE}, owned={"ellie"})
    a = _ReauthArgs(root)
    assert cli._tend_reauth(a) == cli.OK
    assert "no auth-dead agents" in capsys.readouterr().out
    assert panes.capture("p-ellie") == IDLE_PANE


def test_reauth_dry_run_touches_nothing(tmp_path, monkeypatch, capsys):
    root, panes = _reauth_fixture(tmp_path, monkeypatch,
                                  screens={"ellie": AUTH_DEAD_PANE}, owned={"ellie"})
    a = _ReauthArgs(root)
    a.dry_run = True
    assert cli._tend_reauth(a) == cli.OK
    out = capsys.readouterr().out
    assert "would: kill p-ellie and relaunch ellie" in out
    assert panes.capture("p-ellie") == AUTH_DEAD_PANE   # still there, untouched


def test_reauth_refuses_a_session_st_does_not_own(tmp_path, monkeypatch, capsys):
    """The st stop rule, fleet edition: a name match is not permission to kill."""
    root, panes = _reauth_fixture(tmp_path, monkeypatch,
                                  screens={"ellie": AUTH_DEAD_PANE}, owned=set())
    a = _ReauthArgs(root)
    assert cli._tend_reauth(a) == cli.REFUSED
    assert "not permission to kill" in capsys.readouterr().err
    assert panes.capture("p-ellie") == AUTH_DEAD_PANE   # alive, unkilled


def test_reauth_never_relaunches_a_retired_agent(tmp_path, monkeypatch, capsys):
    """Retirement is honoured everywhere or it is not honoured: auth-death must
    not become a side door to respawning what was deliberately stopped."""
    root = _roster(tmp_path, {"ian": "p-ian"})
    crew = root / "crew"
    (crew / "ellie.json").write_text(json.dumps(
        {"role": "worker", "pane": "p-ellie", "retired": True}))
    panes = _ReauthPanes({"p-ellie": AUTH_DEAD_PANE, "p-ian": IDLE_PANE},
                         owned={"p-ellie", "p-ian"})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    monkeypatch.setattr(cli, "_runtime", lambda a, p: _CliRuntime())
    a = _ReauthArgs(root)
    assert cli._tend_reauth(a) == cli.OK
    out = capsys.readouterr().out
    assert "RETIRED" in out and "not relaunching" in out
    assert panes.capture("p-ellie") == AUTH_DEAD_PANE   # untouched
