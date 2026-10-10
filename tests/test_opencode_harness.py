from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from shantytown.harness import get
from shantytown.opencode import bridge_options, project_mcp
from shantytown.protocols import Agent
from shantytown.runtime import CapabilityError, require_capability


def test_config_is_kept_and_governed_commands_are_readable(tmp_path):
    program = get("opencode")
    settings = program.settings("worker", root=tmp_path)
    text = program.render(settings, json.dumps({"model":"custom/model",
                                               "plugin":["personal-plugin"]}))
    config = json.loads(text)
    assert config["model"] == "custom/model"
    assert "personal-plugin" in config["plugin"]
    assert program.read_stop_directions(text) == {"send"}
    assert bridge_options(text) is not None
    assert program.render(settings, text) == text


def test_router_roles_refuse_unproven_stop_capability():
    with pytest.raises(CapabilityError, match="blocking"):
        require_capability(get("opencode"), Agent("reviewer", role="lead"))


def test_launch_pointer_and_paths_with_spaces(tmp_path):
    p=tmp_path / "space here" / "opencode.json"
    program=get("opencode")
    launch=program.launch(Agent("local", harness="opencode", model="local/model",
                               workspace="/workspace with spaces"), str(p), root=tmp_path)
    assert program.carries_settings(launch, str(p))
    assert program.settings_in_cmdline(launch) == str(p.resolve())
    assert "--model local/model" in launch
    assert "--auto" not in launch
    assert "--auto" in program.launch(Agent("local", dangerous=True), str(p))


def test_unknown_and_missing_bridge_are_not_reported_wired():
    program=get("opencode")
    assert program.read_bash_guard("{}") is None
    assert program.read_stop_directions("broken") is None


def test_per_agent_override_uses_its_own_atomic_bridge(tmp_path):
    program=get("opencode")
    path=tmp_path / "settings" / program.agent_settings_name("local")
    path.parent.mkdir(parents=True)
    path.write_text(program.render(program.settings("worker",root=tmp_path)))
    program.provision(str(path),root=tmp_path)
    config=json.loads(path.read_text())
    plugin=config["plugin"][0][0]
    assert plugin == (path.parent / "shantytown-bridge.js").as_uri()
    assert (path.parent / "shantytown-bridge.js").is_file()
    assert (path.stat().st_mode & 0o777)==0o600


def test_mcp_projection_is_private_and_removes_retired_servers(tmp_path):
    program=get("opencode")
    card=Agent("local", harness="opencode")
    path=tmp_path / "settings" / program.settings_name("worker")
    path.parent.mkdir(parents=True)
    path.write_text(program.render(program.settings("worker", root=tmp_path)))
    source={"mcpServers":{"remote":{"type":"http","url":"http://localhost/mcp",
                                   "headers":{"Authorization":"Bearer test-value"}},
                          "local":{"command":"python3","args":["-m","example"]}}}
    project_mcp(card,tmp_path,json.dumps(source),replace=True)
    assert path.stat().st_mode & 0o777 == 0o600
    projected=json.loads(path.read_text())
    assert projected["mcp"]["remote"]["headers"]["Authorization"] == "Bearer test-value"
    assert projected["mcp"]["local"]["command"] == ["python3","-m","example"]
    assert bridge_options(path.read_text()) is not None
    project_mcp(card,tmp_path,json.dumps({"mcpServers":{}}),replace=True)
    assert json.loads(path.read_text())["mcp"] == {}


def test_header_helpers_refuse_instead_of_being_silently_dropped(tmp_path):
    from shantytown.opencode import translate_servers
    with pytest.raises(Exception,match="header helper"):
        translate_servers({"mcpServers":{"direct":{"url":"http://localhost/mcp",
                                                   "headersHelper":"example"}}})


def test_named_credentials_cannot_enter_shared_opencode_config(tmp_path, monkeypatch):
    from shantytown import provision, provision_credentials
    monkeypatch.setattr(provision_credentials,"agent_overrides",lambda root,name:{"TOKEN":"test"})
    card=Agent("local",harness="opencode",workspace=str(tmp_path))
    with pytest.raises(provision.ProvisionError,match="independent per-agent OpenCode"):
        provision.provision(card,tmp_path)


def test_gaming_hold_refuses_new_opencode_before_launch(tmp_path, monkeypatch):
    from shantytown import cli
    from shantytown.files import FilesRegistry
    root=tmp_path / ".shanty"
    FilesRegistry(root / "crew").set(Agent("local", harness="opencode"))

    class Hold:
        held=True
        refusal="test gaming hold"

        def override_lines(self, invocation):
            return []

    monkeypatch.setattr(cli.quiet_time_mod,"read",lambda root:Hold())
    monkeypatch.setattr(cli,"_cmd_new",lambda args:pytest.fail("held card reached launch"))
    assert cli.main(["--root",str(root),"--backend","files","agent","new","local"]) == cli.REFUSED


@pytest.mark.skipif(shutil.which("node") is None, reason="native bridge requires Node/Bun")
@pytest.mark.parametrize("failure", ["exit2", "json-deny", "error"])
def test_native_bridge_blocks_before_side_effect_with_positive_control(tmp_path, failure):
    from shantytown.opencode_bridge import SOURCE
    (tmp_path / "bridge.mjs").write_text(SOURCE)
    (tmp_path / "guard.py").write_text('''import json,sys
d=json.load(sys.stdin)
assert d["hook_event_name"]=="PreToolUse" and d["tool_name"]=="Bash"
assert d["session_id"]=="session-proof"
if "DENIED" in d["tool_input"]["command"]:
 if sys.argv[1]=="json-deny": print(json.dumps({"hookSpecificOutput":{"permissionDecision":"deny"}}))
 else: sys.exit(2 if sys.argv[1]=="exit2" else 1)
''')
    options={"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{
        "type":"command","command":f"python3 guard.py {failure}"}]}]}}
    (tmp_path / "options.json").write_text(json.dumps(options))
    (tmp_path / "runner.mjs").write_text('''
import plugin from './bridge.mjs';
import {readFileSync,writeFileSync,existsSync} from 'node:fs';
const hooks=await plugin({client:{},directory:process.cwd()},JSON.parse(readFileSync('options.json')));
let denied=false;
await hooks['tool.execute.before']({sessionID:'session-proof',tool:'bash'}, {args:{command:'ALLOWED'}});
writeFileSync('ALLOWED','control');
try {
 await hooks['tool.execute.before']({sessionID:'session-proof',tool:'bash'}, {args:{command:'DENIED'}});
 writeFileSync('DENIED','unsafe');
} catch {denied=true;}
if(!denied || existsSync('DENIED') || !existsSync('ALLOWED'))process.exit(1);
''')
    subprocess.run(["node", "runner.mjs"], cwd=tmp_path, check=True, timeout=15)


@pytest.mark.skipif(shutil.which("node") is None, reason="native bridge requires Node/Bun")
def test_stop_runs_whole_group_and_continuation_is_not_a_capability_claim(tmp_path):
    from shantytown.opencode_bridge import SOURCE
    (tmp_path / "bridge.mjs").write_text(SOURCE)
    (tmp_path / "stop.py").write_text('''import json,sys
d=json.load(sys.stdin)
assert d["hook_event_name"]=="Stop"
print(json.dumps({"decision":"block","reason":"required followup"}))
''')
    options={"hooks":{"Stop":[{"hooks":[
        {"type":"command","command":"python3 stop.py"},
        {"type":"command","command":"printf reached > second-hook"}]}]}}
    (tmp_path / "options.json").write_text(json.dumps(options))
    (tmp_path / "runner.mjs").write_text('''
import plugin from './bridge.mjs';
import {readFileSync,existsSync} from 'node:fs';
let prompt;
const client={session:{prompt:async (request)=>{prompt=request;}}};
const h=await plugin({client,directory:process.cwd()},JSON.parse(readFileSync('options.json')));
await h['chat.message']({sessionID:'session-proof',model:{providerID:'local',id:'model'}},{parts:[]});
await h.event({event:{type:'session.idle',properties:{sessionID:'session-proof'}}});
if(!existsSync('second-hook') || prompt.path.id!=='session-proof' ||
   prompt.body.parts[0].text!=='required followup' || prompt.body.model.providerID!=='local')process.exit(1);
''')
    subprocess.run(["node", "runner.mjs"], cwd=tmp_path, check=True, timeout=15)
    assert not get("opencode").hooks(Agent("worker")).blocking_stop


@pytest.mark.skipif(shutil.which("node") is None, reason="native bridge requires Node/Bun")
def test_prompt_admission_context_and_unmeasured_patch_refusal(tmp_path):
    from shantytown.opencode_bridge import SOURCE
    (tmp_path / "bridge.mjs").write_text(SOURCE)
    (tmp_path / "note.py").write_text('''import json,sys
p=json.load(sys.stdin)
if p.get("prompt")=="deny": print(json.dumps({"decision":"block","reason":"prompt denied"}))
else: print(json.dumps({"hookSpecificOutput":{"additionalContext":"measured context"}}))
''')
    options={"hooks":{"UserPromptSubmit":[{"hooks":[{
        "type":"command","command":"python3 note.py"}]}]}}
    (tmp_path / "options.json").write_text(json.dumps(options))
    (tmp_path / "runner.mjs").write_text('''
import plugin from './bridge.mjs';
import {readFileSync} from 'node:fs';
const h=await plugin({client:{},directory:process.cwd()},JSON.parse(readFileSync('options.json')));
await h['chat.message']({sessionID:'proof'},{parts:[{type:'text',text:'allow'}]});
const output={system:['operator context']};
await h['experimental.chat.system.transform']({sessionID:'proof'},output);
if(output.system.join('|')!=='operator context|measured context')process.exit(1);
let denied=false;
try {await h['chat.message']({sessionID:'proof'},{parts:[{type:'text',text:'deny'}]});}
catch {denied=true;}
if(!denied)process.exit(1);
denied=false;
try {await h['tool.execute.before']({sessionID:'proof',tool:'apply_patch'},{args:{patchText:'unsafe'}});}
catch {denied=true;}
if(!denied)process.exit(1);
''')
    subprocess.run(["node", "runner.mjs"], cwd=tmp_path, check=True, timeout=15)


@pytest.mark.skipif(shutil.which("node") is None, reason="native bridge requires Node/Bun")
def test_native_mcp_names_reach_existing_guard_matchers(tmp_path):
    from shantytown.opencode_bridge import SOURCE
    (tmp_path / "bridge.mjs").write_text(SOURCE)
    options={"mcp_servers":["remote"],"hooks":{"PreToolUse":[{
        "matcher":"^mcp__remote__restart$","hooks":[{"type":"command","command":"exit 2"}]}]}}
    (tmp_path / "options.json").write_text(json.dumps(options))
    (tmp_path / "runner.mjs").write_text('''
import plugin from './bridge.mjs';
import {readFileSync} from 'node:fs';
const h=await plugin({client:{},directory:process.cwd()},JSON.parse(readFileSync('options.json')));
await h['tool.execute.before']({sessionID:'proof',tool:'remote_status'},{args:{}});
let denied=false;
try {await h['tool.execute.before']({sessionID:'proof',tool:'remote_restart'},{args:{}});}
catch {denied=true;}
if(!denied)process.exit(1);
''')
    subprocess.run(["node", "runner.mjs"], cwd=tmp_path, check=True, timeout=15)

@pytest.mark.skipif(shutil.which("node") is None, reason="native bridge requires Node/Bun")
@pytest.mark.parametrize("model", ["ollama/fixture", None])
def test_hook_provenance_binds_native_session_and_five_direct_headers(tmp_path, model):
    """Capture actual HTTP headers from hook children; no completeness counter."""
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from shantytown.opencode_bridge import SOURCE

    captured = []

    class Sink(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            captured.append({k: self.headers.get("X-Quipu-" + k.title())
                             for k in ("agent", "harness", "host", "session", "model")})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        (tmp_path / "bridge.mjs").write_text(SOURCE)
        (tmp_path / "writer.py").write_text('''import json,os,urllib.request
payload=json.load(__import__("sys").stdin)
headers={ "X-Quipu-"+key.title():os.environ["QUIPU_"+key.upper()]
          for key in ("agent","harness","host","session","model")
          if os.environ.get("QUIPU_"+key.upper()) }
with urllib.request.urlopen(urllib.request.Request(os.environ["SINK"],data=b"{}",headers=headers)) as response:
 assert response.status==200
''')
        options = {"hooks": {"PreToolUse": [{"hooks": [{
            "type": "command", "command": "python3 writer.py"}]}]}}
        (tmp_path / "options.json").write_text(json.dumps(options))
        (tmp_path / "runner.mjs").write_text('''
import plugin from './bridge.mjs';
import {readFileSync} from 'node:fs';
const h=await plugin({client:{},directory:process.cwd()},JSON.parse(readFileSync('options.json')));
await Promise.all(['ses_native_left','ses_native_right'].map(id =>
 h['tool.execute.before']({sessionID:id,tool:'bash'},{args:{command:'true'}})));
''')
        env = {**os.environ, "SINK": f"http://127.0.0.1:{server.server_port}/proof",
               "SHANTY_AGENT": "opencode-proof", "QUIPU_AGENT": "stale-agent",
               "QUIPU_HARNESS": "codex", "QUIPU_SESSION": "parent-session",
               "QUIPU_MODEL": "parent-model", "QUIPU_HOST": "parent-host",
               "CODEX_SESSION_ID": "parent-codex", "CLAUDE_CODE_SESSION_ID": "parent-claude"}
        env.pop("SHANTY_MODEL", None)
        if model:
            env["SHANTY_MODEL"] = model
        subprocess.run(["node", "runner.mjs"], cwd=tmp_path, env=env, check=True, timeout=15)
        assert len(captured) == 2
        assert {row["session"] for row in captured} == {"ses_native_left", "ses_native_right"}
        import socket
        for row in captured:
            assert row == {"agent": "opencode-proof", "harness": "opencode",
                           "host": socket.gethostname(), "session": row["session"], "model": model}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

@pytest.mark.parametrize("kit", [False, True])
@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("workspace_present", [False, True])
def test_normal_workspace_provision_refreshes_actual_bridge(
        tmp_path, monkeypatch, kit, stale, workspace_present):
    """The launch provisioning entrypoint must refresh bytes without role-set."""
    from shantytown import provision
    from shantytown.opencode import PLUGIN_NAME
    from shantytown.opencode_bridge import SOURCE

    root = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    card = Agent("local", harness="opencode",
                 workspace=str(workspace) if workspace_present else None)
    program = get("opencode")
    config = root / "settings" / program.settings_name(card.role)
    config.parent.mkdir(parents=True)
    config.write_text(program.render(program.settings(card.role, root=root),
                                     json.dumps({"permission": {"edit": "deny"}})))
    bridge = config.parent / PLUGIN_NAME
    if stale:
        bridge.write_text("obsolete plugin with stale provenance")
    monkeypatch.setattr(provision.tooling, "load", lambda root: None)
    if kit:
        template = provision.provision_dir(root) / provision.MCP_TEMPLATE
        template.parent.mkdir(parents=True)
        template.write_text(json.dumps({"mcpServers": {"fixture": {"command": "true"}}}))
    got = provision.provision(card, root, settings_path=str(config))
    assert got == (["fixture"] if kit and workspace_present else [])
    assert bridge.read_text() == SOURCE
    assert bridge.stat().st_mode & 0o777 == 0o600
    assert json.loads(config.read_text())["permission"] == {"edit": "deny"}
    assert json.loads(config.read_text())["plugin"][0][0] == bridge.as_uri()


def test_bridge_refresh_failure_refuses_provisioning(tmp_path, monkeypatch):
    from shantytown import opencode, provision

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "store"
    card = Agent("local", harness="opencode", workspace=str(workspace))
    program = get("opencode")
    config = root / "settings" / program.agent_settings_name(card.name)
    config.parent.mkdir(parents=True)
    config.write_text(program.render(program.settings(card.role, root=root)))
    monkeypatch.setattr(provision.tooling, "load", lambda root: None)

    def denied(*args):
        raise PermissionError("private bridge is unwritable")

    monkeypatch.setattr(opencode, "private_write", denied)
    with pytest.raises(provision.ProvisionError, match="cannot refresh OpenCode bridge"):
        provision.provision(card, root, settings_path=str(config))
