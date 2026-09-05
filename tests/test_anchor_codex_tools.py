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


def _card(harness, mcp_preapproved=()):
    return Anchoring(
        me=Agent(name="malcolm", role="worker", reports_to="wu", pane="p"),
        item=None, lead=None, lead_up=None, context=[], knowledge=[],
        admin="sattler", harness=harness, mcp_preapproved=mcp_preapproved,
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


# --- The card must describe the DEPLOYMENT's world, not the pre-fix one -------
#
# aegis-n549ii. The card above was written when nothing was pre-approved, and it
# said flatly that MCP WRITE tools are refused. Then aegis-h3zyq0 landed
# `default_tools_approval_mode = "approve"` on the deployment's servers and the
# card was not updated — so for months a codex worker was told it lacked tools
# it demonstrably had. A card that is confidently WRONG about tooling is the
# exact failure this file exists to prevent, and it had grown one.


def test_a_preapproved_deployment_names_its_servers():
    """"Some servers are pre-approved" sends the reader looking for which."""
    out = _card("codex", ("homelab", "bobbin")).render()
    assert "Pre-approved" in out
    assert "homelab" in out and "bobbin" in out


def test_a_preapproved_card_does_not_claim_writes_are_refused():
    """The regression that motivated this: the flat claim must be gone when the
    deployment has pre-approved servers, or the card contradicts the config."""
    out = _card("codex", ("homelab",)).render()
    assert "MCP WRITE tools are refused" not in out


def test_with_nothing_preapproved_the_card_states_the_annotation_rule():
    """A deployment that never declared anything still needs the model: an
    UNANNOTATED tool counts as destructive, which is the whole h3zyq0 cause."""
    out = _card("codex").render()
    assert "read-only" in out
    assert "Pre-approved" not in out, "nothing was declared; claiming otherwise is false"


def test_a_narrowed_server_is_absent_from_the_preapproved_line():
    """The n549ii flip: homelab leaves the list so its annotations govern. The
    card must stop naming it, or it promises tools that are now refused."""
    out = _card("codex", ("bobbin", "forgejo", "agent")).render()
    assert "Pre-approved" in out
    assert "homelab" not in out


def test_the_tooling_model_survives_in_both_branches():
    """Whatever the deployment declared, the CLI paths and the do-not-stop
    instruction are the part that actually unblocked the agent."""
    for pre in ((), ("homelab",)):
        out = _card("codex", pre).render()
        assert "Do NOT" in out and "stop and wait" in out
        for cli in ("git", "gh", "br"):
            assert cli in out
