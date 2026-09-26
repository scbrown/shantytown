"""The SessionStart briefing's three timeouts must be ORDERED (aegis-drywac).

The briefing was emitted with a 10s parent and no child budget, so yupana opened
its 30s default, ran a ~12s `/context` past the kill, and printed NOTHING,
because the hook echoes stdout only on exit 0. Same class as the pre-edit ladder
(aegis-wiv6ih): the emitter is the one place that knows both numbers.
"""
from __future__ import annotations

import re

from shantytown import runtime


def test_the_ladder_is_ORDERED_per_call_then_total_then_parent():
    assert (
        runtime.SESSION_START_PER_CALL_SECS
        < runtime.SESSION_START_TOTAL_BUDGET_SECS
        < runtime.SESSION_START_PARENT_TIMEOUT_SECS
    )


def test_the_EMITTED_hook_carries_the_same_numbers_as_the_constants():
    """Read the emitted hook, the only artifact where parent and child are
    visible together."""
    hook = runtime._yupana_brief_cmd()
    assert hook["timeout"] == runtime.SESSION_START_PARENT_TIMEOUT_SECS
    cmd = hook["command"]
    per_call = int(re.search(r"YUPANA_PROJECTION_HTTP_TIMEOUT_SECS=(\d+)", cmd).group(1))
    total = int(re.search(r"YUPANA_PROJECTION_TOTAL_BUDGET_SECS=(\d+)", cmd).group(1))
    assert per_call == runtime.SESSION_START_PER_CALL_SECS
    assert total == runtime.SESSION_START_TOTAL_BUDGET_SECS
    assert per_call < total < hook["timeout"]


def test_the_FAIL_OPEN_CONTRACT_survives_the_env_prefix():
    """CONTROL: stdout still captured and echoed only on exit 0, so a dying
    yupana contributes nothing rather than a fragment of a briefing."""
    cmd = runtime._yupana_brief_cmd()["command"]
    assert cmd.startswith("out=$(")
    assert "yupana hook session-start) || exit 0" in cmd
    assert cmd.endswith('printf %s "$out"')


def test_every_role_gets_the_budgeted_briefing():
    """The emitted settings, per role, carry the ladder, not only the helper."""
    for role in ("worker", "lead", "administrator"):
        hooks = [h for group in runtime.settings_for_role(role)["hooks"]["SessionStart"]
                 for h in group["hooks"] if "yupana hook session-start" in h["command"]]
        assert hooks, role
        for h in hooks:
            assert h["timeout"] == runtime.SESSION_START_PARENT_TIMEOUT_SECS, role
            assert f"YUPANA_PROJECTION_TOTAL_BUDGET_SECS={runtime.SESSION_START_TOTAL_BUDGET_SECS}" in h["command"], role


def test_an_INCOHERENT_ladder_cannot_even_be_IMPORTED():
    text = open(runtime.__file__, encoding="utf8").read()
    assert "SESSION_START_PER_CALL_SECS\n    < SESSION_START_TOTAL_BUDGET_SECS" in text, (
        "the ordering must be asserted at import in runtime.py")
