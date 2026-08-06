# shantytown — first-class defaults, pluggable everything

> Stiwi has now said the same thing three times about three different layers: *"bring your own panes"*,
> *"bring your own tracker"*, and *"first class support for claude code but make it plugable so
> opencode and codex and stuff can be swapped"*. That is **one design stance, stated three times.**
> This doc states it once.

## The stance

**First-class means: it ships, it's the default, it's the one we test against, and it's allowed to be
good.** Pluggable means: it sits behind an interface narrow enough that a second implementation is a
weekend, not a fork.

These are not in tension. The tension people expect — "if you make one first-class, the abstraction
rots" — is real, and it has exactly one cause: **the abstraction was designed from one implementation
and never run against a second.** So the rule below is not about taste. It's a test.

## The rule: two implementations or it isn't an interface

Every adapter layer ships **two** implementations from day one. The second one exists to prove the
first didn't leak.

| layer | first-class (default) | the second implementation, which is the *proof* |
|---|---|---|
| **registry** *(identity)* | **quipu** | `files` — a flat registry. **Required layer; still needs two impls.** |
| **runtime** | **Claude Code** | **codex** — shipped. `shantytown/codex.py` |
| **tracker** | **beads** | `files` — a directory of markdown. Zero dependencies. |
| **panes** | bare `tmux` | `shanty` / `herdr` adapters, later |
| **context** | **bobbin** | none-adapter (returns nothing, harness still works) |
| **knowledge** *(planned — not built)* | quipu | none-adapter |

The **registry** row is the one that breaks the pattern and it's worth staring at: it is the only
layer with **no `none` option** — you cannot start an agent whose identity you can't read. It still
gets a second implementation, because the two-implementations rule isn't about optionality, it's about
**leak detection**: if a flat-file registry is hard to write, quipu has leaked into the core. That
second impl is also the honest answer to "does shantytown now require a graph database?" — no, it
requires a *registry*, and quipu is the good one.

The `files` tracker and the `none` adapters aren't charity. They are the **negative control**: if the
harness can't run with a markdown directory and no bobbin, the interface is a lie and beads/bobbin
have leaked into the core. That's checkable, in CI, on every commit — not a principle we intend to
honour.

**If a second implementation is hard, the interface is wrong.** That's the signal, and it's the whole
reason to keep the second one around when nobody uses it.

## Runtime — Claude Code first-class, swappable

An agent runtime does three things. That's the interface.

```python
class Runtime(Protocol):
    def start(self, card: AgentCard, pane: Pane) -> None: ...
    def send(self, pane: Pane, text: str) -> None:  ...   # dispatch. this is send-keys.
    def hooks(self, card: AgentCard) -> HookSpec:   ...   # what stop/start hooks this runtime supports
```

`hooks()` is where runtimes actually differ, and it's the one that will hurt. Claude Code has a
specific and *load-bearing* stop-hook contract we've measured the hard way:

- a **non-blocking** stop hook's stdout is **discarded** — the agent never sees it
- `reason` (with `decision: block`) reaches the **model**; `systemMessage` reaches the **user's
  terminal only**
- so "notify the agent at stop" is **blocking or nothing**, not blocking-vs-gentle

We lost a day to that in Gas Town. **A runtime that cannot deliver a message to its agent at stop
cannot host any role that RECEIVES stop events** — the whole job of such a role is "receive stop
events from below". So `hooks()` is not metadata; it's a **capability declaration**, and the harness
must refuse a card whose role needs a capability its runtime doesn't have:

```
$ st role set malcolm lead
  refused: harness 'opencode' does not declare blocking stop hooks;
           role 'lead' requires stop-event delivery to the model.
           malcolm stays worker. Nothing written.
```

> **This example used to say `codex`, and that claim has been withdrawn.** It was true of the codex
> it was written against — a `notify` program, non-blocking, stdout discarded. Current codex ships a
> full hooks system whose `Stop` hook parses `{"decision":"block","reason":"…"}` (or exit 2 with the
> reason on stderr) and feeds the reason back to the model as a continuation prompt — which is
> exactly the capability this section defines. So **`CodexHarness` declares `blocking_stop = True`
> and a codex lead/administrator is hostable.** Read out of `openai/codex` `main` on 2026-08-06
> (`codex-rs/hooks/src/events/stop.rs`); every codex fact and its source file is listed in
> `shantytown/codex.py`'s header.
>
> **The cost of that reversal, stated plainly:** while this said False the failure mode was a
> refusal — a lead you could not create. Saying True means a lead on a codex *older* than the hooks
> system is accepted and absorbs nothing, silently. That is a version floor `st` cannot check from
> inside the gate (`codex --version` at role-set time measures a binary the agent may not even
> launch with); it belongs in `st doctor` as a tool row, and it is not built yet.
>
> The gate itself did not change, and that is the point of keying it on the capability: the reversal
> was one method on one class. The refusal path is still exercised — by `StoplessRuntime` and the
> `_NonBlockingHarness` doubles, which now say what they *are* rather than naming a program we were
> wrong about.

The gate fires at **role-set time**, before the card or its settings are written
(`tier.role_set`, aegis-w5l9) — so "Nothing written." is literally true, and a
tier card the fleet could never start never lands in the registry. The same gate
also guards the `st new` launch path as a backstop; there its message ends
"Nothing launched." instead, because by then the card may already be on disk.

**Which roles need it is not a fixed list — it is exactly the set of `route_stop` DESTINATIONS**
(ruled against the tier in tier.py). A **worker** is only ever a stop *source*, so it
needs nothing. A **lead** receives its reports' stops. An **administrator** receives *risen* stops —
Q3 (a report's stop rises to the admin when its lead is down, LOUDLY, carrying `LEAD_UNREACHABLE`),
Q4 (a lead-less worker routes straight to the admin), and lead escalations. So **both lead AND
administrator require `blocking_stop`**, and the admin is the *more* critical case, not the marginal
one: it is the last backstop, with nobody above it to catch what it drops, so a non-blocking
administrator turns Q3's LOUD rise into a *silent* one — regressing the exact invariant that ruling
exists to hold. The gate is keyed on the `blocking_stop` capability, never a runtime name, so a third
capable runtime passes without editing it.

Refusing loudly is the point. A stop-receiver on a runtime that can't deliver stop events is a tier
that exists on paper and absorbs nothing — and that failure is *silent*, which is the one kind we've
agreed not to ship.

### What the second implementation actually cost — the seam codex moved

The `Runtime` block above is the *launcher* seam: compose-or-refuse, then deliver through Panes.
**Which program** an agent runs is a different seam, and it is the card's — a `Harness`
(`shantytown/harness.py`). Writing the second one is the only way to find out where the first leaked,
and it leaked in five places that all *looked* generic:

```python
class Harness(Protocol):
    name: str
    def launch(self, card: Agent, settings_path: str, root=None) -> str: ...
    def settings(self, role: str, root=None) -> dict: ...
    def settings_name(self, role: str) -> str: ...            # WHAT the artifact is called
    def agent_settings_name(self, agent: str) -> str: ...
    def render(self, settings: dict, existing: str = "") -> str: ...   # its BYTES, merged
    def read_stop_directions(self, text: str) -> set[str] | None: ...  # reading it back
    def settings_in_cmdline(self, cmdline: str) -> str | None: ...     # on a RUNNING process
    def carries_settings(self, launch: str, settings_path: str) -> bool: ...  # the invariant
    def provision(self, settings_path: str, root=None) -> list[str]: ...
    def hooks(self, card: Agent) -> HookSpec: ...             # the capability declaration
```

Each of the middle six was a literal somewhere in `cli.py` or `runtime.py`, and every one of them was
Claude Code's: the emitter wrote `<role>.settings.json` and JSON bytes; the compose invariant asserted
the string `--settings`; the live reader grepped a command line for that same flag; the readback
parsed Claude Code's hook schema. **codex has no settings flag at all** — it reads `config.toml` out
of `$CODEX_HOME` — so all four would have quietly answered for the wrong program, and the tests would
have stayed green, because nothing else in the suite ran a second program. That is the leak-detection
argument in this document, paid off exactly as advertised.

What is *shared* is as load-bearing as what differs. **Which** stop hooks a role gets and **what
command** they run is shantytown's (`runtime.role_stop_hooks`) and both harnesses call it — a second
copy of the routing table is how a lead comes to send on one program and drain on the other. Both
programs happen to take the same matcher-group hook shape, so only the container differs.

Two honest gaps, named rather than papered over:

- **The matcher-scoped guards are not emitted for codex.** A matcher is a claim about the host
  program's *tool names* (`"Bash"`, `"mcp__.*"` are Claude Code's), and codex's vocabulary is
  unmeasured. A guard emitted with the wrong vocabulary is not a weaker guard, it is one that never
  fires while reading as wired — this repo has already paid that bill (aegis-ac5x/18e0). So codex
  gets the matcher-free events (`SessionStart`, `Stop`), and the omission is pinned by a test.
- **The pane-reading predicates are still Claude Code's.** `is_live`, the trust and consent screens,
  the auth-dead banner — all matched against a captured pane, all `ClaudeRuntime`'s. They are the same
  *kind* of per-program fact as the argv, but a marker never observed passing is not a marker, and
  there is no codex on the build host to watch. The consequence is stated where it lands: `st new` on
  a codex card reports **could-not-tell (2)**, not a confident wrong answer.
- **The workspace-delivered hooks do not reach a codex agent at all.** The metrics capture, the
  untracked-work nudge and the stale guard are deliberately *not* in the emitted settings — they ride
  `provision.py`'s `<ws>/.claude/settings.local.json`, which is re-applied on every launch so it
  self-heals (aegis-rcyd). That file is Claude Code's by construction, and `provision` writes it for
  every card regardless of harness. So a codex agent gets its stop routing and its session-start
  directive and **none of those three**. Same rule as the matchers: the fix is a delivery channel
  measured against codex, not this one aimed at it and hoped for.

## Context and knowledge — bobbin and quipu, first-class

Stiwi: *"i want first class support for bobbin and quipu as well."*

These are **not trackers** and shouldn't be forced through that interface. They're two different
things and conflating them is how a harness grows a town:

- **bobbin — context.** Given what an agent is doing, what code should it be looking at? Read-only,
  synchronous, best-effort. Already earns its place: it surfaced the files behind a failure repeatedly
  while we built this.
- **quipu — knowledge** *(PLANNED, not built — aegis-ks9b).* What do we know, and what did we just
  learn? Read on start (*"query before you act"*), write on stop (*"capture what you learned"*). The
  *capture-at-stop* behaviour this describes is real and shipped, but it lives in the crew's
  **graph-capture Stop hook + graph-extract skill** (POST to quipu's REST `/episode`), **not** behind
  a shantytown adapter. This layer — `Knowledge`, `Fact`, `Episode`, `TxId`, a `QuipuKnowledge` impl,
  a `none` impl — is a design sketch; none of it exists in `shantytown/`. If it is ever built, it must
  ship with its second implementation and the leak test must actually construct the `none` one (the
  drift-pin below fails the moment the `Knowledge` block below stops saying PLANNED without the code).

```python
class Context(Protocol):        # bobbin
    def relevant(self, query: str, budget: int) -> list[Snippet]: ...
    # raises ContextUnavailable when it could not look. See below — this is
    # not decoration, the signature is unsound without it.

# PLANNED — not built (aegis-ks9b). No Knowledge/Fact/Episode/TxId exists in
# shantytown/. Sketch only; see the note above.
class Knowledge(Protocol):      # quipu
    def search(self, query: str) -> list[Fact]: ...
    def record(self, episode: Episode) -> TxId: ...
```

**`-> list[Snippet]` cannot say "I could not look", and it must.** Built 2026-07-16; the hole was in
this signature, not in the implementation. An empty
list has to carry two opposite facts:

| | means | exit |
|---|---|---|
| `[]` from the **none-adapter** | nothing is configured; we never asked | `0` |
| `[]` from **bobbin, answering** | we asked; nothing matched | `0` |
| `[]` from **bobbin, DOWN** | we could not ask — **not a finding** | `2` |

The first two are answers. The third is a failure wearing their clothes: *the
none-adapter and a downed bobbin return the same bytes and mean opposite things.*
So implementations **raise `ContextUnavailable`** rather than return `[]` when the
backend is unreachable, unparseable, or absent — the exception is the only thing
in the type that can hold that distinction.

This is not hypothetical: a sweep here read a rate-limited **429 as "metric
absent"** and manufactured 32 fake findings. "I could not look" scored as "there
is nothing there". Measured, bobbin itself is honest about this and we just have
to not throw it away — `exit 0` + `{"count":0}` when it answers with nothing,
`exit 1` + *"Failed to connect"* when it cannot. The adapter's job is to carry
that out to the caller.

**bobbin is optional. quipu's *knowledge* job is optional. quipu's *registry* job is not** — see
[`agent-card.md`](agent-card.md): quipu holds identity (who exists, who reports to whom, what role),
and you cannot start an agent whose identity you can't read. Those are two jobs and they must not
share a switch:

| quipu's job | holds | optional? |
|---|---|---|
| **registry** | identity, hierarchy, role | **no — required** |
| **knowledge** *(planned)* | episodes, facts | **yes — `none` adapter valid** *(when built; not built yet — aegis-ks9b)* |

The `none` **knowledge** adapter *(once knowledge is built — aegis-ks9b)* is meant to be the test: an
agent with no bobbin and no episode-store starts, works, and stops. If it can't, we didn't build a
harness with knowledge — we built a knowledge system with a harness attached, and that's the thing
this repo exists to not be. There is no `none`
registry, and that is a real cost to "smaller than what it replaces" — argued honestly in
`agent-card.md`.

### The quipu integration has a known trap, and it's ours

We built the Gas Town version of "capture at stop" and measured every way it fails. Whatever
shantytown does here inherits these, and they're written down because they cost real days:

1. **Search before you mint.** The two agents most primed to be careful both fragmented the graph on
   first use — one caught it, one didn't. `record()` must make dedup the easy path, not a docstring.
2. **Every node needs a type.** One untyped node rejects the *entire* episode, silently, at stop —
   when the agent has already decided it's done.
3. **The session id must be in the episode**, or you cannot tell a capture from an interruption
   nobody acted on. We shipped without it and the resulting metric could only ever report the
   pessimistic answer.
4. **Skipping is a legitimate outcome.** If an agent has nothing durable, silence is correct. A
   capture rate that treats "nothing to say" as failure will get you noise.

## Trackers — beads first-class, `files` as the floor

Two functions. That's the whole tracker interface:

```python
class Tracker(Protocol):
    def get(self, item_id: str) -> WorkItem: ...
    def update(self, item_id: str, **fields) -> None: ...
```

Anything more and the tracker is driving the harness. The `files` implementation — a directory of
markdown — exists to keep that honest and must stay in CI.

## What "first-class" does not license

It does not license reaching around the interface. The moment dispatch imports a beads type, or
triage calls quipu directly, the adapter is decorative and the second implementation is a fiction
we're maintaining for the README.

**The check is mechanical, not cultural:** the test suite runs the whole harness on
**`files` registry + `files` tracker + `none` context + bare tmux**. No quipu, no
beads, no bobbin, no multiplexer. If that goes red, we leaked. That test is the interface — everything
above is commentary. *(There is no `none` knowledge in that run: the knowledge layer is not built —
aegis-ks9b. When it is, this sentence and `tests/test_leak.py` gain `+ none knowledge`, and the test
must construct it.)*

Note what that run proves and what it doesn't: it proves *the core doesn't import quipu*. It does not
license shipping the flat registry — quipu is the default because identity wants provenance, history,
and one place to ask. The flat registry exists to keep the boundary honest, the same way the `files`
tracker does.
