"""Account observations merged across hosts without inventing a fresh timestamp."""
from __future__ import annotations

import math
from collections.abc import Mapping

from .governor import GovernorError, Reading


class FreshestReader:
    """Immutable per-invocation observations with reproducible host provenance.

    The producer's timestamp decides freshness, never the time an SSH response
    arrives. A newer FAILED probe must remain a failed probe; filtering it out
    would turn a transport recovery mechanism into stale-success laundering.
    Governor.evaluate remains responsible for age, rotation grace and fail-safe
    policy. Missing timestamps survive only when no dated observation exists,
    and remain unaged (therefore unusable) at that gate.
    """

    def __init__(self, observations: Mapping[str, Mapping[str, Reading]]):
        selected: dict[str, tuple[float, str, Reading]] = {}
        for host, windows in sorted(observations.items()):
            for window, reading in windows.items():
                if not isinstance(reading, Reading):
                    raise GovernorError(f"invalid reading for {host}/{window}")
                stamp = reading.at
                if stamp is not None and (
                        isinstance(stamp, bool) or not isinstance(stamp, (float, int))
                        or not math.isfinite(stamp)):
                    raise GovernorError(f"invalid probe timestamp for {host}/{window}")
                rank = float('-inf') if stamp is None else stamp
                # Equal timestamps select the same host regardless of which
                # machine is asking or which response finished first.
                if window not in selected or rank > selected[window][0]:
                    selected[window] = (rank, host, reading)
        self._readings = {window: value[2] for window, value in selected.items()}
        self.provenance = {window: value[1] for window, value in selected.items()}

    def read_all(self) -> dict[str, Reading]:
        return dict(self._readings)
