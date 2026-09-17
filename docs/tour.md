# 🖥️ See It In Action

Create work and hand it to an agent. The id is the product — it's what step two has to say.

```text
$ st task "fix the login timeout"
  st-1    fix the login timeout

$ st go st-1 ada
  st-1 -> ada          in progress
  sent to pane crew-ada
```

Every writing command has a `--dry-run`, and dispatch shows you triage's verdict before it commits
to anything:

```text
$ st go st-1 ada --dry-run
  would: tracker.update(st-1, status=in_progress, assignee=ada)
  would: send-keys -> pane crew-ada
  would NOT: create a convoy, spawn a session, wait for ack

  triage: NUDGE    healthy
         inputs: context_high=False context_k=None pane='crew-ada' screen_lines=24
  0 writes. 1 tracker call, 1 send-keys.
```

When the agent is mid-task, `st go` **refuses** rather than typing over its work — and it shows you
the input it judged on, so you can disagree with it:

```text
$ st go st-2 ada
  refused: pane not ready — REFUSE   in-flight work
         inputs: marker='esc to interrupt' pane='crew-ada'
$ echo $?
1
```

`st crew` answers the only question a dispatcher actually has — *who can take the next item?*

```text
$ st crew

  ada         worker         up       current  idle    crew-ada
  bo          worker         up       current  busy    crew-bo
  cy          lead           up       stale    idle    crew-cy
  di          worker         down     —        —       crew-di

  2 free: ada, cy
  1 busy: bo

  ⚠ 1 agent(s) are running settings OLDER than the file on disk: cy
    Their hooks are whatever the file said AT LAUNCH. Rewriting a settings file is not deploying it — only a relaunch
    (`st agent stop <agent> && st agent new <agent>`) re-reads it.
```

And every session starts from the anchor — identity, one item, and where your stop events go:

```text
$ st anchor ada

  You are ada — worker, reports to cy.

  ON YOUR PLATE
    ▶ st-1  fix the login timeout        (in_progress)

  YOUR LEAD
    cy (lead) — up. Your stop events go to them.
```

A message to an agent that isn't there is **never** reported as delivered:

```text
$ st inbox di "protocol step 3"
  could not tell: pane crew-di is not there (agent down?)
$ echo $?
2
```

## Cold start — one command, and only the crew you're paying for

`st fleet start` takes a **mode**, and `lite` — the default — brings up the administrator **alone**: one
agent's context, one agent's bill, and the one agent that can decide who else is needed.

```text
$ st fleet start
  mode 'lite' from the built-in defaults (no config file) — 1 agent(s): sattler

  + sattler      started      launched into 'shanty-sattler', hooks verified

  mode 'lite' · 1 selected · started 1 · 1 up · 0 fault(s)

  attach: `st attach sattler`   ·   roster: `st crew`

$ st attach                          # the admin's pane. starts it first if it's down
```

`--mode heavy` brings up every card. It is **idempotent** — `already-up` is a success, a live agent is
never launched over, and a retired one is never resurrected — so it is safe to run when you don't know
what's already running, which is the only time you need it. Modes are named crew sets in
[`shantytown.toml`](shantytown.toml.example), where the admin can also be told to
[**hibernate**](cli.md#hibernate--when-the-administrators-stop-may-stay-stopped): stop waking
itself at every turn boundary once it has handed the work out.
