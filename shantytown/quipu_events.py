"""quipu_events — subscribe to Quipu entity events. The first-class EventSource.

integrations.md sketches the events adapter (`subscribe(kinds)`) with reactor as
the intended source, but shantytown never built it: reactor has no honest pull
surface (reactor.py). Quipu does — `GET /transactions?since=<tx>` is a real,
cursored pull. This is a WATERMARKED POLL, honest about being a pull, with the
four-state liveness reactor.py insisted on: the watermark advancing is the
liveness proof; "could not reach Quipu" is never "no events".

`poll_and_route` notices new transactions, asks Quipu which governed workflows the
graph assigns (`aegis:assignsWorkflow`), and ROUTES each new one to a sink — the
`st subscribe` command routes it to the administrator, who acts (a bead, a
dispatch). The watermark + handled-set persist so a restart resumes rather than
re-routing what it already handled.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from .quipu import (_ENDPOINT_MISSING, _body_shape, not_quipu, request_headers,
                    resolve_server)
from .protocols import Event, EventsUnavailable


@dataclass(frozen=True)
class Workflow:
    """A governed workflow the graph assigns (`Policy -assignsWorkflow-> Workflow`)."""
    iri: str
    label: str = ""
    target: str = ""


# Which governed workflows does the graph currently assign? One SPARQL, so the
# subscriber borrows the record and never re-derives it.
_WORKFLOWS_SPARQL = (
    "PREFIX aegis: <http://aegis.gastown.local/ontology/> "
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
    "SELECT ?wf ?label ?target WHERE { "
    "?policy a aegis:Policy ; aegis:assignsWorkflow ?wf ; aegis:targets ?target . "
    "OPTIONAL { ?wf rdfs:label ?label } }"
)


def _http_detail(server: str, path: str, e) -> str:
    """One text for a 4xx/5xx on the events surface: an endpoint-shaped status is
    a WRONG-SERVICE diagnosis (see quipu.QuipuNotQuipu), anything else is a quipu
    that answered badly. Shared by _get and _post so they cannot disagree."""
    if e.code in _ENDPOINT_MISSING:
        return not_quipu(server, path.split("?")[0], f"HTTP {e.code}")
    return f"quipu at {server} refused {path}: HTTP {e.code}"


class QuipuEvents:
    """EventSource over Quipu's cursored transaction log. Read-only; the two HTTP
    methods are the only seam tests override (mirrors test_reactor's _Fake)."""

    def __init__(self, server: str | None = None, timeout: float = 5.0, root=None):
        # localhost, NOT an internal hostname: this repo is public, and a real
        # deployment's address is deployment config, never a default baked into
        # source. ONE resolver shared with quipu.py (env.json, then $QUIPU_SERVER,
        # then quipu's own default), so the two clients cannot end up pointed at
        # different servers from the same deployment.
        self.server = resolve_server(server, root).rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> dict:
        try:
            with urllib.request.urlopen(self.server + path, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            # MUST precede the URLError arm — HTTPError subclasses it (the trap
            # quipu.py documents at length). An endpoint-shaped status means the
            # ADDRESS is wrong, not that the graph is quiet.
            raise EventsUnavailable(_http_detail(self.server, path, e)) from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise EventsUnavailable(f"quipu at {self.server} unreachable: {e}") from e

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.server + path, data=json.dumps(body).encode(),
            headers=request_headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise EventsUnavailable(_http_detail(self.server, path, e)) from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise EventsUnavailable(f"quipu at {self.server} unreachable: {e}") from e

    def transactions_since(self, since: int, limit: int = 1000) -> list[Event]:
        """New transactions past `since` (the cursor). Raises EventsUnavailable —
        never returns [] to mean 'could not look'.

        THE KEY MUST BE PRESENT, same fix and same reason as `quipu._query`: this
        was `body.get("transactions", [])`, so a 200 from a service with no
        transaction log became "no new events" — and since the watermark only
        advances on real events, `poll_and_route` would report `idle` forever
        against the wrong daemon. `idle` is a claim ABOUT THE GRAPH; it must not
        be available to a client that never reached one.
        """
        body = self._get(f"/transactions?since={int(since)}&limit={int(limit)}")
        if not isinstance(body, dict) or "transactions" not in body:
            raise EventsUnavailable(not_quipu(
                self.server, "/transactions",
                f'a 200 with no "transactions" key ({_body_shape(body)})'))
        rows = body["transactions"]
        if not isinstance(rows, list):
            raise EventsUnavailable(not_quipu(
                self.server, "/transactions",
                f'"transactions" is a {type(rows).__name__}, not a list'))
        return [Event(id=int(t["id"]), actor=t.get("actor"),
                      source=t.get("source"), timestamp=t.get("timestamp"))
                for t in rows]

    def assigned_workflows(self) -> list[Workflow]:
        """The governed workflows the graph currently assigns. Raises on unreachable."""
        body = self._post("/query", {"query": _WORKFLOWS_SPARQL})
        rows = body.get("rows", []) if isinstance(body, dict) else []
        out: list[Workflow] = []
        for row in rows:
            iri = _local(str(row.get("wf", "")))
            if iri:
                out.append(Workflow(iri=iri, label=str(row.get("label", "")),
                                    target=str(row.get("target", ""))))
        return out

    def subscribe(self, kinds: list[str] | None = None) -> Iterator[Event]:
        """The EventSource contract: yield the transactions since watermark 0 once.
        The `st subscribe` command uses poll_and_route for the stateful loop; this
        satisfies the protocol and is handy for one-shot scripting."""
        for e in self.transactions_since(0):
            if kinds is None or (e.source and any(e.source.startswith(k) for k in kinds)):
                yield e


class NoEvents:
    """The `none` EventSource — the leak detector. No backend, nothing to deliver;
    the subscriber runs (and reports idle) without Quipu."""

    def transactions_since(self, since: int, limit: int = 1000) -> list[Event]:
        return []

    def assigned_workflows(self) -> list[Workflow]:
        return []

    def subscribe(self, kinds: list[str] | None = None) -> Iterator[Event]:
        return iter(())


def _local(iri: str) -> str:
    return iri.rsplit("/", 1)[-1] if iri.startswith("http") else iri


@dataclass
class SubscriptionState:
    """The watermark (last handled tx) + the set of workflow IRIs already routed.
    Persisted so a restart resumes rather than re-routing."""
    watermark: int = 0
    handled: set = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "SubscriptionState":
        if not Path(path).is_file():
            return cls()
        d = json.loads(Path(path).read_text())
        return cls(watermark=int(d.get("watermark", 0)),
                   handled=set(d.get("handled", [])))

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(
            {"watermark": self.watermark, "handled": sorted(self.handled)},
            indent=2, sort_keys=True))


@dataclass
class Report:
    """One poll's outcome, four-state honest (reactor.py's lesson)."""
    reachable: bool
    new_events: int = 0
    routed: int = 0
    watermark: int = 0
    detail: str = ""

    @property
    def verdict(self) -> str:
        if not self.reachable:
            return "cannot tell"     # could not look — NOT "no events"
        if self.new_events:
            return "live"
        return "idle"                # quiet — no new transactions

    def render(self) -> str:
        if self.verdict == "cannot tell":
            return f"  quipu-events: CANNOT TELL — {self.detail}"
        if self.verdict == "idle":
            return f"  quipu-events: idle — no new transactions past tx {self.watermark}"
        return (f"  quipu-events: live — {self.new_events} new tx, "
                f"{self.routed} workflow(s) routed, watermark tx {self.watermark}")


def poll_and_route(events, state: SubscriptionState,
                   route: Callable[[Workflow], None]) -> Report:
    """One poll. Advance the watermark ONLY after the batch is fully processed; on
    any could-not-look, keep the watermark and report cannot-tell so the next poll
    retries — never a silent skip past events we did not handle."""
    try:
        evs = events.transactions_since(state.watermark)
    except EventsUnavailable as e:
        return Report(reachable=False, watermark=state.watermark, detail=str(e))
    if not evs:
        return Report(reachable=True, new_events=0, watermark=state.watermark)
    try:
        wfs = events.assigned_workflows()
    except EventsUnavailable as e:
        # Transactions moved but the assignments could not be read — do NOT advance,
        # so the batch is retried next poll rather than dropped.
        return Report(reachable=False, watermark=state.watermark,
                      detail=f"transactions advanced but /query failed: {e}")
    routed = 0
    for w in wfs:
        if w.iri in state.handled:
            continue
        route(w)
        state.handled.add(w.iri)
        routed += 1
    state.watermark = max(e.id for e in evs)
    return Report(reachable=True, new_events=len(evs), routed=routed,
                  watermark=state.watermark)
