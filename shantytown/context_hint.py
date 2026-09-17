"""Advisory context feedback through existing Stop hooks, never an auto-cycle.

Work ceilings withhold new work and survive idle gaps. Occupancy does neither:
compaction can lower it, and a worker mid-apply must be free to finish safely.
The only 'block' here is the model-facing feedback protocol, once per crossing.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from . import config, context_spend as cs, session_budget as sb


def policy(root, card):
    if root is None:
        return None, None
    cfg, error = config.load_or_default(Path(root))
    # A malformed window must not turn an armed hint silently OFF.
    return (sb.ContextLimits(), str(error)) if error else (
        cfg.session_budget.context_for(card.role, card.name), None)


def _path(root, card):
    return Path(root) / "context_budget" / f"{card.name}.json"


def _load(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        json.dump(data, f)
        tmp = f.name
    os.replace(tmp, path)


def _reading(payload, limits, error=None):
    if error:
        return cs.ContextReading(cs.UNKNOWN, detail=error)
    if not payload.get("session_id"):
        return cs.ContextReading(cs.UNKNOWN, detail="Stop payload has no session_id")
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return cs.ContextReading(cs.UNKNOWN, detail="Stop payload has no transcript_path")
    return cs.read(path, limits.window)


def _category(reading, limits):
    if reading.state != cs.MEASURED:
        return reading.state
    return "high" if reading.over(limits.threshold_pct) else "low"


def emit(root, card, payload=None) -> bool:
    """Emit at most one JSON feedback object; subsequent stops remain allowed.

    Called before active-anchor/role exits, so one long task and administrators
    receive the hint too. The snapshot has its own marker, never the work-budget
    marker that can prohibit haul/feed for the rest of a session.
    """
    try:
        limits, error = policy(root, card)
        if limits is None:
            return False
        if payload is None:
            try:
                payload = {} if sys.stdin.isatty() else json.load(sys.stdin)
            except (OSError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        reading = _reading(payload, limits, error)
        category = _category(reading, limits)
        path = _path(root, card)
        old = _load(path)
        key = [payload.get("session_id"), payload.get("transcript_path"),
               category, limits.window, limits.threshold_pct, error]
        # A below-threshold observation resets the crossing. Session/config
        # changes also re-arm; a stale marker must not silence a new session.
        due = category != "low" and old.get("reported") != key
        snapshot = dict(payload={k: payload.get(k) for k in
                                 ("session_id", "transcript_path")},
                        at=time.time(), reported=key if category != "low" else None,
                        state=reading.state, detail=reading.label(),
                        ceiling=({"measure": "context", "measured": reading.pct,
                                  "limit": limits.threshold_pct} if category == "high" else None))
        try:
            _save(path, snapshot)
        except OSError as exc:
            print(f"context hint: cannot retain observation ({type(exc).__name__})", file=sys.stderr)
        if not due:
            return False
        if category == "high":
            message = (f"CONTEXT HINT: {reading.label()}; configured hint at "
                       f"{limits.threshold_pct:g}%. Finish any critical operation, write "
                       "a checkpoint, then request `st agent cycle --self --checkpoint-file "
                       "<notes-file>`. This is advisory: continue safely if needed; "
                       "no cycle has been scheduled.")
        else:
            message = (f"CONTEXT MEASUREMENT for {card.name}: {reading.label()}. This is not an "
                       "under-threshold reading. Check the transcript and "
                       "[session_budget] context_window configuration. "
                       "No cycle has been scheduled; work may continue.")
        print(json.dumps({"decision": "block", "reason": message}))
        return True
    except Exception as exc:  # a reader failure must not prevent an agent stopping
        print(f"context UNKNOWN: hint reader failed ({type(exc).__name__})", file=sys.stderr)
        return False


def crew_label(root, card) -> str | None:
    """Read the last hook's exact transcript, never guess by newest workspace file."""
    limits, error = policy(root, card)
    if limits is None:
        return None
    data = _load(_path(root, card))
    payload = data.get("payload")
    observed = data.get("at")
    if not isinstance(payload, dict) or not isinstance(observed, (int, float)):
        return "context UNKNOWN — invalid or missing Stop observation"
    current = sb.current_session(root, card.name)
    if (not current or current != payload.get("session_id")
            or time.time() - observed > sb.STALE_AFTER_S):
        return "context UNKNOWN — no recent Stop observation for the current session"
    reading = _reading(payload, limits, error)
    prefix = "ceiling (context), advisory: " if _category(reading, limits) == "high" else ""
    return prefix + reading.label()
