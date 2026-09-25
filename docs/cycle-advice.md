# Cycle advice — keep or cycle at a handoff

Depth answers whether a session *can* continue. It never answered whether it
*should*: a session about to start the next bead in the same module was cycled
anyway, and a session idle past its prompt-cache TTL carried its whole transcript
into unrelated work, paying a full cache write to do it.

`st agent advise` adds two inputs to that decision:

- **Relatedness.** How much the next task (the plate item) needs this session's
  context. st does **not** judge this. Something outside st posts it.
- **Cache state.** How long since the last model request, and which TTL the
  session's cache writes used. st measures this from the transcript
  (`usage.cache_creation.ephemeral_{5m,1h}_input_tokens`), so a session on the
  1h TTL is never priced as if it were on 5m.

## Unconfigured means unchanged

With no signal posted the advice is `default` and every path behaves exactly as
before. There is no model, key or service to set up. That is the point of the
design: st exposes the seam, and whoever wants the judgement calls in.

## Calling in

```
st agent advise <agent> --related 0.85 --by jev [--next <bead>] [--reason "..."]
st agent advise <agent> --decision cycle --by <agent> --reason "next bead is a different repo"
st agent advise <agent> [--json]          # read: the situation and the advice
```

`--json` prints what a caller judges from: depth, cycle line, next task, idle
seconds, cache TTL, the current signal, the advice, and two fields that keep a
judge's input small:

- **`ask`**: false past `always_cycle_above_k` or the cycle line, and when depth
  is unreadable. A caller that sees `ask: false` does not call its judge at all:
  that context is cleared regardless, so there is nothing to spend tokens on.
- **`brief`**: what to judge from, never the transcript. It holds the checkpoint
  you pass with `--checkpoint-file` as `current_session`, plus the next task's
  id, title and notes. It is cut to `brief_chars` in total, and each part gets a
  fair share, so a long checkpoint cannot crowd out the next task. When
  `truncated` is true and the judge is unsure, ask again with a larger
  `--brief-chars N`, which is capped at `max_brief_chars`. That is the
  "ask for more" step: start small and widen only on doubt.

A signal is dropped when:

- it names a different next task than the plate,
- it is older than `signal_max_age_minutes`, or
- the cycle completes. It was about the session that just ended.

## What st does with it

| situation | advice |
|---|---|
| past the cycle line | `cycle`. No signal keeps a saturated context |
| at or past `always_cycle_above_k` (250k) | `cycle`, and nobody is asked (`ask: false`) |
| no signal / stale / wrong task | `default`. Depth lines decide, as before |
| posted `--decision` | that decision |
| related ≥ `keep_at` | `keep`. The reasoning in flight is worth more than the arithmetic |
| related < `cycle_below` | `cycle`. Unrelated work |
| otherwise | the cheaper of keep and cycle over `expected_turns` |

In `st fleet tend`, advice only **escalates**: an agent between the pre-handoff
and cycle lines whose advice is `cycle` gets the cycle prompt instead of the
"write your handoff" nudge. `keep` never holds an agent past the cycle line.

## The arithmetic

In thousands of base-input-equivalent tokens, D = depth, F = `fresh_floor_k`,
N = `expected_turns`, r = read multiplier, w = write multiplier for the
session's TTL:

- keep = (w if the cache lapsed else r)·D + r·D·(N−1)
- cycle = w·(F+R) + r·(F+R)·(N−1), with R = related · `rebuild_fraction` · (D−F)

The multipliers default to Anthropic's list prices (read 0.1×, write 1.25× for
5m and 2× for 1h). On a subscription seat the currency is the governor's usage
window, which st cannot see into, so every number is configurable.

## Configuration

```toml
[cycle_advice]            # every key optional
always_cycle_above_k = 250  # at or past this: cycle, and never ask a judge
brief_chars = 1500          # the first brief a judge gets
max_brief_chars = 6000      # the most any --brief-chars can widen it to
keep_at = 0.8
cycle_below = 0.3
signal_max_age_minutes = 120
cache_ttl_seconds = 300   # only when the transcript records no TTL
read_multiplier = 0.1
write_multiplier_5m = 1.25
write_multiplier_1h = 2.0
fresh_floor_k = 40
rebuild_fraction = 0.3
expected_turns = 10
```

## Example caller: Jev

Lives with the deployment, not in st. Run it after the checkpoint is written.
`jev-noul` below stands for whatever reaches Jev in that deployment (the jev MCP
tool, or camayoc's `scripts/jev.py`). It is not an st command:

```sh
st agent advise "$AGENT" --json --checkpoint-file handoff.md > sit.json
[ "$(jq -r .ask sit.json)" = true ] || exit 0     # past the line: cleared anyway
STATE=$(jq -c .brief.parts sit.json)             # bounded; never the transcript
p=$(jev-noul --state "$STATE" --instructions "Would an agent starting next_task \
materially benefit from keeping the working context of current_session (same \
code, same files, same decisions in flight), rather than starting fresh from a \
written handoff? Sharing only a broad topic or the same repo is not enough. If \
the relationship is unclear or not stated, answer no.")
st agent advise "$AGENT" --related "$p" --by jev
```

The abstain direction ("if unclear, answer no") sits inside the question, so a
missing fact reads as *cycle*, never as a false *keep*.
