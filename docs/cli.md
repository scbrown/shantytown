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
st input <agent>              what's in their input box: EMPTY | TYPED | GHOST, with the
                              SGR evidence. --clear (typed only) --dismiss. NEVER submits.
st ask <agent>                the QUESTION they're blocked on: prompt, the command being
                              approved, and the numbered options VERBATIM. read-only.
st answer <agent> <N>         select option N. refuses on a pane that isn't on a picker,
                              echoes what it selected, and records who answered.
st roles [--check|set|band|sync]
                              the hierarchy: show it, verify it, write it, import it.
                              `band <agent> <first|normal|support|last>` writes the
                              SURVIVAL band — which agents a usage throttle spares.
st init                       scaffold a NEW deployment (wizard): store, cards, hooks, config
st new <agent>                create an agent from a card
st start [--mode lite|heavy]  BOOT the town by mode: the admin alone, or every card. idempotent
st start <agent>...           bring up exactly these agents (already-up is a SUCCESS, not a refusal)
st stop <agent> [--reason]    stop it, and RECORD that it was deliberate
st log [agent]                what happened
st context <query>            what code should I be looking at? (bobbin)
st doctor [--install]         what's installed, stale, missing (out-of-box)
st tend                       supervise the crew: respawn what DIED, never what was RETIRED
st tend --reauth              relaunch every AUTH-DEAD agent (run AFTER the operator re-logs in)
st tend --target N            respawn only toward N LIVE agents (scale UP on loss; never stops a surplus)
st attach [agent]             attach to a crew member — STARTING them if down (pane+socket resolved)
st attach --no-start          attach only if already running; never create a session
st stats [agent] [--files]    what the crew did: files/skills (capture store), tokens by provider (local transcripts)
st dashboard [admin]          live, tier-scoped view: roster/state/work, self-refreshing
st subscribe                  watch quipu entity events; route governed workflows to the admin
st cycle <agent> [--self]     clear an agent's context WITHOUT destroying its runtime:
                              checkpoint -> stop -> relaunch -> re-dispatch. `/clear`
                              drops bypass into MANUAL; this keeps it. --self REQUESTS
                              your own cycle (an agent cannot stop itself), honoured
                              by `st tend`. --allow-loss to cycle over unsaved work.
st worktree <repo> [agent]    provision an agent's isolated worktree off a SHARED project repo
st push <repo> [agent]        push your branch to EVERY remote — pushing one forks a repo with two
```

Twenty-four. `--dry-run` is on every command that writes, from commit one. The surface grew past the
original eight by nine, each on a specific ask — not drift: **inbox**/**task** (the dispatch/tracker
pair, owner-directed), **context** (the bobbin Context protocol), **doctor**
(out-of-box detect/install, Stiwi's direct ask), and **subscribe** (the quipu events adapter,
routing governed workflows to the admin). Each is named on purpose: this doc once
said "eight" while the code had twelve, and a count nobody enforces is a comment — in the one repo
whose whole pitch is the exact count, that was the bug.

The binary is **`st`**, not `shanty`: `shanty` is Stiwi's own tmux command and ours would shadow it
on PATH. This doc said `shanty` in all 29 of its examples long after the entry point was `st`, so
every command a reader copied out of here was uninvokable — the same defect as a wrong count, in the
worse place (GitHub #8).

Two of the seventeen were RENAMED on 2026-07-19, and the count did not move — a rename is not a
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
3. **Where your stop events go**, and **whether that agent will receive them**. If your lead is
   unreachable, anchor says so *here* — not when you stall and discover it — and it says what
   happens next: the event RISES to the administrator with reason `lead-unreachable` and persists
   on disk. It reports the routing rather than inferring it: the destination comes from
   `tier.find_administrator` and reachability from the router's own predicate, so this line cannot
   contradict what `route_stop` will actually do. It used to, and asserted the opposite of the
   truth: workers were told their stop events went nowhere while every one of them was rising to
   the administrator as designed. `up` here means **will drain**, not "a pane answers to that name".
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

### Every dispatch names its STORE

A dispatch used to be an id and a title. This host has **125 bd stores** (measured 2026-08-01), of
which **11 are embedded** — reachable by no amount of thoroughness against the Dolt server. So an id
alone is not underspecified, it is *unanswerable*, and worse: **a cross-store dispatch and a phantom
id are the same observation.** Measured cost (malcolm, 2026-08-01): dispatched an item, could not
find it in the default store, swept every database on the server with validated positive controls,
got zero rows, and concluded it existed in **no** store. It existed — in an embedded one. The wrong
conclusion shipped as `confidence:extracted` and a lead reinforced it before both were retracted.

So the store rides the payload, always, as the command you would type:

```
$ st go st-9h2 ellie --dry-run

  would: tracker.update(st-9h2, status=in_progress, assignee=ellie)
  would: send-keys -> pane %5
  would: name store -> [st store: bd -C /opt/rigs/aegis]
```

When the item is genuinely **not** in the store the recipient's own workspace resolves to, the tag
stops describing and starts warning — naming both sides, because "this is elsewhere" without saying
elsewhere-*than-what* is not re-checkable:

```
  would: name store -> [st store: bd -C /opt/work/sidecar — DIFFERENT STORE
         from your workspace's (embedded:/opt/work/sidecar/na vs
         db.invalid:3306/beads_aegis); -C is REQUIRED, the id will NOT resolve without it]
```

Three properties worth knowing:

* **The tag is unconditional; the warning is not.** Naming the store only "when it differs from the
  default" requires being right about the recipient's default — but `bd` resolves from the *ambient
  cwd*, so that is a function of where the agent is standing when it types, not something this
  process can compute. A conditional built on a guess goes silent in exactly the case that costs a
  day. Naming it always has no failure mode.
* **Stores are compared by identity, not by path.** The same store has many paths, and not rarely:
  16 of those 125 paths resolve to the one database `db.invalid:3306/beads_aegis`, because
  `~/gt/beads_aegis/.beads` holds no metadata — only a `redirect`. Path equality would have shouted
  "different store" at 15 of 16 *correct* dispatches, until the warning meant nothing.
* **A store we could not read is never reported as different.** "I could not read its metadata" is
  not evidence of difference; rendering could-not-tell as a finding is the same error the incident
  was.

The other half is on the read side: when an id fails to resolve, the error now names the store it
searched **and counts the ones it did not** — absence with a boundary is re-checkable, bare absence
is what invited the generalisation.

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
tracker calls:  <= 3   (resolve, write, READ THE WRITE BACK)
connections:    <= 1 per backend
sends:          1
waits for ack:  0
```

Count the connections. A stopwatch on a shared host is exactly the kind of number that flatters — the
`gt sling` regression would have passed a "feels fine" check on a quiet night. **The observable is the
count.**

**The ceiling moved from 2 to 3, once, and only to buy a read-back.** `st go` was printing
`-> in progress` while the tracker row stayed `open` and unassigned — intermittently, on the same
binary and store that had worked minutes earlier. The send landed; only the write vanished, at exit
0. So the item never entered the worker's haul, `st crew` showed them idle right after a successful
dispatch, and the bead re-entered `bd ready` to be handed to somebody else — duplicate work arriving
underneath the assignee guard built to prevent it.

The distinction that keeps the budget meaningful: it was written against **resolution churn** — 63
sequential connections to answer one question before any write. A single read that confirms the write
landed is not that. **A budget is a guard against work that buys nothing, not a reason to report a
write nobody confirmed.** Anything wanting a fourth call has to make its own case.

A dispatch that needed more than one write+read-back round still succeeds, and says so on stderr —
the fault is intermittent, and one that is silently absorbed is one nobody can ever root-cause.

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

## `auth-dead` — login expired, and the pane lies `idle` (aegis-arma)

Measured 2026-07-22: an operator re-login rotated the shared credential and **all 9 live crew went
`● Login expired · Please run /login` at once** — with the ready UI still up and the input box
empty, which is `idle` to every other predicate. So the dead fleet stayed on the free list, Rule
Zero held the coordinator's stop hostage to nine unfeedable corpses, and tend's cycle driver
prompted a saturated dead pane over and over into the very banner it could not see. Recovery was
nine by-hand `st stop` + `st new`.

Now it is a named verdict. The banner is runtime chrome, so `ClaudeRuntime.auth_dead()` owns the
markers (tail-only, trailing blanks dropped, **line-anchored** — a `grep -n` over a dead pane's
scrollback prints the banner mid-line, and a substring match would have called the grepping agent
auth-dead; measured in the session that wrote it). `work_state` takes the answer as a passed-in
flag like `ui_up`/`awaiting`; `AUTH_DEAD` outranks everything but busy/wedged (a pane genuinely
computing has working auth by construction). It falls out of `free`, out of feed_check's feedable
set, and out of the cycle driver's saturated set — one verdict, every consumer.

Recovery is **one command**: `st tend --reauth`, run **after** the operator re-logs in — `/login`
in a pane is an interactive browser OAuth flow nothing can drive, so a relaunch (which re-reads
the refreshed credential) is the whole remedy. Deliberately a flag on `tend`, not an auto-heal on
the default pass: a pass cannot know whether the operator re-logged in yet, and relaunching against
a still-stale credential kill-loops the fleet. Same rule shape as `--cycle-stale`: the supervisor
*reports* (`auth-dead` is a fault, exit 2), the explicit flag *acts*. The kill honours the `st
stop` ownership guard (a name match is not permission to kill), the respawn is the same Tender path
as a normal pass, and the verify is honest about its boundary: it proves the process **came up**,
not that it is authed — the banner only shows on the first failed API call, so if the operator did
not re-log in first, the next `st crew` says so.

Two honest limits. An agent whose auth expired while *idle* shows no banner until something makes
it try an API call — detection keys on the failed turn, so a freshly-dead quiet pane still reads
`idle` until first touch. And an agent whose *response text* begins a line with the exact banner is
indistinguishable from the banner — accepted: the line anchor already refuses every quoted/grepped
form actually measured.

## `manual` — permission posture, and the agent that cannot run a command

Measured 2026-08-01: three cards carried no `dangerous`, so their agents launched in **manual
mode** — a human keystroke to approve **every** bash call. An unattended agent that needs a
keystroke per command cannot make progress *by construction*, and it reads `up`, `current` and
`busy` the whole time. One evening of that: six coordinator picker-answers across five agents, two
agents blocked simultaneously, one agent dead twice, and a permission gauntlet where each approval
only revealed the next. It was found by capturing pane footers **by hand** — the defect had been
rendered at the bottom of every one of those panes from the moment they launched.

So `st crew` grows a **posture** column, on the same principle as the settings column beside it:

| | means |
|---|---|
| `bypass` | the pane shows `⏵⏵ bypass permissions on` — prompts are off, the agent can act |
| `MANUAL` | live, ready UI up, **no** bypass line — a human must approve every call |
| `?` | no ready UI (trust dialog, consent screen, blocking picker). Not rounded to either |
| `—` | not live. A down pane has no posture; what its *card* lacks is a different block |

Two things it deliberately does **not** do.

- **It reads the PANE, never the card.** `dangerous` is read at *launch*, so a card edited without a
  relaunch changes nothing about the running process — reporting the card would report a fix as
  landed while the agent is still stopped dead. This bead's own fix was verified by the footer
  flipping, and that is the only reason we know it took. Same launch-time rule the `STALE` column
  exists for, one field over.
- **It derives `MANUAL` negatively.** Only the bypass footer has been measured; pinning a string for
  the manual-mode line would be a marker never observed passing, which this repo has already paid
  for twice in `READY_MARKERS`. Absence of bypass on a live ready pane is the signal, and it stays
  correct for every other mode the runtime may grow or rename.

### The dormant half: the arming moment now asks about *both* faults

The three bad cards became visible only because they were **un-retired** that day. `retired = true`
had hidden the defect for as long as it held: a retired card is never launched, so its defect never
becomes a symptom. Un-retiring is the moment it surfaces — and it surfaced *twice over*, because
those same cards also had no `workspace` — the fault the arming pre-flight landed for, the same day.

That is not a coincidence, and it is the reason the two checks are one list rather than two gates.
`retired = true` conceals **any** launch fault, so faults accumulate silently on retired cards and
arrive together the moment one is re-armed. `launchable.launch_gaps(card)` is that list — the
workspace half is `workspace.unlaunchable()` unchanged, the permission half is `dangerous`, and the
next one belongs there too rather than in a third place.

**Only one of them may refuse**, and the asymmetry is deliberate:

| fault | at `--unretire` | why |
|---|---|---|
| no/bad `workspace` | **REFUSES** (`--force` overrides; the reason still prints) | nobody *elected* the cwd systemd handed the supervisor |
| no `dangerous` | **says it loudly, proceeds** | `dangerous` is opt-in *by design* here, and an attended agent that wants a prompt per call is making a real choice |

```
$ st tend --unretire ian
  ⚠ ian carries no `dangerous`, so it launches in MANUAL MODE — a human must approve EVERY
    bash call. […] If that is deliberate, nothing here is wrong. If it is not: `dangerous` on
    the card AND a relaunch (the mode is read at launch).
  ian is tended again.
```

A gate that refused manual mode would override an election the harness deliberately offers, and
would fire on every card that simply never set the field. The defect here was never that
manual mode is impossible to *want* — it was that choosing it by accident was impossible to *see*.
So it is said at the arming moment, when a person is present and can act, and `st crew` goes on
saying it every time thereafter, which is the durable half. Retiring is never gated or warned —
only the direction that *starts* something is.

`st crew` names the same cards in its own block, so a trap that is currently harmless (the agent is
down; nothing is stalling) is still visible **before** someone walks into it. Each `Gap` carries a
short label for that column and a full sentence for the refusal: the rule is decided once, the
length is the caller's, and the two surfaces cannot end up talking about different cards.

## `st roles --check` — the hierarchy, verified

```
$ st roles --check

  arnold      administrator  reports: malcolm         hooks: ok live: ok
  malcolm     lead           reports: ellie, ian      hooks: ok live: ok
  ellie       worker         reports_to: malcolm      hooks: ok live: ok
  dearing     worker         reports_to: —            *** ORPHAN ***

  BLOCKED: 1 agent is not correctly attached to the tier — see the reason on its row above.
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

## `st init` — the scaffold wizard

A fresh clone could not reach a runnable state without hand-authoring JSON. Four artifacts had four
different origins — the store directory was a `mkdir`, the crew cards came from a hierarchy file fed to
`roles sync`, the settings files were a side effect of `roles set`, the config was hand-written — and
nothing assigned the `pane` field that every launch, attach, stop and supervise path resolves an agent
through. `st roles set` cannot help: it *refuses* an agent that has no card yet.

```text
$ st init

  st init — a few questions. Enter accepts the [default].

  Administrator name (the coordinator) [admin]
  > sattler
  Worker names, comma-separated (blank for none — the admin can be alone)
  > arnold, billy
  Parent directory for agent workspaces, one dir per agent (blank = launch in the current directory)
  > /srv/crew
  Startup mode — heavy/lite (lite starts the administrator ALONE) [lite]
  >
  Hibernate the administrator? off/idle/schedule/either (off = its stop wakes it at every turn) [off]
  > idle
    Wake when this % of the answerable crew is idle [60]
  > 70

  store    /home/you/project/.shanty
  dir      crew/
  card     sattler      administrator  pane st-sattler  workspace /srv/crew/sattler
  card     arnold       worker         pane st-arnold   workspace /srv/crew/arnold
  hooks    settings/administrator.settings.json
  config   /home/you/project/.shanty/shantytown.toml

  3 agent(s) · startup mode 'lite' · hibernate 'idle'

  Write this? [yes]
```

### It writes through the existing seams, and adds none

Cards go in through the registry — which is where a card gets its generated pane. Roles and stop-hook
routing go through `tier.role_set`, the same generative operation `st roles set` uses, so a crew's cards
and its hooks cannot disagree. The settings files come from the same emitter. Nothing here is a second
way to declare a crew; every artifact is one you would otherwise have written by hand, in the same
place and format.

That is also why it isn't `roles sync --interactive`: **sync projects an authority that already exists**
(the graph, or a hierarchy file) onto cards, and is idempotent against it. **init asks and creates the
authority** — including two artifacts sync has no opinion about at all, the settings files and
`shantytown.toml`. Hanging "invent a crew" off a flag of a command whose contract is *mirror what is
already declared* would make sync's most dangerous property — it overwrites cards to match a source —
reachable from a prompt.

### It never overwrites

- **An existing store is a refusal.** Cards or a config already there → exit 1, nothing written. A
  second init is far more likely to be a mistyped `--root` than an intent. `--force` says it on
  purpose, and even then **no existing card is rewritten** — an agent already carrying
  `pane: shanty-arnold` and a workspace keeps both, and only missing cards are created.
- **An existing `shantytown.toml` is kept**, never regenerated over.
- **A bad name is refused before anything is written.** Agent names become both filenames and tmux
  session names, so they're validated against the intersection (`[a-z0-9][a-z0-9_-]*`) up front rather
  than at launch. `.` and `:` are tmux address syntax and are rejected.

### It never blocks on a prompt

No terminal and no `-y` is a **refusal**, not an `input()` that hangs. A wizard that blocks forever
inside a script or a hook is worse than one that says it cannot ask.

```bash
st init -y --admin boss --crew ada,bo --mode lite     # scripted: asks nothing
st init -n                                            # every path it would write; writes nothing
```

A rejected answer is re-asked with the reason, and the rejection goes out on a channel *separate* from
the asking — routed through the same door it would call `input()` again and eat your next line as the
answer to a question nobody asked, shifting every later answer by one.

Finally, it **loads the config it just wrote**. A file this command emits but `st start` would refuse is
the worst possible handoff.

### Generated pane names

Every card written by any path — `init`, `roles set`, `roles sync` — leaves the registry with a pane, so
it is startable immediately. The default is `st-<name>`: `shanty` is a different program on the same
PATH (the same reason the binary is `st`), and a pane prefix is how you read *whose* session a tmux row
is on a host running more than one orchestrator.

This can only ever **fill a gap** — a card that already names a pane keeps it, whatever the convention
was when it was written. A fleet whose live sessions are `shanty-*` must not have its cards repointed at
`st-*` by a projection, which would leave every card addressing a session that does not exist while the
real ones ran on.

## `st start` — booting the town, by mode

One command that takes *"the crew I want tonight"* and makes it true, with an exit code that says
whether it did.

```
$ st start
  mode 'lite' from the built-in defaults (no config file) — 1 agent(s): sattler

  + sattler      started      launched into 'shanty-sattler', hooks verified

  mode 'lite' · 1 selected · started 1 · 1 up · 0 fault(s)

  attach: `st attach sattler`   ·   roster: `st crew`
```

**Lite is the default, and lite is the administrator alone.** One agent's context, one agent's bill —
and the admin is the one agent that can decide who else is needed and dispatch to them. `--mode heavy`
brings up every card. A mode is a *named set* of crew in
[`shantytown.toml`](shantytown.toml.example), not a `--count`, because "how much fleet" is a decision
you make once and re-use.

```bash
st start                      # the configured mode (default: lite = the admin)
st start --mode heavy         # every non-retired card, admin first
st start --mode night         # a mode your config defined
st start billy harding        # exactly these two
st start --mode heavy -n      # who WOULD start, and who is already up. launches nothing
```

### It is idempotent, and that is the whole point

`already-up` is a **success**, not a refusal, and a live agent is never launched over. The operator
who most needs a boot command is the one who does not know what is currently running — so running it
twice is not an error, and the second run launches nothing.

That is why it is not `st new` in a loop and not a flag on `st tend`. Both of those have a guard that
is load-bearing where it is and wrong here: `new` **refuses** a live session ("never replace a live
agent"), which for a boot is exactly backwards; `tend` refuses to respawn an agent it has no launch
stamp for (another orchestrator's crew), and a cold host has no stamps for anyone.

It launches through the *same* seam as `st new` — workspace ensured, MCP kit provisioned, skills
linked, stop hooks verified on the live process — so an agent booted this way is not a cheaper agent.

### What it will not do

- **It will not start a `retired` agent** — not under `heavy`, not when a mode names it explicitly.
  Retirement is a deliberate, durable shutdown; `st tend --unretire` is what undoes it. Every
  skipped retiree is *reported*, so silence never looks like a config line that was ignored.
- **It will not call an unverified launch a launch.** An agent whose runtime never appeared in the
  pane is `unverified`, is not counted in `up`, and the command exits 2.
- **It will not attach.** A boot that attached would block the shell that ran it, and the
  systemd/cron caller cannot afford a foreground tmux client. Attaching is `st attach`.
- **It will not partially boot on a refusal.** A bad mode, a malformed config or a name with no card
  refuses *before* launching anything: exit 1, nothing created.

Exit codes: **0** every selected agent is up · **1** refused, nothing launched · **2** the pass ran
and somebody is not known to be up.

## `st attach` — and it starts what is down

```bash
st attach                     # the administrator (resolved from the registry), started if down
st attach weaver              # weaver's pane, started if down
st attach -r weaver           # observe only: no keystroke can land in their work
st attach weaver --no-start   # attach ONLY if already running; never create a session
```

*"weaver is down — `st crew` to see who is up"* is true, and an answer to a question nobody asked: the
operator typing `st attach weaver` has already decided they want weaver's pane. Sending them to a
second command to get there is the whole cold-start friction, so the cold-start path is one command:

```bash
st attach                     # brings up the administrator and drops you in its pane
```

`--no-start` keeps the old behaviour for the caller that needs it — a script attaching to whatever is
running must be able to promise it creates nothing. A card with **no pane** is still refused rather
than launched into an invented session: a session absent from the card is invisible to `st crew`,
`st stop` and `st tend`.

A launch that could not be *verified* still attaches, loudly. The session exists; what could not be
established is that the runtime came up. Putting your eyes on that pane is the useful next action —
exiting instead would hide the evidence behind a second command.

## The stop decision — one verdict, five ranks

Every agent's Stop hook is **one command** that returns **one verdict with one
reason**. It was a chain of independent commands that could each block the same
stop with no shared answer; the full argument is in
[`docs/stop-policy-spec.md`](stop-policy-spec.md).

| # | condition | verdict |
|---|---|---|
| 0 | route + persist my own stop event upward | *side effect, never blocks* |
| 1 | a pending event is **urgent** — an untracked-work alert, or one that rose past an unreachable lead | **BLOCK**: deliver |
| 2 | **Rule Zero**: free feedable workers **and** dispatchable work both exist | **BLOCK**: dispatch |
| 3 | **hibernate** declines | **ALLOW**, loudly |
| 4 | a **deliverable** pending event (sender not mid-flight) | **BLOCK**: deliver |
| 5 | idle because a **governor tier** holds the whole queue | **ALLOW**, loudly |
| 6 | otherwise | **ALLOW** |

Read the order as the answer to *"why did my coordinator not stop?"* — it is
answerable from this table alone, which is the property the old chain lacked.

### `dispatchable` means *passes the priority floor*

Measured live, within sixty seconds of itself:

```
st tend -n     governor  usage 57% · 50% tier · dispatch only P1 and above
st go <P2> tim refused: the usage governor's 50% tier is engaged and <bead> is P2
feed_check     RULE ZERO — 1 feedable worker IDLE and 15 DISPATCHABLE bead(s).
               Top ready: <P2>; <P2>; <P2>
```

A **blocking** stop hook was ordering a dispatch the governor forbade, and naming
as its top candidates the exact beads that would be refused. `dispatchable` meant
"open, unassigned, unblocked" and never asked whether the work could be *sent*.

Two changes, and the second is the one that matters:

1. **feed_check asks the governor.** `feed_check.throttle()` filters the ready list
   through `Verdict.admits` — the same resolver `st go` gates on, never a floor
   re-derived here. A duplicated constant would be two copies of a comparison
   whose sign is already documented as a foot-gun.
2. **Rank 5 allows the stop and NARRATES it.** Under an engaged tier, an idle
   fleet with a full queue is the *correct* state — and it is observationally
   identical to a feeder that has broken. A coordinator that cannot tell them
   apart assumes the second, because that is the one it can act on.

> **The escape hatch stays.** `st go`'s refusal still ends *"or raise its priority
> if it really is that important"* — an earlier draft of this fix reworded it and
> that decision was **withdrawn** (Stiwi, on the bead). The bump is legitimate
> because it is **recorded**: `bd history` carries priority per revision with
> timestamps, so an inflation is visible and diffable after the fact. What was
> wrong was never the hatch; it was the blocking hook *herding* an automated actor
> toward it. Remove the push and a re-grade becomes a judgement someone made,
> rather than one the mechanism extracted. Two known limits on that audit trail
> are tracked separately — history entries read `Author: beads` (so *who* bumped is
> not recorded), and `bd compact`/`flatten` can squash the trail away.

`st crew` shows the same fact on the roster: a free agent under an engaged floor
is **THROTTLED-IDLE**, which is not the same failure as an agent nobody fed — and
Rule Zero exists to catch the second. It is evaluated only when somebody is free,
so a saturated fleet pays nothing for it, and it fails to silence on any error.

**Ranks 1–5 fail open.** Any error — tmux, bd, a config typo — allows the stop; a
hook that wedges an agent on a transient hiccup is worse than the stall it
prevents. Rank 0 does not get to fail *silently*: persisting your own stop event is
survival, not a decision, so a failure there is reported rather than swallowed into
a clean-looking allow.

### Hibernate

```toml
[hibernate]
enabled = false
max_quiet_minutes = 60     # 0 = only wake when something pushes
```

An administrator's Stop hook **drains**: it blocks at the end of every turn and
re-injects whatever arrived. That is what keeps a coordinator awake, and billing,
all night. In `lite` mode the shape you want is *admin comes up → assigns hauls →
goes quiet*, and going quiet is exactly what an unconditional drain prevents.

Four properties make this a policy rather than a work-loss bug:

- **Rank 2 sits above it.** Hibernate can only fire when nothing is urgent *and*
  there is nothing to hand out — and when Rule Zero overrides it, the output says
  so by name. You cannot accidentally sleep a coordinator that has work to
  dispatch.
- **It sleeps through ordinary reports, deliberately.** With nothing dispatchable,
  a *"kelly stopped"* is informational; there is no decision to make. That is the
  feature, and it is safe only because —
- **Nothing is consumed.** Rank 3 allows the stop without reaching the drain that
  marks events delivered, so the backlog is intact and the next wake sees all of
  it. Count it without consuming it: `st anchor --events sattler`.
- **It removes a self-wake, never a wake.** `st tend`'s pushes, an `st inbox`, a
  dispatch, or you typing in the pane all still reach it.

`max_quiet_minutes` is **not** a schedule to wake on — it bounds how long waiting
reports may sit unread while nothing pushes. `0` disables the bound, which is a
legitimate choice: a push is a wake with a *reason*, and that beats a timer.

Leads and workers never hibernate: a lead's drain is how it absorbs its reports.

### Down on purpose is not a fault

The forcing functions above can only demand **more** dispatch, so every state where
the right answer is *stop* has to be expressible to them — otherwise the mechanism
produces the wrong instruction at exactly the moment cost control matters (GitHub
#29: an operator out of usage credits stopped nine of eleven crew on instruction and
was told, nine times, to put them back).

Three ways to say it, in increasing strength:

| | what it says | who honours it |
|---|---|---|
| `st stop <agent> --reason "…"` | *I stopped this one, now.* Recorded durably; **not** a retirement | `st crew` and the administrator's drain report it as deliberate. `st tend` still respawns it |
| `st tend --retire <agent>` | *…and do not bring it back.* Lives on the card | `st tend` never respawns it; `st start` skips it; the drain never lists it |
| `[fleet] stood_down = true` | *the whole fleet is quiet by decision* | Rule Zero yields (rank 2), and the drain withholds every dispatch step |

All three **announce themselves** rather than going quiet. A gate that silently
stops firing is indistinguishable from a gate that is broken, which would be a worse
version of the same bug — so the drain prints what it withheld and how to undo it,
and a risen escalation still surfaces through all three. Standing a fleet down
declines to hand out work; it does not decline to *answer*.

A stop record is **cleared on relaunch**, beside the launch stamp and for the same
reason: it describes a stop that is current. One left behind would make the agent's
next real crash read as somebody's decision.

## `st doctor` — the out-of-box feature

```
$ st doctor
  • beads    1.0.5 installed
  • bobbin   0.3.1 installed — 0.6.0 available (STALE)
  ? quipu    present, but cannot report version (known upstream bug: --version opens a store)
  ✗ reactor  not installed
  • st       installed from ~/src/shantytown @ 41d9fc2d
```

### …and it asks the question about ITSELF

The last row is the one that was missing. doctor reported installed-vs-available for four tools and
never once about `st` — the tool that audits deployment drift was the only tool exempt from the
audit, and it is the one whose staleness silently corrupts every other row it prints.

It is not a version check: `st --version` is permanently `0.0.1`, so a check built on it could never
fail. It compares the two things that DO move — the **recorded source path** pipx would rebuild from,
and that checkout's **git HEAD** against the canonical checkout.

```
  ✗ st       `st` was installed from '~/src/shantytown-wt/alice', NOT the canonical
             checkout '~/src/shantytown'. Whatever is in that directory — including
             uncommitted work — is what the whole fleet is running, and a `pipx reinstall` will
             faithfully rebuild it. Fix: pipx install --force ~/src/shantytown
```

That is a real condition that occurred on 2026-07-20 (aegis-daoh): a deploy run as
`pipx install --force <my own worktree>` silently re-pointed the **fleet's** recorded source at one
crew member's private tree, and a later `pipx reinstall` faithfully rebuilt *that* rather than the
shared checkout somebody had just pulled. Nothing in the system could say so. Two rules, both
load-bearing:

1. **A recorded source that is not the canonical checkout is an ERROR, not a note.**
2. **It fails toward "cannot tell."** If it cannot read its own pipx metadata or git HEAD, that is
   exit 2 — never a pass. Uncertainty dominates the exit code, as everywhere else here.

Honest boundary: a green row proves the *source path and HEAD* line up. It does **not** prove the
installed **bytes** match that HEAD — pipx copied them at install time and the checkout can be pulled
forward afterwards. That needs a build stamp; until then this is a floor, not a guarantee.

**Deploying `st`:** always the canonical path explicitly, never bare `pipx reinstall` (it rebuilds
whatever path was last recorded, which is exactly how the above happened).

```
cd ~/src/shantytown && git pull
pipx install --force ~/src/shantytown
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

No field means `claude`, which is every card that never said otherwise, and `st anchor --harness`
prints it either way. Two are implemented — `claude` and `codex` — and a name we do not implement is
**refused**, never quietly replaced with the default: a card that asks for `opencode` and silently
gets `claude` is a launch that succeeded at being the wrong thing.

The point of the field is what it forced. The launcher hardcoded Claude Code in places that had to
agree and had no way to — the argv, the `settings.json` *format*, the artifact's **name**, the
compose invariant's literal `--settings`, the cmdline reader that looked for that same flag, and the
readback that parsed Claude Code's hook schema. Those are one decision, and codex is what proved it:
codex has **no settings flag at all**, it reads `config.toml` out of `$CODEX_HOME`. So a `Harness`
owns all of it — `launch()`, `settings()`, `settings_name()`, `render()`, `carries_settings()`,
`settings_in_cmdline()`, `read_stop_directions()`, `provision()`, `hooks()`.

Two consequences worth knowing before you put `"harness": "codex"` on a card:

- **A codex card can be a lead or an administrator.** codex's Stop hook delivers `decision: block`
  with a reason to the model, so it declares the capability the tier gates on. That reverses what
  this repo used to say, and `docs/adapters.md` carries the evidence and the version caveat.
- **`st new`'s liveness verify does not understand codex panes yet.** The ready-UI markers are
  Claude Code's, and a marker nobody has watched pass is not a marker — so a codex launch reports
  *could-not-tell* (2) rather than a confident wrong answer. The agent is launched; the verify is
  the part that cannot see it.

### Mixing programs across one crew

A card names its own program, and `[harness]` answers for the ones that don't:

```toml
[harness]
default = "codex"            # the fleet runs codex

[harness.by_role]
lead = "claude"              # …except the roles that receive stop events
administrator = "claude"
```

**Most specific wins: card → role → fleet → `claude`.** A card that names its program is never
moved by a config written afterwards — the table is for the silent, and a resolved default is
never written back onto the card (that would be a claim nobody made, and it would survive the
config being changed back).

Both halves are validated at load. An unimplemented harness name is refused, because a typo in
`default` moves every card in the fleet and would otherwise surface as `st new` failing agent by
agent. A role nobody has is refused too — a rule that applies to nobody reads as applied.

Mixed fleets work because the tier is program-blind: a codex worker sends its stop event with
`python -m shantytown.stop_event send` and a Claude Code lead drains it, since those hook commands
are shantytown's own CLI rather than either program's. The artifacts are per **(harness, role)**,
so `st roles set` on a mixed crew writes `worker.settings.json` *and*
`codex/worker/config.toml`, and `st roles --check` reads both formats back.

Every codex fact in the implementation was read out of codex's own source, with the file named
beside it (`shantytown/codex.py`), because a guess about another CLI's flags is exactly the kind of
code that looks shipped and has never run. The Claude Code path is pinned byte-for-byte against the
pre-split launch strings (`tests/test_harness.py`) and its emitted settings file is pinned against
the pre-codex bytes (`tests/test_codex_harness.py`).

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

$ st crew --governor
ok 45/50/5400 24/45/248400
# each budget is current/next-threshold/seconds-until-reset.
#   `-` in the second slot = no higher tier; `-` in the third = no reset published.
#   Neither is ever rendered as a number: a bar reading 0 would say "resets now" forever.
# both windows, because they exhaust independently and are asymmetric — and they
# refresh on completely different clocks (95 minutes vs 70 hours, measured).
# The reset is here because "throttled" and "throttled for another 1h35m" are
# different sentences to the operator: the first invites intervention, the second
# invites waiting. Seconds, not a wall-clock time — the consumer is a program, and
# it formats "resets in 1h35m" itself.
# A tier in force is NAMED after the numbers:
#   ok 70/80/5400 24/45/248400 dispatch only P0 and above [five_hour >= 70%]
# Blind cases carry NO digits, so a bar cannot scrape a stale reading:
#   lost   the signal could not be read
#   off    no governor configured

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
