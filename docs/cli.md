# shantytown — the CLI

> Stiwi, 2026-07-16: *"there needs to be a cli and a primer"*.
>
> Gas Town ships ~110 commands and we measurably use a dozen. This is not a smaller version of that
> list. It is **the short set**, and the discipline is that each command earns its slot — the count is
> now pinned by a test (`tests/test_command_count.py`), so a new command either updates the docstring
> and this doc, or fails CI.

## The whole surface

```
st anchor [--short|--events|--harness]
                              who am I, what's on my plate         <- the anchor
st go <item> [agent]          dispatch. this is the one that matters.
st inbox <agent> <message>    put a message in an agent's inbox (send-keys; -d persists)
st inbox [--count|--read]     read your own inbox
st task <title>               create a work item
st crew [--count]             who exists, what state, what role, WHO IS FREE
st roles [--check]            the hierarchy, and whether it's real
st role set <agent> <role>    generative: rewrites cards, emits hooks
st new <agent>                create an agent from a card
st stop <agent>               stop it
st log [agent]                what happened
st context <query>            what code should I be looking at? (bobbin)
st doctor [--install]         what's installed, stale, missing (out-of-box)
st project                    materialize the crew cards FROM the graph
st tend                       supervise the crew: respawn what DIED, never what was RETIRED
```

Fourteen. `--dry-run` is on every command that writes, from commit one. The surface grew past the
original eight by five, each on a specific ask — not drift: **inbox**/**task** (the dispatch/tracker
pair, owner-directed), **context** (the bobbin Context protocol), **doctor**
(out-of-box detect/install, Stiwi's direct ask), and **project** (the quipu-registry
projection). Each is named on purpose: this doc once
said "eight" while the code had twelve, and a count nobody enforces is a comment — in the one repo
whose whole pitch is the exact count, that was the bug.

The binary is **`st`**, not `shanty`: `shanty` is Stiwi's own tmux command and ours would shadow it
on PATH. This doc said `shanty` in all 29 of its examples long after the entry point was `st`, so
every command a reader copied out of here was uninvokable — the same defect as a wrong count, in the
worse place (GitHub #8).

Two of the fourteen were RENAMED on 2026-07-19, and the count did not move — a rename is not a
new command, and the test that pins the number is what proves it:

- **`prime` -> `anchor`.** An agent's anchor is what holds it to its work; the word is the noun and
  the verb. `prime` named the *harness's* act of loading a session, and we had inherited it from the
  tool we left.
- **`mail` -> `inbox`**, because it is now a real inbox rather than a verb — see below.

If it grows a `st convoy`, a `st rig`, or a `st formula`, we've rebuilt the thing we left —
but the guard against that is now the test, not this sentence.

## `st anchor` — the anchor

The anchor answers **"who am I and what do I do next"** in one call, at session start, with no
arguments. It is the single most-used thing in any agent harness — Gas Town's equivalent ran 21 times
in our measurement window — and it is the highest-leverage surface in this CLI, because *every session
starts here.*

```
$ st anchor

  You are ellie — worker, reports to malcolm.
  You own e2e test coverage.

  ON YOUR PLATE
    ▶ st-9h2  Restore the den service        (in progress, 40m)

  YOUR LEAD
    malcolm (lead) — up. Your stop events go to him.

  CONTEXT (bobbin)
    scripts/e2e/den.sh · roles/den_server/tasks/main.yml

  KNOWN (quipu)
    "auth-api was cowboy-deployed and died once before" — 2026-06-30
```

Four things, and each one has to earn its line:

1. **Identity from the card.** Not from an env var, not from a file in the workspace. One source.
2. **The work.** One item, or none. A surface that prints a backlog is a dashboard.
3. **Where your stop events go**, and **whether that agent is up**. If your lead is down, anchor says
   so *here* — not when you stall and discover it.
4. **Context and knowledge** — bobbin and quipu, first-class, and both optional. With the `none`
   adapters, those two sections vanish and anchor still works.

### anchor is a read. It must never write.

Gas Town's primer has a `--hook` mode that fires at SessionStart and mutates state. That coupling is
why "did I get primed?" became unanswerable when the hook silently didn't register. `st anchor` is
a pure read, safe to run twice, and if you want it at session start you wire it there yourself.

## `st go` — dispatch

This is the command the repo exists for. `gt sling` takes >120 seconds; `--dry-run` alone takes 51s
and **writes nothing**, because the cost is 63 sequential Dolt connections during *resolution, before
any write*. Underneath, dispatch is `tmux send-keys`.

```
$ st go st-9h2 ellie

  st-9h2 -> ellie          in progress
  sent to pane %5             0.4s
```

```
$ st go st-9h2 ellie --dry-run

  would: tracker.update(st-9h2, status=in_progress, assignee=ellie)
  would: send-keys -> pane %5
  would NOT: create a convoy, spawn a session, wait for ack

  0 writes. 1 tracker call, 1 send-keys.
```

**`--dry-run` is non-negotiable and it is first, not last.** A real sling was fired as a diagnostic
during this design and hooked an agent with work nobody meant to assign. *Make the question askable
without the consequence.*

### `--note` / `--note-file` — a caveat that rides WITH the work

```
$ st go aegis-9h2 ellie --note "a design doc is landing; pull YOUR OWN workspace, do NOT blind-pull"

  aegis-9h2 -> ellie          in progress
  sent to pane %5
  note: a design doc is landing; pull YOUR OWN workspace, do NOT blind-pull
```

Dispatch used to be item-and-agent and nothing else, so a qualifier had nowhere to go. Both
workarounds were wrong in a specific way:

* **`st inbox` after the go** — `send-keys` into a pane that has *just started work*. That is exactly
  the mid-flight garble `go`'s triage refuses; sending it by hand routes **around** the safety.
* **a bead comment** — durable, but out-of-band and permanent. The note was about *this dispatch at
  this moment*; it lands on the **item**, for every future reader. Measured (sattler, 2026-07-19):
  four beads left carrying a pull warning that was stale inside a week.

`--note` is composed into the **same payload**, so it passes the same triage gate and the same
verify. The work and its caveat are delivered together or refused together — and that atomicity is
the point: **a caveat that arrives separately from the work it qualifies can arrive after the worker
has already acted.**

Two properties worth knowing:

* **The note is flattened to one line.** The transport is `send-keys -l <text>` plus a separate
  Enter, so a literal newline in the payload is a *submit*. An unflattened three-line note would
  dispatch line one and type the rest into a pane already working. `--dry-run` previews the note
  **as it will be sent**, and a successful dispatch echoes it back.
* **`--note-file <path>` (or `-` for stdin) for anything long or quoted.** Prose in a shell string
  gets `` `...` `` and `$(...)` expanded before `st` ever sees it — the note either runs or is
  silently deleted while the command reports success. A file is inert.

An unreadable `--note-file` is a **refusal** (exit 1, nothing sent, nothing written), never a
note-less dispatch: sending the work without its caveat is the failure this flag exists to close.

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

## Reading a pane: a dim suggestion is not queued input

Pane text is this tier's **only** liveness oracle — `st crew` and `st go` both judge from it. So a
state the pane renders ambiguously is a state the tier cannot reason about, and there was one:

```
❯ file the goldblum install role bead
```

That is either Claude Code's **dimmed placeholder** over an empty buffer (the agent is idle and
fine), or **real unsubmitted text** left by a `send-keys` whose Enter never landed (the aegis-16e
stall). `capture-pane -p` returns plain text, and the dim attribute is precisely the bit it strips —
so the two are the same bytes. It was ambiguous in **both** directions, and both cost:

* **False stall.** An administrator read the line above as a stalled dispatch and sent Enter into a
  healthy agent's pane to "un-stall" it. Enter did nothing. Only typing a literal character — which
  made the whole line vanish, because it was placeholder over an *empty* buffer — showed the agent
  had never been stuck. That is the coordinator corrupting a pane it was trying to help.
* **Hidden stall.** Symmetrically, a genuinely wedged worker reads as "just a suggestion, it's fine"
  and gets left wedged.

**The fix is to capture the bit, not to guess it.** `capture()` takes `attrs=True` (tmux `-e`) and
`triage.input_state()` judges the box on the attribute. Measured across all 18 live panes,
2026-07-20:

```
placeholder   \x1b[39m❯\xa0\x1b[2mbd ready — pick the next item\x1b[0m     <- SGR 2 = dim
real input    \x1b[38;5;246m❯\xa0\x1b[39mzzPROBEzz                        <- no SGR at all
empty         \x1b[38;5;246m❯\xa0\x1b[39m
```

`st crew` gains a fourth verdict beside `idle`/`busy`/`?`: **`queued`** — UI up, nothing in flight,
and text sitting unsubmitted. Not free, not working. It never lands on the free list, because
`send-keys` **appends** to a pane's buffer rather than replacing it: dispatching there produces one
concatenated line that is neither message. `st go` REFUSEs the same pane.

Two rules fall out, and they are the load-bearing part:

1. **Do not "fix" a suspicious pane by pressing Enter at it.** That was the defect, not the remedy.
   `queued` is a state to *report to the agent's owner*, never one to type your way out of.
2. **When the attributes are missing, the answer is `?`.** A stripped capture with text in the box
   returns `UNKNOWN`, which degrades to `?` in `st crew` and to REFUSE in `st go` — never to `idle`.
   Refusing on doubt is cheap; dispatching into a buffer you cannot see is the incident.

This is still a heuristic on somebody else's TUI rendering, and it is the *cheap* tier of the fix.
The better one is for a worker's own hook to report `idle`/`running`/`queued` into `.shanty` so the
tier reads a **fact instead of a rendering** — the Stop event is the natural carrier (aegis-w9z1).

## `st roles --check` — the hierarchy, verified

```
$ st roles --check

  arnold      administrator  reports: malcolm         hooks: ok live: ok
  malcolm     lead           reports: ellie, ian      hooks: ok live: ok
  ellie       worker         reports_to: malcolm      hooks: ok live: ok
  dearing     worker         reports_to: —            *** ORPHAN ***

  BLOCKED: 1 agent's stop events go nowhere.
```

Three outcomes: **ok**, **broken**, **cannot tell**. If it can't read a card it says so and exits
non-zero. A checker that can only report health is not a checker.

**Three legs**, each a strictly stronger question than the last:

| leg | question | column |
|-----|----------|--------|
| lines | does every agent report *somewhere*? | the verdict |
| hooks | does the **role's emitted artifact** carry the stop hooks the graph requires? | `hooks:` |
| live  | does the **process actually running in the pane** carry them? | `live:` |

The third leg exists because the second is not evidence (aegis-0v97). An artifact states
*intent*; `st` does not own every process that answers to a name in its registry. Measured on
the real store: `dearing` was `role=lead`, `lead.settings.json` emitted `[send, drain]`, and the
check was **green** — while the process in its pane had been launched by a foreign launcher with
settings carrying no stop hook at all. Seven workers routed to it and every one of their stop
events was write-only. `tmux.py` already states this rule for the kill path — *a pane NAME match
is never sufficient permission to reap*. The `live:` leg is that same rule for liveness: a name
match is never sufficient evidence of **drain**.

A **down** pane is not a fault: `route_stop` already rises to the administrator when a lead is
unreachable, loudly and with a reason. The `live:` leg catches what that path cannot see — pane
**up**, wiring **wrong**, so nothing rises and nothing drains.

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

## `st inbox` — a message, and somewhere for it to land

```
st inbox ian "go read st-1"          send: straight into ian's pane (send-keys)
st inbox -d ian "HANDOFF: qdal.2"    durable: into ian's INBOX, then a live send
st inbox                             read: what is unread, for me. Marks nothing.
st inbox --count                     one integer, for a status bar. Marks nothing.
st inbox --read                      ACK: mark my unread messages read
```

The default send is unchanged and is still one line of `tmux send-keys`. What is new is the **type**.

The old `mail -d` persisted a message as an ordinary tracker item assigned to the recipient, and then
**nothing ever read it back** — the sender was told "they'll pick it up on their next prime", which
was not true, and the item landed on the recipient's **plate**, which holds exactly one thing. So a
message did not merely fail to arrive; it *evicted the agent's actual work*. Both halves are the same
mistake: a message is not a work item, and it needs its own read side.

So there is an `Inbox` protocol (`shantytown/inbox.py`) with three methods and two implementations:

| | |
|---|---|
| `deliver(to, body, frm)` | the write. On the store before it is read, so a recipient who is down still gets it. |
| `unread(me)` | the **pure read**. Marks nothing. `--count` is `len()` of this. |
| `mark_read(me, ids)` | the ack, separate and explicit. `--read` is the only thing that calls it. |

Selected by the **same `--backend` switch as the tracker** — one switch, or you send on one backend
and read on another. `files` gives a store beside `events/` (structurally off the plate: no plate
reader globs that directory). `beads` maps a message onto a real bead — `inbox: <body>`, assigned to
the recipient, labelled `inbox`, closed when read — which is Stiwi's ask verbatim: *"an inbox concept
we can map to beads or other ticket modules."* On that backend the exclusion cannot be structural, so
`inbox.is_message()` is the one predicate both plate readers use, and it excludes the legacy `mail:`
prefix too — those items are open and assigned on the live store right now.

There is still no bus: no queue, no threads, no routing, no retry, no daemon. Three methods.

## The harness — Claude Code is *a* harness, not the shape of the world

A card can name the agent program it runs:

```json
{ "role": "worker", "harness": "claude", "workspace": "/home/w" }
```

No field means `claude`, which is every card today, and `st anchor --harness` prints it either way.
The point of the field is what it forced: the launcher hardcoded Claude Code in **two** places that
had to agree and had no way to — the argv in `ClaudeRuntime.compose`, and the `settings.json` *format*
in `settings_for_role`. Those are one decision (if you launch a different program, `--settings` is not
its flag and its hook schema is not this one), so a `Harness` now owns both halves.

Claude is the only implementation, and adding a second one is not something to do speculatively: a
guess about another CLI's flags is exactly the kind of code that looks shipped and has never run.
What this buys today is that the second one would touch `harness.py` and a card, and nothing in the
tier. The refactor is pinned byte-for-byte against the pre-split launch strings (`tests/test_harness.py`).

## Machine-readable output — five flags, not five commands

An external status bar needs a handful of values out of shantytown. It gets them as **flags on the
commands that already answer those questions**, never as new subcommands: the count is the thesis,
and "something wants to poll this" does not earn a slot.

```
$ st anchor --short
aegis-1o3g

$ st anchor --short            # empty plate
                              # (nothing on stdout, exit 0)

$ st anchor --events
2

$ st crew --count
3/9

$ st anchor --harness
claude

$ st inbox --count
2
```

The contract, because a program depends on it:

- **One value on stdout, and nothing else** — no banner, no label, no colour, no trailing prose.
  Empty output means *nothing to show*; every human affordance is suppressed when the flag is passed.
- **Exit 0 even when the answer is nothing.** Errors keep the usual codes and go to **stderr**, so
  stdout stays parseable: an empty stdout with exit 0 is "nothing", with exit 2 it is "I could not
  look". Same distinction as everywhere else here.
- **`--short`, `--events` and `--harness` read exactly what `st anchor` reads** — same `$SHANTY_AGENT` resolution,
  same `--backend`/`--repo`. A status bar showing a different plate than the primer would be worse
  than no status bar.
- **`--events` never drains.** The count comes from `events.pending()`, a read that marks nothing.
  `drain()` answers the same question by *consuming* — it marks each event delivered (the BLOCK-ONCE
  rail) — so a bar polling `drain()` every few seconds would deliver the tier's stop events to a
  status bar and the administrator would never be told it had them. A read that destroys the
  delivery guarantee is the worst kind of read, and it would have looked fine.
- **`--harness` names the agent program the card runs** (harness.py). A card with no `harness`
  field prints `claude`, because that *is* the answer — an empty segment would read as "no harness".
- **`inbox --count` never marks anything read.** Same rule as `--events`, one type over: listing and
  counting are reads, and `st inbox --read` is the separate, explicit ack.
- **`--count` is `busy/total`, and total is not the roster size.** It is the number of agents whose
  busy/idle state we can actually answer; an agent that is down, has no pane, or shows a pane with no
  runtime UI is in **neither** number. Counting the unknowns into the denominator would print a
  capacity figure that was never measured, in the same font as one that was.

## What's deliberately absent

- **`st inbox` is thin, not a bus.** The default send is still one line — a tmux send-keys to an
  agent's pane. `-d/--durable` adds a *store*, and only a store: the inbox is three methods
  (deliver / unread / mark_read), with no queue, no threads, no routing, no retry, and no delivery
  daemon. A harness that grows a message *bus* is on its way to being a town. What it is NOT is
  optional plumbing: the old `mail -d` used to persist a message that nothing ever read back, onto the
  recipient's **plate**, where it evicted their actual work. The inbox is the read side and the type
  that keeps a message off the plate (`shantytown/inbox.py`), and it is pluggable — files by default,
  a real bead with `--backend beads`, and any other ticket system behind the same protocol.
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
