"""Harness-owned session usage parsing (aegis-8c1cv)."""
from __future__ import annotations

import json

from shantytown.harness import ClaudeHarness, CodexHarness


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
