"""Declared HOOK BUNDLES: other tools register hooks, st renders and keeps them (aegis-68j0ys).

WHY THIS EXISTS. st emits each role's harness settings from code, and
`merge_one_level` gives the emitted `hooks` key precedence over whatever was on
disk. That is right for st's own hooks and it is also exactly how a tool's
hand-installed hooks were silently BLOWN AWAY when crew settings moved to st's
generator: nothing declared them, so nothing re-emitted them.

A bundle is a JSON file a tool drops into `<root>/hook-bundles/<name>.json`,
normally through `st ops hooks register`. The settings generators read the
registry on EVERY emit and append each bundle's hooks after st's own, so a
registered hook survives any number of regenerations. That is the "never blown
away" guarantee, and it holds by construction rather than by a merge rule.

ST KNOWS NOTHING ABOUT WHAT IS IN A BUNDLE. No tool names, no commands, no
defaults. The installing tool owns WHAT (its own hook definitions, output
budgets, fail-open suffix); st owns keeping it RENDERED and CHECKING that it is.
tests/test_hook_bundles.py asserts this module and its callers name no tool.

Contract: schema "st.hook-bundle/1", recorded on aegis-68j0ys.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = "st.hook-bundle/1"
REGISTRY_DIR = "hook-bundles"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
HARNESSES = ("claude", "codex")

#: The hook events each harness is KNOWN to run. Narrow on purpose: an event we
#: have not seen a harness honour is reported as unsupported rather than written
#: into a file where it would do nothing and look like coverage. Widen by evidence.
#: codex: the events st already emits into codex config.toml and has observed.
SUPPORTED_EVENTS: dict[str, frozenset[str]] = {
    "claude": frozenset({"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                         "PostToolUseFailure", "Stop", "SessionEnd", "PreCompact",
                         "Notification", "SubagentStop"}),
    "codex": frozenset({"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}),
}


@dataclass(frozen=True)
class BundleHook:
    event: str
    command: str
    matcher: str | None = None
    timeout: int | None = None
    harnesses: tuple[str, ...] = HARNESSES

    def group(self) -> dict[str, Any]:
        """The matcher group this hook renders to, in the harness's own shape."""
        hook: dict[str, Any] = {"type": "command", "command": self.command}
        if self.timeout is not None:
            hook["timeout"] = self.timeout
        grp: dict[str, Any] = {"hooks": [hook]}
        if self.matcher is not None:
            grp["matcher"] = self.matcher
        return grp


@dataclass(frozen=True)
class Bundle:
    name: str
    version: str
    owner: str
    roles: tuple[str, ...]
    hooks: tuple[BundleHook, ...]
    source: str = ""
    #: codex's `notify` argv. A single-slot harness key, not a hook event, so at
    #: most ONE registered bundle may own it (see notify_owner).
    codex_notify: tuple[str, ...] | None = None

    def applies_to(self, role: str) -> bool:
        return "*" in self.roles or role in self.roles


@dataclass
class Registry:
    bundles: list[Bundle] = field(default_factory=list)
    #: (file, reason) for every registry file that could not be used. Reported by
    #: `check`, never raised: one bad drop-in must not stop every agent's settings
    #: from being written.
    errors: list[tuple[str, str]] = field(default_factory=list)


def registry_dir(root) -> Path:
    return Path(root) / REGISTRY_DIR


def validate(obj: Any) -> list[str]:
    """Every reason `obj` is not a valid bundle. Empty means valid."""
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["bundle must be a JSON object"]
    if obj.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA!r}, got {obj.get('schema')!r}")
    name = obj.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errs.append(f"name must match {NAME_RE.pattern}, got {name!r}")
    for key in ("version", "owner"):
        if not isinstance(obj.get(key), str) or not obj.get(key):
            errs.append(f"{key} must be a non-empty string")
    roles = obj.get("roles")
    if (not isinstance(roles, list) or not roles
            or not all(isinstance(r, str) and r for r in roles)):
        errs.append('roles must be a non-empty list of role names or ["*"]')
    cn = obj.get("codex_notify")
    if cn is not None and (not isinstance(cn, list) or not cn
                           or not all(isinstance(x, str) and x for x in cn)):
        errs.append("codex_notify must be a non-empty list of strings (an argv)")
    hooks = obj.get("hooks")
    if not isinstance(hooks, list) or (not hooks and cn is None):
        errs.append("hooks must be a list, non-empty unless the bundle declares codex_notify")
        return errs
    for i, h in enumerate(hooks):
        where = f"hooks[{i}]"
        if not isinstance(h, dict):
            errs.append(f"{where} must be an object")
            continue
        if not isinstance(h.get("event"), str) or not h.get("event"):
            errs.append(f"{where}.event must be a non-empty string")
        if not isinstance(h.get("command"), str) or not h.get("command", "").strip():
            errs.append(f"{where}.command must be a non-empty string")
        if "matcher" in h and h["matcher"] is not None and not isinstance(h["matcher"], str):
            errs.append(f"{where}.matcher must be a string or null")
        if "timeout" in h and (not isinstance(h["timeout"], int) or isinstance(h["timeout"], bool)
                               or h["timeout"] <= 0):
            errs.append(f"{where}.timeout must be a positive integer")
        hs = h.get("harnesses", list(HARNESSES))
        if (not isinstance(hs, list) or not hs
                or any(x not in HARNESSES for x in hs)):
            errs.append(f"{where}.harnesses must be a non-empty subset of {list(HARNESSES)}")
    return errs


def parse(obj: dict, source: str = "") -> Bundle:
    """A validated dict -> Bundle. Call validate() first."""
    return Bundle(
        name=obj["name"], version=obj["version"], owner=obj["owner"],
        roles=tuple(obj["roles"]), source=source,
        codex_notify=tuple(obj["codex_notify"]) if obj.get("codex_notify") else None,
        hooks=tuple(BundleHook(event=h["event"], command=h["command"],
                               matcher=h.get("matcher"), timeout=h.get("timeout"),
                               harnesses=tuple(h.get("harnesses", HARNESSES)))
                    for h in obj["hooks"]))


def load(root) -> Registry:
    """Every bundle in the registry, sorted by name so rendering is deterministic."""
    reg = Registry()
    if root is None:
        return reg
    d = registry_dir(root)
    if not d.is_dir():
        return reg
    for f in sorted(d.glob("*.json")):
        try:
            obj = json.loads(f.read_text())
        except (OSError, ValueError) as e:
            reg.errors.append((f.name, f"unreadable: {e}"))
            continue
        errs = validate(obj)
        if errs:
            reg.errors.append((f.name, "; ".join(errs)))
            continue
        if f.stem != obj["name"]:
            reg.errors.append((f.name, f"file name must be {obj['name']}.json"))
            continue
        reg.bundles.append(parse(obj, source=str(f)))
    reg.bundles.sort(key=lambda b: b.name)
    return reg


@dataclass(frozen=True)
class Unsupported:
    bundle: str
    event: str
    harness: str

    def __str__(self) -> str:
        return (f"bundle {self.bundle}: event {self.event} is not supported by {self.harness} "
                f"(not rendered; drop {self.harness} from that hook's harnesses to accept this)")


def groups_for(reg: Registry, role: str, harness: str) -> tuple[dict[str, list[dict]], list[Unsupported]]:
    """{event: [matcher groups]} this role must carry in this harness, plus every
    declared hook that could not be rendered there."""
    out: dict[str, list[dict]] = {}
    unsupported: list[Unsupported] = []
    for b in reg.bundles:
        if not b.applies_to(role):
            continue
        for h in b.hooks:
            if harness not in h.harnesses:
                continue
            if h.event not in SUPPORTED_EVENTS.get(harness, frozenset()):
                unsupported.append(Unsupported(b.name, h.event, harness))
                continue
            out.setdefault(h.event, []).append(h.group())
    return out, unsupported


def notify_owner(reg: Registry, role: str) -> tuple[Bundle | None, list[str]]:
    """The ONE bundle that owns codex `notify` for this role, and the names of
    every claimant. More than one claimant means NOBODY owns it: st will not pick
    a winner for a single slot, and check reports the conflict."""
    claimants = [b for b in reg.bundles if b.codex_notify and b.applies_to(role)]
    names = [b.name for b in claimants]
    return (claimants[0] if len(claimants) == 1 else None), names


def apply(settings: dict, role: str, harness: str, root) -> dict:
    """Return `settings` with every registered bundle hook for (role, harness)
    appended after st's own hooks. `settings` is not mutated.

    A registry that cannot be read contributes nothing: settings for every agent
    are still written, and `st ops hooks check` reports the broken drop-in.
    """
    try:
        reg = load(root)
    except Exception:  # never let a bad drop-in stop settings being written
        return settings
    extra, _unsupported = groups_for(reg, role, harness)
    owner = notify_owner(reg, role)[0] if harness == "codex" else None
    if not extra and owner is None:
        return settings
    out = dict(settings)
    if owner is not None:
        out["notify"] = list(owner.codex_notify)
    hooks = {k: list(v) for k, v in (settings.get("hooks") or {}).items()}
    for event, groups in extra.items():
        hooks[event] = hooks.get(event, []) + groups
    out["hooks"] = hooks
    return out


def _commands_in(groups: Any) -> set[tuple[str | None, str]]:
    found: set[tuple[str | None, str]] = set()
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        for h in g.get("hooks") or []:
            if isinstance(h, dict) and isinstance(h.get("command"), str):
                found.add((g.get("matcher"), h["command"]))
    return found


NOT_CHECKED = "not-checked"


@dataclass
class CheckResult:
    """Schema "st.hook-check/1" (fixed on aegis-68j0ys for the G4 monitor).

    v1 fills `configured`; `live` and `firing` are NOT_CHECKED, which is never ok
    and never counted as a verdict."""
    items: list[dict] = field(default_factory=list)
    registry_errors: list[dict] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.registry_errors:
            return 1
        failing = {"configured": {"missing", "unsupported"}, "live": {"stale"}, "firing": {"silent"}}
        unknown = {"configured": {"unreadable"}, "live": {"unknown"}, "firing": {"unknown"}}
        if any(i[k] in v for i in self.items for k, v in failing.items()):
            return 1
        if any(i[k] in v for i in self.items for k, v in unknown.items()):
            return 2
        return 0

    def summary(self) -> dict[str, int]:
        def n(key, val):
            return sum(1 for i in self.items if i[key] == val)
        return {"items": len(self.items),
                "configured_ok": n("configured", "ok"), "missing": n("configured", "missing"),
                "unsupported": n("configured", "unsupported"),
                "live_ok": n("live", "ok"), "live_stale": n("live", "stale"),
                "firing_ok": n("firing", "ok"), "firing_silent": n("firing", "silent")}

    def to_json(self, root=None, host=None) -> dict:
        from datetime import datetime, timezone
        return {"schema": "st.hook-check/1", "host": host,
                "root": str(root) if root is not None else None,
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "exit": self.exit_code, "summary": self.summary(),
                "registry_errors": self.registry_errors, "items": self.items}


def emitted_role_files(root) -> list[tuple[str, str, Path]]:
    """(harness, role, path) for every ROLE settings file st has emitted."""
    out: list[tuple[str, str, Path]] = []
    s = Path(root) / "settings"
    for p in sorted(s.glob("*.settings.json")):
        role = p.name[: -len(".settings.json")]
        if role.startswith("agent-"):
            continue
        out.append(("claude", role, p))
    for p in sorted(s.glob("codex/*/config.toml")):
        out.append(("codex", p.parent.name, p))
    return out


def _read_hooks(harness: str, path: Path) -> dict | None:
    try:
        if harness == "codex":
            import tomllib
            data = tomllib.loads(path.read_text())
        else:
            data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    hooks = data.get("hooks") if isinstance(data, dict) else None
    return hooks if isinstance(hooks, dict) else {}


def check(root) -> CheckResult:
    """CONFIGURED level: is every registered hook present in every emitted role
    file it targets? Compares (matcher, command) pairs, so an edited command reads
    as missing rather than as present."""
    res = CheckResult()
    reg = load(root)
    res.registry_errors = [{"file": f, "reason": why} for f, why in reg.errors]
    for harness, role, path in emitted_role_files(root):
        hooks = None
        loaded = False
        if harness == "codex":
            _check_notify(res, reg, role, path)
        for b in reg.bundles:
            if not b.applies_to(role):
                continue
            for h in b.hooks:
                if harness not in h.harnesses:
                    continue
                item = {"bundle": b.name, "version": b.version, "harness": harness,
                        "role": role, "event": h.event, "matcher": h.matcher,
                        "command": h.command, "file": str(path),
                        "live": NOT_CHECKED, "firing": NOT_CHECKED, "detail": ""}
                if h.event not in SUPPORTED_EVENTS.get(harness, frozenset()):
                    item["configured"] = "unsupported"
                    item["detail"] = str(Unsupported(b.name, h.event, harness))
                    res.items.append(item)
                    continue
                if not loaded:
                    hooks, loaded = _read_hooks(harness, path), True
                if hooks is None:
                    item["configured"] = "unreadable"
                    item["detail"] = "the emitted settings file could not be parsed"
                elif (h.matcher, h.command) in _commands_in(hooks.get(h.event)):
                    item["configured"] = "ok"
                else:
                    item["configured"] = "missing"
                    item["detail"] = ("not in the emitted file; run `st fleet roles set` to "
                                      "re-emit, and check for a hand edit if it reappears missing")
                res.items.append(item)
    return res


def _check_notify(res: CheckResult, reg: Registry, role: str, path: Path) -> None:
    owner, claimants = notify_owner(reg, role)
    if not claimants:
        return
    for b in reg.bundles:
        if b.name not in claimants:
            continue
        item = {"bundle": b.name, "version": b.version, "harness": "codex", "role": role,
                "event": "notify", "matcher": None, "command": " ".join(b.codex_notify),
                "file": str(path), "live": NOT_CHECKED, "firing": NOT_CHECKED, "detail": ""}
        if owner is None:
            item["configured"] = "unsupported"
            item["detail"] = (f"codex notify is claimed by {len(claimants)} bundles "
                              f"({', '.join(claimants)}); a single slot, so none is rendered")
        else:
            try:
                import tomllib
                have = tomllib.loads(path.read_text()).get("notify")
            except (OSError, ValueError):
                item["configured"], item["detail"] = "unreadable", "could not parse the codex config"
                res.items.append(item)
                continue
            item["configured"] = "ok" if have == list(b.codex_notify) else "missing"
            if item["configured"] == "missing":
                item["detail"] = "codex notify differs from the registered argv"
        res.items.append(item)


def register(root, source_file: Path) -> tuple[str, str]:
    """Validate and install `source_file` into the registry.
    Returns (name, outcome) where outcome is 'installed', 'updated' or 'unchanged'.
    Raises ValueError with every validation reason."""
    obj = json.loads(Path(source_file).read_text())
    errs = validate(obj)
    if errs:
        raise ValueError("; ".join(errs))
    d = registry_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{obj['name']}.json"
    body = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    if dest.exists():
        if dest.read_text() == body:
            return obj["name"], "unchanged"
        outcome = "updated"
    else:
        outcome = "installed"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(body)
    tmp.replace(dest)
    return obj["name"], outcome


def unregister(root, name: str) -> bool:
    if not NAME_RE.match(name):
        raise ValueError(f"not a bundle name: {name!r}")
    p = registry_dir(root) / f"{name}.json"
    if not p.exists():
        return False
    p.unlink()
    return True
