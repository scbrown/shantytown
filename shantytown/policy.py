"""policy — the Ranker adapter. Weight prioritization candidates by structure.

Two implementations, per the leak-detector discipline (protocols.py):

  NullRanker  — the DEFAULT and the leak detector. No backend; the rule-based
                order (workflow.prioritize) stands. The whole feature works on
                this, which proves Hank/Quipu have not leaked into the core.

  PolicyRanker — first-class: weight a candidate by the blast radius of the
                symbol its work item names, via `hank impact <symbol> --json`
                (the `count` is the weight). Governed policy from Quipu folds in
                later (the same shape). It carries Hank's honesty out to the
                caller: RankUnavailable when it could not look, NEVER an unweighted
                list pretending it ranked (mirrors bobbin.BobbinContext).

Opt-in only: stop_event.main selects PolicyRanker when SHANTY_RANKER=policy, else
NullRanker — the hook never reaches for a backend unless asked.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Callable

from .protocols import RankUnavailable


class NullRanker:
    """No backend. Returns candidates unchanged so the rule-based order stands."""

    def weigh(self, candidates: list) -> list:
        return candidates


class PolicyRanker:
    """Blast-radius weighting via Hank. `impact_fn(symbol) -> int` is injected so
    tests drive it with captured `hank impact` output (mirrors test_reactor's
    _Fake); the default shells the real `hank` CLI."""

    def __init__(self, impact_fn: Callable[[str], int] | None = None):
        self._impact = impact_fn or _hank_impact

    def weigh(self, candidates: list) -> list:
        """Weight each candidate whose item names a symbol. Raises RankUnavailable
        (propagated from the impact fn) the first time the backend cannot answer —
        the drain catches it and degrades, so a partial weighting never masquerades
        as a complete one."""
        for c in candidates:
            symbol = _symbol_of(c)
            if not symbol:
                continue
            c.weight = float(self._impact(symbol))     # may raise RankUnavailable
            c.why = f"blast radius {int(c.weight)}"
        return candidates


def _symbol_of(c) -> str | None:
    """Best-effort symbol for weighting: a `mod::sym`-shaped token in the item
    title. Absent -> unweighted (weight stays 0), honestly. The durable source is
    a Quipu governed relation (bead -> touched symbols); this is the MVP heuristic
    and is documented as such."""
    if not (c.item and c.item.title):
        return None
    for tok in c.item.title.split():
        if "::" in tok:
            return tok.strip(".,()")
    return None


def _hank_impact(symbol: str) -> int:
    """`hank impact <symbol> --json` -> the blast-radius `count`. Raises
    RankUnavailable on any could-not-look outcome, carrying hank's own words."""
    if shutil.which("hank") is None:
        raise RankUnavailable("hank CLI not on PATH — cannot weigh")
    cmd = ["hank", "impact", symbol, "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RankUnavailable(f"hank impact failed: {e}") from e
    if r.returncode != 0:
        first = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()
        raise RankUnavailable(f"hank could not answer: {first[0] if first else r.returncode}")
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RankUnavailable(f"hank impact returned unparseable output: {e}") from e
    return int(payload.get("count", 0))
