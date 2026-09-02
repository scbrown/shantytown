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
import json, pathlib, sys

src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])

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
    dst = out / agent / (f.stem + ".txt")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Skip when the projection is already newer than its source: the archive is
    # append-only for a live session, so an up-to-date projection is current.
    try:
        if dst.exists() and dst.stat().st_mtime >= f.stat().st_mtime:
            files += 1; continue
    except OSError:
        pass
    turns = 0
    with f.open(encoding="utf-8", errors="replace") as fh, \
         dst.open("w", encoding="utf-8") as o:
        # PROVENANCE HEADER — the epic asks for agent/session/harness labels, and
        # a search hit is useless without them.
        o.write(f"# transcript: agent={agent} harness={harness} session={f.stem}\n")
        o.write(f"# source: {f.name}\n#\n")
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
    files += 1
print(f"corpus: {files} transcripts scanned, {kept} projected -> {out}")
PY
chmod -R go-rwx "$OUT" 2>/dev/null || true
exit 0
