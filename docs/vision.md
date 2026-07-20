# shantytown — vision

> Create a work item. Tell an agent to go get it. That's the whole idea.

## The problem, measured

We run 14 coding agents on [Gas Town](https://github.com/gastownhall/gastown). It works, and it earned its
complexity honestly — it was built for a world with polecats, a mayor, and an orchestration tier.

We don't live in that world any more, and the numbers say so:

| | |
|---|---|
| Commands Gas Town ships | **~110** |
| Commands we measurably use | **9** |
| Utilisation | **8%** |

The nine, by actual frequency (shell history + every script and skill in the fleet):

```
gt mail       70      gt crew       15      gt tap-guard   9
gt hooks      24      gt formula    14      gt handoff     6
gt prime      21      gt sling      15      gt rig         2
```

Everything else — `daemon`, `mayor`, `deacon`, `witness`, `refinery`, `polecat`, `dog`, `convoy`,
`mq`, `scheduler`, `warrant`, `seance`, `reaper`, `quota`, `estop` — **we do not run.** Not "rarely";
the daemon is masked on this host **by deliberate, permanent directive**. The orchestration tier is
off, and the fleet has been fine without it for months.

We are carrying a town to use a mailbox and a message.

## The tell

`gt sling` is the command that hands work to an agent. Measured, same host, same data plane:

```
gt hook            806 ms
bd show            306 ms
gt crew status     206 ms
bd ready           205 ms
gt sling        > 120,000 ms      ← exceeded a 120s timeout. Twice. Both had to be backgrounded.
```

A ~150× gap. The data plane is not slow — Dolt answers in under a second. Something in the wrapper is.

And here is what `gt nudge --mode immediate` says about itself, in its own help text:

> **immediate** — Send directly via `tmux send-keys`. Interrupts in-flight work.

**Dispatch is already `tmux send-keys`.** Gas Town is a wrapper around it. So the general tool is
**smaller** than the thing it replaces — which is the only honest reason to build one.

## What shantytown is

Three steps. No daemon, no mayor, no convoy, no formula.

```
1. CREATE   the work item          → returns an id
2. SEND     "go read <id>"         → tmux send-keys / shanty / herdr
3. FETCH    the agent reads it itself
```

That's it. Step 1 is pluggable. Step 2 is a pane. Step 3 is the agent doing what agents already do.

## What we keep from Gas Town

Only what we measurably use, and only where it earns its place:

| keep | why |
|---|---|
| **crew creation** | A named workspace + a session + an identity. The genuinely useful primitive. |
| **dispatch** | Create item → tell agent. The `sling` idea, minus the 120 seconds. |
| **mail** | Our heaviest-used command (70). Durable agent-to-agent messages that survive a session death. |
| **hooks / prime** | Session start/stop injection. Where a crew member learns who they are. |
| **cycling** | `/clear`, restart, context triage. Fiddly, load-bearing, currently tribal knowledge. |

## What we drop

Everything else. Specifically the orchestration tier — mayor, deacon, witness, refinery, polecats,
convoys, merge queues, warrants. **Not because it's bad, but because we already turned it off and
nothing broke.** That's the strongest possible evidence it isn't needed here.

If shantytown grows an orchestration tier, we got it wrong.

## Bring your own tracker

shantytown must not know what a bead is. A backend implements three small functions:

```python
create(title: str, **fields) -> WorkItem      # returns the item, id included
get(item_id: str) -> WorkItem                 # read one back
update(item_id: str, **fields) -> None        # mark it assigned
```

*(This vision doc originally promised two — `create` and a `hint` renderer. Dispatch has to read an
item it did not create and mark it assigned, and faking those through `create` would have been the
dishonest kind of small. `hint` was rendering and moved to the CLI. Three is the shipped number;
`shantytown/protocols.py` is the authority. — GitHub #8)*

Reference adapters ship for **beads**, **GitHub issues**, and **plain files** (`./work/<uuid>.md`).
Someone on Jira, Linear, or nothing at all writes ten lines and it works.

This is the difference between a tool for us and a tool for anyone.

## Bring your own panes

| layer | what it is | when |
|---|---|---|
| **bare tmux** | `send-keys`. Already how everything works. | The floor. Always available. |
| **shanty** | A small Go tmux wrapper — Dracula, byobu keybindings, pluggable status segments. | You want the crew to *look* like a crew. |
| **[herdr](https://github.com/ogulcancelik/herdr)** | An **agent multiplexer** — 16.8k stars, actively pushed. Purpose-built for exactly this. | Someone else maintains the hard part. |

**shanty and herdr are not competitors.** shanty makes tmux *pleasant*; herdr manages *agents*. shanty
is a status bar and keybindings; herdr is the multiplexer. shantytown should be able to drive any of
the three, because the pane layer is the part most likely to be replaced — and the part we care least
about owning.

**Recommendation: start on bare tmux.** It's the floor, it already works, and it keeps the pane layer
swappable. Add a shanty/herdr adapter once dispatch is proven. Do not couple the harness to a
multiplexer before the harness exists.

## The hard part isn't dispatch — it's knowing when *not* to

Anyone can shell out to `send-keys`. The knowledge worth packaging is **context triage**, and we paid
for all of it:

| situation | action |
|---|---|
| Target idle-ish, new work **related** to what's in context | **nudge** — send-keys, keep context |
| Session healthy, context large, work **unrelated** | **`/clear` then nudge** — cheapest reset |
| Context exhausted, or session wedged | **restart** — launcher-relaunch only |
| Target mid-flight on something interruptible-at-a-cost | **don't dispatch** |

Two traps, both learned the expensive way:

- **Never cycle via handoff.** Gas Town's `handoff` drops `--settings`, so the agent comes back **with
  no hooks** and looks identical to a healthy one. An entire measurement was burned on this.
- **"running" status lies.** A crew member can be `running` and parked, or `running` and context-dead.
  **Verify by the pane, not the status field.**

`--mode immediate` *interrupts in-flight work* — per Gas Town's own help. Interrupting is a real cost,
so "should I dispatch at all?" is a first-class question, not an afterthought.

## Non-negotiable: dry-run

Dispatch is a state-changing act on someone else's session. It ships with `--dry-run` from commit one,
and the docs say to use it.

This isn't hypothetical: while diagnosing `gt sling`, an operator ran a real sling *as a probe* and
hooked a working crew member with a task nobody meant to assign. There was no way to ask "could you
resolve this?" without actually assigning it. **Make the question askable without the consequence.**

## Non-goals

- **Not a scheduler.** No cron, no queues, no autonomous work generation.
- **Not an orchestrator.** No mayor deciding who does what. A human or a coordinating agent decides.
- **Not a tracker.** Bring your own. We store nothing.
- **Not a multiplexer.** tmux/shanty/herdr already exist.
- **Not a Gas Town fork.** Gas Town is good at what it's for. This is for the 8%.

## How we'll know it worked

- Dispatch a real item to a real agent **without Gas Town**, and the agent picks it up.
- Swap the tracker from beads to files **without touching dispatch code**.
- Context triage demonstrated in **both** directions: one nudge that lands, one target correctly
  refused or cleared. *A triage that has only ever said "nudge" is not triage.*
- Dispatch completes in **under a second**, not 120.

If it can't beat `gt sling` on latency and simplicity, it has no reason to exist — and we should say so
and stop.
