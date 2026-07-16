# shantytown — identity: quipu is the truth, the card is a projection

> Stiwi, 2026-07-16: *"the design says that cards are the source of truth when thats actually
> incorrect, quipu should be the source of truth"*.
>
> Correct, and the earlier version of this doc was wrong. **Quipu holds identity. The card is a
> materialized view of it** — generated, never hand-edited, and diffable against the graph.

## Why the card can't be the truth (the argument is our own evidence)

The first draft said: *Gas Town spreads identity across a role env var, a CLAUDE.md, a settings.json
outside the workspace, and a launcher flag — so put it in one file.*

That diagnosis was right and the fix was wrong. **A file in a workspace is still a copy**, and we have
measured precisely what copies do here:

- an allowlist lived in two places; updating one left the other stale, and it was invisible because
  the two agents already covered were in *both* lists (aegis-u7fo)
- a hook script resolved per-crew, so **clone drift silently decided behaviour** — a stale clone fires
  successfully and just does the wrong thing
- a vocabulary copied into a formula forked and rotted until it contradicted its own source
  (`fp-stale-copy-drift`)

**14 crews × 1 card each = 14 sources of truth = the same trap.** The answer to "identity is scattered
across four files" was never "make it one file." It's *"make it one graph that everything reads."*

## And the model already exists — I should have looked before designing

Checked against the live deployment, not from memory:

```
targetClasses:  aegis:CrewMember   aegis:Rig   aegis:Polecat   aegis:Person   aegis:Overseer
shapes:         aegis:ReportsToShape   aegis:ManagedByShape   aegis:MemberOfShape   aegis:OwnsShape
live graph:     sentinel · ian · strider · mayor · arnold · goldblum · malcolm   (already populated)
```

**`aegis:ReportsToShape` already exists.** The first draft invented a `reports_to:` YAML key for a
relation the ontology already models and the graph already holds. That's the whole correction in one
line: the hierarchy isn't a new thing to store, it's a query.

## The shape

```
  quipu  ── the truth ──────────────────────────────────────────
    ellie  a aegis:CrewMember ; aegis:reports_to malcolm ; aegis:role "worker"
    malcolm a aegis:CrewMember ; aegis:reports_to arnold  ; aegis:role "lead"
           │
           │  shanty project        (materialize — one direction, always)
           ▼
  card   ── a cache the runtime can read ───────────────────────
    crew/ellie.yaml     # GENERATED. Do not edit. Edits are overwritten.
```

**Writes go to the graph. Reads may come from the card.** Never the reverse.

## Why a projection exists at all

Because a runtime cannot query SPARQL at startup. Claude Code reads a prompt and a settings file. So
the card exists for exactly one reason: **the runtime needs a file.** It is a rendering target, not a
record.

That is a *deliberate* copy, and a deliberate copy is fine **only because it is diffable against a
single authority** — which is the exact fix u7fo landed on: one authority, no distributed state.
Card-as-truth gives you 14 sources and silent drift. Card-as-projection gives you 1 source and 14
caches that a query can check. **The difference isn't the copy. It's whether the drift is detectable.**

## `shanty role set` — writes the graph, then re-projects

```
$ shanty role set malcolm lead --reports ellie,ian --dry-run

  quipu   malcolm  aegis:role     "worker" -> "lead"
  quipu   ellie    aegis:reports_to        -> malcolm
  quipu   ian      aegis:reports_to        -> malcolm
  project crew/malcolm.yaml, crew/ellie.yaml, crew/ian.yaml
  hooks   malcolm  +on_report_stop (absorb | delegate | escalate)
  hooks   ellie, ian  stop -> malcolm  (was -> arnold)

  1 graph write. 3 cards projected. 3 hooks emitted. Nothing written (--dry-run).
```

Role creation stays **generative** — Stiwi, 2026-07-16: *"when you create these specialized agent
types, it creates these stop hooks for you and changes the agent card."* The only change is *where the
truth lands*: the graph first, then the card and the hooks fall out of it. Card and hooks are both
projections of one write, which is why they cannot disagree.

## `shanty roles --check` — now a real check, because there's something to check against

```
$ shanty roles --check

  malcolm    lead           reports: ellie, ian    card: fresh    hooks: ok
  ellie      worker         reports_to: malcolm    card: fresh    hooks: ok
  ian        worker         reports_to: malcolm    card: STALE    hooks: ok
                            └─ graph says reports_to=malcolm, card says arnold. GRAPH WINS.
  dearing    worker         reports_to: —          *** ORPHAN: stop events go nowhere ***

  BLOCKED: 1 stale projection, 1 orphan.
```

With card-as-truth this check was impossible — there was nothing to compare to, so "is the hierarchy
real?" was unanswerable, which is how a tier stops existing unnoticed. **The graph makes drift a query
instead of an outage.**

Three outcomes, always: **ok**, **broken**, **cannot tell**. If quipu is unreachable it says so and
exits 2. It never reports `fresh` for a card it couldn't compare.

## The tension this creates — flagging, not quietly resolving

`docs/adapters.md` says quipu is **first-class and optional**: *"an agent with no bobbin and no quipu
still starts, still works, still stops."* If quipu holds identity, that's no longer true — **you cannot
start an agent whose identity you can't read.**

So quipu now has two distinct jobs, and they should not share a switch:

| job | what it holds | optional? |
|---|---|---|
| **registry** | who exists, who reports to whom, what role | **NO — required** |
| **knowledge** | episodes, facts, what we learned | **yes — `none` adapter still valid** |

The `none` knowledge adapter survives. There is no `none` registry.

**What this costs, stated plainly:** shantytown gains a hard dependency on a graph database, and
"smaller than what it replaces" gets harder to defend. Two honest mitigations, and I'd want Stiwi's
call rather than my preference:

- **The projection is the degraded mode.** Quipu down → the harness still *runs* on last-known cards;
  it just refuses to *change* roles. Identity reads survive an outage; identity writes don't.
- **The registry interface is tiny** — `who_am_i`, `reports_to`, `role`, `set_role`. Small enough that
  a second implementation (even a flat file) is a weekend, which is `adapters.md`'s own two-
  implementations rule applied here. If that second implementation is hard, the registry has leaked.

## Orphans, cycles, and a lead going down

- **Orphan** — no `reports_to` and role isn't `administrator`. Stop events have no destination. Refuse
  at write time, in the graph, where it's a query rather than a directory scan.
- **Cycle** — a→b→a. Refuse at write time.
- **Lead down** — reports' stop events rise to the administrator **and say why**:
  `escalated: lead malcolm unreachable`. A silent fallback is how a tier stops existing unnoticed.

## What identity is not

Not the tracker, not the pane, not the model. An agent that changes multiplexer is the same agent.
The graph answers *who am I, who do I report to, what do I absorb*, and stops.

## Open

- **Does the projection need to exist at all?** If a runtime could be handed identity at start
  (env/prompt injection), the card disappears and there is exactly one representation. Worth testing
  before we ship a cache we didn't need.
- **Who writes crew into the graph today?** They're already there — sentinel, ian, arnold, malcolm.
  Something populated that, and shantytown should use it rather than open a second writer.
- **Does `role set` need a relaunch to take effect?** Hooks are read at start on every runtime we've
  measured. If so, `role set` must say it — a config change that silently doesn't apply is the defect
  this repo exists to refuse.
