"""aegis-5lwl PART B: capture must record, queries must answer, and above all
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


def test_mcp_tool_recorded(tmp_path, monkeypatch):
    # aegis-rcyd: the store must SEE mcp__* — the whole point of the capture fix.
    monkeypatch.setenv("SHANTY_AGENT", "kelly")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    p = _payload_tool(tool_name="mcp__bobbin__search",
                      tool_input={"query": "where is X configured"})
    assert _run_capture(tmp_path, p, monkeypatch) == 0
    row = sqlite3.connect(tmp_path / "stats.sqlite").execute(
        "SELECT tool, file, detail FROM events").fetchone()
    assert row == ("mcp__bobbin__search", None, None)


def test_bash_binary_attributed_in_detail(tmp_path, monkeypatch):
    # aegis-rcyd: hank/bobbin CLI runs via Bash — record the binary so 'Bash×N'
    # is not the whole story. Leading VAR=val + wrappers are skipped.
    monkeypatch.setenv("SHANTY_AGENT", "kelly")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    for cmd, want in [
        ("hank impact foo", "hank"),
        ("BOBBIN_SERVER=http://s /home/x/.local/bin/bobbin search q", "bobbin"),
        ("env FOO=1 bd ready", "bd"),
        ("git log --oneline", "git"),
    ]:
        root = tmp_path / want
        p = _payload_tool(tool_name="Bash", tool_input={"command": cmd})
        assert _run_capture(root, p, monkeypatch) == 0
        got = sqlite3.connect(root / "stats.sqlite").execute(
            "SELECT tool, detail FROM events").fetchone()
        assert got == ("Bash", want), f"{cmd!r} -> {got}"


# --- attribution past `cd X &&`, and the risk class (aegis-xxae9) -----------
#
# THE MEASUREMENT THAT FORCED THIS. Replaying the fleet's 50,156 real Bash
# commands, the first version of _bash_bin attributed 25,698 of them to `cd` —
# the largest bucket by 12x, every one hiding the command that actually ran. A
# session that deployed binaries to production three times and restarted two live
# services recorded ZERO deploy-shaped commands. The session ceiling budgets risk
# more tightly than ordinary work, and it cannot do that on a column that is
# blind to precisely the risky half. After the fix: 246 `cd` (99.0% recovered).

@pytest.mark.parametrize("cmd,want", [
    ("cd /srv/repo && ansible-playbook site.yml", "ansible-playbook"),
    ("cd /repo && cargo build --release", "cargo"),
    ("timeout 300 ansible-playbook -i inv deploy.yml", "ansible-playbook"),
    ("timeout -k 5 30 hank impact foo", "hank"),
    ("cd /tmp && ls -la | head -3", "ls"),
    ("cd /a && cd /b && bd ready", "bd"),
    ("cd /tmp", "cd"),                       # genuinely only navigation — say so
])
def test_attribution_sees_past_cd_and_wrappers(cmd, want):
    assert stats._bash_bin({"command": cmd}) == want


@pytest.mark.parametrize("cmd,want", [
    # counted: it changes production
    ("cd ~/ops/ansible && ansible-playbook -i inv site.yml", "deploy"),
    ("systemctl restart bobbin", "restart"),
    ("sudo systemctl reload traefik", "restart"),
    ("ssh host.invalid 'systemctl restart quipu'", "restart"),
    ("scp ./bobbin root@host.invalid:/usr/local/bin/", "deploy"),
    ("docker compose up -d", "deploy"),
    ("ansible web -m copy -a 'src=x dest=/etc/y'", "deploy"),
    # NOT counted, and each one is a false positive that was measured for real
    ("cd ~/ops/ansible && ansible-playbook site.yml --check", None),   # dry run
    ("ansible-playbook site.yml --syntax-check", None),
    ("ansible --version", None),
    ("ansible web -m shell -a 'journalctl -u foo | tail'", None),  # a log read
    ("scp root@host.invalid:/opt/quipu/quipu.db ./local/", None),      # a FETCH
    ("rsync -a root@host:/etc/traefik/ /tmp/look/", None),         # a FETCH
    ("ssh host.invalid 'cat /etc/hosts'", None),
    ("ssh -i ~/.ssh/id_ed25519 root@host.invalid 'systemctl is-active quipu'", None),
    ("systemctl --user restart gastown", None),                    # not production
    ("systemctl status bobbin", None),
    ("docker ps", None),
    ("git status && echo done", None),
])
def test_risk_class_counts_production_actions_only(cmd, want):
    """CONSERVATIVE ON PURPOSE. Under-counting is the bug this fixes, but a
    budget that trips on `git status` is noise, and noise gets switched off. Each
    None above is a real false positive from the first pass over fleet history:
    dry runs, ad-hoc log reads, and — the largest group — scp/rsync FETCHES,
    which were counted as deploys because only "is any endpoint remote" was
    asked, never which END was."""
    assert stats._risk_class("Bash", {"command": cmd}) == want


def test_a_heredoc_BODY_is_data_not_commands():
    """Prose that merely mentions a restart must not be measured as one — the
    aegis-0214 hazard read backwards. A commit message is not a deploy."""
    cmd = ("git commit -F - <<'EOF'\n"
           "fix(deploy): stop running `systemctl restart quipu` by hand\n"
           "EOF")
    assert stats._risk_class("Bash", {"command": cmd}) is None
    assert stats._bash_bin({"command": cmd}) == "git"


def test_the_mcp_restart_tool_is_itself_a_production_action():
    assert stats._risk_class("mcp__homelab__service_restart", {}) == "restart"
    assert stats._risk_class("mcp__bobbin__search", {"query": "x"}) is None


def test_risk_is_recorded_on_the_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_AGENT", "billy")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    p = _payload_tool(tool_name="Bash",
                      tool_input={"command": "cd /a && systemctl restart quipu"})
    assert _run_capture(tmp_path, p, monkeypatch) == 0
    assert sqlite3.connect(tmp_path / "stats.sqlite").execute(
        "SELECT detail, risk FROM events").fetchone() == ("systemctl", "restart")


def test_unparseable_command_still_records_the_row(tmp_path, monkeypatch):
    """Fail-soft: a row without attribution beats a lost row, and capture must
    never raise into a tool call."""
    monkeypatch.setenv("SHANTY_AGENT", "billy")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    p = _payload_tool(tool_name="Bash", tool_input={"command": "echo 'unbalanced"})
    assert _run_capture(tmp_path, p, monkeypatch) == 0
    assert sqlite3.connect(tmp_path / "stats.sqlite").execute(
        "SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_skill_use_recorded_as_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_AGENT", "kelly")
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    p = _payload_tool(tool_name="Skill", tool_input={"skill": "graph-extract"})
    _run_capture(tmp_path, p, monkeypatch)
    (skill,) = sqlite3.connect(tmp_path / "stats.sqlite").execute(
        "SELECT skill FROM events").fetchone()
    assert skill == "graph-extract"


def test_post_tool_scrubs_bearer_from_result_output(tmp_path, monkeypatch):
    """The guard watches persisted OUTPUT, where all five leaks occurred."""
    token = "synthetic_quipu_bearer_0123456789abcdef"
    token_file = tmp_path / "quipu-token"
    token_file.write_text(token)
    monkeypatch.setenv("QUIPU_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("QUIPU_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({
        "type": "tool_result", "content": "token present: yes" + token,
    }) + "\n")
    original_size = transcript.stat().st_size
    payload = _payload_tool(transcript_path=str(transcript))

    assert _run_capture(tmp_path, payload, monkeypatch) == 0
    scrubbed = transcript.read_text()
    assert token not in scrubbed
    assert "token present: yes[REDACTED]" in scrubbed
    assert transcript.stat().st_size == original_size
    json.loads(scrubbed)


def test_scrub_covers_cached_old_and_rotated_file_bearers(tmp_path, monkeypatch):
    old = "synthetic_old_quipu_bearer_0123456789"
    new = "synthetic_new_quipu_bearer_9876543210"
    token_file = tmp_path / "quipu-token"
    token_file.write_text(new)
    monkeypatch.setenv("QUIPU_AUTH_TOKEN", old)
    monkeypatch.setenv("QUIPU_TOKEN_FILE", str(token_file))
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(f"old={old} new={new}\n")

    assert stats._scrub_transcript(str(transcript), full=True) == 2
    scrubbed = transcript.read_text()
    assert old not in scrubbed and new not in scrubbed


def test_scrub_catches_authorization_output_without_known_token(
        tmp_path, monkeypatch):
    bearer = "synthetic_unknown_bearer_0123456789abcdef"
    monkeypatch.delenv("QUIPU_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("QUIPU_TOKEN_FILE", str(tmp_path / "missing"))
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(f"Authorization: Bearer {bearer}\n")

    assert stats._scrub_transcript(str(transcript), full=True) == 1
    assert bearer not in transcript.read_text()


def test_safe_boolean_presence_output_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("QUIPU_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("QUIPU_TOKEN_FILE", str(tmp_path / "missing"))
    transcript = tmp_path / "session.jsonl"
    original = "token present: yes\nAuthorization: Bearer <token>\n"
    transcript.write_text(original)

    assert stats._scrub_transcript(str(transcript), full=True) == 0
    assert transcript.read_text() == original


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


def test_stop_scrubs_full_transcript_before_counting(tmp_path, monkeypatch):
    token = "synthetic_quipu_bearer_0123456789abcdef"
    token_file = tmp_path / "quipu-token"
    token_file.write_text(token)
    monkeypatch.setenv("QUIPU_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("QUIPU_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ST_STATS_PUSHGATEWAY", raising=False)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"content": token}) + "\n" +
        json.dumps({"message": {"usage": {
            "input_tokens": 4, "output_tokens": 2,
        }}}) + "\n")
    stop = {"session_id": "s-stop", "hook_event_name": "Stop",
            "transcript_path": str(transcript)}

    assert _run_capture(tmp_path, stop, monkeypatch) == 0
    assert token not in transcript.read_text()
    assert sqlite3.connect(tmp_path / "stats.sqlite").execute(
        "SELECT input_toks, output_toks FROM tokens").fetchone() == (4, 2)


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
