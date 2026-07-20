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
import stat
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
    if not (ws / ".claude" / CONSENT_TEMPLATE).is_file():
        gaps.append("mcp-consent")
    return gaps


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

    d = provision_dir(root)
    tmpl = d / MCP_TEMPLATE
    if not tmpl.is_file():
        # NO KIT DEFINED is not a HALF kit. This is the line between the two, and
        # it is deliberate: a store with no template describes a fleet that wants
        # no MCP servers, and refusing to launch there would break every install
        # that is not this one. The caller SAYS SO out loud instead — an absent
        # template must be visible, because deleting it would otherwise silently
        # restore exactly the bug this module exists for.
        return []

    rendered = render(tmpl.read_text(), secrets if secrets is not None
                      else load_secrets(root))
    target = ws / ".mcp.json"
    target.write_text(rendered)
    # 0600 BEFORE anyone else can read it. The file carries a bearer token; the
    # workspace is a git clone that other tooling walks.
    target.chmod(stat.S_IRUSR | stat.S_IWUSR)

    consent = d / CONSENT_TEMPLATE
    if consent.is_file():
        out = ws / ".claude"
        out.mkdir(parents=True, exist_ok=True)
        (out / CONSENT_TEMPLATE).write_text(
            render(consent.read_text(), {"SERVERS": ""}) if "${SERVERS}"
            in consent.read_text() else consent.read_text())

    got = servers_in(target)
    want = servers_in_text(tmpl.read_text())
    if sorted(got) != sorted(want):
        raise ProvisionError(
            f"provisioned {card.name} but the written file lists {got}, not the "
            f"template's {want}. Refusing to report a kit we did not verify.")
    return got
