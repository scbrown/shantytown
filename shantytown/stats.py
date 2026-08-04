"""stats — PART B of st observability (aegis-5lwl): capture, aggregate, query.

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
import re
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
    session TEXT,
    detail  TEXT,                   -- Bash: invoked binary (CLI attribution, rcyd)
    risk    TEXT                    -- 'deploy' | 'restart' | NULL (xxae9)
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
    # Migrate pre-detail stores in place (aegis-rcyd): CREATE TABLE IF NOT EXISTS
    # won't add a column to an existing events table. Idempotent — the duplicate-
    # column error just means it's already there.
    for col in ("detail TEXT", "risk TEXT"):
        try:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
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


# Wrappers that PREFIX the real command. `timeout` and `xargs` take an argument
# of their own, so skipping the bare word is not enough — see _segment_bin.
_WRAPPERS = ("env", "sudo", "command", "nice", "nohup", "time", "exec", "doas")
# Wrappers that take operands of their own before the real command. `timeout`
# takes a DURATION (and -k takes one too), so a fixed skip count gets
# `timeout -k 5 30 hank …` wrong — it lands on `5`. Durations are skipped by
# SHAPE instead.
_DURATION_WRAPPERS = ("timeout",)
_FLAG_WRAPPERS = ("xargs", "stdbuf")
_DURATION = re.compile(r"^[0-9]+(\.[0-9]+)?[smhd]?$")
# Leaders that are NAVIGATION, not work. A command is almost never `cd` FOR the
# cd — it is `cd somewhere && <the thing you actually ran>`.
_NAVIGATION = ("cd", "pushd", "popd", "source", ".", "true", ":")
_SEGMENT_SPLIT = ("&&", "||", ";", "|", "&")


def _strip_heredocs(cmd: str) -> str:
    """Drop heredoc BODIES, keeping the command line that introduced them.

    A heredoc body is DATA — a commit message, a bead comment, a config file.
    shlex has no idea, so it tokenises the prose as bare words, and a message
    that merely MENTIONS `systemctl restart foo` then classifies as a restart.
    That is the aegis-0214 hazard read backwards: there, prose was executed;
    here, prose is measured. Both come from the same place — a body is not a
    command, and only the delimiter says where it ends."""
    if "<<" in cmd:
        out, lines, i = [], cmd.split("\n"), 0
        while i < len(lines):
            line = lines[i]
            out.append(line)
            m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
            i += 1
            if m:
                delim = m.group(1)
                while i < len(lines) and lines[i].strip() != delim:
                    i += 1
                i += 1               # consume the closing delimiter too
        return "\n".join(out)
    return cmd


def _segments(cmd: str) -> list[list[str]]:
    """A shell command split into its pipeline/list segments, tokenised.

    Only the structure `_bash_bin` needs — not a shell parser. shlex keeps quoted
    strings whole, so a `;` inside quotes does not split (which a naive
    `cmd.split(';')` would get wrong)."""
    cmd = _strip_heredocs(cmd)
    try:
        import shlex
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        toks = list(lexer)
    except ValueError:
        toks = cmd.split()
    out: list[list[str]] = [[]]
    for t in toks:
        if t in _SEGMENT_SPLIT:
            out.append([])
        else:
            out[-1].append(t)
    return [s for s in out if s]


def _segment_bin(toks: list[str]) -> str | None:
    """The executable a single segment invokes, past assignments and wrappers."""
    i = 0
    while i < len(toks):
        t = toks[i]
        if "=" in t and not t.startswith(("-", "/")) and t.split("=", 1)[0].isidentifier():
            i += 1                       # leading FOO=bar assignment
            continue
        if t in _WRAPPERS:
            i += 1                       # simple wrapper — the real binary is next
            continue
        if t in _DURATION_WRAPPERS:
            i += 1
            # `timeout [-k DUR] [--flags] DURATION cmd` — flags and durations in
            # any order, then the command. Skipping a fixed count landed on the
            # duration; skipping by shape does not.
            while i < len(toks) and (toks[i].startswith("-")
                                     or _DURATION.match(toks[i])):
                i += 1
            continue
        if t in _FLAG_WRAPPERS:
            i += 1
            while i < len(toks) and toks[i].startswith("-"):
                i += 1
            continue
        break
    if i >= len(toks):
        return None
    from os.path import basename
    return basename(toks[i]) or None


def _bash_bin(tool_input: dict) -> str | None:
    """The executable a Bash command actually RAN, so CLI-via-Bash usage (hank,
    bobbin, bd, git, curl to a service…) is attributable — 'Bash×313' cannot tell
    you whether the crew is reaching for hank/bobbin (aegis-rcyd Phase 0).

    THIS LOOKS PAST `cd X &&`, AND THAT IS THE WHOLE POINT (aegis-xxae9). The
    first version took the first token of the whole string, so every
    `cd repo && ansible-playbook …` was recorded as `cd`. Measured before the
    fix: `cd` was 6178 of the fleet's Bash rows — the largest bucket by 12x, and
    every one of them was hiding the command that actually ran. A session that
    built and deployed three binaries and restarted two production services
    showed ZERO deploy-shaped commands in the store. An attribution column that
    is blind to the risky half of the fleet's commands cannot support a budget
    that is supposed to be TIGHTER on risk, which is what sent this back here.

    So: split into segments, and return the first one that is not pure
    navigation. `cd /tmp && ls` is an `ls`. A command that really is only
    navigation still reports `cd` — honest, just no longer the default answer.

    Returns None on anything unparseable — the row is still recorded, just
    without a CLI attribution (fail-soft)."""
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    first = None
    for seg in _segments(cmd):
        b = _segment_bin(seg)
        if b is None:
            continue
        if first is None:
            first = b
        if b not in _NAVIGATION:
            return b
    return first                         # all navigation — say so rather than lie


# --- risk classification (aegis-xxae9) -------------------------------------
#
# WHY AT CAPTURE TIME. The classifier needs the WHOLE command — `ansible-playbook
# --check` is a dry run and must not count, and `ssh host 'systemctl restart x'`
# must. `detail` holds only a basename, so a governor reading it back later
# cannot tell those apart. The hook is the one place the full text exists, so the
# verdict is computed here and stored beside the row.
#
# CONSERVATIVE BY CONSTRUCTION. Under-counting is the bug this exists to fix, but
# OVER-counting is what gets a budget switched off — a governor that trips on
# `git status` is noise, and noise is uninstalled. So nothing lands here on a
# guess: each entry is a subcommand that changes something outside this host, or
# restarts something serving traffic.

_RESTART_VERBS = {"restart", "reload", "start", "stop", "up", "down", "recreate"}
# `mcp__…` tools that ARE the production action, no shell involved.
_RISK_TOOLS = {
    "mcp__homelab__service_restart": "restart",
    "mcp__homelab__container_restart": "restart",
}


def _is_remote(word: str) -> bool:
    """`host:path` — an scp/rsync endpoint on another machine."""
    head = word.split(":", 1)[0]
    return ":" in word and bool(head) and "/" not in head


def _pushes_remote(toks: list[str]) -> bool:
    """Does an scp/rsync call PUSH to a remote destination?

    DIRECTION IS THE WHOLE QUESTION, and getting it wrong is what made this
    over-count: `scp root@host:/opt/svc/data.db ./` is a FETCH — reading a
    production file down to look at it — and the first version counted it as a
    deploy because it only asked whether any argument was remote. Measured
    against the fleet's real history, fetches were a large share of the scp/rsync
    hits. Only the LAST positional (the destination) decides."""
    words = [t for t in toks[1:] if not t.startswith("-")]
    return bool(words) and _is_remote(words[-1])


# ansible invocations that inspect rather than apply. `--check` is a dry run and
# the rest never touch a host at all.
_ANSIBLE_INERT = {"--check", "-C", "--syntax-check", "--list-tasks", "--list-hosts",
                  "--list-tags", "--version", "--help", "-h"}
# Ad-hoc modules that only READ. shell/command/raw are neither — they are
# whatever they were handed, so they recurse.
_ANSIBLE_READ_MODULES = {"setup", "ping", "debug", "slurp", "fetch", "gather_facts"}
_ANSIBLE_PASSTHROUGH_MODULES = {"shell", "command", "raw", "script"}
# ssh flags that consume the NEXT token, so a key path is not mistaken for the
# host and the host is not mistaken for the remote command.
_SSH_ARG_FLAGS = {"-i", "-o", "-p", "-l", "-F", "-E", "-b", "-c", "-D", "-e",
                  "-I", "-J", "-L", "-m", "-O", "-Q", "-R", "-S", "-W", "-w"}


def _opt_value(toks: list[str], flag: str) -> str | None:
    """The value of `--flag value` or `--flag=value`, if present."""
    for i, t in enumerate(toks):
        if t == flag and i + 1 < len(toks):
            return toks[i + 1]
        if t.startswith(flag + "="):
            return t.split("=", 1)[1]
    return None


def _ssh_remote_command(toks: list[str]) -> str | None:
    """The command an `ssh` invocation runs on the far side, or None for a plain
    session/tunnel. Skips flag operands so `ssh -i key host 'cmd'` finds `cmd`."""
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in _SSH_ARG_FLAGS:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        break
    if i >= len(toks) - 1:
        return None                      # host only (or nothing) — no command
    return " ".join(toks[i + 1:])


def _risk_of_segment(toks: list[str]) -> str | None:
    """The production-risk class of ONE command segment, or None."""
    b = _segment_bin(toks)
    if not b:
        return None
    # Re-find the binary's index so subcommand lookups are relative to it.
    try:
        rest = toks[toks.index(b) + 1:] if b in toks else toks[1:]
    except ValueError:                                    # pragma: no cover
        rest = toks[1:]
    words = [w for w in rest if not w.startswith("-")]
    flags = [w for w in rest if w.startswith("-")]
    sub = words[0] if words else ""

    if b in ("ansible-playbook", "ansible"):
        # --check is a DRY RUN. The session this module was written for ran one
        # deliberately, caught unrelated drift, and stopped — exactly the
        # behaviour a budget must not tax. --syntax-check and --version do not
        # reach a host at all; both showed up as false "deploys" when this only
        # tested for --check.
        if any(f.split("=", 1)[0] in _ANSIBLE_INERT for f in flags):
            return None
        if b == "ansible-playbook":
            return "deploy"
        # AD-HOC ansible is only as risky as the module it runs. `-m shell -a
        # "journalctl -u foo"` is a log read that happens to travel over ansible;
        # counting it as a deploy taxes exactly the diagnostic work an agent
        # should feel free to do. So passthrough modules recurse into their -a.
        mod = _opt_value(rest, "-m") or _opt_value(rest, "--module-name") or "command"
        if mod in _ANSIBLE_READ_MODULES:
            return None
        if mod in _ANSIBLE_PASSTHROUGH_MODULES:
            arg = _opt_value(rest, "-a") or _opt_value(rest, "--args") or ""
            for seg in _segments(arg):
                r = _risk_of_segment(seg)
                if r:
                    return r
            return None
        return "deploy"                  # copy/template/service/systemd/…
    if b == "systemctl":
        # --user units are this host's own agents, not a production service.
        if "--user" in flags:
            return None
        return "restart" if sub in _RESTART_VERBS else None
    if b in ("docker", "podman"):
        if sub == "compose":
            nxt = words[1] if len(words) > 1 else ""
            return "deploy" if nxt in _RESTART_VERBS else None
        return "restart" if sub in _RESTART_VERBS else None
    if b == "kubectl":
        return "deploy" if sub in ("apply", "rollout", "delete", "scale") else None
    if b == "helm":
        return "deploy" if sub in ("upgrade", "install", "rollback", "uninstall") else None
    if b in ("scp", "rsync"):
        return "deploy" if _pushes_remote(toks) else None
    if b == "ssh":
        # `ssh host <cmd>` is whatever <cmd> is. `ssh host` alone is a session.
        # Recursing is what keeps `ssh host 'systemctl restart svc'` honest
        # without taxing `ssh host 'cat /etc/hosts'`.
        remote = _ssh_remote_command(toks)
        if not remote:
            return None
        for seg in _segments(remote):
            r = _risk_of_segment(seg)
            if r:
                return r
        return None
    return None


def _risk_class(tool: str, tool_input: dict) -> str | None:
    """'deploy' | 'restart' | None — is this call a production-class action?

    Labelled rather than counted so a tripped budget can NAME what spent it.
    Never raises: an unclassifiable command is None, and the row still records."""
    try:
        if tool in _RISK_TOOLS:
            return _RISK_TOOLS[tool]
        if tool != "Bash":
            return None
        cmd = tool_input.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return None
        for seg in _segments(cmd):
            r = _risk_of_segment(seg)
            if r:
                return r
        return None
    except Exception:                    # noqa: BLE001 — capture never blocks
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
            # `detail` holds the invoked binary for Bash, so CLI-via-Bash usage
            # (hank/bobbin/bd/git/…) is attributable, not just "Bash×N"
            # (aegis-rcyd Phase 0). `file` stays the edited/read path — keeping
            # the file-touch metrics clean.
            conn.execute(
                "INSERT INTO events(ts, agent, kind, tool, file, skill, session,"
                " detail, risk) VALUES (?,?,?,?,?,?,?,?,?)",
                (now, agent, "tool", tool, _file_of(ti),
                 ti.get("skill") if tool == "Skill" else None, session,
                 _bash_bin(ti) if tool == "Bash" else None,
                 _risk_class(tool, ti)),
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
        measured = 0
        for ag, ev, files, stops in rows:
            # BOUNDED BY THE SAME WINDOW AS THE EVENTS BESIDE IT (aegis-u5u98).
            # This query had NO time filter while the events query had one, so
            # the line read `st stats — last 24h` and printed ALL-TIME token
            # totals next to 24h event counts. That is what turned a total,
            # fleet-wide, 12-day capture outage into something that looked like
            # a per-agent quirk: the only agents showing tokens were the four
            # with rows left over from the last day the Stop path ever fired,
            # and the bug report reasonably went hunting for what made those
            # four special. Nothing did. The window did.
            n, inp, outt = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(input_toks),0),"
                " COALESCE(SUM(output_toks),0)"
                " FROM tokens WHERE agent=? AND updated>?", (ag, cutoff)).fetchone()
            measured += n
            # NO ROW IS NOT A ZERO, and this is the whole reason the outage was
            # invisible for twelve days. `tokens_out=0` is a MEASUREMENT — it
            # says this agent ran and produced nothing — and it renders
            # identically to "nothing has ever recorded a token for this agent".
            # One invites a shrug, the other is a broken pipeline. Same rule
            # `roles --check` follows with `hooks: ?`, and `crew --governor`
            # with `?/?/?`: never print a word you did not measure.
            toks = (f"tokens_in={inp} tokens_out={outt}" if n
                    else "tokens=? (none captured)")
            print(f"  {ag:<14} events={ev:<6} files={files:<4} stops={stops:<4}"
                  f" {toks}", file=out)
        if rows and not measured:
            # THE TELL. Every agent reading `tokens=?` is a fleet-wide fault,
            # not twenty independent gaps, and it has exactly one cause worth
            # naming: tokens are written ONLY on the Stop branch of `capture`,
            # so if nothing registers that hook nothing can ever record one.
            # Without this line the reader sees twenty question marks and no
            # sentence telling them it is one thing and where to look.
            print("  ⚠ NO TOKENS CAPTURED FOR ANY AGENT in this window. Tokens are"
                  " recorded only on the Stop hook; events (above) come from"
                  " PostToolUse and prove the store itself is healthy. Check that"
                  " `shantytown.stats capture` is registered on Stop —"
                  " `st doctor` reports it.", file=out)
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
        # CLI-via-Bash attribution (aegis-rcyd): which binaries the crew reached
        # for — the leverage signal 'Bash×N' hides (hank/bobbin/bd/git/curl…).
        cli = conn.execute(
            f"SELECT detail, COUNT(*) FROM events WHERE kind='tool'"
            f" AND tool='Bash' AND detail IS NOT NULL AND ts>? {where}"
            f" GROUP BY detail ORDER BY 2 DESC LIMIT 10",
            [cutoff] + args).fetchall()
        if cli:
            print("  cli:    " + ", ".join(f"{c}×{n}" for c, n in cli), file=out)
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
    #1 of aegis-5lwl): a broken stats layer must be invisible to the tool call
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
