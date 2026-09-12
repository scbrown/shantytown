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
from datetime import datetime, timedelta, timezone
from pathlib import Path

# The marker that makes an auto-written checkpoint recognisable — to a reader, to
# `st cycle`'s gate, and to this module's own "did I already do this" check. One
# string, named once, because a marker written under one spelling and searched
# under another is a checkpoint that exists and cannot be found.
CHECKPOINT_MARKER = "[st precompact checkpoint]"

# Where measurements land. NOT under events/ — this is instrumentation, not a
# fleet event, and nothing schedules on it.
MEASUREMENT_FILE = "compaction.jsonl"

# WHAT THE HOOK DECIDED, written next to WHERE THE BOUNDARY FELL (aegis-902vnu,
# dearing 2026-09-08). The measurement half of this module has always been
# durable; the CHECKPOINT half reported its branch to stderr only, and a
# PreCompact hook's stderr is captured nowhere. So the ledger recorded 15 real
# boundaries carrying 2 checkpoints and NO WAY TO TELL WHY THE OTHER 13 HAVE
# NONE — the designed skip (the agent had already commented, which is the policy
# working) is indistinguishable from a silent total failure (no held bead, dead
# tracker), and the two have opposite remedies.
#
# That ambiguity is not academic: it is exactly the confirm this bead was left
# open for, and it consumed a forensic pass that COULD NOT ANSWER IT — the
# per-agent held bead at a boundary six days ago is not recoverable from any
# store on this host, and the offered explanation ("those sessions predate the
# hook") is FALSIFIED by the ledger itself, since the ledger line is written by
# this hook and one session compacted twice with a checkpoint on only the second.
#
# A mechanism that cannot say which branch it took can only ever be audited by
# reconstruction, and reconstruction ran out. One field ends that.
OUTCOME_KIND = "outcome"

# The branches, exhaustively. Every non-crash exit from main() after the
# measurement lands stamps exactly one of these.
OUTCOME_NO_AGENT = "no-agent"              # $SHANTY_AGENT unset
OUTCOME_NO_BEAD = "no-held-bead"           # nothing to checkpoint onto
OUTCOME_NO_TRACKER = "no-tracker"          # store unreachable; NOT written
OUTCOME_SKIPPED = "skipped-existing"       # the agent had already written one
OUTCOME_WRITTEN = "written"                # auto-checkpoint appended
OUTCOME_WRITE_FAILED = "write-failed"      # append raised

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


#: How stale the agent's own comment may be and still suppress the checkpoint.
#:
#: The window used to be "since the last boundary" ALONE, which on this fleet is
#: routinely 3-16 hours — so a comment written early in a long window suppressed
#: the checkpoint at a fill many hours later, and the reasoning actually about to
#: be summarised away was preserved by nothing. Measured over all 15 real
#: boundaries (aegis-ugztez, dearing 2026-09-08): 13 suppressed, and 5 of those
#: had a last own comment >= 2h old — 2.1h, 2.7h, 2.9h, 8.8h, 11.1h.
#:
#: 90 minutes is chosen against that distribution, not picked round: the median
#: staleness was 0.9h (54 min), so it leaves the ordinary "I just commented"
#: case suppressing exactly as before, while every one of the five measured gaps
#: crosses it. Widening it past ~2h would re-admit the smallest of them.
#:
#: It bounds THIS HOOK'S floor only — see `_checkpoint_floor`.
CHECKPOINT_MAX_AGE_S = 90 * 60


def _parse_ts(value):
    """Borrowed from cycle.py rather than re-spelled — one parser, one answer."""
    from .cycle import _parse_ts as _p
    return _p(value)


def _own_comment_age_s(comments, who: str, now: str | None = None) -> "int | None":
    """Age in seconds of `who`'s NEWEST comment, or None if there is none/unparseable.

    Reported into the ledger so staleness is a measurement rather than an
    archaeology exercise. Never used to DECIDE anything — the decision is
    `has_checkpoint_since`, and a second spelling of it here would be the
    two-answers problem cycle.py warns about.
    """
    stamp = _parse_ts(now or _now())
    if stamp is None:
        return None
    newest = None
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        if who and (c.get("author") or "") != who:
            continue
        at = _parse_ts(c.get("created_at"))
        if at is not None and (newest is None or at > newest):
            newest = at
    if newest is None:
        return None
    return max(0, int((stamp - newest).total_seconds()))


def _checkpoint_floor(window: str | None, now: str | None = None) -> "str | None":
    """The later of the boundary window and `now - CHECKPOINT_MAX_AGE_S`.

    WHY THE BOUND LIVES HERE AND NOT IN `cycle.checkpoint_since` (aegis-ugztez).
    That predicate is THE one shared with the codex-side `st cycle` gate, and the
    two callers want opposite things from staleness. The hook writing a duplicate
    checkpoint costs a comment; the GATE refusing a cycle over a merely-old
    handoff strands a saturated agent, which is the failure aegis-902vnu's
    three-state design exists to avoid. So the recency bound is expressed as a
    TIGHTER `since` passed by this caller, and the shared predicate is untouched.

    Fails toward WRITING a checkpoint, like every other branch in this module: an
    unparseable `now` falls back to the window (today's behaviour), and an absent
    window yields the age floor rather than None, because "no boundary" is not
    evidence that a handoff exists.
    """
    cutoff = None
    stamp = now or _now()
    parsed_now = _parse_ts(stamp)
    if parsed_now is not None:
        cutoff = (parsed_now - timedelta(seconds=CHECKPOINT_MAX_AGE_S)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    if window is None:
        return cutoff
    if cutoff is None:
        return window
    a, b = _parse_ts(window), _parse_ts(cutoff)
    if a is None:
        return cutoff
    if b is None:
        return window
    return window if a >= b else cutoff


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
        if not isinstance(rec, dict) or rec.get("session_id") != session_id:
            continue
        # Outcome records share the session and sit a moment after the boundary
        # they describe. Taking one as the boundary would shift the next
        # window's floor by that moment — small, and the kind of "harmless"
        # that gets found later. Boundary records are the ones being read here.
        if rec.get("kind") == OUTCOME_KIND:
            continue
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


def _outcome(log: Path, session_id: str, agent: str, bead: str,
             code: str, detail: str = "") -> None:
    """Stamp which branch the checkpoint half took, beside the boundary.

    A SEPARATE record rather than a field on the boundary record, because the
    boundary record is written FIRST and unconditionally — before the tracker is
    touched — and that ordering is deliberate: the measurement must survive a
    store that hangs, which is precisely when the checkpoint is hardest to land.
    Folding the outcome into it would mean holding the measurement hostage to
    the slowest thing this hook does.

    Best-effort, like everything here. An outcome that cannot be written must
    never cost the checkpoint that was already written.
    """
    _record(log, {"at": _now(), "kind": OUTCOME_KIND, "agent": agent,
                  "session_id": session_id, "bead": bead,
                  "checkpoint": code, "detail": detail})


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
    window = _last_boundary(log, session_id) or _session_started(records)
    # Bound the window by recency too (aegis-ugztez). See _checkpoint_floor:
    # the tighter floor is passed by THIS caller so the shared predicate —
    # and the codex `st cycle` gate that depends on it — is unchanged.
    since = _checkpoint_floor(window)

    _record(log, {"at": _now(), "agent": me, "harness": "claude",
                  "session_id": session_id, "trigger": trigger,
                  "depth_tokens": depth, "model": _model(records),
                  "transcript_messages": len(records)})

    if not me:
        print("precompact: $SHANTY_AGENT unset — measured, but no agent to "
              "checkpoint for", file=sys.stderr)
        _outcome(log, session_id, me, "", OUTCOME_NO_AGENT)
        return 0

    bead = _held_bead(Path(root), me)
    if not bead:
        print("precompact: no held bead — measurement recorded, no checkpoint",
              file=sys.stderr)
        _outcome(log, session_id, me, "", OUTCOME_NO_BEAD)
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
        except Exception as e2:
            print("precompact: no tracker — checkpoint NOT written", file=sys.stderr)
            _outcome(log, session_id, me, bead, OUTCOME_NO_TRACKER, str(e2))
            return 0

    if has_checkpoint_since(existing, me, since):
        # Record HOW OLD the qualifying comment was, not just that one existed.
        # The gap this hook now closes was invisible for exactly this reason:
        # the ledger said "clean boundary" and nothing carried the staleness, so
        # the only way to find it was to re-resolve every held bead and re-run
        # the predicate by hand (aegis-ugztez). One field makes the next
        # regression measurable from the ledger alone.
        age = _own_comment_age_s(existing, me)
        detail = f"since {since}"
        if age is not None:
            detail += f" (own comment {age}s old, bound {CHECKPOINT_MAX_AGE_S}s)"
        print(f"precompact: {me} already checkpointed {bead} since {since}",
              file=sys.stderr)
        _outcome(log, session_id, me, bead, OUTCOME_SKIPPED, detail)
        return 0

    body = checkpoint_body(me, bead, depth, trigger, transcript_tail(records))
    try:
        from .br import append_comment
        append_comment(trk, bead, body)
        print(f"precompact: checkpoint written to {bead}", file=sys.stderr)
        _outcome(log, session_id, me, bead, OUTCOME_WRITTEN)
    except Exception as e:
        print(f"precompact: checkpoint NOT written to {bead} ({e})", file=sys.stderr)
        _outcome(log, session_id, me, bead, OUTCOME_WRITE_FAILED, str(e))
    return 0


if __name__ == "__main__":         # pragma: no cover - entry point
    raise SystemExit(main())
