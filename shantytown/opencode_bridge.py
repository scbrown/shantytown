"""In-file hook commands stay data; native tool admission runs them before tools."""

SOURCE = r'''
import { spawn } from "node:child_process";
import { hostname } from "node:os";

export default async ({ client, directory }, options = {}) => {
  const hooks = options.hooks || {};
  const started = new Set();
  const stopActive = new Set();
  const context = new Map();
  const prompted = new Set();
  const models = new Map();
  const rememberModel = (id, model) => {
    const modelID = model?.modelID || model?.id;
    if (id && model?.providerID && modelID) models.set(id,{providerID:model.providerID,modelID});
  };
  const names = {bash:"Bash", read:"Read", write:"Write", edit:"Edit",
                 multiedit:"MultiEdit", glob:"Glob", grep:"Grep"};
  const canonical = (tool) => {
    if (names[tool]) return names[tool];
    const candidates = (options.mcp_servers || []).filter(name =>
      tool.startsWith(name.replace(/[^a-zA-Z0-9_-]/g,"_") + "_"));
    if (candidates.length > 1) throw new Error("ambiguous MCP tool identity");
    if (candidates.length === 1) {
      const name = candidates[0];
      return "mcp__" + name + "__" + tool.slice(name.replace(/[^a-zA-Z0-9_-]/g,"_").length + 1);
    }
    return tool;
  };
  async function run(command, payload, timeout) {
    return await new Promise((resolve, reject) => {
      // Each dispatch owns its native session: a shared process environment
      // would cross-credit concurrent sessions or inherit the launching harness.
      const env = {...process.env, QUIPU_HARNESS:"opencode", QUIPU_HOST:hostname()};
      for (const key of ["QUIPU_AGENT","QUIPU_SESSION","QUIPU_MODEL","OPENCODE_SESSION_ID",
                         "CLAUDECODE","CLAUDE_CODE_SESSION_ID","CODEX_HOME",
                         "CODEX_SESSION_ID","CODEX_THREAD_ID"]) delete env[key];
      if(process.env.SHANTY_AGENT)env.QUIPU_AGENT=process.env.SHANTY_AGENT;
      if(process.env.SHANTY_MODEL)env.QUIPU_MODEL=process.env.SHANTY_MODEL;
      if(payload.session_id) {
        env.QUIPU_SESSION=payload.session_id;
        env.OPENCODE_SESSION_ID=payload.session_id;
      }
      const child = spawn("/bin/bash", ["-lc", command],
        { cwd: directory, env, detached:true, stdio:["pipe","pipe","pipe"] });
      const kill = () => {
        try {if(child.pid)process.kill(-child.pid,"SIGKILL");}
        catch {child.kill("SIGKILL");}
      };
      let stdout = "", stderr = "", size = 0;
      const timer = setTimeout(() => {kill();reject(new Error("hook timeout"));},
                               (timeout || 30) * 1000);
      child.on("error", (e) => {clearTimeout(timer);reject(e);});
      child.stdout.on("data", (b) => {size += b.length;if(size>1048576){kill();return;}
                                      stdout += b.toString();});
      child.stderr.on("data", (b) => {if(stderr.length<65536)stderr += b.toString();});
      child.on("close", (code) => {clearTimeout(timer);resolve({code,stdout,stderr});});
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify(payload));
    });
  }
  async function dispatch(event, sessionID, tool = "", args = {}, extra = {}) {
    const payload = {hook_event_name:event, session_id:sessionID, cwd:directory,
                     tool_name:canonical(tool), tool_input:args,
                     stop_hook_active:stopActive.has(sessionID), ...extra};
    const reasons = [];
    for (const group of hooks[event] || []) {
      if (group.matcher && !(new RegExp(group.matcher)).test(payload.tool_name)) continue;
      for (const hook of group.hooks || []) {
        if (hook.type !== "command") throw new Error("unsupported hook handler");
        const result = await run(hook.command, payload, hook.timeout);
        let answer;
        try {answer=JSON.parse(result.stdout);} catch {answer={};}
        const denied = result.code === 2 || answer.decision === "block" ||
          answer.hookSpecificOutput?.permissionDecision === "deny";
        if (event === "PreToolUse" && (denied || result.code !== 0)) {
          throw new Error(answer.reason || answer.hookSpecificOutput?.permissionDecisionReason ||
                          result.stderr || "tool denied by hook");
        }
        if (denied) reasons.push(answer.reason || result.stderr || "Stop hook requests continuation");
        const note = answer.hookSpecificOutput?.additionalContext || answer.systemMessage;
        if (typeof note === "string" && note.trim()) {
          const previous = context.get(sessionID) || [];
          context.set(sessionID, [...previous, note]);
        }
      }
    }
    return reasons;
  }
  return {
    "chat.message": async (input, output) => {
      const prompt = output.parts.filter(p => p.type === "text").map(p => p.text).join("\n");
      const reasons = await dispatch("UserPromptSubmit", input.sessionID, "", {}, {prompt});
      if (reasons.length) throw new Error(reasons.join("\n"));
      prompted.add(input.sessionID);
      rememberModel(input.sessionID,input.model);
    },
    "experimental.chat.system.transform": async (input, output) => {
      rememberModel(input.sessionID,input.model);
      const notes = context.get(input.sessionID) || [];
      output.system.push(...notes);
    },
    "tool.execute.before": async (input, output) => {
      if (input.tool === "apply_patch") throw new Error("apply_patch mapping is unmeasured; tool refused");
      await dispatch("PreToolUse", input.sessionID, input.tool,
                     {...output.args, file_path:output.args.filePath,
                      old_string:output.args.oldString, new_string:output.args.newString,
                      edits:output.args.edits?.map(edit => ({...edit,
                        old_string:edit.oldString, new_string:edit.newString}))});
    },
    "tool.execute.after": async (input, output) => {
      const reasons = await dispatch("PostToolUse", input.sessionID, input.tool, input.args,
                                     {tool_response:output});
      if (reasons.length) context.set(input.sessionID, [...(context.get(input.sessionID) || []), ...reasons]);
    },
    event: async ({event}) => {
      const id = event.properties?.sessionID || event.properties?.info?.id;
      if (!id) return;
      if (event.type === "session.created" && !started.has(id)) {
        started.add(id); await dispatch("SessionStart", id);
      }
      if (event.type === "session.idle" && prompted.has(id)) {
        const reasons = await dispatch("Stop", id);
        if (reasons.length && !stopActive.has(id)) {
          const model = models.get(id);
          if (!model) throw new Error("Stop continuation model unknown; provider selection refused");
          stopActive.add(id);
          try {
            await client.session.prompt({path:{id},body:{model,parts:[{type:"text",text:reasons.join("\n")}]}});
          } finally {stopActive.delete(id);}
        }
      }
    },
  };
};
'''
