"""A codex worker must be told its MCP writes are POLICY, not a lost permission.

Measured on a worker's pane 2026-09-04: every MCP write returned

    Error: MCP tool call requires approval, but approval policy is never

and the agent concluded "the launcher permissions must be restored before I can
push, trigger CI, or send the review notification" — then sat IDLE on a P1 whose
fix it had ALREADY WRITTEN to a file. Nothing was broken. It had git, gh and br
the entire time.

The refusal names its own cause, and was still misread, because the reader had no
model of what `approval_policy = never` covers. So the model goes on the card the
agent reads at the start of every session, not in a bead nobody opens mid-task.

CODEX ONLY, and an unknown harness says NOTHING. Telling a claude agent its MCP
writes are refused would be false, and a card that is confidently wrong about
tooling is worse than a card that is silent (aegis-h3zyq0).
"""
from shantytown.anchor import Anchoring
from shantytown.protocols import Agent


def _card(harness):
    return Anchoring(
        me=Agent(name="malcolm", role="worker", reports_to="wu", pane="p"),
        item=None, lead=None, lead_up=None, context=[], knowledge=[],
        admin="sattler", harness=harness,
    )


def test_a_codex_card_carries_the_tooling_model():
    out = _card("codex").render()
    assert "YOUR TOOLS (codex)" in out
    assert "approval policy is never" in out, "the card must quote the refusal it explains"
    assert "not a" in out and "lost permission" in out
    for cli in ("git", "gh", "br"):
        assert cli in out, f"the card must name {cli} as a working path"


def test_it_says_do_not_stop_and_wait():
    """The failure was not confusion, it was STOPPING. The correction has to
    address the action, not only the belief."""
    out = _card("codex").render()
    assert "Do NOT" in out and "stop and wait" in out


def test_a_claude_card_says_none_of_it():
    out = _card("claude").render()
    assert "YOUR TOOLS" not in out
    assert "approval policy" not in out


def test_an_UNKNOWN_harness_says_nothing_rather_than_guessing():
    """An empty harness is "we could not tell", and the resolver falls back to it
    on any error. Guessing there would put false tooling advice on some future
    harness's card."""
    assert "YOUR TOOLS" not in _card("").render()
