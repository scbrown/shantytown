# Cross-harness handoff envelope

`shantytown.handoff` defines the versioned task/result value that can cross between a
Shantytown session and a Creel browser tab. It deliberately does not send or persist
anything. A pane write, browser channel, or durable inbox is a transport decision; it
does not change the envelope's state or make delivery durable.

Version `crew-handoff-v1` carries:

- `origin`: an attested identity reference (`harness`, `agent`, `session`, `key_id`,
  `introducer`, `binding`);
- `target`: a harness and optional agent routing hint;
- `task`: stable task identity, plus optional title and pointer;
- `ownership`: a stable lease ID and either no owner (`queued`) or a complete attested
  owner identity;
- `state`: `queued`, `claimed`, `succeeded`, or `failed`;
- terminal evidence: a result pointer for success, or structured code/message/retryable
  fields for failure.

The validator rejects unknown fields, incomplete identities, control characters,
owner/state disagreement, ambiguous terminal evidence, and reopening a terminal state.
`canonical_bytes()` sorts every object key and removes insignificant whitespace, so the
same envelope produces the same bytes in Shantytown and Creel.

Example:

```python
from shantytown.handoff import canonical_bytes, transition, validate

queued = validate(received_value)
claimed = transition(queued, "claimed", ownership={
    "lease_id": queued["ownership"]["lease_id"],
    "owner": attested_session_identity,
})
wire_bytes = canonical_bytes(claimed)
```

Do not fill identity fields from a pane name, tab label, message body, or
`BroadcastChannel` sender. They are references to the common session-attestation
contract and must come from its authenticated binding.
