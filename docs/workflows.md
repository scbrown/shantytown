# 🔀 Workflows & events

Shantytown doesn't just dispatch — it **prioritizes** and **reacts**.

- **Prioritized workflows.** At the administrator's stop, the drain composes a
  ranked workflow from fleet state — a stopped worker to re-dispatch, an idle
  worker to give work, an escalation to decide — and injects it straight into the
  admin's terminal. Blast-radius weighting (via Hank) is opt-in; it runs with no
  backend at all.
- **Governed events.** Shantytown subscribes to Quipu entity events — a governed
  `aegis:Workflow` required by a code change, a policy effect, a doc gone stale —
  and acts on them: creating and dispatching work, or routing it to the admin
  (`st ops subscribe`).

The administrator is a real coordinator: it may assign and dispatch autonomously,
not just advise.
