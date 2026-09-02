"""The Bobbin transcript projection keeps signal and drops duplicated output."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "st-history-corpus.sh"


def _run(tmp_path: Path, files: dict[str, list[dict]]) -> dict[str, str]:
    source = tmp_path / "scrubbed"
    output = tmp_path / "corpus"
    for relative, rows in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    env = os.environ | {
        "ST_HISTORY_SCRUBBED_DIR": str(source),
        "ST_HISTORY_CORPUS_DIR": str(output),
    }
    done = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return {str(p.relative_to(output)): p.read_text() for p in output.rglob("*.txt")}


def test_claude_keeps_dialogue_thinking_and_invocations_but_not_results(tmp_path):
    rows = [
        {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "reasoned signal"},
            {"type": "text", "text": "assistant dialogue"},
            {"type": "tool_use", "name": "Read", "input": {"file": "x"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "human dialogue"},
            {"type": "tool_result", "content": "duplicated machine output"},
        ]}},
    ]
    corpus = _run(tmp_path, {"dearing/session.jsonl": rows})
    text = corpus["dearing/session.txt"]
    assert "[assistant/thinking] reasoned signal" in text
    assert "[assistant/text] assistant dialogue" in text
    assert "[assistant/tool:Read]" in text
    assert "[user/text] human dialogue" in text
    assert "duplicated machine output" not in text
    assert "/tool_result]" not in text


def test_codex_keeps_dialogue_and_invocations_but_not_outputs(tmp_path):
    rows = [
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "codex dialogue"}],
        }},
        {"type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec_command", "input": "do thing",
        }},
        {"type": "response_item", "payload": {
            "type": "custom_tool_call_output", "output": "duplicated command output",
        }},
    ]
    corpus = _run(tmp_path, {"gennaro/rollout-2026-09-02.jsonl": rows})
    text = corpus["gennaro/rollout-2026-09-02.txt"]
    assert "[assistant/text] codex dialogue" in text
    assert "[assistant/tool:exec_command] do thing" in text
    assert "duplicated command output" not in text
    assert "/tool_result]" not in text
