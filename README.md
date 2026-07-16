# shantytown

**A small harness for running a crew of coding agents.**

Create a work item. Tell an agent to go get it. That's the whole idea.

shantytown is what's left of a 14-agent homelab harness after you remove everything nobody used.

## Status

Vision and design. No code yet. Read [`docs/vision.md`](docs/vision.md) first — it explains what this
replaces and why the replacement is *smaller*, not bigger.

## The one-paragraph version

We ran 14 agents on [Gas Town](https://github.com/scbrown/gastown) for months. It works. It also ships
~110 commands, of which we measurably use **nine** — and its orchestration tier (mayor, deacon,
witness, refinery, polecats) is switched off on our host by deliberate directive. Meanwhile `gt sling`,
the command that hands work to an agent, takes **>120 seconds** while every other command answers in
under one. Underneath, it's `tmux send-keys`.

shantytown keeps the nine, drops the rest, and stops pretending dispatch is hard.

## The docs

| doc | what it answers |
|---|---|
| [`docs/vision.md`](docs/vision.md) | what this replaces, and how we'll know it failed |
| [`docs/design.md`](docs/design.md) | the shape: dispatch, triage, trackers, panes |
| [`docs/cli.md`](docs/cli.md) | the eight commands, and `shanty prime` — the primer |
| [`docs/agent-card.md`](docs/agent-card.md) | identity — **quipu is the truth**, the card is a projection |
| [`docs/roles.md`](docs/roles.md) | worker / lead / administrator, and why a lead absorbs |
| [`docs/adapters.md`](docs/adapters.md) | first-class defaults (Claude Code, beads, bobbin, quipu), pluggable everything |
| [`docs/integrations.md`](docs/integrations.md) | the rest of the toolbox — tapestry, reactor, dp, skein — and why we ship **no dashboard** |

## Principles

- **Smaller than what it replaces.** If it grows an orchestration tier, we got it wrong.
- **Bring your own tracker.** Beads, GitHub issues, or a directory of markdown files. Two functions.
- **Ship no dashboard.** A dashboard reads the tracker, not the harness. Gas Town ships two dashboard
  servers; both are down; the dashboard everyone uses bypasses both and works.
- **Bring your own panes.** [shanty](https://git.lan/stiwi/shanty), [herdr](https://github.com/ogulcancelik/herdr), or bare tmux.
- **Python.** New code is Python; it has the test tooling bash doesn't.
- **A check must be able to fail.** Anything that reports health must be shown returning red.
