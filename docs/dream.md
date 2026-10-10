# SLEEP/DREAM

Dream work is admitted by an explicit request or an actionable event that
creates an ordinary haul item. `st fleet tend` never creates or wakes a dream
cycle merely because an interval elapsed. Existing dream haul items continue
through the normal work feeder.

`st work dream` shows the last cycle, domain rotation and capacity policy.
`st work dream --run -n` previews an explicitly requested cycle; `--run` creates
one. The live worker, provider signal, headroom, delegation reserve and queued
cycle checks remain. A busy provider can receive a queued P4 artifact without
interrupting its pane; a free selected provider receives one requested-cycle
message. A discrepancy detector may create actionable work through the ordinary
tracker/event path; no new periodic detector or discrepancy producer is added.

Legacy `[dream] enabled` and `interval_minutes` settings remain readable for
configuration compatibility. They never authorize automatic work. Configure
`min_headroom_pct` and `domains` for explicit requests.

Cycles alternate between `consolidate` (measured domain reconciliation) and
`dream` (reviewable improvement proposals), rotating domains. They are bounded,
read-mostly work: no infrastructure, code or deployed configuration changes.
Normal triage turns reviewed artifacts into ordinary work.

State lives at `<root>/dream-state.json` and advances only after observed tracker
creation. A failed explicit request can be retried explicitly; tend does not
blindly retry it by creating another LLM turn.
