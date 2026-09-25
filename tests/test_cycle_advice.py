"""cycle_advice: keep-or-cycle at a handoff, from depth, cache timing and a posted signal.

The property that matters most is the unconfigured one: with nothing posted, the
advice is DEFAULT and the fleet behaves exactly as before. Every other test here
shows the advice saying something a caller would act on, including no.
"""
from __future__ import annotations

import json

import pytest

import shantytown.cli as cli
from shantytown import config
from shantytown import cycle_advice as ca

NOW = 1_800_000_000.0


def _sit(depth=80, idle=None, ttl=300, next_task="aegis-a"):
    cache = ca.Cache(None if idle is None else NOW - idle, ttl)
    return ca.Situation("ada", depth, 400.0, next_task, cache, NOW)


def _sig(related=None, decision=None, age=0, next_task="aegis-a"):
    return ca.Signal("jev", NOW - age, next_task, related, decision)


def test_no_signal_decides_nothing():
    assert ca.decide(_sit(idle=3600), None).decision == ca.DEFAULT


def test_the_cycle_line_beats_any_signal():
    advice = ca.decide(_sit(depth=410), _sig(related=0.99))
    assert advice.decision == ca.CYCLE
    assert ca.decide(_sit(depth=410), _sig(decision="keep")).decision == ca.CYCLE


def test_strong_relatedness_keeps():
    assert ca.decide(_sit(depth=200, idle=3600), _sig(related=0.9)).decision == ca.KEEP


def test_unrelated_cycles():
    assert ca.decide(_sit(depth=80, idle=10), _sig(related=0.1)).decision == ca.CYCLE


def test_a_lapsed_cache_flips_a_middling_signal_to_cycle():
    warm = ca.decide(_sit(depth=80, idle=10), _sig(related=0.6))
    lapsed = ca.decide(_sit(depth=80, idle=600), _sig(related=0.6))
    assert warm.decision == ca.KEEP, warm.render()
    assert lapsed.decision == ca.CYCLE, lapsed.render()
    assert lapsed.cache_lapsed is True and lapsed.keep_cost_k > warm.keep_cost_k


def test_the_1h_ttl_is_not_lapsed_at_ten_minutes():
    assert ca.decide(_sit(idle=600, ttl=3600), _sig(related=0.6)).cache_lapsed is False


def test_unknown_idle_is_priced_warm_not_lapsed():
    advice = ca.decide(_sit(depth=80, idle=None), _sig(related=0.6))
    assert advice.cache_lapsed is None
    assert advice.decision == ca.KEEP


def test_a_stale_signal_is_ignored():
    advice = ca.decide(_sit(), _sig(related=0.1, age=3 * 3600))
    assert advice.decision == ca.DEFAULT and "old" in advice.why


def test_a_signal_about_another_task_is_ignored():
    advice = ca.decide(_sit(next_task="aegis-b"), _sig(related=0.1))
    assert advice.decision == ca.DEFAULT and "aegis-b" in advice.why


def test_a_posted_decision_is_honoured_below_the_line():
    assert ca.decide(_sit(), _sig(decision="cycle")).decision == ca.CYCLE


def test_post_refuses_nonsense(tmp_path):
    with pytest.raises(ValueError):
        ca.post(tmp_path, "ada", ca.Signal("x", NOW, related=1.5))
    with pytest.raises(ValueError):
        ca.post(tmp_path, "ada", ca.Signal("x", NOW))
    assert ca.latest(tmp_path, "ada") is None


def test_post_latest_clear_round_trip(tmp_path):
    ca.post(tmp_path, "ada", ca.Signal("jev", NOW, "aegis-a", 0.7))
    assert ca.latest(tmp_path, "ada").related == 0.7
    ca.clear(tmp_path, "ada")
    assert ca.latest(tmp_path, "ada") is None


def _usage(ts, h1=0, m5=0, read=1000):
    return json.dumps({"timestamp": ts, "message": {"usage": {
        "input_tokens": 2, "cache_read_input_tokens": read,
        "cache_creation_input_tokens": h1 + m5,
        "cache_creation": {"ephemeral_1h_input_tokens": h1,
                           "ephemeral_5m_input_tokens": m5}}}})


def test_read_cache_finds_the_ttl_behind_a_fully_cached_turn(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text("\n".join([_usage("2026-09-25T18:00:00Z", h1=500),
                            _usage("2026-09-25T18:05:00.500Z")]) + "\n")
    cache = ca.read_cache(t)
    assert cache.ttl_seconds == 3600
    assert cache.last_at == pytest.approx(1790359500.5)


def test_read_cache_says_unknown_rather_than_guessing(tmp_path):
    assert ca.read_cache(tmp_path / "missing.jsonl") == ca.Cache()
    t = tmp_path / "s.jsonl"
    t.write_text(_usage("2026-09-25T18:00:00Z") + "\n")
    assert ca.read_cache(t).ttl_seconds is None


def test_config_table(tmp_path):
    (tmp_path / "shantytown.toml").write_text(
        "[cycle_advice]\nkeep_at = 0.9\nexpected_turns = 5\n")
    policy = config.load(tmp_path).cycle_advice
    assert policy.keep_at == 0.9 and policy.expected_turns == 5
    assert config.load(tmp_path / "absent").cycle_advice == ca.Policy()


@pytest.mark.parametrize("body", ["bogus = 1", "keep_at = 2", "expected_turns = 2.5",
                                  "keep_at = true"])
def test_config_refuses_bad_values(tmp_path, body):
    (tmp_path / "shantytown.toml").write_text(f"[cycle_advice]\n{body}\n")
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


def test_cli_post_then_read_back(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_cycle_anchor_bead", lambda a, agent: "aegis-a")
    root = ["--root", str(tmp_path)]
    assert cli.main([*root, "agent", "advise", "ada", "--related", "0.1"]) == cli.REFUSED
    assert cli.main([*root, "agent", "advise", "ada", "--related", "0.1",
                     "--by", "jev"]) == cli.OK
    capsys.readouterr()
    assert cli.main([*root, "agent", "advise", "ada", "--json"]) == cli.OK
    out = json.loads(capsys.readouterr().out)
    assert out["signal"]["by"] == "jev" and out["signal"]["next_task"] == "aegis-a"
    # No transcript recorded: depth unknown, so a middling signal cannot be priced,
    # but a clearly unrelated one still says cycle.
    assert out["depth_k"] is None and out["advice"]["decision"] == ca.CYCLE


# --- the bounds: never ask past the line, never hand over an unbounded brief ----

def test_always_cycle_above_k_overrides_a_keep_and_says_do_not_ask():
    policy = ca.Policy(always_cycle_above_k=250)
    sit = _sit(depth=260)
    assert ca.decide(sit, _sig(related=0.99), policy).decision == ca.CYCLE
    assert ca.decide(sit, None, policy).decision == ca.CYCLE
    assert ca.should_ask(sit, policy) is False
    assert ca.should_ask(_sit(depth=100), policy) is True


def test_unknown_depth_is_not_asked_about():
    assert ca.should_ask(_sit(depth=None), ca.Policy()) is False


def test_the_brief_is_bounded_and_the_next_task_survives_a_huge_checkpoint():
    policy = ca.Policy(max_brief_chars=1000)
    out = ca.brief([("current_session", "x" * 750_000),
                    ("next_task", "aegis-b extend create_advisory")], 400, policy)
    assert sum(len(v) for v in out["parts"].values()) <= 400
    assert out["parts"]["next_task"] == "aegis-b extend create_advisory"
    assert out["truncated"] is True
    bigger = ca.brief([("current_session", "x" * 750_000)], 10**9, policy)
    assert len(bigger["parts"]["current_session"]) == 1000, "clamped to the max"


def test_a_brief_that_fits_is_not_marked_truncated():
    out = ca.brief([("next_task", "short")], 1500, ca.Policy())
    assert out["truncated"] is False and out["parts"] == {"next_task": "short"}


def test_cli_json_brief_carries_the_checkpoint_bounded(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_cycle_anchor_bead", lambda a, agent: "aegis-a")
    note = tmp_path / "handoff.md"
    note.write_text("y" * 100_000)
    assert cli.main(["--root", str(tmp_path), "agent", "advise", "ada", "--json",
                     "--checkpoint-file", str(note), "--brief-chars", "300"]) == cli.OK
    out = json.loads(capsys.readouterr().out)
    assert out["ask"] is False, "depth unknown: nothing to ask"
    assert len(out["brief"]["parts"]["current_session"]) <= 300
    assert out["brief"]["truncated"] is True
