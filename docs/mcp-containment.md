# MCP containment

On Linux with cgroup v2 and a recent user systemd, stdio MCP servers can run
in bounded children of their agent pane. Enable an explicit launch cohort in
`<store>/provision/mcp-limits.json`:

```json
{"enabled": true, "agents": ["example"], "cpu_percent": 200,
 "memory_bytes": 2147483648, "idle_seconds": 300}
```

Provisioning projects the same commands into Claude and Codex configurations.
HTTP servers remain remote. Before starting a selected agent, st waits for its
private pane scope and checks ownership of every process including descendants.
It installs only that scope's runtime `50-st-mcp.conf` drop-in to delegate CPU,
memory and PIDs; `Delegate` cannot be changed with `set-property` on an existing
scope. It moves the pane population to `st-runtime` before enabling controllers.
An unavailable controller or ambiguous owner refuses launch; there is no
unbounded fallback.

Each MCP wrapper creates a sibling of `st-runtime` **inside the agent scope**,
with CPU quota 200%, weight 10, memory 2 GiB, swap zero, group OOM kill and 256
PIDs. Parent memory and gaming caps still constrain the entire subtree. The
wrapper remains outside the child so it can clean up after a server OOM.

The relay forwards opaque protocol bytes with bounded buffers. Client EOF,
termination or no client input for the configured idle interval kills the whole
child cgroup, including detached browser descendants. Inactivity is measured on
client bytes, not browser CPU or server output: a single tool operation lasting
longer than this interval is also terminated. The client must reconnect/restart
its MCP server after timeout. Choose the interval with that constraint in mind.

To disable future launches, remove an agent from the cohort and reprovision it.
Existing wrappers retain their limits until they exit. Runtime delegation and
empty `st-runtime` disappear with the pane scope/reboot; do not move a running
agent back into a parent with active subtree controllers.

## Verification

Focused tests cover projection isolation, foreign descendants, ancestor scope
lookup, missing controllers and refusal while a new pane still shares its
launcher's scope. A live rehearsal on the author's own pane verified all five
child limits, byte-for-byte echo, ownership after delegation, and idle cleanup
of a detached sleeping grandchild. No CPU or memory stress load was used.
