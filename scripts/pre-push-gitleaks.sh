#!/usr/bin/env bash
# pre-push-gitleaks — refuse a push whose NEW commits carry a credential (aegis-0mhzqo).
#
# Runs from the scrub guard's pre-push.d chain (install-scrub-guard.sh places it at
# pre-push.d/50-gitleaks next to the guard). The guard reads git's ref list ONCE and
# replays it on stdin to every chained hook; arguments are <remote> <url>.
#
# WHY A SECOND MECHANISM. Stiwi ruled 2026-09-24 that hostnames and crew names are
# fine in public repos while secrets and personal data are still scrubbed. The scrub
# guard's patterns are identifier regexes projected from the policy graph; it has
# never recognised a credential. gitleaks is purpose-built for that, so credentials
# are gitleaks' job and names stay the graph's (sattler's ruling on aegis-0mhzqo).
#
# ONLY THE PUSHED RANGE. A history scan would refuse every push for a secret an
# upstream author committed and removed years ago. Per ref:
#   deleted ref                 -> nothing to scan
#   new branch (remote sha 0)   -> commits not yet on any remote-tracking ref
#   update                      -> <remote sha>..<local sha>
# Findings are printed REDACTED: a refusal must never re-leak what it caught.
#
# A deliberate fixture (a test that must name a token-shaped string) is allowed per
# line with gitleaks' own inline marker:  gitleaks:allow   (reviewed in the diff).
#
# gitleaks ABSENT => FAIL OPEN, LOUDLY, the same policy as the scrub guard: a push
# guard that hard-fails on an unprovisioned machine gets removed the same day.
#
#   exit 0  no credential in the pushed range (or gitleaks absent: loud warning)
#   exit 1  a credential was found: push refused
set -uo pipefail

ZERO=0000000000000000000000000000000000000000
GITLEAKS="${GITLEAKS_BIN:-$(command -v gitleaks 2>/dev/null || true)}"
[ -z "$GITLEAKS" ] && [ -x "$HOME/.local/bin/gitleaks" ] && GITLEAKS="$HOME/.local/bin/gitleaks"

selftest() {
  local t fail=0 base out rc
  t=$(mktemp -d); trap 'rm -rf "$t"' RETURN
  if [ -z "$GITLEAKS" ]; then echo "UNKNOWN gitleaks not installed; nothing to test"; return 2; fi
  git init -q "$t/r"; cd "$t/r" || return 2
  git config user.email t@t; git config user.name t
  echo seed > f.txt; git add f.txt; git commit -qm seed; base=$(git rev-parse HEAD)
  run() { printf 'refs/heads/x %s refs/heads/x %s\n' "$(git rev-parse HEAD)" "$1" | bash "$SELF" origin git@github.com:o/r.git 2>&1; }
  # 1. clean change passes
  echo "hello db.lan and sattler" > ok.txt; git add ok.txt; git commit -qm ok
  out=$(run "$base"); rc=$?
  [ "$rc" -eq 0 ] && echo "ok   clean push allowed" || { echo "FAIL clean push refused: $out"; fail=1; }
  # 2. PLANTED TOKEN must refuse (the control that proves the scan runs at all)
  base=$(git rev-parse HEAD)
  printf 'aws_secret_access_key = "%s"\n' "wJalrXUtnFEMI/K7MDENG/bPxRfiCYzEXAMPLEKEYx" > leak.txt
  printf 'ghp_%s\n' "Zx9Qw3Er5Ty7Ui9Op1As3Df5Gh7Jk9Lz1Xc3" >> leak.txt
  git add leak.txt; git commit -qm leak
  out=$(run "$base"); rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q 'REFUSED'; then echo "ok   planted token refused"
  else echo "FAIL planted token NOT refused (rc=$rc): $out"; fail=1; fi
  printf '%s' "$out" | grep -q 'Zx9Qw3Er5Ty7' && { echo "FAIL the refusal re-printed the secret"; fail=1; } || echo "ok   refusal output is redacted"
  # 3. only the pushed range: the old leak is not re-scanned by a later clean push
  base=$(git rev-parse HEAD)
  echo "more" >> ok.txt; git commit -qam more
  out=$(run "$base"); rc=$?
  [ "$rc" -eq 0 ] && echo "ok   an old leak outside the pushed range is not re-scanned" || { echo "FAIL range leaked history into the scan: $out"; fail=1; }
  # 4. inline allow marker
  base=$(git rev-parse HEAD)
  printf 'token = "ghp_%s"  # gitleaks:allow (test fixture)\n' "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk1Ll2" > fixture.txt
  git add fixture.txt; git commit -qm fixture
  out=$(run "$base"); rc=$?
  [ "$rc" -eq 0 ] && echo "ok   gitleaks:allow marker honoured" || { echo "FAIL allow marker refused: $out"; fail=1; }
  # 5. deletion push scans nothing
  out=$(printf 'refs/heads/x %s refs/heads/x %s\n' "$ZERO" "$base" | bash "$SELF" origin url 2>&1); rc=$?
  [ "$rc" -eq 0 ] && echo "ok   ref deletion allowed" || { echo "FAIL deletion refused: $out"; fail=1; }
  [ "$fail" -eq 0 ] && echo "PASS" || echo "FAIL"
  return "$fail"
}

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
if [ "${1:-}" = "--selftest" ]; then selftest; exit $?; fi

if [ -z "$GITLEAKS" ]; then
  echo "⚠ pre-push-gitleaks: gitleaks NOT INSTALLED — this push was NOT checked for" >&2
  echo "  credentials. Failing open on purpose; install gitleaks (aegis-0mhzqo)." >&2
  exit 0
fi

refused=0
while read -r local_ref local_sha remote_ref remote_sha; do
  [ -n "${local_sha:-}" ] || continue
  [ "$local_sha" = "$ZERO" ] && continue                       # deleting a ref
  if [ "${remote_sha:-$ZERO}" = "$ZERO" ]; then
    range="$local_sha --not --remotes"                         # new branch
  else
    range="$remote_sha..$local_sha"
  fi
  out=$("$GITLEAKS" git . --log-opts="$range" --redact --no-banner --exit-code 1 \
        --log-level error --report-format json --report-path /dev/stdout 2>/dev/null)
  rc=$?
  if [ "$rc" -eq 1 ]; then
    refused=1
    echo "✗ REFUSED by pre-push-gitleaks: a credential in the commits being pushed ($local_ref):" >&2
    printf '%s' "$out" | python3 -c '
import json,sys
try: f=json.load(sys.stdin)
except Exception: f=[]
for x in f: print("    %s  %s:%s  commit %s  (%s)" % (x["RuleID"], x["File"], x["StartLine"], x["Commit"][:10], x.get("Match","")[:40]))
' >&2
    echo "  Remove the secret (rewrite the commits; rotate it if it was ever real)." >&2
    echo "  A deliberate test fixture: add  gitleaks:allow  on that line." >&2
  elif [ "$rc" -ne 0 ]; then
    refused=1
    echo "✗ REFUSED by pre-push-gitleaks: gitleaks could not scan $local_ref (rc=$rc)." >&2
    echo "  A scan that cannot run must not report clean." >&2
  fi
done
exit "$refused"
