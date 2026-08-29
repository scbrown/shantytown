# SLEEP/DREAM

`st` can use otherwise-idle subscription capacity for one bounded reflection
cycle at a time. Dreaming is background work, never a second priority queue that
competes with operational beads.

```toml
[dream]
enabled = true
interval_minutes = 360
min_headroom_pct = 20
domains = ["ontology", "infra", "codebases", "fleet-config"]
```

`st tend` schedules a cycle only when all of these are true:

1. The interval is due.
2. At least one idle provider has no ordinary ready work it can actually accept.
3. No prior dream task remains ready.
4. An idle, feedable agent has a healthy provider reading.
5. That provider has at least `min_headroom_pct` remaining and is outside its
   configured `delegation_reserve_pct`.

Signal loss is not capacity. A missing or stale governor reading leaves DREAM
asleep. The default is disabled, so merely upgrading `st` never spends tokens.

Cycles alternate between:

- `consolidate`: reconcile one rotating domain against Quipu and create
  `dream-discrepancy` artifacts for measured drift.
- `dream`: create reviewable `dream-proposal` artifacts for functional or
  non-functional improvements.

Both modes are read-mostly. Their generated bead explicitly forbids applying
infrastructure, code, configuration, or ontology changes during the cycle.
Normal triage turns a reviewed artifact into ordinary work later.

"Can actually accept" reuses the foreground dispatch rules: work assigned to
another agent, decision-gated work, dependency-blocked work, and work held below
that provider's current governor floor do not suppress DREAM. Ordinary work that
does clear those gates still preempts reflection for that provider. This keeps a
perpetually non-empty board from making the scheduler inert without turning
DREAM into a competing priority queue.

`st dream` shows the policy, last cycle, next due time, and domain rotation.
`st dream --run -n` previews the next eligible cycle without writing. `--run`
ignores the interval and enabled bit for an operator-requested cycle, but it does
not bypass foreground dispatchability, signal health, headroom, or reserve
protection.

Schedule state lives at `<root>/dream-state.json`. It advances only after the
tracker returns the created bead ID. A failed creation therefore remains due and
is retried on a later tend pass. Every created task records mode, domain,
provider, measured headroom, and the evidence/output contract in its description.
