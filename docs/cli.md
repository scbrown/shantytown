# shantytown — the CLI

> Stiwi, 2026-07-16: *"there needs to be a cli and a primer"*.
>
> Gas Town ships ~110 commands and we measurably use a dozen. This is not a smaller version of that
> list. It is **the short set**, and the discipline is that each command earns its slot — the count is
> now pinned by a test (`tests/test_command_count.py`), so a new command either updates the docstring
> and this doc, or fails CI.

## The whole surface

```
st prime                      who am I, what's on my plate         <- the primer
st go <item> [agent]          dispatch. this is the one that matters.
st mail <agent> <message>     send a message to an agent (tmux send-keys)
st task <title>               create a work item
st crew                       who exists, what state, what role
st roles [--check]            the hierarchy, and whether it's real
st role set <agent> <role>    generative: rewrites cards, emits hooks
st new <agent>                create an agent from a card
st stop <agent>               stop it
st log [agent]                what happened
st context <query>            what code should I be looking at? (bobbin)
st doctor [--install]         what's installed, stale, missing (out-of-box)
st project                    materialize the crew cards FROM the graph
```

Thirteen. `--dry-run` is on every command that writes, from commit one. The surface grew past the
original eight by five, each on a specific ask — not drift: **mail**/**task** (the dispatch/tracker
pair, owner-directed), **context** (the bobbin Context protocol, aegis-rhhw), **doctor**
(out-of-box detect/install, Stiwi's direct ask, aegis-q9eh), and **project** (the quipu-registry
projection, aegis-gz57). Each is named on purpose: this doc once
said "eight" while the code had twelve, and a count nobody enforces is a comment — in the one repo
whose whole pitch is the exact count, that was the bug.

The binary is **`st`**, not `shanty`: `shanty` is Stiwi's own tmux command and ours would shadow it
on PATH. This doc said `shanty` in all 29 of its examples long after the entry point was `st`, so
every command a reader copied out of here was uninvokable — the same defect as a wrong count, in the
worse place (GitHub #8).

If it grows a `st convoy`, a `st rig`, or a `st formula`, we've rebuilt the thing we left —
but the guard against that is now the test, not this sentence.

## `st prime` — the primer

The primer answers **"who am I and what do I do next"** in one call, at session start, with no
arguments. It is the single most-used thing in any agent harness — Gas Town's equivalent ran 21 times
in our measurement window — and it is the highest-leverage surface in this CLI, because *every session
starts here.*

```
$ st prime

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
why "did I get primed?" became unanswerable when the hook silently didn't register. `st prime` is
a pure read, safe to run twice, and if you want it at session start you wire it there yourself.

## `st go` — dispatch

This is the command the repo exists for. `gt sling` takes >120 seconds; `--dry-run` alone takes 51s
and **writes nothing**, because the cost is 63 sequential Dolt connections during *resolution, before
any write*. Underneath, dispatch is `tmux send-keys`.

```
$ st go aegis-9h2 ellie

  aegis-9h2 -> ellie          in progress
  sent to pane %5             0.4s
```

```
$ st go aegis-9h2 ellie --dry-run

  would: tracker.update(aegis-9h2, status=in_progress, assignee=ellie)
  would: send-keys -> pane %5
  would NOT: create a convoy, spawn a session, wait for ack

  0 writes. 1 tracker call, 1 send-keys.
```

**`--dry-run` is non-negotiable and it is first, not last.** A real sling was fired as a diagnostic
during this design and hooked an agent with work nobody meant to assign. *Make the question askable
without the consequence.*

### The performance budget is a test, not an aspiration

`st go` must be **under one second**, and the test asserts the *mechanism*, not the stopwatch:

```
tracker calls:  <= 2
connections:    <= 1 per backend
sends:          1
waits for ack:  0
```

Count the connections. A stopwatch on a shared host is exactly the kind of number that flatters — the
`gt sling` regression would have passed a "feels fine" check on a quiet night. **The observable is the
count.**

## `st roles --check` — the hierarchy, verified

```
$ st roles --check

  arnold      administrator  reports: malcolm         hooks: ok
  malcolm     lead           reports: ellie, ian      hooks: ok
  ellie       worker         reports_to: malcolm      hooks: ok
  dearing     worker         reports_to: —            *** ORPHAN ***

  BLOCKED: 1 agent's stop events go nowhere.
```

Three outcomes: **ok**, **broken**, **cannot tell**. If it can't read a card it says so and exits
non-zero. A checker that can only report health is not a checker.

## `st doctor` — the out-of-box feature

```
$ st doctor
  • beads    1.0.5 installed
  • bobbin   0.3.1 installed — 0.6.0 available (STALE)
  ? quipu    present, but cannot report version (known upstream bug: --version opens a store)
  ✗ reactor  not installed
```

Detect is the product; `--install` is a flag. Three states exist to stop three lies: **absent** vs
**unknown** (quipu is present but its `--version` errors by opening a store — "I could not tell" is not
"not installed"), **installed** vs **stale** (bobbin 0.3.1 while 0.6.0 is out — the out-of-box problem
is not "missing", it's "installed and nobody knows what's there"), and detect **touches nothing**.
`--install` prefers a release binary, falls back to a source build only where there's no release
(beads), and **refuses loudly when the toolchain is missing** rather than half-installing. Never
`--break-system-packages` — this host is PEP-668, which is why `st` itself ships via pipx.

## What's deliberately absent

- **`st mail` is thin, not a bus.** It exists (owner-directed), but it is one line — a tmux
  send-keys to an agent's pane, not a message store. The discipline held: a harness that grows a
  message *bus* is on its way to being a town, so mail carries no queue, no threads, no persistence.
  Agents that need durable work have a tracker (`task`/`go`).
- **No orchestration tier.** No mayor, deacon, witness, refinery, polecat. That tier is switched off
  on our host by directive and nothing broke — the strongest evidence we have that it isn't needed.
- **No convoys.** `gt sling` auto-creates one per dispatch. It's a write on the hot path for
  dashboard visibility. `st log` reads the tracker.
- **No `st handoff`.** Gas Town's drops the settings flag and silently produces a hookless
  session. If cycling a session is needed, it's `stop` then `new`, and the card carries the identity.

## Exit codes, because scripts read them

```
0   did the thing
1   refused — a precondition failed (orphan card, missing capability, unknown agent)
2   could not tell — a backend was unreachable. NOT success, NOT failure.
```

Code 2 exists because of a specific bug we shipped: a check that couldn't reach its target reported
CLEAR. **"I could not look" must never render as "fine."**
