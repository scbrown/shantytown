"""stopped — a deliberate stop is INTENT, and st must be able to tell (GH #29 req 2).

The measured bug this pins: `st stop` recorded nothing, so a pane an operator had
just been instructed to kill and a pane that crashed produced the SAME reading, and
the administrator's drain demanded nine deliberate shutdowns be undone.

The tests are written around the two ways that record can lie rather than around the
happy path, because both are the ones that would hurt:
  - a record that OUTLIVES the stop (agent came back) would make the next real crash
    read as somebody's decision — a fabricated intent;
  - a record consulted for a LIVE pane would assert a past fact as a standing
    property, which is the launch-stamp mistake (launched.py) in a new place.
"""
from __future__ import annotations

from shantytown import workflow as wf
from shantytown.protocols import Agent
from shantytown.stopped import FilesStops


class _Panes:
    def __init__(self, up):
        self._up = set(up)

    def exists(self, pane):
        return pane in self._up


# --- the store ---------------------------------------------------------------

def test_a_recorded_stop_reads_back(tmp_path):
    s = FilesStops(tmp_path / "stopped")
    s.record("felix", 1000.0, by="sattler", reason="credit budget exhausted")
    rec = s.get("felix")
    assert rec.at == 1000.0 and rec.by == "sattler"
    assert rec.reason == "credit budget exhausted", "the WHY is the auditable part"


def test_no_record_is_None_not_a_guess(tmp_path):
    assert FilesStops(tmp_path / "stopped").get("felix") is None


def test_forget_ends_the_record(tmp_path):
    s = FilesStops(tmp_path / "stopped")
    s.record("felix", 1000.0)
    s.forget("felix")
    assert s.get("felix") is None, "the stop is over — it must not outlive itself"


def test_an_UNREADABLE_record_is_not_an_intent(tmp_path):
    """A corrupt file must not invent a decision nobody made."""
    d = tmp_path / "stopped"
    d.mkdir()
    (d / "felix.json").write_text("{ this is not json")
    assert FilesStops(d).get("felix") is None


def test_a_record_that_cannot_be_WRITTEN_never_raises(tmp_path):
    """Best-effort, like the launch stamp: losing the record costs a distinction;
    failing the stop over it would cost the operator their shutdown."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    FilesStops(blocked / "stopped").record("felix", 1000.0)      # must not raise


def test_all_skips_what_it_cannot_read(tmp_path):
    d = tmp_path / "stopped"
    s = FilesStops(d)
    s.record("felix", 1000.0)
    (d / "junk.json").write_text("nope")
    assert list(s.all()) == ["felix"]


# --- the reading: classify ---------------------------------------------------

def test_a_down_pane_WITH_a_record_is_not_a_fault():
    agents = [Agent(name="felix", role="worker", pane="p-felix"),
              Agent(name="arya", role="worker", pane="p-arya")]
    cands = wf.classify(agents, _Panes(set()), None,
                        stopped=lambda n: 1000.0 if n == "felix" else None,
                        now=1000.0 + 14 * 60)
    by = {c.agent: c for c in cands}
    assert by["felix"].state == wf.AgentState.STOPPED_BY_OPERATOR
    assert by["felix"].stopped_ago == 14 * 60
    assert by["arya"].state == wf.AgentState.STOPPED, "no record -> still a fault"


def test_a_record_for_a_LIVE_pane_is_ignored():
    """A stop that has since been undone is history. Reading it as current would be
    launched.py's mistake: one past success asserted as a standing property."""
    agents = [Agent(name="felix", role="worker", pane="p-felix")]
    cands = wf.classify(agents, _Panes({"p-felix"}), None,
                        stopped=lambda n: 1000.0, now=2000.0)
    assert cands[0].state == wf.AgentState.IDLE


def test_no_reader_at_all_behaves_exactly_as_before():
    agents = [Agent(name="felix", role="worker", pane="p-felix")]
    cands = wf.classify(agents, _Panes(set()), None)
    assert cands[0].state == wf.AgentState.STOPPED


def test_a_retired_card_stays_RETIRED_even_with_a_stop_record():
    """Retirement is the stronger statement — it also says do not bring it back."""
    agents = [Agent(name="felix", role="worker", pane="p-felix", retired=True)]
    cands = wf.classify(agents, _Panes(set()), None, stopped=lambda n: 1000.0,
                        now=2000.0)
    assert cands[0].state == wf.AgentState.RETIRED


# --- the reading: prioritize / render ---------------------------------------

def test_a_deliberate_stop_is_REPORTED_and_never_an_item():
    cands = [wf.Candidate("felix", "worker", wf.AgentState.STOPPED_BY_OPERATOR,
                          stopped_ago=14 * 60),
             wf.Candidate("goodnight", "worker", wf.AgentState.STOPPED_BY_OPERATOR),
             wf.Candidate("arya", "worker", wf.AgentState.STOPPED)]
    out = wf.prioritize(cands)
    assert [s.candidate.agent for s in out.steps] == ["arya"], "only the real fault"
    rendered = out.render()
    assert "re-dispatch arya" in rendered
    # ...and the deliberate ones are stated, not hidden: the admin still needs to
    # know the fleet is short, and by whose hand.
    assert "2 agent(s) STOPPED BY AN OPERATOR, not faults" in rendered
    assert "felix (14m)" in rendered and "goodnight" in rendered
    assert "st new <agent>" in rendered


def test_a_deliberate_stop_that_ROSE_is_still_an_item():
    """The agent may be gone by decision; a risen escalation is not."""
    cands = [wf.Candidate("felix", "worker", wf.AgentState.STOPPED_BY_OPERATOR,
                          rose=True, stop_reason="needs-decision",
                          stopped_ago=3600)]
    out = wf.prioritize(cands)
    assert [s.action for s in out.steps] == ["decide"]
    assert out.deliberate == [], "it is an item, so it is not also a footnote"
    assert "stopped by an operator 1h00m ago" in out.render()


def test_nothing_but_deliberate_stops_renders_the_note_alone():
    cands = [wf.Candidate("felix", "worker", wf.AgentState.STOPPED_BY_OPERATOR)]
    rendered = wf.prioritize(cands).render()
    assert "PRIORITIZE" not in rendered, "no instruction list — there is nothing to do"
    assert "STOPPED BY AN OPERATOR" in rendered


# --- the writing: `st stop`, and the clearing on relaunch --------------------

import json                                                        # noqa: E402

from shantytown import cli                                         # noqa: E402
from shantytown.tmux import NullPanes                              # noqa: E402


class _Args:
    def __init__(self, root, **kw):
        self.root = root
        self.agent = kw.pop("agent", "ellie")
        self.dry_run = kw.pop("dry_run", False)
        self.reason = kw.pop("reason", "")
        self.backend = "files"
        self.repo = None
        for k, v in kw.items():
            setattr(self, k, v)


def _world(tmp_path, pane="crew-ellie"):
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps({"role": "worker", "pane": pane}))
    return tmp_path


def test_st_stop_records_the_intent(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    panes = NullPanes(live={"crew-ellie"}, owned={"crew-ellie"})
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    rc = cli._cmd_stop(_Args(root, reason="credit budget exhausted"))
    assert rc == cli.OK
    rec = FilesStops(root / "stopped").get("ellie")
    assert rec is not None, "st stop wrote no intent — the whole of #29 request 2"
    assert rec.reason == "credit budget exhausted"
    out = capsys.readouterr().out
    assert "DELIBERATE" in out
    assert "--retire" in out, "and it points at the way to make it stay down"


def test_st_stop_does_NOT_retire_the_card(tmp_path, monkeypatch):
    """A stop is not a retirement. If it were, respawn-on-loss would be off for
    every agent an operator ever stopped by hand."""
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux",
                        lambda *_a, **_k: NullPanes(live={"crew-ellie"},
                                                    owned={"crew-ellie"}))
    cli._cmd_stop(_Args(root))
    card = json.loads((root / "crew" / "ellie.json").read_text())
    assert card.get("retired", False) is False


def test_a_dry_run_stop_records_nothing(tmp_path, monkeypatch):
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux",
                        lambda *_a, **_k: NullPanes(live={"crew-ellie"},
                                                    owned={"crew-ellie"}))
    cli._cmd_stop(_Args(root, dry_run=True))
    assert FilesStops(root / "stopped").get("ellie") is None


def test_stopping_an_already_down_agent_records_nothing(tmp_path, monkeypatch):
    """st did not put it in that state and cannot know who did. Claiming the stop
    would be a fabricated fact — `st tend --retire` is how an operator adopts an
    agent that is already down."""
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(live=set()))
    cli._cmd_stop(_Args(root))
    assert FilesStops(root / "stopped").get("ellie") is None


def test_the_record_does_not_outlive_a_relaunch(tmp_path):
    """_launched_now is the ONE place that says 'it is running again', so no launch
    site can forget this and leave the next crash reading as a decision."""
    root = tmp_path
    FilesStops(root / "stopped").record("ellie", 1000.0)
    cli._launched_now(_Args(root), "ellie", None)
    assert FilesStops(root / "stopped").get("ellie") is None
