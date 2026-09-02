#!/usr/bin/env bash
# Detect ORDER DEPENDENCE in the test suite, in both directions.
#
# WHY (aegis-rig9vu). The suite's pass/fail was found to depend on which tests
# ran before it, both ways: tests that FAIL alone and PASS in the full run, and
# tests that PASS alone and FAIL in it. That makes a red suite unattributable
# -- you cannot tell your diff from the house's -- and, the worse half, makes a
# GREEN suite not evidence either, because the full run can conceal a test that
# is genuinely broken when run on its own.
#
# Four full-suite runs once produced four different failure sets, with the
# "extra" failure moving between four unrelated files. Nobody could name the
# shared state, and by the time anyone went looking it no longer reproduced.
# This is the instrument that would have caught it while it was live: it does
# not need the cause to be guessable, only for the two runs to disagree.
#
# WHAT IT DOES
#   run W   the whole suite in ONE process       -> set of failing test ids
#   run A   every test file ALONE, own process   -> set of failing test ids
#   diff    the two sets and name every test that disagrees
#   confirm re-measure each disagreement before naming it (see below)
#
#   HIDDEN   fails alone, passes whole -> a real failure the suite CONCEALS
#   INDUCED  passes alone, fails whole -> pollution from an earlier test
#   FLAKY    disagreed, but is not stable within a mode either
#
# This is deliberately NOT a fix by pinning the order. Pinning makes the suite
# reproducible and leaves it order-dependent; the point is to find the coupling
# and say which test carries it.
#
# USAGE   check-test-order-independence.sh [--selftest]
#
# EXIT  0 order-independent (the two runs agree)
#       1 ORDER DEPENDENCE or FLAKINESS FOUND (tests named on stdout)
#       2 CANNOT TELL -- a run did not produce a usable result
#
# 2 is kept distinct from 1 on purpose: "the instrument could not see" and
# "the instrument saw nothing" are different answers, and collapsing them is
# how a broken check reports green.
set -uo pipefail

PYTEST=${PYTEST:-"python3 -m pytest"}
# Overridable so --selftest can point the same logic at a synthetic suite.
# Without this the disagreement paths below would never execute anywhere, and a
# detector whose finding path has never run is not evidence of anything.
ROOT=${ORDERCHECK_ROOT:-"$(cd "$(dirname "$0")/.." && pwd)"}
TESTS=${ORDERCHECK_TESTS:-tests}

# ---------------------------------------------------------------- selftest --
if [ "${1:-}" = "--selftest" ]; then
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    # A module the synthetic tests share, so "state left by an earlier test"
    # is modelled the way it really happens: at IMPORT time, which is what
    # makes it visible in a whole run and invisible when a file runs alone.
    cat > "$tmp/helper_state.py" <<'PY'
TOUCHED = []
BROKEN = False
PY
    # HIDDEN: passes only because another module's import ran first.
    cat > "$tmp/test_a_setter.py" <<'PY'
import helper_state
helper_state.TOUCHED.append(1)
def test_setter_itself_is_fine(): assert True
PY
    cat > "$tmp/test_b_dependent.py" <<'PY'
import helper_state
def test_needs_the_setter(): assert helper_state.TOUCHED, "no earlier import"
PY
    # INDUCED: broken only because another module's import ran first.
    cat > "$tmp/test_c_polluter.py" <<'PY'
import helper_state
helper_state.BROKEN = True
def test_polluter_itself_is_fine(): assert True
PY
    cat > "$tmp/test_d_victim.py" <<'PY'
import helper_state
def test_clean_state(): assert not helper_state.BROKEN, "polluted"
PY
    # FLAKY: alternates on a counter, so three consecutive runs are never all
    # the same -- a random one could land PPP and be misreported as stable.
    cat > "$tmp/test_e_flaky.py" <<'PY'
import pathlib
def test_alternates():
    c = pathlib.Path(__file__).parent / ".counter"
    n = int(c.read_text()) if c.exists() else 0
    c.write_text(str(n + 1))
    assert n % 2 == 0, "odd invocation"
PY
    cat > "$tmp/test_g_clean.py" <<'PY'
def test_always_passes(): assert True
PY
    out=$(ORDERCHECK_ROOT="$tmp" ORDERCHECK_TESTS=. "$0" 2>&1); rc=$?
    fail=0
    chk() { if printf '%s' "$out" | grep -q "$1"; then echo "  ok   $2"
            else echo "  FAIL $2"; fail=1; fi; }
    echo "$out" | sed 's/^/    | /'
    echo "selftest assertions:"
    chk "HIDDEN"                      "reports HIDDEN"
    chk "test_b_dependent.py::test_needs_the_setter"  "names the hidden test"
    chk "INDUCED"                     "reports INDUCED"
    chk "test_d_victim.py::test_clean_state"          "names the induced test"
    chk "FLAKY"                       "reports FLAKY"
    chk "test_e_flaky.py::test_alternates"            "names the flaky test"
    # Match a test ID ("file.py::test_x"), not a bare filename: run A prints
    # every file it visits as progress, so grepping the filename asserts
    # nothing about the findings.
    if printf '%s' "$out" | grep -q "test_g_clean.py::"; then
        echo "  FAIL a clean test was named as a finding"; fail=1
    else echo "  ok   the clean test is not named as a finding"; fi
    [ "$rc" = "1" ] && echo "  ok   exit 1 on a finding" || { echo "  FAIL exit was $rc, want 1"; fail=1; }
    [ "$fail" = "0" ] && { echo "SELFTEST PASSED"; exit 0; } || { echo "SELFTEST FAILED"; exit 1; }
fi

# -------------------------------------------------------------------- main --
cd "$ROOT" || exit 2
outdir=$(mktemp -d)
trap 'rm -rf "$outdir"' EXIT

# Read the short-summary lines: we need the IDENTITY of each failure, and a
# count cannot be diffed.
ids_from() { grep -E '^(FAILED|ERROR) ' "$1" | awk '{print $2}' | sort -u; }
# A file may be entirely skipped -- that is a RESULT, not a failure to run.
usable() { grep -qE '[0-9]+ (passed|failed|skipped|error)|no tests ran' "$1"; }

echo "== run W: the whole suite in one process =="
# shellcheck disable=SC2086
$PYTEST -q --tb=no -rfE "$TESTS" > "$outdir/whole.log" 2>&1
if ! usable "$outdir/whole.log"; then
    echo "CANNOT TELL: the whole-suite run produced no result line." >&2
    tail -20 "$outdir/whole.log" >&2; exit 2
fi
ids_from "$outdir/whole.log" > "$outdir/whole.ids"
tail -1 "$outdir/whole.log"

echo
echo "== run A: each test file alone =="
files=$(ls "$TESTS"/test_*.py)
n=$(echo "$files" | wc -l); i=0
: > "$outdir/alone.ids"
for f in $files; do
    i=$((i+1))
    # shellcheck disable=SC2086
    $PYTEST -q --tb=no -rfE "$f" > "$outdir/one.log" 2>&1
    if ! usable "$outdir/one.log"; then
        echo "CANNOT TELL: $f produced no result line." >&2
        tail -20 "$outdir/one.log" >&2; exit 2
    fi
    ids_from "$outdir/one.log" >> "$outdir/alone.ids"
    printf '\r  %d/%d %-50s' "$i" "$n" "$f"
done
printf '\r  %d/%d files run%-50s\n' "$n" "$n" ""
sort -u "$outdir/alone.ids" -o "$outdir/alone.ids"

echo
echo "whole-suite failures: $(wc -l < "$outdir/whole.ids")"
echo "alone      failures: $(wc -l < "$outdir/alone.ids")"
echo

hidden=$(comm -23 "$outdir/alone.ids" "$outdir/whole.ids")
induced=$(comm -13 "$outdir/alone.ids" "$outdir/whole.ids")

# ---- confirm -------------------------------------------------------------
# A disagreement between ONE whole run and ONE alone run has two causes with
# OPPOSITE remedies, and a single pair of runs cannot tell them apart:
#
#   ORDER-DEPENDENT  stable in each mode, differs between them -> real
#                    coupling; go and find the shared state.
#   FLAKY            not stable within a mode -> nondeterminism; the ordering
#                    is a red herring and there is no shared state to find.
#
# Reporting the second as the first sends the next reader hunting for
# something that does not exist, which is the exact cost this bead was filed
# for. So every disagreement is re-measured before it is named.
disagree=$(printf '%s\n%s\n' "$hidden" "$induced" | grep -v '^$' | sort -u)
flaky=""
if [ -n "$disagree" ]; then
    echo "== confirm: re-measuring $(printf '%s\n' "$disagree" | wc -l) disagreement(s) =="
    # shellcheck disable=SC2086
    $PYTEST -q --tb=no -rfE "$TESTS" > "$outdir/whole2.log" 2>&1
    if ! usable "$outdir/whole2.log"; then
        echo "CANNOT TELL: the confirming whole-suite run produced no result line." >&2
        exit 2
    fi
    ids_from "$outdir/whole2.log" > "$outdir/whole2.ids"
    : > "$outdir/flaky.ids"
    for id in $disagree; do
        seen=""
        for _ in 1 2 3; do
            # shellcheck disable=SC2086
            if $PYTEST -q --tb=no "$id" > "$outdir/c.log" 2>&1
            then seen="${seen}P"; else seen="${seen}F"; fi
        done
        w1=N; grep -qxF "$id" "$outdir/whole.ids"  && w1=Y
        w2=N; grep -qxF "$id" "$outdir/whole2.ids" && w2=Y
        if [ "$seen" != "PPP" ] && [ "$seen" != "FFF" ]; then
            flaky="$flaky    $id (alone over 3 runs: $seen -- not stable)"$'\n'
            echo "$id" >> "$outdir/flaky.ids"
        elif [ "$w1" != "$w2" ]; then
            flaky="$flaky    $id (whole run failed=$w1 then $w2 -- not stable)"$'\n'
            echo "$id" >> "$outdir/flaky.ids"
        fi
    done
    if [ -s "$outdir/flaky.ids" ]; then
        # Drop them from both order sets: a different defect, and leaving them
        # here would bury the real ones.
        hidden=$(printf '%s\n' "$hidden"  | grep -v '^$' | grep -vxF -f "$outdir/flaky.ids")
        induced=$(printf '%s\n' "$induced" | grep -v '^$' | grep -vxF -f "$outdir/flaky.ids")
    fi
    echo
fi

rc=0
if [ -n "$flaky" ]; then
    rc=1
    echo "FLAKY -- these disagreed between the two modes, but their verdict is"
    echo "  not stable WITHIN a mode either. That is nondeterminism, not order"
    echo "  dependence: do NOT go looking for shared state for these."
    printf '%s' "$flaky"
    echo
fi
if [ -n "$hidden" ]; then
    rc=1
    echo "HIDDEN -- these FAIL ALONE and PASS in the full suite."
    echo "  The full suite is CONCEALING a real failure. A green run is not"
    echo "  evidence for these tests; run them alone before believing them."
    printf '%s\n' "$hidden" | sed 's/^/    /'
    echo
fi
if [ -n "$induced" ]; then
    rc=1
    echo "INDUCED -- these PASS ALONE and FAIL in the full suite."
    echo "  Something earlier in the run leaves state these tests can see. The"
    echo "  failure belongs to the coupling, so do not attribute it to your"
    echo "  diff without checking here first."
    printf '%s\n' "$induced" | sed 's/^/    /'
    echo
fi

if [ $rc -eq 0 ]; then
    echo "ORDER-INDEPENDENT: the whole-suite and run-alone failure sets agree."
    echo "That says the two runs AGREE. It is not a claim that the suite is"
    echo "green -- read the counts above for that."
else
    echo "FINDINGS ABOVE. Each test named is a place where 'the suite passed'"
    echo "and 'this test passes' are different statements."
fi
exit $rc
