#!/usr/bin/env bash
# probe-codex-stop-stdout — LIVE FIRE: what does codex do with a Stop hook that
# writes NON-JSON to stdout, and what does it do with one that writes NOTHING?
#
# WHY THIS EXISTS (aegis-6ab8hd). Two codex workers reported
#   "Stop hook (failed)  error: hook returned invalid stop hook JSON output"
# and the first hypothesis was that codex rejects EMPTY stdout — which, if true,
# would mean every st Stop hook has been failing on codex since it was written,
# because `stop_event send` and `stop_event haul` both exit 0 with zero bytes on
# the ordinary path (measured, byte-exact, against a sandbox root).
#
# That hypothesis is worth a live call precisely because acting on it would be
# expensive and wrong: it points at stop_event, and the actual leak is a
# different hook in the same group. Two arms separate them.
#
#   ARM A  every hook silent (0 bytes, rc 0)      -> is empty stdout rejected?
#   ARM B  one hook prints a plain-text summary   -> is non-JSON rejected?
#
# Same shape as probe-codex-stop-hooks.sh, and the same reason it is a script
# and not a unit test: it is a claim about somebody else's program, it costs
# model quota, and the belief it forms should be re-measurable rather than
# re-reasoned. codex's Stop contract was last measured at 0.146.1; the host now
# runs a later build, and a contract nobody re-measured after an upgrade is the
# thing this repo keeps getting caught by.
set -uo pipefail

command -v codex >/dev/null || { echo "no codex on PATH"; exit 2; }
echo "codex: $(codex --version 2>&1 | head -1)"
SB=$(mktemp -d) || exit 2
mkdir -p "$SB/home-a" "$SB/home-b" "$SB/ws"
[ -e "$HOME/.codex/auth.json" ] && ln -sf "$HOME/.codex/auth.json" "$SB/home-a/auth.json"
[ -e "$HOME/.codex/auth.json" ] && ln -sf "$HOME/.codex/auth.json" "$SB/home-b/auth.json"

# ARM A: silent. Writes its mark to a FILE, so stdout is genuinely 0 bytes —
# the exact shape of `stop_event send` and of `haul` with an empty queue.
printf '#!/bin/sh\necho ran >> "%s/a.marks"\nexit 0\n' "$SB" > "$SB/silent.sh"
# ARM B: the leak, reproduced in miniature. This is what st-history-stop-hook.sh
# does today — it never redirects its two child scripts, whose normal human
# summaries ("captured=1 skipped_unchanged=18 bytes=...") go to stdout.
cat > "$SB/chatty.sh" <<'EOF'
#!/bin/sh
echo ran >> "$MARKS"
echo "21:51:37Z captured=1 skipped_unchanged=18 bytes=5831700 agent=probe dest=/tmp/h"
echo "scrubbed 1 file(s) for 'probe', 0 credential-shaped value(s) replaced"
exit 0
EOF
chmod +x "$SB/silent.sh" "$SB/chatty.sh"

group() {   # $1 = home dir, rest = hook scripts
  local home="$1"; shift
  { echo 'project_doc_max_bytes = 262144'; echo; echo '[[hooks.Stop]]'
    for h in "$@"; do
      echo; echo '[[hooks.Stop.hooks]]'; echo 'type = "command"'
      echo "command = \"$h\""
    done; } > "$home/config.toml"
}
group "$SB/home-a" "$SB/silent.sh" "$SB/silent.sh"
group "$SB/home-b" "$SB/silent.sh" "$SB/chatty.sh"

run() {     # $1 = label, $2 = home dir
  cd "$SB/ws" || exit 2
  MARKS="$SB/$1.marks" CODEX_HOME="$2" timeout 240 codex exec \
    --dangerously-bypass-hook-trust \
    --dangerously-bypass-approvals-and-sandbox \
    "Reply with the single word OK." > "$SB/$1.out" 2>"$SB/$1.err"
  local n
  # ⚠ MATCH THE SURFACE YOU ARE ACTUALLY RUNNING. The TUI prints
  #   "• Stop hook (failed)  error: hook returned invalid stop hook JSON output"
  # but `codex exec` prints only "hook: Stop Failed" — the same verdict, none of
  # the same words. The first version of this probe grepped for the TUI text and
  # got 0 in BOTH arms, which reads exactly like "non-JSON is accepted" and would
  # have cleared the real cause. Count the verdict line, not the prose.
  n=$(cat "$SB/$1.out" "$SB/$1.err" 2>/dev/null \
      | grep -cE "Stop Failed|invalid stop hook JSON output")
  echo "$n"
}

a=$(MARKS="$SB/a.marks" run a "$SB/home-a")
b=$(MARKS="$SB/b.marks" run b "$SB/home-b")
echo "ARM A (all hooks silent, 0 bytes): failed-hook verdicts x$a"
echo "ARM B (one hook prints plain text): failed-hook verdicts x$b"

fail=0
[ "$a" -eq 0 ] || { echo "UNEXPECTED: codex rejects EMPTY stdout — every st Stop hook is affected, not just the chatty one"; fail=1; }
[ "$b" -gt 0 ] || { echo "UNEXPECTED: codex accepted NON-JSON stdout — the leak is not the cause; look elsewhere"; fail=1; }
[ "$fail" -eq 0 ] && echo "CONFIRMED: empty stdout is fine; NON-JSON stdout is what codex rejects."
echo "sandbox kept for inspection: $SB"
exit "$fail"
