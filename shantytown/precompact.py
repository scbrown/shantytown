"""PreCompact hook — the checkpoint that lands at the TRUE compaction boundary.

Stiwi, 2026-09-03 17:36 EDT, verbatim to sattler:

    "handoff soon , you should be handing off before compaction same with all
     st agents"

st already had a handoff mechanism (handoff_text, aegis-x6yoq): the 400k idle
cycle line and the 600k mid-haul handoff line, both naming `st cycle --self
--checkpoint-file`. **The gap was never absence, it was ORDERING** (aegis-902vnu,
sattler): those lines are "before compaction" only if every harness compacts
LATER than they fire. Neither harness's own compaction threshold is read by st,
so on any agent whose harness compacts first, the st line fires on a session that
has already lost its reasoning and the checkpoint gets written FROM THE SUMMARY —
exactly the thing being forbidden.

This module closes that with the only hook that runs at the real boundary, and it
does two separable jobs there:

1. **MEASURE** where compaction actually fires, per agent, per model, appending
   to `<root>/compaction.jsonl`. That is deliverable 1 of the bead and it is a
   MEASUREMENT rather than a derivation on purpose — see WHAT THE BINARY SAYS
   below for why the derivation is not enough on its own.
2. **CHECKPOINT** the held bead from the transcript tail, if the agent has not
   already written one since the last boundary.

WHAT THE BINARY SAYS (claude 2.1.260, read out of the shipped bundle under the
install's `versions/` directory, 2026-09-04; confidence:extracted for the SHAPE and INFERRED for the numbers,
because reading the arithmetic is not the same as observing it fire):

  * `executePreCompactHooks` (FX) collects the stdout of every succeeding hook and
    joins it into `newCustomInstructions` — **a PreCompact hook's stdout steers
    the compaction summary.** That is why this module prints a preservation
    instruction rather than staying silent: the summary is the artifact the
    post-compaction session actually reads, so telling the summariser to keep
    landed-vs-local, next step and rollback is a second, cheaper handoff.
  * A PreCompact hook CAN block (`"Compaction blocked by PreCompact hook"`), and
    the caller's own comment on that path is `continuing uncompacted`. **So a
    refusal does not buy time, it removes the only relief valve** and walks the
    session into `"Conversation too long. Press esc twice…"`. The bead offers
    "refuses/prompts until a checkpoint exists (or writes one)"; this takes the
    WRITE arm, deliberately, and never blocks. An agent that cannot compact
    cannot write a checkpoint either.
  * The auto-compact threshold is `window - min(maxOutputTokens, 20000) - 13000`
    (`Ove`/`tF`). On a 1M-window model that is ~967k, comfortably ABOVE both st
    lines; on a 200k-window model it is ~167k, BELOW BOTH — such an agent
    compacts before st ever says a word. The ordering bug is therefore
    model-scoped, not harness-scoped, which is precisely what a per-agent
    measurement log can answer and a constant in this file cannot.

FAIL-OPEN, ALWAYS. Every path returns 0. A hook that can stop an agent from
shedding context is a hook that can wedge the fleet, and this one runs at the
worst possible moment to be clever.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The marker that makes an auto-written checkpoint recognisable — to a reader, to
# `st cycle`'s gate, and to this module's own "did I already do this" check. One
# string, named once, because a marker written under one spelling and searched
# under another is a checkpoint that exists and cannot be found.
CHECKPOINT_MARKER = "[st precompact checkpoint]"

# Where measurements land. NOT under events/ — this is instrumentation, not a
# fleet event, and nothing schedules on it.
MEASUREMENT_FILE = "compaction.jsonl"

# How much of the transcript tail goes into the checkpoint body. A checkpoint is
# a POINTER (handoff_text's rule): enough for a reader to resume, not a replay.
TAIL_MESSAGES = 6
TAIL_CHARS = 4000

# What the compaction summariser is told to preserve. This is the bead's own
# definition of a handoff — state, landed-vs-local, exact next step, rollback.
SUMMARY_INSTRUCTIONS = (
    "Preserve, verbatim where possible: (a) which changes are LANDED (committed "
    "and pushed, with shas) versus LOCAL (working tree only); (b) the exact next "
    "step, as a command or a file:line, not a paraphrase; (c) the rollback for "
    "anything already deployed; (d) the id of the bead being held. Prefer these "
    "over narrative."
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_hook_input() -> dict:
    """The hook payload, or {} — an unreadable payload is not an error worth
    failing on, it is a hook that does nothing."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        value = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _transcript_records(path: str | None) -> list[dict]:
    """Every JSON record in the transcript, in order. Bad lines are SKIPPED, not
    fatal: a transcript being appended to while we read it can end mid-line."""
    if not path:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def depth_tokens(records: list[dict]) -> int | None:
    """Context depth at the boundary, from Claude Code's OWN accounting.

    The last assistant message's usage is what the runtime just sent: input +
    both cache legs. Output is deliberately EXCLUDED — it is what came back, not
    what the window held going in, and counting it would overstate the number
    this log exists to compare against a threshold.

    None means UNKNOWN, never "shallow". A measurement file that cannot tell the
    difference is worse than one with gaps in it (aegis-a-detector-that-pages-
    nobody: 'cannot tell' and 'fine' must not render the same).
    """
    for rec in reversed(records):
        if rec.get("type") != "assistant":
            continue
        usage = ((rec.get("message") or {}).get("usage") or {})
        if not isinstance(usage, dict):
            continue
        parts = [usage.get("input_tokens"),
                 usage.get("cache_read_input_tokens"),
                 usage.get("cache_creation_input_tokens")]
        vals = [int(p) for p in parts if isinstance(p, (int, float))]
        if vals:
            return sum(vals)
    return None


def _model(records: list[dict]) -> str:
    for rec in reversed(records):
        model = (rec.get("message") or {}).get("model")
        if isinstance(model, str) and model:
            return model
    return ""


def _session_started(records: list[dict]) -> str:
    for rec in records:
        ts = rec.get("timestamp")
        if isinstance(ts, str) and ts:
            return ts
    return ""


def transcript_tail(records: list[dict], limit: int = TAIL_MESSAGES) -> str:
    """The last few assistant text blocks, oldest-first.

    Assistant TEXT only: tool calls and their results are the bulk of a
    transcript and the least resumable part of it — a reader wants what the agent
    concluded, not what it ran. If the agent said nothing quotable, this returns
    "" and the caller writes a checkpoint that says so rather than one that
    pretends.
    """
    chunks: list[str] = []
    for rec in reversed(records):
        if len(chunks) >= limit:
            break
        if rec.get("type") != "assistant":
            continue
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "\n".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text").strip()
        else:
            continue
        if text:
            chunks.append(text)
    body = "\n\n".join(reversed(chunks)).strip()
    return body[-TAIL_CHARS:] if len(body) > TAIL_CHARS else body


def _last_boundary(log: Path, session_id: str) -> str:
    """When this session last compacted, from our own measurement log.

    The window a checkpoint must be newer than. Falls back to "" (= use the
    session start), which is the honest answer for a first compaction.
    """
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    seen = ""
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("session_id") == session_id:
            at = rec.get("at")
            if isinstance(at, str):
                seen = at
    return seen


def has_checkpoint_since(comments, who: str, since: str) -> bool:
    """Did `who` already write a handoff on this bead since `since`?

    ONE PREDICATE, and it lives in cycle.py with the codex-side gate that shares
    it — see cycle.checkpoint_since for why any comment by the agent counts and
    not only a marked one. Re-exported here so this module's own reader can see
    what the decision is without following it, but NOT reimplemented: two
    spellings of "is there a checkpoint" is two answers.
    """
    from .cycle import checkpoint_since
    return checkpoint_since(comments, who, since)


def checkpoint_body(agent: str, bead: str, depth: int | None, trigger: str,
                    tail: str) -> str:
    """The comment. Says WHAT IT IS and WHERE IT CAME FROM, first line, because a
    reader must never mistake a machine's tail-scrape for an agent's own handoff.
    """
    depth_s = f"{depth/1000:.0f}k tokens" if depth else "depth unknown"
    head = (f"{CHECKPOINT_MARKER} {agent} — context compacted "
            f"({trigger}, at {depth_s}, {_now()}).\n"
            f"AUTO-WRITTEN from the transcript tail at the compaction boundary, "
            f"NOT composed by the agent. It is the reasoning that was about to be "
            f"summarised away; treat it as evidence, not as a considered handoff.\n")
    if not tail:
        return (head + "\nNo assistant text in the tail to preserve — the "
                "boundary fell inside a tool-call run. The measurement is in "
                "compaction.jsonl; the reasoning is gone.\n")
    return head + "\n--- transcript tail ---\n" + tail + "\n"


def _record(log: Path, rec: dict) -> None:
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError as e:
        print(f"precompact: could not record measurement ({e})", file=sys.stderr)


def _held_bead(root: Path, me: str) -> str:
    """The bead this agent holds, read through the DEPLOYMENT's backend.

    Same resolver the stop hooks use (_plate_reader): a files-only read on a
    beads fleet reports every plate empty, which here would mean "no bead to
    checkpoint" for every agent on the fleet — a silent total failure that looks
    like a quiet success (aegis-tisp).
    """
    try:
        from .stop_event import _plate_reader
        plate = _plate_reader(root)(me)
    except Exception as e:
        print(f"precompact: plate unreadable ({e})", file=sys.stderr)
        return ""
    if plate is None:
        return ""
    iid = (getattr(plate, "id", None)
           or (plate.get("id") if isinstance(plate, dict) else None))
    return str(iid) if iid else ""


def _tracker(root: Path):
    from .beads import EXTRA_REPOS_KEY, parse_extra_repos
    from .br import BrTracker
    from .deployment import deployment_default
    if (deployment_default(root, "SHANTY_BACKEND") or "files") not in ("beads", "br"):
        raise RuntimeError("deployment backend is not beads/br; cannot comment")
    return BrTracker(repo=(deployment_default(root, "SHANTY_BR_REPO")
                           or deployment_default(root, "SHANTY_BEADS_REPO")),
                     extra_repos=parse_extra_repos(
                         deployment_default(root, EXTRA_REPOS_KEY)))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    payload = _read_hook_input()
    # Print the summary instruction FIRST and unconditionally. It is the one
    # half that cannot fail, costs nothing, and helps even when the tracker is
    # unreachable — which is exactly when a checkpoint is hardest to land.
    print(SUMMARY_INSTRUCTIONS)

    try:
        from .stop_event import _root
        root = _root(argv)
    except Exception as e:
        print(f"precompact: no store root ({e}); measured nothing", file=sys.stderr)
        return 0

    me = os.environ.get("SHANTY_AGENT", "")
    records = _transcript_records(payload.get("transcript_path"))
    depth = depth_tokens(records)
    trigger = str(payload.get("trigger") or "?")
    session_id = str(payload.get("session_id") or "")
    log = Path(root) / MEASUREMENT_FILE
    since = _last_boundary(log, session_id) or _session_started(records)

    _record(log, {"at": _now(), "agent": me, "harness": "claude",
                  "session_id": session_id, "trigger": trigger,
                  "depth_tokens": depth, "model": _model(records),
                  "transcript_messages": len(records)})

    if not me:
        print("precompact: $SHANTY_AGENT unset — measured, but no agent to "
              "checkpoint for", file=sys.stderr)
        return 0

    bead = _held_bead(Path(root), me)
    if not bead:
        print("precompact: no held bead — measurement recorded, no checkpoint",
              file=sys.stderr)
        return 0

    try:
        trk = _tracker(Path(root))
        from .br import comments as br_comments
        existing = br_comments(trk, bead)
    except Exception as e:
        # Cannot READ the comments. Write anyway: an unreadable tracker is not
        # evidence that a checkpoint exists, and the whole point of this hook is
        # that the reasoning is about to be destroyed.
        print(f"precompact: comments unreadable on {bead} ({e}); "
              f"writing a checkpoint rather than assuming one exists",
              file=sys.stderr)
        existing = []
        try:
            trk = _tracker(Path(root))
        except Exception:
            print("precompact: no tracker — checkpoint NOT written", file=sys.stderr)
            return 0

    if has_checkpoint_since(existing, me, since):
        print(f"precompact: {me} already checkpointed {bead} since {since}",
              file=sys.stderr)
        return 0

    body = checkpoint_body(me, bead, depth, trigger, transcript_tail(records))
    try:
        from .br import append_comment
        append_comment(trk, bead, body)
        print(f"precompact: checkpoint written to {bead}", file=sys.stderr)
    except Exception as e:
        print(f"precompact: checkpoint NOT written to {bead} ({e})", file=sys.stderr)
    return 0


if __name__ == "__main__":         # pragma: no cover - entry point
    raise SystemExit(main())
