"""hibernate — the wake ledger. The POLICY lives in stop_policy (rank 3).

This module was a policy engine: an idle-share measurement, a four-value trigger,
and a Wake verdict. docs/stop-policy-spec.md 8.3 deletes all of it. Once the stop
decision is ONE ordered list, Rule Zero (rank 2) already owns idleness, and what
is left of hibernate is a boolean plus the question this file now answers:

    how long has this agent's stop been declining to wake it?

WHY A LEDGER AT ALL. `max_quiet_minutes` bounds how long a pending batch may sit
unread while nothing pushes. That needs one timestamp. Only WAKES are recorded,
never sleeps: two clocks could disagree about the same fact, and the question is
always time-since-last-wake.

Every read failure is None (-> treat the batch as stale -> wake) and every write
failure is swallowed. A ledger this module cannot write must never be able to stop
a coordinator from waking.
"""
from __future__ import annotations

import time
from pathlib import Path


class WakeLog:
    """<root>/hibernate/<agent>.json — when this agent's stop last WOKE it.

    Only WAKES are recorded, never sleeps: the schedule asks "how long has this
    coordinator been quiet", and the answer is time-since-last-wake. Recording
    sleeps too would give two clocks to disagree about the same fact.

    Every read failure is None (-> wake) and every write failure is swallowed
    (-> the next pass sees an older or missing stamp, and wakes). A ledger this
    module cannot write must not be able to stop a coordinator from waking.
    """

    def __init__(self, root) -> None:
        self.root = Path(root) / "hibernate"

    def _path(self, agent: str) -> Path:
        return self.root / f"{agent}.json"

    def last(self, agent: str) -> float | None:
        import json
        try:
            data = json.loads(self._path(agent).read_text())
            ts = float(data.get("last_wake") or 0)
            return ts or None
        except (OSError, ValueError, TypeError):
            return None

    def minutes_since(self, agent: str, now: float | None = None) -> float | None:
        ts = self.last(agent)
        if ts is None:
            return None
        return max(0.0, ((now or time.time()) - ts) / 60.0)

    def record_wake(self, agent: str, now: float | None = None) -> None:
        from .files import write_json_atomic
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self._path(agent),
                              {"last_wake": now or time.time()})
        except OSError:
            pass                     # see the class docstring: never fail a wake
