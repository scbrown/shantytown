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


def _deployment_env(root: Path, **env: str) -> None:
    lines = ["[env]", *(f'{key} = "{value}"' for key, value in env.items())]
    (root / "shantytown.toml").write_text("\n".join(lines) + "\n")


def _reader(root: Path):
    # The reader is asked about the CARD, not the role name: which artifact
    # answers depends on the program the card runs (roles._hooks_verdict).
    from shantytown import harness
    return lambda card: emitted_stop_directions(root, card.role,
                                                harness.name_for(card))


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
    always_ok = lambda card: {"send", "drain"}
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
    agents = FilesRegistry(c).all().exact()
    dearing = next(a for a in agents if a.name == "dearing")

    # honest reader, send-only lead
    reader = lambda card: {"send"} if card.role == "lead" else {"send", "drain"}
    hv, note = roles._hooks_verdict(dearing, agents, reader)
    assert hv == roles.BROKEN                      # graph-derived: lead must drain

    # Same file, same reader — but with nobody in the graph reporting to dearing,
    # drain is not required and the identical settings pass.
    hv2, _ = roles._hooks_verdict(dearing, [dearing], reader)
    assert hv2 == roles.OK


# --- the deployment Bash guard extension point (aegis-if4d) -----------------

from shantytown import runtime


def test_no_bash_guard_emitted_by_default(tmp_path):
    """Shantytown ships no GUARD and hardcodes no path: absent the deployment
    config, nothing on the Bash surface can refuse anything.

    A `Bash` matcher IS emitted, and always was going to be once yupana's action
    trace was wired — but a recorder is not a guard. This asserts the invariant
    that actually matters, which is sharper than the matcher-absence it
    replaces: no command we did not configure, and the one hook present is
    yupana's record-only trace, which never denies and always exits 0.
    """
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    bash = [h for h in s["hooks"]["PreToolUse"] if h.get("matcher") == "Bash"]
    assert len(bash) == 1
    cmds = [h["command"] for h in bash[0]["hooks"]]
    assert cmds == ["yupana hook pre-bash || exit 0"], cmds


def test_metrics_capture_is_NOT_in_settings_but_carries_the_right_interpreter(tmp_path):
    # aegis-rcyd: capture is delivered via the provision consent settings (which
    # self-heal every launch), NOT --settings (emitted only on `role set`, so it
    # went stale fleet-wide). --settings must carry NO PostToolUse — a second copy
    # would double-count. But the SHARED _capture_cmd helper must bake a real
    # interpreter (able to import shantytown), never a bare 'python' (tim).
    for role in ("worker", "lead", "administrator"):
        s = runtime.claude_settings_for_role(role, root=tmp_path)
        assert "PostToolUse" not in s["hooks"], f"{role}: capture must not be in --settings"
    cmd = runtime._capture_cmd(tmp_path)["command"]
    assert "shantytown.stats capture" in cmd
    assert f"--root {tmp_path.resolve()}" in cmd
    assert not cmd.startswith("python "), "must not be a bare 'python' (not on PATH)"


def test_toml_bash_guard_is_emitted_for_every_role(tmp_path):
    _deployment_env(tmp_path,
                    SHANTY_BASH_GUARD="/usr/local/lib/guards/host-policy.sh")
    for role in ("worker", "lead", "administrator"):
        s = runtime.claude_settings_for_role(role, root=tmp_path)
        bash = [h for h in s["hooks"]["PreToolUse"] if h.get("matcher") == "Bash"]
        assert len(bash) == 1, role
        # The guard runs FIRST and is unwrapped, exactly as configured. yupana's
        # action trace rides beside it in the same group — one matcher, two
        # hooks, guard before recorder.
        assert bash[0]["hooks"][0] == {"type": "command",
                                       "command": "/usr/local/lib/guards/host-policy.sh"}
        assert any("yupana hook pre-bash" in h["command"] for h in bash[0]["hooks"]), role
        # the edit-policy hook is untouched beside it
        assert any(h.get("matcher") != "Bash" for h in s["hooks"]["PreToolUse"])


def test_ambient_env_supplies_the_guard_when_toml_lacks_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_BASH_GUARD", "/usr/local/lib/guards/ambient.sh")
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    bash = [h for h in s["hooks"]["PreToolUse"] if h.get("matcher") == "Bash"]
    assert bash and bash[0]["hooks"][0]["command"] == "/usr/local/lib/guards/ambient.sh"


# --- the deployment session-capture Stop hook extension point (aegis-x84i) --


def test_no_capture_hook_emitted_by_default(tmp_path):
    """Shantytown ships no capture hook: absent deployment config, every role's
    Stop list is exactly its own stop machinery (positive shape assert, not just
    absence — same-output-two-worlds discipline)."""
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    cmds = [h["command"] for h in s["hooks"]["Stop"][0]["hooks"]]
    assert len(cmds) == 2  # send + haul, nothing appended
    assert all("stop_event" in c for c in cmds)


def test_toml_capture_hook_is_appended_last_for_every_role(tmp_path):
    _deployment_env(tmp_path,
                    SHANTY_STOP_CAPTURE="/usr/local/lib/hooks/session-capture.sh")
    # The administrator is ONE decision; fable-5 gives the lead a third
    # checkpoint/haul decision while workers keep send + haul.
    for role, own_count in (("worker", 2), ("lead", 3), ("administrator", 1)):
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


def test_toml_capture_wins_over_ambient(tmp_path, monkeypatch):
    _deployment_env(tmp_path, SHANTY_STOP_CAPTURE="/from/toml")
    monkeypatch.setenv("SHANTY_STOP_CAPTURE", "/from/ambient")
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    assert s["hooks"]["Stop"][0]["hooks"][-1]["command"] == "/from/toml"


# --- the deployment MCP guard extension point (aegis-uy8e8) ------------------

def test_NO_matcher_covers_the_mcp_surface_without_deployment_config(tmp_path):
    """Shantytown ships no MCP policy, so absent config the surface is
    DELIBERATELY unguarded — asserted as a positive shape, not an absence, so
    this pins WHICH matchers exist rather than merely that one is missing.

    `Bash` is in the list unconditionally now: it carries yupana's record-only
    action trace, which is emitted whether or not the deployment configures a
    host guard. Attribution is not a deployment choice.
    """
    for role in ("worker", "lead", "administrator"):
        s = runtime.claude_settings_for_role(role, root=tmp_path)
        matchers = [h.get("matcher") for h in s["hooks"]["PreToolUse"]]
        assert matchers == ["Edit|Write|MultiEdit", "Bash"], f"{role}: {matchers}"


def test_toml_mcp_guard_is_emitted_for_every_role(tmp_path):
    """THE aegis-uy8e8 GAP. Measured on the live deployment: every role emitted
    exactly ['Edit|Write|MultiEdit', 'Bash'], so nothing matched mcp__* and the
    whole MCP surface — including deploy-class actions like a service restart —
    ran with no policy hook at all, on every role."""
    _deployment_env(tmp_path,
                    SHANTY_MCP_GUARD="/usr/local/lib/guards/mcp-policy.sh")
    for role in ("worker", "lead", "administrator"):
        s = runtime.claude_settings_for_role(role, root=tmp_path)
        mcp = [h for h in s["hooks"]["PreToolUse"] if h.get("matcher") == "mcp__.*"]
        assert len(mcp) == 1, f"{role}: {[h.get('matcher') for h in s['hooks']['PreToolUse']]}"
        assert mcp[0]["hooks"] == [{"type": "command",
                                    "command": "/usr/local/lib/guards/mcp-policy.sh"}]


def test_the_mcp_matcher_actually_matches_mcp_tool_names(tmp_path):
    """The matcher is the whole mechanism, so it is tested AS a regex against
    real tool names rather than compared as a string. `Bash(rm -rf /*)`-style
    matchers looked precise, were permissions syntax, and fired ZERO times
    (aegis-ac5x/18e0) — a matcher nobody evaluated is the failure being avoided."""
    import re
    _deployment_env(tmp_path, SHANTY_MCP_GUARD="/g.sh")
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    pat = next(h["matcher"] for h in s["hooks"]["PreToolUse"]
               if h.get("matcher", "").startswith("mcp__"))
    for name in ("mcp__homelab__service_restart", "mcp__homelab__container_logs",
                 "mcp__bobbin__search", "mcp__homelab__ntfy_publish"):
        assert re.fullmatch(pat, name), f"{pat!r} did not match {name!r}"
    for name in ("Bash", "Edit", "Write", "Read", "mcp_", "notmcp__x"):
        assert not re.fullmatch(pat, name), f"{pat!r} wrongly matched {name!r}"


def test_the_bash_guard_and_the_mcp_guard_are_INDEPENDENT(tmp_path):
    """Configuring one must not emit the other. They see different payload
    shapes (a command string vs a tool name + arbitrary args), and a deployment
    may legitimately govern one surface and not the other — so a deployment that
    set only SHANTY_BASH_GUARD must not silently acquire an MCP hook pointing at
    a guard written to parse `tool_input.command`."""
    _deployment_env(tmp_path, SHANTY_BASH_GUARD="/bash.sh")
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    assert [h.get("matcher") for h in s["hooks"]["PreToolUse"]] == \
        ["Edit|Write|MultiEdit", "Bash"]
    assert not any(h.get("matcher") == "mcp__.*" for h in s["hooks"]["PreToolUse"])

    # `Bash` stays present here for yupana's trace, so independence is asserted
    # on the GUARD COMMANDS rather than on matcher presence — which is the
    # sharper claim anyway: configuring the MCP guard must not put /mcp.sh, or
    # anything else, on the Bash surface.
    _deployment_env(tmp_path, SHANTY_MCP_GUARD="/mcp.sh")
    s = runtime.claude_settings_for_role("worker", root=tmp_path)
    assert [h.get("matcher") for h in s["hooks"]["PreToolUse"]] == \
        ["Edit|Write|MultiEdit", "Bash", "mcp__.*"]
    bash_cmds = [h["command"] for g in s["hooks"]["PreToolUse"]
                 if g.get("matcher") == "Bash" for h in g["hooks"]]
    assert bash_cmds == ["yupana hook pre-bash || exit 0"], bash_cmds


# --- the transcript archiver Stop hook (aegis-xfmon3) ------------------------
#
# Emitted by SHANTYTOWN, not by the deployment: `SHANTY_STOP_CAPTURE` is a
# single-valued slot this deployment already spends on its quipu session-capture
# dispatcher, and `st history` is a shantytown command whose scripts ship here.

def _with_checkout(monkeypatch, tmp_path, script=True):
    """Point canonical_source at a tmp checkout, optionally containing the
    capture script. Overrides conftest's _no_ambient_checkout, which pins
    resolution OFF so no test inherits the runner's filesystem."""
    if script:
        d = tmp_path / "co" / "scripts"
        d.mkdir(parents=True)
        (d / "st-history-stop-hook.sh").write_text("#!/bin/sh\n")
    else:
        (tmp_path / "co").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime, "canonical_source",
                        lambda *a, **k: str(tmp_path / "co"))
    return tmp_path / "co" / "scripts" / "st-history-stop-hook.sh"


def test_archiver_runs_before_anything_that_can_block(tmp_path, monkeypatch):
    """ORDERING IS THE WHOLE TEST. Codex stops after the first BLOCKING Stop
    hook, and haul/drain/policy all block — so an archiver appended last (the
    obvious slot, where the deployment capture hook goes) would never run on
    codex at all. Codex is the half of the fleet whose transcripts live on tmpfs
    and are the reason this epic exists.

    Asserted as an INDEX comparison, not "is present": presence is what a
    last-appended hook would also satisfy.
    """
    script = _with_checkout(monkeypatch, tmp_path)
    for role, blockers in (("worker", ("haul",)),
                           ("lead", ("haul", "drain")),
                           ("administrator", ("stop_policy",))):
        cmds = [h["command"] for h in runtime.role_stop_hooks(role, root=tmp_path)]
        assert str(script) in cmds, role
        i = cmds.index(str(script))
        for b in blockers:
            assert i < next(j for j, c in enumerate(cmds) if b in c), (role, b)


def test_archiver_still_sits_behind_send(tmp_path, monkeypatch):
    """Send-first persistence is what makes a stop legible, and `send` does not
    block — so the archiver goes after it, never in front of it. The
    administrator has no send and _policy_cmd blocks, so there the archiver is
    first; its transcripts are no less lost to a reboot than anyone else's."""
    script = _with_checkout(monkeypatch, tmp_path)
    for role in ("worker", "lead"):
        cmds = [h["command"] for h in runtime.role_stop_hooks(role, root=tmp_path)]
        assert "stop_event send" in cmds[0], role
        assert cmds[1] == str(script), role
    admin = [h["command"] for h in runtime.role_stop_hooks("administrator",
                                                           root=tmp_path)]
    assert admin[0] == str(script)


def test_no_checkout_emits_no_archiver_and_leaves_the_role_untouched(tmp_path,
                                                                     monkeypatch):
    """CANNOT-TELL is not a licence to guess a path. A Stop hook that dies on a
    bad path is indistinguishable from a tier with no capture — which is the
    exact failure this epic exists to fix, so it must not be the failure the fix
    introduces. Positive shape assert, not just absence."""
    monkeypatch.setattr(runtime, "canonical_source", lambda *a, **k: None)
    cmds = [h["command"] for h in runtime.role_stop_hooks("worker", root=tmp_path)]
    assert len(cmds) == 2 and all("stop_event" in c for c in cmds)


def test_a_checkout_without_the_script_emits_no_archiver(tmp_path, monkeypatch):
    """Resolving a checkout is not the same as that checkout carrying the
    script — an older deploy resolves fine and has no archiver in it."""
    _with_checkout(monkeypatch, tmp_path, script=False)
    cmds = [h["command"] for h in runtime.role_stop_hooks("worker", root=tmp_path)]
    assert len(cmds) == 2 and all("stop_event" in c for c in cmds)


def test_archiver_bakes_in_no_agent_name(tmp_path, monkeypatch):
    """Identity is resolved AT RUN TIME from $SHANTY_AGENT, because settings are
    emitted per ROLE and there is no per-agent file to bake a name into. An
    earlier proposal on the bead assumed otherwise; this is the assertion that
    keeps the assumption from coming back."""
    script = _with_checkout(monkeypatch, tmp_path)
    hook = [h for h in runtime.role_stop_hooks("lead", root=tmp_path)
            if h["command"] == str(script)]
    assert len(hook) == 1
    assert hook[0]["command"] == str(script)      # no --agent, no name
    assert hook[0]["timeout"] == 30


def test_archiver_coexists_with_the_deployment_capture_hook(tmp_path, monkeypatch):
    """The two are different mechanisms in different slots, and neither may
    displace the other: SHANTY_STOP_CAPTURE stays LAST, the archiver stays ahead
    of the blockers."""
    _deployment_env(tmp_path, SHANTY_STOP_CAPTURE="/usr/local/lib/hooks/cap.sh")
    script = _with_checkout(monkeypatch, tmp_path)
    cmds = [h["command"] for h in runtime.role_stop_hooks("worker", root=tmp_path)]
    assert cmds[-1] == "/usr/local/lib/hooks/cap.sh"
    assert cmds.index(str(script)) < cmds.index("/usr/local/lib/hooks/cap.sh")
