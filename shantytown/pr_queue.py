"""Read-only PR queue policy (aegis-opw).

Eligibility is an advisory input to the serialized trusted merge helper, never
merge authority. No native auto-merge, forge write, agent wake or timer is armed.
# arming: library -- explicit caller supplies a complete, fresh inventory.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
from statistics import median

from .pr_preflight import Refused

SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def timestamp(value):
    if not isinstance(value, str):
        raise Refused("missing queue timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Refused("invalid queue timestamp") from error
    if result.tzinfo is None:
        raise Refused("queue timestamps require an explicit timezone")
    return result.astimezone(timezone.utc)


def evaluate(snapshot, *, now, cap, stale_hours=48, max_age_seconds=300):
    """Evaluate an explicitly complete snapshot; unknown evidence cannot pass.

    Crew author/reviewer names must be supplied from trusted binding/receipt
    adapters, never inferred from the shared forge login. Fresh trusted-helper
    revalidation remains mandatory even for rows whose eligibility is true.
    """
    if type(cap) is not int or cap < 1:
        raise Refused("WIP cap must be a positive integer")
    if (type(stale_hours) not in (int, float) or not math.isfinite(stale_hours) or stale_hours <= 0
            or type(max_age_seconds) is not int or max_age_seconds < 1):
        raise Refused("invalid queue age policy")
    current = timestamp(now)
    if not isinstance(snapshot, dict) or snapshot.get("complete") is not True:
        raise Refused("incomplete queue inventory")
    repo = snapshot.get("repo")
    if not isinstance(repo, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise Refused("invalid repository")
    age = (current - timestamp(snapshot.get("observed_at"))).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise Refused("queue inventory is stale or from the future")
    pulls = snapshot.get("pulls")
    if not isinstance(pulls, list):
        raise Refused("missing PR inventory")
    seen, counts, ages, rows, stale = set(), {}, [], [], []
    for pull in pulls:
        if not isinstance(pull, dict):
            raise Refused("invalid PR row")
        number, head = pull.get("number"), pull.get("head")
        if type(number) is not int or number < 1 or number in seen:
            raise Refused("invalid or duplicate PR number")
        if not isinstance(head, str) or not SHA.fullmatch(head):
            raise Refused("invalid exact PR head")
        if pull.get("state") != "open" or type(pull.get("draft")) is not bool:
            raise Refused("inventory contains unknown PR state")
        seen.add(number)
        hours = (current - timestamp(pull.get("created_at"))).total_seconds() / 3600
        if hours < 0:
            raise Refused("PR creation date is from the future")
        ages.append(hours)
        author = pull.get("author")
        if not isinstance(author, str) or not re.fullmatch(r"[A-Za-z0-9_]+", author):
            author = None
        if author:
            counts[author] = counts.get(author, 0) + 1
        reasons = []
        reviewer = pull.get("reviewer")
        if not author:
            reasons.append("crew author binding unknown")
        if not isinstance(reviewer, str) or not reviewer or reviewer == author:
            reasons.append("independent named reviewer missing")
        if pull["draft"]:
            reasons.append("draft")
        for key in ("head_green", "base_green", "review_valid", "binding_valid"):
            if pull.get(key) is not True:
                reasons.append(key + " unproven")
        if not isinstance(pull.get("base"), str) or not SHA.fullmatch(pull["base"]):
            reasons.append("exact base unknown")
        if not isinstance(pull.get("approval_digest"), str) or not re.fullmatch(
                r"[0-9a-f]{64}", pull["approval_digest"]):
            reasons.append("review digest missing")
        # Explicit empty holds is evidence; absent/unknown holds is not.
        if pull.get("holds") != []:
            reasons.append("hold clearance unproven")
        rows.append({"number": number, "head": head, "author": author,
                     "reviewer": reviewer, "eligible": not reasons,
                     "reasons": reasons, "age_hours": hours})
        if hours >= stale_hours:
            key = hashlib.sha256(f"{repo}:{number}:{head}:stale".encode()).hexdigest()
            stale.append({"key": key, "number": number, "head": head,
                          "owner": author, "routeable": author is not None})
    return {"repo": repo, "observed_at": snapshot["observed_at"],
            "open_count": len(rows), "median_age_hours": median(ages) if ages else 0,
            "cap": cap, "authors": {author: {"open": count, "admit": count < cap}
                                    for author, count in sorted(counts.items())},
            "unknown_author_count": sum(row["author"] is None for row in rows),
            "rows": rows, "stale_events": stale, "execution_authority": False}


def pending_events(report, acknowledged):
    """Return deterministic event keys until the durable sender confirms delivery.

    Caller must persist acknowledgements only after verified delivery. A lost
    response needs reconciliation against the destination; this function does
    not mark attempted sends as delivered. No sender is activated here.
    """
    if not isinstance(acknowledged, set) or any(not isinstance(k, str) for k in acknowledged):
        raise Refused("invalid event acknowledgement set")
    return [event for event in report["stale_events"]
            if event["routeable"] and event["key"] not in acknowledged]


def admission(report, author):
    """Unknown crew bindings block cap enforcement, rather than undercounting."""
    if report["unknown_author_count"]:
        return False
    return report["authors"].get(author, {"admit": True})["admit"]
