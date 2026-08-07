#!/usr/bin/env bash
# probe-codex-pretooluse — LIVE FIRE: what is codex's PreToolUse MATCHER
# VOCABULARY, and does a guard emitted with it actually refuse?
#
# WHY THIS EXISTS (aegis-610jv). codex agents run without bd-store-guard and
# crew-only-guard, because those are MATCHER-SCOPED PreToolUse hooks and a
# matcher is a claim about the host program's TOOL NAMES. Claude Code's are
# "Bash" and "mcp__.*". codex's are its own, and this repo has already paid for
# one matcher that looked specific and fired zero times (aegis-ac5x/18e0). A
# guard emitted with the wrong program's vocabulary is not a weaker guard — it
# is a guard that never runs WHILE READING AS WIRED, which is strictly worse
# than no guard, because it retires the question.
#
# So codex.py's MATCHERS_NOT_EMITTED was the right call on the evidence then
# available (no codex binary on the host that wrote it). There is one now. This
# script is how that constant gets retired: by measurement, not by reasoning.
#
# WHAT IT MEASURES, in ONE model call, because each run costs quota:
#   1. THE PAYLOAD. A matcher-less PreToolUse hook dumps every payload it is
#      handed. That yields the real tool_name and the real tool_input SHAPE —
#      the two facts a guard needs and neither of which can be read off the
#      binary with confidence.
#   2. WHICH MATCHER FIRES. Candidate matchers each get their own hook and their
#      own witness file, so the answer is "these fired, those did not" rather
#      than one yes/no. A candidate that fires zero times is a RESULT here, not
#      a failure of the probe — it is precisely the aegis-ac5x defect caught
#      before it ships.
#   3. THAT BLOCKING WORKS AT ALL, and in BOTH directions: one command that must
#      be refused and one that must pass. A guard that refuses everything is not
#      a guard, and a probe that only tests the refusal cannot tell the two
#      apart.
#
# Hook bodies are SCRIPT FILES, never inline TOML strings — the same rule
# probe-codex-stop-hooks.sh learned the hard way: escaping a JSON decision
# through TOML quoting is how its first run failed, and a probe that dies of a
# quoting error looks exactly like a probe that disproved what it was testing.
#
# Read the RESULTS block this prints. Do not read the exit code alone: exit 0
# here means "the probe answered its questions", and one of the answers it can
# legitimately return is "no candidate matcher fired", which is a finding.
set -uo pipefail

command -v codex >/dev/null || { echo "no codex on PATH"; exit 2; }
SB=$(mktemp -d) || exit 2
mkdir -p "$SB/home" "$SB/ws" "$SB/seen"

# The same symlink provision() makes, so no credential is ever copied.
[ -e "$HOME/.codex/auth.json" ] && ln -sf "$HOME/.codex/auth.json" "$SB/home/auth.json"

# The two marker commands. ALLOW_MARK must run; BLOCK_MARK must not.
ALLOW_MARK="PROBE_ALLOWED_OK"
BLOCK_MARK="probe_forbidden_command"

# ── the matcher-less observer: dumps every payload it is handed ──────────────
cat > "$SB/observe.sh" <<EOF
#!/bin/sh
cat >> "$SB/payloads.jsonl"
printf '\n' >> "$SB/payloads.jsonl"
exit 0
EOF

# ── one witness per candidate matcher ────────────────────────────────────────
# Candidates are drawn from what the deployed binary's strings suggest AND from
# the neighbouring program's vocabulary, deliberately including ones we expect
# to fail: a candidate list containing only the likely answer cannot tell you
# the matcher is doing any filtering at all.
CANDIDATES="shell exec_command unified_exec local_shell bash apply_patch Bash"
for c in $CANDIDATES; do
  cat > "$SB/m_$c.sh" <<EOF
#!/bin/sh
echo "$c" >> "$SB/seen/fired"
exit 0
EOF
done

# ── the blocker: refuses ONLY the forbidden marker, allows everything else ───
# This is the shape a real guard has, so proving it here proves the shape.
cat > "$SB/blocker.sh" <<EOF
#!/bin/sh
IN="\$(cat)"
printf '%s' "\$IN" >> "$SB/blocker-saw.jsonl"
printf '\n' >> "$SB/blocker-saw.jsonl"
case "\$IN" in
  *$BLOCK_MARK*)
    echo "BLOCKED_BY_PROBE_GUARD" >> "$SB/seen/blocked"
    echo "refused by probe guard: $BLOCK_MARK is forbidden on this host" >&2
    exit 2
    ;;
esac
exit 0
EOF
chmod +x "$SB"/*.sh

# ── the config ───────────────────────────────────────────────────────────────
{
  echo 'project_doc_max_bytes = 262144'
  echo
  # matcher-less group: observer + blocker. A group with NO matcher key is the
  # control — if this fires and the matcher groups do not, the matcher is the
  # variable, which is the whole question.
  echo '[[hooks.PreToolUse]]'
  echo
  echo '[[hooks.PreToolUse.hooks]]'
  echo 'type = "command"'
  echo "command = \"$SB/observe.sh\""
  echo
  echo '[[hooks.PreToolUse.hooks]]'
  echo 'type = "command"'
  echo "command = \"$SB/blocker.sh\""
  for c in $CANDIDATES; do
    echo
    echo '[[hooks.PreToolUse]]'
    echo "matcher = \"$c\""
    echo
    echo '[[hooks.PreToolUse.hooks]]'
    echo 'type = "command"'
    echo "command = \"$SB/m_$c.sh\""
  done
} > "$SB/home/config.toml"

cd "$SB/ws" || exit 2
CODEX_HOME="$SB/home" timeout 300 codex exec \
  --dangerously-bypass-hook-trust \
  --dangerously-bypass-approvals-and-sandbox \
  "Run exactly these two shell commands, in this order, and then stop:
   1. $BLOCK_MARK
   2. echo $ALLOW_MARK
   Do not use any other tool. If a command is refused, say so and continue to the next one." \
  > "$SB/out.txt" 2>"$SB/err.txt"
rc=$?

echo "=============================================================="
echo "RESULTS — codex $(codex --version 2>/dev/null | head -1); exit=$rc"
echo "=============================================================="

echo
echo "--- 1. PAYLOAD: tool_name values the matcher-less hook actually saw ---"
if [ -s "$SB/payloads.jsonl" ]; then
  python3 - "$SB/payloads.jsonl" <<'PY'
import json,sys
names={}
shape=None
for line in open(sys.argv[1]):
    line=line.strip()
    if not line: continue
    try: d=json.loads(line)
    except Exception: continue
    n=d.get("tool_name")
    names[n]=names.get(n,0)+1
    if shape is None and isinstance(d.get("tool_input"),dict):
        shape=sorted(d["tool_input"].keys())
    if n and "event" not in names:
        pass
for n,c in sorted(names.items(), key=lambda kv:-kv[1]):
    print(f"    tool_name={n!r}  x{c}")
print(f"    tool_input keys: {shape}")
PY
  echo "    (raw payloads: $SB/payloads.jsonl)"
else
  echo "    NOTHING — the matcher-less PreToolUse hook never ran."
  echo "    That is the headline result: codex PreToolUse did not fire at all."
fi

echo
echo "--- 2. MATCHER: which candidates fired ---"
for c in $CANDIDATES; do
  if grep -qx "$c" "$SB/seen/fired" 2>/dev/null; then
    echo "    FIRED     matcher=\"$c\""
  else
    echo "    silent    matcher=\"$c\""
  fi
done

echo
echo "--- 3. BLOCK / ALLOW: both outcomes ---"
if [ -s "$SB/seen/blocked" ]; then
  echo "    PASS: the guard refused $BLOCK_MARK (exit 2 reached codex)"
else
  echo "    FAIL: the guard never refused — exit 2 did not block, or never ran"
fi
if grep -q "$ALLOW_MARK" "$SB/out.txt" 2>/dev/null; then
  echo "    PASS: the allowed command still ran ($ALLOW_MARK in transcript)"
else
  echo "    FAIL: the allowed command did NOT run — a guard that refuses"
  echo "          everything is not a guard"
fi

echo
echo "transcript: $SB/out.txt"
echo "sandbox kept for inspection: $SB"
exit 0
