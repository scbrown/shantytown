"""entity_suggest — a SUGGESTED graph node for work that names none (aegis-4hhqoe.12).

`graph_adoption` measures whether a dispatch carries an exact existing quipu node.
Measured over 7 days: 1 of 344 dispatches and cycles carried a VERIFIED node. Most
callers name nothing because finding the right node means a search and a judgment
call nobody has time for mid-dispatch. This module makes that call for them.
camayoc's `entity_link.py` runs quipu `/search`, then one Jev choice with a
none-of-these option, and returns the entity the item ACTS ON, or nothing.

Three rules, each one a reason the suggestion can be trusted to stay a suggestion:

1. **It never becomes the agent's claim.** The suggestion is printed, written to
   the ledger as `suggested` and shown in the resume brief AS a suggestion. It is
   never added to the dispatch's `quipu_nodes`, which stay what a person or agent
   asserted. A node only becomes cited when someone passes it to `--quipu-node`,
   and that act is what the accept/reject read in `graph_adoption` counts.
2. **The written link is an inference.** With a token present it is recorded as
   `<item> aegis:about <entity>`, `sourceKind inferred`, in camayoc's inferred
   quarantine plane. Nothing here promotes it.
3. **It can never take a dispatch down.** A missing camayoc checkout, a missing
   Jev key, a timeout or a crash are all "no suggestion", reported in one line.
   The work reaching the agent matters more than the hint about it.

Gate: blind independent rating on held-out items, 19/20 (strict 15/20); coverage
76.8% (the rest is none-of-these). Evidence is on aegis-4hhqoe.12.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

MODE_ENV = "SHANTY_ENTITY_LINK"
OFF, SUGGEST, WRITE = "off", "suggest", "write"
SOURCE_ENV = "CAMAYOC_SOURCE"
DEFAULT_SOURCE = "~/.local/share/camayoc-src/camayoc"
#: One search plus one Jev choice took ~6 s per item in the evaluation, and the
#: linker retries once on a 30 s Jev timeout. This bound is the whole cost a
#: dispatch can pay for a hint.
TIMEOUT_S = 75


@dataclass(frozen=True)
class Suggestion:
    node: str = ""          # the local name, pasteable into --quipu-node
    confidence: float | None = None
    written: bool = False   # recorded in the inferred plane
    note: str = ""          # why there is no node, when there is none

    def render(self) -> str:
        if self.node:
            conf = "" if self.confidence is None else f", confidence {self.confidence:.2f}"
            where = "; recorded as an inferred link" if self.written else ""
            return (f"suggested graph context: {self.node} (Jev{conf}{where}). "
                    f"If it is right, pass --quipu-node {self.node}")
        return f"no graph-context suggestion: {self.note}"


def mode(root=None) -> str:
    """Env wins, then [env] in the deployment config, then WRITE."""
    env = (os.environ.get(MODE_ENV) or "").strip().lower()
    if env in (OFF, SUGGEST, WRITE):
        return env
    if root:
        try:
            from . import config as config_mod
            cfg, _ = config_mod.load_or_default(Path(root))
            value = str((cfg.env or {}).get(MODE_ENV, "")).strip().lower()
            if value in (OFF, SUGGEST, WRITE):
                return value
        except Exception:  # noqa: BLE001 — an unreadable config must not break dispatch
            pass
    return WRITE


def local_name(iri: str) -> str:
    """`aegis:foo` or `<ns>/foo` -> `foo`: the form `--quipu-node` takes."""
    if iri.startswith("aegis:"):
        return iri.split(":", 1)[1]
    return iri.rstrip("/").rsplit("/", 1)[-1]


def suggest(root, item: str, *, run=subprocess.run) -> Suggestion | None:
    """The suggestion for `item`, or None when suggestions are off or there is no
    item. Never raises."""
    if not item or item == "-" or mode(root) == OFF:
        return None
    script = Path(os.path.expanduser(
        os.environ.get(SOURCE_ENV) or DEFAULT_SOURCE)) / "scripts" / "entity_link.py"
    if not script.is_file():
        return Suggestion(note=f"camayoc entity_link not found at {script}")
    env = dict(os.environ)
    try:
        from .quipu import _token_from_file, resolve_server
        env["QUIPU_SERVER"] = resolve_server(None, root)
        token = env.get("QUIPU_AUTH_TOKEN") or _token_from_file()
    except Exception:  # noqa: BLE001
        token = env.get("QUIPU_AUTH_TOKEN", "")
    argv = ["python3", str(script), item]
    writing = mode(root) == WRITE and bool(token)
    if writing:
        env["QUIPU_AUTH_TOKEN"] = token
        argv += ["--write", "--timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())]
    try:
        out = run(argv, env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return Suggestion(note=f"entity_link timed out after {TIMEOUT_S}s")
    except OSError as e:
        return Suggestion(note=f"entity_link did not run: {e}")
    if out.returncode != 0:
        tail = (out.stderr or "").strip().splitlines()[-1:] or ["no stderr"]
        return Suggestion(note=f"entity_link exit {out.returncode}: {tail[0][:160]}")
    try:
        res = json.loads((out.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Suggestion(note="entity_link printed no result")
    if res.get("error"):
        return Suggestion(note=f"entity_link error: {str(res['error'])[:160]}")
    if not res.get("choice"):
        return Suggestion(note="none of the retrieved entities is this work's subject")
    write = res.get("write") or {}
    return Suggestion(node=local_name(res["choice"]), confidence=res.get("confidence"),
                      written=bool(writing and write.get("conforms")))
