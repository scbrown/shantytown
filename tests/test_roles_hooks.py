"""roles --check, SECOND LEG: do the emitted stop hooks match the graph? (GitHub #6.4)

The complaint this file answers, quoted from the issue:

    "`st roles --check` can say `hooks: ok` in a world where no hook has ever been
     emitted; the check currently verifies reporting *lines*, not that stop events
     actually flow along them."

That was literally true — `hooks: ok` was a constant in the renderer, printed for
every row whose reports_to happened to resolve. So the tests that matter here are
NOT the ok-path ones. They are:

  * test_broken_lead_with_no_emitted_hooks  — the real defect, found on the real
    store: a lead with 10 reports and no lead.settings.json, previously "ok".
  * test_positive_control_*                 — the leg is REMOVED/DEFEATED and the
    failing tests must go green, proving they were detecting the leg and not
    passing for some unrelated reason.

A leg whose failure path has never run is indistinguishable from a column header,
which is what it replaced.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import roles
from shantytown.files import FilesRegistry
from shantytown.runtime import emitted_stop_directions, settings_for_role


def _card(d: Path, name: str, **fields) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(fields))


def _emit(root: Path, *rolenames: str) -> None:
    """Emit exactly the role settings named — the same artifact `role set` writes."""
    s = root / "settings"
    s.mkdir(parents=True, exist_ok=True)
    for r in rolenames:
        (s / f"{r}.settings.json").write_text(json.dumps(settings_for_role(r, root=root)))


def _reader(root: Path):
    return lambda role: emitted_stop_directions(root, role)


def _crew(root: Path) -> Path:
    """admin <- lead <- worker. The minimal graph with both a send and a drain."""
    c = root / "crew"
    _card(c, "sattler", role="administrator")
    _card(c, "dearing", role="lead", reports_to="sattler")
    _card(c, "gennaro", role="worker", reports_to="dearing")
    return c


# --- the ok path (necessary, not sufficient) ---------------------------------

def test_ok_when_every_role_emitted_the_hooks_its_graph_position_needs(tmp_path: Path):
    c = _crew(tmp_path)
    _emit(tmp_path, "administrator", "lead", "worker")
    rep = roles.check(FilesRegistry(c), emitted=_reader(tmp_path))
    assert rep.verdict == roles.OK
    assert all(r.hooks == roles.OK for r in rep.rows)
    assert "hooks: ok" in rep.render()


# --- the defect this leg exists to catch ------------------------------------

def test_broken_lead_with_no_emitted_hooks(tmp_path: Path):
    """THE REAL ONE. Measured on the live store 2026-07-19 before this leg existed:
    dearing was role=lead with 10 workers reporting to it, `.shanty/settings/` held
    only worker + administrator, and `roles --check` printed `hooks: ok` for every
    non-orphan row. Ten agents' stop events were being persisted into a store that
    nothing drained, and the checker called it healthy."""
    c = _crew(tmp_path)
    _emit(tmp_path, "administrator", "worker")        # NO lead.settings.json
    rep = roles.check(FilesRegistry(c), emitted=_reader(tmp_path))

    assert rep.verdict == roles.CANNOT_TELL           # unreadable != "no hooks"
    row = next(r for r in rep.rows if r.agent == "dearing")
    assert row.hooks == roles.CANNOT_TELL
    assert "no readable stop hooks emitted for role 'lead'" in row.note
    out = rep.render()
    assert "every one reports somewhere" not in out, "rendered as a clean bill of health"


def test_broken_when_emitted_hooks_lack_a_direction_the_graph_needs(tmp_path: Path):
    """The file EXISTS and parses — it just doesn't carry the direction this
    agent's position requires. Distinct from the missing-file case above, and it
    must be BROKEN (we read it; we know) rather than cannot-tell."""
    c = _crew(tmp_path)
    _emit(tmp_path, "administrator", "lead", "worker")
    # Downgrade the lead's emitted hooks to send-only: it can report upward but
    # will never drain its own reports' stop events.
    p = tmp_path / "settings" / "lead.settings.json"
    data = json.loads(p.read_text())
    data["hooks"]["Stop"] = [{"hooks": [h for h in data["hooks"]["Stop"][0]["hooks"]
                                        if "drain" not in h["command"].split()]}]
    p.write_text(json.dumps(data))

    rep = roles.check(FilesRegistry(c), emitted=_reader(tmp_path))
    assert rep.verdict == roles.BROKEN
    row = next(r for r in rep.rows if r.agent == "dearing")
    assert row.hooks == roles.BROKEN
    assert "HOOKS DO NOT MATCH THE GRAPH" in row.note
    assert "drain" in row.note


def test_two_problems_on_one_row_both_get_said(tmp_path: Path):
    """An orphan whose hooks are also missing has TWO faults. The first must not
    hide the second — that is how a fix lands, the row still fails, and nobody can
    tell it was for a different reason."""
    c = tmp_path / "crew"
    _card(c, "sattler", role="administrator")
    _card(c, "dearing", role="lead")                  # ORPHAN: no reports_to
    _card(c, "gennaro", role="worker", reports_to="dearing")
    _emit(tmp_path, "administrator", "worker")        # and no lead hooks

    rep = roles.check(FilesRegistry(c), emitted=_reader(tmp_path))
    row = next(r for r in rep.rows if r.agent == "dearing")
    assert "ORPHAN" in row.note
    assert "no readable stop hooks" in row.note


# --- the honesty rule: no reader supplied means UNVERIFIED, never ok ---------

def test_without_a_reader_the_hooks_column_says_it_did_not_look(tmp_path: Path):
    """The whole complaint in one assertion: unmeasured must not print as `ok`."""
    c = _crew(tmp_path)
    rep = roles.check(FilesRegistry(c))               # no emitted= reader
    assert all(r.hooks == roles.UNVERIFIED for r in rep.rows)
    out = rep.render()
    assert "hooks: ?" in out
    assert "hooks: ok" not in out, (
        "printed `hooks: ok` without ever opening a hook file — the #6 defect"
    )


# --- the reader itself: missing/garbage is None, NOT an empty set ------------

def test_reader_returns_None_for_a_missing_file_not_an_empty_set(tmp_path: Path):
    assert emitted_stop_directions(tmp_path, "lead") is None


def test_reader_returns_None_for_unparseable_settings(tmp_path: Path):
    s = tmp_path / "settings"
    s.mkdir()
    (s / "lead.settings.json").write_text("{not json")
    assert emitted_stop_directions(tmp_path, "lead") is None
    (s / "lead.settings.json").write_text('{"hooks": "not a dict"}')
    assert emitted_stop_directions(tmp_path, "lead") is None


def test_reader_reads_back_exactly_what_settings_for_role_emits(tmp_path: Path):
    """Writer and reader are separate on purpose (asking the writer what it would
    write proves nothing about disk). This pins them equivalent."""
    _emit(tmp_path, "worker", "lead", "administrator")
    assert emitted_stop_directions(tmp_path, "worker") == {"send"}
    assert emitted_stop_directions(tmp_path, "lead") == {"send", "drain"}
    assert emitted_stop_directions(tmp_path, "administrator") == {"drain"}


# --- POSITIVE CONTROLS: defeat the leg, the failures must go green ----------

def test_positive_control_a_constant_ok_reader_hides_the_missing_lead_hooks(tmp_path: Path):
    """Model the OLD behavior — a reader that always claims every direction is
    present — and confirm the missing-lead-hooks case then reports CLEAN.

    If this test ever fails, the leg is not what makes the real test above fail,
    and that test is passing for an unrelated reason.
    """
    c = _crew(tmp_path)
    _emit(tmp_path, "administrator", "worker")        # lead hooks still absent
    always_ok = lambda role: {"send", "drain"}
    rep = roles.check(FilesRegistry(c), emitted=always_ok)
    assert rep.verdict == roles.OK
    assert "hooks: ok" in rep.render()


def test_positive_control_ignoring_the_graph_hides_the_missing_drain(tmp_path: Path):
    """Defeat the OTHER half: keep the reader honest but stop deriving the
    requirement from the graph (need nothing of anyone). The send-only lead then
    passes — proving the graph comparison, not merely the file read, is what
    catches it."""
    c = _crew(tmp_path)
    _emit(tmp_path, "administrator", "lead", "worker")
    agents = FilesRegistry(c).all()
    dearing = next(a for a in agents if a.name == "dearing")

    # honest reader, send-only lead
    reader = lambda role: {"send"} if role == "lead" else {"send", "drain"}
    hv, note = roles._hooks_verdict(dearing, agents, reader)
    assert hv == roles.BROKEN                      # graph-derived: lead must drain

    # Same file, same reader — but with nobody in the graph reporting to dearing,
    # drain is not required and the identical settings pass.
    hv2, _ = roles._hooks_verdict(dearing, [dearing], reader)
    assert hv2 == roles.OK


# --- the deployment Bash guard extension point (internal-ref) -----------------

from shantytown import runtime


def test_no_bash_guard_emitted_by_default(tmp_path):
    """Shantytown ships no guard and hardcodes no path: absent the deployment
    config, PreToolUse carries only the edit-policy hook."""
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    matchers = [h.get("matcher") for h in s["hooks"]["PreToolUse"]]
    assert "Bash" not in matchers


def test_env_json_bash_guard_is_emitted_for_every_role(tmp_path):
    (tmp_path / "env.json").write_text(
        '{"SHANTY_BASH_GUARD": "/usr/local/lib/guards/host-policy.sh"}')
    for role in ("worker", "lead", "administrator"):
        s = runtime.claude_settings_for_role(role, root=tmp_path)
        bash = [h for h in s["hooks"]["PreToolUse"] if h.get("matcher") == "Bash"]
        assert len(bash) == 1, role
        assert bash[0]["hooks"] == [{"type": "command",
                                     "command": "/usr/local/lib/guards/host-policy.sh"}]
        # the edit-policy hook is untouched beside it
        assert any(h.get("matcher") != "Bash" for h in s["hooks"]["PreToolUse"])


def test_ambient_env_supplies_the_guard_when_env_json_lacks_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_BASH_GUARD", "/usr/local/lib/guards/ambient.sh")
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    bash = [h for h in s["hooks"]["PreToolUse"] if h.get("matcher") == "Bash"]
    assert bash and bash[0]["hooks"][0]["command"] == "/usr/local/lib/guards/ambient.sh"


# --- the deployment session-capture Stop hook extension point (internal-ref) --


def test_no_capture_hook_emitted_by_default(tmp_path):
    """Shantytown ships no capture hook: absent deployment config, every role's
    Stop list is exactly its own stop machinery (positive shape assert, not just
    absence — same-output-two-worlds discipline)."""
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    cmds = [h["command"] for h in s["hooks"]["Stop"][0]["hooks"]]
    assert len(cmds) == 2  # send + haul, nothing appended
    assert all("stop_event" in c for c in cmds)


def test_env_json_capture_hook_is_appended_last_for_every_role(tmp_path):
    (tmp_path / "env.json").write_text(
        '{"SHANTY_STOP_CAPTURE": "/usr/local/lib/hooks/session-capture.sh"}')
    for role, own_count in (("worker", 2), ("lead", 2), ("administrator", 2)):
        s = runtime.claude_settings_for_role(role, root=tmp_path)
        hooks = s["hooks"]["Stop"][0]["hooks"]
        # appended, exactly once, LAST — the role's own machinery precedes it
        assert len(hooks) == own_count + 1, role
        assert hooks[-1] == {"type": "command",
                             "command": "/usr/local/lib/hooks/session-capture.sh"}, role
        assert all("session-capture" not in h["command"] for h in hooks[:-1]), role


def test_ambient_env_supplies_the_capture_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_STOP_CAPTURE", "/usr/local/lib/hooks/ambient-capture.sh")
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    hooks = s["hooks"]["Stop"][0]["hooks"]
    assert hooks[-1]["command"] == "/usr/local/lib/hooks/ambient-capture.sh"


def test_env_json_capture_wins_over_ambient(tmp_path, monkeypatch):
    (tmp_path / "env.json").write_text(
        '{"SHANTY_STOP_CAPTURE": "/from/env.json"}')
    monkeypatch.setenv("SHANTY_STOP_CAPTURE", "/from/ambient")
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    assert s["hooks"]["Stop"][0]["hooks"][-1]["command"] == "/from/env.json"
