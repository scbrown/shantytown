"""shuttle_runs — watch shuttle workflow runs in quipu's windowed graphs.

The consumer half of the shuttle contract (scbrown/shuttle). Shuttle exports
signed, append-only runs into time-windowed OPERATIONAL graphs and maintains
`urn:shuttle:dataset:open` — the dataset of not-yet-frozen windows. This
module polls those runs the same way `quipu_events` polls transactions:
a watermarked, four-state-honest pull that routes NEWLY SEEN run states to
a sink (`st subscribe` turns them into beads and attributed nudges).

Two hazards this module exists to not have, both measured elsewhere:

- **Silent zero rows.** Shuttle's facts live in NAMED graphs; a default-graph
  query returns zero rows forever and looks exactly like "no runs". Every
  query here names its scope explicitly — the `graph` request param set to
  the open-windows dataset (quipu expands a dataset IRI to its members).
- **A store that predates the surface.** Before the first poll, the probe
  (`GET /graphs?kind=operational`) distinguishes "this quipu has no graph
  registry" (404 -> EventsUnavailable, verdict CANNOT TELL) from "no
  operational graphs yet" (an empty list — honest quiet). Zero rows from a
  pre-graph-kinds store must never read as an empty workload.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .protocols import EventsUnavailable
from .quipu import _ENDPOINT_MISSING, not_quipu, request_headers, resolve_server

#: The dataset shuttle maintains for its not-yet-frozen windows — the name is
#: a contract with shuttle/windows.py. Overridable for a deployment that
#: renames it, like every other identity parameter in this package.
OPEN_DATASET = "urn:shuttle:dataset:open"

#: The aegis namespace the shuttle export writes under. Same base the rest of
#: the stack binds; a parameter in shuttle, mirrored here.
_AEGIS = "http://aegis.gastown.local/ontology/"


@dataclass(frozen=True)
class ShuttleRun:
    """One workflow run as the graph currently tells it."""
    iri: str
    definition: str
    state: str


def _runs_sparql() -> str:
    return (
        f"PREFIX aegis: <{_AEGIS}> "
        "SELECT ?run ?wf ?state WHERE { "
        "?run a aegis:WorkflowRun ; aegis:runOf ?wf ; aegis:currentState ?state }"
    )


class ShuttleRuns:
    """Read-only poll of shuttle runs. The two HTTP methods are the only seam
    tests override (the `quipu_events._Fake` idiom)."""

    def __init__(self, server: str | None = None, timeout: float = 5.0, root=None,
                 dataset: str = OPEN_DATASET):
        self.server = resolve_server(server, root).rstrip("/")
        self.dataset = dataset
        self.timeout = timeout
        self._probed = False

    def _get(self, path: str) -> dict:
        try:
            with urllib.request.urlopen(self.server + path, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in _ENDPOINT_MISSING:
                raise EventsUnavailable(
                    f"quipu at {self.server} has no graph registry endpoint "
                    f"(HTTP {e.code} on {path.split('?')[0]}) — it predates "
                    "graph kinds, and shuttle's windowed facts would read as "
                    "silent zero rows. Upgrade the store; this is CANNOT TELL, "
                    "not an empty workload.") from e
            raise EventsUnavailable(
                f"quipu at {self.server} refused {path}: HTTP {e.code}") from e
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
            if e.code in _ENDPOINT_MISSING:
                raise EventsUnavailable(not_quipu(
                    self.server, path.split("?")[0], f"HTTP {e.code}")) from e
            raise EventsUnavailable(
                f"quipu at {self.server} refused {path}: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise EventsUnavailable(f"quipu at {self.server} unreachable: {e}") from e

    def probe(self) -> None:
        """Once per instance: the store must HAVE the graph-kinds surface.
        Raises EventsUnavailable on a pre-graph-kinds store; an empty listing
        passes — no operational graphs yet is honest quiet."""
        if self._probed:
            return
        body = self._get("/graphs?kind=operational")
        if not isinstance(body, dict) or "graphs" not in body:
            raise EventsUnavailable(not_quipu(
                self.server, "/graphs", 'a 200 with no "graphs" key'))
        self._probed = True

    def runs(self) -> list[ShuttleRun]:
        """Every run the OPEN windows currently hold, with its state. Scope is
        the open-windows dataset, named explicitly — never the default graph.
        Raises EventsUnavailable; never [] for could-not-look."""
        self.probe()
        body = self._post("/query", {"query": _runs_sparql(),
                                     "graph": self.dataset})
        if not isinstance(body, dict) or "rows" not in body:
            raise EventsUnavailable(not_quipu(
                self.server, "/query", 'a 200 with no "rows" key'))
        out = []
        for row in body["rows"]:
            iri = str(row.get("run", ""))
            if iri:
                out.append(ShuttleRun(iri=iri,
                                      definition=str(row.get("wf", "")),
                                      state=str(row.get("state", ""))))
        return out


class NoShuttleRuns:
    """The `none` source — the negative control the adapters doctrine requires.
    Nothing configured, nothing delivered; the subscriber still runs."""

    def probe(self) -> None:
        return None

    def runs(self) -> list[ShuttleRun]:
        return []


@dataclass
class RunsState:
    """(run IRI, state) pairs already routed, persisted beside the events
    subscription so a restart resumes rather than re-announcing."""
    seen: set = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "RunsState":
        if not Path(path).is_file():
            return cls()
        d = json.loads(Path(path).read_text())
        return cls(seen={tuple(x) for x in d.get("seen", [])})

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(
            {"seen": sorted(list(x) for x in self.seen)}, indent=2, sort_keys=True))


@dataclass
class RunsReport:
    """One poll's outcome — the same four-state honesty as quipu_events."""
    reachable: bool
    runs: int = 0
    routed: int = 0
    detail: str = ""

    @property
    def verdict(self) -> str:
        if not self.reachable:
            return "cannot tell"
        return "live" if self.routed else "idle"

    def render(self) -> str:
        if self.verdict == "cannot tell":
            return f"  shuttle-runs: CANNOT TELL — {self.detail}"
        if self.verdict == "idle":
            return f"  shuttle-runs: idle — {self.runs} run(s), nothing new"
        return f"  shuttle-runs: live — {self.routed} new run state(s) of {self.runs} run(s)"


def poll_runs(source, state: RunsState,
              route: Callable[[ShuttleRun, bool], None]) -> RunsReport:
    """One poll. Routes each (run, state) pair exactly once; the second bool
    tells the sink whether the RUN itself is new (first state ever seen) so it
    can open a bead once and nudge thereafter. Could-not-look keeps the seen
    set untouched and reports cannot-tell."""
    try:
        runs = source.runs()
    except EventsUnavailable as e:
        return RunsReport(reachable=False, detail=str(e))
    routed = 0
    for r in runs:
        key = (r.iri, r.state)
        if key in state.seen:
            continue
        is_new_run = not any(k[0] == r.iri for k in state.seen)
        route(r, is_new_run)
        state.seen.add(key)
        routed += 1
    return RunsReport(reachable=True, runs=len(runs), routed=routed)
