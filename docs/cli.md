# shantytown — the CLI

> Stiwi, 2026-07-16: *"there needs to be a cli and a primer"*.
>
> Gas Town ships ~110 commands and we measurably use nine. This is not a smaller version of that list.
> It is **the nine**, and the discipline is that adding a tenth requires deleting one or showing the
> use.

## The whole surface

```
shanty prime                      who am I, what's on my plate         <- the primer
shanty go <item> [agent]          dispatch. this is the one that matters.
shanty crew                       who exists, what state, what role
shanty roles [--check]            the hierarchy, and whether it's real
shanty role set <agent> <role>    generative: rewrites cards, emits hooks
shanty new <agent>                create an agent from a card
shanty stop <agent>               stop it
shanty log [agent]                what happened
```

Eight. `--dry-run` is on every command that writes, from commit one.

That's the entire CLI. If it grows a `shanty convoy`, a `shanty rig`, or a `shanty formula`, we've
rebuilt the thing we left.

## `shanty prime` — the primer

The primer answers **"who am I and what do I do next"** in one call, at session start, with no
arguments. It is the single most-used thing in any agent harness — Gas Town's equivalent ran 21 times
in our measurement window — and it is the highest-leverage surface in this CLI, because *every session
starts here.*

```
$ shanty prime

  You are ellie — worker, reports to malcolm.
  You own e2e test coverage.

  ON YOUR PLATE
    ▶ aegis-9h2  Restore the den service        (in progress, 40m)

  YOUR LEAD
    malcolm (lead) — up. Your stop events go to him.

  CONTEXT (bobbin)
    scripts/e2e/den.sh · roles/den_server/tasks/main.yml

  KNOWN (quipu)
    "den.svc was cowboy-deployed and died once before" — 2026-06-30
```

Four things, and each one has to earn its line:

1. **Identity from the card.** Not from an env var, not from a file in the workspace. One source.
2. **The work.** One item, or none. A primer that prints a backlog is a dashboard.
3. **Where your stop events go**, and **whether that agent is up**. If your lead is down, prime says
   so *here* — not when you stall and discover it.
4. **Context and knowledge** — bobbin and quipu, first-class, and both optional. With the `none`
   adapters, those two sections vanish and prime still works.

### prime is a read. It must never write.

Gas Town's primer has a `--hook` mode that fires at SessionStart and mutates state. That coupling is
why "did I get primed?" became unanswerable when the hook silently didn't register. `shanty prime` is
a pure read, safe to run twice, and if you want it at session start you wire it there yourself.

## `shanty go` — dispatch

This is the command the repo exists for. `gt sling` takes >120 seconds; `--dry-run` alone takes 51s
and **writes nothing**, because the cost is 63 sequential Dolt connections during *resolution, before
any write*. Underneath, dispatch is `tmux send-keys`.

```
$ shanty go aegis-9h2 ellie

  aegis-9h2 -> ellie          in progress
  sent to pane %5             0.4s
```

```
$ shanty go aegis-9h2 ellie --dry-run

  would: tracker.update(aegis-9h2, status=in_progress, assignee=ellie)
  would: send-keys -> pane %5
  would NOT: create a convoy, spawn a session, wait for ack

  0 writes. 1 tracker call, 1 send-keys.
```

**`--dry-run` is non-negotiable and it is first, not last.** A real sling was fired as a diagnostic
during this design and hooked an agent with work nobody meant to assign. *Make the question askable
without the consequence.*

### The performance budget is a test, not an aspiration

`shanty go` must be **under one second**, and the test asserts the *mechanism*, not the stopwatch:

```
tracker calls:  <= 2
connections:    <= 1 per backend
sends:          1
waits for ack:  0
```

Count the connections. A stopwatch on a shared host is exactly the kind of number that flatters — the
`gt sling` regression would have passed a "feels fine" check on a quiet night. **The observable is the
count.**

## `shanty roles --check` — the hierarchy, verified

```
$ shanty roles --check

  arnold      administrator  reports: malcolm         hooks: ok
  malcolm     lead           reports: ellie, ian      hooks: ok
  ellie       worker         reports_to: malcolm      hooks: ok
  dearing     worker         reports_to: —            *** ORPHAN ***

  BLOCKED: 1 agent's stop events go nowhere.
```

Three outcomes: **ok**, **broken**, **cannot tell**. If it can't read a card it says so and exits
non-zero. A checker that can only report health is not a checker.

## What's deliberately absent

- **No `shanty mail`.** Our heaviest-used Gas Town command (70), and still no. A harness that grows a
  message bus is on its way to being a town. Agents that need to talk have `go` and a tracker.
- **No orchestration tier.** No mayor, deacon, witness, refinery, polecat. That tier is switched off
  on our host by directive and nothing broke — the strongest evidence we have that it isn't needed.
- **No convoys.** `gt sling` auto-creates one per dispatch. It's a write on the hot path for
  dashboard visibility. `shanty log` reads the tracker.
- **No `shanty handoff`.** Gas Town's drops the settings flag and silently produces a hookless
  session. If cycling a session is needed, it's `stop` then `new`, and the card carries the identity.

## Exit codes, because scripts read them

```
0   did the thing
1   refused — a precondition failed (orphan card, missing capability, unknown agent)
2   could not tell — a backend was unreachable. NOT success, NOT failure.
```

Code 2 exists because of a specific bug we shipped: a check that couldn't reach its target reported
CLEAR. **"I could not look" must never render as "fine."**
