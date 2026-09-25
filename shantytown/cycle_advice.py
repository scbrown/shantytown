"""cycle_advice — at a handoff, is this session worth KEEPING, or cheaper to cycle?

Stiwi, 2026-09-25: *"when needing to handoff there is decision to maintain the
current session or run /clear ... if the current session and handoff and
potentially next task are related enough to keep the session or clear it"* — and,
the second axis, *"has the cache window lapsed for the model in use? ... sometimes
it may be cheaper to rebuild some of that context vs keeping it all and paying for
the cache miss"*.

Before this, the only input to "cycle or not" was DEPTH (the pre-handoff and
cycle lines in triage). Depth answers "can this session continue", never "should
it". A 450k session about to start the next bead in the same module is cycled
anyway; a 250k session that has sat past its cache TTL carries its whole
transcript into unrelated work and pays a full-prefix cache WRITE to do it.

## Why st does not call a model here

Stiwi, same day: *"for my tools i typically dont code in an agent, but allow a
way for an agent, or process, to call /in/ ... people can leave it unconfigured
without really effecting anything and thats one less dependency and key to
setup"*. So relatedness is a SIGNAL that something outside st posts —
`st agent advise <agent> --related P --by <who>` — and st owns only the parts it
can measure (depth, idle time, the cache TTL the transcript records) and the
arithmetic. Jev, the agent itself at its checkpoint, a cron script, a human: all
the same call. Nobody calling in is the supported default, and it decides
nothing (`DEFAULT`), which is today's behaviour exactly.

## What code owns, whatever the signal says

  * past the cycle line the answer is CYCLE. A signal can make st cycle EARLIER,
    never later: a saturated context cannot be kept however related the work.
  * a signal about a different next task than the one on the plate is not about
    this handoff, and a signal older than `signal_max_age_minutes` is not about
    this moment. Both are ignored, and said so.
  * cannot-tell is never a verdict: unmeasured idle time does not count as a
    lapsed cache (that direction would cycle on missing data).

## The cost model, and what it is not

Per turn, a kept context is re-read from cache (`read_multiplier`, 0.1x base
input) — or, once the TTL has lapsed, re-WRITTEN on the first turn at
`write_multiplier` (1.25x for the 5m TTL, 2x for 1h). A cycled session writes a
fresh floor (`fresh_floor_k`: system prompt + resume brief) and then rebuilds
what the next task needs from the old context, estimated as
`related * rebuild_fraction * (depth - floor)`. The multipliers are Anthropic's
list multipliers; on a subscription seat the real currency is the governor's
five_hour/seven_day budget, whose accounting of cache reads st cannot see — so
every number is config, not a constant.

Tokens are not the only thing a kept context buys: the model also keeps the
REASONING behind decisions in flight, which no rebuild recovers. The arithmetic
cannot price that, so a strong relatedness signal (`keep_at`) keeps the session
without consulting it.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

KEEP = "keep"
CYCLE = "cycle"
#: No opinion: the caller does exactly what it did before this module existed.
DEFAULT = "default"
DECISIONS = (KEEP, CYCLE)
#: How far back read_cache looks for a turn that wrote cache and so names its TTL.
TTL_SCAN_RECORDS = 50


@dataclass(frozen=True)
class Policy:
    """[cycle_advice] in shantytown.toml. Every field has a default, so the table
    is optional; with no signal posted none of it is consulted."""
    # THE FLOOR UNDER EVERYTHING (Stiwi: "there needs to be a configurable
    # threshold where we always decide to just clear context"). At or above it the
    # answer is CYCLE and nobody is asked: `--json` says ask=false, so a caller
    # never ships a request judging a context nobody would keep anyway.
    always_cycle_above_k: int = 250
    # What a caller is handed to judge from, bounded (Stiwi: "we dont want to
    # dump like 750k tokens on this request"). A judge that needs more asks for
    # it with --brief-chars, never past the max.
    brief_chars: int = 1500
    max_brief_chars: int = 6000
    keep_at: float = 0.8
    cycle_below: float = 0.3
    signal_max_age_minutes: int = 120
    # Used only when the transcript names no TTL (a harness that does not
    # record it). Claude Code records it per write; see read_cache.
    cache_ttl_seconds: int = 300
    read_multiplier: float = 0.1
    write_multiplier_5m: float = 1.25
    write_multiplier_1h: float = 2.0
    fresh_floor_k: int = 40
    rebuild_fraction: float = 0.3
    expected_turns: int = 10


@dataclass(frozen=True)
class Signal:
    """What something outside st said about this handoff."""
    by: str
    at: float
    next_task: str = ""
    related: float | None = None
    decision: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class Cache:
    """The transcript's last usage record, read for TIMING, not occupancy."""
    last_at: float | None = None
    ttl_seconds: int | None = None


@dataclass(frozen=True)
class Situation:
    agent: str
    depth_k: float | None
    cycle_line_k: float
    next_task: str = ""
    cache: Cache = field(default_factory=Cache)
    now: float = field(default_factory=time.time)

    @property
    def idle_seconds(self) -> float | None:
        return None if self.cache.last_at is None else max(0.0, self.now - self.cache.last_at)


@dataclass(frozen=True)
class Advice:
    decision: str
    why: str
    keep_cost_k: float | None = None
    cycle_cost_k: float | None = None
    cache_lapsed: bool | None = None

    def render(self) -> str:
        costs = ""
        if self.keep_cost_k is not None and self.cycle_cost_k is not None:
            costs = (f" [keep ~{self.keep_cost_k:.0f}k vs cycle "
                     f"~{self.cycle_cost_k:.0f}k input-equivalent]")
        return f"{self.decision}: {self.why}{costs}"


def cache_lapsed(sit: Situation, policy: Policy) -> bool | None:
    idle = sit.idle_seconds
    if idle is None:
        return None
    return idle >= (sit.cache.ttl_seconds or policy.cache_ttl_seconds)


def costs(sit: Situation, related: float, policy: Policy) -> tuple[float, float]:
    """(keep, cycle) in thousands of base-input-equivalent tokens over the next
    task's expected turns. Unknown lapse is priced as warm — the direction that
    does not cycle on missing data."""
    depth = sit.depth_k or 0.0
    floor = min(policy.fresh_floor_k, depth)
    turns = max(1, policy.expected_turns)
    write = (policy.write_multiplier_1h if (sit.cache.ttl_seconds or 0) >= 3600
             else policy.write_multiplier_5m)
    read = policy.read_multiplier
    first = write if cache_lapsed(sit, policy) else read
    keep = first * depth + read * depth * (turns - 1)
    rebuild = related * policy.rebuild_fraction * (depth - floor)
    cycle = write * (floor + rebuild) + read * (floor + rebuild) * (turns - 1)
    return keep, cycle


def decide(sit: Situation, signal: Signal | None, policy: Policy = Policy()) -> Advice:
    lapsed = cache_lapsed(sit, policy)
    if sit.depth_k is not None and sit.depth_k >= sit.cycle_line_k:
        return Advice(CYCLE, f"{sit.depth_k:.0f}k is past the {sit.cycle_line_k:.0f}k "
                             "cycle line; no signal keeps a saturated context",
                      cache_lapsed=lapsed)
    if sit.depth_k is not None and sit.depth_k >= policy.always_cycle_above_k:
        return Advice(CYCLE, f"{sit.depth_k:.0f}k is at or past always_cycle_above_k "
                             f"({policy.always_cycle_above_k}k); not asking",
                      cache_lapsed=lapsed)
    if signal is None:
        return Advice(DEFAULT, "no signal posted; depth lines decide", cache_lapsed=lapsed)
    age_min = (sit.now - signal.at) / 60
    if age_min > policy.signal_max_age_minutes:
        return Advice(DEFAULT, f"signal from {signal.by} is {age_min:.0f}m old "
                               f"(max {policy.signal_max_age_minutes}m); ignored",
                      cache_lapsed=lapsed)
    if signal.next_task and sit.next_task and signal.next_task != sit.next_task:
        return Advice(DEFAULT, f"signal from {signal.by} is about {signal.next_task}, "
                               f"but the plate says {sit.next_task}; ignored",
                      cache_lapsed=lapsed)
    if signal.decision in DECISIONS:
        return Advice(signal.decision, f"{signal.by} decided {signal.decision}"
                      + (f": {signal.reason}" if signal.reason else ""),
                      cache_lapsed=lapsed)
    if signal.related is None:
        return Advice(DEFAULT, f"signal from {signal.by} carries neither a decision "
                               "nor a relatedness", cache_lapsed=lapsed)
    p = signal.related
    if p >= policy.keep_at:
        return Advice(KEEP, f"{signal.by} rates the next task {p:.2f} related "
                            f"(keep at >= {policy.keep_at:.2f})", cache_lapsed=lapsed)
    if p < policy.cycle_below:
        return Advice(CYCLE, f"{signal.by} rates the next task {p:.2f} related "
                             f"(cycle below {policy.cycle_below:.2f}): unrelated work",
                      cache_lapsed=lapsed)
    if sit.depth_k is None:
        return Advice(DEFAULT, "depth unreadable; cannot price keep vs cycle",
                      cache_lapsed=lapsed)
    keep, cycle = costs(sit, p, policy)
    state = {True: "cache lapsed", False: "cache warm", None: "cache state unknown"}[lapsed]
    if cycle < keep:
        return Advice(CYCLE, f"{signal.by} rates relatedness {p:.2f}; {state}; "
                             "cycling is cheaper", keep, cycle, lapsed)
    return Advice(KEEP, f"{signal.by} rates relatedness {p:.2f}; {state}; keeping is cheaper",
                  keep, cycle, lapsed)


def should_ask(sit: Situation, policy: Policy) -> bool:
    """Is there anything for an outside judge to decide? False past either line,
    and False when depth is unreadable: a judge asked about a context of unknown
    size is the request this bound exists to prevent."""
    return (sit.depth_k is not None and sit.depth_k < sit.cycle_line_k
            and sit.depth_k < policy.always_cycle_above_k)


def brief(parts: list[tuple[str, str]], chars: int, policy: Policy) -> dict:
    """The judge's input: labelled parts cut to `chars` in total (clamped to
    max_brief_chars). Each part gets a FAIR SHARE, with what a short part leaves
    unused passed on, so a long checkpoint can never crowd out the next task — a
    prefix cut would drop exactly the half the question is about. `truncated`
    tells the caller more exists, so it can ask again with a larger
    --brief-chars rather than guess."""
    limit = max(0, min(chars, policy.max_brief_chars))
    parts = [(label, body.strip()) for label, body in parts if body and body.strip()]
    budget, out = limit, {}
    for i, (label, body) in enumerate(sorted(parts, key=lambda p: len(p[1]))):
        share = budget // (len(parts) - i)
        out[label] = body[:share]
        budget -= len(out[label])
    full = sum(len(body) for _, body in parts)
    return dict(parts={label: out[label] for label, _ in parts},
                truncated=full > sum(len(v) for v in out.values()),
                chars=limit, full_chars=full, max_chars=policy.max_brief_chars)


# --- the call-in surface ------------------------------------------------------

def _signals_path(root) -> Path:
    return Path(root) / "notify" / "cycle_signals.json"


def _load(root) -> dict:
    try:
        data = json.loads(_signals_path(root).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def post(root, agent: str, signal: Signal) -> None:
    if signal.related is not None and not 0.0 <= signal.related <= 1.0:
        raise ValueError("related must be between 0 and 1")
    if signal.decision is not None and signal.decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    if signal.related is None and signal.decision is None:
        raise ValueError("a signal needs --related or --decision")
    path = _signals_path(root)
    data = _load(root)
    data[agent] = asdict(signal)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as fh:
        json.dump(data, fh, indent=1)
        tmp = fh.name
    os.replace(tmp, path)


def latest(root, agent: str) -> Signal | None:
    raw = _load(root).get(agent)
    if not isinstance(raw, dict):
        return None
    try:
        return Signal(**{k: raw[k] for k in Signal.__dataclass_fields__ if k in raw})
    except TypeError:
        return None


def clear(root, agent: str) -> None:
    """A cycle consumes the signal: it was about the session that just ended."""
    data = _load(root)
    if data.pop(agent, None) is not None:
        path = _signals_path(root)
        path.write_text(json.dumps(data, indent=1))


def read_cache(transcript: str | Path | None) -> Cache:
    """When the last model request landed, and which TTL its cache write used.

    Claude Code transcripts carry `usage.cache_creation.ephemeral_{5m,1h}_input_tokens`
    per request, so the TTL is MEASURED per session, not assumed: the harness
    switches TTLs (a subscription session can drop from 1h to 5m in overage).
    Unreadable is `Cache()` — every field None — never a guess.
    """
    if not transcript:
        return Cache()
    from .context_spend import _reverse_lines
    last = ttl = None
    seen = 0
    try:
        with Path(transcript).open("rb") as fh:
            for line in _reverse_lines(fh):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("isSidechain"):
                    continue
                msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                if seen == 0:
                    try:
                        last = datetime.fromisoformat(
                            rec["timestamp"].replace("Z", "+00:00")).timestamp()
                    except (KeyError, AttributeError, ValueError):
                        pass
                seen += 1
                # A fully cached turn writes nothing and so names no TTL; the
                # newest turn that DID write says which TTL the session is on.
                split = usage.get("cache_creation")
                if isinstance(split, dict):
                    if split.get("ephemeral_1h_input_tokens"):
                        ttl = 3600
                    elif split.get("ephemeral_5m_input_tokens"):
                        ttl = 300
                if ttl is not None or seen >= TTL_SCAN_RECORDS:
                    break
    except OSError:
        return Cache()
    return Cache(last, ttl)
