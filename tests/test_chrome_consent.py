"""The Chrome consent screen blocks the ready UI, and st must SEE it (aegis-neffw).

`--chrome` was landed as a per-card opt-in (test_harness_chrome.py). This file is
the half that makes it safe, and it exists because live-firing the feature found
two things nobody predicted.

LIVE-FIRE, claude 2.1.220, 2026-08-05, isolated tmux socket:

    claude --chrome
      -> folder-trust dialog          (st already answers this)
      -> CHROME CONSENT screen        <- blocks the ready UI
         shows_ready_ui   False
         waiting_for_human FALSE      <- st could not see it
         is_live          False
      -> one Enter
         is_live          True

**1. The consent screen still appears**, so aegis-84z1's hazard is live rather
than historical: any card that opts into chrome would launch, block, and be
reported could-not-tell — the exact production 0-path failure 84z1 was filed to
repair.

**2. `CONSENT_MARKERS` had ROTTED.** It looked for "Claude in Chrome extension
detected" and "keep browser tools off" — wording this claude no longer prints. So
`waiting_for_human` returned a confident False while a consent screen was
demonstrably up, and `st new` would have returned could-not-tell WITHOUT being
able to name the cause, which is the entire reason that third state exists.

A marker list is a claim about somebody ELSE'S UI text. It cannot fail loudly on
its own — it just quietly stops matching. So the fixture below is the real captured
screen: when the wording changes again, this goes red instead of going quiet.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shantytown.runtime import ClaudeRuntime

FIXTURE = Path(__file__).parent / "fixtures" / "chrome-consent-2.1.220.txt"


@pytest.fixture
def rt():
    return ClaudeRuntime.__new__(ClaudeRuntime)


@pytest.fixture
def consent_screen():
    return FIXTURE.read_text()


def test_the_real_consent_screen_is_recognised(rt, consent_screen):
    """THE REGRESSION TEST, against a screen captured from the running binary
    rather than from anyone's memory of it."""
    assert rt.chrome_prompt(consent_screen), (
        "CONSENT_MARKERS no longer match the real consent screen — claude changed "
        "its wording again. Re-capture the pane and update the markers; do NOT "
        "delete this test, its silence is the failure mode.")


def test_the_real_consent_screen_is_NOT_live(rt, consent_screen):
    """The consequence that matters: a blocked pane must not read as live, or
    `st new` reports success for an agent sitting on a prompt."""
    assert rt.is_live(consent_screen) is False
    assert rt.shows_ready_ui(consent_screen) is False


def test_waiting_for_human_can_now_SEE_it(rt, consent_screen):
    """This is what regressed. `waiting_for_human` is the third state between live
    and failed, and it answered False on a real consent screen — so the launcher
    could report 'could not tell' but not 'blocked on consent', which is the one
    thing a reader needs."""
    assert rt.waiting_for_human(consent_screen) is True


def test_a_ready_pane_is_not_mistaken_for_consent(rt):
    """POSITIVE CONTROL. Widening the markers is how a detector starts firing on
    everything; a ready UI must stay ready."""
    ready = "  ⏸ manual mode on · ? for shortcuts · ← for agents\n❯ \n"
    assert rt.chrome_prompt(ready) is False
    assert rt.is_live(ready) is True


def test_the_older_wording_still_matches(rt):
    """An older claude is still a claude somebody runs. The pre-2.1.220 markers are
    kept, not replaced."""
    assert rt.chrome_prompt("Claude in Chrome extension detected\n") is True
    assert rt.chrome_prompt("...keep browser tools off...\n") is True


def test_the_answer_is_a_bare_enter(rt):
    """Pinned so nobody hardcodes a '1' the way the trust dialog takes: this
    screen is a confirm, not a chooser."""
    assert rt.chrome_answer() == ""
