#!/usr/bin/env bash
# test-push-guard-range-scope.sh — the pre-push guard must scan the RANGE
# per-commit, not the range's net diff (aegis-gsbs1).
#
# WHY THIS TEST EXISTS. The guard refused a push that added an internal
# identifier — correctly. The author then added a SCRUB COMMIT rather than
# amending (the leaking commit had already reached the internal forge, so it
# could no longer be amended), and the next push PASSED, because the guard
# evaluated the NET diff of the range. The identifier went into public history
# anyway and is reachable by sha forever.
#
# The trap punished the right instinct: a scrub commit is what a careful author
# reaches for first, and the guard's own remedy text used to say "amend".
#
# Every assertion below is on the EXIT CODE, not on the message. A guard that
# prints REFUSED and exits 0 does not block a push, and that failure would be
# invisible to any check that only reads output.
#
# The leaked-looking string is assembled at RUNTIME from fragments so this file
# never contains an internal identifier literally — otherwise the guard and the
# ratchet would flag their own test, which is how a test like this gets deleted.
set -uo pipefail

GUARD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/pre-push-scrub-guard.sh"
[ -x "$GUARD" ] || { echo "FAIL: guard not found/executable at $GUARD"; exit 1; }

PUBLIC_URL='https://github.com/scbrown/shantytown.git'
INTERNAL_URL='ssh://git@git.lan/stiwi/shantytown.git'

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cd "$TMP" || exit 1
git init -q work && cd work || exit 1
git config user.email t@example.invalid && git config user.name t

USER_PART="$(printf 'brai%s' 'no')"
NODE_PART="$(printf 'va%s' 'ti')"

echo base > a.txt && git add . && git commit -qm "clean base"
BASE=$(git rev-parse HEAD)
printf '%s@%s:/home/%s/scratch\n' "$USER_PART" "$NODE_PART" "$USER_PART" > fixture.txt
git add . && git commit -qm "add captured fixture"
LEAK=$(git rev-parse HEAD)
rm fixture.txt && git add -A && git commit -qm "scrub the fixture"
TIP=$(git rev-parse HEAD)
echo more > b.txt && git add . && git commit -qm "clean change"
CLEAN=$(git rev-parse HEAD)

run() { # $1=local sha  $2=remote sha  $3=url -> echoes exit code
  echo "refs/heads/main $1 refs/heads/main $2" \
    | bash "$GUARD" origin "$3" >/dev/null 2>&1
  echo $?
}

fail=0
check() { # $1=label $2=actual $3=expected
  if [ "$2" = "$3" ]; then
    echo "ok   $1 (exit $2)"
  else
    echo "FAIL $1: expected exit $3, got $2"
    fail=1
  fi
}

# THE REGRESSION. Net diff of BASE..TIP is empty — the leak is added and removed
# inside the range — yet the push publishes the leaking blob. Must REFUSE.
check "leak+scrub range refuses (the aegis-gsbs1 case)" "$(run "$TIP" "$BASE" "$PUBLIC_URL")" 1

# Negative control. Without this, a guard that refuses everything passes the
# test above and blocks all work — which is how a guard gets uninstalled.
check "clean range is allowed"                          "$(run "$CLEAN" "$TIP" "$PUBLIC_URL")" 0

# The original behaviour must survive the fix.
check "leak alone still refuses"                        "$(run "$LEAK" "$BASE" "$PUBLIC_URL")" 1

# Internal forge stays permissive: internal names belong there, and refusing
# would push people onto --no-verify for legitimate work.
check "internal forge allows the same range"            "$(run "$TIP" "$BASE" "$INTERNAL_URL")" 0

if [ "$fail" -eq 0 ]; then echo "PASSED"; else echo "FAILED"; fi
exit "$fail"
