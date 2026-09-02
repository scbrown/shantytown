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
[ -d "$RAW" ] || { echo "no archive at $RAW"; exit 1; }
mkdir -p "$OUT"; chmod 700 "$OUT"

python3 - "$RAW" "$OUT" <<'PY'
import hashlib, pathlib, re, sys
raw, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
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
files = redacted = 0
for f in sorted(raw.rglob("*.jsonl")):
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
print(f"scrubbed {files} file(s), {redacted} credential-shaped value(s) replaced")

# THE FALSIFIABLE CHECK: no pattern may survive into the derivative. If this
# fails the derivative must not be indexed.
left = 0
for f in out.rglob("*.jsonl"):
    d = f.read_bytes()
    for _, p in PATS:
        left += len(p.findall(d))
print(f"residual credential-shaped hits in the derivative: {left}")
sys.exit(0 if left == 0 else 2)
PY
rc=$?
echo "derivative: $OUT"
[ $rc -eq 0 ] && echo "CLEAN — safe to index" || echo "NOT CLEAN — do NOT index (rc=$rc)"
exit $rc
