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


# How long without a new event before we stop calling it "live". NOT "before it
# is dead" — see the verdict table. This is the boundary of what we know, not a
# diagnosis.
#
# THE MISTAKE THAT PUT THIS COMMENT HERE (aegis-k6hv): v2 of this module treated
# idle > 300s as STALLED and I reported reactor stalled to its owner. It was not.
# It was IDLE — 2 writes to beads_aegis in 15 minutes, because the crew had gone
# to bed. The counter moved 27 -> 46 the moment work arrived. On a fleet that
# legitimately goes quiet overnight, "no event in 5 minutes = broken" PAGES EVERY
# NIGHT, gets silenced, and then misses the real death anyway. That is the exact
# mirror of v1 (live if delivered>0, which never fires): one detector that cannot
# fire, one that cannot stop. I built both in twenty minutes.
#
# AND THE NUMBER ITSELF IS A GUESS — dearing measured reactor's processing
# latency at 5s on one probe and 69s on another. THE SAME REACTOR, THE SAME DAY,
# AN ORDER OF MAGNITUDE APART, and nobody has characterised the distribution. So
# 300 is not "5x the worst case"; it is 4x the largest sample anyone happens to
# have taken. Any threshold here is unfounded until someone characterises the
# latency, which is why this constant only ever downgrades "live" to "cannot
# tell" — never to "dead". A guessed threshold may not accuse.
QUIET_AFTER_S = 300.0


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
    pending: int | None = None     # commits reactor knows it has NOT processed
    detail: str = ""

    @property
    def verdict(self) -> str:
        """Three answers. There is no "STALLED", and that absence is the finding.

        REACTOR'S METRICS CANNOT DISTINGUISH IDLE FROM DEAD. An aging
        reactor_last_event_timestamp means EITHER "nothing happened" OR "I
        stopped looking" — the same reading for opposite states. There is no
        reactor_last_poll_timestamp: nothing that says "I LOOKED and there was
        nothing there". So a frozen counter on a quiet fleet is healthy, a frozen
        counter on a busy fleet is dead, and the metrics do not say which fleet
        you have.
        Answering that question needs a second source (was there work to do?),
        which this adapter deliberately does not reach for — an adapter that
        secretly queries the tracker to grade the event source has made
        shantytown depend on reactor, which is the one thing integrations.md
        forbids. So we report the boundary honestly instead of guessing past it.
        """
        if not self.reachable or self.delivered is None:
            return "cannot tell"
        if self.delivered == 0:
            # Unambiguous, and it is the state reactor was ACTUALLY in for
            # months: answering, monitored, green, and it has never once done
            # the thing. No amount of quiet explains a lifetime total of zero.
            return "GREEN AND DEAD"
        if self.pending and self.pending > 0 and (
                self.idle_s is None or self.idle_s > QUIET_AFTER_S):
            # dearing's discriminator (aegis-4s5d): work is PENDING and nothing
            # has been processed. Quiet cannot explain a backlog. This is the
            # one reading that separates "idle" from "dead" — when it fires.
            return "BACKLOGGED"
        if self.idle_s is None or self.idle_s > QUIET_AFTER_S:
            # It worked at some point and is not working right now. Whether that
            # is a sleeping fleet or a dead process is NOT KNOWABLE FROM HERE.
            # NOTE pending==0 does NOT rescue us: see Reactor.liveness on why
            # this metric cannot prove the negative.
            return "cannot tell"
        return "live"

    def render(self) -> str:
        if self.verdict == "GREEN AND DEAD":
            return ("  reactor: *** GREEN AND DEAD — answering, 0 events "
                    "delivered, ever. It is not doing the thing. ***")
        if self.verdict == "BACKLOGGED":
            return (f"  reactor: *** BACKLOGGED — {self.pending} commits pending "
                    f"and NOTHING processed for {self.idle_s:.0f}s. Quiet does "
                    f"not explain a backlog. It is not working. ***")
        if self.verdict == "live":
            return (f"  reactor: live — {self.delivered} events delivered, "
                    f"last {self.idle_s:.0f}s ago")
        if self.detail:
            return f"  reactor: CANNOT TELL — {self.detail}"
        return (f"  reactor: CANNOT TELL — {self.delivered} delivered, but "
                f"nothing for {self.idle_s:.0f}s. Idle or dead: reactor exposes "
                f"no last-poll, so this reading is the same for both.")


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
            return Liveness(False, None, detail=f"unreachable at {self.base}: {e}")

        n = _counter(body, "reactor_events_dispatched_total")
        if n is None:
            n = _counter(body, "reactor_events_processed_total")
        if n is None:
            # It answered, but not with the thing that means anything. Refusing
            # to guess is the point: a /metrics page without the counter tells
            # you the port is open, which is exactly what up==1 already told you.
            return Liveness(True, None,
                            detail="answered, but exposes no event counter — "
                                   "presence without liveness")

        ts = _counter(body, "reactor_last_event_timestamp")
        idle = None if ts is None else max(0.0, now - float(ts))

        # dearing's discriminator (aegis-4s5d), used ONE-WAY ONLY.
        #
        # pending > 0 + nothing processed  ->  DEAD. Quiet cannot explain a
        # backlog, so this is a true positive whenever it fires.
        #
        # pending == 0  ->  PROVES NOTHING, and this is the trap. The metric's
        # own HELP says "Commits remaining in INITIAL CATCHUP" — it tracks the
        # startup backlog, not steady-state pending work. A reactor that dies
        # AFTER catchup reads 0, identically to a healthy idle one. Nobody has
        # ever observed it above 0 (dearing checked; so did I, repeatedly).
        # So treating pending==0 as "therefore idle, therefore healthy" would
        # build a THIRD detector that cannot fire — v1's exact failure mode,
        # from a metric whose documentation already says it will not help.
        # We upgrade cannot-tell -> BACKLOGGED; we never downgrade to live.
        pending = _counter(body, "reactor_catchup_commits_remaining")

        detail = ("" if ts is not None else
                  f"{n} events total, but no reactor_last_event_timestamp — "
                  "a total is a history, not a pulse")
        return Liveness(True, n, idle, pending, detail)


class NoReactor:
    """The `none` adapter. integrations.md: "the `none` adapter must run the full
    harness. If shantytown *needs* reactor to function, we have made a directive
    that will one day be false."

    This is the second implementation, and per protocols.py it is the leak
    detector: if this is hard to write, reactor has leaked into the core. It was
    not hard to write.
    """

    def liveness(self) -> Liveness:
        return Liveness(False, None, detail="no reactor configured (none adapter)")


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
