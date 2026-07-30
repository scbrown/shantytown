"""stopped — WHICH agents are down ON PURPOSE, and since when.

WHY THIS EXISTS (GitHub #29 request 2, measured 2026-07-25).

`st stop` killed a pane and recorded nothing. So every surface that asks "is this
agent down?" got the same answer for two opposite facts — an operator stopped it,
or it died — and read both as a defect. The night that mattered: an operator out
of usage credits stopped nine of eleven crew on instruction, and the administrator's
drain then listed all nine as `re-dispatch <agent> — STOPPED`, i.e. instructed the
operator to undo the shutdown they had just been told to perform. The two states
were indistinguishable *because nothing wrote the distinction*.

THIS IS NOT RETIREMENT, and the difference is the whole design.

  retired (on the CARD)   "and do not bring it back" — `st tend` never respawns it.
  stopped (HERE)          "I stopped it, now" — tend's respawn-on-loss still applies.

Making `st stop` set `retired` would have been the cheap fix and it is wrong: every
ordinary stop/restart cycle would then need an un-retire, and respawn-on-loss would
be off for anything an operator ever stopped by hand.

WHO MAY READ IT: the surfaces that REPORT (`st crew`, the drain's prioritized
workflow). Explicitly NOT `st tend` — a supervisor that honoured this record would
silently become the retirement above, which is the bug in the other direction.

CLEARED ON RELAUNCH, beside the launch stamp and for the same reason: the record
describes a stop that is CURRENT. One left behind after the agent came back would
turn its next real crash into "deliberate" — a fabricated intent, which is worse
than no record at all. Per-lifecycle runtime state, so it lives in its own store
next to launched/ rather than on the card (identity is what the card is for).

WHY IT IS ON DISK AND NOT IN A PROCESS: the operator decision has to survive the
session that made it. That is the same lesson as retirement — a watchdog reverted a
considered shutdown of eight agents in about a minute, because the intent existed
only in the head of the session that formed it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Stop:
    """One deliberate stop."""
    at: float                    # epoch seconds, when st stop killed it
    by: str = ""                 # who ran it, when we can tell ($SHANTY_AGENT)
    reason: str = ""             # `st stop --reason`, free text, may be empty


class FilesStops:
    """Stop records in a directory of json. Same floor as FilesRegistry."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def record(self, agent: str, at: float, by: str = "", reason: str = "") -> None:
        """Note that `agent` was stopped deliberately, just now.

        Best-effort, like the launch stamp: a record that cannot be written leaves
        the agent reading as an ordinary down agent, which is the state st was in
        before this existed. Losing it costs a distinction; failing the stop over
        it would cost the operator the shutdown they asked for.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            from .files import write_json_atomic
            write_json_atomic(self.root / f"{agent}.json",
                              {"at": float(at), "by": by, "reason": reason})
        except OSError:
            pass

    def get(self, agent: str) -> Stop | None:
        p = self.root / f"{agent}.json"
        if not p.is_file():
            return None
        try:
            d = json.loads(p.read_text())
            return Stop(at=float(d["at"]), by=d.get("by", "") or "",
                        reason=d.get("reason", "") or "")
        except (OSError, ValueError, KeyError, TypeError):
            # An unreadable record is NOT an intent. Fall through to "no record",
            # which reads the agent as ordinarily down — the honest default, and
            # the one that cannot invent a decision nobody made.
            return None

    def forget(self, agent: str) -> None:
        """Drop the record — the agent is running again, so the stop is history."""
        try:
            self.root.joinpath(f"{agent}.json").unlink(missing_ok=True)
        except OSError:
            pass

    def at(self, agent: str) -> float | None:
        """Just the timestamp, for callers that only need 'was this deliberate?'."""
        rec = self.get(agent)
        return rec.at if rec else None

    def all(self) -> dict[str, Stop]:
        """Every current record, by agent. Unreadable files are skipped, not
        guessed at."""
        out: dict[str, Stop] = {}
        if not self.root.is_dir():
            return out
        for p in sorted(self.root.glob("*.json")):
            rec = self.get(p.stem)
            if rec is not None:
                out[p.stem] = rec
        return out
