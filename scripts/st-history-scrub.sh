#!/usr/bin/env bash
# st-history-scrub.sh — produce the INDEXABLE derivative of the transcript
# archive, per sattler's ruling on aegis-ra6hvt.
#
# THE RULING: the raw archive stays on-host, 0700, UN-INDEXED. Only a scrubbed
# derivative is indexed. Credentials are replaced by a placeholder that keeps a
# 6-char hash prefix, so two occurrences of the same secret remain correlatable
# for provenance without the value ever appearing.
#
#     ghp_REALTOKENVALUE...  ->  [REDACTED:ghp:9f2a4c]
#
# WHY THE HASH PREFIX MATTERS: without it every secret scrubs to the same
# string, and an investigator can no longer tell "this one token leaked into
# eight sessions" from "eight different tokens leaked once". The prefix is 6 hex
# chars of sha256 — enough to correlate, far too little to reverse.
#
# WHAT IS DELIBERATELY *NOT* SCRUBBED, and why: internal hostnames, .lan/.svc
# names, private IPs and home paths. This index is on-host and internal, and
# those strings are the SEARCHABLE CONTENT — "which agent reasoned about a
# given host or service" is the query the epic exists to answer. Scrubbing them would produce a corpus
# that cannot answer the question it was built for. If the corpus ever leaves
# this host that decision must be revisited; it is recorded here so the next
# reader sees it was a decision, not an oversight.
set -uo pipefail

RAW="${ST_HISTORY_DIR:-$HOME/gt/shantytown/.shanty/history}"
OUT="${ST_HISTORY_SCRUBBED_DIR:-$HOME/gt/shantytown/.shanty/history-scrubbed}"

# --agent scopes the scrub to ONE agent's sessions, and INCREMENTAL skips a file
# whose derivative is already current. Both exist for the Stop hook: a full
# re-scrub is 21.1s over 966 MB / 332 files MEASURED 2026-09-02, and it grows
# with the archive, so paying it on every agent's every stop is not viable.
#
# An EMPTY --agent is REFUSED for the same reason as in st-history-capture.sh:
# the hook passes "$SHANTY_AGENT", and an unset identity must not silently
# become a fleet-wide 21s re-scrub.
ONLY_AGENT=""
FULL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --agent) shift
             if [ -z "${1:-}" ]; then
               echo "REFUSED: --agent needs a name (got empty). Scrubbed nothing."
               echo "  In the Stop hook this means \$SHANTY_AGENT is unset."
               exit 1
             fi
             ONLY_AGENT="$1" ;;
    # Re-scrub every file even if its derivative looks current. For use after a
    # PATTERN CHANGE: adding a credential class must revisit files the
    # incremental check would skip, and a derivative that silently kept an
    # old-pattern scrub would be the one thing this script must never produce.
    --full) FULL=1 ;;
    *) ;;
  esac
  shift
done

[ -d "$RAW" ] || { echo "no archive at $RAW"; exit 1; }
if [ -n "$ONLY_AGENT" ] && [ ! -d "$RAW/$ONLY_AGENT" ]; then
  echo "no captured sessions for '$ONLY_AGENT' under $RAW — scrubbed nothing"
  exit 0
fi
mkdir -p "$OUT"; chmod 700 "$OUT"

python3 - "$RAW" "$OUT" "$ONLY_AGENT" "$FULL" <<'PY'
import hashlib, pathlib, re, sys
raw, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
only_agent, full = sys.argv[3], sys.argv[4] == "1"
# gitleaks-shaped credential classes. Add here, never weaken: a smaller number
# from a looser pattern is not progress.
PATS = [
    ("ghp",     re.compile(rb'ghp_[A-Za-z0-9]{20,}')),
    ("gho",     re.compile(rb'gho_[A-Za-z0-9]{20,}')),
    ("ghs",     re.compile(rb'ghs_[A-Za-z0-9]{20,}')),
    ("sk",      re.compile(rb'sk-[A-Za-z0-9]{20,}')),
    ("aws",     re.compile(rb'AKIA[0-9A-Z]{16}')),
    ("bearer",  re.compile(rb'(?<=Bearer )[A-Za-z0-9._-]{24,}')),
    ("privkey", re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
]
# SCOPE. Unscoped is the whole archive; scoped is one agent's directory. The
# scoped walk is the hook's cost model: it must be a function of the stopping
# agent's own sessions, never of the fleet's.
srcs = sorted((raw / only_agent).rglob("*.jsonl")) if only_agent \
    else sorted(raw.rglob("*.jsonl"))

files = redacted = skipped = 0
for f in srcs:
    dst_pre = out / f.relative_to(raw)
    # INCREMENTAL. A capture is byte-append-only for a live session and
    # immutable once its session ends, so a derivative at least as new as its
    # source, and non-empty, is already current. mtime AND size: mtime alone
    # trusts a clock, size alone misses a same-length rewrite.
    if not full and dst_pre.exists():
        try:
            ss, ds = f.stat(), dst_pre.stat()
            if ds.st_mtime >= ss.st_mtime and ds.st_size > 0:
                skipped += 1
                continue
        except OSError:
            pass
    data = f.read_bytes()
    n = 0
    for name, p in PATS:
        def sub(m, _n=name):
            global n
            n += 1
            h = hashlib.sha256(m.group(0)).hexdigest()[:6]
            return b"[REDACTED:" + _n.encode() + b":" + h.encode() + b"]"
        data = p.sub(sub, data)
    dst = out / f.relative_to(raw)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    dst.chmod(0o600); dst.parent.chmod(0o700)
    files += 1; redacted += n
scope = f" for {only_agent!r}" if only_agent else ""
print(f"scrubbed {files} file(s){scope}, {redacted} credential-shaped "
      f"value(s) replaced, {skipped} already current")

# THE FALSIFIABLE CHECK: no pattern may survive into the derivative.
#
# It covers EXACTLY what this run is answerable for. Unscoped, that is the whole
# derivative — unchanged from before. Scoped, it is that agent's subtree, and it
# re-reads the SKIPPED files too, so an incremental run still proves the state it
# leaves behind rather than only the bytes it happened to write. The printed
# verdict names its scope: a scoped CLEAN says nothing about anyone else's
# sessions, and a check that let itself be read as fleet-wide would be worse than
# no check at all.
checked = out / only_agent if only_agent else out
left = 0
for f in checked.rglob("*.jsonl"):
    d = f.read_bytes()
    for _, p in PATS:
        left += len(p.findall(d))
print(f"residual credential-shaped hits in {checked}: {left}")
sys.exit(0 if left == 0 else 2)
PY
rc=$?
echo "derivative: $OUT${ONLY_AGENT:+/$ONLY_AGENT}"
if [ $rc -eq 0 ]; then
  echo "CLEAN — safe to index${ONLY_AGENT:+ (scope: $ONLY_AGENT only)}"
else
  echo "NOT CLEAN — do NOT index${ONLY_AGENT:+ $ONLY_AGENT} (rc=$rc)"
fi
exit $rc
