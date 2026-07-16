<div align="center">

```
                          .-.                    .--.
             .--.        /   \      .-.          |[]|    .-.
            /::::\   .--|:::::|--. /   \    .--. |  |   /   \
      .-.   |::[]:|  |==|:::::|==| |:::|   /::::\|[]|  |:::::|   .--.
     /   \  |::::||  |  |[]:[]|  | |:::|   |::::||  |  |:::::|  /    \
    |:::::| |[]::||  |  |:::::|  | |:[]|   |[]::||::|  |:[]:[|  |::[]|
    |:[]:[| |::::||__|__|:::::|__|_|:::|___|::::||::|__|:::::|__|::::|
   _|_____|_|____||__|__|_____|__|_|___|___|____||__|__|_____|__|____|_
  ///////////////////////////////////////////////////////////////////////

                          s h a n t y t o w n
                a crew of agents. no town hall required.
```

# shantytown

**A small harness for running a crew of coding agents.**

*Create a work item. Tell an agent to go get it. That's the whole idea.*

[![dispatch 3.4s](https://img.shields.io/badge/dispatch-3.4s-brightgreen)](#-speed)
[![35x faster](https://img.shields.io/badge/vs%20gt%20sling-35%C3%97%20faster-brightgreen)](#-versus-gas-town)
[![10 commands](https://img.shields.io/badge/commands-10-blue)](#-the-whole-surface)
[![tests](https://img.shields.io/badge/tests-57%20passing-blue)](#-a-check-that-cannot-fail-is-not-a-check)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](#-install)
[![license MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

</div>

```bash
st task "fix the login timeout"      # → st-1
st mail ian "go read st-1"           # → straight into ian's pane
st crew                              # → who's up, who's on what
```

Three steps: **create → send → fetch.** No daemon. No mayor. No broker. No queue.

## 📮 Routing: there is nothing in the middle

**`st mail` *is* `tmux send-keys`.** That's not an implementation detail — it's the product.

```
st mail ian "go read st-1"
   │
   ├─ registry.get("ian")        → identity: role, reports_to, pane
   ├─ pane = "aegis-crew-ian"    → the address IS the pane
   ├─ panes.exists(pane)?        → NO  → exit 2 "could not tell". nothing sent.
   └─ tmux send-keys -t <pane>   → the message. that's the delivery.
```

**No message bus. No queue. No delivery guarantee — because there's nothing to guarantee.** The pane is either there or it isn't, and you're told which.

| routing outcome | exit | what it means |
|---|---|---|
| delivered | **0** | the keys went into a live pane |
| no such agent / no pane | **1** | refused. nothing sent. |
| pane named but gone | **2** | *could not tell* — never a cheerful success |

**Identity resolves through the registry, not through a config file you hand-edit.** The graph is the truth; the agent card is a projection of it. Writes go to the graph, reads may come from the card, never the reverse — so an agent's address can't quietly drift from reality.

## ✨ Features

- 📮 **`st mail`** — a message into a pane. One send-keys. Nothing between you and the agent.
- 📋 **`st task`** — create work, get an id back. The id is the product; it's what step 2 says.
- 🚀 **`st go`** — dispatch: bind an item to an agent and tell them. **35× faster than `gt sling`.**
- 🧭 **`st prime`** — who am I, what's on my plate. A **read**; it never writes.
- 🚦 **`st triage`** — refuse / nudge / clear, judged from what the runtime actually prints on screen.
- 👥 **`st crew`** — who exists, who's up, what role. Reports **down** only when it's really down.
- 🔌 **Pluggable trackers** — beads today, files tomorrow, yours next. *Same dispatch code.* Proven by a swap test, not by an interface.
- 🖥️ **tmux-native** — bring your own panes. Named sockets supported, so it works from cron and systemd too.
- 🧪 **`--dry-run` on every writing command**, from commit one.
- 🔢 **Exit codes scripts can read** — `0` did it · `1` refused · `2` couldn't tell.

## ⚡ Speed

Measured on one host, one store, one day. Not estimated.

| | `gt sling` | `st go` | |
|---|---:|---:|---|
| dispatch (dry-run) | 51.54 s | **0.15 s** | **344× faster** |
| dispatch (real) | > 120 s ⏱️ | **3.40 s** | **35× faster** |
| Dolt connections | 63 | **3** | **21× fewer** |
| CPU while running | 4% | — | *waiting, not working* |

## 🧱 The whole surface

```
st task <title>                   create work, get an id back
st mail <agent> <message>         a message into a pane. send-keys, nothing more.
st go <item> [agent]              dispatch. the one that matters.
st prime                          who am I, what's on my plate      ← the primer
st crew                           who exists, what state, what role
st roles [--check]                the hierarchy, and whether it's real
st role set <agent> <role>        generative: rewrites cards, emits hooks
st new <agent>                    create an agent from a card
st stop <agent>                   stop it
st log [agent]                    what happened
```

## 🆚 Versus Gas Town

Gas Town is the parent, and it earned its complexity honestly — it was built for a world with an orchestration tier. We don't live there any more. It ships **~110 commands; we measurably used nine.**

| | Gas Town | shantytown |
|---|---|---|
| Commands | ~110 | **10** |
| Dispatch | `gt sling` → convoy + formula + hook | **`tmux send-keys`** |
| Dispatch cost | >120 s, 63 Dolt conns | **3.4 s, 3 conns** |
| Messaging | `gt mail` → bus + queue + router | **`st mail` → send-keys** |
| Undeliverable message | queued forever, reports ✓ | **exit 2, nothing sent** |
| Orchestration tier | mayor · deacon · witness · refinery · polecat | **none** |
| Convoys | auto-created per dispatch, on the hot path | **none** |
| Tracker | Beads, welded in | **pluggable protocol** |
| Identity | 4 files | **the graph** (card is a projection) |
| Dashboard | two servers, both down | **none** — a dashboard reads the tracker |

**What we kept:** beads (as *a* tracker, not *the* tracker), tmux panes, the agent card. That's it.

## 📚 Docs

| doc | what it answers |
|---|---|
| [`docs/vision.md`](docs/vision.md) | what this replaces, and how we'll know it failed |
| [`docs/design.md`](docs/design.md) | the shape: dispatch, triage, trackers, panes |
| [`docs/cli.md`](docs/cli.md) | the eight commands, and `st prime` |
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

## 📄 Licence

MIT — see [LICENSE](LICENSE).
---

<div align="center"><sub>
Built by the aegis crew. Every number here was measured on the host, not estimated.<br>
<i>If it grows a mayor, delete it.</i>
</sub></div>
