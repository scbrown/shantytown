"""Finite decision lists for anchor: one scoped projection and a 30s cache.

Advisory only; this never selects custody, dispatches, or relaxes readiness.
Cache bookkeeping belongs to the CLI, not the pure anchor resolver.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .answer import Answer
from .query_identity import declared_read_operation

CAP = 400
TTL = 30


def _cut(text, budget):
    raw = text.encode("utf-8")
    return text if len(raw) <= budget else raw[:max(0, budget-3)].decode("utf-8", "ignore") + "…"


def _date(value):
    if isinstance(value, dict) and set(value) == {"datatype", "value"}:
        value = value["value"]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid due date")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("due date has no timezone")
    return result


@declared_read_operation("decision-lists")
def projected(tracker, owner, *, now=None):
    """One scoped SDK projection; reuse the worker's existing plate read."""
    now = now or datetime.now(timezone.utc)
    try:
        answer = tracker.decision_rows(owner)
        if not answer.complete:
            return Answer.capped({},how=answer.how,caveat=answer.caveat)
        rows=answer.exact()
        if len(rows)>=10000: raise ValueError("decision candidate cap reached")
        items=[]; seen=set()
        from .protocols import rank_order
        from .inbox import is_message
        for row in rows:
            if row.get("status") not in ("open","in_progress","blocked"):
                continue
            if not all(isinstance(row.get(k),str) and row[k] for k in ("id","title","status")) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*",row["id"]):
                raise ValueError("invalid decision identity")
            if row["id"] in seen: raise ValueError("duplicate decision identity")
            seen.add(row["id"])
            if is_message(row["title"]) or any(str(label).lower()=="inbox" for label in row.get("labels",[])):
                continue
            if type(row.get("priority")) is not int or row["priority"] not in range(5):
                raise ValueError("invalid decision priority")
            due=_date(row.get("due_at"))
            items.append({"id":row["id"],"title":row["title"],"status":row["status"],"priority":row["priority"],"due_at":due.isoformat() if due else None, "rank":row.get("rank"), "created_at":row.get("created_at")})
        packet={"top":sorted(items,key=lambda row:(row["priority"],*rank_order(row.get("rank"),row.get("due_at"),row.get("created_at")),row["id"]))[:3],
                "coming":sorted((row for row in items if row["due_at"] and _date(row["due_at"])<=now+timedelta(days=3)),key=lambda row:(_date(row["due_at"]),row["priority"],row["id"]))[:3],
                "undated":sum(row["due_at"] is None for row in items)}
        return Answer.complete_read(packet,how=answer.how+"; decision lists reuse scalar fields")
    except Exception as error:
        return Answer.capped({},how="decision lists",caveat=f"UNKNOWN: {type(error).__name__}")


def render(answer, *, now=None, fleet=False):
    now = now or datetime.now(timezone.utc)
    if not answer.complete:
        return "  Top of your plate: UNKNOWN\n  Coming due: UNKNOWN\n"
    packet = answer.exact()
    def entry(row):
        due = _date(row.get("due_at"))
        date = ("OVERDUE" if due <= now else due.strftime("%m-%d")) if due else "no due"
        status = " [blocked]" if row["status"] == "blocked" else ""
        title = _cut(" ".join(row["title"].split()), 12)
        return f'P{row["priority"]} {row["id"]} {date}{status} {title}'
    packet = {**packet, "top":list(packet["top"][:3]), "coming":list(packet["coming"][:3])}
    omitted = False
    while True:
        lists = []
        for label, key in (("Top of your plate", "top"), ("Coming due", "coming")):
            text = "; ".join(entry(row) for row in packet[key]) or "none"
            lists.append(f"  {label}:\n    {text}")
        if fleet:
            lists.append(f'  {packet["undated"]} active items have no due date')
        if omitted:
            lists.append("  … display capped")
        result = "\n".join(lists) + "\n"
        if len(result.encode()) <= CAP:
            return result
        key = max(("top", "coming"), key=lambda k:len(packet[k]))
        if not packet[key]:
            return "  Top of your plate: UNKNOWN (byte cap)\n  Coming due: UNKNOWN (byte cap)\n"
        packet[key].pop()
        omitted = True


def _valid_packet(packet):
    """Treat cached data as untrusted; malformed data cannot imply empty work."""
    if not isinstance(packet, dict) or set(packet) != {"top", "coming", "undated"}:
        return False
    if type(packet["undated"]) is not int or not 0 <= packet["undated"] < 10000:
        return False
    try:
        for key in ("top", "coming"):
            rows = packet[key]
            if not isinstance(rows, list) or len(rows) > 3:
                return False
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    return False
                identifier = row.get("id")
                if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", identifier):
                    return False
                if identifier in seen:
                    return False
                seen.add(identifier)
                if not isinstance(row.get("title"), str) or not row["title"]:
                    return False
                if type(row.get("priority")) is not int or row["priority"] not in range(5):
                    return False
                if row.get("status") not in ("open", "in_progress", "blocked"):
                    return False
                _date(row.get("due_at"))
                from .protocols import rank_order
                rank_order(row.get("rank"),row.get("due_at"),row.get("created_at"))
        return True
    except (TypeError, ValueError):
        return False


def load(tracker, root: Path, owner, *, now=None):
    """Cache only complete packets. Failures never become empty lists."""
    now = now or datetime.now(timezone.utc)
    try:
        if owner is not None and owner in getattr(tracker,"_decision_candidates",{}):
            return projected(tracker,owner,now=now)
        if not tracker._use_remote_projection():
            raise ValueError("unsupported decision projection")
        proof = tracker.prove_store()
        identity = hashlib.sha256(json.dumps([proof.quipu_url,proof.graph,owner,"priority-rank-v1"]).encode()).hexdigest()
        path = root / "cache" / ("decision-" + identity + ".json")
        try:
            if path.is_symlink() or path.stat().st_size > 32768:
                raise ValueError("unsafe decision cache")
            cached = json.loads(path.read_text())
            age = now.timestamp() - cached["at"]
            if cached["identity"] == identity and 0 <= age < TTL and _valid_packet(cached["packet"]):
                return Answer.complete_read(cached["packet"],how="scoped decision cache <=30s")
        except (OSError, ValueError, KeyError, TypeError):
            pass
        answer = projected(tracker,owner,now=now)
        if answer.complete:
            try:
                if path.parent.is_symlink():
                    raise OSError("decision cache parent is a symlink")
                path.parent.mkdir(parents=True,exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w",dir=path.parent,delete=False) as file:
                    json.dump({"identity":identity,"at":now.timestamp(),"packet":answer.exact()},file)
                os.replace(file.name,path)
            except OSError:
                pass  # cache bookkeeping cannot change the observed answer
        return answer
    except Exception as error:
        return Answer.capped({},how="decision lists",caveat=f"UNKNOWN: {type(error).__name__}")
