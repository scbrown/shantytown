<div align="center">

```
    ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄
   █                          █
   █   ▟▙    ▟▙    ▟▙    ▟▙   █        s h a n t y t o w n
   █  ▐██▌  ▐██▌  ▐██▌  ▐██▌  █
   █   ██    ██    ██    ██   █        ┌───┐   ┌───┐   ┌───┐
   █  ─────────────────────   █        │ ▸ │──▶│ ▸ │──▶│ ▸ │
   ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀        └───┘   └───┘   └───┘
                                       create   send    fetch
```

# shantytown

**A small harness for running a crew of coding agents.**

*Create a work item. Tell an agent to go get it. That's the whole idea.*

[![dispatch 3.4s](https://img.shields.io/badge/dispatch-3.4s-brightgreen)](#-the-numbers)
[![35x faster](https://img.shields.io/badge/vs%20gt%20sling-35%C3%97%20faster-brightgreen)](#-versus-gas-town)
[![3 connections](https://img.shields.io/badge/dolt%20conns-3%20(was%2063)-brightgreen)](#-the-numbers)
[![8 commands](https://img.shields.io/badge/commands-8-blue)](#-the-whole-surface)
[![tests](https://img.shields.io/badge/tests-45%20passing-blue)](#-testing-philosophy)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](#-install)

</div>

---

shantytown is what's left of a 14-agent homelab harness **after you remove everything nobody used.**

It is deliberately **smaller than the thing it replaces**. That's the only honest reason to build one.

## 🔎 Why

We run 14 coding agents on [Gas Town](https://github.com/scbrown/gastown). It works, and it earned its
complexity honestly — it was built for a world with polecats, a mayor, and an orchestration tier.

We don't live in that world any more, and the numbers say so:

| | |
|---|---|
| Commands Gas Town ships | **~110** |
| Commands we measurably use | **9** |
| Utilisation | **8%** |

Everything else — `daemon`, `mayor`, `deacon`, `witness`, `refinery`, `polecat`, `dog`, `convoy`,
`mq`, `scheduler`, `warrant`, `seance`, `reaper` — **we do not run.** Not "rarely": the daemon is
masked on our host by deliberate, permanent directive, and the fleet has been fine for months.

> We were carrying a town to use a mailbox and a message.

## 💡 The idea

Three steps. No daemon, no mayor, no convoy, no formula.

```
1. CREATE   the work item          → returns an id
2. SEND     "go read <id>"         → tmux send-keys
3. FETCH    the agent reads it itself
```

Step 1 is pluggable. Step 2 is a pane. Step 3 is the agent doing what agents already do.

The tell: `gt nudge --mode immediate` says, in its own help text, *"Send directly via `tmux
send-keys`."* **Dispatch already was `tmux send-keys`.** Gas Town is a wrapper around it — which is
why the replacement is smaller.

## ⚡ The numbers

Measured on the same host, same store, same day. Not estimated.

| | `gt sling` | `shanty go` | |
|---|---:|---:|---|
| dispatch (dry-run) | 51.54 s | **0.15 s** | **344× faster** |
| dispatch (real) | > 120 s ⏱️ | **3.40 s** | **35× faster** |
| Dolt connections | 63 | **3** | **21× fewer** |
| CPU while running | 4% | — | *it was waiting, not working* |

`gt sling --dry-run` **writes nothing** and still took 51 s: it spawns 20 `bd` subprocesses per
dispatch, 13 of them the *same* dependency query — an N+1 over **processes**, which is why connection
reuse is impossible rather than merely absent.

**And the honest part.** Of `shanty go`'s 3.40 s, **~3.2 s is the tracker write** — `bd update` costs
3.93 s while `bd show` costs 0.18 s. **shantytown's own overhead is ~0.20 s.** We wrote a criterion of
*"dispatch under a second"*, missed it, and **kept the miss on the record** rather than moving the
goalposts quietly: the floor is bd's, not ours. A target set against an unmeasured floor can only ever
return FAIL, and looks rigorous doing it. With the files tracker, dispatch is **0.04 s**.

## ✨ Features

- 🎯 **8 commands.** The entire surface. If it grows a `convoy`, a `rig`, or a `formula`, we've
  rebuilt the thing we left.
- 🔌 **Pluggable trackers.** Beads today, files tomorrow, yours next — *same dispatch code*. Proven by
  a swap test, not by an interface.
- 🖥️ **tmux-native.** Dispatch is `send-keys` into a pane. No broker, no queue, no daemon.
- 🧭 **`shanty prime`** — who am I, what's on my plate. It is a **read**, and must never write.
- 🚦 **`shanty triage`** — refuse / nudge / clear, decided from what the runtime actually prints.
- 🌐 **Identity is the graph.** The agent card is a **projection**, never the truth. Writes go to the
  graph; reads may come from the card; never the reverse.
- 🧪 **`--dry-run` on every writing command**, from commit one.
- 🔢 **Exit codes scripts can read.** `0` did it · `1` refused · `2` couldn't tell.

## 🧱 The whole surface

```
shanty prime                      who am I, what's on my plate      ← the primer
shanty go <item> [agent]          dispatch. this is the one that matters.
shanty crew                       who exists, what state, what role
shanty roles [--check]            the hierarchy, and whether it's real
shanty role set <agent> <role>    generative: rewrites cards, emits hooks
shanty new <agent>                create an agent from a card
shanty stop <agent>               stop it
shanty log [agent]                what happened
```

## 🆚 Versus Gas Town

Not a competitor. Gas Town is the parent — shantytown is what the parent looks like with the unused
92% removed.

| | Gas Town | shantytown |
|---|---|---|
| Commands | ~110 | **8** |
| Dispatch | `gt sling` → convoy + formula + hook | **`tmux send-keys`** |
| Dispatch cost | >120 s, 63 Dolt conns | **3.4 s, 3 conns** |
| Orchestration tier | mayor · deacon · witness · refinery · polecat | **none** |
| Message bus | `gt mail` — our most-used command | **none**, deliberately |
| Convoys | auto-created per dispatch, on the hot path | **none** — `shanty log` reads the tracker |
| Tracker | Beads, welded in | **pluggable protocol** |
| Identity | 4 files | **the graph** (card is a projection) |
| Handoff | drops `--settings`, silently hookless | **none** — `stop` + `new`; the card carries identity |

### What we kept
Beads (as **a** tracker, not **the** tracker), tmux panes, and the agent card. That's it.

### What's deliberately absent
- **No `shanty mail`.** Our heaviest-used Gas Town command (70 invocations), and still no. *A harness
  that grows a message bus is on its way to being a town.*
- **No orchestration tier.** It is switched off on our host by directive and nothing broke — the
  strongest evidence we have that it isn't needed.
- **No convoys.** A write on the hot path, for dashboard visibility.
- **No dashboard.** A dashboard reads the tracker, not the harness. Gas Town ships two dashboard
  servers; both are down; the dashboard everyone actually uses bypasses both and works.
- **No handoff.** Gas Town's drops the settings flag and silently produces a hookless session.

## 🧪 Testing philosophy

> **A check that has only ever passed is indistinguishable from a broken one.**

Every guard ships with a demonstrated **failing** case, because this codebase has already been bitten
by the alternative — repeatedly, and always by a *green test*:

- `triage`'s CLEAR branch **could never fire.** It needed >400 screen lines; `capture-pane` returns
  the ~24 visible ones. Its unit test synthesised a 500-line screen and asserted CLEAR. **Green test,
  dead branch** — in the file written to encode this very lesson.
- `roles --check` on a missing registry printed *"0 agents, every one reports somewhere"* and **exited
  0** — the code path its own docs said exit 2 existed for. The test passed throughout: its mock
  *raised*, while the real registry returned `[]`. **The mock didn't behave like the thing it stood in
  for.**
- `prime` **wrote to disk.** Asking who you are created a directory.

None of these were caught by the green suite. Every one was caught by *driving the command*.

## 📚 Docs

| doc | what it answers |
|---|---|
| [`docs/vision.md`](docs/vision.md) | what this replaces, and how we'll know it failed |
| [`docs/design.md`](docs/design.md) | the shape: dispatch, triage, trackers, panes |
| [`docs/cli.md`](docs/cli.md) | the eight commands, and `shanty prime` |
| [`docs/agent-card.md`](docs/agent-card.md) | identity — the graph is the truth, the card is a projection |
| [`docs/roles.md`](docs/roles.md) | worker / lead / administrator, and why a lead absorbs |
| [`docs/adapters.md`](docs/adapters.md) | first-class defaults, pluggable everything |
| [`docs/integrations.md`](docs/integrations.md) | the rest of the toolbox — and why we ship no dashboard |

## 📦 Install

```bash
git clone https://github.com/scbrown/shantytown && cd shantytown
pip install -e .
shanty prime
```

Python 3.11+ and `tmux`. A tracker backend (Beads) is optional — the files tracker needs nothing.

## 🧭 Principles

- **Smaller than what it replaces.** If it grows an orchestration tier, we got it wrong.
- **Bring your own tracker.** Beads, GitHub issues, or a directory of markdown files. Two functions.
- **Ship no dashboard.** It reads the tracker, not the harness.
- **Bring your own panes.** [shanty](https://git.lan/stiwi/shanty), [herdr](https://github.com/ogulcancelik/herdr), or bare tmux.
- **A check must be able to fail.** Anything that reports health must be shown returning red.

---

<div align="center"><sub>
Built by the aegis crew. Every number here was measured on the host, not estimated.<br>
<i>If it grows a mayor, delete it.</i>
</sub></div>
