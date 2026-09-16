"""How much of the model's CONTEXT WINDOW this session is sitting in.

Stiwi, 2026-09-14 (aegis-b2nwi9): *"we need to see hints coming in to cycle when
you start hitting a configurable percentage of your model's context setting."*

`session_budget` already ceilings ITEMS and HOURS. Neither is a proxy for context:
one long investigative item can burn more window than six short ones, which is
exactly the shape of a lead's session. A 1M-context session ran to 69% unsignalled
the night this was filed.

## Why this does not reuse harness.read_usage, which looks like exactly the thing

`read_usage` SUMS the per-message deltas, because it answers "what did this session
COST" — cumulative spend, for stats and the governor. Context occupancy is not a
sum, it is the LAST record's occupancy: `cache_read_input_tokens` on turn N already
contains everything the previous turns put in the window, so adding the turns
together double-counts almost the whole conversation. Measured on a live transcript
at ~414k of real occupancy, the summing reader returns tens of millions. The two
readers must stay separate and the names must say which is which.

## Why the window is CONFIGURATION and never inferred from the model string

The obvious design is a per-model window map. It cannot work here, and the failure
is silent in the direction that matters. Measured 2026-09-16 against a live 1M
session's transcript:

    grep -o '"model":"[^"]*"' <transcript> | sort -u   ->   "model":"claude-opus-5"
    occurrences of '[1m]' in any model field           ->   0
    any window / context_limit / max_tokens key        ->   none

The transcript says `claude-opus-5` with no variant marker, while that session's
window was provably 1M — it was at 414k and still running. The same model string
serves both the 200k and the 1M deployment, so a map keyed on it must pick one:
pick 200k and a 1M session reads 207% and fires instantly; pick 1M and a 200k
session reads 41% and NEVER FIRES. The second is the bug this module exists to
prevent, wearing a principled-looking mechanism.

For Claude, the window must be declared by the deployment. Codex token_count
records carry an authoritative model_context_window alongside last_token_usage;
use that capacity when no override is declared. Neither path guesses from model
names. Missing capacity remains UNKNOWN, never a default.

## Three states, and UNKNOWN is not zero

"Cannot measure" must never render as "comfortably under the threshold", because
that is today's behaviour — never cycling — wearing a new name. And `consumed >
window` is reported as its own state rather than clamped to 100%: an impossible
percentage is the ONLY self-evidence available that the configured window is wrong,
so normalising it away destroys the one signal that says "your config is wrong"
and replaces it with a hint firing for the wrong reason.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MEASURED = "measured"
UNKNOWN = "unknown"
OVER_WINDOW = "over_window"


@dataclass(frozen=True)
class ContextReading:
    """Occupancy of the context window, or an explicit inability to say."""
    state: str
    consumed: int | None = None
    window: int | None = None
    model: str | None = None
    detail: str = ""

    @property
    def pct(self) -> float | None:
        """Percentage, ONLY when it means something. UNKNOWN has no percentage —
        returning 0.0 here is precisely the collapse this module refuses."""
        if self.state == UNKNOWN or not self.window or self.consumed is None:
            return None
        return 100.0 * self.consumed / self.window

    def over(self, threshold_pct: float) -> bool:
        """Is the hint due? UNKNOWN is never 'due' and never 'fine' — callers must
        branch on `state` for that, which is why this returns a plain bool only
        for a reading that HAS a percentage."""
        p = self.pct
        return p is not None and p >= threshold_pct

    def label(self) -> str:
        if self.state == UNKNOWN:
            return f"context UNKNOWN — {self.detail}"
        if self.state == OVER_WINDOW:
            return (f"context {self.consumed:,} EXCEEDS the configured window "
                    f"{self.window:,} — the window is wrong, not the session")
        return f"context {self.pct:.0f}% ({self.consumed:,} of {self.window:,})"


def read_consumed(session_path: str | Path) -> tuple[int, str | None] | None:
    """Latest input occupancy and model, preserving the measurement-only API."""
    snapshot = _read_snapshot(session_path)
    return snapshot[:2] if snapshot is not None else None


def _read_snapshot(session_path: str | Path) -> tuple[int, str | None, int | None] | None:
    """(input occupancy, model, native window) from the latest usage record.

    None means cannot-tell — an unreadable, absent or usage-free transcript. It is
    deliberately not (0, None): a caller that cannot distinguish those will report
    a broken reader as an idle session.

    Reads the TOP-LEVEL `message.usage`, not the nested `iterations[-1]` that
    repeats the same field names. On a compaction turn the iterations do not carry
    the whole picture, so the nested copy under-reports exactly when occupancy
    matters most.
    """
    try:
        # Stop hooks and the crew table poll this. Walking from the end avoids
        # repeatedly scanning a long session's entire transcript at its ceiling.
        with Path(session_path).open("rb") as fh:
            for line in _reverse_lines(fh):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("isSidechain"):
                    continue
                payload = rec.get("payload")
                if (rec.get("type") == "event_msg" and isinstance(payload, dict)
                        and payload.get("type") == "token_count"):
                    info = payload.get("info")
                    usage = info.get("last_token_usage") if isinstance(info, dict) else None
                    if not isinstance(usage, dict):
                        continue
                    # Codex input already includes cached input; total_token_usage
                    # is cumulative cost and must never be used as occupancy.
                    value = usage.get("input_tokens")
                    native = info.get("model_context_window")
                    native = native if type(native) is int and native > 0 else None
                    return (value, None, native) if type(value) is int and value >= 0 else None
                msg = rec.get("message")
                msg = msg if isinstance(msg, dict) else {}
                usage = msg.get("usage") or rec.get("usage")
                if not isinstance(usage, dict) or not usage:
                    continue
                if "input_tokens" not in usage:
                    return None
                total = 0
                for key in ("input_tokens", "cache_creation_input_tokens",
                            "cache_read_input_tokens"):
                    v = usage.get(key, 0)
                    if type(v) is not int or v < 0:
                        return None
                    total += v
                return total, msg.get("model") if isinstance(msg.get("model"), str) else None, None
    except OSError:
        return None
    return None


def _reverse_lines(fh):
    """Yield complete JSONL records newest first, retaining cross-block lines."""
    fh.seek(0, 2)
    offset = fh.tell()
    pending = b""
    while offset:
        size = min(offset, 65536)
        offset -= size
        fh.seek(offset)
        parts = (fh.read(size) + pending).split(b"\n")
        pending = parts[0]
        yield from reversed(parts[1:])
    if pending:
        yield pending


def read(session_path: str | Path, window: int | None) -> ContextReading:
    """Use an explicit window, otherwise the native window from the same record."""
    got = _read_snapshot(session_path)
    if got is None:
        return ContextReading(
            UNKNOWN, detail=f"no usage records readable at {session_path}")
    consumed, model, native_window = got
    if window is None:
        window = native_window
    if not window or window <= 0:
        return ContextReading(
            UNKNOWN, consumed=consumed, model=model,
            # The citation for WHY a model string cannot identify a window lives
            # in this module's docstring, not in this message: shantytown is a
            # PUBLIC repo and a string literal is a value the program can emit.
            detail=(f"no context_window configured, and the transcript model "
                    f"{model or 'unknown'!r} does not identify one; "
                    f"set [session_budget] context_window"))
    if consumed > window:
        return ContextReading(OVER_WINDOW, consumed=consumed, window=window,
                              model=model,
                              detail="configured window is smaller than measured use")
    return ContextReading(MEASURED, consumed=consumed, window=window, model=model)
