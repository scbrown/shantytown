#!/usr/bin/env bash
# pre-push-scrub-guard — refuse a PUBLIC push that introduces a NEW internal name.
#
# Install:  ln -sf ../../scripts/pre-push-scrub-guard.sh .git/hooks/pre-push
# Verify:   scripts/pre-push-scrub-guard.sh --selftest
#
# WHY IT IS NEW-OCCURRENCE, NOT ANY-OCCURRENCE, and this is the whole design:
# Several internal names are ALREADY on the public remote. A hook that
# refuses any occurrence would fire on every push from day one, be recognised as
# broken, and be disabled within a day — leaving no guard at all. Refusing only
# what a push ADDS means it is silent on the existing debt and loud on new leaks,
# so it survives long enough to be useful.
#
# It fires only on remotes that are NOT the internal forge. Pushing internal names
# to the internal forge is not a leak; that is where they belong.
set -uo pipefail

# ── THE LIST DOES NOT LIVE IN THIS FILE, AND THAT IS THE POINT ──────────────
# The first version inlined it:
#     PATTERNS='<a dozen internal hostnames>|192\.168\.[0-9]+...'
# which enumerated the estate, by name, in a repo whose remote is github.com.
# The guard written to stop hostnames reaching the public remote was the densest
# hostname leak in the repo, and it was caught by running the policy over the
# push before making it — i.e. by this guard's own idea, applied to itself.
#
# (Not a dig at whoever wrote it. The same thing happened to a .gitignore comment
# I wrote an hour earlier explaining a leak, by naming the leak. A note about a
# leak is not exempt from it.)
#
# So: this file holds the MECHANISM, the config holds the NAMES, and the config
# lives outside the public repo.
#
#   $SCRUB_PATTERNS_FILE, else ~/.config/aegis/scrub-patterns.conf
#   two lines:  internal_host_re=...      the forge that may receive them
#               patterns=...              ERE alternation of forbidden names
#
# Source of truth is the policy graph (aegis-mqnl); the config is a generated
# projection of it, so this hook and the pre-edit guard cannot drift into
# disagreeing about what is forbidden. Regenerate, do not hand-edit.
#
# NO CONFIG => FAIL OPEN, LOUDLY. A push guard that hard-failed when unconfigured
# would block every push on every machine that had not been set up, and would be
# removed the same day. Loud on stderr, exit 0, so "the guard did not run" is at
# least visible rather than silent.

CONF="${SCRUB_PATTERNS_FILE:-$HOME/.config/aegis/scrub-patterns.conf}"
INTERNAL_HOST_RE=""
PATTERNS=""
# Ticket IDs (aegis-9cr1). Projected SEPARATELY from block-tier names because they
# are enforced differently: internal names are refused in the diff AND commit
# messages; ticket IDs are refused only in FILE CONTENT (a public CHANGELOG or a
# source comment — the quipu #38 leak), NOT in commit messages, which keep the
# bead ref for internal git history. Same graph rule, distinct enforcement point.
TICKET_PATTERNS=""
if [ -r "$CONF" ]; then
  # shellcheck disable=SC1090
  internal_host_re=""; patterns=""
  while IFS='=' read -r k v; do
    case "$k" in
      internal_host_re) INTERNAL_HOST_RE="$v" ;;
      patterns)         PATTERNS="$v" ;;
      ticket_patterns)  TICKET_PATTERNS="$v" ;;
    esac
  done < "$CONF"
fi

if [ "${1:-}" = "--selftest" ]; then
  # Synthesises its own config, so the controls run without the real names ever
  # appearing in this repo — and so the test proves the MECHANISM, which is the
  # only thing this file is now responsible for.
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  # Synthetic config: the controls must prove the MECHANISM without the estate's
  # real names ever appearing in this public file. Reserved names only
  # (RFC 2606 .invalid, RFC 5737 198.51.100.0/24).
  INTERNAL_HOST_RE='forge\.invalid'
  PATTERNS='[a-z0-9-]+\.invalid\b|\b(alphahost|betahost)\b|198\.51\.100\.[0-9]+|/home/jsmith'
  fail=0
  # MUST detect. Includes a BARE host name: the .lan/.svc form is not the only
  # shape an internal identifier takes, and an enumerate-the-services list sails
  # straight past the place-name scheme.
  for bad in 'connect to secret-host.invalid:3306' 'rebuilt on alphahost' \
             'addr 198.51.100.7' '/home/jsmith/src/x' 'host betahost'; do
    printf '%s\n' "$bad" > "$tmp/dirty"
    if grep -nEq "$PATTERNS" "$tmp/dirty"; then echo "ok   detects: $bad"; else echo "FAIL misses: $bad"; fail=1; fi
  done
  # MUST NOT fire. English prose containing host-name substrings is the
  # cry-wolf case that kills a guard: an unanchored short name matches
  # "derivative" and "activation", which measured 81 false positives in one repo.
  # Word boundaries are load-bearing, so they get a control.
  for ok in 'the derivative activation of a private motivation' \
            'version 1.2.3.4 released' 'see 8.8.8.8 for public dns' \
            '/home/user/src/x'; do
    printf '%s\n' "$ok" > "$tmp/clean"
    if grep -nEq "$PATTERNS" "$tmp/clean"; then echo "FAIL fires on clean: $ok"; fail=1; else echo "ok   silent on: $ok"; fi
  done
  # Ticket IDs (aegis-9cr1). Synthetic prefix `zz-` so no real tracker prefix
  # appears in this public file. MUST detect a ticket in file content; MUST NOT
  # fire on ordinary prose that merely contains a hyphenated word.
  TICKET_PATTERNS='\bzz-[a-z0-9]{3,6}\b'
  for bad in '- Fixed the thing (zz-1a2b)' '// see zz-9x8w for the reason'; do
    printf '%s\n' "$bad" > "$tmp/dirty"
    if grep -nEq "$TICKET_PATTERNS" "$tmp/dirty"; then echo "ok   detects ticket: $bad"; else echo "FAIL misses ticket: $bad"; fail=1; fi
  done
  for ok in 'a well-formed sentence with a hyphen' 'the zz top of the file'; do
    printf '%s\n' "$ok" > "$tmp/clean"
    if grep -nEq "$TICKET_PATTERNS" "$tmp/clean"; then echo "FAIL ticket fires on clean: $ok"; fail=1; else echo "ok   ticket silent on: $ok"; fi
  done
  if printf 'ssh://git@forge.invalid/x/y.git' | grep -qE "$INTERNAL_HOST_RE"; then echo "ok   recognises the internal forge"; else echo "FAIL internal forge unrecognised"; fail=1; fi
  if printf 'git@github.com:scbrown/x.git' | grep -qE "$INTERNAL_HOST_RE"; then echo "FAIL treats github as internal"; fail=1; else echo "ok   treats github as public"; fi
  # The unconfigured path must be VISIBLE, never silent.
  out=$(SCRUB_PATTERNS_FILE=/nonexistent "$0" --check-unconfigured 2>&1 >/dev/null)
  case "$out" in *"NOT CONFIGURED"*) echo "ok   unconfigured is loud" ;; *) echo "FAIL unconfigured is silent"; fail=1 ;; esac
  [ "$fail" -eq 0 ] && echo "selftest PASSED" || echo "selftest FAILED"
  exit "$fail"
fi

if [ -z "$PATTERNS" ]; then
  echo "⚠ pre-push-scrub-guard: NOT CONFIGURED ($CONF missing) — this push was" >&2
  echo "  NOT checked for internal names. Failing open on purpose; see aegis-mqnl." >&2
  exit 0
fi
[ "${1:-}" = "--check-unconfigured" ] && exit 0

# Files whose JOB is to contain pattern samples (this guard, the ratchets). Their
# fixtures are not leaks; scanning them refuses a push for the guard working. Same
# set the policy graph exempts. git exclude pathspecs, so the hunks never appear.
GUARD_EXCLUDE=(
  ':(exclude,glob)**/pre-push-scrub-guard.sh'
  ':(exclude,glob)**/no_internal_identifiers.rs'
  ':(exclude,glob)**/test_internal_identifier_ratchet.py'
  ':(exclude,glob)**/test_no_internal_ids_in_output.py'
)

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
    diffcmd=(git show --format=%B "$lsha" -- . "${GUARD_EXCLUDE[@]}")
  else
    range="$rsha..$lsha"
    diffcmd=(git diff "$rsha" "$lsha" -- . "${GUARD_EXCLUDE[@]}")
  fi
  # ADDED lines only (+ prefix), so pre-existing occurrences never trip it.
  addedlines=$("${diffcmd[@]}" 2>/dev/null | grep -E '^\+' || true)
  added=$(printf '%s\n' "$addedlines" | grep -nE "$PATTERNS" || true)
  msgs=$(git log --format=%B "$range" 2>/dev/null | grep -nE "$PATTERNS" || true)
  # Ticket IDs are checked in FILE CONTENT only (the diff), never in commit
  # messages — a bead ref in a subject is the fleet's deliberate internal habit,
  # but the same ref in a CHANGELOG or a source comment reaching a public repo is
  # a leak a stranger cannot resolve (aegis-9cr1, the quipu #38 CHANGELOG).
  tickets=""
  [ -n "$TICKET_PATTERNS" ] && tickets=$(printf '%s\n' "$addedlines" | grep -nE "$TICKET_PATTERNS" || true)
  if [ -n "$added" ] || [ -n "$msgs" ] || [ -n "$tickets" ]; then
    violations=1
    echo "✗ REFUSED: this push would add internal identifiers to a PUBLIC remote." >&2
    echo "  remote: $REMOTE_URL" >&2
    [ -n "$added" ]   && { echo "  internal names in the diff:" >&2; printf '%s\n' "$added" | head -10 | sed 's/^/    /' >&2; }
    [ -n "$msgs" ]    && { echo "  internal names in commit messages:" >&2; printf '%s\n' "$msgs" | head -10 | sed 's/^/    /' >&2; }
    [ -n "$tickets" ] && { echo "  internal ticket IDs in file content (CHANGELOG / source comments):" >&2; printf '%s\n' "$tickets" | head -10 | sed 's/^/    /' >&2; }
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
