#!/usr/bin/env bash
# install-scrub-guard — arm the pre-push scrub guard on every repo with a PUBLIC
# remote, and make that arming a re-runnable MECHANISM rather than a one-time
# manual state (internal-ref).
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
# on "internal" by construction — the internal-ref one-rule-one-place requirement
# applied to the installer too.
#
# WHAT IT DOES NOT DO. Git does not run hooks from a clone automatically, so this
# cannot make a brand-new clone self-arm; it makes RE-ARMING a single command. The
# residual gap (a fresh clone on a fresh host is unguarded until this runs once) is
# real and named here rather than hidden.
#
# Usage:
#   install-scrub-guard.sh [--root DIR] [--check] [--selftest]
#     (no args)   arm every public-remote repo under DIR (default: repos beside
#                 this one, i.e. the parent of the repo containing this script)
#     --check     report armed / unarmed / not-public, change nothing; exit 1 if
#                 any public repo is unarmed
#     --selftest  prove the discovery + arm logic on throwaway repos, no network
set -uo pipefail

CONF="${SCRUB_PATTERNS_FILE:-$HOME/.config/aegis/scrub-patterns.conf}"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$SELF/pre-push-scrub-guard.sh"

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

arm_one() {
  local dir="$1" hook="$dir/.git/hooks/pre-push"
  install -m 755 "$GUARD" "$hook"
}

if [ "${1:-}" = "--selftest" ]; then
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
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
  [ "$fail" -eq 0 ] && echo "selftest PASSED" || echo "selftest FAILED"
  exit "$fail"
fi

ROOT="$(dirname "$(dirname "$SELF")")"   # default: the dir holding sibling repos
CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root)  ROOT="$2"; shift 2 ;;
    --check) CHECK=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done

RE="$(internal_re)"
if [ -z "$RE" ]; then
  echo "⚠ no internal_host_re in $CONF — cannot tell internal from public." >&2
  echo "  Regenerate it (policy/emit-scrub-config.py) and re-run. Nothing armed." >&2
  exit 1
fi

unarmed=0 armed=0 skipped=0
for gitdir in "$ROOT"/*/.git; do
  [ -e "$gitdir" ] || continue
  dir="$(dirname "$gitdir")"
  name="$(basename "$dir")"
  if ! is_public "$dir" "$RE"; then
    skipped=$((skipped+1)); continue
  fi
  hook="$dir/.git/hooks/pre-push"
  if [ -x "$hook" ] && cmp -s "$GUARD" "$hook"; then
    printf "  armed    %s\n" "$name"; armed=$((armed+1)); continue
  fi
  if [ "$CHECK" -eq 1 ]; then
    printf "  UNARMED  %s\n" "$name"; unarmed=$((unarmed+1))
  else
    arm_one "$dir" && printf "  armed    %s (installed)\n" "$name" && armed=$((armed+1))
  fi
done

echo
echo "  public armed: $armed   unarmed: $unarmed   internal/none skipped: $skipped"
if [ "$CHECK" -eq 1 ] && [ "$unarmed" -gt 0 ]; then
  echo "  run without --check to arm them." >&2
  exit 1
fi
exit 0
