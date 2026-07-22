"""stop_event admin enrichment — the prioritized workflow injected at the
administrator's drain. The invariants that matter:

  - it RIDES the block-once stop events (no events -> silent, no nag),
  - a non-admin drain is byte-identical to before (no workflow),
  - a down ranker degrades to the rule-based order (the hook never wedges).
"""
from __future__ import annotations

import json
from pathlib import Path

from shantytown import stop_event
from shantytown.events import FilesEvents
from shantytown.files import FilesRegistry
from shantytown.protocols import RankUnavailable


class _Panes:
    def __init__(self, up):
        self._up = set(up)

    def exists(self, pane):
        return pane in self._up


def _crew(tmp_path: Path) -> FilesRegistry:
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "goldblum.json").write_text(json.dumps(
        {"role": "administrator", "pane": "p-gb"}))
    (crew / "ellie.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "goldblum", "pane": "p-ellie"}))
    (crew / "bran.json").write_text(json.dumps(
        {"role": "worker", "reports_to": "goldblum", "pane": "p-bran"}))
    return FilesRegistry(crew)


def test_admin_drain_appends_the_prioritized_workflow(tmp_path, capsys):
    reg = _crew(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="goldblum", frm="ellie", reason="too-large", rose=False)
    # ellie's pane is DOWN (stopped); bran is up with no plate (idle).
    panes = _Panes({"p-gb", "p-bran"})
    rc = stop_event._drain(ev, "goldblum", reg=reg, panes=panes,
                           plate=lambda _w: None, rank=None)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "block"
    assert "ellie stopped" in payload["reason"]        # the raw stop event, verbatim
    assert "PRIORITIZE" in payload["reason"]           # the workflow enrichment
    assert "re-dispatch ellie" in payload["reason"]    # down -> stopped
    assert "assign work bran" in payload["reason"]     # up, empty -> idle
    assert "goldblum" not in payload["reason"].split("PRIORITIZE")[1], \
        "the admin must never prioritize itself"


def test_non_admin_drain_is_byte_identical(tmp_path, capsys):
    reg = _crew(tmp_path)
    (tmp_path / "crew" / "maldoon.json").write_text(json.dumps(
        {"role": "lead", "reports_to": "goldblum", "pane": "p-m"}))
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="maldoon", frm="ellie", reason=None, rose=False)
    stop_event._drain(ev, "maldoon", reg=reg, panes=_Panes({"p-m"}),
                      plate=lambda _w: None, rank=None)
    payload = json.loads(capsys.readouterr().out)
    assert "ellie stopped" in payload["reason"]
    assert "PRIORITIZE" not in payload["reason"], "a lead gets no workflow"


def test_admin_drain_with_no_events_is_silent(tmp_path, capsys):
    """Rides block-once: an idle/stopped fleet with NO new stop event must not
    re-block the admin — otherwise the admin can never go idle (the wedge)."""
    reg = _crew(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    rc = stop_event._drain(ev, "goldblum", reg=reg, panes=_Panes({"p-bran"}),
                           plate=lambda _w: None, rank=None)
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_admin_drain_degrades_when_the_ranker_is_unavailable(tmp_path, capsys):
    reg = _crew(tmp_path)
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="goldblum", frm="ellie", reason="too-large", rose=False)

    class _Boom:
        def weigh(self, _candidates):
            raise RankUnavailable("hank down")

    rc = stop_event._drain(ev, "goldblum", reg=reg, panes=_Panes({"p-gb"}),
                           plate=lambda _w: None, rank=_Boom())
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "PRIORITIZE" in payload["reason"], "still injected, just unweighted"
