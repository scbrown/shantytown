#!/usr/bin/env bash
# st-history-corpus.sh — project the scrubbed transcript archive into an
# INDEXABLE corpus for bobbin (aegis-xfmon3 slice 2).
#
# WHY A PROJECTION RATHER THAN INDEXING THE JSONL. The scrubbed derivative is
# 967 MB of raw harness jsonl across two incompatible schemas. bobbin's existing
# record corpora (hla-records, beads, archive) are indexed ONE CHUNK PER FILE,
# which is right for a 2 KB record and wrong for a 48 MB session. Handing bobbin
# a jsonl parser for two harnesses would put schema knowledge in the search
# engine; handing it extracted text keeps that knowledge here, where the capture
# already lives, and lets bobbin index a directory of text files the way it
# already indexes several.
#
# SCOPE IS wu's CAPACITY/RELEVANCE RULING (aegis-oeswpq): dialogue,
# reasoning when plaintext exists, and tool INVOCATIONS — never tool RESULTS.
# Tool output is largely source and command stdout already represented in the
# code index; it was measured at 49.5% of the first projection's bytes.
#
#   codex   response_item payloads: message, custom_tool_call
#   claude  assistant/user text + thinking, assistant tool_use
#
# Codex `reasoning` payloads carry encrypted_content with an empty summary, so
# there is no plaintext to project. Claude `thinking` is plaintext and is kept.
# The provenance header names the harness, making that coverage difference
# explicit rather than silently discarding the reasoning that does exist.
set -uo pipefail

SRC="${ST_HISTORY_SCRUBBED_DIR:-$HOME/gt/shantytown/.shanty/history-scrubbed}"
OUT="${ST_HISTORY_CORPUS_DIR:-$HOME/gt/shantytown/.shanty/history-corpus}"
[ -d "$SRC" ] || { echo "no scrubbed archive at $SRC" >&2; exit 1; }
mkdir -p "$OUT"; chmod 700 "$OUT"

python3 - "$SRC" "$OUT" <<'PY'
import datetime, json, pathlib, sys

src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
PROJECTION_VERSION = 3  # v3 emits Bobbin archive Markdown with frontmatter

def clip(s, n=4000):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + f" …[+{len(s)-n} chars]"

def claude_turns(fh):
    for line in fh:
        try: d = json.loads(line)
        except Exception: continue
        if d.get("type") not in ("assistant", "user"):
            continue
        role, content = d["type"], (d.get("message") or {}).get("content")
        if isinstance(content, str):
            yield role, "text", clip(content); continue
        if not isinstance(content, list):
            continue
        for b in content:
            t = b.get("type")
            if t == "text":
                yield role, "text", clip(b.get("text", ""))
            elif t == "thinking":
                yield role, "thinking", clip(b.get("thinking", ""))
            elif t == "tool_use":
                yield role, f"tool:{b.get('name')}", clip(json.dumps(b.get("input", {})))

def codex_turns(fh):
    for line in fh:
        try: d = json.loads(line)
        except Exception: continue
        if d.get("type") != "response_item":
            continue
        p = d.get("payload") or {}
        t = p.get("type")
        if t == "message":
            c = p.get("content")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            yield p.get("role", "?"), "text", clip(c)
        elif t == "custom_tool_call":
            yield "assistant", f"tool:{p.get('name')}", clip(p.get("input", ""))

files = kept = 0
for f in sorted(src.rglob("*.jsonl")):
    agent = f.parent.name
    harness = "codex" if f.name.startswith("rollout-") else "claude"
    dst = out / agent / (f.stem + ".md")
    legacy = out / agent / (f.stem + ".txt")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Skip when this projector version is already newer than its source. The
    # version marker matters: an mtime-only cache left every old output intact
    # after the v2 scope change, so shipping a new projector changed no corpus.
    try:
        if dst.exists() and dst.stat().st_mtime >= f.stat().st_mtime:
            with dst.open(encoding="utf-8", errors="replace") as old:
                header = "".join(old.readline() for _ in range(4))
            if f"projection_version: {PROJECTION_VERSION}\n" in header:
                files += 1; continue
    except OSError:
        pass
    turns = 0
    with f.open(encoding="utf-8", errors="replace") as fh, \
         dst.open("w", encoding="utf-8") as o:
        # Bobbin archive records are Markdown with matching YAML frontmatter.
        # The timestamp is the scrubbed source's mtime: capture is append-only,
        # so it identifies the latest material represented by this projection.
        timestamp = datetime.datetime.fromtimestamp(
            f.stat().st_mtime, datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z")
        o.write("---\n")
        o.write("schema: agent-transcript/v1\n")
        o.write(f"projection_version: {PROJECTION_VERSION}\n")
        o.write(f"id: {f.stem}\n")
        o.write(f"timestamp: {timestamp}\n")
        o.write(f"agent: {agent}\n")
        o.write(f"harness: {harness}\n")
        o.write(f"source_file: {f.name}\n")
        o.write("---\n\n")
        # Human-readable provenance remains in the body so a raw search hit is
        # useful even on clients that do not render archive metadata.
        o.write(f"# transcript: agent={agent} harness={harness} session={f.stem}\n")
        o.write(f"# source: {f.name}\n#\n")
        o.write(f"# projection: {PROJECTION_VERSION}\n")
        gen = codex_turns(fh) if harness == "codex" else claude_turns(fh)
        for role, kind, text in gen:
            if not text:
                continue
            o.write(f"[{role}/{kind}] {text}\n")
            turns += 1
    if turns == 0:
        dst.unlink(missing_ok=True)   # nothing indexable — do not ship an empty file
    else:
        kept += 1
    legacy.unlink(missing_ok=True)   # v2 was plain .txt, invisible to archive indexing
    files += 1
print(f"corpus: {files} transcripts scanned, {kept} projected -> {out}")
PY
chmod -R go-rwx "$OUT" 2>/dev/null || true
exit 0
