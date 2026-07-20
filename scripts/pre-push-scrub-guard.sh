#!/usr/bin/env bash
# pre-push-scrub-guard — refuse a PUBLIC push that introduces a NEW internal name.
#
# Install:  ln -sf ../../scripts/pre-push-scrub-guard.sh .git/hooks/pre-push
# Verify:   scripts/pre-push-scrub-guard.sh --selftest
#
# WHY IT IS NEW-OCCURRENCE, NOT ANY-OCCURRENCE, and this is the whole design:
# dolt.lan, quipu.svc and mayor are ALREADY on the public remote. A hook that
# refuses any occurrence would fire on every push from day one, be recognised as
# broken, and be disabled within a day — leaving no guard at all. Refusing only
# what a push ADDS means it is silent on the existing debt and loud on new leaks,
# so it survives long enough to be useful.
#
# It fires only on remotes that are NOT the internal forge. Pushing internal names
# to the internal forge is not a leak; that is where they belong.
set -uo pipefail

INTERNAL_HOST_RE='git\.lan'
# Names that must not newly reach a public remote. Hostnames and infra
# identifiers, not English words — a term that shows up in ordinary prose would
# make this cry wolf.
PATTERNS='dolt\.lan|quipu\.svc|bobbin-mcp\.svc|homelab-mcp\.svc|forgejo-mcp\.svc|agent-mcp\.svc|\bkota\b|\bluvu\b|\bvati\b|\bgoldblum\.lan\b|monitoring\.lan|persistence\.lan|matrix\.lan|traefik\.lan|secrets\.lan|message\.lan|dns\.lan|192\.168\.[0-9]+\.[0-9]+'

if [ "${1:-}" = "--selftest" ]; then
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  printf 'clean line\n' > "$tmp/clean"
  printf 'connect to dolt.lan:3306\n' > "$tmp/dirty"
  fail=0
  if grep -nEq "$PATTERNS" "$tmp/dirty"; then echo "ok   detects an internal name"; else echo "FAIL misses an internal name"; fail=1; fi
  if grep -nEq "$PATTERNS" "$tmp/clean"; then echo "FAIL fires on clean text"; fail=1; else echo "ok   silent on clean text"; fi
  if printf 'ssh://git@git.lan/stiwi/x.git' | grep -qE "$INTERNAL_HOST_RE"; then echo "ok   recognises the internal forge"; else echo "FAIL internal forge unrecognised"; fail=1; fi
  if printf 'git@github.com:scbrown/x.git' | grep -qE "$INTERNAL_HOST_RE"; then echo "FAIL treats github as internal"; fail=1; else echo "ok   treats github as public"; fi
  [ "$fail" -eq 0 ] && echo "selftest PASSED" || echo "selftest FAILED"
  exit "$fail"
fi

REMOTE_URL="${2:-}"
if printf '%s' "$REMOTE_URL" | grep -qE "$INTERNAL_HOST_RE"; then
  exit 0   # internal forge — internal names belong there
fi

# stdin: <local ref> <local sha> <remote ref> <remote sha>
violations=0
while read -r _lref lsha _rref rsha; do
  [ "$lsha" = "0000000000000000000000000000000000000000" ] && continue
  if [ "$rsha" = "0000000000000000000000000000000000000000" ]; then
    range="$lsha"          # new branch: check the commit itself
    diffcmd=(git show --format=%B "$lsha")
  else
    range="$rsha..$lsha"
    diffcmd=(git diff "$rsha" "$lsha")
  fi
  # ADDED lines only (+ prefix), so pre-existing occurrences never trip it.
  added=$("${diffcmd[@]}" 2>/dev/null | grep -E '^\+' | grep -nE "$PATTERNS" || true)
  msgs=$(git log --format=%B "$range" 2>/dev/null | grep -nE "$PATTERNS" || true)
  if [ -n "$added" ] || [ -n "$msgs" ]; then
    violations=1
    echo "✗ REFUSED: this push would add internal names to a PUBLIC remote." >&2
    echo "  remote: $REMOTE_URL" >&2
    [ -n "$added" ] && { echo "  in the diff:" >&2; printf '%s\n' "$added" | head -10 | sed 's/^/    /' >&2; }
    [ -n "$msgs" ]  && { echo "  in commit messages:" >&2; printf '%s\n' "$msgs" | head -10 | sed 's/^/    /' >&2; }
  fi
done

if [ "$violations" -ne 0 ]; then
  cat >&2 <<'EOM'

  Scrub them and amend, or push to the internal forge instead.
  Pre-existing occurrences are deliberately NOT flagged — this refuses only what
  the push ADDS, so it stays quiet enough to stay installed.
  Override for a deliberate, reviewed publish:  git push --no-verify
EOM
  exit 1
fi
exit 0
