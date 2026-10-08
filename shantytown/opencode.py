"""OpenCode v1 adapter. Native tool interception measured on 1.18.35.

The plugin receives options from a [module, options] config entry (upstream
ConfigPluginV1.Spec and applyPlugin). Stop is notification/continuation only;
until lifecycle enforcement is measured this adapter must not host routers.
"""
from __future__ import annotations

import json
import shlex
import tempfile
from pathlib import Path

PLUGIN_NAME = "shantytown-bridge.js"


def private_write(path, text):
    target = Path(path).resolve()
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(text)
            f.close()
            temporary.chmod(0o600)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def translate_servers(servers):
    from .provision import ProvisionError
    projected = {}
    for name, raw in servers.get("mcpServers", servers).items():
        if raw.get("headersHelper"):
            raise ProvisionError(f"OpenCode header helper is unsupported: {name}")
        if "url" in raw:
            spec = {"type": "remote", "url": raw["url"], "enabled": True}
            if raw.get("headers"):
                spec["headers"] = raw["headers"]
        elif "command" in raw:
            spec = {"type": "local", "command": [raw["command"], *raw.get("args", [])],
                    "enabled": True}
            if raw.get("env"):
                spec["environment"] = raw["env"]
        else:
            raise ProvisionError(f"unsupported OpenCode MCP entry: {name}")
        projected[name] = spec
    return projected


def config_path(card, root):
    from .harness import get
    program = get("opencode")
    directory = Path(root) / "settings"
    agent = directory / program.agent_settings_name(card.name)
    return agent if agent.is_file() else directory / program.settings_name(card.role)


def project_mcp(card, root, rendered, replace=False):
    """Project canonical remote/local MCP entries into a private config file."""
    from .provision import ProvisionError
    path = config_path(card, root)
    if not path.is_file():
        raise ProvisionError("OpenCode config is missing")
    config = json.loads(path.read_text())
    projected = translate_servers(json.loads(rendered))
    config["mcp"] = projected if replace else {**config.get("mcp", {}), **projected}
    for plugin in config.get("plugin", []):
        if isinstance(plugin, list) and len(plugin) == 2 and str(plugin[0]).endswith(PLUGIN_NAME):
            plugin[1]["mcp_servers"] = sorted(config["mcp"])
    private_write(path, json.dumps(config, indent=2) + "\n")


def bridge_options(text: str) -> dict | None:
    try:
        config = json.loads(text)
        entries = [p for p in config.get("plugin", [])
                   if isinstance(p, list) and len(p) == 2
                   and str(p[0]).endswith(PLUGIN_NAME)]
        if len(entries) != 1 or not isinstance(entries[0][1], dict):
            return None
        return entries[0][1]
    except (ValueError, TypeError, AttributeError):
        return None


def make_harness(base):
    class OpenCodeHarness(base):
        name = "opencode"
        settings_env_var = "OPENCODE_CONFIG"
        picker_markers = ()
        # Captured from the idle 1.18.35 TUI, not guessed from documentation.
        ready_patterns = (r"(?m)^\s*tab agents\s+ctrl\+p commands\s*$",)
        stranded_markers = ()
        clear_command = None
        bypass_markers = ()

        def settings_name(self, role):
            return f"opencode/{role}/opencode.json"

        def agent_settings_name(self, agent):
            return f"opencode/agent-{agent}/opencode.json"

        def settings(self, role, root=None):
            from .hook_bundles import apply as apply_bundles
            from .runtime import claude_settings_for_role
            # Shared command contracts; the bridge maps observed native tools.
            hooks = claude_settings_for_role(role, root=root).get("hooks", {})
            data = apply_bundles({"hooks": hooks}, role, "opencode", root)
            location = Path(root or ".") / "settings" / self.settings_name(role)
            plugin = location.resolve().parent / PLUGIN_NAME
            return {"plugin": [[plugin.as_uri(), data]],
                    "permission": {"*": "ask"}}

        def render(self, settings, existing="", root=None):
            current = json.loads(existing) if existing.strip() else {}
            if not isinstance(current, dict):
                raise TypeError("OpenCode config must be an object")
            own = [p for p in settings.get("plugin", [])]
            user = [p for p in current.get("plugin", [])
                    if not (isinstance(p, list) and len(p) == 2
                            and str(p[0]).endswith(PLUGIN_NAME))]
            current.setdefault("permission", settings.get("permission", {}))
            current["plugin"] = user + own
            return json.dumps(current, indent=2) + "\n"

        def launch(self, card, settings_path, root=None):
            from .harness import Unsupported, resolve_model
            if card.chrome:
                raise Unsupported("OpenCode has no measured browser integration")
            config = Path(settings_path).resolve()
            env = {"OPENCODE_CONFIG": str(config), "SHANTY_AGENT": card.name,
                   "BOBBIN_ROLE": card.role, "BEADS_ACTOR": card.name,
                   "ST_ROLES": ",".join(card.effective_roles())}
            if root:
                env["SHANTY_ROOT"] = str(Path(root).resolve())
            if card.reports_to:
                env["ST_REPORTS_TO"] = card.reports_to
            if card.domain:
                env["ST_ROLE_DOMAIN"] = card.domain
            line = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
            line += " opencode"
            if card.dangerous:
                # Observed 1.18.35 CLI flag. Explicit deny rules still apply.
                line += " --auto"
            model = resolve_model(card, root)
            if model:
                line += " --model " + shlex.quote(model)
            # No global permission bypass: explicit per-tool permissions remain.
            if card.workspace:
                line = "cd " + shlex.quote(card.workspace) + " && " + line
            return line

        def settings_in_cmdline(self, cmdline):
            for token in shlex.split(cmdline):
                if token.startswith("OPENCODE_CONFIG="):
                    return token.split("=", 1)[1]
            return None

        def carries_settings(self, launch, settings_path):
            return self.settings_in_cmdline(launch) == str(Path(settings_path).resolve())

        def _translated(self, text):
            options = bridge_options(text)
            return json.dumps(options) if options is not None else None

        def read_stop_directions(self, text):
            value = self._translated(text)
            return super().read_stop_directions(value) if value is not None else None

        def read_bash_guard(self, text):
            value = self._translated(text)
            return super().read_bash_guard(value) if value is not None else None

        def read_pre_edit_guard(self, text):
            value = self._translated(text)
            return super().read_pre_edit_guard(value) if value is not None else None

        def read_usage(self, session_path):
            return None  # Native session usage parser not yet observed.

        def hooks(self, card):
            from .runtime import HookSpec
            return HookSpec(blocking_stop=False)

        def provision(self, settings_path, root=None, workspaces=()):
            from .opencode_bridge import SOURCE
            config_path = Path(settings_path).resolve()
            path = config_path.parent / PLUGIN_NAME
            private_write(path, SOURCE)
            config = json.loads(config_path.read_text())
            changed = False
            for plugin in config.get("plugin", []):
                if (isinstance(plugin, list) and len(plugin) == 2
                        and str(plugin[0]).endswith(PLUGIN_NAME) and plugin[0] != path.as_uri()):
                    plugin[0] = path.as_uri()
                    changed = True
            if changed:
                private_write(config_path, json.dumps(config, indent=2) + "\n")
            return []

    return OpenCodeHarness()
