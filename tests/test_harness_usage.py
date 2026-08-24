"""Harness-owned session usage parsing (aegis-8c1cv)."""
from __future__ import annotations

import json
import io

from shantytown.harness import ClaudeHarness, CodexHarness
from shantytown import stats


def test_claude_sums_message_deltas_and_keeps_cache_dimensions(tmp_path):
    p = tmp_path / "claude.jsonl"
    p.write_text("\n".join(json.dumps({"message": {"usage": u}}) for u in [
        {"input_tokens": 2, "cache_read_input_tokens": 3,
         "cache_creation_input_tokens": 5, "output_tokens": 7},
        {"input_tokens": 11, "output_tokens": 13},
    ]) + "\n")
    got = ClaudeHarness().read_usage(p)
    assert got is not None
    assert (got.input_tokens, got.cached_input_tokens,
            got.cache_write_input_tokens, got.output_tokens, got.total_tokens) == (13, 3, 5, 20, 41)


def test_codex_uses_final_cumulative_snapshot_not_the_sum(tmp_path):
    p = tmp_path / "codex.jsonl"
    def event(total, inp, out):
        return {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"total_tokens": total, "input_tokens": inp,
                                  "cached_input_tokens": 4, "output_tokens": out,
                                  "reasoning_output_tokens": 2}}}}
    p.write_text("\n".join(map(json.dumps, [event(10, 7, 3), event(30, 21, 9)])) + "\n")
    got = CodexHarness().read_usage(p)
    assert got is not None
    assert (got.total_tokens, got.input_tokens, got.output_tokens) == (30, 21, 9)


def test_absent_usage_is_unknown_not_zero(tmp_path):
    p = tmp_path / "empty.jsonl"; p.write_text(json.dumps({"type": "event_msg"}) + "\n")
    assert ClaudeHarness().read_usage(p) is None
    assert CodexHarness().read_usage(p) is None


def test_stats_reads_each_harness_store_and_keeps_missing_usage_unknown(tmp_path, monkeypatch):
    """The end-to-end seam: card -> harness-owned store -> provider-labelled stats.

    Codex is mapped by its recorded cwd; Claude's hyphenated workspace proves we
    compare the encoded project slug rather than trying to decode it.
    """
    root, home = tmp_path / "store", tmp_path / "home"
    crew = root / "crew"; crew.mkdir(parents=True)
    claude_ws = "/work/a-team"
    codex_ws = "/work/codex-team"
    role_codex_ws = "/work/role-codex-team"
    (crew / "claude-agent.json").write_text(json.dumps({"role": "worker", "workspace": claude_ws}))
    (crew / "codex-agent.json").write_text(json.dumps({"role": "worker", "workspace": codex_ws,
                                                        "harness": "codex"}))
    (crew / "role-codex-agent.json").write_text(json.dumps(
        {"role": "worker", "workspace": role_codex_ws, "harness": "codex"}))
    # The resolver's per-agent override wins over the role home.  Usage must
    # follow this path, not the default ~/.codex home.
    codex_home = root / "settings" / "codex" / "agent-codex-agent"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text("model = \"test\"\n")
    role_codex_home = root / "settings" / "codex" / "worker"
    role_codex_home.mkdir(parents=True)
    (role_codex_home / "config.toml").write_text("model = \"test\"\n")
    cp = home / ".claude" / "projects" / "-work-a-team" / "one.jsonl"
    cp.parent.mkdir(parents=True)
    cp.write_text(json.dumps({"message": {"usage": {"input_tokens": 2, "output_tokens": 3}}}) + "\n")
    xp = codex_home / "sessions" / "2026" / "08" / "07" / "rollout.jsonl"
    xp.parent.mkdir(parents=True)
    xp.write_text("\n".join(json.dumps(x) for x in [
        {"payload": {"cwd": codex_ws}},
        {"type": "event_msg", "payload": {"info": {"total_token_usage": {
            "total_tokens": 12, "input_tokens": 8, "output_tokens": 4}}}},
    ]) + "\n")
    # A second Codex session belonging to the same card has no token snapshot.
    (xp.parent / "unknown.jsonl").write_text(json.dumps({"payload": {"cwd": codex_ws}}) + "\n")
    rp = role_codex_home / "sessions" / "2026" / "08" / "07" / "role.jsonl"
    rp.parent.mkdir(parents=True)
    rp.write_text("\n".join(json.dumps(x) for x in [
        {"payload": {"cwd": role_codex_ws}},
        {"type": "event_msg", "payload": {"info": {"total_token_usage": {
            "total_tokens": 9, "input_tokens": 5, "output_tokens": 4}}}},
    ]) + "\n")

    got = stats.session_usage(root, home=home)
    assert got["claude-agent"]["claude"][0].total_tokens == 5
    assert got["codex-agent"]["codex"][0].total_tokens == 12
    assert got["codex-agent"]["codex"][1] == 1
    assert got["codex-agent"]["codex"][2] == 1
    assert got["role-codex-agent"]["codex"][0].total_tokens == 9
    # A transcript-only agent is included even before a capture hook event exists.
    monkeypatch.setattr(stats.Path, "home", lambda: home)
    buf = io.StringIO()
    assert stats.stats_report(root, out=buf) == 0
    rendered = buf.getvalue()
    assert "claude_tokens=5" in rendered
    assert "codex_tokens=12 (1 session unknown)" in rendered
    assert "usage_known=1 usage_in=8 usage_out=4 cache_read=0" in rendered


def test_stats_normalises_cache_hit_denominator_across_providers():
    rendered = stats._render_usage({
        "claude": (stats.Usage(input_tokens=10, cached_input_tokens=30,
                               cache_write_input_tokens=10, output_tokens=5,
                               total_tokens=55), 0, 1),
        # Codex input already includes its cached subset.
        "codex": (stats.Usage(input_tokens=50, cached_input_tokens=20,
                              output_tokens=7, total_tokens=57), 0, 1),
    })
    assert "usage_known=1 usage_in=100 usage_out=12 cache_read=50" in rendered


def test_unknown_only_usage_does_not_claim_measured_zeros():
    rendered = stats._render_usage({"codex": (stats.Usage(), 2, 0)})
    assert "usage_known=" not in rendered
    assert "codex_tokens=0 (2 sessions unknown)" in rendered
