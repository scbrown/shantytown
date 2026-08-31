#!/usr/bin/env bash
# install-scrub-guard — arm the pre-push scrub guard on every repo with a PUBLIC
# remote, and make that arming a re-runnable MECHANISM rather than a one-time
# manual state (aegis-oauk).
#
# WHY THIS EXISTS AND NOT JUST `cp`. The bead this closes is about decay: a scrub
# is a state and states rot. A hook hand-installed into .git/hooks is exactly such
# a state — it does not survive a reclone, and nobody re-does it by morning. This
# script is the anti-decay step for the INSTALL itself: run it any time (after a
# clone, on a new host, from cron) and every public-remote repo is armed again.
# It is idempotent and reports what it changed.
#
# WHAT COUNTS AS PUBLIC. A repo is public if ANY of its remotes is NOT the
# internal forge. The internal-forge pattern is read from the SAME generated
# config the guard uses (internal_host_re), so this installer and the guard agree
# on "internal" by construction — the aegis-mqnl one-rule-one-place requirement
# applied to the installer too.
#
# INSTALLATION BY REFERENCE. Every repo hook is an absolute symlink to one
# ownership-neutral, read-only source under ~/.local/share. A guard update is
# published there once and every armed repo sees the same inode immediately;
# there are no per-repo copies to decay independently. --check also compares the
# neutral source with this reviewed source, so a missed publish is loud.
#
# Usage:
#   install-scrub-guard.sh [--root DIR]... [--check] [--selftest]
#     (no args)   arm every public-remote repo under EVERY default root:
#                 ~/gt (or $GT_ROOT), ~/workspace and ~/src. All three, because a
#                 single default root left ~/workspace dark and reported green
#                 while doing it, and the hourly --check — the arm that ESCALATES
#                 — saw neither ~/workspace nor ~/src (aegis-v7joru).
#     --root DIR  repeatable; REPLACES the defaults rather than adding to them
#     --check     report armed / unarmed / not-public, change nothing; exit 1 if
#                 any public repo is unarmed
#     --selftest  prove the discovery + arm logic on throwaway repos, no network
set -uo pipefail

CONF="${SCRUB_PATTERNS_FILE:-$HOME/.config/aegis/scrub-patterns.conf}"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$SELF/pre-push-scrub-guard.sh"
SOURCE_DIR="${SCRUB_GUARD_SOURCE_DIR:-$HOME/.local/share/shantytown-guard}"
LIVE_GUARD="$SOURCE_DIR/pre-push-scrub-guard.sh"

internal_re() {
  # The forge that may receive internal names, from the generated config. If the
  # config is absent we cannot tell internal from public, so we refuse to guess
  # (a wrong guess arms nothing or arms the internal forge pointlessly).
  local re=""
  [ -r "$CONF" ] && while IFS='=' read -r k v; do
    [ "$k" = "internal_host_re" ] && re="$v"
  done < "$CONF"
  printf '%s' "$re"
}

is_public() {
  # A repo is public if it has a remote whose URL does NOT match the internal
  # forge. No remotes -> not public (nothing to leak to). We read every remote URL
  # from config (fetch and push URLs both), because `remote get-url` needs a name
  # and a repo can have several remotes.
  local dir="$1" re="$2" url
  while read -r _k url; do
    [ -n "$url" ] || continue
    printf '%s' "$url" | grep -qE "$re" || return 0
  done < <(git -C "$dir" config --get-regexp '^remote\..*\.(push)?url$' 2>/dev/null)
  return 1
}

publish_source() {
  local staged="$SOURCE_DIR/.pre-push-scrub-guard.sh.new"
  mkdir -p "$SOURCE_DIR"
  chmod u+w "$LIVE_GUARD" 2>/dev/null || true
  install -m 0555 "$GUARD" "$staged" || return
  mv -f "$staged" "$LIVE_GUARD"
  chmod a-w "$LIVE_GUARD"
}

hook_path() {
  local common
  common="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return
  printf '%s/hooks/pre-push\n' "$common"
}

arm_one() {
  local dir="$1" hook
  hook="$(hook_path "$dir")" || return 1
  mkdir -p "$(dirname "$hook")"
  ln -sfn "$LIVE_GUARD" "$hook"
}

is_armed() {
  local dir="$1" hook target
  hook="$(hook_path "$dir")" || return 1
  [ -L "$hook" ] || return 1
  target="$(readlink -f "$hook")" || return 1
  [ "$target" = "$LIVE_GUARD" ] || return 1
  [ -x "$target" ] && [ ! -w "$target" ] || return 1
  cmp -s "$GUARD" "$target"
}

if [ "${1:-}" = "--selftest" ]; then
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  SOURCE_DIR="$tmp/neutral-source"
  LIVE_GUARD="$SOURCE_DIR/pre-push-scrub-guard.sh"
  re='forge\.invalid'
  fail=0
  # A "public" repo: remote on a non-invalid host.
  git init -q "$tmp/pub"; git -C "$tmp/pub" remote add origin https://example.com/x.git
  # An "internal" repo: remote on the reserved internal forge only.
  git init -q "$tmp/int"; git -C "$tmp/int" remote add origin ssh://git@forge.invalid/x.git
  # A repo with no remote.
  git init -q "$tmp/bare"
  is_public "$tmp/pub" "$re" && echo "ok   public repo detected" || { echo "FAIL public missed"; fail=1; }
  is_public "$tmp/int" "$re" && { echo "FAIL internal treated as public"; fail=1; } || echo "ok   internal repo skipped"
  is_public "$tmp/bare" "$re" && { echo "FAIL no-remote treated as public"; fail=1; } || echo "ok   no-remote skipped"
  publish_source || { echo "FAIL neutral source publish"; exit 1; }
  arm_one "$tmp/pub" || { echo "FAIL public repo arm"; exit 1; }
  if is_armed "$tmp/pub"; then
    echo "ok   by-reference hook is current and locked"
  else
    echo "FAIL by-reference hook did not arm"; fail=1
  fi

  # Recreate the old defect: replace the link with a byte-identical copy. It
  # passes the guard's own behavioural selftest, but installation validation
  # must reject it because it can now rot independently.
  pub_hook="$(hook_path "$tmp/pub")"
  rm -f "$pub_hook"
  cp "$LIVE_GUARD" "$pub_hook"
  chmod 0755 "$pub_hook"
  if is_armed "$tmp/pub"; then
    echo "FAIL stale copied hook reported armed"; fail=1
  else
    echo "ok   stale copied hook is detected"
  fi

  # A changed reviewed source without republishing must also go red. This is
  # the cadence acceptance: source drift cannot hide behind healthy symlinks.
  GUARD="$tmp/reviewed-new"
  cp "$LIVE_GUARD" "$GUARD"
  chmod u+w "$GUARD"
  printf '\n# synthetic source update\n' >> "$GUARD"
  arm_one "$tmp/pub"
  if is_armed "$tmp/pub"; then
    echo "FAIL unpublished source change reported current"; fail=1
  else
    echo "ok   unpublished source change is detected"
  fi
  # ── MULTI-ROOT DISCOVERY (aegis-v7joru) ────────────────────────────────────
  # The arms above all call arm_one on a known path, so NONE of them exercise
  # the discovery loop — which is exactly why a single default root sat here
  # unnoticed while ~/workspace went unswept and the summary reported green.
  # A selftest that never asks "did you FIND it" cannot catch a blind sweep.
  #
  # Two throwaway roots, one public repo in each. The sweep must see BOTH, and
  # must report a nonzero repos-seen — a scan of nothing also prints 0 unarmed.
  mkdir -p "$tmp/rootA" "$tmp/rootB"
  git init -q "$tmp/rootA/alpha"; git -C "$tmp/rootA/alpha" remote add origin https://example.com/a.git
  git init -q "$tmp/rootB/beta";  git -C "$tmp/rootB/beta"  remote add origin https://example.com/b.git
  sweep=$("$SELF/install-scrub-guard.sh" --root "$tmp/rootA" --root "$tmp/rootB" --check 2>&1)
  if printf '%s' "$sweep" | grep -q 'alpha' && printf '%s' "$sweep" | grep -q 'beta'; then
    echo "ok   discovery reaches BOTH roots (repeatable --root)"
  else
    echo "FAIL a repo outside the first root was not discovered"; fail=1
  fi
  if printf '%s' "$sweep" | grep -qE 'repos seen: [1-9]'; then
    echo "ok   sweep reports a nonzero repos-seen (a scan of nothing cannot pass)"
  else
    echo "FAIL sweep did not report repos seen"; fail=1
  fi
  # And the DEFAULTS must name ~/workspace, or the fix is a flag nobody passes
  # rather than a behaviour change. Checked against the source text because
  # DEFAULT_ROOTS is not yet assigned this early in the script.
  if grep -q '^DEFAULT_ROOTS=(.*workspace' "$SELF/install-scrub-guard.sh"; then
    echo "ok   default roots include ~/workspace"
  else
    echo "FAIL default roots do not include ~/workspace"; fail=1
  fi

  [ "$fail" -eq 0 ] && echo "selftest PASSED" || echo "selftest FAILED"
  exit "$fail"
fi

# DEFAULT ROOTS ARE PLURAL (aegis-v7joru). This was a single ROOT defaulting to
# the dir holding sibling repos — i.e. ~/gt — and NOTHING ELSE WAS EVER SWEPT.
# Public-remote repos also live under ~/workspace, and on 2026-08-31 all 13 of
# them were unarmed while the default sweep reported a clean 16 armed / 0 unarmed:
#
#     --check                      public armed: 16   unarmed:  0   EXIT 0
#     --check --root ~/workspace   public armed:  0   unarmed: 13   EXIT 1
#
# 16-for-16 was a true number and a green report that was structurally blind to a
# whole tree. ~/workspace/gastown is a PUBLIC scbrown repo and had no pre-push
# hook at all; a push went to it that day with no scrub guard in the path.
#
# A default you must remember to override is the thing that failed here, so the
# fix is to sweep both by default rather than to document the flag harder.
# --root is now REPEATABLE and REPLACES the defaults when given, so an explicit
# invocation still means exactly what it says.
# The roots are NAMED, not derived from where this file happens to sit. The old
# default was `dirname(dirname($SELF))`, so the sweep followed the SCRIPT: run
# from the canonical ~/gt/shantytown it swept ~/gt, but run from a worktree at
# ~/gt/shantytown-wt/<agent> it swept ~/gt/shantytown-wt — a tree of worktrees —
# and reported a confident green about the wrong thing. Measured 2026-08-31 while
# fixing the ~/workspace hole; same defect class, found only because this version
# prints the roots it swept.
#
# Script-relative survives only as a LAST RESORT, used when neither named root
# exists on this host. Keeping it in the normal list would reinstate the very
# location-dependence described above: run from a worktree it appends a tree of
# worktrees, so the sweep's scope would again depend on which copy you invoked.
# The list is de-duplicated by real path.
DEFAULT_ROOTS=("${GT_ROOT:-$HOME/gt}" "$HOME/workspace" "$HOME/src")
FALLBACK_ROOT="$(dirname "$(dirname "$SELF")")"
ROOTS=()
CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root)  ROOTS+=("$2"); shift 2 ;;
    --check) CHECK=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done
[ ${#ROOTS[@]} -eq 0 ] && ROOTS=("${DEFAULT_ROOTS[@]}")

# A root that does not exist is skipped quietly (not every host has ~/workspace),
# but a root that exists and holds NO repo at all is reported: silence there is
# indistinguishable from the blindness this change exists to remove.
kept=()
for r in "${ROOTS[@]}"; do
  [ -d "$r" ] || continue
  r=$(cd "$r" 2>/dev/null && pwd -P) || continue
  dup=0
  for k in ${kept[@]+"${kept[@]}"}; do [ "$k" = "$r" ] && dup=1 && break; done
  [ "$dup" -eq 0 ] && kept+=("$r")
done
if [ ${#kept[@]} -eq 0 ] && [ -d "$FALLBACK_ROOT" ]; then
  echo "⚠ neither default root exists; falling back to $FALLBACK_ROOT" >&2
  kept=("$(cd "$FALLBACK_ROOT" && pwd -P)")
fi
if [ ${#kept[@]} -eq 0 ]; then
  echo "no sweepable root exists (looked in: ${ROOTS[*]})" >&2
  exit 1
fi
ROOTS=("${kept[@]}")

RE="$(internal_re)"
if [ -z "$RE" ]; then
  echo "⚠ no internal_host_re in $CONF — cannot tell internal from public." >&2
  echo "  Regenerate it (policy/emit-scrub-config.py) and re-run. Nothing armed." >&2
  exit 1
fi

source_current=0
if [ -x "$LIVE_GUARD" ] && [ ! -w "$LIVE_GUARD" ] && cmp -s "$GUARD" "$LIVE_GUARD"; then
  source_current=1
elif [ "$CHECK" -eq 1 ]; then
  echo "  SOURCE STALE  $LIVE_GUARD" >&2
else
  publish_source || { echo "failed to publish locked guard source" >&2; exit 1; }
  source_current=1
fi

unarmed=0 armed=0 skipped=0 seen=0
for ROOT in "${ROOTS[@]}"; do
  printf "  [%s]\n" "$ROOT"
  for gitdir in "$ROOT"/*/.git; do
    [ -e "$gitdir" ] || continue
    seen=$((seen+1))
    dir="$(dirname "$gitdir")"
    name="$(basename "$dir")"
    if ! is_public "$dir" "$RE"; then
      skipped=$((skipped+1)); continue
    fi
    if [ "$source_current" -eq 1 ] && is_armed "$dir"; then
      printf "  armed    %s\n" "$name"; armed=$((armed+1)); continue
    fi
    if [ "$CHECK" -eq 1 ]; then
      printf "  UNARMED  %s\n" "$name"; unarmed=$((unarmed+1))
    else
      arm_one "$dir" && printf "  armed    %s (installed)\n" "$name" && armed=$((armed+1))
    fi
  done
done

echo
# Name the roots in the summary. The old summary reported a count with no scope,
# so "16 armed, 0 unarmed" read as "the host is covered" when it meant "one tree
# is covered" — a number that cannot state what it did not look at.
echo "  roots swept: ${ROOTS[*]}"
echo "  repos seen: $seen"
echo "  public armed: $armed   unarmed: $unarmed   internal/none skipped: $skipped"
if [ "$CHECK" -eq 1 ] && { [ "$unarmed" -gt 0 ] || [ "$source_current" -eq 0 ]; }; then
  echo "  run without --check to arm them." >&2
  exit 1
fi
exit 0
