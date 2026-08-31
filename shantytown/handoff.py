"""Versioned task/result envelopes shared with browser-resident harnesses.

Transport is deliberately absent.  A tmux send, BroadcastChannel message, or
durable inbox may carry these bytes, but none of those mechanisms changes what
the envelope means or whether its attested identity is complete.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping


VERSION = "crew-handoff-v1"
HARNESSES = frozenset({"shantytown", "creel"})
STATES = ("queued", "claimed", "succeeded", "failed")
_TRANSITIONS = {
    "queued": frozenset({"claimed", "failed"}),
    "claimed": frozenset({"queued", "succeeded", "failed"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
}
_IDENTITY_FIELDS = ("agent", "session", "key_id", "introducer", "binding")


class InvalidHandoff(ValueError):
    """The envelope is ambiguous, incomplete, or violates its state model."""


def _object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidHandoff(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidHandoff(f"{field} must be a non-empty string")
    if any(ord(char) < 32 for char in value):
        raise InvalidHandoff(f"{field} contains a control character")
    return value


def validate_identity(value: Any, field: str = "identity") -> dict[str, str]:
    identity = _object(value, field)
    unknown = set(identity) - set(_IDENTITY_FIELDS) - {"harness"}
    if unknown:
        raise InvalidHandoff(f"{field} has unknown field(s): {', '.join(sorted(unknown))}")
    out = {name: _text(identity.get(name), f"{field}.{name}") for name in _IDENTITY_FIELDS}
    out["harness"] = _text(identity.get("harness"), f"{field}.harness")
    if out["harness"] not in HARNESSES:
        raise InvalidHandoff(f"{field}.harness must be one of {sorted(HARNESSES)}")
    return out


def validate(envelope: Any) -> dict[str, Any]:
    """Return a detached, validated envelope; reject lossy/ambiguous shapes."""
    src = _object(envelope, "envelope")
    allowed = {"version", "id", "origin", "target", "task", "ownership", "state", "result", "failure"}
    unknown = set(src) - allowed
    if unknown:
        raise InvalidHandoff(f"envelope has unknown field(s): {', '.join(sorted(unknown))}")
    if src.get("version") != VERSION:
        raise InvalidHandoff(f"version must be {VERSION!r}")

    out: dict[str, Any] = {
        "version": VERSION,
        "id": _text(src.get("id"), "id"),
        "origin": validate_identity(src.get("origin"), "origin"),
    }
    target = _object(src.get("target"), "target")
    out["target"] = {"harness": _text(target.get("harness"), "target.harness")}
    if set(target) - {"harness", "agent"}:
        raise InvalidHandoff("target has unknown fields")
    if out["target"]["harness"] not in HARNESSES:
        raise InvalidHandoff(f"target.harness must be one of {sorted(HARNESSES)}")
    if target.get("agent") is not None:
        out["target"]["agent"] = _text(target["agent"], "target.agent")

    task = _object(src.get("task"), "task")
    if set(task) - {"id", "title", "pointer"}:
        raise InvalidHandoff("task has unknown fields")
    out["task"] = {"id": _text(task.get("id"), "task.id")}
    for name in ("title", "pointer"):
        if task.get(name) is not None:
            out["task"][name] = _text(task[name], f"task.{name}")

    ownership = _object(src.get("ownership"), "ownership")
    if set(ownership) - {"lease_id", "owner", "claimed_at", "expires_at"}:
        raise InvalidHandoff("ownership has unknown fields")
    out["ownership"] = {"lease_id": _text(ownership.get("lease_id"), "ownership.lease_id")}
    owner = ownership.get("owner")
    out["ownership"]["owner"] = None if owner is None else validate_identity(owner, "ownership.owner")
    for name in ("claimed_at", "expires_at"):
        if ownership.get(name) is not None:
            out["ownership"][name] = _text(ownership[name], f"ownership.{name}")

    state = _text(src.get("state"), "state")
    if state not in STATES:
        raise InvalidHandoff(f"state must be one of {list(STATES)}")
    out["state"] = state
    if state == "queued" and out["ownership"]["owner"] is not None:
        raise InvalidHandoff("queued envelope cannot have an owner")
    if state in {"claimed", "succeeded", "failed"} and out["ownership"]["owner"] is None:
        raise InvalidHandoff(f"{state} envelope requires an owner")

    result, failure = src.get("result"), src.get("failure")
    if state == "succeeded":
        result = _object(result, "result")
        if set(result) - {"pointer", "summary"}:
            raise InvalidHandoff("result has unknown fields")
        out["result"] = {"pointer": _text(result.get("pointer"), "result.pointer")}
        if result.get("summary") is not None:
            out["result"]["summary"] = _text(result["summary"], "result.summary")
        if failure is not None:
            raise InvalidHandoff("succeeded envelope cannot have failure")
    elif state == "failed":
        failure = _object(failure, "failure")
        if set(failure) - {"code", "message", "retryable"}:
            raise InvalidHandoff("failure has unknown fields")
        if not isinstance(failure.get("retryable"), bool):
            raise InvalidHandoff("failure.retryable must be a boolean")
        out["failure"] = {
            "code": _text(failure.get("code"), "failure.code"),
            "message": _text(failure.get("message"), "failure.message"),
            "retryable": failure["retryable"],
        }
        if result is not None:
            raise InvalidHandoff("failed envelope cannot have result")
    elif result is not None or failure is not None:
        raise InvalidHandoff(f"{state} envelope cannot have result or failure")
    return out


def canonical_bytes(envelope: Any) -> bytes:
    """Stable cross-language bytes; signatures and durable IDs bind to these."""
    return json.dumps(validate(envelope), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def transition(envelope: Any, state: str, **changes: Any) -> dict[str, Any]:
    """Apply one legal state transition and validate the resulting envelope."""
    current = validate(envelope)
    if state not in _TRANSITIONS[current["state"]]:
        raise InvalidHandoff(f"illegal transition {current['state']} -> {state}")
    nxt = deepcopy(current)
    nxt["state"] = state
    for name in ("ownership", "result", "failure"):
        if name in changes:
            value = changes[name]
            if value is None:
                nxt.pop(name, None)
            else:
                nxt[name] = value
    return validate(nxt)
