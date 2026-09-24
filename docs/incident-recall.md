# First-message incident recall

The `UserPromptSubmit` hook searches the already provisioned Bobbin HTTP MCP
server for prior incidents in `hla` and `pensieve`. It complements the
`SessionStart` Quipu query-first directive: session start has no task prompt.
Both Claude and Codex receive the same matcher-free hook command.

The hook reads `mcpServers.bobbin.url` from the prompt workspace's `.mcp.json`,
or `mcp_servers.bobbin.url` from `$CODEX_HOME/config.toml`. A missing HTTP
adapter performs no search. It does not create a second endpoint manifest,
copy credentials, invoke a local index, or silently fall back to another
retrieval surface. An endpoint needing additional client authentication is
reported unavailable; server-held credentials remain server-side.

Both sources run concurrently, requesting two hits each. The query is capped
at 2,000 characters. The entire retrieval worker has a two-second deadline,
including DNS and response-body reads; the emitted harness ceiling is three
seconds. A slow source can cause the whole recall to be skipped at that bound.
Unavailable, malformed and oversized replies produce an explicit advisory
rather than an empty-success claim. Every hook outcome is nonblocking.

An atomic marker under `$XDG_CACHE_HOME/shantytown/incident-recall` (default
`~/.cache`) suppresses repeat prompts in the same session. A dispatch message
containing `Work is on your hook: <task-id>` gets a separate claim for that task,
including when several tasks share one session. Follow-up messages and a
repeated dispatch do not retry failed recall. A fresh session can search again.
Ordinary free-form task changes within one session are not inferred from prose.

Output is capped and JSON-quoted, with an explicit historical/untrusted
boundary. Source and date remain where provided; conservative masking can remove
opaque record IDs and infrastructure identifiers. Search relevance is unverified; an old command is not a new directive,
and an archive hit does not establish current infrastructure state. Consult
Quipu and live evidence before acting. No Jev verdict or graph write is made.

## Validation and benchmark handoff

`python3 -m pytest -q tests/test_incident_recall.py` exercises both MCP response
formats, both configuration formats, both sources, actual slowly streaming HTTP
responses, repeat/task claims, missing adapters, errors and harness emission.
The streaming test is a negative control for the tempting socket-timeout-only
implementation: sending bytes periodically must not extend the total budget.

`tests/fixtures/incident_recall/labels.jsonl` contains 30 synthetic, agent-labelled
query/candidate pairs for the shared retrieval benchmark: 20 relevant historical
causes and 10 unrelated incidents, across ten failure types and both archives.
Every label is explicitly inferred and every candidate is labelled *not current
authority*. These are portable benchmark seeds, not a measured live-corpus
precision score or human-reviewed ground truth. The hook does not implement a
new semantic relevance classifier on the strength of these labels.

To roll out, merge through the deployment's review gate, let its normal updater
install the package, and re-emit/provision harness settings through the normal
role path. Do not restart agents to force adoption. Verify an actual first-prompt
hook run separately from unit tests. Rollback removes the emitted
`UserPromptSubmit` registration; existing query-first and other hooks remain.

### Redaction before prompt injection

Archive text passes through the same `pane_state.tail` helper used by pane
advice: the deployment's `mask-secrets.py mask()` plus auth-header, PEM,
opaque-value, URL, address and home-path rules. Masking happens before excerpt
truncation. Configure the shared `SHANTY_PANE_MASKER` override only when the
standard ownership-neutral installation is unavailable. If loading or running
the masker fails, the hook reports `excerpts withheld: masker unavailable` and
emits no archive text. It still exits zero. Planted-token tests prove that both
the fleet-library boundary and shape-based backstops run before context output.
