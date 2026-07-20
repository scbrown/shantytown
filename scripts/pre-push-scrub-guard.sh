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
#
# Host names come from a place-name scheme (koror, palau, yap, kota, luvu, vati,
# goldblum). They are matched with word boundaries because several are also
# ordinary words or real geography — `vati` unanchored matches "activation" and
# "derivative", which is 81 false positives in quipu alone and exactly the
# cry-wolf that gets a guard switched off.
#
# The .lan/.svc match is GENERALISED rather than an enumerated list: the old list
# named six specific services and would have sailed past koror.lan, yap.lan and
# palau.lan, which are the most common internal names in quipu. Enumerating
# known-bad is how a filter silently ages out.
PATTERNS='[a-z0-9-]+\.(lan|svc)\b|\b(kota|luvu|vati|koror|palau|yap|goldblum)\b|(^|[^0-9.])(192\.168|10\.(1[0-9]|2[0-9]|3[01]|[0-9]))\.[0-9]+\.[0-9]+|/home/braino'

if [ "${1:-}" = "--selftest" ]; then
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  # Clean text that MUST NOT trip it: English words containing host-name
  # substrings. `vati` unanchored matches these, and that false-positive rate is
  # what kills a guard.
  printf 'the derivative activation of a private motivation\n' > "$tmp/clean"
  fail=0
  for bad in 'connect to dolt.lan:3306' 'rebuilt on koror' 'ssh yap.lan' 'addr 192.168.7.212' '/home/braino/gt/x' 'host palau'; do
    printf '%s\n' "$bad" > "$tmp/dirty"
    if grep -nEq "$PATTERNS" "$tmp/dirty"; then echo "ok   detects: $bad"; else echo "FAIL misses: $bad"; fail=1; fi
  done
  for ok in 'the derivative activation of a private motivation' 'version 1.2.3.4 released' 'see 8.8.8.8 for public dns'; do
    printf '%s\n' "$ok" > "$tmp/clean"
    if grep -nEq "$PATTERNS" "$tmp/clean"; then echo "FAIL fires on clean: $ok"; fail=1; else echo "ok   silent on: $ok"; fi
  done
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
