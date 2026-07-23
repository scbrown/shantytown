"""desirepath (dp) — an OPTIONAL data source for st (internal-ref).

dp records FAILED tool calls: the capabilities an agent reached for that did not
exist ("desire paths"). It is the failed-harness signal that pairs with st's
successful-harness signal (files touched, skills used, tokens) — one improvement
loop, two halves.

st reads that signal wherever it is useful (doctor today; the dashboard and a
stats surface next) — but ONLY when dp is present. Absent dp, every function here
returns None and the caller shows nothing. st works whole with dp missing, the
same discipline the shanty status segments follow when st itself is missing: an
optional input hides, it never errors.

The read is over dp's own machine-readable output (`dp stats --json`), not its
SQLite file directly — dp owns its schema; st consumes its published contract.
"""
from __future__ import annotations

import json
import shutil
import subprocess


def available() -> bool:
    """True iff the dp binary is on PATH. The single gate every reader passes
    through, so 'dp absent' is one branch, checked once."""
    return shutil.which("dp") is not None


def _run_json(*args: str):
    """Run `dp <args>` and parse stdout as JSON, or return None on any failure —
    dp absent, a non-zero exit, a timeout, or unparseable output. None is the one
    'we cannot tell' value; callers render nothing for it, never a guess."""
    if not available():
        return None
    try:
        r = subprocess.run(["dp", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except (ValueError, json.JSONDecodeError):
        return None


def summary() -> dict | None:
    """A compact view of the failed-tool-call signal, or None if dp is absent or
    unreadable:

        {"total": int, "unique": int, "top": [(name, count), ...]}

    `total` is how many failed calls dp has recorded, `unique` how many distinct
    tool names, `top` the most-reached-for missing capabilities (the improvement
    candidates). Shaped for a one-liner in `st doctor` or a tile on the dashboard;
    the caller decides how much of `top` to show.
    """
    data = _run_json("stats", "--json")
    if not isinstance(data, dict):
        return None
    # `or []`, not a .get default: a FRESH dp (zero data — exactly the state
    # `st doctor --install` leaves it in) emits `"top_desires": null`, and
    # .get(key, []) returns that existing None. Found live by the internal-ref
    # end-to-end: doctor crashed with a TypeError the moment the tool it had
    # just installed became visible.
    top = [
        (d.get("name"), d.get("count"))
        for d in (data.get("top_desires") or [])
        if isinstance(d, dict) and d.get("name")
    ]
    return {
        "total": data.get("total_desires"),
        "unique": data.get("unique_paths"),
        "top": top,
    }


def summary_line() -> str | None:
    """A single human line for `st doctor`, or None when dp is absent/unreadable.

        "321 failed tool calls captured (7 unique); top: Bash×313, Read×3"
    """
    s = summary()
    if not s or s.get("total") is None:
        return None
    parts = f"{s['total']} failed tool calls captured ({s['unique']} unique)"
    top = s.get("top") or []
    if top:
        shown = ", ".join(f"{name}×{count}" for name, count in top[:3])
        parts += f"; top: {shown}"
    return parts
