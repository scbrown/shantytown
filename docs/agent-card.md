# shantytown — the agent card

> An agent card is the whole definition of an agent: who it is, who it reports to, and what role it
> holds. **Declaring a role rewrites the card and emits that role's stop hooks.** Role creation is
> generative — you don't wire a hierarchy by hand, you declare it and the harness builds it.

## Why the card is the unit

Gas Town spreads an agent's identity across a role env var, a CLAUDE.md, a settings.json registered
outside the workspace, and a launcher flag. We measured what that costs: a session relaunched through
the wrong path silently loses every hook it has, and *looks completely normal from the inside*. The
identity was real; nothing carried it.

One file. If the card doesn't say it, it isn't true.

## The card

```yaml
# crew/ellie.yaml
name: ellie
role: worker            # worker (default) | lead | administrator
reports_to: malcolm     # required unless role: administrator
prompt: |
  You own e2e test coverage.
```

That's the minimum. Three of those five lines are the hierarchy.

## Three roles

Stiwi, 2026-07-16: *"an administrator that receives all stop hooks from their reports except for those
blocked by leads, which take on like work and then also delegate like an administrator"* — and then,
correcting the count: *"oh right and the worker role."*

**`worker` is a role, not an absence.** It is also the default value of `role:`. Those are compatible
and the distinction matters: a worker is a *declared thing with a definition*, so `shanty role set
malcolm worker` is a real demotion with real consequences (its reports get re-pointed, its
`on_report_stop` hook is removed), not a deletion of an attribute.

| role | takes work | delegates | receives stop hooks from |
|---|---|---|---|
| **`worker`** *(default)* | yes | no | — |
| **`lead`** | light work only | yes | its own reports |
| **`administrator`** | no | yes | anything a lead didn't absorb |

The administrator receives **all** stop hooks from its reports **except those a lead absorbed**. That
exception is the entire tier.

Read the table by what each role *refuses*: a worker refuses to delegate, an administrator refuses to
take work, and a lead refuses neither — which is why it needs a limit and the other two don't.

## Declaring a role is generative

Stiwi, 2026-07-16: *"when you create these specialized agent types, it creates these stop hooks for
you and changes the agent card."*

So `role:` is not a label the harness reads later. Writing it **emits** the hooks:

```
$ shanty role set malcolm lead --reports ellie,ian

  card    crew/malcolm.yaml   role: worker -> lead
  card    crew/ellie.yaml     reports_to: -> malcolm
  card    crew/ian.yaml       reports_to: -> malcolm
  hook    malcolm             +on_report_stop   (absorb | delegate | escalate)
  hook    ellie               stop -> malcolm   (was: -> administrator)
  hook    ian                 stop -> malcolm   (was: -> administrator)

  3 cards, 3 hooks. --dry-run to see this without writing.
```

Two properties this must have, and they're the same property twice:

- **The card and the hooks cannot disagree.** They're one write. A card claiming `lead` with no
  `on_report_stop` hook is the failure this whole harness exists to avoid — a role that is *declared*
  and does not *act*.
- **`role set` is reversible and dry-runnable.** Demoting a lead re-points its reports' stop hooks
  upward. If that can't be shown before it happens, it will be discovered afterward.

## The card is the source of truth, and it must be checkable

A declared hierarchy that isn't the running one is worse than no hierarchy, because it reads as
coverage. So the card ships with its own check:

```
$ shanty roles --check

  malcolm       lead            reports: ellie, ian          hooks: ok
  ellie         worker          reports_to: malcolm          hooks: ok
  ian           worker          reports_to: malcolm          hooks: ok
  arnold        administrator   reports: malcolm             hooks: ok
  dearing       worker          reports_to: —                *** ORPHAN: no lead, no administrator ***

  BLOCKED: 1 agent's stop events go nowhere.
```

Three outcomes, not two: **ok**, **broken**, and **cannot tell**. A checker that can only say "fine"
is not a checker — if it can't reach a card, it says so and exits non-zero rather than reporting
health it didn't verify.

## Orphans and cycles are the two ways this breaks

- **Orphan** — `reports_to` empty and role isn't `administrator`. Its stop events have no destination.
  Refuse at write time; don't discover it when the agent stalls.
- **Cycle** — a reports to b reports to a. Refuse at write time.
- **Lead is down** — reports' stop events rise to the administrator **and say so loudly.** A silent
  fallback is how a tier stops existing without anyone noticing. The escalation must name the reason:
  `escalated: lead malcolm unreachable`, not just `escalated`.

## What the card does not hold

Not the tracker. Not the pane. Not the model. Those are harness config, not identity — an agent that
changes multiplexer is the same agent. The card answers *who am I, who do I report to, what do I
absorb*, and stops.

## Open

- **Does a lead need its own lead?** Lean no — 2 tiers, per Stiwi. N tiers is an org chart.
- **Can an administrator have zero leads?** Yes, and then it receives everything, which is exactly the
  configuration we ran and which failed. The harness should note it, not forbid it.
- **Does `role set` restart the agent?** Hooks are read at start on every harness we've measured. If a
  card change needs a relaunch to take effect, `role set` must say so — a config change that silently
  doesn't apply is the same defect as a card that disagrees with its hooks.
