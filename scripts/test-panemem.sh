#!/usr/bin/env bash
# test-panemem.sh — prove the per-pane memory ceiling (aegis-0j0n1n).
#
# Runs on its OWN tmux socket and never touches the live crew socket. Every
# assertion is read back from the kernel (`/sys/fs/cgroup/.../memory.*`) or from
# `systemctl --user show`, never from a tool's exit status — the defect this
# guards against was a `set-property` that returned 0 after writing the limit to
# the wrong cgroup.
#
# ARMS
#   1 launched pane IS bounded, and THE LAUNCHER'S OWN SCOPE IS UNTOUCHED.
#     This is the regression for the 2026-09-04 17:07:57 incident, in which the
#     launch path bounded its own pane and left the launched one at infinity.
#   2 a runaway inside a bounded pane dies INSIDE ITS OWN SCOPE (CONSTRAINT_MEMCG
#     naming that scope) and every other pane survives — the bead's falsifiable
#     close.
#   3 the instant-kill guard REFUSES a MemoryMax at or below current usage.
#   4 the launcher-scope guard REFUSES when the pane cannot leave the caller's
#     cgroup, rather than bounding the caller.
#
# Arm 2 allocates real memory. It is bounded by construction: the victim scope
# carries the ceiling before the hog starts, so the kernel caps it there.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
SOCK="panemem-selftest-$$"
FAIL=0
pass() { printf '  ✅ %s\n' "$*"; }
fail() { printf '  ❌ %s\n' "$*"; FAIL=1; }
cleanup() { tmux -L "$SOCK" kill-server 2>/dev/null; }
trap cleanup EXIT

cgpath() { sed -n 's/^0:://p' "/proc/$1/cgroup" 2>/dev/null; }
scope()  { cgpath "$1" | sed 's|.*/||'; }
prop()   { systemctl --user show "$1" -p "$2" --value 2>/dev/null; }

MY_SCOPE="$(scope $$)"
echo "launcher scope: ${MY_SCOPE:-<none>}"
if [[ -z "$MY_SCOPE" ]]; then
  echo "SKIP: this shell is not in a systemd scope; the guards under test are unreachable here."
  exit 0
fi
MY_MAX_BEFORE="$(prop "$MY_SCOPE" MemoryMax)"

echo
echo "=== arm 1: the LAUNCHED pane is bounded, the LAUNCHER is not ==="
export SHANTY_PANE_HIGH_GIB=3 SHANTY_PANE_MAX_GIB=3   # shipped shape: no throttle band
LAUNCH_OUT="$(python3 - "$SOCK" <<'PY'
import sys
sys.path.insert(0, ".")
from shantytown.tmux import Tmux
t = Tmux(socket=sys.argv[1])
t.new_session("victim")
print(t.pane_pid("victim"))
PY
)"
VPID="$(printf '%s\n' "$LAUNCH_OUT" | tail -1)"
VSCOPE="$(scope "$VPID")"
echo "victim pid=$VPID scope=${VSCOPE:-<none>}"

if [[ -n "$VSCOPE" && "$VSCOPE" != "$MY_SCOPE" ]]; then
  pass "victim landed in its own scope, not the launcher's"
elif [[ "$VSCOPE" == "$MY_SCOPE" ]]; then
  # CANNOT TELL is not FAIL, and conflating them is how a green suite gets
  # believed on a host where the mechanism is simply absent. tmux only puts a
  # pane in its own scope if its systemd integration is working; when it is not,
  # every pane inherits its launcher's cgroup and panemem has nothing to bind.
  # That is a HOST condition, not a defect in the code under test — and panemem
  # refusing (rather than capping the launcher) is the correct behaviour, which
  # this branch verifies. Measured on the crew host 2026-09-04: no `Started tmux-spawn`
  # in the journal after 17:29:49, and a respawned crew pane landed in a shared
  # `ptyxis-spawn-*.scope`. See aegis-0j0n1n.
  echo "  ⚠ UNAVAILABLE: tmux created no scope for the new pane — it inherited the"
  echo "     launcher's cgroup. Per-pane containment is IMPOSSIBLE on this host right now."
  echo "     Check: journalctl --user | grep 'Started tmux-spawn' | tail -1"
  [[ "$(prop "$MY_SCOPE" MemoryMax)" == "$MY_MAX_BEFORE" ]] \
    && pass "and panemem REFUSED rather than capping the launcher — the fail-safe held" \
    || fail "panemem capped the LAUNCHER when it could not resolve a scope"
  echo
  echo "SKIP — the mechanism under test is not available on this host (exit 2)."
  exit 2
else
  fail "victim scope is unreadable"
fi
VMAX="$(prop "$VSCOPE" MemoryMax)"; VHIGH="$(prop "$VSCOPE" MemoryHigh)"
[[ "$VMAX" == "3221225472" ]] && pass "victim MemoryMax=3G ($VMAX)" \
                              || fail "victim MemoryMax=$VMAX, expected 3221225472"
[[ "$VHIGH" == "3221225472" ]] && pass "victim MemoryHigh=3G, equal to Max — no throttle band ($VHIGH)" \
                               || fail "victim MemoryHigh=$VHIGH, expected 3221225472"
# read the KERNEL, not systemd's view
KMAX="$(cat "/sys/fs/cgroup$(cgpath "$VPID")/memory.max" 2>/dev/null)"
[[ "$KMAX" == "3221225472" ]] && pass "kernel memory.max agrees ($KMAX)" \
                              || fail "kernel memory.max=$KMAX"
MY_MAX_AFTER="$(prop "$MY_SCOPE" MemoryMax)"
[[ "$MY_MAX_AFTER" == "$MY_MAX_BEFORE" ]] \
  && pass "LAUNCHER's scope untouched (MemoryMax=$MY_MAX_AFTER, was $MY_MAX_BEFORE)" \
  || fail "LAUNCHER's scope CHANGED: $MY_MAX_BEFORE -> $MY_MAX_AFTER — this is the 0j0n1n bug"

echo
echo "=== arm 2a: SHIPPED SHAPE (High=Max) — runaway dies in its own scope, slice quiet ==="
tmux -L "$SOCK" new-session -d -s bystander 'sleep 900'
sleep 0.3
BPID="$(tmux -L "$SOCK" list-panes -t bystander -F '#{pane_pid}')"
CREW_BEFORE="$(tmux list-sessions -F '#{session_name}' 2>/dev/null | sort | tr '\n' ' ')"
echo "live crew before: ${CREW_BEFORE:-<none>}"
VCG="/sys/fs/cgroup$(cgpath "$VPID")"
PSI_FILE=/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/memory.pressure
psi() { sed -n 's/^full .*avg10=\([0-9.]*\).*/\1/p' "$PSI_FILE" 2>/dev/null; }
# $1 = session, $2 = 256MiB blocks to demand
hog() {
  rm -f "/tmp/panemem-hog-$1.out"
  tmux -L "$SOCK" send-keys -t "$1" \
    "python3 -c 'b=[]
for i in range($2): b.append(bytearray(256*1024*1024))
print(\"COMPLETED\", len(b))' > /tmp/panemem-hog-$1.out 2>&1" Enter
}

hog victim 32           # 8 GiB demanded against the 3 GiB ceiling set in arm 1
PSI_PEAK=0; PSI_RUN=0; PSI_MAXRUN=0
for _ in $(seq 1 90); do
  p="$(psi)"
  if [[ -n "$p" ]]; then
    PSI_PEAK="$(python3 -c "print(max($PSI_PEAK,$p))")"
    if python3 -c "import sys; sys.exit(0 if $p > 50.0 else 1)"; then
      PSI_RUN=$((PSI_RUN+1)); [[ $PSI_RUN -gt $PSI_MAXRUN ]] && PSI_MAXRUN=$PSI_RUN
    else PSI_RUN=0; fi
  fi
  grep -qE 'oom_kill [1-9]' "$VCG/memory.events" 2>/dev/null && break
  sleep 0.5
done
EV="$(tr '\n' ' ' < "$VCG/memory.events" 2>/dev/null)"
CUR="$(cat "$VCG/memory.current" 2>/dev/null)"
MAXV="$(cat "$VCG/memory.max" 2>/dev/null)"
echo "     memory.events: $EV"
echo "     memory.current=$CUR  memory.max=$MAXV"
VGONE=no; tmux -L "$SOCK" has-session -t victim 2>/dev/null || VGONE=yes
if grep -qE 'oom_kill [1-9]' <<<"${EV:-}" || [[ "$VGONE" == yes ]]; then
  pass "the 8G runaway was killed inside its OWN scope (events: ${EV:-<scope gone>})"
else
  fail "runaway neither killed nor contained (events: ${EV:-?}, session still up)"
fi
if [[ -z "$CUR" || ( -n "$MAXV" && "$CUR" -le "$MAXV" ) ]]; then
  pass "usage never left the ceiling (${CUR:-<scope gone>} <= ${MAXV:-3221225472})"
else
  fail "usage escaped the ceiling: current=$CUR max=$MAXV"
fi
grep -q COMPLETED /tmp/panemem-hog-victim.out 2>/dev/null \
  && fail "the 8G allocation COMPLETED — it was not contained" \
  || pass "the 8G allocation did not complete"
tmux -L "$SOCK" send-keys -t victim C-c 2>/dev/null; sleep 0.5
grep -q bystander <<<"$(tmux -L "$SOCK" list-sessions -F '#{session_name}')" \
  && pass "bystander pane survived" || fail "bystander pane DIED — containment failed"
CREW_AFTER="$(tmux list-sessions -F '#{session_name}' 2>/dev/null | sort | tr '\n' ' ')"
[[ "$CREW_BEFORE" == "$CREW_AFTER" ]] \
  && pass "every live crew session survived (${CREW_AFTER:-<none>})" \
  || fail "live crew changed: '$CREW_BEFORE' -> '$CREW_AFTER'"
echo "     user@1000.service pressure full avg10: peak $PSI_PEAK, longest run >50% = $((PSI_MAXRUN/2))s"
echo "     (oomd fires on >50.00% sustained for 20s — peak alone is NOT its condition)"
[[ $PSI_MAXRUN -lt 40 ]] \
  && pass "pressure never held >50% for oomd's 20s (longest $((PSI_MAXRUN/2))s, peak $PSI_PEAK)" \
  || fail "pressure held >50% for $((PSI_MAXRUN/2))s — oomd WOULD have killed a bystander"

echo
echo "=== arm 2b: the REJECTED shape (High<Max) grinds the slice — OPT-IN ONLY ==="
# ⛔ THIS ARM HURTS OTHER PEOPLE. It is off by default and that is not caution,
# it is a measured fact: run on 2026-09-04 it held user@1000.service pressure
# above oomd's 50% limit for 46-52s, and oomd then killed 17 processes in
# dearing's pane (tmux-spawn-e7b38589, 17:30:30) and gnome-shell (17:30:59).
# st respawned dearing a minute later. That is the SAME failure aegis-0j0n1n
# exists to fix, caused by the test written to prove the fix.
#
# The lesson is not "be careful", it is structural: arm 2a proves what we SHIP
# and is safe (peak 4.3% pressure, 0s sustained). Arm 2b proves what we
# REJECTED, and the only way to prove a shape is dangerous is to be dangerous.
# A regression suite that reaps live agents will be run once and then never
# again, so it would protect nothing. Keep the evidence, gate the reproduction.
#
# Before setting PANEMEM_TEST_PRESSURE=1: tell every live agent, or run it on a
# host with no crew on it.
if [[ "${PANEMEM_TEST_PRESSURE:-0}" != "1" ]]; then
  echo "    SKIPPED. This arm drives user@1000.service pressure past oomd's limit and"
  echo "    HAS killed live crew sessions and gnome-shell (2026-09-04 17:30, aegis-0j0n1n)."
  echo "    Set PANEMEM_TEST_PRESSURE=1 to run it, and warn the crew first."
  echo
  MY_MAX_END="$(prop "$MY_SCOPE" MemoryMax)"
  [[ "$MY_MAX_END" == "$MY_MAX_BEFORE" ]] \
    && pass "launcher scope unchanged so far ($MY_MAX_END)" \
    || fail "launcher scope changed: $MY_MAX_BEFORE -> $MY_MAX_END"
else
echo "    ⚠ RUNNING THE DANGEROUS ARM — live crew sessions may be OOM-killed."
MARK="$(date +%Y-%m-%d\ %H:%M:%S)"   # LOCAL: journalctl --since is local time
SHANTY_PANE_HIGH_GIB=2 SHANTY_PANE_MAX_GIB=3 python3 - "$SOCK" <<'DOOMED' >/dev/null
import sys
sys.path.insert(0, ".")
from shantytown.tmux import Tmux
Tmux(socket=sys.argv[1]).new_session("doomed")
DOOMED
DPID="$(tmux -L "$SOCK" list-panes -t doomed -F '#{pane_pid}')"
DSCOPE="$(scope "$DPID")"
DCG="/sys/fs/cgroup$(cgpath "$DPID")"
echo "doomed pid=$DPID scope=$DSCOPE memory.max=$(cat "$DCG/memory.max" 2>/dev/null)"
hog doomed 32
PSI_PEAK2=0; PSI_RUN2=0; PSI_MAXRUN2=0
for _ in $(seq 1 90); do
  p="$(psi)"
  if [[ -n "$p" ]]; then
    PSI_PEAK2="$(python3 -c "print(max($PSI_PEAK2,$p))")"
    if python3 -c "import sys; sys.exit(0 if $p > 50.0 else 1)"; then
      PSI_RUN2=$((PSI_RUN2+1)); [[ $PSI_RUN2 -gt $PSI_MAXRUN2 ]] && PSI_MAXRUN2=$PSI_RUN2
    else PSI_RUN2=0; fi
  fi
  SNAP="$(tr '\n' ' ' < "$DCG/memory.events" 2>/dev/null)"
  [[ -n "$SNAP" ]] && EV2="$SNAP"        # latch: the cgroup vanishes with the pane
  grep -qE 'oom_kill [1-9]' <<<"$SNAP" && break
  tmux -L "$SOCK" has-session -t doomed 2>/dev/null || break
  sleep 0.5
done
echo "     memory.events: $EV2"
HIGH_EV2="$(sed -n 's/^high //p' <<<"$(tr ' ' '\n' <<<"$EV2" | paste -d' ' - - 2>/dev/null)" 2>/dev/null)"
grep -qE 'high [1-9]' <<<"${EV2:-}" \
  && pass "the band throttled instead of killing, as documented (events: $EV2)" \
  || echo "     note: no throttle events seen this run (events: ${EV2:-<gone>})"
OOMLINE="$(journalctl -k --since "$MARK" 2>/dev/null | grep -F "oom-kill:constraint" | grep -F "$VSCOPE" | tail -1)"
if [[ -n "$OOMLINE" ]]; then
  grep -q "CONSTRAINT_MEMCG" <<<"$OOMLINE" \
    && pass "kernel confirms arm 2a's kill was CONSTRAINT_MEMCG on the victim's own scope" \
    || fail "wrong constraint: $OOMLINE"
  printf '     %s\n' "$(sed 's/.*oom-kill:/oom-kill:/' <<<"$OOMLINE" | cut -c1-190)"
else
  echo "     (no kernel line matched the scope; memory.events above is the authority)"
fi
tmux -L "$SOCK" send-keys -t doomed C-c 2>/dev/null; sleep 0.5
grep -q bystander <<<"$(tmux -L "$SOCK" list-sessions -F '#{session_name}')" \
  && pass "bystander pane survived the kill" || fail "bystander DIED"
CREW_AFTER2="$(tmux list-sessions -F '#{session_name}' 2>/dev/null | sort | tr '\n' ' ')"
[[ "$CREW_BEFORE" == "$CREW_AFTER2" ]] \
  && pass "every live crew session survived the kill (${CREW_AFTER2:-<none>})" \
  || fail "live crew changed: '$CREW_BEFORE' -> '$CREW_AFTER2'"
echo "     pressure full avg10: peak $PSI_PEAK2, longest run >50% = $((PSI_MAXRUN2/2))s"
[[ $PSI_MAXRUN2 -ge 20 ]] \
  && pass "band held pressure >50% for $((PSI_MAXRUN2/2))s — the measured reason it is not shipped" \
  || echo "     ⚠ band did NOT grind the slice this run ($((PSI_MAXRUN2/2))s >50%, peak $PSI_PEAK2)."
[[ $PSI_MAXRUN2 -ge 20 ]] || echo "       If this repeats, re-run the controlled experiment before trusting DEFAULT_HIGH_GIB."

fi   # end PANEMEM_TEST_PRESSURE gate

echo "=== arm 3: refuse a ceiling that is an immediate kill ==="
python3 - <<'PY'
import os, sys
sys.path.insert(0, ".")
from shantytown import panemem
scope = panemem.own_scope()
cur = panemem.current_bytes(scope) or 0
os.environ["SHANTY_PANE_HIGH_GIB"] = "0.001"
os.environ["SHANTY_PANE_MAX_GIB"] = "0.002"
# a pid that is genuinely in another scope would be needed to reach the guard,
# so drive it directly: the launcher check is arm 4's job.
lim = panemem.limits()
ok = cur >= lim.max
print(f"scope={scope} current={cur} max={lim.max} would_be_instant_kill={ok}")
sys.exit(0 if ok else 1)
PY
if [[ $? -eq 0 ]]; then pass "premise holds: a 2MB ceiling is below live usage"
else fail "could not construct the instant-kill premise"; fi
# now the real assertion, through bound_pane, against the bystander pane
OUT="$(SHANTY_PANE_HIGH_GIB=0.001 SHANTY_PANE_MAX_GIB=0.002 python3 - "$BPID" <<'PY'
import sys
sys.path.insert(0, ".")
from shantytown import panemem
a = panemem.bound_pane(sys.argv[1])
print(f"ok={a.ok} reason={a.reason}")
PY
)"
echo "     $OUT"
grep -q "ok=False" <<<"$OUT" && grep -qi "immediate memcg OOM kill\|reclaim thrash" <<<"$OUT" \
  && pass "bound_pane REFUSED an instant-kill ceiling" \
  || fail "bound_pane did not refuse: $OUT"
# and specifically the MemoryMax arm: high >= max clamps, so the max guard fires first
OUT2="$(SHANTY_PANE_HIGH_GIB=0.002 SHANTY_PANE_MAX_GIB=0.0005 python3 - "$BPID" <<'MAXG'
import sys
sys.path.insert(0, ".")
from shantytown import panemem
a = panemem.bound_pane(sys.argv[1])
print(f"ok={a.ok} reason={a.reason}")
MAXG
)"
echo "     $OUT2"
grep -q "ok=False" <<<"$OUT2" && grep -q "immediate memcg OOM kill" <<<"$OUT2" \
  && pass "bound_pane REFUSED a MemoryMax below current usage" \
  || fail "MemoryMax guard did not fire: $OUT2"
BMAX="$(prop "$(scope "$BPID")" MemoryMax)"
[[ "$BMAX" == "infinity" ]] && pass "bystander left unbounded after the refusal" \
                            || fail "bystander was bounded anyway: MemoryMax=$BMAX"

echo
echo "=== arm 4: refuse to bound the caller's own scope ==="
OUT="$(python3 - "$BPID" <<'PY'
import sys
sys.path.insert(0, ".")
from shantytown import panemem
# pretend the caller lives in the bystander's scope: the pane can then never
# resolve to anything else, which is exactly the losing arm of the race.
victim_scope = panemem.scope_of_pid(sys.argv[1])
a = panemem.bound_pane(sys.argv[1], launcher=victim_scope)
print(f"ok={a.ok} reason={a.reason}")
PY
)"
echo "     $OUT"
grep -q "ok=False" <<<"$OUT" && grep -q "LAUNCHER's own scope" <<<"$OUT" \
  && pass "bound_pane REFUSED the caller's own scope" \
  || fail "bound_pane did not refuse the caller's own scope: $OUT"
BMAX="$(prop "$(scope "$BPID")" MemoryMax)"
[[ "$BMAX" == "infinity" ]] && pass "caller's scope still unbounded" \
                            || fail "caller's scope was bounded: MemoryMax=$BMAX"

echo
MY_MAX_END="$(prop "$MY_SCOPE" MemoryMax)"
[[ "$MY_MAX_END" == "$MY_MAX_BEFORE" ]] \
  && pass "launcher scope unchanged across the whole run ($MY_MAX_END)" \
  || fail "launcher scope changed across the run: $MY_MAX_BEFORE -> $MY_MAX_END"

echo
if [[ $FAIL -eq 0 ]]; then echo "PASS — panemem bounds the launched pane and nothing else."; exit 0
else echo "FAIL — see ❌ above."; exit 1; fi
