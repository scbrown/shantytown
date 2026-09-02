#!/usr/bin/env bash
# st-codex-sessions-retain.sh — prune the DURABLE codex sessions root, and never
# a rollout the archive does not already hold.
#
# WHY THIS EXISTS (aegis-4tbo84). aegis-tx4fiy moved each card's codex
# `sessions/` off tmpfs onto durable state, so a rollout survives a reboot
# instead of depending on a capture winning a race against one. That was the
# point. It also removed a janitor nobody ever had to think about:
#
#   before   tmpfs   self-cleaning at every reboot
#   after    disk    nothing prunes it, ever
#
# A DIFFERENT SCRIPT FROM st-history-retain.sh, AND THE GATE IS THE MIRROR
# IMAGE OF ITS ONE. That script prunes the ARCHIVE and refuses whenever the
# archive holds the ONLY copy. This one prunes the SOURCE, and refuses unless
# the archive DOES hold a copy. Same principle stated from opposite ends --
# never delete the last copy of a session -- so keeping them apart is what
# stops one file carrying two contradictory-looking rules.
#
# THE SIZE CHECK IS NOT PARANOIA. Capture re-copies a rollout when the source
# GROWS, because a live session's jsonl is appended to. So an archive copy can
# be a real file, with the right name, and still be BEHIND the source. Pruning
# on name alone would silently truncate the session to whatever had been
# captured last -- a loss that leaves a plausible-looking file behind, which is
# the worst shape for a reader to have to notice. An archive copy is proof only
# if it is at least as large as what it would replace.
#
# DRY-RUN BY DEFAULT. --apply is required to delete anything.
set -uo pipefail

ROOT="${ST_CODEX_SESSIONS_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/shantytown/codex}"
ARCHIVE="${ST_HISTORY_DIR:-$HOME/gt/shantytown/.shanty/history}"
KEEP_DAYS="${ST_CODEX_KEEP_DAYS:-30}"   # age horizon
KEEP_MIN="${ST_CODEX_KEEP_MIN:-5}"      # always keep this many newest per agent
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

say() { echo "$(date -u +%H:%M:%SZ) $*"; }

[ -d "$ROOT" ] || { say "no durable codex sessions root at $ROOT — nothing to do"; exit 0; }
# An UNREADABLE archive must never read as "no copy exists": that inverts the
# gate and turns a missing instrument into permission to delete everything.
[ -d "$ARCHIVE" ] || { say "REFUSING: archive $ARCHIVE is absent, so 'is it archived?' cannot be answered"; exit 2; }

# basename -> largest size seen in the archive. Largest, not first: the same
# session can appear under more than one agent directory after a rename, and the
# most complete copy is the one that decides.
declare -A ARCHIVED
while IFS= read -r f; do
  b="$(basename "$f")"; sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  [ "${ARCHIVED[$b]:-0}" -lt "$sz" ] && ARCHIVED["$b"]="$sz"
done < <(find -L "$ARCHIVE" -name '*.jsonl' -type f 2>/dev/null)

say "root=$ROOT archive_entries=${#ARCHIVED[@]} keep_days=$KEEP_DAYS keep_min=$KEEP_MIN apply=$APPLY"

now=$(date +%s)
pruned=0; freed=0; kept_unarchived=0; kept_behind=0; kept_recent=0; kept_young=0
for adir in "$ROOT"/*/; do
  [ -d "$adir" ] || continue
  agent="$(basename "$adir")"
  idx=0
  while IFS= read -r f; do
    idx=$((idx+1))
    base="$(basename "$f")"
    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
    asz="${ARCHIVED[$base]:-}"
    if [ -z "$asz" ]; then
      kept_unarchived=$((kept_unarchived+1)); continue        # the only copy
    fi
    if [ "$asz" -lt "$sz" ]; then
      kept_behind=$((kept_behind+1)); continue                # archive is stale
    fi
    if [ "$idx" -le "$KEEP_MIN" ]; then
      kept_recent=$((kept_recent+1)); continue
    fi
    age_days=$(( (now - $(stat -c %Y "$f")) / 86400 ))
    if [ "$age_days" -lt "$KEEP_DAYS" ]; then
      kept_young=$((kept_young+1)); continue
    fi
    if [ "$APPLY" = 1 ]; then rm -f "$f" && { pruned=$((pruned+1)); freed=$((freed+sz)); }
    else say "WOULD prune $agent/$base (${age_days}d, archived ${asz}B >= ${sz}B)"
         pruned=$((pruned+1)); freed=$((freed+sz)); fi
  done < <(find -L "$adir" -name 'rollout-*.jsonl' -type f -printf '%T@ %p\n' 2>/dev/null \
           | sort -rn | cut -d' ' -f2-)
done

# Date directories are created per day and are worthless once empty. Only under
# --apply, and only EMPTY ones: -delete on a non-empty dir is a no-op by design.
[ "$APPLY" = 1 ] && find "$ROOT" -mindepth 2 -type d -empty -delete 2>/dev/null

say "pruned=$pruned freed=$((freed/1048576))MB"
say "kept: unarchived=$kept_unarchived (NEVER pruned) archive-behind=$kept_behind recent-floor=$kept_recent younger-than-${KEEP_DAYS}d=$kept_young"
[ "$APPLY" = 1 ] || say "(dry run — nothing deleted; pass --apply)"
exit 0
