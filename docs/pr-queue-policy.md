# Private queue policy prototype

The `shantytown.pr_queue` library implements the read-only portion of aegis-opw.
Its caller supplies a complete, fresh repository inventory and independently
verified crew author bindings and exact-head review evidence. Shared forge login
names do not establish who authored or reviewed a change.

`evaluate(snapshot, now=..., cap=...)` returns open count, median age, per-author
WIP admission, advisory eligibility and deterministic stale event keys. A cap is
required; this prototype chooses no fleet-wide cap. Unknown author bindings block
new admissions because their outstanding work cannot be counted reliably. Missing
or invalid evidence does not make a PR eligible. Explicit hold clearance, exact
head/base, independent named reviewer and a review digest are required. These
inputs are assertions of the caller, not authentication performed by the library.
The eventual adapter must use the deployed trusted helper to verify them again.

`pending_events` keeps an event pending until verified delivery is acknowledged.
The event identity includes repository, PR number and exact head. Attempts and
lost responses must not be acknowledged as delivery; the sender must reconcile
against a durable destination before retrying. This library sends no messages,
starts no agent and enables no timer.

Eligibility is separate from execution. Malcolm's serialized writer and the
trusted neutral merge helper retain complete green checks, fresh base and review
verification, expected-head merge, holds/blackout/rollback gates and remote merge
receipt checks. Native auto-merge is not enabled. A read-only queue policy cannot
replace these controls.

Acceptance remains open: integrate the author-bound registry (aegis-62g), named
review assignment, complete live inventory and metrics, verified event delivery,
serialized helper execution and actual one-week under-cap observation. Unit
controls establish the library boundary only. No dashboard or production
activation is claimed.

The private `pr_binding` library adds explicit primary-marker registration and a
0600 journal under a private directory. A selected-board adapter must attest the
reviewed graph identity and return fresh records for the known bound IDs only.
The forge adapter must provide complete comments attributed to the registered
crew writer, normalize exact head/state/body/draft, and implement draft conversion
through the supported forge API. These adapters are not installed by this change.

Registration rejects a closed bead, another owner or mismatched PR state. A
reconcile request rereads the authoritative bead; an old event cannot close a
currently open bead. Deferred beads draft their bound PR; closed beads close it.
A public comment carries the bead ID and status; the full private reason remains
in the journal. Intents survive lost responses and require read reconciliation
before another write. Changed heads, primary markers, owners, reasons or board
identity hold the pending action. There is no mass import or unregistered action.

Integration still required: CLI binding, known-ID selected Seeds reader, actual
GitHub adapter, primary-marker CI admission, producer migration, disabled job
entry and reviewed observed activation. The library tests are private harness
controls, not real forge state mutation or scheduled-path acceptance.

A foreground adapter is available with `python -m shantytown.pr_binding`.
Supply the reviewed server/graph and a private registry path explicitly. Its
`register` operation proves the known bead and exact existing PR, while
`reconcile` applies only registered lifecycle actions. `create` composes the
complete overlap preflight with author ownership and durable creation intent;
ambiguous creation requires explicit verified registration before another create.
Registration of a matching PR reconciles that intent without replaying the POST.
None of these entries arms a job or intercepts other API clients.

Observed private foreground control: PR189 registered to its author-owned active
bead, followed by an empty lifecycle reconcile. No forge state or comment write
was needed. This proves the read/registry entry, not the draft/close write path.
