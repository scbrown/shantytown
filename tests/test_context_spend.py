"""Context occupancy: three states, and UNKNOWN is never 0% (aegis-b2nwi9)."""
import json

import pytest

from shantytown import context_spend as cs


def _write(p, records):
    p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return p


def _turn(read_, write_, inp=2, model="claude-opus-5"):
    return {"message": {"model": model, "usage": {
        "input_tokens": inp, "cache_read_input_tokens": read_,
        "cache_creation_input_tokens": write_, "output_tokens": 900}}}


# --- occupancy is a SNAPSHOT, not a sum ------------------------------------

def test_occupancy_is_the_LAST_record_not_the_sum(tmp_path):
    """The defect that makes reusing harness.read_usage wrong.

    cache_read on turn N already contains what turns 1..N-1 put in the window, so
    summing double-counts nearly the whole conversation. Three turns of ~100k
    occupancy must read ~100k, not ~300k.
    """
    f = _write(tmp_path / "s.jsonl",
               [_turn(50_000, 1_000), _turn(80_000, 1_000), _turn(100_000, 2_000)])
    consumed, model = cs.read_consumed(f)
    assert consumed == 2 + 100_000 + 2_000
    assert model == "claude-opus-5"


def test_a_reading_uses_the_configured_window(tmp_path):
    f = _write(tmp_path / "s.jsonl", [_turn(690_000, 2_000)])
    r = cs.read(f, window=1_000_000)
    assert r.state == cs.MEASURED
    assert 69 <= r.pct <= 70
    assert r.over(70) is False and r.over(60) is True


# --- UNKNOWN is a THIRD state, never zero ----------------------------------

@pytest.mark.parametrize("records", [[], [{"message": {"model": "m"}}], [{"junk": 1}]])
def test_a_transcript_without_usage_is_UNKNOWN_not_zero(tmp_path, records):
    f = _write(tmp_path / "s.jsonl", records)
    r = cs.read(f, window=1_000_000)
    assert r.state == cs.UNKNOWN
    assert r.pct is None, "UNKNOWN must have NO percentage — 0.0 reads as 'plenty of room'"
    assert r.over(1) is False


def test_a_missing_transcript_is_UNKNOWN_not_zero(tmp_path):
    r = cs.read(tmp_path / "does-not-exist.jsonl", window=1_000_000)
    assert r.state == cs.UNKNOWN and r.pct is None


def test_an_unconfigured_window_is_UNKNOWN_and_says_why(tmp_path):
    """The measured refusal to infer a window from the model string.

    A live 1M session's transcript says only `claude-opus-5`, with no variant
    marker — the same string a 200k deployment writes. Guessing picks a silent
    failure in one direction or the other, so we decline and name the fix.
    """
    f = _write(tmp_path / "s.jsonl", [_turn(400_000, 2_000)])
    r = cs.read(f, window=None)
    assert r.state == cs.UNKNOWN and r.pct is None
    assert r.consumed == 2 + 400_000 + 2_000, "still report what WAS measurable"
    assert "context_window" in r.detail and "claude-opus-5" in r.detail


# --- over-window stays LOUD instead of clamping ----------------------------

def test_consumed_over_the_window_is_its_own_state_not_100_percent(tmp_path):
    """Acceptance criterion 4. An impossible percentage is the only self-evidence
    that the configured window is wrong; clamping it to 100% destroys that signal
    AND fires the hint for a reason that is not true."""
    f = _write(tmp_path / "s.jsonl", [_turn(400_000, 2_000)])
    r = cs.read(f, window=200_000)
    assert r.state == cs.OVER_WINDOW
    assert "window is wrong, not the session" in r.label()


def test_THE_CONTROL_a_normal_reading_is_not_over_window(tmp_path):
    """So the test above cannot pass by the state always being OVER_WINDOW."""
    f = _write(tmp_path / "s.jsonl", [_turn(10_000, 500)])
    assert cs.read(f, window=200_000).state == cs.MEASURED


def test_labels_name_the_measure_never_a_bare_verdict(tmp_path):
    f = _write(tmp_path / "s.jsonl", [_turn(100_000, 1_000)])
    lab = cs.read(f, window=1_000_000).label()
    assert "101,002" in lab and "1,000,000" in lab, lab
    assert "%" in lab, "a bare count without the percentage makes the reader do the division"
    assert "UNKNOWN" in cs.read(tmp_path / "nope.jsonl", window=1).label()
