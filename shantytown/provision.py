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
import stat
import tomllib
from pathlib import Path

from .protocols import Agent


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


def load_secrets(root) -> dict:
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
    for k in list(out) + _needed_names(root):
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
    return sorted((data.get("mcpServers") or data).keys())


def expected_servers(root) -> list[str]:
    """What a fully-equipped agent has, per the template. The comparison target
    for `st new`'s claim and for tend's gap report."""
    try:
        return servers_in_text((provision_dir(root) / MCP_TEMPLATE).read_text())
    except OSError:
        return []


def servers_in_text(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return sorted((data.get("mcpServers") or data).keys())


def _skill_sources(ws: Path) -> list[Path]:
    """The workspace's git-tracked skills — a directory with a SKILL.md in it.
    Anything else under skills/ (a README, a scratch dir) is not a skill."""
    try:
        return sorted(p for p in (ws / SKILLS_SRC).iterdir()
                      if (p / "SKILL.md").is_file())
    except OSError:
        return []                              # no skills/ is not an error


def skills_linked(ws) -> list[str]:
    """The skill names the RUNTIME can actually load, measured. Pure read.

    Same discipline as servers_in: not "the directory exists" but "these names
    resolve, through a symlink, to a real SKILL.md". Every green signal in the
    original bug (files present, well-formed, committed, pulled) was true of a
    fleet loading ZERO skills — presence was never the question.
    """
    ws = Path(ws).expanduser()
    dst = ws.joinpath(*SKILLS_RUNTIME)
    out = []
    for src in _skill_sources(ws):
        link = dst / src.name
        if (link.is_symlink() and os.readlink(link) == str(src)
                and (link / "SKILL.md").is_file()):
            out.append(src.name)
    return out


def codex_skills_linked(ws) -> list[str]:
    """The same source skills, realized at Codex's documented repo location."""
    ws = Path(ws).expanduser()
    dst = ws.joinpath(*CODEX_SKILLS_RUNTIME)
    return [src.name for src in _skill_sources(ws)
            if (dst / src.name).is_symlink()
            and os.readlink(dst / src.name) == str(src)
            and (dst / src.name / "SKILL.md").is_file()]


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


def _codex_servers(servers: dict, templates: dict | None = None) -> dict:
    """Translate Claude's declarative MCP entries into Codex config values."""
    out = {}
    templates = templates or {}
    for name, raw in servers.items():
        spec = dict(raw)
        spec.pop("type", None)
        if "headers" in spec:
            headers = dict(spec.pop("headers"))
            template_headers = templates.get(name, {}).get("headers", {})
            template_auth = template_headers.get("Authorization", "")
            match = re.fullmatch(r"Bearer \$\{([A-Z0-9_]+)\}", template_auth)
            if match:
                spec["bearer_token_env_var"] = match.group(1)
                headers.pop("Authorization", None)
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


def _project_codex_mcp(card: Agent, root, rendered: str) -> None:
    if card.harness != "codex":
        return
    config = _codex_config(card, root)
    if config is None:
        raise ProvisionError(f"cannot project MCP kit for {card.name}: no Codex "
                             "config.toml exists for the agent or its role")
    from . import codex as codex_mod
    data = json.loads(rendered)
    template_data = json.loads((provision_dir(root) / MCP_TEMPLATE).read_text())
    servers = _codex_servers(data.get("mcpServers") or data,
                             template_data.get("mcpServers") or template_data)
    config.write_text(codex_mod.render({"mcp_servers": servers}, config.read_text()))


def missing_kit(card: Agent, root) -> list[str]:
    """What this agent's workspace LACKS, by name. Empty = fully equipped.

    Cheap enough to run on every supervision pass, which is the point: nothing in
    the tier reported this difference, so five agents carried it for a night.
    """
    if not card.workspace:
        return []                                  # no workspace: not our claim
    ws = Path(card.workspace).expanduser()
    if not ws.is_dir():
        return ["workspace"]
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
        if not (ws / "AGENTS.md").is_symlink() or os.readlink(ws / "AGENTS.md") != "CLAUDE.md":
            gaps.append("instructions(AGENTS.md)")
    return gaps


def uniformity_report(cards, root) -> tuple[str, bool]:
    """Doctor's harness-neutral realization check; manifest data is discovered."""
    rows = []
    broken = False
    for card in sorted(cards, key=lambda c: c.name):
        if card.retired or not card.workspace:
            continue
        gaps = missing_kit(card, root)
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
    from .runtime import _capture_cmd  # lazy: provision<->runtime import hygiene
    try:
        cfg = json.loads(text)
    except ValueError:
        return text
    if not isinstance(cfg, dict):
        return text
    hooks = cfg.setdefault("hooks", {})
    hooks["PostToolUse"] = [{"matcher": ".*", "hooks": [_capture_cmd(root)]}]
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

    # SKILLS FIRST, and outside every early return below: a store that defines no
    # MCP template still has a workspace full of skills the runtime cannot see,
    # and the skill links depend on nothing but the clone itself.
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
    if not tmpl.is_file():
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

    rendered = render(tmpl.read_text(), secrets if secrets is not None
                      else load_secrets(root))
    target = ws / ".mcp.json"
    target.write_text(rendered)
    # 0600 BEFORE anyone else can read it. The file carries a bearer token; the
    # workspace is a git clone that other tooling walks.
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    _project_codex_mcp(card, root, rendered)

    consent = d / CONSENT_TEMPLATE
    if consent.is_file():
        out = ws / ".claude"
        out.mkdir(parents=True, exist_ok=True)
        text = (render(consent.read_text(), {"SERVERS": ""}) if "${SERVERS}"
                in consent.read_text() else consent.read_text())
        final = _with_stale_hook(
            _with_untracked_hook(
                _with_capture_hook(_consent_for_role(text, card.role), root),
                card.role, root),
            card.role, root)
        (out / CONSENT_TEMPLATE).write_text(final)

    got = servers_in(target)
    want = servers_in_text(tmpl.read_text())
    if sorted(got) != sorted(want):
        raise ProvisionError(
            f"provisioned {card.name} but the written file lists {got}, not the "
            f"template's {want}. Refusing to report a kit we did not verify.")
    return got
