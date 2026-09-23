# ⚙️ Configuration

Two files and a table, and every default is local. Nothing here needs to be set to run the harness on
the files tracker.

**`<root>/shantytown.toml` — what to bring up, and when the admin may sleep.** The one thing that is
*not* an environment variable, because a mode is a named set of crew plus a policy and that does not
flatten into `KEY=value` without inventing a syntax nobody can read. Absent → the built-in defaults
(`lite`, hibernate off). A copy-pasteable, fully commented example is
[`docs/shantytown.toml.example`](shantytown.toml.example).

```toml
[startup]
mode = "lite"                  # lite = the administrator ALONE. heavy = every card.

[modes.night]                  # your own modes MERGE over the built-ins
crew = ["administrator", "lead"]

[hibernate]                    # when may the admin's stop STAY stopped?
enabled = false                # it can only go quiet when there is nothing to
max_quiet_minutes = 60         # dispatch; Rule Zero overrides it, and says so

[tmux]
socket = "default"             # the fleet's tmux server, DECLARED, never inferred from $TMUX

[host]                         # only on a multi-host fleet
name = "rig-a"                 # which rig this is, as the graph spells it
admission_owner = "rig-a"      # same always-on lock host on EVERY peer

[host.peers.laptop]            # how to reach the OTHER host's st
ssh  = "me@laptop.example"     # `st inbox <agent>` relays here when the agent lives there
root = "/opt/st/.shanty"
```

Those are the tables most deployments touch. The rest — `[fleet]`, `[governor]` and its tiers,
`[session_budget]`, `[hostmem]`, `[harness]`, `[model]`, `[roles.<name>]`, `[precedence.<axis>]`,
`[dream]`, `[crew.<name>]` — are each explained where they appear in the example file.

It is `shantytown.toml`, never `shanty.toml` — `shanty` is a different program on the same PATH, for
the same reason the binary here is `st`. An unknown key is **refused**, not ignored: a silently
dropped key is how an operator comes to believe a policy is in force when it is not.

**`<root>/shantytown.toml` `[env]` and the environment — where the plumbing lives.** Flat values,
read in that order, every one of them also settable as an env var:

Every CLI command loads `[env]` after resolving its root. These values also reach
child processes and readers that use the process environment directly. The CLI
restores the caller's environment on return when invoked as a Python function.

| variable | what it points at | default |
|---|---|---|
| `SHANTY_ROOT` | **which store `st` reads and writes** — the single most consequential setting. Precedence: `--root` > `$SHANTY_ROOT` > a `.shanty` found walking UP from the cwd > this box's pointer (`~/.config/shantytown/root`, written by `st fleet init`) > `cwd/.shanty`. The CLI and the Stop hook resolve it identically. The walk-up cannot help from a directory that is a SIBLING of the store rather than under it — an agent workspace, typically — which is what the pointer is for; with neither, `st` says so before the command runs rather than reporting "no such agent: &lt;your own name&gt;". | discovered; else `./.shanty` |
| `SHANTY_AGENT` | who you are, so `st anchor` needs no argument | — |
| `SHANTY_TEND_SWEEP_BUDGET_S` | seconds a `st fleet tend` pass may spend on the BEST-EFFORT sweeps that follow respawn (aegis-qwadc). Spending it SKIPS the next sweep; it never interrupts one in flight, because a wall-clock kill lands mid-write. Respawn runs before any of them and is never shed. Raise it on a deployment with slow notifiers; a recurring deferral is a slow store, not failing supervision. | `120` |
| `SHANTY_HOSTMEM_FLOOR_GIB` | a ONE-RUN override of `[hostmem] floor_gib`, the physical admission floor (aegis-do672). `0` disables the brake for this invocation. It exists because the alternative an operator reaches for under a brake they need to get past is commenting out the table — which disarms it for everyone and stays disarmed. | the `[hostmem]` table |
| `SHANTY_PANE_MEMORY` | set to `off`/`0`/`false` to launch panes with NO memory ceiling at all. The escape hatch has to exist: a wrong ceiling that cannot be switched off kills every pane, including the one you would fix it from. It is the only setting here that disables `panemem` outright — the two `_GIB` knobs only move the ceiling. | on |
| `SHANTY_PANE_MAX_GIB` | `MemoryMax` on each agent pane's own systemd scope — the KILL line. Sits above measured normal peak (an agent plus a Rust build runs 10-13 GiB) and below the runaway that caused this to exist (31.8 GiB in ten minutes, which tripped host-wide oomd and killed an unrelated agent). Lowering it toward normal peak does not make the host safer: a pane held near its limit reclaims continuously, and that reclaim is itself what raises slice pressure. | `20` |
| `SHANTY_PANE_HIGH_GIB` | `MemoryHigh` on the same scope — the THROTTLE line, where a pane is slowed rather than killed. Defaults to the same value as `SHANTY_PANE_MAX_GIB`, i.e. no throttle band, because a band is only useful when you have measured where the workload actually sits. A value above `MAX` is clamped to it rather than refused: a throttle that can never fire before the kill is meaningless, and a misconfigured pair should still get a working ceiling. | `SHANTY_PANE_MAX_GIB` |
| `SHANTY_PANE_SWAP_MAX` | `MemorySwapMax` on the scope. `0` — no swap — is deliberate and is a HYPOTHESIS, not a settled default: a capped scope with swap to thrash into grinds against its limit and raises pressure on the whole user slice, which is how a correctly-capped pane still got a bystander killed. With no swap it is killed promptly instead. On a host carrying resident swap use that trades a short hard failure for a long soft one, so raise it if panes start dying that used to survive. | `0` |
| `SHANTY_BACKEND` | the deployment's default tracker backend (`files`\|`beads`\|`br`\|`forgejo`) when `--backend` is not given. Set it ONCE (under `[env]` in `<root>/shantytown.toml`, or the env) on a fleet whose plates live in a shared tracker and every plain `st anchor`/`st crew` call — including the status-bar segment and the session picker, which by design pass no flags — resolves the real tracker instead of rendering an empty files plate. An unrecognized value refuses; it never silently means files. Explicit `--backend` always wins. | per-command (`files`; `inbox -d` → `beads`) |
| `SHANTY_BEADS_REPO` | the bead store directory for `--backend beads` when `--repo` is not given (bd's `-C`). Same one-time deployment setting as `SHANTY_BACKEND`; explicit `--repo` wins, and unset falls back to the `.beads` walk-up. | the `.beads` walk-up |
| `SHANTY_BR_REPO` | the SQLite+JSONL store directory for `--backend br` when `--repo` is not given. Explicit `--repo` wins; when unset, the beads repo setting and then the `.beads` walk-up remain compatible migration fallbacks. | migration fallback |
| `SHANTY_BR_BIN` | the `br` executable used by the br tracker backend. Primarily useful for hermetic rehearsals and installations where `br` is not yet on `PATH`. | `br` |
| `SHANTY_BEADS_REPOS_EXTRA` | ADDITIONAL bead stores to read plates and hauls from, beyond `SHANTY_BEADS_REPO`. A `,`/newline/`os.pathsep`-separated list (a JSON array is also accepted). **In `shantytown.toml` write a STRING, not a TOML array** — `deployment_default()` is `str | None`, so an array value is silently dropped and the setting appears to do nothing. Set this when an agent's work lives in a repo's own embedded store: without it that agent can never **self-feed**, because `hauls()` cannot see its queue, so it never advances at its own stop *and* reads as having no work — landing back on the coordinator for a hand dispatch every cycle. Reads are unioned and **raise** if any listed store is unreadable (a partial union is indistinguishable from "no work"). Writes and the inbox still go to the primary store only. A malformed value degrades to single-store rather than failing. | none (single store) |
| `SHANTY_TMUX_SOCKET` | the named tmux server your agents live on. **A `socket` declared under `[tmux]` in `<root>/shantytown.toml` wins over this** — a socket declared in the store is read from there, not from your shell's ambient env, so the answer cannot change with which pane you ran `st` from. | bare tmux |
| `SHANTY_HOST` | WHICH RIG HOST this deployment is, as the graph and the cards spell it (`vati`, `macbookair-stiwi`). **A `name` declared under `[host]` in `<root>/shantytown.toml` wins over this.** Never inferred from `hostname(1)`: the value decides which cards `roles sync` may write and where an ephemeral `st inbox` is relayed (`[host.peers.<name>] ssh/root`). Unset = single-host deployment. | unset |
| `SHANTY_CREEL_ADMISSION_PROBE` | path to Creel's headless `tools/creel-admission.js` reader. `st crew --governor` and `st fleet tend` pass their measured usage snapshot to this executable and display its canonical `controller_line`; they do not implement the controller. Missing Node/probe dependencies render an explicit `advisory unavailable`, never a zero recommendation. | unavailable |
| `SHANTY_BASH_GUARD` | a command emitted as a PreToolUse Bash hook in every role's settings — the deployment's host-policy guard (e.g. blocking another orchestrator's start verbs on a shared host). Claude Code contract: exit 2 blocks, else allows. Unset = no hook emitted; shantytown ships no guard and hardcodes no path. | — |
| `SHANTY_MCP_GUARD` | a command emitted as a PreToolUse hook on matcher `mcp__.*` in every role's settings — the deployment's policy guard for the MCP tool surface, which is otherwise entirely ungoverned (hook matchers match TOOL NAMES, and no edit/Bash matcher covers `mcp__*`). Matchers cannot see arguments, so the guard filters itself. Separate from `SHANTY_BASH_GUARD` because the payload shapes differ — a command string vs a tool name plus an arbitrary argument object. Claude Code contract: exit 2 blocks, else allows. Unset = no hook emitted; shantytown ships no guard and hardcodes no path. | — |
| `SHANTY_CODEX_MCP_APPROVE` | MCP server names (comma- or space-separated) whose tools a **codex** worker may call without approval — rendered as `mcp_servers.<name>.default_tools_approval_mode = "approve"` in that role's `config.toml`. codex judges an MCP tool from its ANNOTATIONS (`destructive_hint`/`open_world_hint`, both defaulting to TRUE when absent), so a server that ships none has EVERY tool — read or write — judged approval-requiring, which under `approval_policy = never` is a flat refusal. A server named here but absent from the config is skipped, never created, and the key is merged into the existing table so a server definition is never replaced. Unset = nothing emitted. `writes` is deliberately not offered: without annotations it narrows nothing while looking as though it does. | — |
| `SHANTY_PANEMEM_LOG` | file the launcher appends one line to for every pane memory-ceiling outcome, applied or refused (default `~/.local/log/panemem.log`). The stderr warning goes to whoever ran the launch, which for an interactive `st agent new <agent>` is a pane scrollback that is gone with the pane — so the reason a pane is unbounded was unrecoverable for most cases. This is the record that outlives the pane it describes. | `~/.local/log/panemem.log` |
| `SHANTY_REMOTE_CONTROL` | whether Claude-harness sessions launch with `--remote-control <agent>` and register through Anthropic's relay for access from claude.ai or the Claude app. Accepts `true`/`false` (and common boolean spellings); an invalid value refuses launch rather than guessing the off-host exposure posture. Codex cards are unchanged: Codex Remote Control is a separate app-server daemon, not a per-session Claude.ai flag. | `true` (compatibility with the original default-on launcher) |
| `SHANTY_STOP_CAPTURE` | a command appended LAST to every role's Stop hook list — the deployment's session-end knowledge-capture hook. Runs after the role's own stop machinery (send/drain/haul/feed-gate) settles. Solicitation etiquette (block-once, markers) is the command's own responsibility. Unset = nothing appended; shantytown ships no capture hook and hardcodes no path. | — |
| `SHANTY_HIERARCHY_FILE` | the hierarchy file `st fleet roles sync` falls back to when the graph cannot be read (`.ttl`\|`.yaml`\|`.json` describing `CrewMember` + `reports_to`). Unset = look for `hierarchy.*` beside the crew root; if that is absent too, `sync` REFUSES rather than projecting an empty crew. Only the ontology-first *default* falls back — an explicit `--from quipu` that cannot reach the graph refuses instead of silently substituting this file. | `<root>/hierarchy.*` if present |
| `SHANTY_SHARED_CHECKOUT_OK` | set to `1` to allow ONE deliberate `git commit`/`rebase`/`merge` in a SHARED project checkout, past the guard `st repo worktree` installs there. Read by the hook, not by `st` — it is the maintenance escape hatch, not a mode. Everyday work belongs in `st repo worktree <repo>`, where index and HEAD are per-agent; the guard exists because a shared checkout's index is shared, so one session's commit can carry another's staged files and its reset can drop the other's commit, with git reporting success to both. Note the guard fires at COMMIT only — `git reset` has no hook and is not guarded, so this is a seatbelt, not a cage. | unset (guard active) |
| `SHANTY_GRAPH_CONTEXT` | how hard `st go` and `st agent cycle` insist on graph context: `advise` (default) warns and RECORDS the gap, `require` refuses a dispatch that names neither a `--quipu-node` nor a `--no-graph-context` reason. Advise is the default because this landed on a fleet whose scripts and crons already call `st go`, and a flag that refuses every existing caller the day it ships is a fleet-stopping change dressed as a measurement — so the ledger measures the habit first and the flip is one line once the callers carry it. Independent of the mode: a node the graph positively does NOT hold is refused either way (a wrong claim, not an absent one), while a graph that cannot be reached never refuses. Read the numbers with `st agent stats --graph`. | `advise` |
| `SHANTY_STALL_MIN` | minutes an idle worker may hold an in_progress item with zero pane/item/shell change before tend acts on the neglected anchor. At this threshold tend NUDGES the agent itself to close-or-release it (self-heal); the coordinator is only escalated to if that goes unanswered. Default 15 — ~30 consecutive unchanged 30s passes: far above prompt-render lag, far below the measured hours-long parked failure. | `15` |
| `SHANTY_STALL_ESCALATE_MIN` | minutes a still-frozen anchor waits AFTER the self-heal nudge before tend escalates it to the coordinator (aegis-es1tt). Default = `SHANTY_STALL_MIN`: the agent gets the same grace to act on the nudge that it got to be noticed. Any progress in the window re-arms the episode, so an agent that acts is never escalated; a decision/blocked-labelled anchor is never nudged or escalated at all. | `SHANTY_STALL_MIN` |
| `SHANTY_GOVERNOR_WAKE` | **set by `st`, not by you** — the window name (`five_hour`\|`seven_day`) on the tend pass a governor reset-wake timer started. tend prints it when a tier is released, so the log says whether the crew came back via the one-shot wake or via the ordinary five-minute pass. Setting it by hand only mislabels a line; it changes no decision, because the timer never decides anything — it schedules a READING, and the reading releases the tier. Unset = an ordinary pass. | unset |
| `SHANTY_DARK_AGENTS` | names (space/comma-separated) Rule Zero and tend must never count feedable — panes another orchestrator keeps respawning with this deployment's worker settings, which carry the stop-event wiring but route nothing here. The launch-stamp ownership gate excludes unstamped agents structurally; this list is the explicit override/belt for named ghosts. | the gastown-dark crew |

What `st` puts INTO an agent's session at launch — read by the agent, not by `st`:

| variable | what it carries |
|---|---|
| `SHANTY_AGENT` | its own name |
| `BOBBIN_ROLE` / `BEADS_ACTOR` | its tree position, and who its tracker writes are attributed to |
| `ST_ROLES` | its **stacked role set**, comma-separated — the trait-presets it holds, which a single tree position cannot express. Carried **opaquely**: `st` passes the set through and draws no conclusion from it. Emitted for every agent, including one whose set is just its tree position, so an agent's view of itself does not depend on whether its card has been migrated. |
| `ST_ROLE_DOMAIN` | which domain a domain-scoped role owns — a per-member parameter, so `keeper` stays reusable and the member supplies what it keeps. Omitted when absent, never emitted empty. |
| `ST_REPORTS_TO` | its lead, for an agent that wants it without re-reading its card. Omitted when absent. |
| `QUIPU_SERVER` | quipu, for `--registry quipu`, `st fleet roles sync`, or `st ops subscribe` | `http://localhost:3030` |
| `SHANTY_ONTO_NS` | the ontology IRI base your graph is keyed under | required for CLI graph access |
| `SHANTY_TOOLING_MANIFEST` | Absolute Quipu entity IRI whose single `rdf:value` is the canonical MCP, skills, and tooling-instructions JSON. Provision and doctor read it fresh; unavailable or ambiguous data refuses rather than falling back. See [harness tooling](harnesses.md#canonical-tooling-from-quipu). | unset (legacy local kit) |
| `SHANTY_ONTO_CREW_CLASS` | the class local-name your graph uses for a crew member, resolved under `SHANTY_ONTO_NS`. Point st at your own vocabulary instead of adopting ours. | `CrewMember` |
| `SHANTY_ONTO_REPORTS_PRED` | the predicate local-name for the supervisor edge. | `reports_to` |
| `SHANTY_ONTO_STATUS_PRED` | the predicate local-name marking a non-live crew member (its ABSENCE means active, so a forgotten mark leaves a retiree visible rather than hiding a live agent). | `crewStatus` |
| `SHANTY_ONTO_ROLE_CLASS` | the class local-name for a declared ROLE — a trait preset your deployment defines rather than one st ships. | `CrewRole` |
| `SHANTY_ONTO_HAS_ROLE_PRED` | the predicate local-name for the member→role edge. Multi-valued: roles STACK on one agent. | `hasRole` |
| `SHANTY_ONTO_HOST_PRED` | the predicate local-name for the member→host edge (which rig host a member runs on). Its ABSENCE on a member reads as UNSCOPED, never as "this host"; once any member is placed, `roles sync` projects only the members placed on the declared local host (aegis-5du1bz). | `runsOn` |
| `SHANTY_ONTO_ROLE_NAME_PRED` | the predicate local-name holding a role's own name. | `crewRoleName` |
| `SHANTY_ONTO_TRAIT_PREFIX` | the shared prefix of the trait-axis predicates (`traitAttachment`, `traitScope`, …). One knob for the convention, not six for the axes. | `trait` |
| `SHANTY_ONTO_TRAIT_VALUE_CLASS` | the class local-name of the rows that RANK trait values, so a stacked role set with a conflicting single-valued axis resolves from declared data instead of a tie-break in code. | `TraitValue` |
| `BOBBIN_SERVER` | bobbin, for `st repo context` | `http://localhost:8080` |
| `SHANTY_RANKER` | `policy` to weight the admin workflow by Hank blast radius; else rule-based | — |
| `SHANTY_FORGEJO_URL` | a self-hosted forge: `st ops doctor`'s release checks, and the base URL for `--backend forgejo` (issues as work items; pair with `SHANTY_FORGEJO_TOKEN` and `--repo owner/name`) | `http://localhost:3000` |
| `SHANTY_FORGEJO_TOKEN` | API token for `--backend forgejo` (issue read/write on the repo) | — |
| `SHANTY_REACTOR_URL` | reactor, if you use it as an event source | `http://localhost:8075` |
| `SHANTY_CANONICAL_SOURCE` | the checkout a fleet deploy must be built from, for `st ops doctor`'s self-check. Unset and not in a git checkout → the check is `CANNOT_TELL`, never OK. | the MAIN working tree of the running package's checkout (a linked worktree resolves to its primary, never itself) |
| `SHANTY_DEFER_MAX_AGE_S` | ceiling on how long a stop event may be held back because its sender is still mid-flight. The defer gate measures "busy" at the coordinator's drain, so an agent that stops and immediately takes the next item is busy at every later drain and its events were deferred indefinitely while the pending count kept reporting them (aegis-d1qko). Past this age the event is delivered regardless. | `1800` (30m) |

⚠️ **`SHANTY_ONTO_NS` is data identity, not cosmetics.** Every triple in a graph is keyed under it.
Pick one per graph, set it before the first write, and never change it — repointing it does not
error, it just stops new facts from joining the old ones.
CLI graph clients refuse before sending a request when the namespace is missing
or is the documentation example. Local commands still work without a namespace.
Library clients outside a CLI invocation retain the warned example fallback.
