# Durable browser handoffs

The optional bridge accepts `crew-handoff-v1` envelopes from Creel and deposits a
pointer-sized message in the existing `br` inbox. It returns success only after
reading that message back. `fleet_send` remains local to a browser burst.

Run the bridge on the inbox host, with a persistent spool and an **explicit** br
repository. Nothing installs or starts a background service automatically:

```sh
python -m shantytown.inbox_bridge --root /var/lib/crew-bridge \
  --repo /srv/crew --config /etc/crew-bridge.json
```

It binds loopback port 8766 by default. For a remote browser, put it behind an
HTTPS reverse proxy, preserve Authorization and Origin, and restrict request size
and time. Creel accepts HTTP only on loopback. Back up the spool together with
the inbox store; deleting either destroys the corresponding delivery evidence.
The bridge does not provide availability, backup, or an independent identity issuer.

The private configuration contains exact allowed browser origins and separately
provisioned bearer principals (use a random secret of at least 32 characters):

```json
{
  "origins": ["https://creel.example.org"],
  "principals": [{
    "token": "REPLACE_WITH_A_RANDOM_PRIVATE_BEARER",
    "identity": {
      "harness": "creel", "agent": "tab-1", "session": "session-1",
      "key_id": "key-1", "introducer": "launcher", "binding": "binding-1"
    },
    "recipients": ["worker"]
  }]
}
```

Keep the file owner-readable only. The trusted launcher/operator must supply the
identity from the shared attestation contract. The bridge binds that complete
identity to the bearer; it does **not** authenticate identities copied out of a
browser message, tab label or URL. Provision one principal per attested session,
limit its recipients, and restart with updated configuration to revoke a token.
A missing browser Origin is permitted for authenticated non-browser clients;
present Origins must match the allowlist exactly. No cookie authentication is used.

The Creel launcher configures its tab through the write-only API:

```js
CreelDurableInbox.configure({ endpoint: 'https://bridge.example.org', token: bearer });
```

The bearer is held in a closure and is not returned by fleet tools or persisted
in browser storage. Configure each new tab/session explicitly; clearing with
`configure(null)` removes the in-memory credential. Do not put secrets in model
prompts. The optional tools are `fleet_handoff({envelope})` and
`fleet_handoff_status({receipt_id})`.

`POST /handoffs` accepts the unchanged envelope, requires an explicit authorized
Shantytown target and `task.pointer`, and returns `crew-inbox-receipt-v1`:

- `receipt_id`: stable SHA-256 of the canonical `[origin, handoff_id]` pair;
- `handoff_id`, `task_id`, `envelope_sha256`: identify the exact immutable payload;
- `inbox_id`, `backend`, `delivery: "persisted"`: observed durable inbox entry;
- `acknowledged`: whether the recipient marked that entry read.

`GET /handoffs/<receipt_id>` reads the same receipt, including closed inbox entries.
It neither acknowledges the message nor changes task state. Acknowledgement means
**read**, never acceptance of ownership, successful work, or lease transfer. The
transport retains owner/result/failure fields without interpreting them as actions.

Recipients use the ordinary `st inbox worker` and
`st inbox worker --read-id <inbox_id>` paths. Inspect the retained envelope locally:

```sh
python -m shantytown.inbox_bridge --root /var/lib/crew-bridge --show RECEIPT_ID
```

The inbox body is `handoff:<receipt_id> <envelope_sha256> <task.pointer>`, at most
493 UTF-8 bytes. Full payloads live in SQLite with a committed transaction before
inbox creation. A host file lock serializes deposits and recovery. The bridge
checks all inbox statuses before creating and again before reporting success.
The files and tracker receipt readers implement the same narrow read seam; the
HTTP CLI deliberately requires `br`, so the configured production route is the
same ticket-backed inbox recipients already read.

If a reply is lost, resend the **identical envelope**, including its ID. It finds
the existing entry even after acknowledgement. Reusing an ID with changed bytes
returns 409; use a new envelope ID for a later snapshot. Ambiguous duplicates,
changed/missing entries, failed reads or failed writes return an unproven result,
never an acknowledgement. Restore missing store data before retrying if it was
removed administratively. Idempotence covers bridge deposits sharing one spool;
independently copying spools or manually duplicating inbox markers is not supported.

Browser storage and BroadcastChannel are not part of this durability guarantee.
