#!/usr/bin/env bash
# mirror-sync-github.sh — forward-only, squashed + scrubbed sync of the public
# mirror from the working remote's main.
#
# THE MIRROR PROTOCOL (decided on the fork-reconciliation ticket, 2026-07-23):
# the public mirror advances by SQUASHED, SCRUBBED release pushes — never by
# mirroring the raw commit stream. Why each word:
#   FORWARD-ONLY  the past is never rewritten; no force-push. Each sync is one
#                 new commit parented on the mirror's current tip.
#   SQUASHED      working-remote commit messages carry internal context by
#                 deliberate convention; one clean message supersedes them.
#   SCRUBBED      internal ticket references in FILE CONTENT are neutralized
#                 to `internal-ref` before publishing. Citing a ticket in
#                 internal source is correct; the same ref public is
#                 unresolvable noise (the pre-push guard blocks it, and the
#                 guard is right). The scrub happens here, at the mirror seam,
#                 so the internal citation convention stays intact.
#
# The scrub regex mirrors the governed pattern catalogue's ticket rule
# (spelled with a lookahead here because python has one; the guard's ERE
# spelling uses a trailing-char guard for the same semantics — a ticket ref
# is a TERMINAL token, so ticket-shaped prefixes of longer hyphenated
# identifiers, e.g. pane names, are not matches).
#
#   scripts/mirror-sync-github.sh            # dry-run: report what would sync
#   scripts/mirror-sync-github.sh --push     # build the scrubbed squash + push
#
# Idempotent: when the mirror already carries the current scrubbed tree, both
# modes report in-sync and exit 0 without committing anything.
set -euo pipefail

WORK_REMOTE="${WORK_REMOTE:-origin}"
MIRROR_REMOTE="${MIRROR_REMOTE:-github}"
BRANCH="${BRANCH:-main}"

# Paths never scrubbed: the scrub/ratchet guards' own fixture files plant
# ticket-shaped strings ON PURPOSE (same exemption list as the pre-push guard).
EXEMPT_RE='(pre-push-scrub-guard\.sh|no_internal_identifiers\.rs|test_internal_identifier_ratchet\.py|test_no_internal_ids_in_output\.py)$'

git fetch -q "$WORK_REMOTE" "$BRANCH"
git fetch -q "$MIRROR_REMOTE" "$BRANCH"
SRC="$WORK_REMOTE/$BRANCH"
DST="$MIRROR_REMOTE/$BRANCH"

# Precondition: the mirror must carry nothing the working remote lacks. In
# steady state the mirror tip is a chain of THIS SCRIPT'S sync commits on top
# of some commit that IS an ancestor of the working branch — a raw
# is-ancestor test fails forever after the first sync (the squash commits
# exist only mirror-side; learned on first re-run). Walk down through sync
# commits; whatever they sit on must be an ancestor.
BASE=$(git rev-parse "$DST")
while ! git merge-base --is-ancestor "$BASE" "$SRC"; do
    subj=$(git log -1 --format=%s "$BASE")
    case "$subj" in
        "sync: forward-only scrubbed mirror sync"*) BASE=$(git rev-parse "$BASE^") ;;
        *)
            echo "✗ $DST carries non-sync commit(s) the working remote lacks" >&2
            echo "  (tip of foreign history: $(git rev-parse --short "$BASE") \"$subj\")." >&2
            echo "  Reconcile INWARD first (merge mirror-side commits into $SRC)," >&2
            echo "  then re-run. Forward-only sync never discards mirror history." >&2
            exit 1
            ;;
    esac
done

behind=$(git rev-list --count "$DST..$SRC")
echo "mirror is $behind commit(s) behind $SRC"

# Build the scrubbed tree in a temp index: start from the source tree, rewrite
# any text blob whose content carries a ticket ref (outside exempt paths).
export GIT_INDEX_FILE=$(mktemp)
trap 'rm -f "$GIT_INDEX_FILE"' EXIT
git read-tree "$SRC^{tree}"

SCRUBBED=$(git ls-tree -r "$SRC^{tree}" | python3 -c '
import re, subprocess, sys
TICKET = re.compile(rb"\b(?:aegis|hq|gassy|qp)-[a-z0-9]{3,6}\b(?!-)")
EXEMPT = re.compile(sys.argv[1].encode())
changed = 0
for line in sys.stdin.buffer:
    meta, path = line.rstrip(b"\n").split(b"\t", 1)
    mode, kind, sha = meta.split()
    if kind != b"blob" or EXEMPT.search(path):
        continue
    blob = subprocess.run(["git", "cat-file", "blob", sha.decode()],
                          capture_output=True, check=True).stdout
    if b"\0" in blob[:8000] or not TICKET.search(blob):
        continue
    new = TICKET.sub(b"internal-ref", blob)
    h = subprocess.run(["git", "hash-object", "-w", "--stdin"],
                       input=new, capture_output=True, check=True).stdout.strip()
    subprocess.run(["git", "update-index", "--cacheinfo",
                    f"{mode.decode()},{h.decode()},{path.decode()}"], check=True)
    changed += 1
print(changed)
' "$EXEMPT_RE")
echo "scrubbed $SCRUBBED file(s) (ticket refs -> internal-ref)"

TREE=$(git write-tree)
if [ "$(git rev-parse "$DST^{tree}")" = "$TREE" ]; then
    echo "✓ mirror already carries this scrubbed tree — in sync, nothing to push"
    exit 0
fi

if [ "${1:-}" != "--push" ]; then
    echo "dry-run: would push scrubbed tree $TREE (parent $(git rev-parse --short "$DST"))."
    echo "run with --push to publish."
    exit 0
fi

MSG_FILE=$(mktemp)
cat > "$MSG_FILE" <<EOF
sync: forward-only scrubbed mirror sync ($behind commits squashed)

Content-identical to the working tree except that internal ticket
references in file content are neutralized to \`internal-ref\` (they cite a
tracker the public cannot resolve). See scripts/mirror-sync-github.sh for
the mirror protocol.
EOF
SHA=$(git commit-tree "$TREE" -p "$DST" -F "$MSG_FILE")
rm -f "$MSG_FILE"
echo "squash commit: $SHA"
git push "$MIRROR_REMOTE" "$SHA:$BRANCH"
git fetch -q "$MIRROR_REMOTE" "$BRANCH"
[ "$(git rev-parse "$DST^{tree}")" = "$TREE" ] \
    && echo "✓ pushed and READ BACK: $DST now carries the scrubbed tree" \
    || { echo "✗ push reported success but $DST tree differs — investigate before retrying" >&2; exit 1; }
