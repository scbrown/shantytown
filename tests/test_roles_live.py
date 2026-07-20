"""roles --check, THIRD LEG: does the RUNNING PROCESS match the graph? (aegis-0v97)

Leg two (test_roles_hooks.py) verifies the ROLE'S ARTIFACT carries the right stop
hooks. This leg exists because that question is strictly weaker than the one the
tier actually needs answered, and the gap between them was LIVE on the real store
for the entire time leg two existed:

    dearing   role=lead   lead.settings.json emits [send, drain]   -> hooks: ok
              ...while the process in its pane had been launched by a FOREIGN
              launcher (gt-crew-up) with gastown settings carrying no stop_event
              hook at all. Seven workers routed to it. Every one of their stop
              events was write-only, and `st roles --check` exited 0 throughout.

An artifact is a statement of INTENT. `st` does not own every process that answers
to a name in its registry, so intent is not evidence. tmux.py already states this
rule for the kill path — a pane NAME match is never sufficient permission to reap.
This is the same rule for liveness: a name match is never sufficient evidence of
DRAIN.

As in leg two, the ok-path tests are not the ones that matter. These are:

  * test_live_lead_without_drain_is_broken   — the real defect, real shape.
  * test_positive_control_*                  — defeat the leg and the failing
    tests must go green, proving they detect THIS leg and are not passing for
    some unrelated reason.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import roles
from shantytown.files import FilesRegistry
from shantytown.runtime import (LiveWiring, live_stop_directions, live_wiring,
                                settings_path_in_cmdline, stop_directions_in,
                                settings_for_role)


def _card(d: Path, name: str, **fields) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(fields))


def _settings_file(tmp: Path, name: str, role: str) -> Path:
    """Write a real emitted settings artifact and return its path."""
    p = tmp / f"{name}.settings.json"
    p.write_text(json.dumps(settings_for_role(role, root=tmp)))
    return p


def _tier(tmp: Path):
    """A lead with two reports under an administrator — the real shape."""
    crew = tmp / "crew"
    _card(crew, "admin", role="administrator", reports_to=None, pane="p-admin")
    _card(crew, "lead", role="lead", reports_to="admin", pane="p-lead")
    _card(crew, "w1", role="worker", reports_to="lead", pane="p-w1")
    _card(crew, "w2", role="worker", reports_to="lead", pane="p-w2")
    return FilesRegistry(crew)


def _live_from(mapping):
    """A live reader driven by a pane -> directions map. Absent = cannot tell."""
    def read(pane):
        if pane not in mapping:
            return None
        return LiveWiring(directions=mapping[pane], settings_path="/fake/s.json")
    return read


# --- the parse helpers, which both readers share -------------------------

def test_settings_path_pulled_from_a_real_launch_line():
    line = ("/home/x/.local/bin/claude --dangerously-skip-permissions "
            "--settings /home/x/gt/.shanty/settings/lead.settings.json")
    assert settings_path_in_cmdline(line).endswith("lead.settings.json")


def test_settings_path_equals_form_also_parses():
    assert settings_path_in_cmdline("claude --settings=/tmp/a.json") == "/tmp/a.json"


def test_no_settings_on_the_line_is_none():
    # The gastown launch shape that started all of this carries --settings, but a
    # launch with none at all is the hookless zombie and must be distinguishable.
    assert settings_path_in_cmdline("claude --dangerously-skip-permissions") is None
    assert settings_path_in_cmdline("") is None


def test_live_reader_reads_the_file_the_process_names(tmp_path):
    p = _settings_file(tmp_path, "lead", "lead")
    got = live_stop_directions("pane", lambda _: f"claude --settings {p}")
    assert got == {"send", "drain"}


def test_live_reader_settingsless_process_is_an_empty_set_not_none(tmp_path):
    """The distinction the whole leg rests on. A process with no --settings
    carries no stop hooks — that is a MEASUREMENT (empty set). None means we
    could not look, and must never be rendered as a finding."""
    assert live_stop_directions("pane", lambda _: "claude --dangerous") == set()
    assert live_stop_directions("pane", lambda _: None) is None


def test_live_reader_unreadable_settings_is_cannot_tell(tmp_path):
    got = live_stop_directions("pane", lambda _: "claude --settings /nope/x.json")
    assert got is None


# --- the leg itself ------------------------------------------------------

def test_live_lead_without_drain_is_broken(tmp_path):
    """THE REAL DEFECT. The lead's ARTIFACT is perfect; the process running in
    its pane carries nothing. Leg two passes, leg three must not."""
    reg = _tier(tmp_path)
    live = _live_from({
        "p-admin": {"drain"},
        "p-lead": set(),          # launched by a foreign launcher: no hooks
        "p-w1": {"send"},
        "p-w2": {"send"},
    })
    rep = roles.check(reg, live=live)
    row = next(r for r in rep.rows if r.agent == "lead")
    assert row.verdict == roles.BROKEN
    assert "LIVE PROCESS DOES NOT MATCH THE GRAPH" in row.note
    assert rep.verdict == roles.BROKEN


def test_the_note_names_every_consequence_not_just_the_first(tmp_path):
    """A lead missing BOTH legs strands its reports as well as itself. Reporting
    only the first consequence would understate it by two agents."""
    reg = _tier(tmp_path)
    rep = roles.check(reg, live=_live_from({"p-lead": set()}))
    note = next(r for r in rep.rows if r.agent == "lead").note
    assert "its own stop dies here" in note
    assert "2 report(s)" in note


def test_the_note_says_what_it_HAS_not_only_what_it_lacks(tmp_path):
    """dearing's correction, and it is load-bearing. The first version said
    "carries NO stop hooks at all". That is false as English and false in the
    EXPENSIVE direction: the 8 real agents it named do carry hooks — gastown's,
    including the rm -rf and force-push tap guards — they simply carry no
    `stop_event` direction. Read literally the old string IS aegis-05up
    ("respawn dropped --settings, the guards are gone"), a genuine emergency
    that was not happening. A reader would scramble for the wrong thing, or
    start disbelieving 05up for when it really fires."""
    reg = _tier(tmp_path)
    live = lambda p: LiveWiring(directions=set(),
                                settings_path="/gt/crew/.claude/settings.json")
    note = next(r for r in roles.check(reg, live=live).rows
                if r.agent == "lead").note
    assert "no `stop_event` hook" in note
    assert "NO stop hooks at all" not in note
    # Naming the path is what makes the FOREIGN LAUNCHER self-evident.
    assert "/gt/crew/.claude/settings.json" in note


def test_a_process_with_no_settings_at_all_is_still_called_out_as_05up(tmp_path):
    """The genuinely hookless case must stay distinguishable from the
    wrong-hooks case — that is the whole point of the wording fix. It is not
    softened away, it is NAMED."""
    reg = _tier(tmp_path)
    live = lambda p: LiveWiring(directions=set(), settings_path=None)
    note = next(r for r in roles.check(reg, live=live).rows
                if r.agent == "lead").note
    assert "NO --settings at all" in note
    assert "aegis-05up" in note


def test_a_healthy_tier_passes_all_three_legs(tmp_path):
    reg = _tier(tmp_path)
    live = _live_from({
        "p-admin": {"drain"}, "p-lead": {"send", "drain"},
        "p-w1": {"send"}, "p-w2": {"send"},
    })
    rep = roles.check(reg, live=live)
    assert rep.verdict == roles.OK
    assert all(r.live == roles.OK for r in rep.rows)


def test_a_down_pane_is_not_a_fault(tmp_path):
    """route_stop already RISES to the administrator when a lead is unreachable,
    loudly and with a reason. An unreadable pane must therefore not be reported
    as a finding — this leg exists for pane UP / wiring WRONG, which that rise
    path cannot see."""
    reg = _tier(tmp_path)
    rep = roles.check(reg, live=_live_from({}))   # every pane unreadable
    assert rep.verdict == roles.OK
    assert all(r.live == roles.UNVERIFIED for r in rep.rows)


def test_omitting_the_live_reader_reports_unverified_never_ok(tmp_path):
    """Same contract as leg two: this checker does not print a word it did not
    measure."""
    rep = roles.check(_tier(tmp_path))
    assert all(r.live == roles.UNVERIFIED for r in rep.rows)
    assert "live: ok" not in rep.render()


def test_both_legs_are_reported_neither_hides_the_other(tmp_path):
    """A row failing leg two AND leg three must say both. The first problem
    hiding the second is the exact bug the renderer already had once."""
    crew = tmp_path / "crew"
    _card(crew, "admin", role="administrator", reports_to=None, pane="p-admin")
    _card(crew, "lead", role="lead", reports_to="admin", pane="p-lead")
    _card(crew, "w1", role="worker", reports_to="lead", pane="p-w1")
    reg = FilesRegistry(crew)
    rep = roles.check(reg,
                      emitted=lambda role: None,     # leg two: cannot tell
                      live=_live_from({"p-lead": set()}))   # leg three: broken
    row = next(r for r in rep.rows if r.agent == "lead")
    assert "no readable stop hooks emitted" in row.note
    assert "LIVE PROCESS DOES NOT MATCH THE GRAPH" in row.note


# --- positive controls: defeat the leg, the failures must vanish ---------

def test_positive_control_defeating_the_leg_makes_the_defect_pass(tmp_path):
    """Feed the leg the ARTIFACT's answer instead of the PROCESS's — i.e. undo
    exactly this change. The broken lead must then read as healthy, proving
    these tests detect leg three and not something incidental."""
    reg = _tier(tmp_path)
    defeated = lambda _pane: LiveWiring(directions={"send", "drain"},
                                        settings_path="/fake/s.json")
    rep = roles.check(reg, live=defeated)
    assert rep.verdict == roles.OK
    assert next(r for r in rep.rows if r.agent == "lead").verdict == roles.OK


def test_positive_control_the_reader_is_not_a_constant(tmp_path):
    """live_stop_directions must return DIFFERENT answers for different inputs.
    `hooks: ok` was a constant that looked like a measurement; a reader that
    always returns the same set would reproduce that bug one layer down."""
    p_lead = _settings_file(tmp_path, "lead", "lead")
    p_worker = _settings_file(tmp_path, "worker", "worker")
    a = live_stop_directions("x", lambda _: f"claude --settings {p_lead}")
    b = live_stop_directions("x", lambda _: f"claude --settings {p_worker}")
    c = live_stop_directions("x", lambda _: "claude")
    assert a == {"send", "drain"}
    assert b == {"send"}
    assert c == set()
    assert len({frozenset(a), frozenset(b), frozenset(c)}) == 3


def test_both_readers_share_one_parse(tmp_path):
    """emitted_stop_directions and live_stop_directions must agree on the same
    file. If they parsed differently, a leg-two/leg-three mismatch would be
    unattributable — you could not tell real runtime drift from two parsers
    disagreeing."""
    p = _settings_file(tmp_path, "lead", "lead")
    direct = stop_directions_in(p)
    via_live = live_stop_directions("x", lambda _: f"claude --settings {p}")
    assert direct == via_live == {"send", "drain"}
