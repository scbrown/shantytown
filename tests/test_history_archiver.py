"""The transcript archiver's scripts (aegis-xfmon3).

WHAT THESE PROTECT, in one line each, because none of it is obvious from
reading the scripts:

  * an EMPTY `--agent` must be REFUSED, never read as "no filter". The Stop hook
    passes "$SHANTY_AGENT"; an unset identity would silently turn a one-agent
    capture into a fleet-wide sweep, and a 0.5s scoped scrub into a 21s
    re-scrub of every agent's archive — on somebody else's turn boundary. Two
    different worlds must not produce the same behaviour.

  * the hook must NEVER exit 2. Claude Code reads 2 from a Stop hook as a
    BLOCKING error: the stop is refused and stderr goes back to the model.
    st-history-scrub.sh exits 2 on purpose and correctly — it means "credential
    -shaped strings survived into the derivative, do NOT index it" — but that is
    a finding about an ARCHIVE and must not be spelled the same way as "this
    agent may not finish its turn". A transcript archiver has no business
    holding a turn boundary open under any of its failure modes.

  * the incremental skip must still be able to say NO. A scrub that skips
    everything is indistinguishable from a scrub that cannot see anything, so
    both branches are asserted, never just the cheap one.
"""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
CAPTURE = SCRIPTS / "st-history-capture.sh"
SCRUB = SCRIPTS / "st-history-scrub.sh"
HOOK = SCRIPTS / "st-history-stop-hook.sh"

# A credential-SHAPED string, not a credential: 22 chars of 'A' behind a gitleaks
# prefix. It exists to be matched by the scrub's own pattern list.
FAKE_TOKEN = "ghp_" + "A" * 22


def _archive(tmp_path, agent="tester", body='{"t":"benign"}\n'):
    """A self-contained archive: raw + derivative + empty harness roots, so
    nothing here can reach the real one."""
    raw, out = tmp_path / "raw", tmp_path / "scrubbed"
    (raw / agent).mkdir(parents=True)
    (out).mkdir(parents=True)
    (raw / agent / "a.jsonl").write_text(body)
    for d in ("empty_codex", "empty_claude"):
        (tmp_path / d).mkdir()
    env = dict(os.environ,
               ST_HISTORY_DIR=str(raw), ST_HISTORY_SCRUBBED_DIR=str(out),
               ST_CODEX_ROOT=str(tmp_path / "empty_codex"),
               ST_CLAUDE_ROOT=str(tmp_path / "empty_claude"))
    return raw, out, env


def _run(script, *args, env=None, agent=None):
    e = dict(env or os.environ)
    if agent is None:
        e.pop("SHANTY_AGENT", None)
    else:
        e["SHANTY_AGENT"] = agent
    return subprocess.run([str(script), *args], capture_output=True, text=True,
                          env=e, timeout=120)


@pytest.mark.parametrize("script", [CAPTURE, SCRUB], ids=["capture", "scrub"])
def test_empty_agent_is_refused_not_treated_as_no_filter(script, tmp_path):
    """The silent scope explosion. `--agent ""` and no `--agent` at all are two
    different intentions and one of them is a bug."""
    _, _, env = _archive(tmp_path)
    r = _run(script, "--agent", "", env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "REFUSED" in r.stdout + r.stderr


def test_the_hook_refuses_when_identity_is_unset(tmp_path):
    _, _, env = _archive(tmp_path)
    r = _run(HOOK, env=env, agent=None)
    assert r.returncode == 1
    assert "SHANTY_AGENT" in r.stderr


def test_a_dirty_derivative_never_reaches_the_harness_as_a_blocking_stop(tmp_path):
    """THE LOAD-BEARING TEST. Both halves asserted: the scrub really does say 2
    (without that control the remap could be passing for the wrong reason), and
    the hook turns it into 1 — still nonzero, still loud, no longer a veto.

    The planted file has no source in the raw archive, so the scrub cannot
    overwrite it: it is a residual by construction.
    """
    raw, out, env = _archive(tmp_path)
    (out / "tester").mkdir(parents=True)
    (out / "tester" / "planted.jsonl").write_text('{"t":"%s"}\n' % FAKE_TOKEN)

    control = _run(SCRUB, "--agent", "tester", env=env)
    assert control.returncode == 2, "scrub must report a dirty derivative as 2"

    r = _run(HOOK, env=env, agent="tester")
    assert r.returncode == 1, "2 is Claude Code's BLOCKING code — never emit it"
    assert "NOT CLEAN" in r.stdout + r.stderr, "and it must still say so loudly"


def test_a_clean_run_exits_zero_and_scrubs_the_credential_shape(tmp_path):
    raw, out, env = _archive(tmp_path, body='{"t":"%s"}\n' % FAKE_TOKEN)
    r = _run(HOOK, env=env, agent="tester")
    assert r.returncode == 0, r.stdout + r.stderr
    derived = (out / "tester" / "a.jsonl").read_text()
    assert FAKE_TOKEN not in derived
    assert "[REDACTED:ghp:" in derived
    # the RAW archive is untouched — it is the evidence, and only the
    # derivative is ever indexed
    assert FAKE_TOKEN in (raw / "tester" / "a.jsonl").read_text()


def test_incremental_skip_can_still_say_no(tmp_path):
    """Both branches. A scrub that skips everything and a scrub that sees
    nothing print the same reassuring zero."""
    raw, out, env = _archive(tmp_path)
    first = _run(SCRUB, "--agent", "tester", env=env)
    assert "scrubbed 1 file(s)" in first.stdout

    again = _run(SCRUB, "--agent", "tester", env=env)
    assert "scrubbed 0 file(s)" in again.stdout and "1 already current" in again.stdout

    src = raw / "tester" / "a.jsonl"
    # Explicit future mtime, not utime(None): "now" can land inside the same
    # filesystem timestamp tick as the derivative written a moment ago, which
    # would make this assert flaky in the direction that HIDES a broken
    # incremental check.
    dst = out / "tester" / "a.jsonl"
    os.utime(src, (dst.stat().st_mtime + 10, dst.stat().st_mtime + 10))
    third = _run(SCRUB, "--agent", "tester", env=env)
    assert "scrubbed 1 file(s)" in third.stdout, "a changed source must re-scrub"

    full = _run(SCRUB, "--agent", "tester", "--full", env=env)
    assert "0 already current" in full.stdout, "--full must revisit skipped files"


def test_scoping_does_not_touch_another_agent(tmp_path):
    """A scoped run is the hook's cost model; it must also be the hook's blast
    radius."""
    raw, out, env = _archive(tmp_path)
    (raw / "other").mkdir()
    (raw / "other" / "b.jsonl").write_text('{"t":"benign"}\n')
    _run(SCRUB, "--agent", "tester", env=env)
    assert (out / "tester" / "a.jsonl").exists()
    assert not (out / "other").exists()


def test_a_scoped_clean_verdict_names_its_scope(tmp_path):
    """A scoped CLEAN says nothing about anyone else's sessions. A verdict that
    could be read as fleet-wide would be worse than no verdict."""
    _, _, env = _archive(tmp_path)
    r = _run(SCRUB, "--agent", "tester", env=env)
    assert r.returncode == 0
    assert "scope: tester only" in r.stdout


def test_an_unknown_agent_is_not_an_error_and_scrubs_nothing(tmp_path):
    """An agent with no captured sessions yet is a normal state at a stop, not a
    fault — the hook must not go nonzero for it on every turn boundary."""
    _, out, env = _archive(tmp_path)
    r = _run(SCRUB, "--agent", "nobody", env=env)
    assert r.returncode == 0
    assert not (out / "nobody").exists()


def test_the_hook_records_that_it_ran(tmp_path):
    """The run log is the ONLY thing that separates "the hook is live" from "the
    interim timer is quietly covering for it" — both leave the same archive
    behind. Retiring the timer is unsafe without it: removing the timer and
    watching captures continue would prove nothing.

    Asserted on both a clean run and a failing one, because a log that only
    records successes cannot answer "did it run" on the night it matters.
    """
    raw, out, env = _archive(tmp_path)
    _run(HOOK, env=env, agent="tester")
    log = (raw / "hook.log").read_text()
    assert "\ttester\t" in log and "rc=0" in log

    (out / "tester").mkdir(exist_ok=True)
    (out / "tester" / "planted.jsonl").write_text('{"t":"%s"}\n' % FAKE_TOKEN)
    _run(HOOK, env=env, agent="tester")
    lines = (raw / "hook.log").read_text().strip().splitlines()
    assert len(lines) == 2 and lines[-1].endswith("rc=1")


def test_an_unwritable_log_does_not_change_the_hooks_verdict(tmp_path):
    """A hook that fails BECAUSE it could not write its own audit line would be
    a stop-path fault introduced by the thing watching the stop path."""
    raw, _, env = _archive(tmp_path)
    (raw / "hook.log").mkdir()          # a directory where the log wants a file
    r = _run(HOOK, env=env, agent="tester")
    assert r.returncode == 0, r.stdout + r.stderr


# --- the timer-retirement gate (aegis-xfmon3 step 3) -------------------------

def _gate():
    """Load the gate's decision rule. It is a script, not a module, so it is
    loaded by path — the same way it runs."""
    import importlib.util
    spec = importlib.util.spec_from_loader(
        "timer_gate",
        importlib.machinery.SourceFileLoader(
            "timer_gate", str(SCRIPTS / "st-history-timer-gate.py")))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_one_agents_proof_does_not_license_removing_a_fleet_wide_timer():
    """THE POINT OF THE GATE. Settings reach a live agent only on RELAUNCH, so at
    the moment the hook is proven on the first relaunched agent every OTHER live
    agent still has no archiver at all — and the timer is their only capture.
    Removing it then un-covers the fleet silently, because the archive keeps
    growing from the agents that ARE covered."""
    g = _gate()
    rc, out = g.decide(live=["a", "b"], stale=["b"], unknown=[], fired={"a"})
    assert rc == 1
    assert "NOT SAFE" in "\n".join(out)


def test_current_settings_are_not_proof_the_hook_ever_RAN():
    """An agent can be on the current file and still never have fired it.
    Settings prove the config was LOADED; only the log proves the harness RAN
    it. This repo already draws that line for pane liveness vs hook
    registration."""
    g = _gate()
    rc, out = g.decide(live=["a"], stale=[], unknown=[], fired=set())
    assert rc == 1
    assert "hook has never fired for: a" in "\n".join(out)


def test_unknown_launch_verdict_is_never_rounded_to_safe():
    g = _gate()
    rc, _ = g.decide(live=["a"], stale=[], unknown=["a"], fired={"a"})
    assert rc == 1


def test_an_empty_fleet_is_cannot_tell_not_safe():
    """Nobody live is the least informative moment to make a permanent change,
    and it is exactly when the check looks cleanest."""
    g = _gate()
    rc, out = g.decide(live=[], stale=[], unknown=[], fired=set())
    assert rc == 2
    assert "CANNOT TELL" in out[0]


def test_safe_requires_every_live_agent_on_both_counts():
    g = _gate()
    rc, out = g.decide(live=["a", "b"], stale=[], unknown=[], fired={"a", "b"})
    assert rc == 0
    assert "SAFE" in "\n".join(out)


def test_a_missing_log_is_an_empty_set_not_an_error(tmp_path):
    """Before the first stop under the new settings, no log is the true and
    expected state — it must read as 'nobody has fired', not as a fault."""
    g = _gate()
    assert g.read_fired(tmp_path / "absent.log") == set()


def test_the_gate_answers_when_invoked_THE_WAY_A_READER_WOULD():
    """THE LANE, not the code. The gate's decision rule was tested and correct
    while `./scripts/st-history-timer-gate.py` could only ever return CANNOT
    TELL — the shebang picked the system python3, which has no shantytown. The
    only measurement taken had named a venv python explicitly, and a green
    obtained through an interpreter nobody else would type proves the code and
    says nothing about the lane a reader uses.

    So: execute it as a program, with no interpreter named, and require that it
    REACHED the fleet question.

    Asserted on the IMPORT lane, not on the verdict: "no live agents" is a
    legitimate CANNOT TELL and is what CI sees, so pinning rc in (0,1) would
    make this test a fleet-liveness test that goes red on every machine without
    a running crew. The defect being pinned is narrower and permanent — the gate
    could not load shantytown at all, so no invocation could ever produce an
    answer.
    """
    gate = SCRIPTS / "st-history-timer-gate.py"
    assert os.access(gate, os.X_OK), "the gate must be executable to be a lane"
    r = subprocess.run([str(gate)], capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert "not importable" not in out, (
        f"the gate cannot load shantytown when run as a program:\n{out}")
    assert r.returncode in (0, 1, 2) and out.strip(), out
