# 📦 Getting started

```bash
git clone https://github.com/scbrown/shantytown && cd shantytown
pip install -e .
st ops doctor            # what's installed, what's stale, what's missing
```

Python 3.11+ and `tmux`. No third-party dependencies. A tracker backend is optional — the files
tracker needs nothing at all; `beads`, its Rust port `br`, and a Forgejo issue tracker plug in with
`--backend` or one line of `[env]`.


## First run — `st fleet init` asks, and you have a town

```bash
st fleet init
```

Five questions, each with a default that Enter accepts: the administrator's name, worker names, where
agents should work, the startup mode, and whether the admin may
[hibernate](cli.md#hibernate--when-the-administrators-stop-may-stay-stopped). It shows every path
it would write, waits for a yes, then creates:

```text
  crew/<name>.json                   one card per agent, each with a generated pane (st-<name>)
  settings/<role>.settings.json      the role's stop hooks
  shantytown.toml                    startup mode + hibernate policy
  events/  launched/                 the ledgers
```

Then the town runs:

```bash
st fleet start             # mode lite: the administrator ALONE
st attach            # its pane (starts it if it's down)
st crew              # who exists, who's up
```

Scripted installs skip the questions — `st fleet init -y --admin boss --crew ada,bo`. `-y` is **required**
when stdin isn't a terminal, so an init inside a script or a hook refuses instead of hanging on a
prompt. `-n` shows every path and writes nothing.

`st fleet init` refuses a store that already has cards or a config — a second init is far more likely to be
a mistyped `--root` than an intent. To add one agent to a store that exists, `st fleet roles set <name>
<role>` writes the card and its hooks in the same operation.
