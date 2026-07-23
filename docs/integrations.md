# shantytown — what else is in the toolbox, and how it plugs in

> Stiwi, 2026-07-16: *"look at my other tooling in forgejo and github repo; what else do i have that we
> could integrate with and then how will we do it? i would like to have the concept of a dashboard,
> like tapestry, thats plugable but with default support for tapestry first"* — and *"oh and then also
> reactor"*.
>
> This is the survey and the answer. The dashboard answer is the interesting one, and it is: **ship no
> dashboard.**

## The inventory — measured, not remembered

**GitHub (`scbrown/*`) is authoritative.** Stiwi, 2026-07-16: *"make sure you use the github repo
version if there are duplicates, the most up to date stuff is on github."*

| tool | home | what it is | shantytown |
|---|---|---|---|
| **beads** | github `scbrown/beads` | issue tracker on Dolt | **first-class tracker** |
| **bobbin** | github `scbrown/bobbin` | code context / semantic search | **first-class context** |
| **quipu** | github `scbrown/quipu` | RDF/SPARQL knowledge graph | **first-class knowledge** |
| **tapestry** | *(not public)* | HTMX dashboard over beads | **first-class dashboard** — see below |
| **reactor** | — | watches bead lifecycle, fires actions | **event source**, with caveats |
| **desire-path (dp)** | github `scbrown/desire-path` | records failed tool calls | **telemetry — the best idea nobody is using** |
| **shanty** | github `scbrown/shanty` | Go tmux wrapper | pane adapter, later |
| **skein** | github `scbrown/skein` | agent-skills spec + validator | skills, later |
| **Gas Town** | github `gastownhall/gastown` | the harness this shrinks | — |
| gt-api | *(not public)* | tapestry's intended backend | **do not build.** See below. |

**A dangling reference, found while surveying:** a stale `ssh://` remote for quipu → *"Cannot find
repository."* It does not exist. GitHub's `scbrown/quipu` has 58 branches and is the only quipu.
Seven of our own issues still cite the dead path. Nothing is stranded there — there is no there —
but the citations are wrong and will send someone hunting.

---

## The dashboard: ship no dashboard

Stiwi asked for a pluggable dashboard with Tapestry first-class. The survey produced a better answer
than an interface.

**Measured, just now:**

```
gt dashboard    (:8080, Gas Town's built-in web server)   -> not running
gastown-api     (:8080, the dashboard's INTENDED backend) -> 503, undeployed
tapestry        (reads the store directly, bypasses both)  -> 200  ✓  serving
```

Gas Town ships **two** servers for this. Both are dead. **Tapestry bypasses both and works.** Its
config points straight at the SQL port — it reads the beads store directly and has never needed a harness
API. `gt-api` was built to be its backend; it is entirely undeployed (no binary, no unit, no role —
`service-catalog.yml:764` names an ansible role that does not exist), and **nobody noticed for
months**, because its only consumer never needed it.

That is the whole design input:

> **A dashboard reads the tracker. It does not read the harness.**

So shantytown's dashboard interface is **not** `Dashboard.render()`. There is no dashboard interface.
There is an obligation:

```
Whatever shantytown knows, it writes to the tracker.
Dashboards read the tracker.
The harness never learns the dashboard exists.
```

Tapestry is "first-class" the way gravity is first-class: it already works, and it works because it
reads the store. Swapping it is `git clone` of something else that reads the store. **Pluggability
here costs zero lines**, because the coupling point is a database that already has readers.

### What this forbids

- **No `st dashboard`.** Gas Town has one; it is not running; nobody filed a bead.
- **No `shanty-api`.** Gas Town built one; it is undeployed; its only consumer routed around it.
- **No dashboard-shaped writes on the dispatch path.** `gt sling` auto-creates a convoy per dispatch
  *for dashboard visibility* — a write on the hot path, for a reader that doesn't need it.

### What this obliges

State must be **legible in the tracker**, not just in the harness's head. If `st go` sets
`status=in_progress, assignee=ellie` and nothing else, Tapestry already renders it. If shantytown
wants to show "absorbed by lead" it writes that to the item — it does not grow an endpoint.

**The test:** point Tapestry at a shantytown-driven beads db. If it renders without a shantytown
change, the boundary is right. That test is the interface.

---

## reactor — an event source we do not depend on

reactor watches the tracker's storage layer for lifecycle events (bead created / closed) and fires
actions — the marquee one being **auto-ingest into quipu**.

**It is the highest-value integration here and it is currently the most broken thing in the survey.**
Both verified against the running deployment, not inferred:

- **The directive is false.** Crew `CLAUDE.md` states *"Reactor handles bead lifecycle → Quipu ingestion
  automatically."* **That directive is FALSE.** reactor has never watched the live tracker database.
  Every crew member has been relying on it.
- **It watches the wrong thing.** Reactor is subscribed to **dead databases**, not the live one.
  `up{job=reactor}==1`, systemd `active(running)`, no alerts firing, **zero events processed**.
- **Its own alarms cannot ring.** Its staleness alerts (`ReactorEventStale`, `ReactorHighActionLatency`) are dead
  rules and cannot fire.

So reactor is *present, running, monitored, green, and doing nothing* — while a standing directive
tells 14 agents it is doing the thing. That is the exact failure this repo's principles exist to
forbid, and it is sitting in the tool we most want to integrate.

**Therefore the integration rule:**

```python
class Events(Protocol):                       # reactor
    def subscribe(self, kinds: list[str]) -> Iterator[Event]: ...
```

- **Optional.** The `none` adapter must run the full harness. If shantytown *needs* reactor to
  function, we have made a directive that will one day be false.
- **It must prove liveness, not presence.** `up==1` is what reactor has today and it means nothing.
  The adapter's health answer is **"how many events have you delivered?"** — a count, not a ping.
- **Never claim it in docs before it is measured.** The first bug filed against reactor was, in its
  entirety, that a doc claimed reactor was working. We do not repeat that sentence in this repo.

The reactor idea worth having is **the bead-advisor**: on create, warn the author of
duplicates, directive conflicts, known failure patterns, relitigated decisions. That is triage with a
knowledge base, and it is exactly `docs/design.md`'s triage layer — *which must be able to refuse, not
just nudge.* Design it as a **consumer of events**, never as a thing reactor hardcodes.

### The events source we DID build: quipu (`st subscribe`)

The `events` row above sat empty for a reason: reactor, the intended source, has **no honest pull
surface** (`/events`, `/subscribe` all 503), so a `subscribe()` on it would be an invented endpoint —
the exact defect this repo refuses. **Quipu has one.** `GET /transactions?since=<tx>` is a real,
cursored transaction log, so the first true `EventSource` (`shantytown/quipu_events.py`) is a
**watermarked poll** over it: honest about being a pull, four-state liveness (the watermark advancing
is the proof, "could not reach quipu" is never "no events"). `st subscribe` runs the loop — on new
transactions it asks quipu which governed workflows the graph assigns (`aegis:assignsWorkflow`) and
routes each new one to the administrator, who acts (a bead + a nudge). The watermark + handled set
persist, so a restart resumes rather than re-routing. This is the bead-advisor's substrate: a consumer
of events, sourced from the one tool that answers a pull honestly.

---

## desire-path (dp) — the one nobody is using

`dp` records **tool calls that failed** — what an agent *tried* and could not do. That is the highest-
signal telemetry a harness can have, and it is the only thing in this inventory that measures **the
gap between what agents want and what the harness offers.**

Shantytown's whole thesis is "we ship 110 commands and use 9." **dp is how you find the 10th
honestly** — not by asking, by watching what people reach for and miss.

```python
class Telemetry(Protocol):                    # dp
    def record_miss(self, intent: str, error: str) -> None: ...
```

Optional, fire-and-forget, never on the hot path. It informs the CLI surface; it does not gate it.

---

## How all of it plugs in — one table

Everything above is the same shape as `docs/adapters.md`: a first-class default and a narrow
interface, with a second implementation as the proof.

| layer | protocol | first-class | the proof (second impl) |
|---|---|---|---|
| tracker | `get` / `update` | beads | `files` (markdown dir) |
| context | `relevant` | bobbin | `none` |
| knowledge | `search` / `record` | quipu | `none` |
| events | `subscribe` | **quipu** (cursored transaction log) | `none` |
| telemetry | `record_miss` | dp | `none` |
| runtime | `start` / `send` / `hooks` | Claude Code | opencode / codex |
| panes | `open` / `send` | bare tmux | shanty / herdr |
| **dashboard** | **— none —** | **tapestry (reads the tracker)** | **any store reader** |

The dashboard row is the point of this document. Every other row is an interface. That one is an
**absence**, and it is the only row that cost nothing to design, because the tool that would consume
it already solved it by ignoring the harness entirely.

---

## Open

- **skein** — agent-skills spec + validator. We have 22 skills. Skills are probably an
  adapter, not a core concept, but this needs reading before deciding.
- **Does the bead-advisor belong in shantytown at all?** It is triage with a knowledge base, and
  triage IS in scope. Lean: the *interface* is (events + knowledge), and the advisor is a consumer that
  lives outside. If it lands inside, the harness has grown an opinion about work quality.
- **dp's schema** — `record_miss(intent, error)` is a guess. Read the repo before fixing it.
