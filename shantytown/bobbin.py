"""bobbin — the first-class context adapter, and the `none` that proves it optional.

    "given what an agent is doing, what code should it be looking at?
     Read-only, synchronous, best-effort."          — docs/adapters.md:89
    "i want first class support for bobbin and quipu as well."  — Stiwi, :84

ONE method. See protocols.Context for why it stays one.

Deliberately shells out to the `bobbin` CLI rather than speaking HTTP itself —
the same call the crew hooks already make, and the same reasoning as beads.py:
let the tool own its transport, make one call, don't grow a second client to
maintain. It also means we inherit bobbin's OWN honesty about failure, which
turns out to be the whole ballgame (below).

WHY THIS ADAPTER IS MOSTLY ABOUT EXIT CODES
Measured against the live server, 2026-07-16:

    reachable + hits    exit 0   {"count": 2,  "results": [...]}
    reachable + NO hits exit 0   {"count": 0,  "results": []}
    UNREACHABLE         exit 1   "Error: Failed to connect to bobbin server"

bobbin does not lie: it distinguishes "nothing matched" from "I could not ask".
That distinction is load-bearing and easy to throw away — a naive adapter does
`json.load(out).get("results", [])`, gets [] for both, and reports a cheerful
empty result while the service is down. That is precisely the shape this repo
keeps finding (a 429 read as "metric absent" → 32 fake findings), so this file's
real job is to CARRY bobbin's honesty out to the caller rather than flatten it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from .protocols import ContextUnavailable, Snippet


class BobbinContext:
    """Context via the `bobbin` CLI. Read-only; nothing here writes."""

    def __init__(self, server: str | None = None, repo: str | None = None,
                 mode: str = "hybrid", timeout: int = 20):
        # BOBBIN_SERVER is the variable the crew hooks already use.
        self.server = server or os.environ.get("BOBBIN_SERVER") or "http://search.svc"
        self.repo = repo
        self.mode = mode
        self.timeout = timeout

    def relevant(self, query: str, budget: int = 5) -> list[Snippet]:
        """Places to look, best-effort.

        Returns [] ONLY when bobbin answered and had nothing — a real finding.
        Raises ContextUnavailable when we could not get an answer at all:
        binary missing, connection refused, timeout, or a reply we cannot parse.
        Never silently empty. Never a partial list presented as complete.
        """
        if not query.strip():
            # A precondition failure, not a backend failure: refuse (exit 1),
            # do not claim the graph is empty.
            raise ValueError("empty query")

        if shutil.which("bobbin") is None:
            raise ContextUnavailable("bobbin CLI not on PATH — cannot look")

        cmd = ["bobbin", "search", query, "--limit", str(budget),
               "--mode", self.mode, "--json"]
        if self.repo:
            cmd += ["--repo", self.repo]

        env = dict(os.environ, BOBBIN_SERVER=self.server)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired as e:
            raise ContextUnavailable(
                f"bobbin timed out after {self.timeout}s — could not look") from e
        except OSError as e:
            raise ContextUnavailable(f"could not run bobbin: {e}") from e

        if r.returncode != 0:
            # bobbin's own words, kept verbatim. The operator needs to know
            # WHICH failure this was; "context unavailable" alone is a shrug.
            detail = (r.stderr or r.stdout or "").strip().splitlines()
            first = detail[0] if detail else f"exit {r.returncode}"
            raise ContextUnavailable(f"bobbin could not answer: {first}")

        try:
            payload = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            # Exit 0 with unparseable output is still "I could not tell".
            # Guessing [] here would be inventing an answer.
            raise ContextUnavailable(f"bobbin returned unparseable output: {e}") from e

        results = payload.get("results")
        if results is None:
            raise ContextUnavailable("bobbin reply had no 'results' key")

        return [
            Snippet(
                path=str(hit.get("file_path", "")),
                lines=self._lines(hit),
                score=float(hit.get("score") or 0.0),
                repo=str(hit.get("repo", "")),
                name=str(hit.get("name", "")),
            )
            for hit in results
        ]

    @staticmethod
    def _lines(hit: dict) -> str:
        a, b = hit.get("start_line"), hit.get("end_line")
        return f"{a}-{b}" if a and b else (str(a) if a else "")


class NoContext:
    """The none-adapter. THE LEAK DETECTOR, not charity.

    docs/adapters.md:29 — "none-adapter (returns nothing, harness still works)".
    If the harness cannot run on this, bobbin has leaked into the core and the
    interface was decorative.

    Returns [] and means it: there is no context configured, so there is nothing
    to look at. That is an ANSWER, not a failure — which is why this returns
    rather than raises, and why it maps to exit 0. A `none` adapter and a
    downed bobbin are the same bytes and opposite facts; this class is the
    "same bytes" half, and ContextUnavailable is what keeps them apart.
    """

    def relevant(self, query: str, budget: int = 5) -> list[Snippet]:
        return []
