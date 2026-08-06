#!/usr/bin/env bash
# probe-codex-stop-hooks — LIVE FIRE: does codex actually run a lead's Stop
# hooks, and does a hook's block reason reach the MODEL?
#
# WHY THIS IS A SCRIPT AND NOT A UNIT TEST. It is a claim about somebody else's
# program, and it costs model quota to answer, so it cannot run in CI. The suite
# can only pin what we BELIEVE about codex; this is how that belief was formed,
# kept re-runnable so the next person can re-measure instead of re-reasoning.
#
# WHAT WAS UNTESTED, and why it mattered enough to spend a live call on: `send`
# was proven end to end, `drain` was not. drain is the LEAD half -- a lead with
# seven reports that cannot drain silently swallows every one of their stop
# events, and the config declaring the hook looks perfect throughout. st emits
# send and drain as two hooks in ONE Stop group, so the specific risk was that
# codex ran the first and stopped, or stopped as soon as a hook returned a block
# decision.
#
# MEASURED, codex-cli 0.146.1:
#   * ALL THREE hooks ran. codex does not stop at the first.
#   * the hook AFTER the one returning {"decision":"block"} still ran, so a
#     block decision does not preempt the rest of the group.
#   * the reason REACHED THE MODEL -- it replied with the word the reason asked
#     for, which it could not have produced otherwise.
#   * the block drove a second turn, and the whole group ran again on the second
#     stop (block-once is what terminates it -- without it a block re-fires every
#     stop and the run never ends).
#   * ⚠ ORDER IS NOT GUARANTEED, and it is NONDETERMINISTIC rather than simply
#     reversed: one run's second cycle came out 2,1,3 and the next run's came out
#     1,2,3. So observing the declared order once proves nothing, which is the
#     trap -- a reader who runs this and sees 1,2,3 will conclude codex honours
#     the order. Nothing in st may depend on send preceding drain. It does not
#     today and this is why: send
#     writes THIS agent's stop event for its lead to collect, drain collects
#     events addressed to THIS agent. Different objects, so the two commute even
#     for an agent that is both a lead and a report.
#
# Hook bodies are SCRIPT FILES, never inline TOML strings: escaping a JSON block
# decision through TOML quoting is how the first run of this failed, and a probe
# that dies of a quoting error looks exactly like a probe that disproved the
# thing it was testing.
set -uo pipefail

command -v codex >/dev/null || { echo "no codex on PATH"; exit 2; }
SB=$(mktemp -d) || exit 2
MARK="$SB/marks"; FLAG="$SB/blocked-once"
mkdir -p "$SB/home" "$SB/ws"
: > "$MARK"

# The same symlink provision() makes, so no credential is ever copied.
[ -e "$HOME/.codex/auth.json" ] && ln -sf "$HOME/.codex/auth.json" "$SB/home/auth.json"

for n in 1 2 3; do
  printf '#!/bin/sh\necho HOOK%s_RAN >> "%s"\n' "$n" "$MARK" > "$SB/h$n.sh"
done
# hook 2 is the drain analogue: emits the block protocol ONCE, then allows.
cat > "$SB/h2.sh" <<EOF
#!/bin/sh
echo HOOK2_RAN >> "$MARK"
if [ ! -f "$FLAG" ]; then
  : > "$FLAG"
  printf '%s' '{"decision":"block","reason":"reply with the single word DRAINED and nothing else."}'
fi
exit 0
EOF
chmod +x "$SB"/h*.sh

{
  echo 'project_doc_max_bytes = 262144'
  echo
  echo '[[hooks.Stop]]'
  for n in 1 2 3; do
    echo
    echo '[[hooks.Stop.hooks]]'
    echo 'type = "command"'
    echo "command = \"$SB/h$n.sh\""
  done
} > "$SB/home/config.toml"

cd "$SB/ws" || exit 2
CODEX_HOME="$SB/home" timeout 240 codex exec \
  --dangerously-bypass-hook-trust \
  --dangerously-bypass-approvals-and-sandbox \
  "Reply with the single word OK." > "$SB/out.txt" 2>"$SB/err.txt"
rc=$?

ran=$(sort -u "$MARK" 2>/dev/null | wc -l)
echo "codex exit=$rc; distinct hooks that ran: $ran/3"
echo "--- marks, in the order codex ran them ---"; cat "$MARK"
fail=0
[ "$ran" -eq 3 ] || { echo "FAIL: not every Stop hook ran — a lead would not drain"; fail=1; }
if grep -qi "DRAINED" "$SB/out.txt" 2>/dev/null; then
  echo "PASS: the block reason reached the MODEL"
else
  echo "FAIL: the block reason did NOT reach the model — drain would be write-only"; fail=1
fi
echo "sandbox kept for inspection: $SB"
exit "$fail"
