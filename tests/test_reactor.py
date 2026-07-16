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


def test_stalled_is_not_live_even_with_a_big_counter():
    """THE CORRECTION THAT COST THIS MODULE A REWRITE — and I caught it live.

    v1 was `live if delivered > 0`, straight from integrations.md's "the health
    answer is a count". Driven against the REAL reactor while firing a REAL bead
    comment, it reported "live - 27 events delivered" for six minutes while
    reactor processed NOTHING and my event went unhandled (measured: counter
    frozen at 27, last_event_age climbing 310->400s, health {"status":"ok"}).

    A CUMULATIVE COUNTER NEVER GOES DOWN. delivered>0 proves it worked ONCE and
    will keep saying "live" a week after it dies — up==1 with a bigger number,
    which is the exact failure this module exists to detect.
    """
    lv = _Fake(REAL).liveness(now=1784180913.885 + 600)   # 10 min of silence
    assert lv.reachable is True
    assert lv.delivered == 27, "the counter is healthy-looking — that is the trap"
    assert lv.verdict == "STALLED", (
        "27 events and total silence reported as live — the adapter is reading "
        "a history and calling it a pulse"
    )
    assert "it is not working" in lv.render()


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
    for mod in ("cli.py", "dispatch.py", "prime.py", "files.py", "protocols.py"):
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
