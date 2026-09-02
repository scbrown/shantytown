#!/usr/bin/env bash
# st-history-capture.sh — copy agent transcripts off VOLATILE storage into a
# durable per-agent archive (aegis-xfmon3, Stiwi directive 2026-09-01).
#
# WHY THIS IS URGENT RATHER THAN TIDY. The epic said codex rollouts are "lost on
# daemon restart". Measured 2026-09-02, the truth is worse:
#
#     CODEX_HOME=/run/user/1000/shantytown/codex/<agent>
#     $ stat -f -c %T /run/user/1000   ->  tmpfs
#
# /run/user/<uid> is RAM. Every codex transcript on this host — 77 rollouts,
# 1.2 GB, 10 agents, 2026-08-29 onward — dies on reboot, not merely on a daemon
# restart. That is why ian's session was unrecoverable: not "not exported in
# time", but never on durable storage at all. ~/.codex/sessions still holds
# rollouts, but its newest is 2026-08-15 — stale by 18 days, because the running
# app-servers point CODEX_HOME elsewhere.
#
# Claude is the easy half: ~/.claude/projects/<proj>/<session>.jsonl is already on
# disk and already contains reasoning (measured: 415 `thinking` blocks in one
# live session), so capture is a copy plus a manifest.
#
# IDEMPOTENT AND NON-DESTRUCTIVE. It never deletes a source, never overwrites a
# byte-identical capture, and re-copies only when the source has grown — a live
# session's jsonl is appended to, so the newest capture must win.
set -uo pipefail

DEST="${ST_HISTORY_DIR:-$HOME/gt/shantytown/.shanty/history}"
CODEX_ROOT="${ST_CODEX_ROOT:-/run/user/$(id -u)/shantytown/codex}"
CLAUDE_ROOT="${ST_CLAUDE_ROOT:-$HOME/.claude/projects}"
DRY=0
ONLY_AGENT=""
# Defined BEFORE the arg loop: the loop refuses an empty --agent and needs to
# say so, and a function referenced before its definition is a runtime failure.
say() { echo "$(date -u +%H:%M:%SZ) $*"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    # Scope to ONE agent. This exists for the Stop hook: a hook runs on EVERY
    # agent's stop, so it must cost that agent only what their own session
    # costs. A full sweep is 3.1s measured — not ruinous, but paying it on every
    # stop fleet-wide to re-skip 300+ unchanged files of other people's sessions
    # is work nobody asked for.
    # An EMPTY value is REFUSED, never treated as "no filter". `--agent ""`
    # and no --agent at all would otherwise be the same world to this script,
    # and one of them is a bug: the Stop hook passes "$SHANTY_AGENT", so an
    # unset identity would silently turn a one-agent capture into a fleet-wide
    # sweep on every stop — the expensive wrong thing, done quietly.
    --agent) shift
             if [ -z "${1:-}" ]; then
               say "REFUSED: --agent needs a name (got empty). Captured nothing."
               say "  In the Stop hook this means \$SHANTY_AGENT is unset."
               exit 1
             fi
             ONLY_AGENT="$1" ;;
    *) ;;
  esac
  shift
done

copied=0; skipped=0; bytes=0

capture() {   # <agent> <harness> <src>
  local agent="$1" harness="$2" src="$3"
  [ -n "$ONLY_AGENT" ] && [ "$agent" != "$ONLY_AGENT" ] && return
  local base; base="$(basename "$src")"
  local dir="$DEST/$agent"; local dst="$dir/$base"
  if [ -f "$dst" ] && [ "$(stat -c %s "$dst" 2>/dev/null)" -ge "$(stat -c %s "$src" 2>/dev/null)" ]; then
    skipped=$((skipped+1)); return
  fi
  [ "$DRY" = 1 ] && { say "WOULD capture $agent/$harness/$base"; copied=$((copied+1)); return; }
  mkdir -p "$dir" || return
  # 0700/0600 is a RULING, not a preference (aegis-ra6hvt): the raw archive holds
  # credential-shaped strings pasted into transcripts in error, so it stays
  # owner-only and un-indexed while a scrubbed derivative is what gets indexed.
  # Enforced HERE rather than by a one-shot chmod, because every future capture
  # would otherwise land at the default umask and silently undo the lock.
  chmod 700 "$DEST" "$dir" 2>/dev/null || true
  # The staging name carries THIS PROCESS'S pid. Two capturers can legitimately
  # target the same live jsonl at once — the Stop hook and the interim timer
  # overlap by design until the timer is retired — and a SHARED .part is two
  # writers interleaving into one file that is then published as if whole. With
  # a per-pid stage the worst case is last-writer-wins between two COMPLETE
  # copies, which is the same outcome as running them a second apart.
  local part="$dst.$$.part"
  cp -p "$src" "$part" 2>/dev/null || { say "FAILED $src"; rm -f "$part"; return; }
  chmod 600 "$part" 2>/dev/null || true
  mv -f "$part" "$dst"
  local sz; sz="$(stat -c %s "$dst" 2>/dev/null || echo 0)"
  bytes=$((bytes+sz)); copied=$((copied+1))
  # One manifest line per capture. Append-only: the history of a session's growth
  # is itself evidence, and rewriting it would lose when a session was first seen.
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$agent" "$harness" "$base" "$sz" "$src" \
    >> "$DEST/manifest.tsv"
  chmod 600 "$DEST/manifest.tsv" 2>/dev/null || true
}

[ "$DRY" = 1 ] || mkdir -p "$DEST"

# codex — the volatile half, captured first because it is the one that vanishes
if [ -d "$CODEX_ROOT" ]; then
  while IFS= read -r f; do
    agent="$(sed -E "s#^$CODEX_ROOT/([^/]+)/.*#\1#" <<<"$f")"
    capture "$agent" codex "$f"
  # -L FOLLOWS SYMLINKS, and it is load-bearing rather than defensive.
  # aegis-tx4fiy redirects each card's `sessions/` to durable state with a
  # symlink, so from this root the rollouts are now BEHIND one. Plain `find`
  # does not descend into a symlinked directory: measured against the real
  # layout, the same sweep returned 0 without -L and 1 with it. That is the
  # worst shape a capture failure can take -- it reports success having
  # archived nothing, and the thing it stopped archiving is the half that does
  # not survive a reboot.
  done < <(find -L "$CODEX_ROOT" -name 'rollout-*.jsonl' -type f 2>/dev/null)
else
  say "NOTE: no codex root at $CODEX_ROOT"
fi

# claude — CREW PROJECTS ONLY, deliberately.
#
# Claude's jsonl is already on durable disk, so capturing it is about having ONE
# archive that answers for both harnesses — not about rescue. Sweeping all 52
# project dirs would copy 666 MB of already-safe data, most of it not an agent
# transcript at all (a dry run counted 3879 files). Scoped to the crew project
# path so "agent chat history" means what it says; widen deliberately if a
# non-crew project ever needs archiving.
if [ -d "$CLAUDE_ROOT" ]; then
  while IFS= read -r f; do
    proj="$(basename "$(dirname "$f")")"
    agent="${proj##*crew-}"
    capture "$agent" claude "$f"
  done < <(find "$CLAUDE_ROOT" -maxdepth 2 -path '*crew-*' -name '*.jsonl' -type f 2>/dev/null)
fi

say "captured=$copied skipped_unchanged=$skipped bytes=$bytes${ONLY_AGENT:+ agent=$ONLY_AGENT} dest=$DEST"
[ "$DRY" = 1 ] && say "(dry run — nothing written)"
exit 0
