# Automatic notification risk

Automatic advisories interrupt an administrator only for a failure at risk 2
(warning) or 3 (critical). A recommendation, successful action, recovery or
expected quiet-time hold is logged when it changes, then deduplicated. This
policy does not filter human inbox messages, assignment delivery or existing
failure escalation routes.

A missing governor advisory remains a warning when its lane has live agents.
With no live agents it is recorded; becoming live makes the same missing
advisory eligible again. A quiet-time probe failure remains a warning. Healthy
hold and lift transitions do not change enforcement and do not interrupt panes.

Deferral findings at P0 or P1 interrupt the administrator. P2–P4 findings go to
the scheduled log. Unknown priority and exhausted read failures remain loud.
Automatic release still requires the existing fresh-read and mechanical gates.
Raising a logged finding's priority can notify even when its condition is
unchanged. Failed delivery leaves the actionable finding pending.

A Stop hook can be silent only after a recorded Stop more recent than its last
run evidence. Tool activity alone is not a Stop opportunity. Missing opportunity
evidence remains UNKNOWN. Other hooks retain their carrier-activity check.

The notification ledger records observed keys and risk; scheduled logs preserve
routine transitions. It is an observation ledger, not proof of delivery. Only
successful pane sends consume actionable notifications.
