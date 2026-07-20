# shantytown — design

Read [`vision.md`](vision.md) first. This is the how.

## Shape

```
shantytown/
  shantytown/
    __init__.py
    cli.py              # the only entry point
    dispatch.py         # create → send → verify
    triage.py           # nudge / clear / restart / refuse
    panes/
      base.py           # Pane protocol
      tmux.py           # send-keys. the floor.
      shanty.py         # optional
      herdr.py          # optional
    trackers/
      base.py           # Tracker protocol
      beads.py          # bd create
      github.py         # gh issue create
      files.py          # ./work/<uuid>.md
  tests/
  pyproject.toml
```

Python, per directive. It buys pytest/mutmut/hypothesis — none of which reach bash, which is why the
verification discipline we needed had to be hand-built last time.

## The two protocols

Everything pluggable is a few small interfaces. Every method below has had to argue for its slot.

This block is the **shipped** protocol — `shantytown/protocols.py` is the authority and this is a
copy of it, not a sketch of what we hoped for. It used to be the sketch (`create`/`hint`), and it
disagreed with the code for months; a design doc that describes an interface the code doesn't have
teaches the reader the wrong thing with full confidence (GitHub #8).

```python
class Tracker(Protocol):
    def get(self, item_id: str) -> WorkItem: ...
    def update(self, item_id: str, **fields) -> None: ...
    def create(self, title: str, **fields) -> WorkItem: ...

class Panes(Protocol):
    def send(self, pane: str, text: str) -> None: ...
    def exists(self, pane: str) -> bool: ...
    def capture(self, pane: str, history: int = 0) -> str: ...   # for triage + verification
    # session lifecycle — `st new` / `st stop`, and triage's RESTART
    def new_session(self, name: str) -> str: ...
    def kill_session(self, name: str) -> None: ...
    def owns(self, name: str) -> bool: ...
```

**Why the tracker is three and not two.** The two-function pitch (`create` + `hint`) is still the
*adoption* claim — writing a backend is small — but dispatch needs to READ an item it did not create
(`get`) and to MARK it assigned (`update`), and faking either through `create` is worse than
admitting the third method. `hint` never shipped: it was rendering, and rendering belongs to the CLI.

`capture` is not optional. **You cannot triage a session you cannot look at, and you cannot verify a
dispatch you cannot read back.** A Pane that can only write is a Pane that always reports success.

## Dispatch

```python
def dispatch(tracker, pane, target, title, body, *, dry_run=False) -> Result:
    plan = triage(pane, target, body)          # decide BEFORE creating anything
    if plan.action is REFUSE:
        return Result(dispatched=False, reason=plan.reason)
    if dry_run:
        return Result(dispatched=False, plan=plan)   # ← says what it WOULD do. ships day one.

    id   = tracker.create(title, body)
    if plan.action is CLEAR:   pane.send(target, "/clear")
    if plan.action is RESTART: relaunch(target)      # launcher only. NEVER handoff.
    pane.send(target, tracker.hint(id))
    return Result(dispatched=True, id=id, plan=plan, verified=verify(pane, target, id))
```

Two things load-bearing here:

**Triage runs before `create`.** If we refuse, we do not leave an orphan work item behind. Deciding
after creating is how you get a tracker full of items nobody was ever told about.

**`verify` reads the pane back.** Send-and-assume is how you believe work was assigned when it wasn't.
A tool can only report what it *sent*, never what *landed*.

## Triage

The part worth packaging. Everything else is plumbing.

```python
def triage(pane, target, new_work) -> Plan:
    if not pane.exists(target):            return Plan(RESTART, "no session")
    screen = pane.capture(target)
    if looks_wedged(screen):               return Plan(RESTART, "wedged")
    if mid_flight(screen):                 return Plan(REFUSE,  "in-flight work")
    if context_high(screen) and unrelated(screen, new_work):
                                           return Plan(CLEAR,   "high context, unrelated")
    return Plan(NUDGE, "healthy")
```

Encoded knowledge, all of it paid for:

- **`RESTART` means launcher-relaunch. Never handoff.** Gas Town's handoff drops `--settings`; the
  agent returns **with no hooks** and is indistinguishable from a healthy one. This burned a whole
  measurement.
- **"running" is not health.** `exists()` is necessary, not sufficient. Read the pane.
- **`REFUSE` is a real outcome.** Sending interrupts in-flight work — Gas Town's own help says so.
  A triage that never refuses isn't triage.

`context_high` and `unrelated` are the honest unknowns. Start crude and visible (screen length;
keyword overlap), log every decision with its inputs, and tune against real dispatches. **Do not ship
a confident heuristic you cannot inspect** — the whole point is that the operator can see why it chose.

## Trackers

```python
# beads
def create(t, b): return sh(f"bd create {q(t)} -d {q(b)}").id
def hint(i):      return f"go read bead {i} — run: bd show {i}"

# files — the zero-dependency floor. proves the abstraction.
def create(t, b): p = Path("work")/f"{uuid4()}.md"; p.write_text(f"# {t}\n\n{b}"); return str(p)
def hint(i):      return f"go read {i}"
```

The **files** adapter is not a toy. It's the proof the abstraction holds — if shantytown works with a
directory of markdown, it works for someone with no tracker at all, and that's the whole generality
claim.

## Panes

`tmux.py` is the floor and the reference:

```python
def send(self, target, text):
    run(["tmux", "send-keys", "-t", target, text, "Enter"])
def capture(self, target, lines=40):
    return run(["tmux", "capture-pane", "-p", "-t", target]).tail(lines)
```

**Start here. Ship this. Add shanty/herdr adapters only once dispatch is proven.**

Coupling the harness to a multiplexer before the harness exists is how it inherits someone else's
lifecycle. The pane layer is the part most likely to be replaced and the part we care least about
owning — so it should be the thinnest thing in the repo.

## Crew creation

The second primitive worth keeping. A crew member is:

```
workspace   a directory (usually a git clone)
session     a named pane
identity    who am I, what am I for  → injected at session start
```

Gas Town does this well and we use it. Keep the shape, drop the rig/town/mayor scaffolding around it.

## Testing — the part that justifies Python

```
tests/
  test_triage.py      every branch, including REFUSE and RESTART
  test_dispatch.py    dry_run creates NOTHING. verify() catches a send that didn't land.
  test_trackers.py    same suite against all three adapters — that IS the portability proof
  test_panes.py       FakePane. no tmux needed in CI.
```

Two tests that must exist because they are the ones that would have caught our real bugs:

- **`dry_run` creates nothing.** Assert the tracker was never called. (An operator hooked a live crew
  member with an accidental probe. Make the question askable without the consequence.)
- **`verify` fails when the send didn't land.** Feed it a pane whose capture doesn't contain the id and
  assert it reports failure. *A verifier that has never returned false is not a verifier.*

Then run `mutmut` over `triage.py`. If mutants survive, the tests are decorative — which is exactly the
class of defect we keep finding by hand.

## Open questions

1. **`context_high` — measured how?** Screen length is crude. Is there a real signal? Start crude,
   log, tune. Don't guess in silence.
2. **`unrelated` — keyword overlap, or ask the agent?** Asking is honest but costs a round trip.
3. **Does mail belong here?** It's our heaviest-used Gas Town command (70). It may be a separate small
   thing rather than harness scope. **Lean: separate.** A harness that grows a message bus is on its
   way to being a town.
4. **Multi-host?** Everything above is single-host tmux. Cross-host dispatch is a different problem;
   don't design for it until we have it.

## Sequence

1. `tmux.py` + `files.py` + `dispatch.py` with `--dry-run`. Prove: dispatch to a real agent, no Gas Town.
2. `triage.py` with both branches demonstrated (one nudge lands; one target refused/cleared).
3. `beads.py`. Prove the swap: same dispatch code, different tracker.
4. **THE GATE.** Time it against `gt sling` (>120s). **If it isn't dramatically faster, say so and
   stop** — the latency claim is the reason this exists.
5. `github.py`, then a pane adapter, only if wanted.

Stop after 4 if the numbers don't hold. That's a real outcome, not a failure.

**Step 4 result (ruled PASSED by dearing):** `st go` is ~35x faster on the real path
(3.4s vs gt sling's >120s), 63→3 Dolt connections. The literal "under a second" was MISSED at 3.4s —
but that 3.4s is entirely `bd update`'s cost (a bd write is 17x a bd read, root-caused to
an INFORMATION_SCHEMA check), not shantytown's; shantytown's own overhead is ~0.2s. "Under a second"
was a bd target wearing shantytown's name. The gate passed on the corrected criterion (own-overhead
≤0.25s, ≥10x vs sling), so the project earns its existence and step 5 is unblocked.

**The CLI + `prime` are NOT a numbered step** — they came from Stiwi's later "there needs
to be a cli and a primer" (2026-07-16), and `docs/cli.md` specifies them. They are the interface *over*
steps 1–4, built after the gate passed, not a rung on the ladder to it. The orchestration tier
(worker/lead/administrator) is likewise a Stiwi-directed capability on top of a passed
gate, not part of the original prove-it-is-faster sequence.
