"""hierarchy — WHERE the tier comes from (aegis-t4eve, st-redesign 3/4).

quipu DESCRIBES the tier (`aegis:CrewMember` + `aegis:reports_to`). This module
decides WHICH source answers `st roles sync`, and makes it SAY SO.

    --from quipu          force the graph.  Unreachable => REFUSE.
    --from file:<path>    force that file.  Unreadable  => REFUSE.
    (omitted)             ontology-first: try the graph, fall back to the file
                          ONLY if the graph cannot be read — and report that it
                          happened.

**An explicit `--from` is never silently substituted.** If you asked for the
graph and the graph is down, that is a refusal, not a fallback. A projection
sourced from a stale file is indistinguishable from one sourced from the graph
unless it says which — so the source is part of the OUTPUT, not a debug detail.
Silent substitution is exactly how a forgotten file quietly becomes the
authority over a fleet.

Role is not stored by either source. It is DERIVED from the shape of
`reports_to` by `quipu.derive_agents` for BOTH sources, so a file and a graph
describing the same hierarchy cannot disagree about who is a lead. One
derivation, not two.

Zero dependencies, because shantytown declares none (`pyproject: dependencies =
[]`):
  * `.json`        — stdlib.
  * `.yaml`/`.yml` — needs PyYAML; says so plainly when it is absent rather than
                     failing with an ImportError traceback.
  * `.ttl`         — a deliberately MINIMAL `CrewMember`/`reports_to` extractor,
                     NOT a turtle parser. It recognizes the one shape quipu
                     emits and REFUSES on a file with no CrewMember in it,
                     rather than quietly returning an empty hierarchy (an empty
                     projection is the failure mode this whole module exists to
                     prevent).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .protocols import Agent
from .quipu import QuipuRegistry, derive_agents


class HierarchyUnavailable(Exception):
    """The requested source could not be read. NOT 'the hierarchy is empty'.

    Same discipline as `QuipuUnreachable`: an errored source is not a
    zero-result. Callers must refuse, never project nothing.
    """


@dataclass(frozen=True)
class SourceInfo:
    """Which source actually answered — carried to the caller so it can be printed.

    `explicit` records whether the operator NAMED this source (`--from`) or it
    was chosen by the ontology-first default. `fallback_reason` is set only when
    the graph was tried first and could not answer.
    """
    kind: str                      # "quipu" | "file"
    detail: str                    # "quipu" | the file path
    explicit: bool = False
    fallback_reason: str | None = None

    def render(self) -> str:
        how = "named with --from" if self.explicit else "ontology-first default"
        if self.fallback_reason:
            return (f"source: {self.detail} ({self.kind}) "
                    f"— FELL BACK from quipu: {self.fallback_reason}")
        return f"source: {self.detail} ({self.kind}, {how})"


def parse_spec(spec: str | None) -> tuple[str | None, str | None]:
    """`--from` -> (kind, path). `None` kind means "auto: ontology-first".

    Accepts exactly `quipu`, `file:<path>`. Anything else is a usage error —
    guessing at a mistyped source is how you project from the wrong one.
    """
    if spec is None or spec == "":
        return None, None
    if spec == "quipu":
        return "quipu", None
    if spec.startswith("file:"):
        path = spec[len("file:"):]
        if not path:
            raise ValueError("--from file: needs a path (e.g. file:crew.yaml)")
        return "file", path
    raise ValueError(
        f"unknown --from source: {spec!r} (want 'quipu' or 'file:<path>')")


# ── file readers: every one returns quipu's row shape [{s, rt}] ──────────────
# Returning ROWS (not Agents) is what lets `derive_agents` be the single role
# derivation for both sources.

def _rows_from_mapping(data) -> list[dict]:
    """Accept the two obvious hand-written shapes, reject anything else.

        {"ian": {"reports_to": "dearing"}, "dearing": {"reports_to": null}}
        {"ian": "dearing", "dearing": null}
        [{"name": "ian", "reports_to": "dearing"}, ...]

    `role` is deliberately IGNORED if present: role is derived, and honoring a
    hand-written role here would let a file disagree with the graph about who is
    a lead — the exact divergence this module exists to avoid.
    """
    rows: list[dict] = []
    if isinstance(data, dict):
        for name, v in data.items():
            if v is None or v == "":
                rows.append({"s": str(name)})
            elif isinstance(v, str):
                rows.append({"s": str(name), "rt": v})
            elif isinstance(v, dict):
                rt = v.get("reports_to")
                rows.append({"s": str(name)} if not rt
                            else {"s": str(name), "rt": str(rt)})
            else:
                raise HierarchyUnavailable(
                    f"unrecognized entry for {name!r}: {v!r}")
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict) or "name" not in item:
                raise HierarchyUnavailable(
                    f"list entries need a 'name' key, got: {item!r}")
            rt = item.get("reports_to")
            rows.append({"s": str(item["name"])} if not rt
                        else {"s": str(item["name"]), "rt": str(rt)})
    else:
        raise HierarchyUnavailable(
            f"want a mapping or a list of entries, got {type(data).__name__}")
    return rows


_TTL_STMT = re.compile(r"\.\s*(?:\n|$)")
_TTL_CREWMEMBER = re.compile(r"\b(?:a|rdf:type)\b[^;]*?\bCrewMember\b")
_TTL_REPORTS_TO = re.compile(r"\breports_to\b\s+(\S+)")


def _ttl_local(term: str) -> str:
    """`aegis:ian` / `<http://…/ian>` / `"ian"` -> `ian`."""
    t = term.strip().strip(";.").strip()
    t = t.strip("<>").strip('"')
    if "/" in t:
        t = t.rsplit("/", 1)[-1]
    if ":" in t:
        t = t.rsplit(":", 1)[-1]
    return t


def _rows_from_ttl(text: str) -> list[dict]:
    """MINIMAL extractor for the one shape quipu emits — not a turtle parser.

        aegis:ian a aegis:CrewMember ;
            aegis:reports_to aegis:dearing .

    Statement-oriented: split on a `.` that ends a line, take the first token as
    the subject, then look for the CrewMember type and a reports_to object among
    its `;`-separated predicates. Anything it does not recognize is IGNORED
    rather than guessed at — but a file with NO CrewMember at all raises, because
    silently returning an empty hierarchy is the failure this module prevents.
    """
    rows: list[dict] = []
    body = "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith(("#", "@prefix", "@base",
                                                    "PREFIX", "BASE")))
    for stmt in _TTL_STMT.split(body):
        stmt = stmt.strip()
        if not stmt:
            continue
        if not _TTL_CREWMEMBER.search(stmt):
            continue
        subject = _ttl_local(stmt.split()[0])
        if not subject:
            continue
        m = _TTL_REPORTS_TO.search(stmt)
        if m:
            rows.append({"s": subject, "rt": _ttl_local(m.group(1))})
        else:
            rows.append({"s": subject})
    if not rows:
        raise HierarchyUnavailable(
            "no aegis:CrewMember found — this does not look like a hierarchy "
            "file (refusing rather than projecting an empty crew)")
    return rows


def load_file_rows(path: str | Path) -> list[dict]:
    """Read a hierarchy file into quipu's row shape. Dispatch on suffix."""
    p = Path(path)
    if not p.is_file():
        raise HierarchyUnavailable(f"no such hierarchy file: {p}")
    try:
        text = p.read_text()
    except OSError as e:
        raise HierarchyUnavailable(f"cannot read {p}: {e}") from e

    suffix = p.suffix.lower()
    if suffix == ".ttl":
        return _rows_from_ttl(text)
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # optional: shantytown declares no dependencies
        except ImportError as e:
            raise HierarchyUnavailable(
                f"{p} is YAML but PyYAML is not installed. Use a .json "
                "hierarchy file, or install PyYAML.") from e
        try:
            return _rows_from_mapping(yaml.safe_load(text))
        except HierarchyUnavailable:
            raise
        except Exception as e:
            raise HierarchyUnavailable(f"cannot parse {p}: {e}") from e
    if suffix == ".json":
        try:
            return _rows_from_mapping(json.loads(text))
        except HierarchyUnavailable:
            raise
        except Exception as e:
            raise HierarchyUnavailable(f"cannot parse {p}: {e}") from e
    raise HierarchyUnavailable(
        f"unsupported hierarchy file type {suffix!r} for {p} "
        "(want .ttl, .yaml, .yml or .json)")


class FileHierarchy:
    """Registry-shaped, READ-ONLY view of a hierarchy file.

    Deliberately not a `MutableRegistry`: a file is a description you sync FROM,
    never a place `roles sync` writes back to. The cards are the projection.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def all(self) -> list[Agent]:
        return derive_agents(load_file_rows(self.path))

    def get(self, name: str) -> Agent:
        for a in self.all():
            if a.name == name:
                return a
        raise LookupError(f"no such agent in {self.path}: {name}")


_DEFAULT_NAMES = ("hierarchy.json", "hierarchy.yaml", "hierarchy.yml",
                  "hierarchy.ttl")


def default_file(root: str | Path | None) -> Path | None:
    """The fallback file for the ontology-first default, or None if there isn't one.

    `SHANTY_HIERARCHY_FILE` wins (an operator naming it explicitly), else the
    first `hierarchy.*` beside the crew root. Returns None rather than inventing
    a path, so `resolve` can refuse with "there is no fallback configured"
    instead of "no such file" — the operator needs to know WHICH of those is true.
    """
    import os
    env = os.environ.get("SHANTY_HIERARCHY_FILE")
    if env:
        return Path(env)
    if root is None:
        return None
    for name in _DEFAULT_NAMES:
        p = Path(root) / name
        if p.is_file():
            return p
    return None


def resolve(spec: str | None, *, file_default: str | Path | None = None,
            quipu_factory=QuipuRegistry, file_factory=FileHierarchy):
    """Pick the source. Returns `(source, SourceInfo)`; raises on refusal.

    The factories are injected so this is testable without a live graph or a
    real file — the same reason `derive_agents` is a pure function.
    """
    kind, path = parse_spec(spec)

    if kind == "quipu":
        src = quipu_factory()
        src.all()  # probe now: an explicit --from quipu must fail loudly here
        return src, SourceInfo("quipu", "quipu", explicit=True)

    if kind == "file":
        src = file_factory(path)
        src.all()  # probe now, same reason
        return src, SourceInfo("file", str(path), explicit=True)

    # AUTO: ontology-first, file-fallback.
    try:
        src = quipu_factory()
        src.all()
        return src, SourceInfo("quipu", "quipu")
    except Exception as e:
        if file_default is None:
            raise HierarchyUnavailable(
                f"quipu could not be read ({e}) and there is no fallback "
                "hierarchy file configured — refusing to project nothing. "
                "Pass --from file:<path>.") from e
        why = f"{type(e).__name__}: {e}"
        src = file_factory(file_default)
        try:
            src.all()
        except Exception as fe:
            raise HierarchyUnavailable(
                f"quipu could not be read ({e}) AND the fallback "
                f"{file_default} could not be read ({fe})") from fe
        return src, SourceInfo("file", str(file_default), fallback_reason=why)
