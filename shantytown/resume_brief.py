"""resume_brief — what the NEXT session is handed, and the one place it is worded.

Stiwi, 2026-09-21 direct:

    "cycle needs to work a lot better in st. it needs to be seamless; it kicked
     me out of claude code and i had to reattach. it should probably use native
     /clear or something with an injection of whats expected for hte next
     sessions with the docs and quipu node etc"

THE GAP THIS CLOSES. `st agent cycle` already collected everything the next
session needs — a checkpoint line, a checkpoint bead, the quipu nodes the
requesting agent named (aegis-x6yoq, aegis-rcyd.1), the plate item — and then
delivered NONE of it into the new context. It went onto the stop record, onto a
bead comment and into a re-dispatch note, all of which the fresh session has to
go LOOKING for, at exactly the moment it has just been emptied of the knowledge
that it should look. Whether the agent found its own handoff was a matter of
whether it thought to run `st anchor` before doing something else.

So the brief is a FIRST-CLASS ARTIFACT with two ends that cannot drift:

  WRITE   the cycle writes it before it clears anything, from what the cycle
          already knows. If the clear never happens, an unconsumed brief is
          inert — it is read at SessionStart and nowhere else.
  READ    a SessionStart hook prints it. Claude Code injects a SessionStart
          hook's stdout into the model's live context (documented for sources
          `startup`, `resume`, `clear`, `compact`, `fork`), so the brief lands
          in the first context window of the new session WITHOUT the agent
          having to ask for it. That is the "injection" the directive names,
          and it is why the soft clear can be seamless at all: the mechanism
          that empties the context is the same one that refills the part worth
          keeping.

DELIVERED EXACTLY ONCE, and that is a correctness property rather than tidiness.
A brief left on disk is re-injected at every subsequent SessionStart — including
the plain restarts that have nothing to do with a cycle — so an agent three
sessions later would be told to resume a task it finished two sessions ago, in
the authoritative voice of its own harness. Consumed on read (read-then-delete,
atomically enough for a single reader) so the SECOND session start after a cycle
gets nothing, which is the correct amount.

FAIL-OPEN, ALWAYS. Every path in the hook returns 0 and prints nothing it is not
sure of. A hook that can refuse a session start is a hook that can take an agent
off the fleet, and this one runs at the only moment an agent cannot recover by
itself.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


# The marker that makes an injected brief recognisable IN THE TRANSCRIPT — to the
# agent reading it, to a human reading the pane, and to a test. Named once,
# because a banner written under one spelling and searched under another is a
# handoff that arrived and cannot be proved to have arrived.
BANNER = "[st cycle resume brief]"

# How long a brief may sit unconsumed before the hook drops it unread. A cycle
# delivers its brief within seconds; anything still here a day later belongs to a
# cycle that never completed, and injecting it into an unrelated session start is
# worse than losing it. Dropped SILENTLY on the model's side and reported only on
# stderr: the fresh session cannot act on a stale handoff, so telling it about one
# only spends context on a thing it must ignore.
STALE_AFTER_S = 24 * 60 * 60


def compose(agent: str, checkpoint: str = "", checkpoint_bead: str = "",
            quipu_nodes=(), item: str = "", docs=()) -> str:
    """The brief itself. Pure text from facts the cycle already holds.

    ORDER IS THE POINT. The first line says what just happened, because a session
    that does not know it was cycled reads everything below it as a new
    assignment. Then the checkpoint (what I was mid-way through), then the
    pointers (where the rest of it is written down). A fresh session reads top to
    bottom and stops reading early, so the irreplaceable thing goes first: the
    checkpoint exists nowhere else in that context, while every pointer below it
    can be re-derived from the board.

    POINTERS, NOT CONTENTS. The bead body, the graph nodes and the docs are NOT
    inlined. They are addresses, and the session is told to open them. Inlining
    would rebuild a large context at the exact moment the point of the exercise
    was to have a small one — and it would be a SNAPSHOT, stale the moment
    anything moved, competing with the live source it was copied from.
    """
    lines = [f"{BANNER} Your context was just cleared by `st agent cycle`. The "
             f"runtime, your tools and your permissions are unchanged; only the "
             f"conversation is gone. You are still {agent}."]
    if checkpoint:
        lines.append("")
        lines.append("WHERE YOU WERE (your own checkpoint, written just before "
                     "the clear):")
        lines.append(f"  {checkpoint}")
    pointers = []
    if checkpoint_bead:
        pointers.append(f"  checkpoint bead: {checkpoint_bead} — `br show "
                        f"{checkpoint_bead}`. The full notes are a comment on it.")
    if item:
        pointers.append(f"  on your plate: {item} — it was re-dispatched to you "
                        f"as part of the cycle.")
    for node in quipu_nodes:
        pointers.append(f"  graph context: {node} — ask quipu about it before "
                        f"you re-derive anything.")
    for doc in docs:
        pointers.append(f"  docs: {doc}")
    if pointers:
        lines.append("")
        lines.append("WHERE THE REST OF IT IS WRITTEN DOWN:")
        lines.extend(pointers)
    lines.append("")
    lines.append("START BY READING THE POINTERS ABOVE, not by re-deriving what "
                 "they already hold. Then `st anchor` to confirm the work on "
                 "your hook. Do NOT re-do work the checkpoint says is done.")
    return "\n".join(lines)


class Briefs:
    """Per-agent pending resume briefs, one small JSON file each.

    A DIRECTORY OF FILES, not one shared map, and deliberately so: the writer is
    `st agent cycle` on the host and the reader is a hook inside the agent's own
    process, running concurrently with every other agent's hook. A shared file
    would make two unrelated agents' cycles contend, and the failure of that
    contention is a lost handoff — the one payload in this system that cannot be
    reconstructed after the fact.
    """

    def __init__(self, root):
        self.root = Path(root) / "notify" / "resume"

    def _path(self, agent: str) -> Path:
        # Basename only. An agent name is registry-controlled, but this path is
        # also composed from $SHANTY_AGENT inside the hook, which is environment
        # the host does not re-validate at read time.
        return self.root / f"{Path(str(agent)).name}.json"

    def put(self, agent: str, text: str, **facts) -> Path:
        """Record the brief for `agent`. REPLACES any pending one.

        Replace rather than append: a second cycle's brief describes a later
        state than the first, and handing a fresh session two handoffs leaves it
        to guess which one is current. The newer one is always the answer.
        """
        from .files import write_json_atomic
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(agent)
        write_json_atomic(path, {"agent": agent, "at": time.time(),
                                 "text": text, **facts})
        return path

    def peek(self, agent: str) -> dict | None:
        """Read WITHOUT consuming — for `--dry-run` and for tests. Never raises."""
        try:
            data = json.loads(self._path(agent).read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def take(self, agent: str) -> dict | None:
        """Read AND consume. The delete happens even if the caller then fails to
        print — see the module docstring: a brief that survives its own delivery
        is re-injected into every later session start, which is a worse failure
        than one that is lost. Never raises."""
        data = self.peek(agent)
        try:
            self._path(agent).unlink()
        except OSError:
            pass
        return data

    def drop(self, agent: str) -> None:
        """Forget a pending brief without delivering it. Used when a cycle
        REFUSES after the brief was written, so the next ordinary session start
        is not told it was cycled when it was not."""
        try:
            self._path(agent).unlink()
        except OSError:
            pass


def main(argv=None) -> int:
    """The SessionStart hook. `<interpreter> -m shantytown.resume_brief`.

    Prints the pending brief for $SHANTY_AGENT, or nothing. stdout of a
    SessionStart hook is added to the model's context, so "prints nothing" is the
    correct and common case — this hook runs at EVERY session start and most of
    them are not cycles.

    Returns 0 on every path, including the ones that found nothing and the ones
    that failed. Argued in the module docstring; restated here because this is
    the function somebody will later be tempted to make strict.
    """
    root = ""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--root" in args:
        try:
            root = args[args.index("--root") + 1]
        except IndexError:
            root = ""
    root = root or os.environ.get("SHANTY_ROOT", "")
    agent = os.environ.get("SHANTY_AGENT", "")
    if not root or not agent:
        # Not an error and not reported: a session started outside st has neither,
        # and a hook that complains on every non-fleet session start is a hook
        # that gets removed.
        return 0
    try:
        record = Briefs(root).take(agent)
    except Exception:  # noqa: BLE001 — see FAIL-OPEN in the module docstring
        return 0
    if not record:
        return 0
    at = record.get("at")
    if isinstance(at, (int, float)) and time.time() - at > STALE_AFTER_S:
        print(f"  st: dropped a resume brief for {agent} unread — it is older "
              f"than {STALE_AFTER_S // 3600}h, so it describes a cycle that "
              f"never completed.", file=sys.stderr)
        return 0
    text = record.get("text") or ""
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
