"""A cycle must not evict the person watching it, and should not restart what it
can simply empty.

Stiwi, 2026-09-21, verbatim:

    "cycle needs to work a lot better in st. it needs to be seamless; it kicked
     me out of claude code and i had to reattach. it should probably use native
     /clear or something with an injection of whats expected for hte next
     sessions with the docs and quipu node etc"

Three separable claims, one per section below. The first is the bug, the second
is the mechanism, the third is the handoff that makes the mechanism worth having.
"""
from pathlib import Path

import pytest

from shantytown import cycle as cycle_mod
from shantytown import harness as harness_mod
from shantytown import resume_brief
from shantytown.protocols import Agent
from shantytown.runtime import ClaudeRuntime
from shantytown.tmux import NullPanes


def _plan(**over):
    base = dict(clear_command="/clear", session_live=True, owned=True, idle=True,
                input_empty=True, dangerous=False, bypass_verifiable=False)
    return cycle_mod.plan(**{**base, **over})


# --------------------------------------------------------------------------
# 1. THE BUG: a cycle detached its operator.
# --------------------------------------------------------------------------

def test_respawn_keeps_the_session_alive():
    """THE REGRESSION TEST FOR THE COMPLAINT. kill_session destroys the session,
    and destroying a session detaches every client attached to it — which is how
    cycling an agent ejected the human watching it. respawn replaces the process
    and leaves the session standing."""
    panes = NullPanes(live={"st-hammond"})
    panes.respawn("st-hammond", "/tmp")
    assert panes.exists("st-hammond"), (
        "the session must survive — an operator attached to it stays attached")
    assert panes.respawned == [("st-hammond", "/tmp")]


def test_respawn_refuses_to_invent_a_session():
    """A restart that silently becomes a start is how an agent ends up in a
    session whose ownership nobody checked."""
    panes = NullPanes(live=set())
    with pytest.raises(RuntimeError):
        panes.respawn("st-nobody")


def test_no_mechanism_but_the_floor_destroys_the_session():
    """The POSITIVE CONTROL on the fix. Only the last-resort mode is allowed to
    take the session down; if a future edit routes the common cases back through
    it, the operator starts getting ejected again and this fails."""
    assert _plan().mode == cycle_mod.SOFT
    assert _plan(idle=False).mode == cycle_mod.RESPAWN
    assert _plan(clear_command=None).mode == cycle_mod.RESPAWN
    assert _plan(prefer_soft=False).mode == cycle_mod.RESPAWN
    # The one case that has no live session to keep is the one case that starts
    # a new one — and by then there is no client attached to lose.
    assert _plan(session_live=False).mode == cycle_mod.RELAUNCH


# --------------------------------------------------------------------------
# 2. THE MECHANISM: clear in place when it is safe, restart when it is not.
# --------------------------------------------------------------------------

def test_a_busy_or_typed_in_pane_is_never_cleared_in_place():
    """A clear command is TYPED, at the same prompt a human uses. Into a busy
    pane it queues behind the turn; into a pane with unsubmitted text it APPENDS,
    and what gets submitted is neither the clear nor the operator's line."""
    assert _plan(idle=False).mode == cycle_mod.RESPAWN
    assert _plan(input_empty=False).mode == cycle_mod.RESPAWN


def test_a_bypassed_card_is_not_cleared_until_bypass_can_be_PROVEN():
    """The measured fault this verb was built around: a clear dropped the session
    out of bypass into MANUAL, leaving the agent up and undispatchable — the
    remedy needing its own remedy. Unproven means unproven, whichever way it
    would be convenient to read it."""
    assert _plan(dangerous=True, bypass_verifiable=False).mode == cycle_mod.RESPAWN
    assert _plan(dangerous=True, bypass_verifiable=True).mode == cycle_mod.SOFT
    # A card that does not run on bypass is indifferent: a permission mode that
    # resets to what its settings file already says has not changed.
    assert _plan(dangerous=False, bypass_verifiable=False).mode == cycle_mod.SOFT


def test_the_bypass_marker_is_the_one_measured_off_a_live_pane():
    """Measured 2026-09-21 on a live crew pane. Matched on WORDS, never on the
    ⏵⏵ glyphs: decoration is what gets restyled between releases, and a marker
    that depends on it stops matching silently — which here would route every
    bypassed card away from the soft path with nobody noticing."""
    card = Agent(name="sattler")
    footer = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
    assert harness_mod.bypass_verifiable(card) is True
    assert harness_mod.bypass_intact(card, footer) is True
    assert harness_mod.bypass_intact(card, "? for shortcuts") is False


def test_a_harness_with_no_clear_command_says_so_rather_than_guessing():
    assert harness_mod.clear_command_for(Agent(name="a")) == "/clear"
    assert harness_mod.clear_command_for(Agent(name="b", harness="codex")) is None
    # Unmeasured must read as "do not risk it", never as "fine".
    assert harness_mod.bypass_intact(Agent(name="b", harness="codex"), "x") is None


# --------------------------------------------------------------------------
# 3. THE HANDOFF: what the emptied session is handed.
# --------------------------------------------------------------------------

def test_the_brief_carries_the_checkpoint_the_bead_the_nodes_and_the_docs():
    """Every one of these was already known at cycle time and delivered nowhere
    the new session would look."""
    text = resume_brief.compose(
        "hammond", checkpoint="mid-way through the jev mapping",
        checkpoint_bead="st-2", quipu_nodes=["aegis:decision-jev"],
        item="sha-ei0", docs=["docs/handoff.md"])
    assert resume_brief.BANNER in text
    for expected in ("mid-way through the jev mapping", "st-2",
                     "aegis:decision-jev", "sha-ei0", "docs/handoff.md"):
        assert expected in text
    assert "You are still hammond" in text


def test_the_brief_is_delivered_exactly_once(tmp_path):
    """A brief left on disk is re-injected at EVERY later session start — so a
    session three restarts later would be told, in its own harness's voice, to
    resume a task it finished two sessions ago."""
    briefs = resume_brief.Briefs(tmp_path)
    briefs.put("hammond", "resume this")
    assert briefs.take("hammond")["text"] == "resume this"
    assert briefs.take("hammond") is None
    assert briefs.peek("hammond") is None


def test_a_refused_cycle_leaves_no_brief_behind(tmp_path):
    """The brief is written BEFORE the clear, so a cycle that then refuses must
    take it back — otherwise the agent's next ordinary session start is told its
    context was cleared by a cycle that never happened."""
    briefs = resume_brief.Briefs(tmp_path)
    briefs.put("hammond", "resume this")
    briefs.drop("hammond")
    assert briefs.peek("hammond") is None


def test_the_hook_prints_nothing_when_there_is_nothing(tmp_path, monkeypatch, capsys):
    """This hook runs at EVERY session start and almost none of them are cycles.
    Silence is the common case and must cost the model nothing."""
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    monkeypatch.setenv("SHANTY_AGENT", "hammond")
    assert resume_brief.main([]) == 0
    assert capsys.readouterr().out == ""


def test_the_hook_injects_the_brief_then_consumes_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    monkeypatch.setenv("SHANTY_AGENT", "hammond")
    resume_brief.Briefs(tmp_path).put("hammond", "the handoff body")
    assert resume_brief.main([]) == 0
    assert "the handoff body" in capsys.readouterr().out
    assert resume_brief.main([]) == 0
    assert capsys.readouterr().out == ""


def test_the_hook_never_fails_a_session_start(tmp_path, monkeypatch, capsys):
    """FAIL-OPEN, ALWAYS. A hook that can refuse a session start is a hook that
    can take an agent off the fleet, and this one runs at the only moment an
    agent cannot recover by itself."""
    monkeypatch.delenv("SHANTY_ROOT", raising=False)
    monkeypatch.delenv("SHANTY_AGENT", raising=False)
    assert resume_brief.main([]) == 0
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    monkeypatch.setenv("SHANTY_AGENT", "hammond")
    path = resume_brief.Briefs(tmp_path)._path("hammond")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert resume_brief.main([]) == 0


def test_a_stale_brief_is_dropped_unread(tmp_path, monkeypatch, capsys):
    """Anything still pending a day later belongs to a cycle that never
    completed. A fresh session cannot act on it, so spending context telling it
    about one is worse than losing it."""
    monkeypatch.setenv("SHANTY_ROOT", str(tmp_path))
    monkeypatch.setenv("SHANTY_AGENT", "hammond")
    briefs = resume_brief.Briefs(tmp_path)
    briefs.put("hammond", "ancient")
    import json
    p = briefs._path("hammond")
    data = json.loads(p.read_text())
    data["at"] = 0.0
    p.write_text(json.dumps(data))
    assert resume_brief.main([]) == 0
    assert "ancient" not in capsys.readouterr().out


def test_the_brief_is_wired_into_the_session_start_hook():
    """The whole point of the soft path: the event that EMPTIES the context is
    the one that refills the part worth keeping. If the hook is not emitted, a
    cleared session comes back knowing nothing."""
    from shantytown import runtime as runtime_mod
    hooks = runtime_mod.session_start_hooks("/tmp/root")
    commands = [h["command"] for group in hooks for h in group["hooks"]]
    assert any("shantytown.resume_brief" in c for c in commands)
    # resolve() on the expected side too: macOS resolves /tmp to /private/tmp,
    # and a test that hardcodes the unresolved spelling fails on a developer's
    # laptop while passing in CI — the green-in-CI-red-on-every-desk shape this
    # repo has already been bitten by once.
    rooted = next(c for c in commands if "shantytown.resume_brief" in c)
    assert f"--root {Path('/tmp/root').resolve()}" in rooted, (
        "unrooted, the hook resolves cwd/.shanty — a store the agent's own "
        "workspace does not have")
    assert "shantytown.resume_brief" in commands[0], (
        "FIRST: a session that was just cycled must learn THAT before it is "
        "told how to research")


# --------------------------------------------------------------------------
# 4. A CYCLE THAT COULD NEVER RUN.
# --------------------------------------------------------------------------

def test_a_workspace_that_is_not_a_repo_does_not_block_a_cycle(tmp_path):
    """A directory with no repository in it holds no commits, so it cannot
    strand any — and a cycle relaunches the same clone and leaves its files
    exactly where they are. The same reasoning that already makes untracked
    files a notice rather than a gate.

    MEASURED, 2026-09-21: this host's administrator workspace is a plain
    directory, so its cycle refused permanently and `st crew` showed it
    cycle-blocked. The remedy the refusal named — "commit and `st repo push`
    first" — is not available in a directory with no repository to commit to, so
    the refusal had no exit at all."""
    from shantytown.workspace import tree_staleness
    plain = tmp_path / "workspace"
    plain.mkdir()
    (plain / "NOTES.md").write_text("work in progress")
    s = tree_staleness(plain)
    assert s.error is None, (
        "a non-repository must not read as a tree we could not read — "
        "cycle.assess gates on `error` and the refusal would be permanent")
    assert "not a git working tree" in s.note

    verdict = cycle_mod.assess("hammond", [str(plain)], "a checkpoint",
                               staleness=tree_staleness)
    assert verdict.ok, verdict.render()


def test_a_REAL_repo_that_cannot_be_read_still_blocks(tmp_path):
    """The negative control, and the reason the downgrade above is narrow. Only
    'there is no repository here' is downgraded; a tree that genuinely could not
    be read still gates, because that one may hold the only copy of something."""
    class Unreadable:
        error = "could not run git"
        note = error
        unverified = None
        untracked_count = 0
        dirty = False
        unpushed = 0
        ref = None

    verdict = cycle_mod.assess("hammond", ["/some/tree"], "a checkpoint",
                               staleness=lambda _t: Unreadable())
    assert not verdict.ok
