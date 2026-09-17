# 📮 Routing: there is nothing in the middle

**`st inbox` *is* `tmux send-keys`.** That's not an implementation detail — it's the product.

```
st inbox ada "go read st-1"
   │
   ├─ registry.get("ada")        → identity: role, reports_to, pane
   ├─ pane = "crew-ada"          → the address IS the pane
   ├─ panes.exists(pane)?        → NO  → exit 2 "could not tell". nothing sent.
   └─ tmux send-keys -t <pane>   → the message. that's the delivery.
```

**No message bus. No queue. No delivery guarantee — because there's nothing to guarantee.** The pane
is either there or it isn't, and you're told which.

| routing outcome | exit | what it means |
|---|---|---|
| delivered | **0** | the keys went into a live pane |
| no such agent / no pane | **1** | refused. nothing sent. |
| pane named but gone | **2** | *could not tell* — never a cheerful success |

For the messages that must survive a dead recipient — a handoff, a protocol step — `--durable`
persists to the tracker **first** and only then attempts the live send, so the recipient picks it up
on their next anchor.

**Identity resolves through the registry, not through a config file you hand-edit.** The graph is the
truth; the agent card is a projection of it. Writes go to the graph, reads may come from the card,
never the reverse — so an agent's address can't quietly drift from reality.
