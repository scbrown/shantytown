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
| **runtime** | **Claude Code** | one other (`opencode` / `codex`) |
| **tracker** | **beads** | `files` — a directory of markdown. Zero dependencies. |
| **panes** | bare `tmux` | `shanty` / `herdr` adapters, later |
| **context** | **bobbin** | none-adapter (returns nothing, harness still works) |
| **knowledge** | **quipu** | none-adapter |

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
cannot host a `lead`** — the whole role is "receive your reports' stop events". So `hooks()` is not
metadata; it's a **capability declaration**, and the harness must refuse a card whose role needs a
capability its runtime doesn't have:

```
$ shanty role set malcolm lead
  ERROR: runtime 'codex' does not declare blocking stop hooks.
         role 'lead' requires on_report_stop delivery to the model.
         malcolm stays worker. Nothing written.
```

Refusing loudly is the point. A lead on a runtime that can't deliver stop events is a tier that
exists on paper and absorbs nothing — and that failure is *silent*, which is the one kind we've
agreed not to ship.

## Context and knowledge — bobbin and quipu, first-class

Stiwi: *"i want first class support for bobbin and quipu as well."*

These are **not trackers** and shouldn't be forced through that interface. They're two different
things and conflating them is how a harness grows a town:

- **bobbin — context.** Given what an agent is doing, what code should it be looking at? Read-only,
  synchronous, best-effort. Already earns its place: it surfaced the files behind a failure repeatedly
  while we built this.
- **quipu — knowledge.** What do we know, and what did we just learn? Read on start (*"query before
  you act"*), write on stop (*"capture what you learned"*).

```python
class Context(Protocol):        # bobbin
    def relevant(self, query: str, budget: int) -> list[Snippet]: ...

class Knowledge(Protocol):      # quipu
    def search(self, query: str) -> list[Fact]: ...
    def record(self, episode: Episode) -> TxId: ...
```

**Both must be optional at the interface and excellent by default.** The `none` adapter is the test:
an agent with no bobbin and no quipu still starts, still works, still stops. If it can't, we didn't
build a harness with knowledge — we built a knowledge system with a harness attached, and that's the
thing this repo exists to not be.

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
`files` + `none` + `none` + bare tmux. If that goes red, we leaked. That test is the interface —
everything above is commentary.
