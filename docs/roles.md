# shantytown — hierarchical roles

> Reports' stop hooks terminate at their **lead**. What the lead doesn't absorb rises to the
> **administrator**. That's the whole hierarchy.

**Roles live in the agent card, and declaring one is generative** — it rewrites the card and emits
that role's stop hooks in the same write. See [`agent-card.md`](agent-card.md) for the card, the
`role set` contract, and what happens to a lead's reports when it's demoted or goes down.

## Three roles

| role | takes work? | delegates? | receives stop hooks from |
|---|---|---|---|
| **worker** | yes | no | — |
| **lead** | **light work only** | yes | its own reports |
| **administrator** | **no** | yes | anything a lead didn't absorb |

A lead is not a smaller administrator. **A lead is a worker who also absorbs.** That distinction is the
design: it means escalation has a layer that can *just do the thing* instead of routing it.

## What "stop hook" means here

When a worker finishes or stalls, its stop event goes to its **lead** — not to the administrator, and
not to a queue. The lead does one of three things:

```
ABSORB    — it's light. Do it. Nothing rises.
DELEGATE  — hand it to another worker. Nothing rises.
ESCALATE  — genuinely needs the administrator. Rises.
```

**The administrator only ever sees what a lead couldn't absorb or place.** That's the filter, and it's
the entire point of the tier.

## Why this exists — the thing it fixes

Without a lead tier, every stop event reaches one coordinator. We ran that: **one agent received
every report from 14 crew**, and the failure wasn't overload — it was that **absorbing and delegating
compete for the same attention.** A coordinator who stops to do a two-minute fix isn't coordinating;
a coordinator who never does is a router that adds latency to trivia.

The lead tier resolves it by making "just do it" a *legitimate* outcome at the layer where the
information already is.

## The rule that keeps a lead a lead

**"Light" must be defined, or every lead becomes a worker and the tier collapses.** The failure mode
is real and it is not hypothetical: the lead absorbs, absorbs, absorbs, and stops absorbing stop
hooks because it's heads-down on the third thing it absorbed.

Proposed, and it needs to be enforced by the harness rather than by intent:

- **A lead may hold at most one absorbed task at a time.** Second one arrives → delegate or escalate.
- **A lead with an absorbed task still receives stop hooks.** If it can't, it isn't a lead right now —
  and the harness should say so rather than silently queue.
- **Absorbing is logged as a decision**, so "this lead never delegates" is a query, not a vibe.

If a lead's absorb rate approaches 100%, the tier isn't working and the harness should surface that.

## Escalation is a decision, not a fallback

An escalation must carry **why the lead didn't absorb it**. Without that, the administrator gets a
router with extra steps and no signal.

```
escalate(item, reason)   # reason ∈ {needs-authority, needs-decision, too-large, blocked-on-human}
```

**"I was busy" is not an escalation reason.** That's a capacity problem, and it should surface as
one.

## Open questions

1. **Can a lead have leads?** Arbitrary depth is tempting and probably wrong. Two tiers solve the
   observed problem; N tiers is an org chart. **Lean: depth 2, until something proves otherwise.**
2. **Who assigns leads?** Config, presumably. Not dynamic — a hierarchy that reorganises itself is a
   scheduler, and we said we're not building one.
3. **What happens when a lead is down?** Reports' stop hooks must not vanish. Either they rise to the
   administrator, or the harness refuses to start reports whose lead is absent. **Lean: rise, and say
   loudly that they rose.** A silent fallback is how you discover in six weeks that a tier stopped
   existing.
4. **Does the administrator ever see a worker directly?** Only if that worker has no lead. Otherwise
   the filter has a hole and the tier is decorative.

## What this is not

- **Not a mayor.** No autonomous assignment. A lead delegates what it was already handed; it doesn't
  go looking for work to distribute.
- **Not an approval chain.** Escalation moves *information*, not permission.
- **Not an org chart.** Two tiers, config-defined, because a specific failure demanded it.
