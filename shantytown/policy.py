"""policy — the Ranker adapter. Weight prioritization candidates by structure.

Two implementations, per the leak-detector discipline (protocols.py):

  NullRanker  — the DEFAULT and the leak detector. No backend; the rule-based
                order (workflow.prioritize) stands. The whole feature works on
                this, which proves Yupana/Quipu have not leaked into the core.

  PolicyRanker — first-class: weight a candidate by the blast radius of the
                symbol its work item names, via `yupana impact <symbol> --json`
                (the `count` is the weight). Governed policy from Quipu folds in
                later (the same shape). It carries Yupana's honesty out to the
                caller: RankUnavailable when it could not look, NEVER an unweighted
                list pretending it ranked (mirrors bobbin.BobbinContext).

The binary is `yupana`; it was named `hank` until v0.6.0. This adapter kept
saying `hank` after the rename, and because every could-not-look outcome here
is an honest RankUnavailable, the breakage presented as "the ranker is
unavailable" — indistinguishable from a legitimately absent backend. Honest
degradation hides a stale name as effectively as it reports a missing one.

Opt-in only: stop_event.main selects PolicyRanker when SHANTY_RANKER=policy, else
NullRanker — the hook never reaches for a backend unless asked.
"""
from __future__ import annotations

from .answer import Answer

import json
import shutil
import subprocess
from typing import Callable

from .protocols import RankUnavailable


class NullRanker:
    """No backend. Returns candidates unchanged so the rule-based order stands."""

    def weigh(self, candidates: list) -> Answer:
        # COMPLETE, not capped. NullRanker deliberately weighs nothing, and that
        # is the whole search space it claims to cover: there is no backend that
        # could have said more. An unweighted answer here MEANS unweighted.
        return Answer.complete_read(
            candidates, how="NullRanker: no backend, rule-based order stands")


class PolicyRanker:
    """Blast-radius weighting via Yupana. `impact_fn(symbol) -> int` is injected
    so tests drive it with captured `yupana impact` output (mirrors test_reactor's
    _Fake); the default shells the real `yupana` CLI."""

    def __init__(self, impact_fn: Callable[[str], int] | None = None):
        self._impact = impact_fn or _yupana_impact

    def weigh(self, candidates: list) -> Answer:
        """Weight each candidate whose item names a symbol. Raises RankUnavailable
        (propagated from the impact fn) the first time the backend cannot answer —
        the drain catches it and degrades, so a partial weighting never masquerades
        as a complete one.

        AND THE OTHER PARTIAL, which the exception does not cover (aegis-q0bzh):
        a candidate whose title carries no `mod::sym` token is SKIPPED, keeping
        `weight = 0`. That is indistinguishable from a real blast radius of zero,
        so the answer says how many were skipped rather than leaving the caller to
        infer it from weights that look like measurements."""
        skipped = 0
        for c in candidates:
            symbol = _symbol_of(c)
            if not symbol:
                skipped += 1
                continue
            c.weight = float(self._impact(symbol))     # may raise RankUnavailable
            c.why = f"blast radius {int(c.weight)}"
        how = f"PolicyRanker: yupana impact over {len(candidates)} candidate(s)"
        if skipped:
            return Answer.capped(
                candidates, how=how,
                caveat=(f"{skipped} of {len(candidates)} candidate(s) name no "
                        f"mod::sym symbol and were never weighed — their weight 0 "
                        f"is 'not asked', not 'no blast radius'"))
        return Answer.complete_read(candidates, how=how)


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


def _yupana_impact(symbol: str) -> int:
    """`yupana impact <symbol> --json` -> the blast-radius `count`. Raises
    RankUnavailable on any could-not-look outcome, carrying yupana's own words."""
    if shutil.which("yupana") is None:
        raise RankUnavailable("yupana CLI not on PATH — cannot weigh")
    cmd = ["yupana", "impact", symbol, "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RankUnavailable(f"yupana impact failed: {e}") from e
    if r.returncode != 0:
        first = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()
        raise RankUnavailable(f"yupana could not answer: {first[0] if first else r.returncode}")
    try:
        payload = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RankUnavailable(f"yupana impact returned unparseable output: {e}") from e
    return int(payload.get("count", 0))
