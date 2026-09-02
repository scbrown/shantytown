"""A Stop hook's STDOUT is a protocol channel, not a place to print progress.

aegis-6ab8hd. codex parses a Stop hook's stdout as JSON: empty is fine, valid
JSON is a decision, and ANYTHING ELSE is a failed hook —

    • Stop hook (failed)  error: hook returned invalid stop hook JSON output

MEASURED, codex-cli 0.152.1, scripts/probe-codex-stop-stdout.sh, two arms:

    all hooks silent (0 bytes)      -> "hook: Stop Completed"
    one hook printing plain text    -> "hook: Stop Failed"

so it is NOT that codex rejects empty stdout. That was the first hypothesis and
it is the expensive one, because it accuses `shantytown.stop_event` — which
correctly writes zero bytes on the ordinary path — and would send the next
reader rewriting the one component that is behaving.

WHAT MAKES THIS WORTH A TEST RATHER THAN A COMMENT. The failure is invisible
everywhere anyone looks: the hook exits 0, hook.log records rc=0, the capture
really happened, the stop event really persisted, and the agent really stopped.
The only symptom is one red line in a pane — and it is buried whenever a later
hook in the group returns a block decision, so it only becomes visible on a stop
with an EMPTY HAUL, when nothing follows it to push it off screen. It ran that
way for ~16h. A shell script cannot be trusted to keep quiet by convention;
every script here prints a summary because printing a summary is right when a
human runs it by hand.

So this pins the CONTRACT, not the fix: any command st wires into a Stop hook
must write either nothing or one valid JSON object to stdout.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "scripts" / "st-history-stop-hook.sh"


def _assert_stdout_is_a_valid_stop_payload(out: bytes, who: str) -> None:
    """Empty, or exactly one JSON object. Anything else fails a codex Stop."""
    if out.strip() == b"":
        return
    try:
        payload = json.loads(out)
    except ValueError:                      # noqa: PERF203 — the message is the point
        pytest.fail(
            f"{who} wrote NON-JSON to stdout, which codex rejects as an invalid "
            f"Stop hook payload (aegis-6ab8hd). Send diagnostics to stderr "
            f"(`>&2`). First 200 bytes:\n{out[:200]!r}")
    assert isinstance(payload, dict), f"{who} stdout must be a JSON object"


@pytest.mark.skipif(not HOOK.exists(), reason="hook script absent")
def test_history_stop_hook_writes_nothing_to_stdout(tmp_path):
    """The regression itself: its two child scripts print human summaries, and
    the hook must not let those reach stdout."""
    env = dict(os.environ, SHANTY_AGENT="nobody-such-agent",
               ST_HISTORY_DIR=str(tmp_path / "history"))
    r = subprocess.run([str(HOOK)], capture_output=True, env=env, timeout=180)
    _assert_stdout_is_a_valid_stop_payload(r.stdout, "st-history-stop-hook.sh")


def test_stop_event_send_and_haul_write_nothing_on_the_ordinary_path(tmp_path):
    """The component the first hypothesis accused, pinned as INNOCENT.

    Both exit 0 with zero bytes when there is nothing to say — `send` always
    (it reports on stderr), `haul` whenever the queue is empty. If a future
    change makes either chatty, codex stops break and nothing else notices.
    """
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ian.json").write_text(json.dumps(
        {"role": "worker", "pane": "shanty-ian", "reports_to": "dearing"}))
    (crew / "dearing.json").write_text(json.dumps(
        {"role": "lead", "pane": "shanty-dearing"}))
    (tmp_path / "events").mkdir()
    env = dict(os.environ, SHANTY_AGENT="ian")
    env.pop("TMUX_PANE", None)      # else _stop_identity refuses on a foreign pane
    for mode in ("send", "haul"):
        r = subprocess.run(
            [sys.executable, "-m", "shantytown.stop_event", mode, "--root", str(tmp_path)],
            capture_output=True, env=env, cwd=str(REPO), timeout=60)
        assert r.returncode == 0, f"{mode} exited {r.returncode}: {r.stderr[:300]!r}"
        _assert_stdout_is_a_valid_stop_payload(r.stdout, f"stop_event {mode}")


def test_the_assertion_itself_rejects_plain_text():
    """A guard nobody has watched fail is not a guard (the control).

    Without this, a check that silently passed on everything would read exactly
    like the two tests above passing — which is how the probe that FOUND this bug
    first reported a false negative: it grepped for the TUI's wording while
    running `codex exec`, whose verdict line says only "hook: Stop Failed".
    """
    with pytest.raises(BaseException):
        _assert_stdout_is_a_valid_stop_payload(
            b"21:51:37Z captured=1 skipped_unchanged=18 agent=ian\n", "control")
