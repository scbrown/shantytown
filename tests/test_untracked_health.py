"""untracked_health — out-of-band 'has the fail-open nudge ever run?' (aegis-06ue4).

Every branch is pinned by MECHANISM with injected I/O — the whole point of the
module is to tell apart states that look identical from outside (ran / never-ran /
dead-hook / idle / not-wired / cannot-tell), so each is asserted separately here.
"""
from __future__ import annotations

from shantytown.protocols import Agent
from shantytown.untracked_health import (CANNOT_TELL, GRACE_S, IDLE, NEVER_WIRED,
                                          RAN, SUSPECT, TOO_SOON, check, render,
                                          worst_exit)

NOW = 1_000_000.0
WIRED = '{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"python -m shantytown.untracked --root /r"}]}]}}'
BARE = '{"hooks":{"PreToolUse":[]}}'


def _agents(*specs):
    # specs: (name, role)
    return [Agent(name=n, role=r, workspace=f"/ws/{n}") for n, r in specs]


def _run(agents, *, ledger=None, launched=None, active=None, consent=None):
    """Wire the injected readers from plain dicts keyed by agent name."""
    ledger = ledger or {}
    launched = launched or {}
    consent = consent or {}
    return check(
        agents, "/r", now=NOW,
        ledger_stat=lambda p: ledger.get(p.stem),  # /r/untracked/<name>.json -> name
        launch_time=lambda n: launched.get(n),
        pane_active=(None if active is None else (lambda n: active.get(n))),
        consent_read=lambda p: consent.get(str(p)),
    )


def _consent(name, body):
    return {f"/ws/{name}/.claude/settings.local.json": body}


def _by(rows):
    return {r.agent: r for r in rows}


def test_ledger_present_means_ran():
    rows = _run(_agents(("w", "worker")),
                ledger={"w": NOW - 120}, launched={"w": NOW - 3600},
                consent=_consent("w", WIRED))
    assert _by(rows)["w"].verdict == RAN
    assert "2m ago" in _by(rows)["w"].detail


def test_admin_is_skipped_entirely():
    # No ledger, launched long ago, pane active — would be SUSPECT for a worker.
    rows = _run(_agents(("boss", "administrator")),
                launched={"boss": NOW - 9999}, active={"boss": True},
                consent=_consent("boss", BARE))
    assert rows == []  # structurally exempt: not even a reassuring row


def test_no_ledger_recent_launch_is_too_soon():
    rows = _run(_agents(("w", "worker")),
                launched={"w": NOW - (GRACE_S / 2)},
                consent=_consent("w", WIRED))
    assert _by(rows)["w"].verdict == TOO_SOON


def test_no_ledger_old_launch_active_pane_is_suspect():
    rows = _run(_agents(("w", "worker")),
                launched={"w": NOW - (GRACE_S * 3)}, active={"w": True},
                consent=_consent("w", WIRED))
    r = _by(rows)["w"]
    assert r.verdict == SUSPECT
    assert "may be dying" in r.detail
    assert worst_exit(rows) == 1


def test_no_ledger_old_launch_idle_pane_is_benign():
    rows = _run(_agents(("w", "worker")),
                launched={"w": NOW - (GRACE_S * 3)}, active={"w": False},
                consent=_consent("w", WIRED))
    assert _by(rows)["w"].verdict == IDLE
    assert worst_exit(rows) == 0


def test_no_ledger_old_launch_unknown_activity_is_cannot_tell():
    # pane_active reader absent -> must NOT assert SUSPECT; idle vs dead unknown.
    rows = _run(_agents(("w", "worker")),
                launched={"w": NOW - (GRACE_S * 3)},
                consent=_consent("w", WIRED))
    assert _by(rows)["w"].verdict == CANNOT_TELL
    assert worst_exit(rows) == 2


def test_non_admin_without_hook_is_never_wired():
    rows = _run(_agents(("w", "worker")),
                ledger={"w": NOW - 60}, launched={"w": NOW - 9999},
                consent=_consent("w", BARE))
    # The ledger is irrelevant when the hook is not even wired — reachability
    # is the prior question, and it fails.
    assert _by(rows)["w"].verdict == NEVER_WIRED
    assert worst_exit(rows) == 1


def test_unreadable_consent_is_cannot_tell_not_a_pass():
    rows = _run(_agents(("w", "worker")),
                launched={"w": NOW - 9999})  # no consent entry -> read returns None
    assert _by(rows)["w"].verdict == CANNOT_TELL
    assert worst_exit(rows) == 2


def test_wired_no_ledger_unknown_launch_is_cannot_tell():
    # launch time unknown -> cannot apply the grace window -> must not guess SUSPECT.
    rows = check(_agents(("w", "worker")), "/r", now=NOW,
                 launch_time=lambda n: None,
                 pane_active=lambda n: True,
                 consent_read=lambda p: WIRED)
    assert _by(rows)["w"].verdict == CANNOT_TELL


def test_worst_exit_cannot_tell_outranks_fault():
    rows = _run(_agents(("a", "worker"), ("b", "worker")),
                launched={"a": NOW - 9999, "b": NOW - 9999},
                active={"a": True},                       # a -> SUSPECT (fault)
                consent=_consent("a", WIRED) | _consent("b", WIRED))  # b -> cannot-tell
    assert worst_exit(rows) == 2  # cannot-tell outranks the fault


def test_render_is_scannable_and_marks_faults():
    rows = _run(_agents(("good", "worker"), ("bad", "worker")),
                ledger={"good": NOW - 60},
                launched={"good": NOW - 9999, "bad": NOW - 9999},
                active={"bad": True},
                consent=_consent("good", WIRED) | _consent("bad", WIRED))
    out = render(rows)
    assert "***" in out                 # the fault is marked
    assert "bad" in out and "good" in out
    # findings sort first
    assert out.index("bad") < out.index("good")


def test_render_empty_when_all_admins():
    assert "admin-exempt" in render([])
