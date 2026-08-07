"""The pre-push guard and the ratchet must agree on what BLOCKS (aegis-krlog).

THE DEFECT. Two mechanisms in this repo enforce the same policy and they
disagreed about one pattern class, with the stricter one deciding publication:

  * tests/test_internal_identifier_ratchet.py  — BLOCK_TIER deliberately
    EXCLUDES "internal ticket id", with the comment "they are warn-tier in the
    graph rule (a bead reference leaks no topology)". Correct.
  * scripts/pre-push-scrub-guard.sh            — REFUSED a push for the same
    class. Wrong, and it is the one with the veto.

Both are meant to be projections of the policy graph, which aegis-mqnl makes the
source of truth, and the graph is unambiguous — every pattern is `block` except
one:

    pattern_internal-lan-host   block      pattern_internal-home-path  block
    pattern_internal-svc-host   block      pattern_guard-canary        block
    pattern_private-ipv4        block      pattern_internal-node-name  block
    pattern_bead-reference      WARN

So this was never a judgement call about how strict to be. It was a
MIS-PROJECTION: the guard enforced a tier the graph does not state, and the cost
was that citing a bead — this codebase's own documented convention — made a push
fail. 172 bead ids were already public in origin/main file content and 191 in its
commit messages, so the rule was not protecting a secret; it was demanding
`--no-verify` on essentially every push, which is how a guard that also catches
REAL hostnames gets reflexively bypassed on the day it is right.

WHY THIS FILE EXISTS RATHER THAN A COMMENT. A divergence between two enforcement
points is invisible from inside either one — each looked correct in its own
tests, and the guard's own selftest PINNED the wrong behaviour ("ticket in a
CHANGELOG is still refused"), so it defended the defect. Only a test that reads
BOTH can catch it, and the next re-divergence will be as quiet as this one.
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "scripts" / "pre-push-scrub-guard.sh"

# The one class the graph rates warn. Named here so a reader of a failure knows
# WHICH pattern the two mechanisms are arguing about.
WARN_TIER_CLASS = "internal ticket id"


def test_the_ratchet_keeps_bead_ids_out_of_its_block_tier():
    """The ratchet's half of the agreement, asserted directly rather than
    assumed — this is the mechanism that was already right, and a later edit
    'tightening' it would re-open the divergence from the other side."""
    from tests import test_internal_identifier_ratchet as ratchet
    assert WARN_TIER_CLASS in ratchet.FORBIDDEN, (
        "the ratchet no longer knows about bead ids at all — it cannot agree "
        "or disagree, which is worse than disagreeing")
    assert WARN_TIER_CLASS not in ratchet.BLOCK_TIER, (
        "the ratchet moved bead ids into BLOCK_TIER. The graph rates "
        "pattern_bead-reference `warn`; blocking here re-creates aegis-krlog "
        "from the ratchet's side.")


@pytest.mark.skipif(not GUARD.exists(), reason="guard script not present")
def test_the_pre_push_guard_treats_bead_ids_as_WARN_not_block():
    """The guard's half — asserted by RUNNING its selftest, not by reading its
    source. The selftest exercises both directions on a real repo, and it is the
    artifact that previously encoded the wrong answer, so running it is what
    proves the correction actually took.
    """
    r = subprocess.run(["bash", str(GUARD), "--selftest"],
                       capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    assert "selftest PASSED" in out, f"guard selftest failed:\n{out}"
    # The specific line that used to read "still refused". Asserted on the
    # OUTCOME the selftest prints rather than on the guard's source, so a
    # rewrite of the guard that keeps the behaviour keeps this passing.
    assert "ticket in a CHANGELOG is allowed AND warned about" in out, (
        "the guard's selftest no longer proves bead ids are warn-tier — either "
        "it regressed to blocking (aegis-krlog) or the case was deleted")
    # …and the block tier must still be proven, in the same run. A guard that
    # stopped refusing hostnames would also make the line above pass.
    assert "hostname in source is still refused" in out
    assert "new leak in a new branch is refused" in out


@pytest.mark.skipif(not GUARD.exists(), reason="guard script not present")
def test_the_guard_does_not_count_bead_ids_toward_a_REFUSAL():
    """Structural backstop for the one thing the selftest cannot notice: that
    `tickets` is not wired back into the refusal condition. A future edit could
    re-add it and still pass the selftest if it also relaxed something else, so
    this reads the condition itself.

    Deliberately narrow — it asserts the absence of ONE construct, not the shape
    of the whole script, because a broad source assertion breaks on every honest
    refactor and gets deleted.
    """
    src = GUARD.read_text()
    refusal = [ln for ln in src.splitlines()
               if re.search(r'^\s*if\s+\[\s+-n\s+"\$added"', ln)]
    assert refusal, "could not find the refusal condition — has the guard been rewritten?"
    for ln in refusal:
        assert "$tickets" not in ln, (
            "bead ids are counted toward a REFUSAL again. The graph rates them "
            "warn; see aegis-krlog for what that cost.")
