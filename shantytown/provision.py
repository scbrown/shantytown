"""provision — an agent is FULLY EQUIPPED or it is not created.

ensure_workspace took this line for the directory: clone it, or refuse to launch
into nothing. This is the rest of the kit, and it exists because the directory
was never the whole job. Five agents were created from clean clones and worked P1
beads for a night with no code search, no knowledge graph and no ops tools —
because the file that wires those tools is UNCOMMITTED, lives only in older
working trees, and a fresh clone therefore cannot have it. They looked live in
`st crew`, accepted dispatch, did the work, and silently lacked what the work
assumed. A half-equipped agent is worse than a missing one: a missing agent is
visible.

WHY THE FILE IS NOT SIMPLY COMMITTED. It carries a live bearer token. "Commit it"
trades a provisioning bug for a credential leak, which is a worse bug with a
longer tail. So the TEMPLATE is the artifact — it holds every server and a
`${PLACEHOLDER}` where each secret goes — and the secret is injected at provision
time from one place that is not a repo.

THE RULES, and each is a refusal rather than a warning:

  A placeholder that cannot be resolved REFUSES. It never renders empty and never
  renders the literal `${...}`. A .mcp.json with an empty Authorization header is
  the exact failure this module exists to stop: the agent launches, the server
  401s on the first call, and the pane shows a tool error the operator reads as a
  flaky service. Partial provisioning is the bug wearing a success costume.

  VERIFY BY LISTING, NOT BY EXISTENCE. provision() returns the server NAMES it
  parsed back out of the file it just wrote. "the file is there" is the claim
  that was true for a broken render; "these six servers are configured" is the
  claim worth making.

  THE SECRET IS NEVER PRINTED, NEVER LOGGED, NEVER PUT IN A LAUNCH STRING. The
  launcher composes its command with `tmux send-keys`, so an env prefix carrying a
  token would put it on a pane, in scrollback, and in every capture the tier
  takes. It goes in a 0600 file in the agent's own workspace — which is where the
  established crew already keep it — and nowhere else.
"""
from __future__ import annotations
import json
import os
import re
import shutil
import tomllib
import tempfile
from pathlib import Path

from .protocols import Agent
from . import tooling


class ProvisionError(RuntimeError):
    """The kit could not be completed. REFUSE: launch nothing.

    Same shape as WorkspaceError and for the same reason — the failure we do not
    ship is the silent one. An agent launched without its tools is indistinguish-
    able from a healthy one on every surface the tier has.
    """


# Everything provisioning reads lives under <root>/provision/. That directory is
# inside the store, which is gitignored by construction, so the secret file
# cannot be committed by an absent-minded `git add -A` in the tool's own repo.
PROVISION_DIR = "provision"
MCP_TEMPLATE = "mcp.template.json"
CONSENT_TEMPLATE = "settings.local.json"
SECRETS = "secrets.env"

# Skills are the OTHER half of the kit, and they rot the same way .mcp.json went
# missing. A workspace keeps its skills git-tracked in <ws>/skills/; the runtime
# reads ONLY <ws>/.claude/skills/. `.claude/` is gitignored, so the bridge between
# them cannot ship in the clone — it has to be built at provision time or not at
# all. See link_skills().
SKILLS_SRC = "skills"
SKILLS_RUNTIME = (".claude", "skills")
CODEX_SKILLS_RUNTIME = (".agents", "skills")

_PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


def provision_dir(root) -> Path:
    return Path(root) / PROVISION_DIR


def load_secrets(root, template: str | None = None) -> dict:
    """Secrets for rendering: the environment WINS over the file.

    Two sources on purpose. The file is the fleet's one copy — the thing that did
    not exist when this bug happened, when the token lived in seventeen working
    trees and nowhere else. The environment override is for a caller that already
    holds the secret (a CI run, a human doing a one-off) without writing it down.
    """
    out = {}
    p = provision_dir(root) / SECRETS
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass                      # no file is not an error; an UNRESOLVED name is
    needed = _needed_names(root) if template is None else _PLACEHOLDER.findall(template)
    for k in list(out) + needed:
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def _needed_names(root) -> list[str]:
    try:
        text = (provision_dir(root) / MCP_TEMPLATE).read_text()
    except OSError:
        return []
    return sorted(set(_PLACEHOLDER.findall(text)))


def render(text: str, secrets: dict) -> str:
    """Substitute every ${NAME}, or RAISE naming the ones that are missing.

    All-or-nothing. A template that rendered what it could would produce a file
    that parses, loads, and fails on the first authenticated call — the operator
    sees a tool error, not a provisioning error, and looks in the wrong place.
    """
    missing = sorted({n for n in _PLACEHOLDER.findall(text) if not secrets.get(n)})
    if missing:
        raise ProvisionError(
            f"cannot render: no value for {', '.join(missing)}. Put it in "
            f"<root>/{PROVISION_DIR}/{SECRETS} (KEY=value, one per line) or the "
            f"environment. Refusing to write a half-rendered config — an empty "
            f"credential fails at first use, as a tool error, in the wrong place."
        )
    return _PLACEHOLDER.sub(lambda m: secrets[m.group(1)], text)


def servers_in(path) -> list[str]:
    """The server NAMES actually configured in a rendered file. The verification:
    a file that exists proves nothing, a parsed server list is a measurement."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    return sorted((data.get("mcpServers", data) or {}).keys())


def expected_servers(root) -> list[str]:
    """What a fully-equipped agent has, per the template. The comparison target
    for `st new`'s claim and for tend's gap report."""
    manifest = tooling.load(root)
    if manifest is not None:
        return sorted(manifest.mcp)
    try:
        return servers_in_text((provision_dir(root) / MCP_TEMPLATE).read_text())
    except OSError:
        return []


def servers_in_text(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return sorted((data.get("mcpServers", data) or {}).keys())


def _skill_sources(ws: Path) -> list[Path]:
    """The workspace's git-tracked skills — a directory with a SKILL.md in it.
    Anything else under skills/ (a README, a scratch dir) is not a skill."""
    try:
        return sorted(p for p in (ws / SKILLS_SRC).iterdir()
                      if (p / "SKILL.md").is_file())
    except OSError:
        return []                              # no skills/ is not an error


def _projected_targets(ws: Path) -> dict[str, str]:
    """What the tooling projection RECORDED it linked, or {} if it never ran.

    A manifest may legitimately source a skill from OUTSIDE the workspace, and on
    this deployment every one of them does: the projection sources all 24 from the
    ownership-neutral skills-src clone rather than from each agent's own tree. The
    receipt is the only place that chosen source survives provisioning, so it is
    the only way a pure read can tell a correctly-projected runtime from a broken
    one (aegis-c64jfe).
    """
    try:
        data = json.loads((ws / tooling.RECEIPT).read_text())
        skills = data["skills"]
        return {name: target for name, target in skills.items()
                if isinstance(name, str) and isinstance(target, str)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {}


def _runtime_linked(ws: Path, runtime: tuple[str, str]) -> list[str]:
    """The skill names this runtime can actually load, measured. Pure read.

    Same discipline as servers_in: not "the directory exists" but "these names
    resolve, through a symlink, to a real SKILL.md". Every green signal in the
    original bug (files present, well-formed, committed, pulled) was true of a
    fleet loading ZERO skills — presence was never the question.

    TWO targets are accepted and no others: the workspace's own source, and the
    one THIS workspace's receipt recorded. That keeps the teeth — a link to some
    arbitrary path still reads unlinked, which is the aegis-y0ky6 property this
    predicate exists to hold — while no longer calling a runtime unlinked for
    obeying the projection. Measured before this change: 0 of 24 on 13 of 13 live
    agents, while codex was loading all 24 through those same links.
    """
    projected = _projected_targets(ws)
    out = []
    for src in _skill_sources(ws):
        link = ws.joinpath(*runtime, src.name)
        if not link.is_symlink() or not (link / "SKILL.md").is_file():
            continue
        target = os.readlink(link)
        if target == str(src) or target == projected.get(src.name):
            out.append(src.name)
    return out


def skills_linked(ws) -> list[str]:
    """The skill names Claude Code's runtime can actually load. Pure read."""
    return _runtime_linked(Path(ws).expanduser(), SKILLS_RUNTIME)


def codex_skills_linked(ws) -> list[str]:
    """The same source skills, realized at Codex's documented repo location."""
    return _runtime_linked(Path(ws).expanduser(), CODEX_SKILLS_RUNTIME)


def _link_skill_runtime(ws: Path, runtime: tuple[str, str]) -> None:
    dst = ws.joinpath(*runtime)
    for src in _skill_sources(ws):
        link = dst / src.name
        try:
            if (link.is_symlink() and os.readlink(link) == str(src)
                    and (link / "SKILL.md").is_file()):
                continue
            dst.mkdir(parents=True, exist_ok=True)
            if link.is_symlink() or link.exists():
                if link.is_dir() and not link.is_symlink():
                    shutil.rmtree(link)
                else:
                    link.unlink()
            link.symlink_to(src)
        except OSError:
            continue


def link_skills(ws) -> list[str]:
    """SYMLINK every <ws>/skills/<name> into <ws>/.claude/skills/. Never COPY.

    WHY THIS IS PROVISIONING'S JOB (aegis-atm3 / aegis-qvxd). These links were
    made BY HAND, once, and nothing created or maintained them. Measured on this
    deployment 2026-07-24: 8 of 23 crew clones had no .claude/skills directory at
    all — loading ZERO skills — and 6 more were partial, while every surface said
    fine. Then a NEW skill landed correctly in git and reached 1 of 24 runtimes,
    because a new skill needs a new link and nothing makes one; its author paid
    the fix by hand, twice, and it was still late. That is the same shape as the
    .mcp.json bug this module was written for: the agent launches, looks healthy,
    accepts dispatch, and silently lacks what the work assumes.

    So it belongs HERE, next to the capture hook and for the identical reason —
    provision is re-run on EVERY launch, so a clone converges by construction
    instead of by somebody remembering. A copy would defeat the whole point: a
    real copy is byte-identical the day it is made and tracks no fix afterwards
    (a stale graph-report copy shadowed the source for ~24 days that way). The
    LINK is what makes a canonical fix reach the runtime.

    IDEMPOTENT and ADDITIVE, safe on a live agent: a correct link is left
    untouched, and a name with no source twin (a personal skill dropped in by
    hand) is never touched at all. Only the defect is replaced — a real copy, a
    link to the wrong place, a dangling link.

    NEVER the reason provisioning fails. A skill that cannot be linked is simply
    absent from the return value, which is what missing_kit reports on.
    """
    ws = Path(ws).expanduser()
    srcs = _skill_sources(ws)
    if not srcs:
        return []                        # this workspace ships no skills: fine
    _link_skill_runtime(ws, SKILLS_RUNTIME)
    _link_skill_runtime(ws, CODEX_SKILLS_RUNTIME)
    return skills_linked(ws)               # verify by listing, not by existence


def link_instructions(ws, harness: str | None) -> bool:
    """Project the one rulebook into the filename each harness discovers."""
    ws = Path(ws).expanduser()
    source = ws / "CLAUDE.md"
    if not source.is_file():
        return False
    if harness != "codex":
        return True
    target = ws / "AGENTS.md"
    if target.exists() and source.resolve() == target.resolve():
        return True
    if target.is_symlink() and os.readlink(target) == "CLAUDE.md":
        return True
    if target.exists() and not target.is_symlink():
        try:
            if target.read_bytes() != source.read_bytes():
                raise ProvisionError(
                    f"cannot make tooling instructions uniform in {ws}: AGENTS.md "
                    "differs from CLAUDE.md. Refusing to overwrite either source; "
                    "reconcile them, then re-launch.")
        except OSError as e:
            raise ProvisionError(f"cannot compare instruction files in {ws}: {e}")
        target.unlink()
    elif target.is_symlink():
        target.unlink()
    target.symlink_to("CLAUDE.md")
    return target.is_symlink() and os.readlink(target) == "CLAUDE.md"


def _codex_config(card: Agent, root) -> Path | None:
    from . import codex as codex_mod
    settings = Path(root) / "settings" / "codex"
    candidates = (settings / f"agent-{card.name}" / codex_mod.CONFIG_FILE,
                  settings / card.role / codex_mod.CONFIG_FILE)
    return next((p for p in candidates if p.is_file()), None)


def _codex_servers(servers: dict) -> dict:
    """Translate Claude's declarative MCP entries into Codex config values.

    EVERY header — Authorization included — is carried as a LITERAL in
    `http_headers`, which lands in the role's 0600 gitignored config.toml. It
    used to be otherwise: an `Authorization: Bearer ${VAR}` in the template was
    translated to codex's `bearer_token_env_var = "VAR"`, and CodexHarness.launch
    then sourced <root>/provision/secrets.env with `set -a` so codex could read
    it. That put the bearer in the SESSION ENVIRONMENT, and codex writes a shell
    snapshot of its environment at session start — so the secret was captured
    into $CODEX_HOME/shell_snapshots/*.sh BY CONSTRUCTION, on every launch, with
    no careless act required (aegis-6qau3t, the env-capture half of aegis-lg8kxj).

    MEASURED 2026-09-11, before and after, on this host:

      * 6 of 6 existing codex shell snapshots across 5 agents carried a bearer
        (grep -F against a 0600 needle file; CONTROL: the same grep against
        .mcp.json itself returned 1, so the search could see the value).
      * codex-cli 0.154.0 REFUSES the obvious alternative: `bearer_token` in
        config.toml dies at load with "bearer_token is not supported for
        streamable_http", which would have stopped every codex agent starting.
        `http_headers` is the form it accepts — `codex mcp get homelab` reports
        `http_headers: Authorization=*****` (codex masks it itself).
      * END TO END with NO *_MCP_TOKEN in the environment: `codex exec` ran
        homelab/service_health and agent/devops_check and both COMPLETED.
        CONTROL that this is not two open servers: both endpoints answer 401
        with no Authorization and 403 with a wrong bearer.

    So the bearer now lives only in files the deployment already keeps at 0600
    and out of git (provision/secrets.env, settings/codex/<role>/config.toml),
    and in no process environment at all.
    """
    out = {}
    for name, raw in servers.items():
        spec = dict(raw)
        spec.pop("type", None)
        if "headers" in spec:
            headers = dict(spec.pop("headers"))
            if headers:
                spec["http_headers"] = headers
        out[name] = spec
    return out


def codex_servers_in(path) -> list[str]:
    try:
        data = tomllib.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return []
    return sorted((data.get("mcp_servers") or {}).keys())


def _project_codex_mcp(card: Agent, root, rendered: str, template: str | None = None) -> None:
    if card.harness != "codex":
        return
    config = _codex_config(card, root)
    if config is None:
        raise ProvisionError(f"cannot project MCP kit for {card.name}: no Codex "
                             "config.toml exists for the agent or its role")
    from . import codex as codex_mod
    data = json.loads(rendered)
    servers = _codex_servers(data.get("mcpServers", data))
    existing = config.read_text()
    if template is not None:
        current = tomllib.loads(existing)
        current.pop("mcp_servers", None)
        existing = codex_mod.dumps(current)
    # THIS FILE NOW CARRIES THE BEARER (see _codex_servers), so it is written the
    # same way .mcp.json is: created privately, then atomically published. A
    # plain write_text() would inherit the umask on first creation and leave the
    # secret world-readable for the life of the file. Resolve first, because a
    # deployment points its per-card CODEX_HOME at this path through a symlink
    # and replace() onto the link would swap the link for a regular file.
    target = Path(os.path.realpath(config))
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as tmp:
        temporary = Path(tmp.name)
        try:
            tmp.write(codex_mod.render({"mcp_servers": servers}, existing, root=root))
            tmp.close()
            os.chmod(temporary, 0o600)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


_UNREAD = object()


def _render_manifest(manifest: tooling.Manifest, secrets: dict) -> dict:
    # Substitute string values before JSON serialization, so quotes/backslashes
    # in a credential cannot alter the shape of its configuration file.
    def expand(value):
        if isinstance(value, str):
            return render(value, secrets)
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value
    return {"mcpServers": expand(manifest.mcp)}


def _manifest_gaps(card: Agent, root, manifest: tooling.Manifest, secrets=None) -> list[str]:
    ws = Path(card.workspace).expanduser()
    template = json.dumps({"mcpServers": manifest.mcp})
    try:
        rendered = _render_manifest(manifest, secrets if secrets is not None else load_secrets(root, template))
    except (ProvisionError, ValueError):
        return ["tooling-source(unresolved credentials)"]
    gaps = []
    try:
        if json.loads((ws / ".mcp.json").read_text()) != rendered:
            gaps.append("mcp(Quipu drift)")
    except (OSError, ValueError):
        gaps.append("mcp(unreadable)")
    for name, src in manifest.sources(ws):
        for runtime in (SKILLS_RUNTIME, CODEX_SKILLS_RUNTIME):
            link = ws.joinpath(*runtime, name)
            try:
                valid = link.is_symlink() and link.resolve() == src.resolve() and (link / "SKILL.md").is_file()
            except (OSError, RuntimeError):
                valid = False
            if not valid:
                gaps.append(f"{runtime[0]}-skills({name})")
    gaps.extend(tooling.instruction_gaps(ws, manifest))
    try:
        if tooling.retired_links(ws, manifest):
            gaps.append("skills(retired in Quipu)")
    except tooling.ToolingError:
        gaps.append("skills(receipt or retired link unreadable)")
    if card.harness == "codex":
        from . import codex as codex_mod
        config = _codex_config(card, root)
        want = _codex_servers(rendered["mcpServers"])
        # Include the deployment's existing approval projection in the expected
        # values; endpoint/auth/command drift still must compare exactly.
        want = tomllib.loads(codex_mod.render({"mcp_servers": want}, root=root)).get("mcp_servers", {})
        try:
            have = tomllib.loads(config.read_text()).get("mcp_servers", {}) if config else None
        except (OSError, ValueError):
            have = None
        if have != want:
            gaps.append("codex-mcp(Quipu drift)")
    else:
        try:
            consent = json.loads((ws / ".claude" / CONSENT_TEMPLATE).read_text())
            enabled = set(consent.get("enabledMcpjsonServers", []))
            disabled = set(consent.get("disabledMcpjsonServers", []))
            if enabled != set(manifest.mcp) or disabled & set(manifest.mcp):
                gaps.append("mcp-consent(Quipu drift)")
        except (OSError, ValueError, TypeError, AttributeError):
            gaps.append("mcp-consent")
    return gaps


def missing_kit(card: Agent, root, *, manifest=_UNREAD) -> list[str]:
    """What this agent's workspace LACKS, by name. Empty = fully equipped.

    Cheap enough to run on every supervision pass, which is the point: nothing in
    the tier reported this difference, so five agents carried it for a night.
    """
    if not card.workspace:
        return []                                  # no workspace: not our claim
    ws = Path(card.workspace).expanduser()
    if not ws.is_dir():
        return ["workspace"]
    if manifest is _UNREAD:
        try:
            manifest = tooling.load(root)
        except tooling.ToolingError:
            return ["tooling-source(UNKNOWN)"]
    if manifest is not None:
        return _manifest_gaps(card, root, manifest)
    gaps = []
    want = expected_servers(root)
    have = servers_in(ws / ".mcp.json")
    if want and sorted(have) != sorted(want):
        gaps.append(f"mcp({','.join(sorted(set(want) - set(have))) or 'mismatch'})")
    if card.harness != "codex" and not (ws / ".claude" / CONSENT_TEMPLATE).is_file():
        gaps.append("mcp-consent")
    # SKILLS ARE KIT TOO — and this is the "wire the detector to something that
    # runs" half of aegis-qvxd. The standing guard for skill drift was a shell
    # script no hook, timer or CI ever called, so a stale runtime sat unnoticed
    # for ~24 days; and when it WAS finally run by hand it false-failed off its
    # own stale checkout. Reporting the gap from the supervision pass fixes both:
    # tend already runs every cycle, and it runs THIS code, so the detector can
    # never be older than the fleet it is judging.
    want = {p.name for p in _skill_sources(ws)}
    unlinked = sorted(want - set(skills_linked(ws)))
    if unlinked:
        gaps.append(f"skills({','.join(unlinked)})")
    if card.harness == "codex":
        codex_unlinked = sorted(want - set(codex_skills_linked(ws)))
        if codex_unlinked:
            gaps.append(f"codex-skills({','.join(codex_unlinked)})")
        config = _codex_config(card, root)
        have = codex_servers_in(config) if config else []
        if expected_servers(root) and have != expected_servers(root):
            gaps.append("codex-mcp(uniformity)")
        if not (ws / "AGENTS.md").is_file() or not (ws / "CLAUDE.md").is_file() or (ws / "AGENTS.md").resolve() != (ws / "CLAUDE.md").resolve():
            gaps.append("instructions(AGENTS.md)")
    return gaps


def uniformity_report(cards, root) -> tuple[str, bool]:
    """Doctor's harness-neutral realization check; manifest data is discovered."""
    rows = []
    broken = False
    try:
        manifest = tooling.load(root)
    except tooling.ToolingError as e:
        return f"  TOOLING UNIFORMITY: UNKNOWN — {e}", True
    for card in sorted(cards, key=lambda c: c.name):
        if card.retired or not card.workspace:
            continue
        gaps = missing_kit(card, root, manifest=manifest)
        harness = card.harness or "claude"
        if gaps:
            broken = True
            rows.append(f"  ! {card.name:<12} {harness:<7} {', '.join(gaps)}")
        else:
            rows.append(f"  ✓ {card.name:<12} {harness:<7} uniform")
    head = "  TOOLING UNIFORMITY (MCP + skills + instructions)"
    return "\n".join([head] + (rows or ["  ? no provisionable crew cards"])), broken


def _consent_for_role(text: str, role: str) -> str:
    """WORKERS lose the interactive picker; every other role keeps it.

    The template's `permissions.deny AskUserQuestion` is the aegis-qxc2 flip: a
    worker's option-picker blocks its pane invisibly (7 of 10 workers stalled on
    pickers at once, found only by hand-capturing panes), so workers must route
    decisions to beads/inbox instead — and A/B proof on that bead shows the deny
    strips the picker while ordinary permission prompts survive. But this ONE
    template renders into EVERY provisioned workspace, and a lead/administrator
    picker is a HUMAN channel (the administrator's picker is answered by the
    overseer over remote control). Denying it there severs the human, not the
    stall. Role-blind rendering was the bug; this filter is the narrowest fix:
    only AskUserQuestion is stripped, any other deny entry passes through, and a
    template that is not JSON passes through verbatim — this helper must never
    be the reason provisioning fails.
    """
    if role == "worker":
        return text
    try:
        cfg = json.loads(text)
    except ValueError:
        return text
    deny = cfg.get("permissions", {}).get("deny")
    if isinstance(deny, list) and "AskUserQuestion" in deny:
        deny = [t for t in deny if t != "AskUserQuestion"]
        if deny:
            cfg["permissions"]["deny"] = deny
        else:
            del cfg["permissions"]["deny"]
            if not cfg["permissions"]:
                del cfg["permissions"]
    return json.dumps(cfg, indent=2) + "\n"


def _manifest_consent(text: str, manifest: tooling.Manifest) -> str:
    try:
        cfg = json.loads(text)
        if not isinstance(cfg, dict):
            raise ValueError
        disabled = set(cfg.get("disabledMcpjsonServers", []))
    except (ValueError, TypeError):
        raise ProvisionError("canonical MCP consent template is invalid") from None
    if disabled & set(manifest.mcp):
        raise ProvisionError("canonical MCP server is disabled by the consent template; reconcile the policy")
    cfg["enabledMcpjsonServers"] = sorted(manifest.mcp)
    return json.dumps(cfg, indent=2) + "\n"


def _with_capture_hook(text: str, root) -> str:
    """Inject the metrics-capture hook into the workspace consent settings, so
    EVERY provisioned agent captures tool usage (mcp__*, Skill, CLI-via-Bash)
    from launch — aegis-rcyd — AND its token totals on stop — aegis-u5u98.

    BOTH EVENTS, because `capture` has two branches and only one was ever wired.
    PostToolUse takes the tool branch; the TOKEN totals are written exclusively
    on the Stop branch, which nothing registered. So tokens could not be recorded
    at all, anywhere on the fleet, and had not been for twelve days when this was
    found — while events kept flowing and made the store look healthy.

    It read as a per-agent bug rather than a dead pipeline for a second reason,
    fixed in stats.stats_report: the token query was unbounded by time, so four
    agents with rows left over from the last day the Stop path fired still showed
    totals under a `last 24h` header. Two halves of one illusion — a capture that
    could not run, and a display that made its absence look selective.

    ONE COMMAND, TWO EVENTS IS NOT DOUBLE-CAPTURE. The hazard named below is the
    SAME event delivered from two settings sources; these are different events
    with different payloads, and `capture` dispatches on the payload rather than
    trusting the registration. The token write is an UPSERT keyed by session
    holding ABSOLUTE totals, so re-firing is idempotent by construction.

    WHY HERE and not in --settings (claude_settings_for_role): this consent file
    is re-applied on EVERY launch (provision is idempotent, the launcher calls it
    each start), so it SELF-HEALS — a fleet whose settings went stale picks the
    hook up on next launch. --settings is emitted only on `role set`, which is
    exactly why the 693024d wiring never collected fleet-wide: running agents
    never regenerated it. Single home, so no double-capture.

    The interpreter + store root are BAKED at provision time (in the st process,
    i.e. the pipx venv python that can actually import shantytown) via
    runtime._capture_cmd — a static template cannot resolve them, and a bare
    `python` is not on PATH / cannot import shantytown (aegis-rcyd: tim). Never
    the reason provisioning fails: a non-JSON / non-dict template passes through
    verbatim.
    """
    from .runtime import (  # lazy import hygiene
        _capture_cmd,
        _yupana_action_outcome_cmd,
        _yupana_post_tool_cmd,
    )
    try:
        cfg = json.loads(text)
    except ValueError:
        return text
    if not isinstance(cfg, dict):
        return text
    hooks = cfg.setdefault("hooks", {})
    pre = hooks.get("PreToolUse", [])
    pre = [g for g in pre if not any(
        "shantytown.stats capture" in h.get("command", "")
        for h in g.get("hooks", []) if isinstance(h, dict))]
    hooks["PreToolUse"] = pre + [{"matcher": ".*", "hooks": [_capture_cmd(root)]}]
    hooks["PostToolUse"] = [
        {"matcher": ".*", "hooks": [_capture_cmd(root), _yupana_post_tool_cmd()]},
        # The ACTION OUTCOME record (aegis-368cu.10), Bash only — see
        # runtime._yupana_action_outcome_cmd for why the matcher is not `.*`.
        # Assigned HERE, in the same statement, because this assignment REPLACES
        # `PostToolUse` wholesale: a separate injector running before it would be
        # silently discarded, and one running after would have to append and get
        # the ordering right. One home, one assignment.
        {"matcher": "Bash", "hooks": [_yupana_action_outcome_cmd()]},
    ]
    # THE FAILURE EVENT IS NOT OPTIONAL. `PostToolUse` fires on success and
    # `PostToolUseFailure` on failure, so the event name is how the outcome is
    # OBSERVED. Wiring only the success event would record every failed command
    # as `unknown` — a trace that looks complete and cannot answer the one
    # question it exists for.
    hooks["PostToolUseFailure"] = [
        {"matcher": ".*", "hooks": [_capture_cmd(root)]},
        {"matcher": "Bash", "hooks": [_yupana_action_outcome_cmd()]},
    ]
    # NO MATCHER on Stop: Stop carries no tool name, and a matcher on an event
    # that has nothing to match is the aegis-ac5x failure — a registration that
    # looks specific and fires zero times.
    hooks["Stop"] = [{"hooks": [_capture_cmd(root)]}]
    return json.dumps(cfg, indent=2) + "\n"


def _with_untracked_hook(text: str, role: str, root) -> str:
    """Inject the untracked-work PreToolUse nudge (aegis-fv2zc) — for NON-ADMIN
    roles only.

    WHY HERE, and not in --settings, is the SAME finding as _with_capture_hook
    above, and it is the reason that one moved: this consent file is re-applied
    on every launch, so it SELF-HEALS; `claude_settings_for_role` is emitted only
    on `role set`, and 693024d's wiring proved a hook delivered that way never
    collects fleet-wide because running agents never regenerate it. A governance
    hook that reaches nobody is not a governance hook. Measured on this
    deployment 2026-07-24: the capture hook delivered HERE is live in all 8
    agents' workspaces and collecting, while the --settings files predate it.

    ONE HOME, deliberately: Claude Code merges hooks from every settings source,
    so wiring the same command in both places fires it TWICE per tool call —
    double strikes, double warnings, and an escalation at half the threshold.

    ADMIN EXEMPT structurally: an administrator's consent file never carries the
    hook, so a coordinator cannot be warned for dispatching by a hook that does
    not exist for it. untracked.check() re-checks the role anyway, for the window
    where a promoted worker is still running its old settings.

    APPENDS rather than assigns: a template that ships its own PreToolUse entries
    keeps them. Idempotent even so — any previous untracked entry is dropped
    first, so re-provisioning cannot stack them.

    Never the reason provisioning fails: a non-JSON / non-dict template passes
    through verbatim, exactly like the capture injector.
    """
    if role == "administrator":
        return text
    from .runtime import _untracked_hook  # lazy: provision<->runtime hygiene
    try:
        cfg = json.loads(text)
    except ValueError:
        return text
    if not isinstance(cfg, dict):
        return text
    hooks = cfg.setdefault("hooks", {})
    kept = [e for e in hooks.get("PreToolUse", [])
            if not any("shantytown.untracked" in h.get("command", "")
                       for h in e.get("hooks", []))]
    hooks["PreToolUse"] = kept + [_untracked_hook(root)]
    return json.dumps(cfg, indent=2) + "\n"


def _with_stale_hook(text: str, role: str, root) -> str:
    """Inject the edit-time STALENESS advisory (aegis-ib65p decision 5).

    HERE, not in `claude_settings_for_role`, for the reason the two injectors
    above already record and that this codebase has now measured twice: this
    consent file is re-applied on EVERY launch and therefore self-heals, while
    `--settings` is emitted only on `role set` and never reaches an agent that
    is already running. A staleness guard that reaches nobody would be a
    particularly bad joke, since not reaching people is the entire bug.

    UNLIKE the untracked nudge, EVERY role gets this one, administrators
    included. That exemption exists there because a coordinator should not be
    scolded for dispatching rather than committing — a role-specific behaviour.
    Staleness is not role-specific: the incident that opened this bead was the
    COORDINATOR rebuilding a fix that already existed. Exempting the role it
    actually happened to would be exactly the wrong lesson.

    APPENDS and is idempotent — any previous entry is dropped first, so
    re-provisioning cannot stack it and fire it twice per tool call.
    """
    from .runtime import _stale_hook      # lazy: provision<->runtime hygiene
    try:
        cfg = json.loads(text)
    except ValueError:
        return text
    if not isinstance(cfg, dict):
        return text
    hooks = cfg.setdefault("hooks", {})
    kept = [e for e in hooks.get("PreToolUse", [])
            if not any("shantytown.stale_guard" in h.get("command", "")
                       for h in e.get("hooks", []))]
    hooks["PreToolUse"] = kept + [_stale_hook(root)]
    return json.dumps(cfg, indent=2) + "\n"


def _with_precompact_hook(text: str, role: str, root) -> str:
    """Inject the PreCompact checkpoint (aegis-902vnu — Stiwi: "you should be
    handing off before compaction same with all st agents").

    HERE for the third time and the same measured reason: this consent file is
    re-applied on every launch and therefore self-heals, while --settings is
    emitted once at `role set` and never reaches a running agent. This one has
    the sharpest version of that argument — the population it must reach is
    precisely the long-lived sessions, i.e. the ones that have been running
    since before any settings regeneration.

    EVERY ROLE, administrators included, and that is the directive's own word:
    "all st agents", coordinator included. The untracked nudge exempts admins
    because being scolded for dispatching is role-specific; losing your
    reasoning to a summary is not.

    APPENDS and is idempotent — any previous entry is dropped first, so
    re-provisioning cannot stack it and fire two checkpoints per boundary.

    NOTE for the codex half of the bead: codex has no PreCompact event, so this
    injector is Claude-only by construction. Codex is covered by the pre-cycle
    gate in cycle.py instead, not by a second copy of this.
    """
    from .runtime import _precompact_hook     # lazy: provision<->runtime hygiene
    try:
        cfg = json.loads(text)
    except ValueError:
        return text
    if not isinstance(cfg, dict):
        return text
    hooks = cfg.setdefault("hooks", {})
    kept = [e for e in hooks.get("PreCompact", [])
            if not any("shantytown.precompact" in h.get("command", "")
                       for h in e.get("hooks", []))]
    hooks["PreCompact"] = kept + [_precompact_hook(root)]
    return json.dumps(cfg, indent=2) + "\n"


def provision(card: Agent, root, *, secrets=None) -> list[str]:
    """Equip the agent's workspace. Returns the server names it can now reach.

    IDEMPOTENT: re-rendering the same template with the same secrets rewrites the
    same bytes. It is safe on an already-provisioned agent, which matters because
    the caller is a launcher that runs every time an agent starts.
    """
    if not card.workspace:
        return []                       # no workspace elected — nothing to equip
    ws = Path(card.workspace).expanduser()
    if not ws.is_dir():
        raise ProvisionError(
            f"cannot provision {card.name}: workspace {ws} does not exist. "
            f"ensure_workspace runs first, and refuses before this is reached.")

    # Establish the authority before touching any realized kit. A graph outage
    # must never silently fall back to a stale, locally consistent template.
    try:
        manifest = tooling.load(root)
        updates = tooling.instruction_updates(ws, manifest) if manifest is not None else {}
        retired = tooling.retired_links(ws, manifest) if manifest is not None else []
    except tooling.ToolingError as e:
        raise ProvisionError(str(e)) from None
    template = json.dumps({"mcpServers": manifest.mcp}) if manifest is not None else None
    rendered = None
    canonical_consent = None
    if manifest is not None:
        consent_path = provision_dir(root) / CONSENT_TEMPLATE
        if consent_path.is_file():
            canonical_consent = _manifest_consent(consent_path.read_text(), manifest)
        elif card.harness != "codex":
            raise ProvisionError("canonical MCP consent template is missing")
        rendered = json.dumps(_render_manifest(manifest, secrets if secrets is not None else load_secrets(root, template)))
        try:
            json.loads(rendered)
        except ValueError:
            raise ProvisionError("rendered tooling MCP is not valid JSON") from None
        if card.harness == "codex":
            config = _codex_config(card, root)
            if config is None:
                raise ProvisionError("cannot project tooling: Codex config is missing")
            try:
                tomllib.loads(config.read_text())
            except (OSError, ValueError):
                raise ProvisionError("cannot project tooling: Codex config is unreadable") from None
        for name, src in manifest.sources(ws):
            if not (src / "SKILL.md").is_file():
                raise ProvisionError(f"canonical skill source unavailable: {name}")
            for runtime in (SKILLS_RUNTIME, CODEX_SKILLS_RUNTIME):
                link = ws.joinpath(*runtime, name)
                if link.exists() and not link.is_symlink():
                    raise ProvisionError(f"refusing to replace personal skill: {name}")
        for path, text in updates.items():
            if not path.is_file() or path.read_text() != text:
                path.write_text(text)
        for name, src in manifest.sources(ws):
            for runtime in (SKILLS_RUNTIME, CODEX_SKILLS_RUNTIME):
                link = ws.joinpath(*runtime, name)
                link.parent.mkdir(parents=True, exist_ok=True)
                if link.is_symlink():
                    if link.resolve() == src.resolve():
                        continue
                    link.unlink()
                link.symlink_to(src)
        for link in retired:
            link.unlink()
        (ws / tooling.RECEIPT).write_text(json.dumps({"skills": {
            name: str(src) for name, src in manifest.sources(ws)}}) + "\n")

    # SKILLS FIRST, and outside every early return below: a store that defines no
    # MCP template still has a workspace full of skills the runtime cannot see,
    # and the skill links depend on nothing but the clone itself.
    if manifest is None:
        link_skills(ws)
        link_instructions(ws, card.harness)

    # Codex does not read Claude Code's workspace consent file.  Its equivalent
    # self-healing channel is the config.toml selected by the card, so refresh
    # the workspace hooks here on every launch (aegis-jlmqn).  Prefer a per-agent
    # override exactly as the launcher does, then fall back to the role artifact.
    if card.harness == "codex":
        from . import codex as codex_mod
        config = _codex_config(card, root)
        if config is not None:
            before = config.read_text()
            after = codex_mod.with_workspace_hooks(before, card.role, root)
            if after != before:
                config.write_text(after)

    d = provision_dir(root)
    tmpl = d / MCP_TEMPLATE
    if manifest is None and not tmpl.is_file():
        # NO KIT DEFINED is not a HALF kit, and a DELETED kit is neither (GitHub
        # #36). Three states, and the old code collapsed the last two into a note:
        #
        #   no provision dir at all   -> this store wants no MCP servers. Launch.
        #                                Refusing here would break every install
        #                                that is not ours.
        #   dir exists, is EMPTY      -> same: nothing has ever been configured.
        #   dir exists WITH content   -> the store DOES provision (secrets, a
        #                                consent template, anything), and the MCP
        #                                template is GONE. That is a deletion, and
        #                                launching produces the half-equipped agent
        #                                this module's own contract forbids.
        #
        # The third case is a REFUSAL. provision.py opens by saying every rule here
        # is "a refusal rather than a warning"; this was the one path that was not,
        # and it is the one that fires when the whole kit disappears.
        siblings = sorted(x.name for x in d.iterdir()) if d.is_dir() else []
        if siblings:
            raise ProvisionError(
                f"cannot provision {card.name}: {d} exists and holds "
                f"{', '.join(siblings)}, but {MCP_TEMPLATE} is MISSING. This store "
                f"DOES define a kit, so the template was deleted or renamed rather "
                f"than never written — and launching now would create a "
                f"half-equipped agent that looks identical to a healthy one on "
                f"every surface. Restore {tmpl}, or empty {d} to declare that this "
                f"fleet wants no MCP servers.")
        return []

    if rendered is None:
        rendered = render(tmpl.read_text(), secrets if secrets is not None
                          else load_secrets(root))
    target = ws / ".mcp.json"
    # Create privately BEFORE writing secret bytes, then atomically publish.
    # chmod after write leaves a newly created file readable for that window.
    with tempfile.NamedTemporaryFile(mode="w", dir=ws, delete=False) as tmp:
        temporary = Path(tmp.name)
        try:
            tmp.write(rendered)
            tmp.close()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    _project_codex_mcp(card, root, rendered, template)

    consent = d / CONSENT_TEMPLATE
    if consent.is_file():
        out = ws / ".claude"
        out.mkdir(parents=True, exist_ok=True)
        text = canonical_consent if canonical_consent is not None else (render(consent.read_text(), {"SERVERS": ""}) if "${SERVERS}"
                in consent.read_text() else consent.read_text())
        final = _with_precompact_hook(
            _with_stale_hook(
                _with_untracked_hook(
                    _with_capture_hook(_consent_for_role(text, card.role), root),
                    card.role, root),
                card.role, root),
            card.role, root)
        (out / CONSENT_TEMPLATE).write_text(final)

    got = servers_in(target)
    want = sorted(manifest.mcp) if manifest is not None else servers_in_text(tmpl.read_text())
    if sorted(got) != sorted(want):
        raise ProvisionError(
            f"provisioned {card.name} but the written file lists {got}, not the "
            f"template's {want}. Refusing to report a kit we did not verify.")
    if manifest is not None:
        gaps = _manifest_gaps(card, root, manifest, secrets)
        if gaps:
            raise ProvisionError("provisioned tooling failed verification: " + ", ".join(gaps))
    return got
