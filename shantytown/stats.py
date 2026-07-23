"""stats — PART B of st observability (internal-ref): capture, aggregate, query.

The harness already KNOWS everything worth counting — Claude Code's PostToolUse
hook sees every tool call (name, file, skill) and the Stop hook can read the
session transcript's token usage. This module only has to CAPTURE it locally.

Three faces, one file:
  capture   the hook entry: `python -m shantytown.stats capture --root <shanty>`
            reads the hook's JSON payload from stdin, appends to a local sqlite
            store. FAIL OPEN BY CONTRACT: whatever happens — corrupt payload,
            locked db, missing dir, no stdin — it exits 0. A telemetry hook that
            can block a tool call is a control inversion nobody signed up for,
            so the ONLY unguarded line in main() is the exit itself.
  st stats  the query surface (cli.py wires it): files touched, skills used,
            tokens per agent, activity, closed-item throughput.
  export    OPTIONAL push to a Prometheus pushgateway, and only when
            ST_STATS_PUSHGATEWAY is set (st's env-var config discipline —
            local-first, the exporter is a bonus, never a dependency). Absent
            env -> the code path does not run at all.

The store is .shanty/stats.sqlite (WAL, busy_timeout) — append-only in spirit:
capture only INSERTs (events) or UPSERTs monotonically (tokens). No external
service is consulted, ever, on the capture path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    ts      REAL NOT NULL,
    agent   TEXT NOT NULL,
    kind    TEXT NOT NULL,          -- 'tool' | 'stop'
    tool    TEXT,
    file    TEXT,
    skill   TEXT,
    session TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_agent_ts ON events(agent, ts);
CREATE TABLE IF NOT EXISTS tokens (
    session      TEXT PRIMARY KEY,  -- one row per harness session, monotonic
    agent        TEXT NOT NULL,
    input_toks   INTEGER NOT NULL DEFAULT 0,
    output_toks  INTEGER NOT NULL DEFAULT 0,
    updated      REAL NOT NULL
);
"""


def _db(root: Path) -> sqlite3.Connection:
    p = Path(root) / "stats.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=3)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=2000")
    conn.executescript(_SCHEMA)
    return conn


def _agent() -> str:
    # Workers are launched with SHANTY_AGENT in env (compose seam). An unset
    # value is recorded honestly as 'unknown', never guessed from cwd.
    return os.environ.get("SHANTY_AGENT", "unknown")


def _file_of(tool_input: dict) -> str | None:
    for k in ("file_path", "path", "notebook_path"):
        v = tool_input.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _transcript_tokens(path: str) -> tuple[int, int]:
    """Sum assistant-message usage over a transcript jsonl. ABSOLUTE totals for
    the session — the tokens table upserts, so re-summing on every stop is
    idempotent, not double-counting. Corrupt lines are skipped (the ev-172
    lesson: one bad record must not dam the readable ones)."""
    inp = out = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            u = (d.get("message") or {}).get("usage") or {}
            inp += int(u.get("input_tokens") or 0)
            out += int(u.get("output_tokens") or 0)
    return inp, out


def capture(root: Path, payload: dict) -> None:
    """One hook firing -> at most one events row (+ a tokens upsert on stop)."""
    now = time.time()
    agent = _agent()
    session = payload.get("session_id") or ""
    hook = payload.get("hook_event_name") or ""
    conn = _db(root)
    try:
        if hook == "PostToolUse" or payload.get("tool_name"):
            ti = payload.get("tool_input") or {}
            tool = payload.get("tool_name") or "?"
            conn.execute(
                "INSERT INTO events(ts, agent, kind, tool, file, skill, session)"
                " VALUES (?,?,?,?,?,?,?)",
                (now, agent, "tool", tool, _file_of(ti),
                 ti.get("skill") if tool == "Skill" else None, session),
            )
        else:  # Stop (or anything stop-shaped): record the stop + token totals
            conn.execute(
                "INSERT INTO events(ts, agent, kind, session) VALUES (?,?,?,?)",
                (now, agent, "stop", session),
            )
            tp = payload.get("transcript_path")
            if tp and os.path.isfile(tp):
                inp, out = _transcript_tokens(tp)
                conn.execute(
                    "INSERT INTO tokens(session, agent, input_toks, output_toks,"
                    " updated) VALUES (?,?,?,?,?) ON CONFLICT(session) DO UPDATE"
                    " SET input_toks=excluded.input_toks,"
                    " output_toks=excluded.output_toks, updated=excluded.updated",
                    (session, agent, inp, out, now),
                )
        conn.commit()
    finally:
        conn.close()
    _maybe_export(root, agent)


# --- optional export -------------------------------------------------------

def _maybe_export(root: Path, agent: str) -> None:
    """Push per-agent aggregates to a Prometheus pushgateway IFF configured.
    ST_STATS_PUSHGATEWAY=http://[user:pass@]host[:port] — nothing set, nothing
    sent, no import-time side effects: 'export cleanly absent when not
    configured' is an acceptance line, not a nice-to-have."""
    url = os.environ.get("ST_STATS_PUSHGATEWAY", "").strip()
    if not url:
        return
    conn = _db(root)
    try:
        ev, files = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT file) FROM events WHERE agent=?",
            (agent,)).fetchone()
        inp, out = conn.execute(
            "SELECT COALESCE(SUM(input_toks),0), COALESCE(SUM(output_toks),0)"
            " FROM tokens WHERE agent=?", (agent,)).fetchone()
    finally:
        conn.close()
    body = (
        f"# TYPE st_events_total gauge\nst_events_total {ev}\n"
        f"# TYPE st_files_touched gauge\nst_files_touched {files}\n"
        f"# TYPE st_tokens_input_total gauge\nst_tokens_input_total {inp}\n"
        f"# TYPE st_tokens_output_total gauge\nst_tokens_output_total {out}\n"
    ).encode()
    from urllib.parse import urlsplit, urlunsplit
    import base64
    parts = urlsplit(url)
    headers = {"Content-Type": "text/plain"}
    netloc = parts.netloc
    if "@" in netloc:  # basic-auth userinfo in the env var
        cred, netloc = netloc.rsplit("@", 1)
        headers["Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
    push = urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", "")) \
        + f"/metrics/job/st_stats/agent/{agent}"
    req = urllib.request.Request(push, data=body, headers=headers, method="PUT")
    urllib.request.urlopen(req, timeout=3).read()


# --- query surface (st stats) ---------------------------------------------

def stats_report(root: Path, agent: str | None = None, since_h: float = 24.0,
                 out=sys.stdout) -> int:
    """The default `st stats` answer: per-agent activity, files, skills,
    tokens — from the LOCAL store only."""
    p = Path(root) / "stats.sqlite"
    if not p.is_file():
        print("st stats — no capture store yet (.shanty/stats.sqlite absent).\n"
              "The capture hook writes it on the first tool call after the\n"
              "hooks are wired (settings PostToolUse/Stop).", file=out)
        return 1
    cutoff = time.time() - since_h * 3600
    conn = _db(root)
    try:
        where, args = ("AND agent=?", [agent]) if agent else ("", [])
        rows = conn.execute(
            f"SELECT agent, COUNT(*), COUNT(DISTINCT file),"
            f" SUM(kind='stop') FROM events WHERE ts>? {where}"
            f" GROUP BY agent ORDER BY 2 DESC", [cutoff] + args).fetchall()
        print(f"st stats — last {since_h:g}h", file=out)
        if not rows:
            print("  (no activity captured in the window)", file=out)
        for ag, ev, files, stops in rows:
            inp, outt = conn.execute(
                "SELECT COALESCE(SUM(input_toks),0),COALESCE(SUM(output_toks),0)"
                " FROM tokens WHERE agent=?", (ag,)).fetchone()
            print(f"  {ag:<14} events={ev:<6} files={files:<4} stops={stops:<4}"
                  f" tokens_in={inp} tokens_out={outt}", file=out)
        sk = conn.execute(
            f"SELECT skill, COUNT(*) FROM events WHERE skill IS NOT NULL"
            f" AND ts>? {where} GROUP BY skill ORDER BY 2 DESC LIMIT 10",
            [cutoff] + args).fetchall()
        if sk:
            print("  skills: " + ", ".join(f"{s}×{n}" for s, n in sk), file=out)
        tools = conn.execute(
            f"SELECT tool, COUNT(*) FROM events WHERE kind='tool' AND ts>?"
            f" {where} GROUP BY tool ORDER BY 2 DESC LIMIT 8",
            [cutoff] + args).fetchall()
        if tools:
            print("  tools:  " + ", ".join(f"{t}×{n}" for t, n in tools), file=out)
    finally:
        conn.close()
    return 0


def stats_files(root: Path, agent: str, since_h: float = 24.0,
                out=sys.stdout) -> int:
    conn = _db(root)
    try:
        rows = conn.execute(
            "SELECT file, COUNT(*) FROM events WHERE agent=? AND file IS NOT"
            " NULL AND ts>? GROUP BY file ORDER BY 2 DESC LIMIT 50",
            (agent, time.time() - since_h * 3600)).fetchall()
    finally:
        conn.close()
    print(f"files touched by {agent} (last {since_h:g}h):", file=out)
    for f, n in rows:
        print(f"  {n:>4}  {f}", file=out)
    if not rows:
        print("  (none captured)", file=out)
    return 0


# --- hook entry ------------------------------------------------------------

def main(argv=None) -> int:
    """`python -m shantytown.stats capture --root <shanty>` — the hook entry.
    THIS FUNCTION MUST NEVER RETURN NONZERO from the capture path. The except
    below is not lazy error handling; it is the fail-open contract (constraint
    #1 of internal-ref): a broken stats layer must be invisible to the tool call
    it observes. Diagnostics go to stderr, which Claude Code surfaces in
    hook-error output without failing the call on exit 0."""
    try:
        import argparse
        ap = argparse.ArgumentParser(prog="shantytown.stats")
        ap.add_argument("cmd", choices=["capture"])
        ap.add_argument("--root", required=True)
        a = ap.parse_args(argv)
        payload = json.load(sys.stdin)
        capture(Path(a.root), payload)
    except Exception as e:  # noqa: BLE001 — the contract IS the breadth
        print(f"stats capture (fail-open, tool call unaffected): "
              f"{type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
