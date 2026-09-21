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
#
# ⚠️ THIS ONE IS ON A TIMER AND st-history-retain.sh IS NOT. That is deliberate,
# not an inconsistency somebody forgot to fix, and it must not be "harmonised"
# by arming the other one (aegis-yl5uza).
#
# The two have opposite worst cases:
#
#   st-history-retain.sh  prunes the ARCHIVE, whose whole job is to hold the copy
#                         of record for sessions whose source is gone. Its worst
#                         case is IRREVERSIBLE LOSS OF THE ONLY COPY, so it stays
#                         manual: "no cron line can start pruning by accident".
#
#   this one              prunes a SOURCE, and only when the archive already
#                         holds a copy at least as large. Its worst case is
#                         deleting a file that provably still exists elsewhere.
#
# A hard safety gate is what makes automation defensible here and is exactly what
# the archive pruner lacks. Automate the one that cannot lose data; leave the one
# that can to a human.
set -uo pipefail

ROOT="${ST_CODEX_SESSIONS_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/shantytown/codex}"
ARCHIVE="${ST_HISTORY_DIR:-$HOME/gt/shantytown/.shanty/history}"
KEEP_DAYS="${ST_CODEX_KEEP_DAYS:-30}"   # age horizon
KEEP_MIN="${ST_CODEX_KEEP_MIN:-5}"      # always keep this many newest per agent
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

say() { echo "$(date -u +%H:%M:%SZ) $*"; }

# PORTABILITY, AND WHY IT IS A CORRECTNESS FIX RATHER THAN A CONVENIENCE.
#
# This script was written GNU-only — `stat -c`, `find -printf`, and a bash-4
# associative array — and ran on a Linux timer where all three exist. On a BSD
# userland (a developer's Mac) every `stat -c` failed, the `|| echo 0` fallback
# turned each failure into a SIZE OF ZERO, so nothing ever registered as
# archived, and the run ended with a clean-looking "pruned=0 / kept: 0 0 0 0".
# A janitor that silently does nothing and reports success is the shape this
# repo keeps paying for, and the six tests covering it were red on every desk
# and green in CI — which teaches people to ignore a local red.
#
# THE `|| echo 0` WAS THE REAL DEFECT, not the flag spelling. Size is the ENTIRE
# safety gate here: a source is pruned only when the archive copy is at least as
# large. A stat that cannot be read must therefore REFUSE, never decay to 0 —
# `asz >= sz` is trivially satisfied when sz is a failure reported as zero, so
# an unreadable source would have been pruned against any archive copy at all.
# Neither helper below has a fallback value, by construction.
if stat -c %s . >/dev/null 2>&1; then
  fsize()  { stat -c %s "$1"; }
  fmtime() { stat -c %Y "$1"; }
elif stat -f %z . >/dev/null 2>&1; then
  fsize()  { stat -f %z "$1"; }
  fmtime() { stat -f %m "$1"; }
else
  say "REFUSING: neither 'stat -c' nor 'stat -f' works here, so no file's size "\
      "can be read — and size is the whole safety gate"
  exit 2
fi

# `declare -A` is bash 4+. macOS still ships bash 3.2 as /bin/bash, and under
# `set -u` the failed declaration surfaces LATER as an unbound-variable error in
# the middle of the run rather than at the top. Say it here, where the reason is
# legible, instead of leaving a confusing failure two screens down.
if ! declare -A _probe 2>/dev/null; then
  say "REFUSING: bash 4+ is required (this is ${BASH_VERSION:-unknown})"
  exit 2
fi
unset _probe

[ -d "$ROOT" ] || { say "no durable codex sessions root at $ROOT — nothing to do"; exit 0; }
# An UNREADABLE archive must never read as "no copy exists": that inverts the
# gate and turns a missing instrument into permission to delete everything.
[ -d "$ARCHIVE" ] || { say "REFUSING: archive $ARCHIVE is absent, so 'is it archived?' cannot be answered"; exit 2; }

# basename -> largest size seen in the archive. Largest, not first: the same
# session can appear under more than one agent directory after a rename, and the
# most complete copy is the one that decides.
declare -A ARCHIVED
# COUNTED EXPLICITLY. `${#ARCHIVED[@]}` on an array that is declared but still
# EMPTY trips `set -u` as an unbound variable, which is exactly what happened
# once the size reads above started failing: the run died on its own summary
# line. A counter cannot have that failure mode, and the number it reports is
# the same one.
archived_n=0
while IFS= read -r f; do
  b="$(basename "$f")"
  if ! sz=$(fsize "$f" 2>/dev/null); then
    # An archive entry we cannot measure is not evidence of anything. Skipping
    # it means the matching source stays — the safe direction, and the one the
    # whole gate is built around.
    say "  note: cannot read the size of archive entry $f — ignoring it, so "\
        "any source it would have released is KEPT"
    continue
  fi
  if [ -z "${ARCHIVED[$b]+set}" ]; then archived_n=$((archived_n+1)); fi
  [ "${ARCHIVED[$b]:-0}" -lt "$sz" ] && ARCHIVED["$b"]="$sz"
done < <(find -L "$ARCHIVE" -name '*.jsonl' -type f 2>/dev/null)

say "root=$ROOT archive_entries=$archived_n keep_days=$KEEP_DAYS keep_min=$KEEP_MIN apply=$APPLY"

now=$(date +%s)
pruned=0; freed=0; kept_unarchived=0; kept_behind=0; kept_recent=0; kept_young=0
for adir in "$ROOT"/*/; do
  [ -d "$adir" ] || continue
  agent="$(basename "$adir")"
  idx=0
  while IFS= read -r f; do
    idx=$((idx+1))
    base="$(basename "$f")"
    if ! sz=$(fsize "$f" 2>/dev/null); then
      # See the helpers above: a size we could not read must never become 0,
      # because `archived >= 0` is true of every archive copy in existence.
      say "  note: cannot read the size of $agent/$base — KEEPING it"
      kept_unarchived=$((kept_unarchived+1)); continue
    fi
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
    if ! mtime=$(fmtime "$f" 2>/dev/null); then
      say "  note: cannot read the mtime of $agent/$base — KEEPING it"
      kept_young=$((kept_young+1)); continue
    fi
    age_days=$(( (now - mtime) / 86400 ))
    if [ "$age_days" -lt "$KEEP_DAYS" ]; then
      kept_young=$((kept_young+1)); continue
    fi
    if [ "$APPLY" = 1 ]; then rm -f "$f" && { pruned=$((pruned+1)); freed=$((freed+sz)); }
    else say "WOULD prune $agent/$base (${age_days}d, archived ${asz}B >= ${sz}B)"
         pruned=$((pruned+1)); freed=$((freed+sz)); fi
    # NEWEST FIRST, and the ordering is load-bearing: `idx` is what the
    # keep-the-N-newest floor counts. `find -printf` is GNU-only and was the
    # second thing that made this script a no-op off Linux, so the timestamp is
    # taken with the same portable helper everything else here uses.
  done < <(find -L "$adir" -name 'rollout-*.jsonl' -type f -print 2>/dev/null \
           | while IFS= read -r rf; do
               rt=$(fmtime "$rf" 2>/dev/null) || rt=0
               printf '%s %s\n' "$rt" "$rf"
             done | sort -rn | cut -d' ' -f2-)
done

# Date directories are created per day and are worthless once empty. Only under
# --apply, and only EMPTY ones: -delete on a non-empty dir is a no-op by design.
[ "$APPLY" = 1 ] && find "$ROOT" -mindepth 2 -type d -empty -delete 2>/dev/null

say "pruned=$pruned freed=$((freed/1048576))MB"
say "kept: unarchived=$kept_unarchived (NEVER pruned) archive-behind=$kept_behind recent-floor=$kept_recent younger-than-${KEEP_DAYS}d=$kept_young"
[ "$APPLY" = 1 ] || say "(dry run — nothing deleted; pass --apply)"
exit 0
