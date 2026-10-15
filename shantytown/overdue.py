"""Due-date reminders on the existing fleet-tend schedule (aegis-873).

Reads are bounded projections. Delivery intent is written before sending; a
stable inbox receipt marker reconciles unknown writes without duplicate sends.
A complete healthy read rearms resolved/re-dated episodes. UNKNOWN preserves
state and sends nothing. One digest per recipient per pass, then at most daily.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import fcntl

from .files import write_json_atomic
from .inbox import is_message


@dataclass(frozen=True)
class PendingReceipt:
    identifier: str


def verify_inbox_receipt(value,recipient,marker,identifier):
    if (not isinstance(value,dict) or value.get("id") != identifier
            or value.get("assignee") != recipient
            or not value.get("title","").startswith("inbox: " + marker + " ")
            or "inbox" not in (value.get("labels") or [])):
        raise ValueError("exact reminder receipt UNKNOWN")
    return identifier


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def findings(rows, now):
    result = []
    seen = set()
    for row in rows:
        if row.get("status") not in ("open", "in_progress", "blocked"):
            continue
        if is_message(row.get("title", "")) or any(str(label).lower() == "inbox" for label in (row.get("labels") or [])):
            continue
        identifier, due = row.get("id"), row.get("due_at")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("overdue row identity missing or duplicated")
        seen.add(identifier)
        if not due:
            continue
        if instant(due) < now:
            result.append(row)
    return result


class OverdueAlerter:
    def __init__(self, root, reg, *, read, deliver, events=None, receipt=None, ack=None, specs=None, spec_ack=None, spec_deliver=None, now=None, log=None):
        self.root, self.reg = Path(root), reg
        self.specs, self.spec_ack, self.spec_deliver = specs, spec_ack, spec_deliver
        self.ack = ack
        self.receipt = receipt
        self.events = events
        self.read, self.deliver = read, deliver
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.log = log or (lambda message: None)
        self.path = self.root / "notify" / "overdue.json"

    def sweep(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.with_suffix(".lock").open("a") as held:
                try:
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return []
                return self._sweep()
        except Exception as error:
            self.log(f"overdue: UNKNOWN ({type(error).__name__}); no repeat delivery")
            return []

    def _sweep(self):
        from .tier import find_administrator
        now = self.now()
        rows = self.read().exact()
        current = findings(rows, now)
        try:
            events = self.events().exact() if self.events is not None else []
        except Exception as error:
            self.log(f"unlock producer UNKNOWN ({type(error).__name__}); overdue reminders still evaluated")
            events = []
        # One shared notifier; 8iq supplies condition events, not a sender.
        for event in events:
            if (not isinstance(event,dict) or event.get("kind") != "unlock"
                    or not all(isinstance(event.get(k),str) and event[k] for k in ("id","episode"))
                    or (event.get("recipient") is not None and not isinstance(event["recipient"],str))):
                raise ValueError("invalid unlock reminder event")
            current.append({"id":event["id"],"due_at":event["episode"],
                            "assignee":event["recipient"],"kind":"unlock"})
        try:
            spec_events = self.specs().exact() if self.specs is not None else []
        except Exception as error:
            self.log(f"spec producer UNKNOWN ({type(error).__name__}); other reminders still evaluated")
            spec_events = []
        for event in spec_events:
            if (not isinstance(event,dict) or event.get("kind") != "spec"
                    or not all(isinstance(event.get(k),str) and event[k] for k in ("id","episode","summary"))
                    or type(event.get("previous")) is not int or type(event.get("version")) is not int
                    or not 0 <= event["previous"] < event["version"]
                    or event["episode"] != str(event["version"]) or type(event.get("urgent")) is not bool):
                raise ValueError("invalid spec notification event")
            current.append({"id":event["id"],"due_at":event["episode"],"kind":"spec",
                            "assignee":event.get("recipient"),"detail":event})
        # Corruption is UNKNOWN, not an empty ledger authorizing repeat wakes.
        ledger = json.loads(self.path.read_text()) if self.path.exists() else {"entries": {}, "pending": {}}
        if not isinstance(ledger, dict) or set(ledger) != {"entries", "pending"}:
            raise ValueError("invalid overdue ledger")
        entries, pending = ledger["entries"], ledger["pending"]
        if not isinstance(entries, dict) or not isinstance(pending, dict):
            raise ValueError("invalid overdue ledger entries")
        if len(entries) > 10000:
            raise ValueError("unlock receipt retention budget reached; review compaction before delivery")
        details = {}
        grouped = {}
        live_keys = set()
        for row in current:
            recipient = row.get("assignee") or find_administrator(self.reg)
            if not recipient:
                self.log(f"overdue: {row['id']} recipient UNKNOWN")
                continue
            self.reg.get(recipient)  # Unknown owner must not silently reroute.
            kind = row.get("kind", "overdue")
            # Unlock identity belongs to the producer generation, not a later
            # assignee edit; one event must not resend to another recipient.
            key = json.dumps([kind,row["id"], row["due_at"],
                              recipient if kind == "overdue" else None], separators=(",", ":"))
            if key in live_keys:
                raise ValueError("duplicate reminder episode")
            live_keys.add(key)
            details[key] = row
            previous = entries.get(key)
            if isinstance(previous,dict):
                if set(previous) != {"at","receipt","ack"}:
                    raise ValueError("invalid retained reminder receipt")
                previous = previous["at"]
            if previous is not None and (not isinstance(previous, (int,float)) or isinstance(previous,bool)):
                raise ValueError("invalid overdue delivery timestamp")
            if previous is None or (kind == "overdue" and now.timestamp() - previous >= 86400):
                grouped.setdefault(recipient, []).append((key, row["id"]))
        # Reconcile already attempted messages byte-for-byte before new work.
        sent = []
        for recipient, packet in list(pending.items()):
            # A recorded attempt can have committed despite an unknown result.
            # Even a complete absent receipt read never authorizes a resend.
            if packet.get("attempted") is not True:
                raise ValueError("overdue attempt fence missing; operator reconciliation required")
            known_id = packet.get("receipt_id")
            if known_id is not None and (not isinstance(known_id,str) or not known_id):
                raise ValueError("invalid known reminder receipt ID")
            already = (self.receipt(recipient,packet["marker"],known_id) if known_id is not None
                       else self.receipt(recipient,packet["marker"])) if self.receipt is not None else False
            if already and any(json.loads(key)[0] in ("unlock","spec") for key in packet["keys"]) and (self.ack is not None or self.spec_ack is not None) and not isinstance(already,str):
                raise ValueError("unlock ACK requires a verified receipt ID")
            if not already:
                self.log(f"overdue: {recipient} attempted receipt UNKNOWN; no resend")
                continue
            for key in packet["keys"]:
                if key in live_keys or json.loads(key)[0] in ("unlock","spec"):
                    entries[key] = {"at":now.timestamp(),"receipt":already if isinstance(already,str) else None,"ack":None}
            del pending[recipient]
            sent.append(recipient)
            write_json_atomic(self.path, ledger)
        for recipient, candidates in sorted(grouped.items()):
            if recipient in pending or recipient in sent:
                continue
            keys = sorted(key for key, _ in candidates)
            digest = hashlib.sha256(json.dumps([keys,now.isoformat()]).encode()).hexdigest()[:24]
            marker = f"[overdue:{digest}]"
            ids = sorted(identifier for _,identifier in candidates)
            # Bound the durable title; complete recipient membership is in the
            # ledger packet, not lost when the visible preview is shortened.
            preview = ", ".join(ids[:5])[:150]
            overdue_count = sum(json.loads(key)[0] == "overdue" for key in keys)
            spec_keys = [key for key in keys if json.loads(key)[0] == "spec"]
            unlock_count = len(keys) - overdue_count - len(spec_keys)
            body = (f"Work reminders ({overdue_count} overdue, {unlock_count} unlocked): {preview}. "
                    "Close, re-rank, or re-date with a reason. Full digest: notify/overdue.json.")
            if spec_keys:
                notices = []
                for key in spec_keys[:3]:
                    detail = details[key]["detail"]
                    summary = detail["summary"].encode()[:30].decode("utf-8", "ignore")
                    notices.append(f'{detail["id"]} spec v{detail["previous"]} -> v{detail["version"]}: {summary}')
                body = "; ".join(notices) + ". Diff: sd history <id>; acknowledge: st work item ack <id>."
                if len(spec_keys) > 3 or overdue_count or unlock_count:
                    body += " Other reminders retained in notify/overdue.json."
                if len((marker + " " + body).encode()) > 400:
                    raise ValueError("spec pointer exceeds shared notification byte cap")
            packet = {"marker":marker,"body":body,"keys":keys,"attempted":True}
            pending[recipient] = packet
            write_json_atomic(self.path, ledger)
            if spec_keys:
                if self.spec_deliver is None:
                    raise ValueError("spec delivery adapter unavailable")
                urgent = any(details[key]["detail"]["urgent"] for key in spec_keys)
                delivered = self.spec_deliver(recipient, marker, body, urgent=urgent)
            else:
                delivered = self.deliver(recipient, marker, body)
            if isinstance(delivered,PendingReceipt):
                packet["receipt_id"] = delivered.identifier
                write_json_atomic(self.path,ledger)
                continue
            if delivered is not None and type(delivered) not in (bool,str):
                raise ValueError("unsupported reminder delivery evidence")
            if delivered and ((self.ack is None and self.spec_ack is None) or not (unlock_count or spec_keys) or isinstance(delivered,str)):
                for key in keys:
                    entries[key] = {"at":now.timestamp(),"receipt":delivered if isinstance(delivered,str) else None,"ack":None}
                del pending[recipient]
                sent.append(recipient)
                write_json_atomic(self.path, ledger)
        if self.ack is not None or self.spec_ack is not None:
            for key, evidence in entries.items():
                kind, identifier, episode, _recipient = json.loads(key)
                acknowledge = self.spec_ack if kind == "spec" else self.ack if kind == "unlock" else None
                if acknowledge is None or not isinstance(evidence,dict) or evidence.get("ack") == "confirmed":
                    continue
                retained = evidence.get("receipt")
                if not isinstance(retained,str) or not retained:
                    self.log("unlock receipt UNKNOWN; producer ACK held")
                    continue
                attempted = evidence.get("ack") == "attempted"
                evidence["ack"] = "attempted"
                write_json_atomic(self.path,ledger)
                try:
                    if acknowledge(identifier,episode,retained,attempted=attempted):
                        evidence["ack"] = "confirmed"
                        write_json_atomic(self.path,ledger)
                except Exception as error:
                    self.log(f"unlock ACK UNKNOWN ({type(error).__name__}); retained receipt, no resend")
        ledger["entries"] = {key:value for key,value in entries.items()
                             if key in live_keys or json.loads(key)[0] in ("unlock","spec")}
        write_json_atomic(self.path, ledger)
        return sent
