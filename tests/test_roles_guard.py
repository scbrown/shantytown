"""roles --check, FOURTH LEG: does the card's artifact carry the host Bash guard?

THE DEFECT (aegis-610jv). A codex agent ran with NO bd-store-guard and NO
crew-only-guard, and every surface that could have said so reported it healthy:

  * `roles --check` measured the STOP ROUTING and printed `hooks: ok`, which is
    true and answers a different question;
  * the emitter had a constant (`codex.MATCHERS_NOT_EMITTED = ("PreToolUse",)`)
    that correctly recorded the omission — in a place no operator reads;
  * and both guards were green on every CLAUDE card, so any spot check of "are
    the guards working on this host" came back yes.

What that cost: bd-store-guard is the thing between an agent and a `bd`
subcommand that opens one of the 14 exposed stores read-write and wedges it
(aegis-lmi — gastown is the crater, the one store somebody did open that way).
crew-only-guard is the thing between an agent and a `gt up` that puts a live
witness on a crew-only host (aegis-bah2). Converting a claude card to codex
dropped both, silently, which is why the codex expansion was blocked on it.

So the tests that matter here are NOT the ok-path ones. They are:

  * test_a_codex_card_with_NO_guard_is_BROKEN — the actual defect, in the shape
    it actually had: a config that is perfectly readable and carries no guard.
  * test_positive_control_*                   — the leg is DEFEATED and the
    failing tests must go green, proving they detect the leg rather than passing
    for some unrelated reason. A leg whose failure path has never run is
    indistinguishable from a column header, which is what the last one replaced.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import codex, harness, roles
from shantytown.files import FilesRegistry
from shantytown.runtime import emitted_bash_guard, settings_for_role

GUARD = "/guards/bash-guards.sh"


def _card(d: Path, name: str, **fields) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(fields))


def _emit_claude(root: Path, *rolenames: str) -> None:
    s = root / "settings"
    s.mkdir(parents=True, exist_ok=True)
    for r in rolenames:
        (s / f"{r}.settings.json").write_text(
            json.dumps(settings_for_role(r, root=root)))


def _emit_codex(root: Path, *rolenames: str) -> None:
    """The artifact `role set` writes for a CODEX role — a config.toml in a
    directory per role, which is why the guard reader has to go through the
    harness rather than guessing a filename."""
    for r in rolenames:
        d = root / "settings" / "codex" / r
        d.mkdir(parents=True, exist_ok=True)
        (d / codex.CONFIG_FILE).write_text(
            codex.render(codex.settings_for_role(r, root=root)))


def _reader(root: Path):
    # Asked about the CARD, not the role name: which artifact answers depends on
    # the program that card runs. A reader that assumed claude would open a file
    # that is not there for exactly the cards this leg exists to find.
    return lambda card: emitted_bash_guard(root, card.role,
                                           harness.name_for(card))


def _crew(root: Path, harness_name: str | None = None) -> Path:
    c = root / "crew"
    extra = {"harness": harness_name} if harness_name else {}
    _card(c, "sattler", role="administrator", **extra)
    _card(c, "dearing", role="lead", reports_to="sattler", **extra)
    _card(c, "gennaro", role="worker", reports_to="dearing", **extra)
    return c


@pytest.fixture
def guarded(monkeypatch):
    """A deployment that CONFIGURES a Bash guard. Without this the leg correctly
    declines to measure anything — see test_a_deployment_with_NO_guard_is_not_a_finding."""
    monkeypatch.setenv("SHANTY_BASH_GUARD", GUARD)


# --- the ok path (necessary, not sufficient) ---------------------------------

def test_ok_when_a_codex_role_emitted_the_guard(tmp_path: Path, guarded):
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "lead", "worker")
    rep = roles.check(FilesRegistry(c), guard=_reader(tmp_path))
    assert rep.verdict == roles.OK
    assert all(r.guard == roles.OK for r in rep.rows)
    assert "guard: ok" in rep.render()


def test_ok_for_claude_too_so_the_leg_is_not_codex_only(tmp_path: Path, guarded):
    """The leg reads through whichever harness the card declares. If it only ever
    worked for codex it would be a codex feature rather than a check, and the
    claude cards — where the guards have always been wired — would go unverified."""
    c = _crew(tmp_path)
    _emit_claude(tmp_path, "administrator", "lead", "worker")
    rep = roles.check(FilesRegistry(c), guard=_reader(tmp_path))
    assert rep.verdict == roles.OK
    assert all(r.guard == roles.OK for r in rep.rows)


# --- the defect this leg exists to catch ------------------------------------

def test_a_codex_card_with_NO_guard_is_BROKEN(tmp_path: Path, monkeypatch):
    """THE REAL ONE, in the shape it actually had. The config is present, valid
    TOML, and carries a full correct stop routing — so every other leg passes and
    the row rendered `hooks: ok`. It simply has no PreToolUse guard, which on
    this deployment means an agent that can wedge a store and start a witness.

    Emitted with NO guard configured, then checked WITH one configured: that is
    the real-world sequence, since the artifact predates the emitter fix.
    """
    monkeypatch.delenv("SHANTY_BASH_GUARD", raising=False)
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "lead", "worker")

    monkeypatch.setenv("SHANTY_BASH_GUARD", GUARD)
    rep = roles.check(FilesRegistry(c), guard=_reader(tmp_path))

    assert rep.verdict == roles.BROKEN
    row = next(r for r in rep.rows if r.agent == "dearing")
    assert row.guard == roles.BROKEN
    assert "emits NO Bash guard" in row.note
    assert "UNENFORCED" in row.note
    out = rep.render()
    assert "guard: ok" not in out, "an unguarded card must not render as guarded"


def test_a_guard_scoped_to_a_MATCHER_THAT_NEVER_FIRES_is_BROKEN(tmp_path: Path,
                                                                guarded):
    """The aegis-ac5x failure, which is the one a presence-only check would miss:
    a PreToolUse block IS there, and it is scoped to `shell` — a matcher MEASURED
    not to fire on codex (probe-codex-pretooluse.sh). The agent is unguarded and
    the config looks guarded. This is why the reader matches on the matcher and
    not on the existence of a hook."""
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "lead", "worker")
    p = tmp_path / "settings" / "codex" / "lead" / codex.CONFIG_FILE
    p.write_text(p.read_text().replace('matcher = "Bash"', 'matcher = "shell"'))

    rep = roles.check(FilesRegistry(c), guard=_reader(tmp_path))
    assert rep.verdict == roles.BROKEN
    row = next(r for r in rep.rows if r.agent == "dearing")
    assert row.guard == roles.BROKEN


def test_an_UNREADABLE_config_is_cannot_tell_never_a_pass(tmp_path: Path, guarded):
    """Three states, not two. A config we failed to parse is not a config with no
    guard — and it is emphatically not a config with one. Reporting either
    certainty here would be a verdict we did not measure."""
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "lead", "worker")
    (tmp_path / "settings" / "codex" / "lead" / codex.CONFIG_FILE).write_text("[[[ nope")

    rep = roles.check(FilesRegistry(c), guard=_reader(tmp_path))
    assert rep.verdict == roles.CANNOT_TELL
    row = next(r for r in rep.rows if r.agent == "dearing")
    assert row.guard == roles.CANNOT_TELL
    assert "could not read" in row.note


def test_a_MISSING_artifact_is_cannot_tell(tmp_path: Path, guarded):
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "worker")      # no lead config at all
    rep = roles.check(FilesRegistry(c), guard=_reader(tmp_path))
    row = next(r for r in rep.rows if r.agent == "dearing")
    assert row.guard == roles.CANNOT_TELL


# --- what is NOT a finding ---------------------------------------------------

def test_a_deployment_with_NO_guard_is_not_a_finding(tmp_path: Path, monkeypatch):
    """shantytown ships NO guard and hardcodes NO path — which commands are
    dangerous is a property of the DEPLOYMENT. A store that configures none must
    not fail its own health check for declining an optional extension point;
    that is the exists-not-acts shape this repo refuses elsewhere."""
    monkeypatch.delenv("SHANTY_BASH_GUARD", raising=False)
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "lead", "worker")
    rep = roles.check(FilesRegistry(c), guard=_reader(tmp_path))
    assert rep.verdict == roles.OK
    assert all(r.guard == roles.UNVERIFIED for r in rep.rows)
    assert "guard:" not in rep.render(), "an unconfigured guard must print nothing"


def test_omitting_the_reader_reports_UNVERIFIED_not_ok(tmp_path: Path, guarded):
    """The checker does not get to print a word it did not measure. This is the
    same contract the hooks and live legs already owe, and the reason `hooks: ok`
    as a rendered constant was the original GitHub #6 complaint."""
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "lead", "worker")
    rep = roles.check(FilesRegistry(c))              # no guard= reader
    assert all(r.guard == roles.UNVERIFIED for r in rep.rows)
    assert "guard:" not in rep.render()


def test_the_configured_guard_is_read_with_the_STORE_ROOT_not_the_ambient_env(
        tmp_path: Path, monkeypatch):
    """MEASURED BUG, found by running this leg against the live store before it
    shipped (aegis-610jv). _guard_verdict looked the configured guard up itself
    with no root, so it saw only the ambient environment and never the STORE'S
    env.json — which is where a deployment actually records its guard. Result:
    the codex lead and both codex workers, all genuinely unguarded, came back
    UNVERIFIED. The leg found the defect and then downgraded its own finding to
    silence, which is indistinguishable from not having the leg at all.

    The environment is deliberately EMPTY here, so the only way to reach BROKEN
    is for the caller-supplied value to be honoured.
    """
    monkeypatch.delenv("SHANTY_BASH_GUARD", raising=False)
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "lead", "worker")

    rep = roles.check(FilesRegistry(c), guard=_reader(tmp_path),
                      guard_configured=GUARD)
    assert rep.verdict == roles.BROKEN
    assert all(r.guard == roles.BROKEN for r in rep.rows)


def test_a_guard_that_is_ABSENT_from_config_is_not_confused_with_UNREADABLE(
        tmp_path: Path, monkeypatch):
    """`bash_guard_command` returns None for a setting that is simply absent,
    which means NOT CONFIGURED — an ordinary state. None in the verdict means
    COULD NOT TELL. Letting those share a value made every default deployment
    report as unmeasurable rather than as having no guard; caught by
    test_a_deployment_with_NO_guard_is_not_a_finding going red."""
    monkeypatch.delenv("SHANTY_BASH_GUARD", raising=False)
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "lead", "worker")

    absent = roles.check(FilesRegistry(c), guard=_reader(tmp_path),
                         guard_configured="")
    assert absent.verdict == roles.OK
    assert all(r.guard == roles.UNVERIFIED for r in absent.rows)

    unknown = roles.check(FilesRegistry(c), guard=_reader(tmp_path),
                          guard_configured=None)
    assert unknown.verdict == roles.CANNOT_TELL


# --- positive controls: prove the leg is what detects it ---------------------

def test_positive_control_defeating_the_leg_makes_the_defect_pass(tmp_path: Path,
                                                                  monkeypatch):
    """Remove the leg and the unguarded-codex-card case goes GREEN. That is the
    state the fleet was actually in, and it is the proof that the assertion above
    is detecting THIS leg rather than passing for an unrelated reason."""
    monkeypatch.delenv("SHANTY_BASH_GUARD", raising=False)
    c = _crew(tmp_path, harness_name="codex")
    _emit_codex(tmp_path, "administrator", "lead", "worker")
    monkeypatch.setenv("SHANTY_BASH_GUARD", GUARD)

    rep = roles.check(FilesRegistry(c))              # the leg, omitted
    assert rep.verdict == roles.OK, (
        "without the guard leg an entirely unguarded codex card checks out "
        "clean — which is exactly what aegis-610jv measured")


def test_positive_control_the_reader_sees_the_real_emitted_artifact(tmp_path: Path,
                                                                    guarded):
    """Not a stub: the bytes `role set` writes, parsed back off disk. A check that
    asks the WRITER what it would write proves nothing about what is on disk —
    and here the writer was the broken part, so a writer-consulting check would
    have pronounced the defect fixed while every config on the host lacked a
    guard."""
    _emit_codex(tmp_path, "worker")
    p = tmp_path / "settings" / "codex" / "worker" / codex.CONFIG_FILE
    assert GUARD in p.read_text(), "the guard is not in the bytes on disk"

    _card(tmp_path / "crew", "gennaro", role="worker", harness="codex")
    got = _reader(tmp_path)(FilesRegistry(tmp_path / "crew").all()[0])
    assert got == GUARD


def test_the_action_trace_is_NOT_reported_as_a_deployment_guard(tmp_path: Path, monkeypatch):
    """A RECORDER IS NOT A GUARD, and the reader must know the difference.

    yupana's action trace shares the `Bash` matcher with the deployment guard —
    it has to, that is the tool it records. A reader that returned the first
    command under that matcher would answer "guard: yupana hook pre-bash" for a
    store that configures no guard at all, and `roles --check` would print
    coverage for a surface where nothing can refuse anything.

    That is the false clear this file's three-state contract exists to refuse,
    and it would have been introduced by the very change that made actions
    attributable. Asserted for BOTH harnesses, because both readers parse the
    same group out of two different formats.
    """
    monkeypatch.delenv("SHANTY_BASH_GUARD", raising=False)
    from shantytown import runtime, codex as codex_mod, harness as harness_mod

    # The trace IS emitted...
    group = runtime.bash_group(root=tmp_path)
    assert any(runtime.is_trace_command(h["command"]) for h in group["hooks"])

    # ...and neither reader counts it as a guard.
    claude_settings = runtime.claude_settings_for_role("worker", root=tmp_path)
    claude = harness_mod.get("claude").read_bash_guard(json.dumps(claude_settings))
    assert claude == "", f"claude reader mistook the recorder for a guard: {claude!r}"

    codex_text = codex_mod.render(codex_mod.settings_for_role("worker", root=tmp_path))
    assert codex_mod.bash_guard(codex_text) == "", "codex reader mistook the recorder for a guard"


def test_a_real_guard_is_still_found_beside_the_trace(tmp_path: Path, monkeypatch):
    """The control. Without it the test above would pass against a reader that
    had simply stopped finding guards at all — which is the same false clear
    pointing the other way."""
    monkeypatch.setenv("SHANTY_BASH_GUARD", "/usr/local/lib/guards/host-policy.sh")
    from shantytown import runtime, codex as codex_mod, harness as harness_mod

    claude_settings = runtime.claude_settings_for_role("worker", root=tmp_path)
    assert harness_mod.get("claude").read_bash_guard(json.dumps(claude_settings)) == \
        "/usr/local/lib/guards/host-policy.sh"

    codex_text = codex_mod.render(codex_mod.settings_for_role("worker", root=tmp_path))
    assert codex_mod.bash_guard(codex_text) == "/usr/local/lib/guards/host-policy.sh"
