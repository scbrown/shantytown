# Deferred candidate selection

The sweep selects deferred work, dated open work, original asks, and blocked
waiters in the two configured graphs. Each old UNION branch joined the broad
Action type population independently. The new query first selects distinct
status/date/note candidates within each graph, then checks their Action type.
Field projection and cross-graph shadow refusal remain unchanged.

[Recorded comparison](assets/deferred-status-first.cast) displays retained
bounded read results: 9.074 seconds before, 1.324 seconds after, with the same
525 distinct subject/graph pairs and no truncation. These sequential samples
establish candidate parity, but load variation limits the timing comparison.
The recording displays the retained results rather than replaying the queries.

The isolated native test includes both date spellings, original asks, blocked
gates, non-Action exclusion, and cross-graph shadow refusal. A candidate limit
still refuses before any field read. Acceptance requires normal source adoption
and 24 hours of scheduled sweep passes with zero 408s and an observed successful
pass; a source test or one fast read does not establish that result.
