"""Depth must be KNOWN for every local agent (aegis-zl7jwm item 3).

Two defects made `st agent advise` print "depth unknown" for an agent with a
593k session: Claude's project slug was built by replacing only "/", and a
context hint naming a file that no longer exists shadowed the fallback.
"""
import argparse
import json
from types import SimpleNamespace

from shantytown import cli, session_budget
from shantytown.stats import _claude_project_dir


def test_the_project_slug_replaces_every_non_alphanumeric():
    assert (_claude_project_dir("/srv/my_repo/crew/malcolm")
            == "-srv-my-repo-crew-malcolm")
    assert _claude_project_dir("/srv/x/.cache/y") == "-srv-x--cache-y"


def _usage_line(tokens):
    return json.dumps({"type": "assistant", "timestamp": "2026-10-07T03:00:00Z",
                       "message": {"id": "m1", "role": "assistant", "usage": {
                           "input_tokens": 10, "cache_read_input_tokens": tokens,
                           "cache_creation_input_tokens": 0, "output_tokens": 5}}})


def test_a_hint_naming_a_vanished_file_falls_back_to_the_claude_session(
        tmp_path, monkeypatch):
    root, home = tmp_path / "root", tmp_path / "home"
    ws = "/w/my_repo/crew/malcolm"
    proj = home / ".claude" / "projects" / _claude_project_dir(ws)
    proj.mkdir(parents=True)
    (proj / "sid1.jsonl").write_text(_usage_line(593_000) + "\n")
    (root / "context_budget").mkdir(parents=True)
    (root / "context_budget" / "malcolm.json").write_text(json.dumps(
        {"payload": {"transcript_path": str(tmp_path / "gone.jsonl")}}))

    monkeypatch.setattr(cli.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(session_budget, "current_session", lambda r, a: "sid1")
    monkeypatch.setattr(cli, "_registry", lambda a: SimpleNamespace(
        get=lambda n: SimpleNamespace(workspace=ws, pane="")))
    monkeypatch.setattr(cli, "_cycle_anchor_bead", lambda a, n: "")
    sit, transcript = cli._advise_situation(
        argparse.Namespace(root=str(root)), "malcolm", None)
    assert transcript and transcript.endswith("sid1.jsonl")
    assert sit.depth_k is not None and sit.depth_k > 590
