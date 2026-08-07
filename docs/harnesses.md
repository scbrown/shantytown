# Harnesses — running more than one agent program

> Claude Code is **a** harness, not the shape of the world. Two ship: `claude` (the default) and
> `codex`. This is the setup and reference doc for both — what to put on a card, what to put in
> config, what lands on disk, and what does **not** work yet.
>
> For *why* the interface looks like this, see [`adapters.md`](adapters.md). This doc is the
> operator's half.

## The one-minute version

```toml
# <root>/shantytown.toml
[harness]
default = "codex"            # every card that does not say otherwise

[harness.by_role]
lead = "claude"              # …except these roles
administrator = "claude"
```

```json
// or per card — <root>/crew/<agent>.json
{ "role": "worker", "harness": "codex", "pane": "st-dearing" }
```

**Most specific wins: card → role → fleet → `claude`.** A card that names its program is never
moved by a config written afterwards; the table answers for the silent. A resolved default is
never written back onto the card — that would be a claim nobody made, and it would outlive the
config being changed back.

### …and which MODEL that program runs

`[model]` is the same table one axis over — harness picks the **program**, model picks what that
program runs — with the same two levels, the same precedence, and a card still beating both:

```toml
[model]
default = "gpt-5.6-luna"       # the fleet's model

[model.by_role]
administrator = "gpt-5.6-terra"   # …except these roles
```

Before this table, `model` was a **card field only**: a deployment that wanted its administrator on
the top model and its workers on a cheap one had to stamp the slug onto every card by hand, and
every card added afterwards silently reverted to whatever the harness defaults to. That is the same
"reads as configured, isn't" failure the card field itself was added to fix.

**Two differences from `[harness]`, both deliberate:**

- **The fallback is `None`, not a name.** `claude` is a sane fallback program; there is no sane
  fallback *model*. Saying nothing omits `--model` and lets the harness apply its own default —
  which is exactly what every deployment that never writes this table already gets.
- **The slug is not validated against a list.** Model names are the *provider's* vocabulary and
  rotate without a shantytown release, so a build-time allowlist would refuse a model newer than
  your installed version — wrong in the direction that blocks work. A bad slug still fails loudly,
  at launch, from the harness itself. The **role** half *is* validated, because an unknown role
  there fails the other way: it applies to nobody and reads as applied.

`st anchor <agent> --harness` prints the **resolved** answer, which is what the agent will
actually run.

---

## Setting up codex on a host

### 1. Log in **before** you emit

```bash
codex login          # or: printenv OPENAI_API_KEY | codex login --with-api-key
```

Order matters, and this is the step that bites. `CODEX_HOME` is not just where `config.toml`
lives — it is also where codex keeps `auth.json`. shantytown points each role at a home *inside
the store*, so `st roles set` **symlinks** your real `auth.json` into it. Emit before you log in
and there is nothing to link; you get an agent that starts, looks live, and cannot call a model.

`st roles set` says so at the time rather than leaving you to find out:

```
⚠ no codex auth.json found — agents using <root>/settings/codex/worker will launch
  UNAUTHENTICATED. Run `codex login` (or set CODEX_HOME to a logged-in home before
  emitting) and re-run `st roles set`.
```

A **symlink, never a copy**: the token stays in the one place you already manage, one
`codex login` refreshes every agent at once, and the store — a git repo in every deployment we
know of — never holds a credential.

### 2. Declare it

Either the config table above, or `harness = "codex"` on the cards you want.

### 3. Emit

```bash
st roles set <agent> worker
```

On a mixed crew this writes one artifact per **(harness, role)** pair — `worker` on Claude Code
and `worker` on codex are two different files, because which one a card reads is decided by the
program it runs:

```
<root>/settings/worker.settings.json            claude, all claude workers
<root>/settings/administrator.settings.json     claude
<root>/settings/codex/worker/config.toml        codex, all codex workers
<root>/settings/codex/worker/auth.json          → symlink to your ~/.codex/auth.json
```

The per-agent override still works and is harness-aware: `codex/agent-<name>/config.toml` beats
the role's file when it exists.

### 4. Launch

```bash
st new <agent> --dry-run     # look at the composed line first
st new <agent>
```

```
cd /w && SHANTY_ROOT=<root> CODEX_HOME=<root>/settings/codex/worker \
SHANTY_AGENT=dearing BOBBIN_ROLE=worker BEADS_ACTOR=dearing ST_ROLES=worker \
codex --dangerously-bypass-hook-trust
```

`--dangerously-bypass-hook-trust` is a **default** here, and it is the only `dangerously-` flag in
this repo that is not opt-in per card. It does not widen what the model may do — that is
`--dangerously-bypass-approvals-and-sandbox`, which stays per card via `dangerous: true`. It says
*"run the hooks in the home I wrote myself"*. Without it codex declines to run any hook it has no
persisted trust record for, and the role's whole stop routing would be present, wired, and inert.

### 5. Check it landed

```bash
st roles --check          # reads the routing back OFF DISK, in whichever format
st anchor <agent> --events
```

`hooks: ok` here means the artifact that card will actually read carries the stop directions its
position in the tier requires. It is a readback, not a claim by the emitter.

---

## What works across a mixed fleet

**The tier is program-blind.** A codex worker sends its stop event with
`python -m shantytown.stop_event send` and a Claude Code lead drains it — those hook commands are
shantytown's own CLI, not either program's. So a codex worker under a claude lead under a claude
administrator routes exactly like an all-Claude-Code crew, and the reverse works too.

**A codex card can hold any role, including lead and administrator.** codex's Stop hook parses
`{"decision":"block","reason":"…"}` (or exit 2 with the reason on stderr) and feeds the reason back
to the model as a continuation prompt — the capability the tier gates on. This reverses what this
repo said before; the evidence, and the cost of the reversal, are in
[`adapters.md`](adapters.md#what-the-second-implementation-actually-cost--the-seam-codex-moved).

**Card fields honoured on codex:** `role`, `reports_to`, `workspace`, `model` (→ `--model`),
`dangerous` (→ `--dangerously-bypass-approvals-and-sandbox`), `roles`/`domain` (carried as
`ST_ROLES` / `ST_ROLE_DOMAIN`, opaquely).

**`chrome: true` is REFUSED on codex**, not ignored — there is no browser integration to enable,
and a card claiming a capability its process does not have is the same class of failure as
launching the wrong program:

```
$ st new ellie
  refused: card 'ellie' sets chrome=True, and harness 'codex' has no browser
           integration to enable. …
$ echo $?
1
```

---

## What does NOT work yet

One gap, named because a gap you can see is cheaper than one you discover. It is not a
TODO with no shape — each says what would close it.

| gap | what you see | why it is not guessed |
|---|---|---|
| **The *remaining* matcher-scoped guards** | no hank edit guard and no `SHANTY_MCP_GUARD` on a codex agent. **`SHANTY_BASH_GUARD` now IS emitted** — see below. | A matcher is a claim about the host program's **tool names**, and only the SHELL one has been measured. `probe-codex-pretooluse.sh` made codex call a shell tool and nothing else, so it says nothing about `Edit|Write|MultiEdit` or `mcp__.*`. Emitting on the strength of the Bash result would be guessing from an adjacent measurement — the same move this repo has already paid for once. Closing it is the same script with a prompt that makes codex edit a file. |

### What CLOSED, and how

`st doctor` now reports Codex's installed version and independently probes the
hooks floor by requiring `--dangerously-bypass-hook-trust` in `codex --help`.
The capability probe—not a version comparison—decides the verdict: missing
Codex and a present CLI without hooks both fail loudly, while a failed help
probe is unknown rather than clean.

Codex liveness now uses the measured persistent status line
`<model> <effort> · <workspace>`, matched only in the pane tail. It is present in
both idle and busy captures and absent from the directory-trust picker, so
`st new` can observe a healthy launch without rubber-stamping a blocked process.
The same signal drives `st crew`; unclassified live panes are named and counted
as UNKNOWN instead of disappearing between the free and busy totals. Codex
approval bypass is read from the live process flag because its status line does
not render that posture.

The workspace-delivered hooks closed in aegis-jlmqn. Provisioning now merges metrics capture,
the non-admin untracked-work nudge, and the stale-tree advisory into the Codex role or agent
`config.toml` on every launch, preserving the existing stop routes and operator keys. A live Codex
0.146.1 run measured all three mechanisms: metrics wrote `apply_patch` and `stop` events, the
untracked ledger refreshed its hooked verdict, and an edit in a one-commit-unpushed clone wrote the
stale finding. The same edit in a current clone produced no stale finding (the negative control).
Codex supplies apply-patch input as a patch string rather than Claude's `file_path`; the stale guard
adapts that payload explicitly.

The Bash guard used to be in that table. It came out by **measurement**, and the shape of the fix is
worth more than the fix:

| question | how it was answered |
|---|---|
| what does codex call its shell tool? | `scripts/probe-codex-pretooluse.sh` — a matcher-less `PreToolUse` hook dumped every payload it was handed. **`tool_name` is `Bash`; `tool_input` is `{"command": …}`** — byte-identical to Claude Code. |
| does a matcher on it actually fire? | the same run carried **seven** candidate matchers, six of them expected to fail. `"Bash"` fired; `"shell"`, `"exec_command"`, `"unified_exec"`, `"local_shell"`, `"bash"` and `"apply_patch"` were silent. The failures are the result: they show the matcher **discriminates**, so the one hit is not a match-all. |
| does the guard st SHIPS refuse a real command? | `scripts/probe-codex-bash-guard.sh` — config generated by `codex.settings_for_role`, guard taken from the live `SHANTY_BASH_GUARD`, run against codex-cli 0.146.1. Asserted from **codex's own accounting** (`hook: PreToolUse Blocked`, and the absence of an `exec` record) rather than from the agent narrating a refusal — the first draft passed on that narration and would have certified a guard that never ran. |
| was the block the *guard's* decision? | `--control` swaps in an allow-everything guard and asserts the same command **runs**. Without it, "codex refused" has three candidate causes and the probe attributes none of them. |

Because the vocabulary turned out to be identical, the guards themselves needed **no translation** —
`bd-store-guard` and `crew-only-guard` were run unmodified against a real codex payload and refused
exactly what they refuse on Claude Code, while benign commands passed.

`st roles --check` now reads the guard **back off disk** for a codex role, as a fourth leg beside the
stop routing. It has three states, not two: a command, `""` for *read it, there is none*, and
cannot-tell for *could not read*. Collapsing the last two is what makes an unguarded agent look
healthy, which is how this gap survived — every other leg was green and printed `hooks: ok`.

---

## Reference

### Precedence

| what says it | where | beats |
|---|---|---|
| the card | `harness` on `<root>/crew/<agent>.json`, or `[crew.<name>] harness` | everything |
| the role | `[harness.by_role] <role> = "…"` | the fleet default |
| the fleet | `[harness] default = "…"` | the built-in default |
| the built-in | — | `claude` |

Same ladder for the model, one axis over:

| what says it | where | beats |
|---|---|---|
| the card | `model` on `<root>/crew/<agent>.json`, or `[crew.<name>] model` | everything |
| the role | `[model.by_role] <role> = "…"` | the fleet default |
| the fleet | `[model] default = "…"` | saying nothing |
| nothing | — | no `--model` flag; the harness picks |

Both config halves are validated **at load**, and each catches a different silent failure. An
unimplemented harness name is refused, because a typo in `default` moves every card in the fleet
and would otherwise surface as `st new` failing agent by agent — a fleet-wide config error reported
as a per-agent launch failure. A role nobody has is refused for the reason every table in that file
refuses unknown keys: a rule that applies to nobody reads as applied.

A card naming a harness this build does not implement is **refused**, never quietly replaced with
the default — a card that asks for `opencode` and silently gets `claude` is a launch that succeeded
at being the wrong thing.

### The two harnesses, side by side

| | `claude` | `codex` |
|---|---|---|
| artifact | `<role>.settings.json` | `codex/<role>/config.toml` |
| format | JSON, Claude Code's hooks schema | TOML, `[hooks]` (same matcher-group shape) |
| how the launch points at it | `--settings <path>` | `CODEX_HOME=<dir>` (absolute, always) |
| blocking stop hooks | yes | yes |
| per-card permission bypass | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` |
| model flag | `--model` | `--model` |
| browser | `--chrome` / `--no-chrome` | none — `chrome: true` is refused |
| pane-reading (is it live?) | measured | **not measured** — see the gaps above |

Everything in the codex column was read out of `openai/codex` `main` on 2026-08-06, with the source
file named beside each fact in [`shantytown/codex.py`](../shantytown/codex.py). There was no codex
binary on the machine that wrote it, and a guess about another CLI's flags is exactly the kind of
code that looks shipped and has never run.

### Editing an emitted artifact by hand

You may. `st` owns the hook **events** it emits and replaces those wholesale on the next
`roles set` — a stale stop direction must never survive a rewrite — and everything else in the file
is yours and is preserved. That includes codex's `[hooks.state]` trust ledger, and anything you add
alongside (`model`, `shell_environment_policy`, an MCP server).

One cost specific to TOML: the merge is a parse-and-re-emit, so **comments and key order do not
survive** a re-emission. The alternative — surgically editing your TOML as text — is a parser we
would have to be right about every time, and being wrong there corrupts the file that decides how
the agent runs.

### Writing a third harness

`shantytown/harness.py` and one card. The interface is in
[`adapters.md`](adapters.md#what-the-second-implementation-actually-cost--the-seam-codex-moved);
`ClaudeHarness` and `CodexHarness` are the two worked examples, and `tests/test_codex_harness.py`
separates *claims about the program* from *claims about the seam*, which is the split to copy.

Nothing in the tier, the emitter, the resolver or the capability gate should need to change. If it
does, that is the leak, and it is the signal the two-implementations rule exists to produce.
