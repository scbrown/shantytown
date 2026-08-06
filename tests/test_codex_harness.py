"""THE SECOND HARNESS (codex) — and, per docs/adapters.md, the proof the first
did not leak.

These tests are deliberately of two kinds, and the difference matters when one
of them goes red:

  CLAIMS ABOUT CODEX — the launch string, the config.toml shape, the blocking
  Stop contract. Every one was read out of openai/codex `main` on 2026-08-06 and
  is cited in shantytown/codex.py's header. There is no codex binary on the
  build host, so these tests pin what we BELIEVE about somebody else's program;
  they cannot prove codex agrees. When codex changes, they go red for the right
  reason (a claim we made) and the fix is to re-read the source, not to loosen
  the assert.

  CLAIMS ABOUT US — that the seam is actually generic: the emitter, the
  resolver, the compose invariant, the live reader and the artifact reader all
  go through the harness and none of them assume Claude Code's file name, format
  or flag. These are the ones that would have been green while the interface was
  a lie, which is exactly why the second implementation exists.

The end-to-end at the bottom is the one that matters most: emit -> resolve ->
compose -> read the routing back off the composed command line. That path
crosses every place Claude Code used to be hardcoded.
"""
from __future__ import annotations
import json
import tomllib
from pathlib import Path

import pytest

from shantytown import cli, codex, harness as harness_mod
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent
from shantytown.runtime import (ClaudeRuntime, live_wiring, require_capability,
                                settings_path_in_cmdline, stop_directions_in)
from shantytown.tmux import NullPanes

CODEX = harness_mod.get("codex")
CLAUDE = harness_mod.get("claude")


# --- the launch (claims about codex) -------------------------------------------

def test_a_relative_settings_path_still_becomes_an_absolute_home(tmp_path):
    """The launch `cd`s into the workspace before the env applies, and codex does
    not fail on a home that is not there — it quietly uses an empty one: no
    hooks, no auth, an agent that starts and is governed by nothing."""
    rel = "settings/codex/worker/config.toml"
    launch = CODEX.launch(Agent(name="ellie", role="worker", workspace="/w"), rel)
    assert f"CODEX_HOME={Path(rel).resolve().parent} " in launch
    assert CODEX.carries_settings(launch, rel)      # the invariant still holds


def test_the_launch_points_at_the_config_by_CODEX_HOME_not_a_flag():
    """codex has no `--settings`. It reads config.toml out of its home, so the
    pointer is an env export naming that DIRECTORY — and the invariant "a
    composed launch always carries its settings" is answered by the harness
    rather than by grepping for Claude Code's flag."""
    card = Agent(name="ellie", role="worker")
    p = "/s/codex/worker/config.toml"
    launch = CODEX.launch(card, p)
    assert "CODEX_HOME=/s/codex/worker " in launch
    assert "--settings" not in launch
    assert CODEX.carries_settings(launch, p)
    # and it comes back out again — this is what the live reader uses.
    assert CODEX.settings_in_cmdline(launch) == p


def test_the_launch_carries_the_same_identity_env_as_claude():
    """SHANTY_AGENT/BOBBIN_ROLE/BEADS_ACTOR/ST_ROLES are not Claude Code's — they
    are how a shantytown agent knows who it is, and dropping one on a second
    harness is how a codex agent's bd events all get written as $USER (GitHub
    #24) or its role set stops reaching it (#37)."""
    card = Agent(name="ada", role="lead", domain="ops", reports_to="arnold")
    launch = CODEX.launch(card, "/s/codex/lead/config.toml", root="/tmp/r")
    for expected in ("SHANTY_ROOT=/tmp/r", "SHANTY_AGENT=ada", "BOBBIN_ROLE=lead",
                     "BEADS_ACTOR=ada", "ST_ROLES=lead", "ST_ROLE_DOMAIN=ops",
                     "ST_REPORTS_TO=arnold"):
        assert expected in launch, f"{expected} missing from {launch}"


def test_hook_trust_is_bypassed_and_that_is_the_point():
    """codex will not run a hook it has no persisted trust record for
    (codex.py fact 4). Emitting a role's whole stop routing into a program that
    then declines to run it is the declared-but-inert failure this repo exists
    to not ship, so the flag is a DEFAULT here — unlike every other
    `dangerously-` flag, which stays per-card below."""
    launch = CODEX.launch(Agent(name="ellie", role="worker"), "/s/c/w/config.toml")
    assert "--dangerously-bypass-hook-trust" in launch
    # It is NOT the approvals/sandbox bypass, which is a different flag and a
    # different decision.
    assert "--dangerously-bypass-approvals-and-sandbox" not in launch


def test_the_permission_bypass_is_per_card_exactly_like_claudes():
    plain = CODEX.launch(Agent(name="ellie", role="worker"), "/s/c/w/config.toml")
    danger = CODEX.launch(Agent(name="ellie", role="worker", dangerous=True),
                          "/s/c/w/config.toml")
    assert "--dangerously-bypass-approvals-and-sandbox" not in plain
    assert "--dangerously-bypass-approvals-and-sandbox" in danger


def test_the_cards_model_and_workspace_are_honoured():
    card = Agent(name="ellie", role="worker", model="gpt-5-codex", workspace="/w")
    launch = CODEX.launch(card, "/s/c/w/config.toml")
    assert "--model gpt-5-codex" in launch          # GitHub #17/#9, one harness over
    assert launch.startswith("cd /w && ")


def test_a_chrome_card_is_REFUSED_not_silently_stripped():
    """codex has no browser integration. Honouring the field by ignoring it would
    launch an agent whose card claims a capability its process does not have —
    the same class of failure as launching a different program than the card
    asked for, which is what UnknownHarness already refuses."""
    card = Agent(name="ellie", role="worker", chrome=True)
    with pytest.raises(harness_mod.Unsupported, match="chrome"):
        CODEX.launch(card, "/s/c/w/config.toml")


def test_st_new_turns_that_refusal_into_exit_1_not_a_traceback(tmp_path, capsys,
                                                              monkeypatch):
    """The refusal has to reach the operator the way every other one does. A new
    exception type that nothing catches is an exit-1 path that exits 2 with a
    stack trace (the bug aegis-85ox fixed for UnknownHarness)."""
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "ellie.json").write_text(json.dumps(
        {"role": "worker", "harness": "codex", "pane": "crew-ellie", "chrome": True}))
    cli._emit_role_settings(root, {"worker"}, harness_name="codex")
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: NullPanes(live=set()))

    class _Args:
        cmd, agent, root_how = "new", "ellie", "explicit"
        dry_run, session, force = True, None, False
        def __init__(self, root):
            self.root = root
    rc = cli._cmd_new(_Args(root))
    assert rc == cli.REFUSED
    assert "refused:" in capsys.readouterr().err


# --- the capability (the claim that changed) -----------------------------------

def test_codex_declares_blocking_stop_so_a_lead_is_hostable():
    """This REVERSES what this repo used to say. codex's Stop hook parses
    decision:block + reason and feeds the reason back to the model as a
    continuation prompt (codex.py fact 3), which is the whole capability. The
    gate is keyed on the declaration and never on a program name, so it opens
    here without anything in tier.py or runtime.py knowing what codex is."""
    assert CODEX.hooks(Agent(name="x", role="lead")).blocking_stop is True
    require_capability(CODEX, Agent(name="x", role="administrator"))   # must not raise


def test_role_set_lets_a_codex_card_become_a_lead(tmp_path):
    from shantytown.tier import role_set
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "malcolm.json").write_text(json.dumps({"role": "worker", "harness": "codex"}))
    (crew / "ellie.json").write_text(json.dumps({"role": "worker", "harness": "codex"}))
    reg = FilesRegistry(crew)
    role_set(reg, "malcolm", "lead", reports=["ellie"])      # gate must not refuse
    assert reg.get("malcolm").role == "lead"
    assert reg.get("malcolm").harness == "codex"             # and the field survived


# --- the artifact (claims about us: the seam is generic) -----------------------

def test_the_artifact_is_a_config_toml_in_a_directory_per_role():
    """CODEX_HOME names a DIRECTORY and codex reads a fixed filename inside it,
    so the harness's name carries directories — which is the thing the emitter
    used to assume it never would."""
    assert CODEX.settings_name("worker") == "codex/worker/config.toml"
    assert CODEX.agent_settings_name("ada") == "codex/agent-ada/config.toml"
    # and claude's are untouched — nine live agents launch from these names.
    assert CLAUDE.settings_name("worker") == "worker.settings.json"
    assert CLAUDE.agent_settings_name("ada") == "agent-ada.settings.json"


def test_the_emitted_config_is_TOML_that_parses_back_to_what_we_meant():
    """The writer is ours (zero dependencies, and the stdlib reads TOML but does
    not write it), so it is proved by ROUND TRIP rather than by eye. Its first
    bug was a double-declared table that looked fine in the output and made the
    file unloadable."""
    settings = codex.settings_for_role("lead", root="/tmp/r")
    text = codex.render(settings)
    assert tomllib.loads(text) == settings


@pytest.mark.parametrize("role,expected", [("worker", {"send"}),
                                           ("lead", {"send", "drain"}),
                                           ("administrator", {"drain"})])
def test_the_stop_routing_is_the_same_routing_claude_gets(role, expected):
    """The routing table is SHANTYTOWN's (runtime.role_stop_hooks), shared by
    both harnesses. A second copy is how a lead comes to send on one program and
    drain on the other."""
    text = codex.render(codex.settings_for_role(role, root="/tmp/r"))
    assert codex.stop_directions(text) == expected
    from shantytown.runtime import claude_settings_for_role
    claude = CLAUDE.read_stop_directions(
        json.dumps(claude_settings_for_role(role, root="/tmp/r")))
    assert codex.stop_directions(text) == claude


def test_the_matcher_scoped_guards_are_NOT_emitted_and_that_is_deliberate():
    """A matcher is a claim about the host program's TOOL NAMES. Claude Code's
    are "Bash" and "mcp__.*"; codex's are unmeasured. Emitting the guards with
    the wrong vocabulary would not be a weaker guard, it would be a guard that
    never runs while reading as wired — this repo has already paid that bill
    once (aegis-ac5x/18e0). Pinned so nobody "completes" the emitter by copying
    Claude Code's matchers across."""
    settings = codex.settings_for_role("worker", root="/tmp/r")
    for event in codex.MATCHERS_NOT_EMITTED:
        assert event not in settings["hooks"], (
            f"{event} is matcher-scoped and codex's tool vocabulary is unmeasured "
            f"— see codex.MATCHERS_NOT_EMITTED before adding it")
    # the matcher-free events ARE emitted, so the omission is narrow.
    assert set(settings["hooks"]) == {"SessionStart", "Stop"}


def test_the_whole_rulebook_reaches_the_agent_not_the_first_32KiB():
    """codex truncates AGENTS.md at `project_doc_max_bytes` (default 32 KiB) and
    SAYS NOTHING — no notice in the prompt, none on stderr. MEASURED on
    codex-cli 0.146.1 via `codex debug prompt-input`, which renders the
    model-visible prompt at zero model cost (aegis-ovffp):

        crew rulebook on disk       65367 B
        delivered at the default    32690 B   — cut MID-WORD, 50% lost
        canary on the last line     ABSENT

    This is a CLAIM ABOUT CODEX in the sense of this file's header, but an
    unusually strong one: it was measured against the binary on this host rather
    than read out of source. If codex changes the default, this test does not go
    red — the emitted value is what protects us, which is the point of emitting
    it instead of relying on a default.
    """
    settings = codex.settings_for_role("worker", root="/tmp/r")
    assert settings["project_doc_max_bytes"] == codex.PROJECT_DOC_MAX_BYTES
    # Headroom, not a snug fit: the rulebook grows, and a snug limit drops the
    # NEWEST rule first — the one added because something just went wrong.
    assert codex.PROJECT_DOC_MAX_BYTES >= 4 * 32768, (
        "a limit close to codex's 32 KiB default leaves the rulebook one edit "
        "away from being silently truncated again")
    # and it has to survive the writer -> parser round trip as a root scalar.
    data = tomllib.loads(codex.render(settings))
    assert data["project_doc_max_bytes"] == codex.PROJECT_DOC_MAX_BYTES


def test_the_CLAUDE_md_fallback_is_NOT_emitted_because_it_cannot_fire():
    """`project_doc_fallback_filenames = ["CLAUDE.md"]` works — with AGENTS.md
    absent, codex reads CLAUDE.md in full — but it is consulted ONLY when
    AGENTS.md is ABSENT. MEASURED: a stale AGENTS.md beside a configured
    fallback wins outright and CLAUDE.md is never read.

    So it cannot be what keeps codex and claude on one rulebook, and emitting it
    anyway would be decoration: inert while the AGENTS.md -> CLAUDE.md symlink is
    intact, useless the moment `bd init` regenerates a real AGENTS.md over it.
    Pinned so nobody adds it later as belt-and-braces and reads the fleet as
    covered twice when it is covered once."""
    settings = codex.settings_for_role("worker", root="/tmp/r")
    for key in codex.FALLBACK_NOT_EMITTED:
        assert key not in settings, (
            f"{key} is consulted only when AGENTS.md is absent — see "
            f"codex.FALLBACK_NOT_EMITTED before adding it")


def test_an_operators_snug_doc_limit_does_NOT_survive_a_re_emission():
    """Same contract as a stale stop direction: st owns what it emits. An
    operator who pinned the old 32 KiB default — or who never set one and picked
    up codex's — gets the fleet's value back on `roles set`, because a truncated
    rulebook is not a preference we honour."""
    existing = "project_doc_max_bytes = 32768\n"
    data = tomllib.loads(codex.render(
        codex.settings_for_role("worker", root="/tmp/r"), existing))
    assert data["project_doc_max_bytes"] == codex.PROJECT_DOC_MAX_BYTES


def test_the_settings_env_vars_are_DERIVED_from_the_registry():
    """A process reader must not know which program it is looking at. The
    previous one tested for the literal `--settings`, which is Claude Code's
    spelling, so it did not check the fleet — it checked the half of the fleet
    running that program. Derived, so a third harness with an env-borne pointer
    is covered by declaring it on itself rather than by a third special case."""
    assert harness_mod.settings_env_vars() == (codex.HOME_VAR,)
    assert CLAUDE.settings_env_var is None      # a flag, nothing to recover
    assert CODEX.settings_env_var == codex.HOME_VAR


def _spawn_with_env(**extra):
    """A live process whose EXEC has completed, so /proc/<pid>/environ is its own.

    Popen returns as soon as the fork is under way; until exec lands, the child's
    /proc still shows the PARENT's environment. Waiting is therefore part of
    setting the fixture up, not part of the assertion — and it waits on argv
    (exec completed), never on the environ this is about to measure, so the wait
    cannot make the test pass by itself.
    """
    import os
    import subprocess as sp
    import time
    proc = sp.Popen(["sleep", "30"], env={**os.environ, **extra})
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with open(f"/proc/{proc.pid}/cmdline", "rb") as fh:
                if b"sleep" in fh.read():
                    return proc
        except OSError:
            pass
        time.sleep(0.01)
    proc.kill(); proc.wait()
    raise AssertionError("child never exec'd — fixture failed, not the code")


def test_a_codex_agents_ENVIRON_is_folded_back_into_the_launch_line(tmp_path):
    """THE FALSE POSITIVE, at its source. `CODEX_HOME=<dir> codex …` is a shell
    assignment: the shell eats it and the child's argv never contains it. So
    `ps` shows a wired codex agent as `node …/codex --model …` with no pointer.

    Measured against a REAL process rather than a mocked string, because the
    whole defect was believing a launch line we had reconstructed wrongly."""
    from shantytown.tmux import Tmux
    home = tmp_path / "codexhome"
    home.mkdir()
    proc = _spawn_with_env(**{codex.HOME_VAR: str(home)})
    try:
        argv = "node /somewhere/codex --model gpt-5.6-terra"
        line = Tmux._launch_line(str(proc.pid), argv)
        assert line.startswith(f"{codex.HOME_VAR}={home} "), line
        # and the reconstructed line now answers the question argv could not.
        assert CODEX.settings_in_cmdline(line) == str(home / codex.CONFIG_FILE)
    finally:
        proc.kill()
        proc.wait()


def test_only_the_settings_vars_are_recovered_never_the_whole_environ(tmp_path):
    """A codex home sits beside its auth.json, and this string is printed in
    operator-facing findings. Recovering the whole environment to fix a display
    bug would put secrets in `st crew` output."""
    from shantytown.tmux import Tmux
    proc = _spawn_with_env(**{codex.HOME_VAR: str(tmp_path),
                              "AWS_SECRET_ACCESS_KEY": "hunter2"})
    try:
        line = Tmux._launch_line(str(proc.pid), "node /somewhere/codex")
        assert "hunter2" not in line
        assert "AWS_SECRET_ACCESS_KEY" not in line
    finally:
        proc.kill()
        proc.wait()


def test_a_healthy_codex_lead_is_NOT_reported_as_a_hookless_zombie(tmp_path):
    """DIRECTION ONE of the acceptance. A codex lead whose config carries send
    AND drain must read as wired — this is the alarm that fired verbatim on
    dearing while its config carried both and its process pointed at that exact
    file."""
    home = tmp_path / "codex" / "lead"
    home.mkdir(parents=True)
    (home / codex.CONFIG_FILE).write_text(
        codex.render(codex.settings_for_role("lead", root=str(tmp_path))))
    launch = f"{codex.HOME_VAR}={home} node /somewhere/codex --model gpt-5.6-terra"
    wiring = live_wiring("pane", lambda _p: launch)
    assert wiring is not None
    assert wiring.directions == {"send", "drain"}, (
        "a codex lead carrying both stop directions was read as carrying none")
    assert wiring.settings_path == str(home / codex.CONFIG_FILE)


def test_a_codex_pane_that_dropped_to_bash_STILL_reports_the_zombie():
    """DIRECTION TWO, and the reason one direction alone made this defect
    survivable: the same warning was a TRUE positive within ten minutes of being
    a false one, and was dismissed. A launch line with no pointer anywhere is an
    EMPTY SET (a measurement), never None (a failure to measure)."""
    wiring = live_wiring("pane", lambda _p: "node /somewhere/codex --model x")
    assert wiring is not None, "no pointer is a finding, not a failure to look"
    assert wiring.directions == set()
    assert wiring.settings_path is None


# Captured VERBATIM off a live codex pane on this host with `tmux capture-pane
# -p`, 2026-08-06. Kept as fixtures rather than paraphrased because the whole
# value of a marker is that it matches what the program actually draws — a
# paraphrase would test the paraphrase.
CODEX_MODEL_PICKER = """\
  Select Model and Effort
  Access legacy models by running codex -m <model_name> or in your config.toml

› 1. gpt-5.6-sol (current)  Latest frontier agentic coding model.
  2. gpt-5.6-terra          Balanced agentic coding model for everyday work.
  3. gpt-5.6-luna           Fast and affordable agentic coding model.

  Press enter to confirm or esc to go back
"""

CODEX_TRUST_DIALOG = """\
> You are in /home/x/ws

  Do you trust the contents of this directory? Working with untrusted contents
  comes with higher risk of prompt injection.

› 1. Yes, continue
  2. No, quit

  Press enter to continue
"""

# The healthy case, also verbatim — a codex agent idling with its input box up.
CODEX_IDLE = """\
• No actionable request was included.

› Summarize recent commits

  gpt-5.6-terra default · /home/x/ws
"""


@pytest.mark.parametrize("screen,what", [
    (CODEX_MODEL_PICKER, "the /model picker"),
    (CODEX_TRUST_DIALOG, "the directory-trust dialog"),
])
def test_a_codex_agent_on_a_BLOCKING_PICKER_is_seen(screen, what):
    """The picker is the state the crew rules forbid an agent to sit in, and a
    codex agent could sit in it invisibly: the pane predicates run Claude Code's
    marker strings against every card, and codex's pickers share none of that
    text — so `st crew` said `?` and `st input` said NO-BOX. `?` is honest and
    unactionable; it does not tell a coordinator an agent is stalled on a
    question only a person can answer.

    This is the defect that ended dearing's session, and it is ours, not
    codex's."""
    rt = ClaudeRuntime(NullPanes(), lambda _c: None)
    assert rt.awaiting_answer(screen) is True, f"{what} was not seen"


def test_an_IDLE_codex_agent_is_NOT_called_blocked():
    """The other direction, and the one that decides whether the verdict stays
    worth reading. A detector that fires on healthy agents trains a coordinator
    to scroll past it, and then the real one is missed too — the same way the
    zombie warning went from alarming to ignorable."""
    rt = ClaudeRuntime(NullPanes(), lambda _c: None)
    assert rt.awaiting_answer(CODEX_IDLE) is False


def test_codex_picker_markers_are_matched_TAIL_ONLY():
    """These strings are ordinary English — this very test file contains them —
    so a whole-screen match would report any agent DISCUSSING a picker as sitting
    on one. Every text predicate here is tail-only for that reason, one of them
    after a healthy agent was classified wedged for printing a traceback."""
    rt = ClaudeRuntime(NullPanes(), lambda _c: None)
    buried = CODEX_MODEL_PICKER + "\n".join(f"line {i}" for i in range(20))
    assert rt.awaiting_answer(buried) is False, (
        "a picker scrolled far up the pane is history, not a live block")


def test_trailing_blank_padding_does_not_hide_a_codex_picker():
    """codex draws its picker high and leaves the rest of the pane blank —
    measured, the capture came back with ~20 empty lines under it. A fixed window
    off the raw bottom would miss every one of them, which is how an earlier
    padding case slipped through on a claude pane."""
    rt = ClaudeRuntime(NullPanes(), lambda _c: None)
    assert rt.awaiting_answer(CODEX_MODEL_PICKER + "\n" * 25) is True


def test_the_markers_are_DERIVED_from_the_registry_not_listed_in_the_predicate():
    """Same shape as settings_env_vars: a program is covered by declaring its own
    measured chrome, not by editing the predicate. An empty tuple is a legitimate
    value meaning NOBODY HAS WATCHED ONE — never "this program has no pickers"."""
    assert set(CODEX.picker_markers) <= set(harness_mod.picker_markers())
    assert CLAUDE.picker_markers == ()      # claude's live on ClaudeRuntime


def test_the_operator_keeps_everything_st_did_not_emit():
    """Same merge rule as Claude Code's (harness.merge_one_level), one format
    over — including `[hooks.state]`, which is codex's own trust ledger and
    emphatically not ours to rewrite."""
    existing = ('model = "gpt-5-codex"\n\n[shell_environment_policy]\n'
                'inherit = "all"\n\n[hooks.state]\nmine = "kept"\n')
    text = codex.render(codex.settings_for_role("worker", root="/tmp/r"), existing)
    data = tomllib.loads(text)
    assert data["model"] == "gpt-5-codex"
    assert data["shell_environment_policy"] == {"inherit": "all"}
    assert data["hooks"]["state"] == {"mine": "kept"}
    assert codex.stop_directions(text) == {"send"}       # ours still landed


def test_a_stale_stop_direction_does_NOT_survive_a_re_emission():
    """The other half of the merge rule, and the reason it is not a plain deep
    merge: st OWNS the events it emits. A lead demoted to worker must not keep
    draining."""
    was_lead = codex.render(codex.settings_for_role("lead", root="/tmp/r"))
    now_worker = codex.render(codex.settings_for_role("worker", root="/tmp/r"), was_lead)
    assert codex.stop_directions(now_worker) == {"send"}


def test_an_unreadable_artifact_is_CANNOT_TELL_never_no_hooks():
    """None is not an empty set — the contract every harness's reader owes. A
    file we could not parse is not a file with no hooks, and rendering that as a
    pass is the defect the whole readback exists to catch."""
    assert codex.stop_directions("this is not toml {{{") is None
    assert codex.stop_directions('model = "x"') is None          # no hooks at all
    assert codex.stop_directions("") is None


def test_neither_harness_claims_the_others_file():
    """What makes the sniffing readers (stop_directions_in,
    settings_path_in_cmdline) safe: each reader is format-anchored and answers
    None rather than guessing, so first-match is a decision, not a coin toss."""
    from shantytown.runtime import claude_settings_for_role
    claude_text = json.dumps(claude_settings_for_role("lead", root="/tmp/r"))
    codex_text = codex.render(codex.settings_for_role("lead", root="/tmp/r"))
    assert CODEX.read_stop_directions(claude_text) is None
    assert CLAUDE.read_stop_directions(codex_text) is None
    assert CLAUDE.settings_in_cmdline("CODEX_HOME=/s/c/w codex") is None
    assert CODEX.settings_in_cmdline("claude --settings /s/w.settings.json") is None


# --- provisioning: the file alone is not enough --------------------------------

def test_the_operators_codex_login_is_LINKED_into_the_home_we_wrote(tmp_path,
                                                                    monkeypatch):
    """CODEX_HOME holds auth.json as well as config.toml, so pointing an agent at
    a home the store owns points it at a home with no login — an agent that
    starts, looks live, and cannot call a model. A SYMLINK, never a copy: the
    token stays in one place, `codex login` refreshes the whole fleet at once,
    and the store (a git repo in every deployment we know of) never holds a
    credential."""
    real = tmp_path / "dot-codex"
    real.mkdir()
    (real / "auth.json").write_text('{"token": "secret"}')
    monkeypatch.setenv("CODEX_HOME", str(real))
    root = tmp_path / ".shanty"
    [path] = cli._emit_role_settings(root, {"worker"}, harness_name="codex")
    assert CODEX.provision(str(path), root=root) == []
    link = path.parent / "auth.json"
    assert link.is_symlink() and link.readlink() == real / "auth.json"
    assert not link.is_file() or link.read_text() == '{"token": "secret"}'


def test_a_missing_login_is_SAID_not_silently_survived(tmp_path, monkeypatch):
    """The failure this exists to prevent is invisible at launch time, so the
    emitter has to be the one that says it."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nowhere"))
    root = tmp_path / ".shanty"
    [path] = cli._emit_role_settings(root, {"worker"}, harness_name="codex")
    notes = CODEX.provision(str(path), root=root)
    assert notes and "UNAUTHENTICATED" in notes[0]


# --- end to end: every place Claude Code used to be hardcoded ------------------

def test_emit_resolve_compose_and_read_the_routing_back(tmp_path):
    """THE ONE THAT MATTERS. A codex card, from `role set`'s emission to a
    composed launch to the stop directions read back off that launch's command
    line. It crosses the emitter (name + format), the resolver (which file), the
    compose invariant (how the launch points at it), the cmdline reader (finding
    it on a running process) and the artifact reader (parsing it) — all five of
    which were Claude Code wearing a generic name before codex existed.
    """
    root = tmp_path / ".shanty"
    [path] = cli._emit_role_settings(root, {"lead"}, harness_name="codex")
    assert path == root / "settings" / "codex" / "lead" / "config.toml"

    card = Agent(name="ada", role="lead", harness="codex", workspace="/w")
    rt = ClaudeRuntime(NullPanes(), cli._default_settings(root), root=root)
    launch = rt.compose(card)                       # resolver + gate + invariant
    assert "codex --dangerously-bypass-hook-trust" in launch

    # what a `ps` on the live pane would show, read back through the generic
    # readers — neither of which is told which harness it is looking at.
    assert settings_path_in_cmdline(launch) == str(path.resolve())
    assert stop_directions_in(path) == {"send", "drain"}
    wiring = live_wiring("pane", lambda _p: launch)
    assert wiring.directions == {"send", "drain"}
    assert wiring.settings_path == str(path.resolve())


def test_role_set_emits_the_artifact_the_CARD_will_actually_read(tmp_path, capsys):
    """MEASURED WHILE WIRING CODEX UP, on a live store: `st roles set` on a codex
    card wrote Claude Code's `worker.settings.json` and nothing the agent reads.

    The cause was one field. tier.plan_role_set builds FRESH Agents for its
    writes and dropped `harness`; files.set() re-merged it from disk, so the
    persisted card was right and every in-memory reader of the plan was wrong —
    the emitter here, and the capability gate, which asks harness.for_card of a
    card whose harness it just dropped."""
    root = tmp_path / ".shanty"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "ada.json").write_text(json.dumps(
        {"role": "worker", "harness": "codex", "pane": "crew-ada"}))
    assert cli.main(["--root", str(root), "roles", "set", "ada", "worker"]) == cli.OK
    assert (root / "settings" / "codex" / "worker" / "config.toml").is_file()
    assert not (root / "settings" / "worker.settings.json").exists(), \
        "emitted Claude Code's artifact for a codex card"
    # and the card kept its program (the #9 bug, one field over)
    assert FilesRegistry(root / "crew").get("ada").harness == "codex"


def test_a_store_can_run_both_programs_at_once(tmp_path):
    """Two harnesses, same role, two artifacts, neither overwriting the other.
    Settings were per-ROLE; which artifact a card reads is decided by the PROGRAM
    it runs, so `worker` on codex and `worker` on claude are two files."""
    root = tmp_path / ".shanty"
    [claude_path] = cli._emit_role_settings(root, {"worker"})
    [codex_path] = cli._emit_role_settings(root, {"worker"}, harness_name="codex")
    assert claude_path != codex_path
    assert json.loads(claude_path.read_text())["hooks"]["Stop"]
    assert tomllib.loads(codex_path.read_text())["hooks"]["Stop"]
    resolve = cli._default_settings(root)
    assert resolve(Agent(name="ellie", role="worker")) == str(claude_path)
    assert resolve(Agent(name="ada", role="worker", harness="codex")) == str(codex_path)


def test_the_claude_artifact_is_byte_identical_to_what_it_always_was(tmp_path):
    """The refactor half. Moving the merge and the serialization onto the harness
    must not move one byte of the file nine live agents' hooks are wired from."""
    from shantytown.runtime import settings_for_role
    root = tmp_path / ".shanty"
    [path] = cli._emit_role_settings(root, {"lead"})
    assert path.read_text() == json.dumps(
        settings_for_role("lead", root=root), indent=2, sort_keys=True)
