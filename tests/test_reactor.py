"""reactor adapter tests. One of these is the entire reason the module exists.

test_green_and_dead_is_not_live reproduces the real outage: reactor answering
/health with 200, up{job=reactor}==1, systemd active(running), no alerts firing
— and ZERO events delivered, for months, while a directive told 14 agents it was
working (aegis-uyvs / lfhk / r2r0 / 5lnp).

If this adapter cannot tell that state from a healthy one, it is another light
where a detector should be, and shantytown has reproduced the bug it was written
in reaction to.
"""
from __future__ import annotations

import pytest

from shantytown.reactor import Liveness, NoReactor, Reactor, _counter

# A real capture from dolt.lan:8075, 2026-07-16. Not invented.
REAL = """\
reactor_uptime_seconds 749.3
reactor_events_processed_total 27
reactor_events_dispatched_total 27
reactor_pending_decisions 0
reactor_catchup_commits_remaining 0
reactor_mode 0
reactor_last_event_timestamp 1784180913.885
reactor_events_by_type{event_type="bead.assigned"} 4
reactor_events_by_type{event_type="bead.closed"} 1
reactor_events_by_type{event_type="bead.commented"} 13
"""

# The same page as it looked during the outage: everything green, counter at 0.
GREEN_AND_DEAD = REAL.replace("_processed_total 27", "_processed_total 0") \
                     .replace("_dispatched_total 27", "_dispatched_total 0")


class _Fake(Reactor):
    def __init__(self, body=None, boom=None):
        super().__init__()
        self._body, self._boom = body, boom

    def _metrics(self):
        if self._boom:
            raise self._boom
        return self._body


NOW = 1784180913.885 + 10.0     # 10s after REAL's last event


def test_live_when_events_are_flowing():
    lv = _Fake(REAL).liveness(now=NOW)
    assert lv.verdict == "live"
    assert lv.delivered == 27
    assert "27 events delivered" in lv.render()


def test_idle_is_not_live_and_is_not_dead_either():
    """TWO WRONG ANSWERS, BOTH MINE, TWENTY MINUTES APART (aegis-k6hv).

    v1: `live if delivered > 0`. A cumulative counter never goes down, so it says
        "live" a week after death — up==1 with a bigger number. NEVER FIRES.
    v2: `idle > 300s -> STALLED`. I reported reactor STALLED to its owner. It was
        not. It was IDLE: 2 writes to beads_aegis in 15 min because the crew had
        gone to bed. The counter moved 27 -> 46 the moment work arrived. CRIES
        WOLF EVERY NIGHT — and a detector that cries wolf gets silenced, and then
        misses the real death anyway.
    v3 (this): an aging last_event means "nothing happened" OR "I stopped
        looking" — THE SAME READING FOR OPPOSITE STATES. reactor exposes no
        last-poll timestamp, so it is not knowable from here. Say so.
    """
    lv = _Fake(REAL).liveness(now=1784180913.885 + 1200)   # 20 min — above the measured 788s max
    assert lv.reachable is True
    assert lv.delivered == 27, "the counter looks healthy — that is v1's trap"
    assert lv.verdict == "cannot tell", (
        "reported a verdict the metrics cannot support: idle and dead are the "
        "same reading here"
    )
    out = lv.render()
    assert "Idle or dead" in out
    assert "live" not in out.replace("delivered", "")


def test_backlogged_is_dead_not_idle():
    """dearing's discriminator (aegis-4s5d): quiet cannot explain a BACKLOG.

    pending > 0 AND nothing processed = work is waiting and reactor is not doing
    it. That is the one reading which separates idle from dead, and it is a true
    positive whenever it fires.
    """
    body = REAL.replace("reactor_catchup_commits_remaining 0",
                        "reactor_catchup_commits_remaining 9")
    lv = _Fake(body).liveness(now=1784180913.885 + 1200)
    assert lv.pending == 9
    assert lv.verdict == "BACKLOGGED"
    assert "Quiet does not explain a backlog" in lv.render()


def test_pending_zero_does_NOT_prove_healthy():
    """THE TRAP IN THE DISCRIMINATOR, and it is in the metric's own HELP text.

    "Commits remaining in INITIAL CATCHUP" — it tracks the STARTUP backlog, not
    steady-state pending work. A reactor that dies after catchup reads 0,
    identically to a healthy idle one. Nobody has ever seen it above 0.

    So pending==0 + stale must stay CANNOT TELL. Downgrading it to "live" would
    build a third detector that cannot fire — v1's failure mode again, from a
    metric whose own documentation says it will not help.
    """
    lv = _Fake(REAL).liveness(now=1784180913.885 + 1200)   # pending==0, 10min idle
    assert lv.pending == 0
    assert lv.verdict == "cannot tell", (
        "pending==0 was treated as proof of health — but the metric only counts "
        "INITIAL catchup, so 0 is what a dead steady-state reactor also reports"
    )


def test_600s_of_silence_is_INSIDE_the_measured_distribution():
    """THE THRESHOLD WAS BELOW NORMAL AND THE MEASUREMENT PROVED IT (aegis-8qk1).

    Measured end-to-end latency on beads_aegis: n=66, min=0s, avg=45s, MAX=788s.
    The old QUIET_AFTER_S was 300 — **3 of 66 healthy events exceeded it**. All
    three of tonight's reported "stalls" sat inside this distribution.

    So 600s of silence, which I personally reported as a stall, must read as
    live: it is well inside normal. This is the regression test for the false
    alarm I sent to reactor's owner.
    """
    lv = _Fake(REAL).liveness(now=1784180913.885 + 600)
    assert lv.verdict == "live", (
        "600s read as trouble — but the measured max is 788s, so this is normal"
    )


def test_quiet_fleet_does_not_read_as_broken():
    """THE FALSE ALARM I ACTUALLY SENT. Regression test for it.

    A real, healthy reactor on a sleeping fleet must NOT be reported broken.
    This is the v2 bug: I watched an 8-minute window at 2am and called it a
    stall. Nothing was wrong.
    """
    lv = _Fake(REAL).liveness(now=1784180913.885 + 3600)   # an hour of quiet
    assert lv.verdict != "GREEN AND DEAD", "a sleeping fleet reported as dead"
    assert lv.verdict == "cannot tell"


def test_a_total_without_a_timestamp_is_cannot_tell():
    """It has a count but won't say when. A total is a history, not a pulse —
    refuse to upgrade that to 'live'."""
    body = "reactor_events_processed_total 27\n"
    lv = _Fake(body).liveness(now=NOW)
    assert lv.verdict == "cannot tell"
    assert "not a pulse" in lv.detail


def test_green_and_dead_is_not_live():
    """THE TEST THIS MODULE EXISTS FOR.

    reactor answers. /health is 200. up==1. Every signal green. Zero events.
    That is the state it was ACTUALLY in for months. The adapter must call it
    dead, not healthy.
    """
    lv = _Fake(GREEN_AND_DEAD).liveness(now=NOW)
    assert lv.reachable is True, "it IS answering — that is the trap"
    assert lv.delivered == 0
    assert lv.verdict == "GREEN AND DEAD", (
        "a reachable reactor delivering nothing was reported as healthy — "
        "this adapter is a light, not a detector"
    )
    assert "not doing the thing" in lv.render()


def test_unreachable_is_cannot_tell_not_broken_and_not_fine():
    """Exit-code-2 thinking. "I could not look" must never render as "fine"."""
    lv = _Fake(boom=OSError("connection refused")).liveness(now=NOW)
    assert lv.reachable is False
    assert lv.delivered is None
    assert lv.verdict == "cannot tell"
    assert "CANNOT TELL" in lv.render()
    assert "live" not in lv.render().replace("CANNOT TELL", "")


def test_answering_without_a_counter_is_cannot_tell():
    """A /metrics page with no event counter proves the PORT is open — which is
    exactly what up==1 already told us, and it meant nothing. Refuse to guess.
    """
    lv = _Fake("reactor_uptime_seconds 12.0\n").liveness(now=NOW)
    assert lv.verdict == "cannot tell"
    assert "presence without liveness" in lv.detail


def test_none_adapter_runs_and_never_claims_health():
    """integrations.md: the `none` adapter must run the full harness. And it must
    not lie in the comfortable direction — absent is 'cannot tell', not 'live'.
    """
    lv = NoReactor().liveness()
    assert lv.verdict == "cannot tell"
    assert lv.delivered is None


def test_shantytown_does_not_import_reactor_on_any_working_path():
    """"An event source we do not depend on." Prove it structurally: the CLI, the
    dispatcher and the primer must not import reactor. If they do, shantytown has
    grown a required subscriber — an orchestration tier by the back door.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "shantytown"
    for mod in ("cli.py", "dispatch.py", "anchor.py", "files.py", "protocols.py"):
        src = (root / mod).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "reactor" not in node.module, f"{mod} imports reactor"
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert "reactor" not in a.name, f"{mod} imports reactor"


@pytest.mark.parametrize("body,name,want", [
    ("x_total 5\n", "x_total", 5),
    ("x_total 5.0\n", "x_total", 5),
    ("# HELP x_total nope\nx_total 7\n", "x_total", 7),
    ("x_total_other 9\n", "x_total", None),      # prefix must not match
    ("x_total nan\n", "x_total", None),
    ("", "x_total", None),
])
def test_counter_parsing(body, name, want):
    assert _counter(body, name) == want
