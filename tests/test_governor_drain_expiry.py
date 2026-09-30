"""A governor DRAIN must not outlive the window it was issued for (aegis-l2m4t2).

THE INCIDENT. Six drains were delivered 2026-09-29 21:14Z while the Claude
seven_day budget sat at ~99%. The window reset at 23:00:49Z and read 6%. The
tier relaxed, the ledger was cleared — and the delivered messages stayed OPEN,
because nothing ever took a durable drain back. wu, relaunched at ~00:45Z, read
its drain on startup and stopped itself holding a ready P1; gennaro drained
after the reset holding a ready P0. The fleet drained against a budget that no
longer existed.

And every one of those six said "95% usage tier, at 6%": the message quoted the
DEFAULT window's reading (five_hour) beside a seven_day tier — the aegis-cjjdx
display trap again, making a correct drain look absurd and a stale one
impossible to recognise.

The arms, both directions, as the bead asked:
  * a stale drain is CLOSED (tier relaxed; episode superseded; tier no longer
    excludes the agent);
  * a live drain is KEPT and still reaches the agent;
  * a lost signal retracts NOTHING and forgets nothing (never fail either way);
  * a failed retraction is retried, never forgotten.
"""
from __future__ import annotations

from shantytown import config, governor as gov
from shantytown.inbox import FilesInbox
from shantytown.protocols import Agent
from shantytown.stopped import FilesStops

FIVE, SEVEN = gov.FIVE_HOUR, gov.SEVEN_DAY
NOW = 1_790_708_089.0            # 2026-09-29 21:14:49Z
RESET = 1_790_722_849.0          # the seven_day reset, 23:00:49Z
HOUR = 3600.0
TIM = Agent(name="tim", pane="p-tim", roles=("worker",))

LADDER = """
[governor]
source = "stub"
relax_margin = 5

[[governor.tier]]
at = 50
window = "five_hour"
min_priority = 1

[[governor.tier]]
at = 95
window = "seven_day"
action = "drain"
"""


def _verdict(tmp_path, seven, *, five=6.0, ok=True, at=None):
    (tmp_path / "shantytown.toml").write_text(LADDER)
    policy = config.load(tmp_path).governor
    reader = gov.StubReader(pct={FIVE: five, SEVEN: seven},
                            at=NOW if at is None else at, ok=ok, now=lambda: NOW,
                            resets={FIVE: NOW + HOUR, SEVEN: RESET})
    return gov.Governor(policy, reader, gov.FilesGovernorState(tmp_path),
                        now=lambda: NOW).evaluate(persist=False)


class _World:
    """The real FilesInbox and the real Drainer, wired the way tend wires them."""

    def __init__(self, tmp_path, *, fail_retract=False):
        self.tmp = tmp_path
        self.inbox = FilesInbox(tmp_path / "inbox")
        self.fail_retract = fail_retract
        self.logs: list[str] = []

    def _retract(self, who, msg_id):
        if self.fail_retract:
            raise RuntimeError("store unreachable")
        self.inbox.mark_read(who, [msg_id])

    def sweep(self, verdict, episode=1.0):
        d = gov.Drainer(self.tmp, deliver=lambda w, b: self.inbox.deliver(w, b),
                        stops=FilesStops(self.tmp / "stopped"),
                        log=self.logs.append, now=lambda: NOW,
                        retract=self._retract)
        return d.sweep([TIM], verdict, episode, live=lambda a: True)

    def open_drains(self):
        return [m for m in self.inbox.unread("tim") if m.body.startswith("DRAIN")]


# --- the incident, replayed ---------------------------------------------------

def test_THE_2026_09_29_INCIDENT_a_drain_is_taken_back_when_its_window_resets(tmp_path):
    w = _World(tmp_path)
    w.sweep(_verdict(tmp_path, 99.0))
    assert len(w.open_drains()) == 1, "control: the live drain was delivered"

    # 23:00:49Z — the window reset; the next pass reads 6%.
    w.sweep(_verdict(tmp_path, 6.0))
    assert w.open_drains() == [], (
        "a drain from a window that has RESET is still open — a fresh session "
        "will read it and stop itself against a budget that refilled")
    assert gov.DrainLedger(tmp_path).agents() == []
    assert any("RETRACTED" in m for m in w.logs), "the take-back must be visible"


def test_a_LIVE_drain_is_kept_and_obeyable(tmp_path):
    """The other arm: while the tier holds, repeated passes must neither re-send
    nor retract. Retracting a live drain would be an off switch for the tier."""
    w = _World(tmp_path)
    v = _verdict(tmp_path, 99.0)
    for _ in range(3):
        w.sweep(v)
    assert len(w.open_drains()) == 1
    assert not any("RETRACTED" in m for m in w.logs)


def test_the_drain_quotes_ITS_OWN_window_and_says_when_it_is_void(tmp_path):
    w = _World(tmp_path)
    w.sweep(_verdict(tmp_path, 99.0, five=6.0))
    [msg] = w.open_drains()
    assert "seven_day 95% tier, at 99%" in msg.body, msg.body
    assert "at 6%" not in msg.body, "quoted the five_hour reading beside a seven_day tier"
    assert "VOID after 09-29 23:00Z" in msg.body, msg.body


def test_the_stamped_drain_still_fits_the_durable_channel():
    body = gov.drain_message("a-very-long-agent-name", 95, 99.5,
                             window=SEVEN, reset_at=RESET)
    assert len(body.encode()) <= 493, f"{len(body.encode())} bytes"
    # The longest reading text is "signal high"; a 16-char name still fits.
    body = gov.drain_message("sixteen-chars-xx", 95, None, window=SEVEN,
                             reset_at=RESET)
    assert len(body.encode()) <= 493, f"{len(body.encode())} bytes"
    assert body.isascii(), "a multibyte character spends 3 bytes of a 493 cap"


# --- never fail in either direction -------------------------------------------

def test_a_LOST_signal_retracts_nothing_and_forgets_nothing(tmp_path):
    """Blind is blind both ways. And the ledger must survive the outage: clearing
    it (the old behaviour) threw away the only record of which messages were out,
    so a window that reset DURING an outage could never be cleaned up after."""
    w = _World(tmp_path)
    w.sweep(_verdict(tmp_path, 99.0))
    w.sweep(_verdict(tmp_path, 99.0, at=NOW - 10_000))       # stale -> signal lost
    assert len(w.open_drains()) == 1, "a blind governor took a drain back"
    assert gov.DrainLedger(tmp_path).agents() == ["tim"], "the ledger was forgotten"

    w.sweep(_verdict(tmp_path, 6.0))                          # signal back, reset
    assert w.open_drains() == [], "and once it can see again, it cleans up"


def test_a_failed_retraction_is_retried_not_forgotten(tmp_path):
    w = _World(tmp_path)
    w.sweep(_verdict(tmp_path, 99.0))
    w.fail_retract = True
    w.sweep(_verdict(tmp_path, 6.0))
    assert len(w.open_drains()) == 1
    assert gov.DrainLedger(tmp_path).agents() == ["tim"], (
        "forgetting a drain we could not close recreates the incident")
    assert any("could NOT retract" in m for m in w.logs)

    w.fail_retract = False
    w.sweep(_verdict(tmp_path, 6.0))
    assert w.open_drains() == []


def test_a_new_episode_SUPERSEDES_the_old_drain(tmp_path):
    """A drain re-issued in a new episode must not leave the old one open beside
    it: two open drains, one stale, is the same trap with extra steps."""
    w = _World(tmp_path)
    w.sweep(_verdict(tmp_path, 99.0), episode=1.0)
    [first] = w.open_drains()
    w.sweep(_verdict(tmp_path, 99.0), episode=2.0)
    [second] = w.open_drains()
    assert second.id != first.id


def test_an_already_closed_drain_is_retracted_quietly(tmp_path):
    """The agent obeyed and closed it itself. Taking back a closed message is a
    no-op, not an error that holds the ledger open forever."""
    w = _World(tmp_path)
    w.sweep(_verdict(tmp_path, 99.0))
    w.inbox.mark_read("tim")
    w.sweep(_verdict(tmp_path, 6.0))
    assert gov.DrainLedger(tmp_path).agents() == []
    assert not any("could NOT" in m for m in w.logs)


# --- the wiring tend actually runs --------------------------------------------

class _Panes:
    def exists(self, pane):
        return True


def test_TENDS_drain_sweep_takes_the_drain_back_through_the_inbox(tmp_path, monkeypatch):
    """The Drainer arms above prove the mechanism. This proves tend's call site
    hands it a retract that reaches the same inbox the drain was delivered to —
    a Drainer built without `retract` would pass every arm above and fix nothing."""
    import argparse
    from shantytown import cli
    monkeypatch.delenv("SHANTY_BACKEND", raising=False)
    a = argparse.Namespace(root=str(tmp_path), backend="files", me="st fleet tend")
    live = _verdict(tmp_path, 99.0)
    cli._drain_sweep(a, live, [TIM], _Panes(), episode=1.0)
    inbox = FilesInbox(tmp_path / "inbox")
    assert [m.body[:5] for m in inbox.unread("tim")] == ["DRAIN"], \
        "control: tend delivered the drain to the files inbox"

    cli._drain_sweep(a, _verdict(tmp_path, 6.0), [TIM], _Panes(), episode=1.0)
    assert inbox.unread("tim") == []
