"""SessionStart hook: inject the QUERY-FIRST directive (aegis-rcyd adoption fix).

quipu is written-to by hooks far more than it is QUERIED for context by agents
(aegis-rcyd measured reality: 152k facts, but agents grep beads instead of
asking the graph). A CLAUDE.md line does not move that — the fleet already
mandates bobbin/hank before edits and adoption is ~0, so "requested" is proven
insufficient. This puts the directive in the model's LIVE context at every
session start, the same mechanism as the bobbin-first hint. It is static,
best-effort, and fail-open: it prints a reminder plus a copy-runnable one-liner
and never blocks the session.

Emitted as a SessionStart command hook by runtime._query_first_cmd:
    <interpreter> -m shantytown.query_first

$QUIPU_SERVER is carried into the agent env (runtime._CARRIED_ENV), so the
one-liner below resolves in the agent's OWN shell — no host is hardcoded here.
"""
from __future__ import annotations

DIRECTIVE = (
    "\U0001F50E QUERY-FIRST (knowledge graph): before you start a task, ask "
    "quipu what is already known about what you're about to touch — prior "
    "incidents, decided facts, provenance — instead of re-deriving it or "
    "grepping beads. It is a live query, not a doc:\n"
    "  curl -s \"$QUIPU_SERVER/ask\" -H 'content-type: application/json' "
    "-d '{\"question\":\"what do we know about <subject>?\"}'\n"
    "  (also: /search for entities, /query for SPARQL.)\n"
    "Query-first, not grep-first: the graph already knows more than you can grep."
)


def main() -> int:
    # stdout of a SessionStart hook is injected as session context.
    print(DIRECTIVE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
