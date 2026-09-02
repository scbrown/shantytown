#!/usr/bin/env bash
# st-history-retain.sh — prune the transcript archive WITHOUT ever deleting the
# only copy of a session.
#
# DELIBERATELY A SEPARATE SCRIPT FROM CAPTURE. Capture runs unattended on a
# timer; deletion must never ride along on that. Keeping the destructive path in
# its own file means no cron line can start pruning by accident.
#
# THE POLICY IS ASYMMETRIC, and that asymmetry is the whole design:
#
#   SOURCE-LIVE   the original still exists on disk, so the capture is a BACKUP.
#                 Prunable on age — the source is the copy of record.
#
#   SOURCE-GONE   the original is gone (a codex rollout whose tmpfs CODEX_HOME
#                 was cleared, or a rotated claude session). The archive holds
#                 the ONLY copy. NEVER pruned on age. These are the sessions the
#                 epic exists for; deleting one would be the exact loss the
#                 archive was built to prevent.
#
# Age alone is a bad rule here for a reason worth stating: the sessions most
# likely to be old are the ones most likely to have lost their source, so a
# naive "delete older than N days" deletes precisely the irreplaceable ones
# first. This inverts that.
#
# DRY-RUN BY DEFAULT. --apply is required to delete anything.
set -uo pipefail

DEST="${ST_HISTORY_DIR:-$HOME/gt/shantytown/.shanty/history}"
KEEP_DAYS="${ST_HISTORY_KEEP_DAYS:-30}"   # age horizon for SOURCE-LIVE captures
KEEP_MIN="${ST_HISTORY_KEEP_MIN:-5}"      # always keep this many newest per agent
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

[ -d "$DEST" ] || { echo "no archive at $DEST"; exit 1; }

say() { echo "$(date -u +%H:%M:%SZ) $*"; }
say "archive=$DEST keep_days=$KEEP_DAYS keep_min=$KEEP_MIN apply=$APPLY"

# source path per captured file, from the append-only manifest (last line wins)
declare -A SRC
if [ -f "$DEST/manifest.tsv" ]; then
  while IFS=$'\t' read -r _ts _agent _harness base _sz src; do
    [ -n "${base:-}" ] && SRC["$base"]="${src:-}"
  done < "$DEST/manifest.tsv"
fi

now=$(date +%s)
pruned=0; kept_only_copy=0; kept_recent=0; kept_young=0; freed=0
for adir in "$DEST"/*/; do
  [ -d "$adir" ] || continue
  # newest-first, so the KEEP_MIN floor is applied to the most recent
  idx=0
  while IFS= read -r f; do
    idx=$((idx+1))
    base="$(basename "$f")"
    src="${SRC[$base]:-}"
    if [ -n "$src" ] && [ ! -e "$src" ]; then
      kept_only_copy=$((kept_only_copy+1)); continue          # irreplaceable
    fi
    if [ "$idx" -le "$KEEP_MIN" ]; then
      kept_recent=$((kept_recent+1)); continue                # floor
    fi
    age_days=$(( (now - $(stat -c %Y "$f")) / 86400 ))
    if [ "$age_days" -lt "$KEEP_DAYS" ]; then
      kept_young=$((kept_young+1)); continue
    fi
    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
    if [ "$APPLY" = 1 ]; then rm -f "$f" && { pruned=$((pruned+1)); freed=$((freed+sz)); }
    else say "WOULD prune $(basename "$adir")/$base (${age_days}d, source-live)"; pruned=$((pruned+1)); freed=$((freed+sz)); fi
  done < <(ls -1t "$adir"*.jsonl 2>/dev/null)
done

say "pruned=$pruned freed=$((freed/1048576))MB"
say "kept: only-copy=$kept_only_copy (NEVER pruned) recent-floor=$kept_recent younger-than-${KEEP_DAYS}d=$kept_young"
[ "$APPLY" = 1 ] || say "(dry run — nothing deleted; pass --apply)"
exit 0
