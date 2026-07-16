"""reactor — an event source we do not depend on.

docs/integrations.md is blunt about why this module is shaped the way it is:
reactor is "present, running, monitored, green, and doing nothing" while a
standing directive told 14 agents it was working (aegis-uyvs). It watched dead
databases for months with up{job=reactor}==1, systemd active(running), and zero
alerts firing (aegis-lfhk/r2r0). Its own staleness alerts were dead rules and
could not fire (aegis-5lnp).

So this adapter has exactly one job and it is not "talk to reactor":

    ANSWER "HOW MANY EVENTS HAVE YOU DELIVERED?" — A COUNT, NOT A PING.

integrations.md: "It must prove liveness, not presence. `up==1` is what reactor
has today and it means nothing."

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not subscribe. integrations.md sketches:

    class Events(Protocol):
        def subscribe(self, kinds: list[str]) -> Iterator[Event]: ...

**That protocol cannot be implemented against reactor as it exists.** Measured
2026-07-16 against dolt.lan:8075 — its entire HTTP surface is /health and
/metrics; /events, /subscribe, /api/events and every other path return 503.
reactor is a PUSH system: it watches Dolt and fires actions. There is nothing to
pull. Writing a subscribe() here would mean either inventing an endpoint reactor
does not have, or polling /metrics and calling the delta an "event stream" —
which is a made-up interface wearing a real one's name. That is the exact defect
this repo exists to refuse, so the protocol stays unimplemented and the gap is
reported (aegis-k6hv) rather than papered over.

The actual integration is the other direction and needs no code from us:
reactor's own action config shells out to `shanty go <item> <agent>`. A shell
out to our CLI, not an import (aegis-k6hv scope item 3). The CLI is the API.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass


# How long without a new event before "live" is a lie. reactor is poll-mode
# (reactor_mode 0) and was observed processing a burst within seconds, so minutes
# of silence is not a slow tick — it is a stop. Tunable, because the honest
# answer to "how stale is too stale" is owned by whoever runs reactor, not by me.
STALE_AFTER_S = 300.0


@dataclass(frozen=True)
class Liveness:
    """The honest answer about an event source.

    FOUR states, not two. "I could not look" is not "it is fine"; and — the
    correction that cost this module a rewrite — "it worked once" is not "it
    works now".
    """
    reachable: bool
    delivered: int | None          # None = could not tell
    idle_s: float | None = None    # seconds since the last event; None = unknown
    detail: str = ""

    @property
    def verdict(self) -> str:
        if not self.reachable or self.delivered is None:
            return "cannot tell"
        if self.delivered == 0:
            return "GREEN AND DEAD"          # never did anything
        if self.idle_s is None:
            # It has a total but won't say when. A total is a HISTORY, not a
            # pulse — refuse to upgrade that to "live".
            return "cannot tell"
        if self.idle_s > STALE_AFTER_S:
            return "STALLED"                 # did something once; not now
        return "live"

    def render(self) -> str:
        if self.verdict == "cannot tell":
            return f"  reactor: CANNOT TELL — {self.detail}"
        if self.verdict == "GREEN AND DEAD":
            return ("  reactor: *** GREEN AND DEAD — answering, 0 events "
                    "delivered. It is not doing the thing. ***")
        if self.verdict == "STALLED":
            return (f"  reactor: *** STALLED — {self.delivered} events total but "
                    f"NOTHING for {self.idle_s:.0f}s. It answers; it is not "
                    f"working. ***")
        return (f"  reactor: live — {self.delivered} events delivered, "
                f"last {self.idle_s:.0f}s ago")


class Reactor:
    """Liveness by COUNT, read from reactor's own /metrics.

    Not a subscriber. Not required. Nothing in shantytown may call this on a
    path that has to work.
    """

    def __init__(self, base: str = "http://dolt.lan:8075", timeout: float = 5.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _metrics(self) -> str:
        with urllib.request.urlopen(f"{self.base}/metrics", timeout=self.timeout) as r:
            return r.read().decode()

    def liveness(self, now: float | None = None) -> Liveness:
        """Has it delivered events, AND has it delivered one RECENTLY?

        Never raises — unreachable is a verdict, not an exception.

        THE COUNT ALONE IS NOT ENOUGH, and this module learned that the hard way
        (aegis-k6hv). integrations.md says the health answer is "how many events
        have you delivered?" — and the first version of this method implemented
        exactly that: `live if delivered > 0`. Then I drove it against the real
        reactor while firing a real bead event, and it reported "live — 27 events
        delivered" for six straight minutes during which reactor processed
        NOTHING and my event went unhandled.
        A CUMULATIVE COUNTER NEVER GOES DOWN. `delivered > 0` proves reactor
        worked ONCE — it will keep saying "live" a week after it dies. That is
        the same shape as up==1, just with a bigger number, which is precisely
        the failure this module exists to detect. The pulse is the DELTA, so we
        read reactor_last_event_timestamp too.
        """
        import time
        now = time.time() if now is None else now
        try:
            body = self._metrics()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            # NOT "broken", NOT "fine". We could not look.
            return Liveness(False, None, None, f"unreachable at {self.base}: {e}")

        n = _counter(body, "reactor_events_dispatched_total")
        if n is None:
            n = _counter(body, "reactor_events_processed_total")
        if n is None:
            # It answered, but not with the thing that means anything. Refusing
            # to guess is the point: a /metrics page without the counter tells
            # you the port is open, which is exactly what up==1 already told you.
            return Liveness(True, None, None,
                            "answered, but exposes no event counter — "
                            "presence without liveness")

        ts = _counter(body, "reactor_last_event_timestamp")
        idle = None if ts is None else max(0.0, now - float(ts))
        detail = ("" if ts is not None else
                  f"{n} events total, but no reactor_last_event_timestamp — "
                  "a total is a history, not a pulse")
        return Liveness(True, n, idle, detail)


class NoReactor:
    """The `none` adapter. integrations.md: "the `none` adapter must run the full
    harness. If shantytown *needs* reactor to function, we have made a directive
    that will one day be false."

    This is the second implementation, and per protocols.py it is the leak
    detector: if this is hard to write, reactor has leaked into the core. It was
    not hard to write.
    """

    def liveness(self) -> Liveness:
        return Liveness(False, None, "no reactor configured (none adapter)")


def _counter(body: str, name: str) -> int | None:
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("#") or not line.startswith(name):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name:
            try:
                return int(float(parts[1]))
            except ValueError:
                return None
    return None
