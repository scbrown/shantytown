"""Read-only new-host acceptance, including the peer's view of the return path.

A successful init is a local scaffold, not proof of a two-host deployment. Keep
unknown distinct from incomplete, and never turn an SSH failure into an empty host.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import tomllib
from urllib.parse import quote

from . import config, harness, relay_doctor
from .deployment import deployment_default
from .files import FilesRegistry
from .quipu import QuipuRegistry

VERSION = 1


def _row(name, code, detail):
    return {"name": name, "code": code, "detail": detail}


def _host_graph(root, host):
    server = deployment_default(root, "QUIPU_SERVER")
    onto = deployment_default(root, "SHANTY_ONTO_NS")
    if not server or not onto:
        return _row("graph Host", 1, "Set QUIPU_SERVER and SHANTY_ONTO_NS in [env]; "
                    "use the existing fleet's exact namespace.")
    try:
        graph = QuipuRegistry(server=server, onto=onto, root=root)
        # A passing control is required before an absent Host means anything.
        control = graph._query_answer("SELECT ?s WHERE { ?s ?p ?o } LIMIT 1").exact()
        if not control:
            return _row("graph Host", 2, "Graph control returned no rows; Host presence is unproven.")
        iri = onto + quote(host, safe="-._~")
        if any(c in onto for c in '<>"{}|^`\\\r\n '):
            return _row("graph Host", 2, "Ontology namespace is not a safe absolute IRI.")
        rows = graph._query_answer(
            f"SELECT ?t WHERE {{ <{iri}> a ?t . FILTER(?t = <{onto}Host>) }}").exact()
        if rows:
            return _row("graph Host", 0, f"Direct Host type verified at {iri}.")
        return _row("graph Host", 1, f"Missing direct Host type at {iri}. Search the existing "
                    "graph identity first; register that exact host using the governed Host type "
                    "and verify read-back. See docs/new-host.md.")
    except Exception as exc:
        # Never echo HTTP bodies, URLs carrying credentials, or exception text.
        return _row("graph Host", 2, f"Could not verify graph ({type(exc).__name__}).")


def _capture_root(command, root):
    try:
        words = shlex.split(command)
        module = words.index("-m")
        target = words.index("--root")
        return (module == 1 and words[module + 1:module + 3] == ["shantytown.stats", "capture"]
                and Path(words[target + 1]).resolve() == root)
    except (ValueError, IndexError):
        return False


def snapshot(root, *, backend=None):
    """Only this host. JSON contains selected public config, never [env] or tokens."""
    root = Path(root).resolve()
    result = {"version": VERSION, "scope": "local", "root": str(root),
              "host": None, "peers": {}, "checks": []}
    checks = result["checks"]
    try:
        cfg = config.load(root)
    except (config.ConfigError, OSError):
        checks.append(_row("config", 2, "Cannot parse deployment config; repair shantytown.toml."))
        return result
    result["host"] = cfg.host_name
    result["peers"] = {n: {"ssh": p.ssh, "root": p.root} for n, p in cfg.host_peers.items()}
    checks.append(_row("host", 0 if cfg.host_name else 1,
                       f"[host] name = {json.dumps(cfg.host_name)}" if cfg.host_name else
                       "Declare [host] name; new stores: st fleet init --host NAME."))
    checks.append(_row("peers", 0 if cfg.host_peers else 1,
                       f"{len(cfg.host_peers)} declared peer(s); both hosts need a return entry. "
                       "New stores: --peer NAME=SSH,ROOT; existing stores: see docs/new-host.md."))
    relay_code, relay_text = relay_doctor.check(root, backend=backend)
    checks.append(_row("incoming relay environment", relay_code, relay_text))
    if cfg.host_name:
        checks.append(_host_graph(root, cfg.host_name))
    try:
        cards = FilesRegistry(root / "crew").all().exact()
        cards = [c for c in cards if not c.retired and (not c.host or c.host == cfg.host_name)]
    except Exception as exc:
        checks.append(_row("cards", 2, f"Cannot read local cards ({type(exc).__name__})."))
        return result
    checks.append(_row("cards", 0 if cards else 1, f"{len(cards)} active local card(s)."))
    # Borrow the launcher's selection, including per-agent overrides and workers
    # with reports. Looking only at the role file can certify an unused artifact.
    from .cli import _default_settings
    select = _default_settings(root, cards)
    for card in cards:
        prefix = card.name + "/"
        checks.append(_row(prefix + "permissions", 0 if type(card.dangerous) is bool else 1,
                           "UNATTENDED" if card.dangerous is True else "MANUAL" if card.dangerous is False
                           else "Undecided: choose manual or unattended explicitly (see docs/new-host.md)."))
        ws = Path(card.workspace).expanduser() if card.workspace else None
        checks.append(_row(prefix + "workspace", 0 if ws and ws.is_absolute() and ws.is_dir() else 1,
                           str(ws) if ws and ws.is_absolute() and ws.is_dir() else
                           "Set an absolute workspace directory and create it; plain directories are supported."))
        try:
            program = harness.for_card(card, root=root)
            selected = select(card)
            if not selected:
                checks.append(_row(prefix + "settings", 1,
                    f"Missing settings: st --root {shlex.quote(str(root))} fleet roles set "
                    f"{shlex.quote(card.name)} {shlex.quote(card.role)}"))
                continue
            path = Path(selected)
            data = (tomllib.loads(path.read_text()) if program.name == "codex"
                    else json.loads(path.read_text()))
            checks.append(_row(prefix + "settings", 0, str(path)))
            sources = [data]
            if program.name == "claude" and ws:
                # Older role files intentionally leave capture in workspace
                # consent. Both are loaded; counting only one gives false reds.
                consent = ws / ".claude" / "settings.local.json"
                if consent.is_file():
                    sources.append(json.loads(consent.read_text()))
            for event in ("PostToolUse", "Stop"):
                commands = [h.get("command", "") for source in sources
                            for g in source.get("hooks", {}).get(event, [])
                            if g.get("matcher", "") in ("", "*", ".*", None)
                            for h in g.get("hooks", [])
                            if "shantytown.stats capture" in h.get("command", "")]
                wired = len(commands) == 1 and _capture_root(commands[0], root)
                checks.append(_row(prefix + event + " capture", 0 if wired else 1,
                                   "Configured once for this root (runtime delivery needs a real session)." if wired else
                                   "Capture missing, duplicated or points at another root; re-emit role settings "
                                   "with st fleet roles set and reconcile workspace capture hooks."))
            if program.name == "codex":
                binary = harness.codex_standalone_binary(path.parent)
                rc = program._remote_control(root)
                ready = not rc or binary.is_file() and os.access(binary, os.X_OK)
                checks.append(_row(prefix + "Codex RC", 0 if ready else 1,
                    "Remote Control off." if not rc else "Standalone executable present." if ready else
                    "Install the managed standalone package, or set [env] SHANTY_REMOTE_CONTROL = 'false'."))
        except Exception as exc:
            checks.append(_row(prefix + "settings", 2,
                               f"Cannot inspect selected settings ({type(exc).__name__})."))
    return result


def _read_peer(peer):
    # Do not inject PATH or exports: that would hide precisely the SSH environment
    # defect this check must detect. --local makes peer inspection non-recursive.
    command = shlex.join(["st", "--root", peer.root, "ops", "doctor", "--deploy", "--local", "--json"])
    proc = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "--",
                           peer.ssh, command], capture_output=True, text=True, timeout=25)
    if proc.returncode not in (0, 1, 2):
        raise ValueError(f"SSH exit {proc.returncode}")
    value = json.loads(proc.stdout)
    if (not isinstance(value, dict) or value.get("version") != VERSION
            or value.get("scope") != "local" or value.get("host") != peer.name
            or value.get("root") != peer.root or not isinstance(value.get("peers"), dict)
            or not isinstance(value.get("checks"), list) or not value["checks"]):
        raise ValueError("incompatible snapshot")
    for row in value["checks"]:
        if (not isinstance(row, dict) or type(row.get("code")) is not int
                or row["code"] not in (0, 1, 2)
                or any(not isinstance(row.get(k), str) for k in ("name", "detail"))):
            raise ValueError("invalid check row")
    if proc.returncode != exit_code(value):
        raise ValueError("snapshot exit disagrees with checks")
    return value


def check(root, *, backend=None, local=False):
    report = snapshot(root, backend=backend)
    if local:
        return report
    report["scope"] = "deployment"
    try:
        cfg = config.load(root)
    except (config.ConfigError, OSError):
        return report

    def inspect(peer):
        try:
            other = _read_peer(peer)
            reverse = other["peers"].get(cfg.host_name)
            reciprocal = (isinstance(reverse, dict) and reverse.get("root") == report["root"]
                          and isinstance(reverse.get("ssh"), str) and bool(reverse["ssh"].strip()))
            detail = ("Return entry names this root. Run this checklist from the peer too "
                      "to test its outbound SSH path." if reciprocal else
                      f"On {peer.name}, add/correct [host.peers.{json.dumps(cfg.host_name)}] "
                      f"with root = {json.dumps(report['root'])} and ssh = '<this host SSH target>'.")
            rows = [_row(peer.name + "/reciprocal peer", 0 if reciprocal else 1, detail)]
            rows.extend(_row(peer.name + "/" + r["name"], r["code"], r["detail"])
                        for r in other["checks"])
            return rows
        except Exception as exc:
            return [_row(peer.name + "/snapshot", 2,
                         f"Could not verify peer ({type(exc).__name__}); check SSH and installed st version.")]

    peers = sorted(cfg.host_peers.values(), key=lambda p: p.name)
    if peers:
        with ThreadPoolExecutor(max_workers=min(8, len(peers))) as pool:
            for rows in pool.map(inspect, peers):
                report["checks"].extend(rows)
    return report


def exit_code(report):
    return max((r["code"] for r in report["checks"]), default=2)


def render(report):
    rows = [f"Deployment checklist: {report['host'] or 'undeclared host'} ({report['root']})"]
    for row in report["checks"]:
        rows.append(f"  {('OK', 'INCOMPLETE', 'UNKNOWN')[row['code']]} {row['name']}: {row['detail']}")
    rows.append("Read-only setup checks; no agents launched, graph writes, or shell edits. "
                "Run from both hosts over non-interactive SSH. See docs/new-host.md for repairs.")
    return "\n".join(rows)
