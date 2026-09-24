<div align="center">

<img src="assets/logo.svg" alt="shantytown" width="360"/>


# shantytown

**A small harness for running a crew of coding agents.**

*Create a work item. Tell an agent to go get it. That's the whole idea.*

[![dispatch 3.4s](https://img.shields.io/badge/dispatch-3.4s-brightgreen)](docs/why.md#measured-against-gas-town)
[![33 commands](https://img.shields.io/badge/commands-35-blue)](#-the-whole-surface)
[![tests](https://img.shields.io/badge/tests-3489%20passing-blue)](docs/principles.md)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](#-install)
[![dependencies none](https://img.shields.io/badge/dependencies-none-blue)](#-install)
[![license MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

</div>

```bash
st task "fix the login timeout"      # → st-1
st inbox ada "go read st-1"           # → straight into ada's pane
st crew                              # → who's up, who's on what
```

Three steps: **create → send → fetch.** No resident daemon. No broker. No queue — just a
thin harness plus an orchestration layer that prioritizes work and reacts to
governed events (see [Workflows & events](docs/workflows.md)).

> **Where this came from.** Shantytown was written by someone who runs a
> [Gas Town](https://github.com/gastownhall/gastown) fleet daily — it is not a rival pitch from
> outside, it is the smaller thing that fell out of operating one. Gas Town is built for a world
> with an orchestration tier. Some days the job is just *"give that agent this ticket"*, and on
> those days a whole town is more than the work needs. This is what's left when you keep only that.

## 🖥️ One dispatch, end to end

```text
$ st task "fix the login timeout"
  st-1    fix the login timeout

$ st go st-1 ada
  st-1 -> ada          in progress
  sent to pane crew-ada

$ st go st-2 ada
  refused: pane not ready — REFUSE   in-flight work
         inputs: marker='esc to interrupt' pane='crew-ada'
$ echo $?
1
```

Dispatch consults triage and **refuses** rather than typing over an agent's in-flight work. Every
writing command has `--dry-run`, and exit codes mean one thing each: `0` did it, `1` refused, `2`
could not tell. The longer walk-through, including boot modes and the anchor, is in the
[book](docs/tour.md).

## 🧱 The whole surface

```
st task <title>                   create work, get an id back
st go <item> <agent>              dispatch. the one that matters. the agent is named, never guessed.
st inbox <agent> <message>        a message into a pane. send-keys, nothing more.
st crew                           who exists, what state, what role
st anchor                         who am I, what's on my plate      ← the anchor
st attach [agent]                 attach to a crew member — STARTING them if down (socket + pane resolved)
st work                           the item and the board
  repool <item>                   hand an item back: status -> open AND assignee cleared, verified
  defer <item> <kind> --reason-file <path|->
                                  park it with one blocker-kind label + durable reason, verified
  cost [bead] [--sync]            parser-owned cost reads and closed-bead/metric publication
  triage [item ...]             preview Jev board suggestions; --publish comments only
  dream [--run]                   inspect or run one bounded spare-capacity reflection cycle
st agent                          one agent
  new <agent>                     create an agent from a card
  stop <agent>                    stop it
  harness <agent> [claude|codex]  convert one agent to another harness. Prints the card's
                                  harness when the target is omitted
  cycle <agent> [--self]          clear context WITHOUT destroying the runtime: checkpoint ->
                                  stop -> relaunch -> re-dispatch (/clear drops bypass; this keeps it)
  input <agent>                   what's in their input box: EMPTY | TYPED | GHOST (never submits)
  ask <agent>                     the question they're blocked on, options read verbatim
  answer <agent> <N>              select option N. refuses unless a picker is really up
  log [agent]                     what happened
  history <agent>                 captured transcripts: what was archived, and
                                  whether the source still exists
  stats [agent]                   files/skills plus provider tokens and cache dimensions
st fleet                          the whole crew
  start [--mode lite|heavy]       BOOT the town: the admin alone, or every card. idempotent.
  tend                            supervise the crew: respawn what DIED, never what was RETIRED
  roles [--check|set|sync]        the hierarchy: show it, verify it, write it, import it
  init                            scaffold a NEW deployment: asks, then writes store+cards+config
  hold gaming [--clear|--status]  hold local launches during a gaming session
  window plan <id>                snapshot + acquire one maintenance transaction
  window drain|clear|release|abort <id>
                                  drain and restore exactly that snapshot
  dashboard [admin]               live, tier-scoped view: roster/state/work, self-refreshing
st repo                           a shared project repo
  worktree <repo> [agent]         provision an agent's isolated worktree off a SHARED project repo
  push <repo> [agent]             push wt/<agent> to EVERY remote; refuses if invoked from another branch
  context <query>                 what code should I be looking at?
st ops                            the installation
  doctor [--install]              what's installed, what's stale, what's missing
  provision [agent]           register Quipu tooling for local crew without launching
  subscribe                       watch quipu entity events; route governed workflows to the admin
  help <topic>                    rationale pages: handoff/cycle, haul, inbox
```

Thirty-five, and the count is load-bearing: six verbs and twenty-nine grouped commands under five
groups, and a test pins this block AND this sentence to the parser, so the next command either updates
both or fails CI. A group is a namespace, not a command; it earns no slot. The flat spellings from
before the grouping (st cycle for st agent cycle, and so on) still work for two releases and say so on
stderr.

## 📦 Install

```bash
git clone https://github.com/scbrown/shantytown && cd shantytown
pip install -e .
st ops doctor            # what's installed, what's stale, what's missing
st fleet init              # five questions, then a town: cards, hooks, config
st fleet start && st attach
```

Python 3.11+ and `tmux`. No third-party dependencies. A tracker backend is optional — the files
tracker needs nothing at all; `beads`, its Rust port `br`, and a Forgejo issue tracker plug in with
`--backend` or one line of `[env]`. Everything after that — modes, hibernate, the tmux socket, a
second host, the governor, every environment variable — is in
[Getting started](docs/getting-started.md) and [Configuration](docs/configuration.md).

## 📚 The book

Everything the README used to say, and more, lives in [`docs/`](docs/SUMMARY.md) as an mdbook:

| chapter | what it answers |
|---|---|
| [Why Shantytown?](docs/why.md) | the honest comparison, and the numbers measured against `gt sling` |
| [Features](docs/features.md) | what it does, including what landed after the pitch was written |
| [Routing](docs/routing.md) | `st inbox` *is* `tmux send-keys`, and what that buys |
| [Workflows & events](docs/workflows.md) | prioritized workflows at the admin's stop; governed Quipu events |
| [Getting started](docs/getting-started.md) · [Configuration](docs/configuration.md) | `st fleet init`, boot modes, every table and env var |
| [The CLI](docs/cli.md) | every command, the stop decision, exit codes |
| [Design](docs/design.md) · [Vision](docs/vision.md) · [Principles](docs/principles.md) | the shape, what it replaces, and how we'd know it failed |
| [Roles](docs/roles.md) · [Agent card](docs/agent-card.md) · [Harnesses](docs/harnesses.md) · [Adapters](docs/adapters.md) · [Integrations](docs/integrations.md) | identity, the tier, more than one agent program, pluggable everything |

## 📄 Licence

MIT — see [LICENSE](LICENSE).

---

<div align="center"><sub>
Every number here was measured on one host, not estimated.<br>
<i>A crew of agents, and someone running the town.</i>
</sub></div>
