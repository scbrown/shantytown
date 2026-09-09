"""Read the canonical tooling manifest and project its instruction block.

Opt in with SHANTY_TOOLING_MANIFEST (an entity IRI) in deployment [env].
That entity's rdf:value is one versioned JSON document. No cached or local
fallback is accepted once a deployment selects the graph as its authority.
Secrets stay in provision/secrets.env; the graph contains placeholders only.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .answer import CouldNotLook, PartialAnswer
from .deployment import deployment_default
from .quipu import QuipuRegistry, QuipuQueryRejected


class ToolingError(RuntimeError):
    """Cannot establish or realize the canonical tooling content."""


BEGIN = "<!-- shantytown:tooling begin -->"
END = "<!-- shantytown:tooling end -->"
VALUE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#value"
RECEIPT = ".shantytown-tooling.json"


@dataclass(frozen=True)
class Manifest:
    entity: str
    mcp: dict
    skills: dict[str, str]
    instructions: str

    def sources(self, workspace: Path) -> list[tuple[str, Path]]:
        return [(name, (workspace / Path(path).expanduser()).absolute())
                for name, path in sorted(self.skills.items())]


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ToolingError("duplicate key in tooling manifest")
        out[key] = value
    return out


def parse(text: str, entity: str) -> Manifest:
    try:
        data = json.loads(text, object_pairs_hook=_object)
    except (ValueError, TypeError):
        raise ToolingError("tooling manifest is not valid JSON") from None
    if (not isinstance(data, dict)
            or set(data) != {"version", "mcpServers", "skills", "instructions"}
            or type(data["version"]) is not int or data["version"] != 1):
        raise ToolingError("tooling manifest requires version 1, mcpServers, skills, instructions")
    servers, skills, instructions = data["mcpServers"], data["skills"], data["instructions"]
    if not isinstance(servers, dict) or not isinstance(skills, dict):
        raise ToolingError("mcpServers and skills must be objects")
    for name, spec in servers.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not isinstance(spec, dict):
            raise ToolingError("invalid MCP server entry")
        if not (bool(spec.get("url")) ^ bool(spec.get("command"))):
            raise ToolingError("each MCP server needs exactly one URL or command")
        if not all(isinstance(spec[k], str) for k in ("url", "command") if k in spec):
            raise ToolingError("MCP URL and command must be strings")
        headers = spec.get("headers", {})
        if not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
            raise ToolingError("MCP headers must be a string map")
        for key, value in headers.items():
            if key.lower() == "authorization" and not re.fullmatch(r"Bearer \$\{[A-Z0-9_]+\}", value):
                raise ToolingError("authorization must reference a bearer environment variable")
    for name, path in skills.items():
        if (not re.fullmatch(r"[A-Za-z0-9_-]+", name)
                or not isinstance(path, str) or not path.strip() or "\x00" in path):
            raise ToolingError("skills must map simple names to source directories")
    if (not isinstance(instructions, str) or not instructions.strip()
            or BEGIN in instructions or END in instructions):
        raise ToolingError("instructions must be nonempty text without projection markers")
    return Manifest(entity, servers, skills, instructions)


def load(root) -> Manifest | None:
    entity = deployment_default(root, "SHANTY_TOOLING_MANIFEST")
    if entity is None:
        return None
    # Only absolute IRIs, never interpolated SPARQL syntax or a CURIE whose
    # namespace can change independently of the deployment.
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*:[^<>\s\"{}|^`\\]+", entity):
        raise ToolingError("SHANTY_TOOLING_MANIFEST must be an absolute entity IRI")
    try:
        client = QuipuRegistry(root=root, client_label="shantytown-tooling")
        rows = client._query_answer(f"SELECT ?value WHERE {{ <{entity}> <{VALUE}> ?value }}").exact()
    except (CouldNotLook, PartialAnswer, QuipuQueryRejected):
        # Do not echo HTTP bodies: a misconfigured source might contain secrets.
        raise ToolingError("Quipu tooling source unavailable, rejected, or truncated") from None
    if len(rows) != 1 or not isinstance(rows[0], dict) or not isinstance(rows[0].get("value"), str):
        raise ToolingError("Quipu tooling source must have exactly one JSON rdf:value")
    return parse(rows[0]["value"], entity)


def instruction_text(before: str, instructions: str) -> str:
    """Replace only our marked block; all surrounding user instructions survive."""
    block = f"{BEGIN}\n{instructions.rstrip()}\n{END}"
    if BEGIN not in before and END not in before:
        return before + ("\n" if before.endswith("\n") else "\n\n" if before else "") + block + "\n"
    if before.count(BEGIN) != 1 or before.count(END) != 1 or before.index(END) < before.index(BEGIN):
        raise ToolingError("tooling instruction markers are ambiguous; reconcile the rulebook")
    start, end = before.index(BEGIN), before.index(END) + len(END)
    return before[:start] + block + before[end:]


def instruction_updates(ws: Path, manifest: Manifest) -> dict[Path, str]:
    if (ws / "AGENTS.override.md").exists():
        raise ToolingError("AGENTS.override.md shadows the projected tooling instructions")
    updates = {}
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = ws / name
        try:
            resolved = path.resolve(strict=path.is_symlink())
            before = resolved.read_text() if resolved.exists() else ""
        except (OSError, RuntimeError, ValueError):
            raise ToolingError("cannot read tooling instruction destination") from None
        # Reverse symlinks and a shared inode are already uniform. Never replace
        # their source with a symlink back to itself.
        updates[resolved] = instruction_text(before, manifest.instructions)
    return updates


def instruction_gaps(ws: Path, manifest: Manifest) -> list[str]:
    try:
        updates = instruction_updates(ws, manifest)
        return ["instructions(Quipu drift)"] if any(
            not p.is_file() or p.read_text() != text for p, text in updates.items()) else []
    except (ToolingError, OSError):
        return ["instructions(unreadable or shadowed)"]


def retired_links(ws: Path, manifest: Manifest) -> list[Path]:
    """Remove only links recorded by our previous projection, never personal skills."""
    path = ws / RECEIPT
    if not path.exists():
        return []
    try:
        previous = json.loads(path.read_text())
        if not isinstance(previous, dict) or set(previous) != {"skills"} or not isinstance(previous["skills"], dict):
            raise ValueError
        retired = []
        for name, target in previous["skills"].items():
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not isinstance(target, str):
                raise ValueError
            if name in manifest.skills:
                continue
            for harness in (".claude", ".agents"):
                link = ws / harness / "skills" / name
                if not link.exists() and not link.is_symlink():
                    continue
                if not link.is_symlink() or link.readlink() != Path(target):
                    raise ToolingError("retired skill link has personal changes; reconcile it")
                retired.append(link)
        return retired
    except (OSError, ValueError, TypeError):
        raise ToolingError("cannot read tooling projection receipt") from None
