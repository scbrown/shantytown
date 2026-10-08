# Local models and OpenCode

Cards select a harness and model independently. Existing Claude and Codex defaults,
role pins, skills and settings remain in place. OpenCode is an additional worker
harness; lead and administrator roles refuse because blocking Stop delivery has
not been established.

Install the desired CLI separately. The adapter was measured with OpenCode
1.18.35, Codex 0.161.0 and Ollama 0.40.1. OpenCode runs with `OPENCODE_CONFIG`
pointing to its emitted JSON file, and a native plugin translates hook payloads.
Tool-hook exit 2, a deny decision, other errors and timeouts prevent the tool call.
A dangerous card opts into OpenCode's `--auto`; explicit deny permissions and the
hook bridge remain active.

## Codex with Ollama

Use a per-agent Codex config override, so changing a local card never changes all
workers sharing the role config. Set these operator-owned keys in that config:

```toml
model_provider = "ollama"
model_context_window = 65536
model_auto_compact_token_limit = 58000
```

Set the card's model to an installed Ollama tag. The built-in `ollama` provider ID
is reserved; do not define a replacement `[model_providers.ollama]` table. An
explicit model is necessary for a native `codex exec --oss` probe, otherwise that
command can select and pull its default model.

The Ollama model's actual context must match the advertised context. For example,
create a shared-weight variant with `PARAMETER num_ctx 65536` in a Modelfile.
Advertising a larger window in the client alone does not change the server.
Keep model residency finite in the server configuration.

## OpenCode with Ollama

Set a worker card's harness to `opencode`, and its model to `ollama/<tag>`.
Keep provider configuration in the emitted role or per-agent config:

```json
{
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {"baseURL": "http://localhost:11434/v1"},
      "models": {
        "local-model:64k": {
          "name": "Local model",
          "limit": {"context": 65536, "output": 2048}
        }
      }
    }
  },
  "permission": {
    "*": "deny",
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
    "skill": "allow"
  }
}
```

Launches carry the exact resolved card/role/fleet model in `SHANTY_MODEL` for
tool and usage provenance. An absent selection clears inherited metadata; it does
not guess which model a provider will choose.

This example scopes a card to reading. Broader tools need an explicit permission
policy; supplying a model is not permission to expand its role.

Canonical MCP entries are projected into the private OpenCode config with headers
kept in the file, and `.agents/skills` and `AGENTS.md` carry the shared skill and
instruction sources. Personal plugins and unrelated settings survive rendering.
A headers-helper transport currently refuses rather than silently dropping auth.
Unsupported bundle events are reported; they are never presented as rendered.

Use `st agent new <card>` and `st inbox <card> '<task>'` through the normal launch
and dispatch seams. Gaming holds gate those seams for every harness. The model
server also needs its own admission and unload mechanism; stopping a client alone
is not proof that GPU memory was released. Subscription-usage governors and gaming
holds are different controls: a local provider need not spend a hosted budget.

## Measure before assigning a crew role

Start with small read-only tasks and known answers. Record valid-result pass rate,
wall time, real tool calls and usage from each harness; unknown usage is not zero.
Measure actual prompt length and GPU memory, including MCP definitions and the
instruction files the CLI really loads. A context failure is not a task result.

For a native OpenCode `run` probe, pass `--dir` explicitly and set `PWD` consistently.
Version 1.18.35 resolves inherited `PWD` before `process.cwd()`, so changing only a
subprocess's working directory can send it to the wrong project. Verify a positive
file-reading control before treating that run as a comparison.

An observed idle callback can notify and request another model turn. It does not
establish blocking Stop semantics or authorize a router role. Test tool admission
with both a permitted side effect and a denied one, and verify the files rather
than relying on the model's report.
