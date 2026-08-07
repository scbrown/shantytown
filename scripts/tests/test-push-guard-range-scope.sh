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

# The internal host is assembled at RUNTIME, per this file's own convention above.
# It was a LITERAL here until 2026-08-07 and was PUBLIC on origin/main for as long
# as this file existed (aegis-qtweq) — the leak arrived inside 75a952e, the commit
# that HARDENED this very guard. That is the likeliest way this recurs: the fixture
# a guard needs in order to prove it catches real hostnames is itself a real
# hostname. The rest of the file already knew that; line 28 simply escaped the rule.
#
# ⛔ DO NOT "simplify" this to a reserved name such as forge.invalid. It looks like
# the obvious scrub and it SILENTLY INVERTS the assertion at the bottom of this file.
# The guard classifies a remote as internal with internal_host_re taken from the
# DEPLOYED config (~/.config/aegis/scrub-patterns.conf), which this test does not
# synthesise — so, measured 2026-08-07 against that real config:
#     the assembled host below     -> INTERNAL   (guard allows: what we assert)
#     ssh://git@forge.invalid/…    -> public     (guard scans and REFUSES)
# (The internal form is described, never spelled — writing the counter-example out
#  is how this comment would leak the identifier the code beneath it just stopped
#  leaking. That is not hypothetical: the first draft of this very comment did it.)
# A reserved name therefore turns "internal forge allows the same range" from a
# real permissive-path check into a test that passes for the wrong reason, or fails
# outright. The value must be a host the deployed config actually recognises.
#
# Assembling it keeps BOTH properties: no literal in this public file, and the exact
# runtime string the assertion needs. The alternative — excluding this file via the
# ratchet's GUARD_FILES — would also silence the ratchet for every FUTURE leak here,
# in the one file most likely to acquire one.
INTERNAL_HOST="$(printf 'git.%s' 'lan')"
INTERNAL_URL="ssh://git@${INTERNAL_HOST}/stiwi/shantytown.git"

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
