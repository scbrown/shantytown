"""internal-ref PART B: capture must record, queries must answer, and above all
the hook must FAIL OPEN — a broken stats layer that blocks a tool call would be
a control inversion, so the fail-open cases here are the load-bearing ones."""
from __future__ import annotations

import io
import json
import sqlite3
import sys

import pytest

from shantytown import stats


def _payload_tool(**kw):
    d = {"session_id": "s1", "hook_event_name": "PostToolUse",
         "tool_name": "Edit", "tool_input": {"file_path": "/tmp/x.py"}}
    d.update(kw)
    return d


def _run_capture(root, payload, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return stats.main(["capture", "--root", str(root)])


# --- capture ---------------------------------------------------------------

def test_tool_call_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_AGENT", "kelly")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    assert _run_capture(tmp_path, _payload_tool(), monkeypatch) == 0
    row = sqlite3.connect(tmp_path / "stats.sqlite").execute(
        "SELECT agent, kind, tool, file FROM events").fetchone()
    assert row == ("kelly", "tool", "Edit", "/tmp/x.py")


def test_skill_use_recorded_as_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_AGENT", "kelly")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    p = _payload_tool(tool_name="Skill", tool_input={"skill": "graph-extract"})
    _run_capture(tmp_path, p, monkeypatch)
    (skill,) = sqlite3.connect(tmp_path / "stats.sqlite").execute(
        "SELECT skill FROM events").fetchone()
    assert skill == "graph-extract"


def test_stop_sums_transcript_tokens_idempotently(tmp_path, monkeypatch):
    """Token totals are ABSOLUTE per session and upserted — capturing the same
    stop twice must not double-count (re-summing is the idempotency)."""
    monkeypatch.setenv("SHANTY_AGENT", "kelly")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    tr = tmp_path / "t.jsonl"
    tr.write_text(
        json.dumps({"message": {"usage": {"input_tokens": 10, "output_tokens": 5}}})
        + "\nnot json at all\n"      # corrupt line must be skipped, not fatal
        + json.dumps({"message": {"usage": {"input_tokens": 7, "output_tokens": 3}}})
        + "\n")
    stop = {"session_id": "s9", "hook_event_name": "Stop",
            "transcript_path": str(tr)}
    for _ in range(2):
        assert _run_capture(tmp_path, stop, monkeypatch) == 0
    rows = sqlite3.connect(tmp_path / "stats.sqlite").execute(
        "SELECT input_toks, output_toks FROM tokens").fetchall()
    assert rows == [(17, 8)]


# --- fail-open: the contract ----------------------------------------------

def test_garbage_stdin_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{{{ not json"))
    assert stats.main(["capture", "--root", str(tmp_path)]) == 0


def test_unwritable_store_exits_zero(tmp_path, monkeypatch):
    """The db path is a DIRECTORY -> sqlite cannot open it. Still exit 0:
    the tool call being observed must never pay for our breakage."""
    (tmp_path / "stats.sqlite").mkdir()
    assert _run_capture(tmp_path, _payload_tool(), monkeypatch) == 0


def test_no_stdin_at_all_exits_zero(tmp_path, monkeypatch):
    class Dead:
        def read(self, *a): raise OSError("stdin gone")
    monkeypatch.setattr(sys, "stdin", Dead())
    assert stats.main(["capture", "--root", str(tmp_path)]) == 0


# --- query -----------------------------------------------------------------

def test_stats_report_answers_from_local_store(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SHANTY_AGENT", "billy")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    _run_capture(tmp_path, _payload_tool(), monkeypatch)
    p = _payload_tool(tool_name="Skill", tool_input={"skill": "dataviz"})
    _run_capture(tmp_path, p, monkeypatch)
    buf = io.StringIO()
    assert stats.stats_report(tmp_path, out=buf) == 0
    got = buf.getvalue()
    assert "billy" in got and "dataviz" in got and "events=2" in got


def test_stats_report_without_store_says_so(tmp_path):
    buf = io.StringIO()
    assert stats.stats_report(tmp_path, out=buf) == 1
    assert "no capture store yet" in buf.getvalue()


def test_stats_files_lists_touches(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_AGENT", "billy")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    _run_capture(tmp_path, _payload_tool(), monkeypatch)
    buf = io.StringIO()
    assert stats.stats_files(tmp_path, "billy", out=buf) == 0
    assert "/tmp/x.py" in buf.getvalue()


# --- export: present when configured, ABSENT when not ----------------------

def test_no_export_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    calls = []
    monkeypatch.setattr(stats.urllib.request, "urlopen",
                        lambda *a, **k: calls.append(a) or io.BytesIO(b""))
    _run_capture(tmp_path, _payload_tool(), monkeypatch)
    assert calls == [], "export must be CLEANLY ABSENT when unconfigured"


def test_export_pushes_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_AGENT", "zia")
    monkeypatch.setenv("ST_STATS_PUSHGATEWAY", "http://u:p@pg.invalid")
    seen = {}
    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization", "")
        seen["body"] = req.data.decode()
        return io.BytesIO(b"")
    monkeypatch.setattr(stats.urllib.request, "urlopen", fake_urlopen)
    _run_capture(tmp_path, _payload_tool(), monkeypatch)
    assert seen["url"].endswith("/metrics/job/st_stats/agent/zia")
    assert seen["auth"].startswith("Basic ")
    assert "st_events_total 1" in seen["body"]


def test_export_failure_is_still_fail_open(tmp_path, monkeypatch):
    monkeypatch.setenv("ST_STATS_PUSHGATEWAY", "http://pg.invalid")
    def boom(*a, **k): raise OSError("gateway down")
    monkeypatch.setattr(stats.urllib.request, "urlopen", boom)
    assert _run_capture(tmp_path, _payload_tool(), monkeypatch) == 0
