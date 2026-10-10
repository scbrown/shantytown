"""Bounded spare-capacity reflection for explicitly admitted work (aegis-2o5n2).

Dreaming creates reviewed work artifacts; it never edits the systems it studies.
The planner is pure.  Its state advances only after the caller observes a tracker
creation, so a failed write is retried rather than silently consuming a cycle.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

DREAM_LABELS = frozenset({"dream", "dream-discrepancy", "dream-proposal"})
# The CYCLE itself, as opposed to the proposals and discrepancies it produces
# (aegis-qx96wr). camayoc derives the DreamCycle work kind from this label.
CYCLE_LABEL = "dream-cycle"
# Cycles created before CYCLE_LABEL existed are recognised by their title.
LEGACY_CYCLE_TITLES = ("DREAM consolidate:", "DREAM propose:")
# A cycle with no tracker activity for this long has lapsed and stops holding
# the queue. Two 6h dream intervals; the same window as camayoc's DreamCycle
# idleLimit (PT12H), so the lane and the lapse alert agree on when it is stale.
CYCLE_IDLE_LIMIT_S = 12 * 3600


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
                "last_domain": plan.domain, "last_agent": plan.agent}
        tmp = self.path.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)


def is_dream(item: dict) -> bool:
    labels = item.get("labels") or []
    if isinstance(labels, str):
        labels = labels.split(",")
    return bool(DREAM_LABELS.intersection(labels))


def is_cycle(item: dict) -> bool:
    """A dream CYCLE, not one of its outputs. An unassigned dream-proposal or
    dream-discrepancy is reviewable work a cycle produced; counting it as a
    queued cycle silenced the lane for 11 days (aegis-2idcev)."""
    labels = item.get("labels") or []
    if isinstance(labels, str):
        labels = labels.split(",")
    if CYCLE_LABEL in labels:
        return True
    return "dream" in labels and str(item.get("title") or "").startswith(LEGACY_CYCLE_TITLES)


def _epoch(value) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def is_lapsed(item: dict, now: float) -> bool:
    """True when the cycle has had no tracker activity for CYCLE_IDLE_LIMIT_S.
    An unreadable timestamp is NOT lapsed: the queue bound holds when in doubt."""
    seen = _epoch(item.get("updated_at") or item.get("created_at"))
    return seen is not None and now - seen > CYCLE_IDLE_LIMIT_S


def is_queued_dream(item: dict, now: float | None = None) -> bool:
    """True only for a dream cycle still waiting to be claimed, and still live.

    Assigned cycles are somebody's foreground haul. Outputs (proposals,
    discrepancies) never hold the queue, whoever holds them. A cycle idle past
    CYCLE_IDLE_LIMIT_S has lapsed: it stays open for its owner, but it no longer
    stops the next cycle.
    """
    now = time.time() if now is None else now
    return (is_cycle(item) and not str(item.get("assignee") or "").strip()
            and not is_lapsed(item, now))


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
    if any(is_queued_dream(item, now) for item in ready):
        return None, "a dream cycle is already queued"
    capacity_eligible = [c for c in candidates
                         if c.get("headroom") is not None
                         and float(c["headroom"]) >= policy.min_headroom_pct]
    if not capacity_eligible:
        return None, "no idle subscription has measured spare capacity"
    # This pure planner does not admit a wake. Explicit callers queue one bounded
    # P4 artifact behind a provider's foreground haul; assignment does not
    # interrupt active work, and the existing-DREAM gate above bounds the queue
    # globally.  Capacity and delegation reserve remain hard gates.
    # Spread cycles: the last cycle's agent goes only when nobody else is
    # eligible. Most-headroom alone sent 5 of 5 cycles to one agent.
    others = [c for c in capacity_eligible if c["agent"] != state.get("last_agent")]
    chosen = max(others or capacity_eligible,
                 key=lambda c: (float(c["headroom"]), c["agent"]))
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
        labels = f"dream,dream-discrepancy,{CYCLE_LABEL}"
        outcome = ("Document each measured divergence as a dream-discrepancy bead "
                   "and/or Quipu episode. Correct stale truth in Quipu only; do not "
                   "mutate infrastructure, code, or deployed configuration.")
    else:
        title = f"DREAM propose: improve {domain}"
        labels = f"dream,dream-proposal,{CYCLE_LABEL}"
        outcome = ("Create one or more reviewable dream-proposal beads covering "
                   "functional and/or non-functional improvements. Do not implement "
                   "or auto-apply any proposal in this cycle.")
    description = (
        f"Admitted bounded {mode} cycle for domain {domain}. Quipu is the source "
        f"of truth. {outcome} Query Quipu before analysis; carry commands and "
        f"observations as evidence; stop after this one bounded domain pass. "
        f"Provenance: st work dream selected {chosen['harness']} with "
        f"{float(chosen['headroom']):.0f}% measured headroom.")
    return Plan(agent=chosen["agent"], harness=chosen["harness"],
                headroom=float(chosen["headroom"]), mode=mode, domain=domain,
                title=title, description=description, labels=labels), ""
