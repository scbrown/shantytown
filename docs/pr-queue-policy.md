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
