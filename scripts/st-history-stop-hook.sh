#!/usr/bin/env bash
# st-history-stop-hook.sh — capture THIS agent's transcripts at its stop, and
# refresh the scrubbed derivative for the same agent (aegis-xfmon3, sattler's
# ruling 2026-09-02 05:05Z).
#
# It is the Stop-hook face of two scripts that already exist and stay usable on
# their own; it adds identity resolution and exit-code discipline, nothing else.
#
# ⚠️ THIS HOOK MUST NEVER BE ABLE TO STOP AN AGENT FROM STOPPING.
#
# Claude Code's Stop contract reads exit 2 as a BLOCKING error — the hook's
# stderr is fed back to the model and the stop is refused. st-history-scrub.sh
# exits 2 on purpose, and correctly: 2 means "residual credential-shaped hits
# survived into the derivative, do NOT index it". That is a real finding about
# an ARCHIVE, and it must not be spelled the same way as "this agent may not
# finish its turn". So 2 is remapped to 1 here — still nonzero, still loud, no
# longer a veto on the stop path.
#
# This is the aegis-ovffp hazard in miniature: the stop event is how a lead
# learns an agent finished, and a fault on the stop path is a coordinator going
# blind. A transcript archiver is not important enough to hold a turn boundary
# open, under ANY of its failure modes.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Identity comes from the launcher, exactly as shantytown.stop_event reads it.
# Unset is REFUSED rather than defaulted: the scripts below treat an empty
# --agent as a fleet-wide sweep, which on the stop path would be a 21s re-scrub
# of every agent's archive on somebody else's turn boundary.
AGENT="${SHANTY_AGENT:-}"
if [ -z "$AGENT" ]; then
  echo "st-history: \$SHANTY_AGENT unset — captured nothing" >&2
  exit 1
fi

# ⚠️ STDOUT BELONGS TO THE HARNESS ON A STOP HOOK — SEND THE CHILDREN TO STDERR.
#
# Both scripts below print a human summary on stdout when run by hand
# ("captured=1 skipped_unchanged=18 bytes=5831700 agent=ian ...", "scrubbed 1
# file(s) ..."). That is right for a terminal and WRONG here: codex parses a Stop
# hook's stdout as JSON, so 405 bytes of plain text is a FAILED hook —
#   "• Stop hook (failed)  error: hook returned invalid stop hook JSON output"
# — on every codex stop, while the hook itself exits 0 and logs rc=0 (aegis-6ab8hd).
#
# MEASURED, codex-cli 0.152.1, scripts/probe-codex-stop-stdout.sh, two arms:
#   silent hooks (0 bytes)      -> "hook: Stop Completed"
#   one hook printing plain text-> "hook: Stop Failed"
# So it is NOT that codex rejects empty stdout — the first hypothesis, and the
# expensive one, because it points at shantytown.stop_event (which correctly
# writes zero bytes on the ordinary path) instead of at this hook.
#
# The diagnostics are not lost, they are re-routed: stderr is what a human sees
# when running the hook by hand, and it is what both harnesses surface on a
# failure. Claude Code discards a non-blocking Stop hook's stdout, so nothing
# depended on these lines being there.
#
# WHY THIS IS LOUD RATHER THAN A ONE-CHARACTER FIX. The failure is silent in the
# only place anyone looks: hook.log records rc=0 (the hook DID succeed — the
# capture happened), the stop event is persisted, and the agent stops. The only
# symptom is a red line in a pane, and it is buried whenever a later hook in the
# group blocks. It was visible for ~16h before anyone read a pane at the one
# moment — an empty haul — when nothing followed it to push it off screen.
rc=0
"$HERE/st-history-capture.sh" --agent "$AGENT" >&2 || rc=$?
if [ "$rc" -eq 0 ]; then
  "$HERE/st-history-scrub.sh" --agent "$AGENT" >&2 || rc=$?
fi

# The remap. See the warning above — 2 is Claude Code's blocking code and this
# hook has no business using it.
[ "$rc" -eq 2 ] && rc=1

# A RUN LOG, and it is not test scaffolding — it is the only thing that can tell
# "the hook is live" from "the */30 timer is quietly covering for it". Both
# produce archive entries; only the hook writes here. That distinction is the
# precondition for retiring the timer at all: without it, removing the timer and
# watching captures continue would prove nothing, because a working timer and a
# working hook leave the same archive behind.
#
# Failure to log is never allowed to change the hook's verdict — an unwritable
# log is not a reason to disturb a stop.
LOG="${ST_HISTORY_DIR:-$HOME/gt/shantytown/.shanty/history}/hook.log"
{ printf '%s\t%s\t%s\trc=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$AGENT" "${SHANTY_ROLE:-?}" "$rc" \
    >> "$LOG" && chmod 600 "$LOG"; } 2>/dev/null || true

exit "$rc"
