"""Bounded spare-capacity reflection for ``st tend`` (aegis-2o5n2).

Dreaming creates reviewed work artifacts; it never edits the systems it studies.
The planner is pure.  Its state advances only after the caller observes a tracker
creation, so a failed write is retried rather than silently consuming a cycle.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

DREAM_LABELS = frozenset({"dream", "dream-discrepancy", "dream-proposal"})


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    interval_minutes: int = 360
    min_headroom_pct: int = 20
    domains: tuple[str, ...] = ("ontology", "infra", "codebases", "fleet-config")


@dataclass(frozen=True)
class Plan:
    agent: str
    harness: str
    headroom: float
    mode: str
    domain: str
    title: str
    description: str
    labels: str


class State:
    def __init__(self, root):
        self.path = Path(root) / "dream-state.json"

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def record(self, plan: Plan, item_id: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"last_at": now, "last_item": item_id, "last_mode": plan.mode,
                "last_domain": plan.domain}
        tmp = self.path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)


def is_dream(item: dict) -> bool:
    labels = item.get("labels") or []
    if isinstance(labels, str):
        labels = labels.split(",")
    return bool(DREAM_LABELS.intersection(labels))


def plan(policy: Policy, state: dict, ready: list[dict], candidates: list[dict],
         now: float | None = None, force: bool = False) -> tuple[Plan | None, str]:
    """Return one bounded cycle or the explicit reason it must stay asleep.

    candidates carry ``agent``, ``harness`` and measured ``headroom``.  A caller
    must omit signal-lost providers; absence is not spare capacity.
    """
    now = time.time() if now is None else now
    if not policy.enabled and not force:
        return None, "dreaming is disabled"
    last = state.get("last_at")
    if (not force and isinstance(last, (int, float))
            and now < float(last) + policy.interval_minutes * 60):
        return None, "not due"
    if any(is_dream(item) for item in ready):
        return None, "a dream cycle is already queued"
    capacity_eligible = [c for c in candidates
                         if c.get("headroom") is not None
                         and float(c["headroom"]) >= policy.min_headroom_pct]
    if not capacity_eligible:
        return None, "no idle subscription has measured spare capacity"
    # Foreground work preempts DREAM only when this particular idle provider
    # could actually accept it.  The fleet's board is intentionally never
    # empty; assigned, decision-gated, dependency-blocked, and governor-held
    # work is not a runnable queue for this provider.  Missing dispatchability
    # evidence fails closed: callers must positively show that the candidate
    # has no ordinary work it can take.
    eligible = [c for c in capacity_eligible
                if c.get("ordinary_dispatchable") is False]
    if not eligible:
        return None, "normal work is dispatchable to every idle provider"
    chosen = max(eligible, key=lambda c: (float(c["headroom"]), c["agent"]))
    domains = policy.domains or Policy.domains
    previous_domain = state.get("last_domain")
    try:
        domain_i = (domains.index(previous_domain) + 1) % len(domains)
    except (ValueError, TypeError):
        domain_i = 0
    domain = domains[domain_i]
    mode = "dream" if state.get("last_mode") == "consolidate" else "consolidate"
    if mode == "consolidate":
        title = f"DREAM consolidate: reconcile {domain} reality against Quipu"
        labels = "dream,dream-discrepancy"
        outcome = ("Document each measured divergence as a dream-discrepancy bead "
                   "and/or Quipu episode. Correct stale truth in Quipu only; do not "
                   "mutate infrastructure, code, or deployed configuration.")
    else:
        title = f"DREAM propose: improve {domain}"
        labels = "dream,dream-proposal"
        outcome = ("Create one or more reviewable dream-proposal beads covering "
                   "functional and/or non-functional improvements. Do not implement "
                   "or auto-apply any proposal in this cycle.")
    description = (
        f"Scheduled bounded {mode} cycle for domain {domain}. Quipu is the source "
        f"of truth. {outcome} Query Quipu before analysis; carry commands and "
        f"observations as evidence; stop after this one bounded domain pass. "
        f"Provenance: st dream selected {chosen['harness']} with "
        f"{float(chosen['headroom']):.0f}% measured headroom.")
    return Plan(agent=chosen["agent"], harness=chosen["harness"],
                headroom=float(chosen["headroom"]), mode=mode, domain=domain,
                title=title, description=description, labels=labels), ""
