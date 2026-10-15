# Change a work specification with its owner in view

```sh
st work item rescope work-123 --spec-file new.md --quote "Limit this to the retention audit." \
  --requester operator --summary "retention audit"
st work item ack work-123
```

These commands require the selected Seeds backend and its rescope capability.
The description is replaced and stamped `SPEC vN`; the request's verbatim quote,
actor and session remain in a comment from the same transaction. Empty,
layered or oversized specifications refuse. The configured Seeds byte cap starts
at 4,096 bytes. A dry-run validates the same request without committing it.
The acting agent's recorded session supplies provenance, or an explicit
`--session` may be used when that record is unavailable.

An ACK reads the exact current generation and binds the write to it. A newer
rescope or owner change refuses a stale ACK. Owner start/claim transitions with
known session provenance record the starting spec; these facts and explicit
ACKs are queryable through Seeds' portable `seed_spec_awareness` definition.
They are not inferred from notification delivery.

The existing priority/due briefing includes `SPEC CHANGED vN-1 -> vN (unacked)`
until the owner ACKs. The administrator sees unacknowledged changes with age.
All these lists share the 400-byte context cap. Spec notices can include deferred
work without making it dispatchable. Remote workers reuse the complete scalar
plate projection. The recipient Stop adapter reads only bounded spec metadata,
with a one-second read bound; partial or conflicting data remains UNKNOWN.

The existing scheduled reminder sweep handles due work, external unlocks and
spec changes through one ledger and sender. A default spec pointer is persisted
in the owner's inbox without live input and appears at its next Stop boundary.
Urgent P0 pointers use the existing durable live-input lane. An unreachable
recipient retains the pointer; acceptance by the sender is not proof of arrival.
The recipient fences each generation before emitting its capped notice, and the
session ceiling takes priority. Delivery receipts are confirmed independently
of owner ACKs. Ambiguous delivery cannot authorize another send; an ACK whose
response was lost is reconciled by exact receipt readback.

`[overdue] spec_changes = false` is the default. Enable it only through reviewed
configuration projection after the matching Seeds SDK, scheduled source and
recipient hook are installed. Acceptance requires an original scheduled run,
its retained receipt and actual recipient boundary arrival. The private controls
exercise the real candidate CLI/SDK, quote/lint, ACK, mixed shared ledger,
committed-ACK/EOF reconciliation and once-per-generation recipient notice; they
do not certify a running installation.

Rollback disables/removes new configuration before reverting the parser. Preserve
items, bitemporal history and notification receipts; pause governed spec edits
until a supporting client is restored. The README capability section is maintained
separately from this feature documentation.
