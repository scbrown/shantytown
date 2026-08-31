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
# WHERE the ticket rule applies — a git pathspec list, projected from the graph
# (aegis-4boql). Ticket IDs are refused in USER-FACING artefacts (a published
# CHANGELOG, README, docs/) and ALLOWED in source comments and docstrings, which
# is this fleet's documentation convention and the reason its comments are worth
# reading: a citation next to the code is how the reasoning stays findable.
#
# WHY THE SCOPE, not just a softer tier: a guard that refuses substantially every
# normal push does not prevent the push, it trains the pusher to reach for
# --no-verify without reading the finding — and the next finding might be a real
# hostname. Measured on shantytown before this landed: 92 distinct ticket ids
# were ALREADY public across 101 files, README among them, so refusing the 93rd
# protected nothing while manufacturing exactly that reflex.
#
# EMPTY MEANS EVERYWHERE, deliberately. An old or un-regenerated config must keep
# TODAY's behaviour rather than silently checking nothing — a scope that fails
# open is a guard that reports success while enforcing less than it says.
# (Named TICKET_PATHS here until aegis-o4a3k; the reader below is
# TICKET_EXEMPT_RE, so the rename left this variable with no default at all.
# Under `set -u` a config missing the key then killed ticket_files' subshell —
# an unbound-variable error on stderr and the ticket arm silently skipped for
# that commit. Not hypothetical: the stale emitter in aegis-akh22 emitted no
# ticket_exempt_path_re at all, which is exactly this config.)
TICKET_EXEMPT_RE=""
if [ -r "$CONF" ]; then
  # shellcheck disable=SC1090
  internal_host_re=""; patterns=""
  while IFS='=' read -r k v; do
    case "$k" in
      internal_host_re) INTERNAL_HOST_RE="$v" ;;
      patterns)         PATTERNS="$v" ;;
      ticket_patterns)  TICKET_PATTERNS="$v" ;;
      ticket_exempt_path_re) TICKET_EXEMPT_RE="$v" ;;
    esac
  done < "$CONF"
fi

# ── A PRESENT-BUT-BROKEN CONFIG DISARMS THIS GUARD, AND ONLY --selftest LOOKED ──
# The no-config case above fails open LOUDLY, on purpose. The broken-config case
# failed open SILENTLY, which is the worse half, and it is the half that happened.
#
# aegis-m3jpf: the governed private-IPv4 rule reached this file as PCRE `\d{1,3}`.
# POSIX ERE has no `\d`, so grep -E read it as a literal letter d, the arm required
# a `d` inside an IP address, and it matched nothing across five public repos.
# aegis-akh22: the SAME break, four weeks later, from regenerating the config out of
# a 426-commit-stale checkout of the emitter. Both times --selftest caught it. Both
# times only because somebody ran --selftest. Nothing looked at push time, and push
# time is the only moment that decides anything.
#
# Three ways a config disarms this guard. All three measured on this host, with the
# live grep, under aegis-o4a3k:
#
#   1. `patterns` is not a valid ERE   -> grep -E exits 2 on every call, and every
#                                         call site swallows it with `|| true`, so
#                                         the push is reported CLEAN and allowed
#   2. `patterns` is valid but VACUOUS -> the `\d` case: compiles, matches nothing
#   3. `internal_host_re` is empty     -> `grep -qE ""` matches EVERY url, so every
#                                         push looks like an internal-forge push and
#                                         exits 0 before scanning a single commit
#
# (3) is the widest and the least visible: the guard is entirely off for public
# pushes and prints nothing at all. It is not a what-if — the stale emitter in
# aegis-akh22 dropped `ticket_exempt_path_re` from this same file, so silently
# dropping a key is a MEASURED behaviour of this config path.
#
# WHAT WE DO ABOUT IT: enforce MORE, never less, and say so on stderr. Same rule the
# ticket scope above already follows. We deliberately do NOT refuse the push over a
# broken config — the pusher did not break it, and a guard that refuses everything
# is a guard that gets --no-verify'd, which is how we end up with no guard at all.
#
# RFC1918 is the one arm reconstructible WITHOUT the estate's names: it is in the
# RFC, not in anybody's topology. That is what lets the fallback live in a public
# repository, and it is why it is the only arm we can rebuild from in here.
# The trailing class is `[^0-9]`, not `[^0-9.]`: excluding the dot would have made
# the fallback miss an address at the end of a sentence ("... reaches 172.16.4.9.")
# to avoid matching a five-part version string, and prose is where these actually
# leak. It over-matches x.y.z.w.v by design — loud is the right error here, since
# this arm only runs at all when the config is ALREADY broken and already shouting.
BUILTIN_RFC1918='(^|[^0-9.])(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3})([^0-9]|$)'

# ere_usable <regex> — can grep -E actually COMPILE it? An invalid regex exits 2; a
# valid one that simply does not match exits 1. Every `|| true` in this file erases
# that difference, which is defect (1) above in one character.
ere_usable() { printf '' | grep -qE "$1" 2>/dev/null; [ "$?" -lt 2 ]; }

# harden_config — called on the PUBLIC-push path only, so a sound config costs three
# greps on three short strings and an internal push is not nagged. Repairs PATTERNS
# toward MORE coverage and leaves the reason in CONFIG_DEGRADED (empty = sound).
CONFIG_DEGRADED=""
harden_config() {
  if [ -n "$TICKET_PATTERNS" ] && ! ere_usable "$TICKET_PATTERNS"; then
    # Safe to blank: the ticket call site is already `[ -n "$TICKET_PATTERNS" ]`-gated.
    # An invalid regex there enforces nothing anyway, loudly and per-commit; blanking
    # it turns that into one honest sentence instead of a wall of grep errors.
    CONFIG_DEGRADED="ticket_patterns= is not a valid POSIX ERE, so the ticket-id arm
  is OFF for this push. Internal-name checking below is unaffected."
    TICKET_PATTERNS=""
  fi
  [ -z "$PATTERNS" ] && return 0        # the unconfigured path below owns that case
  if ! ere_usable "$PATTERNS"; then
    CONFIG_DEGRADED="patterns= is not a valid POSIX ERE. grep -E refused it, and this
  guard swallows that exit status at every call site, so EVERY internal-name check in
  this push would have found nothing and reported the push clean."
    PATTERNS="$BUILTIN_RFC1918"
    return 0
  fi
  # Non-vacuity, one probe per RFC1918 block — it was ONE arm that was dead in
  # aegis-m3jpf and a single probe would have kept missing the other two. These
  # addresses are top-of-range in each block, in use on no estate this guard
  # protects, so they exercise every arm and publish nothing about anyone.
  for probe in 10.255.255.1 172.31.255.1 192.168.255.1; do
    printf '%s\n' "$probe" | grep -qE "$PATTERNS" && continue
    CONFIG_DEGRADED="patterns= does not match private address space (probe $probe).
  That is the aegis-m3jpf/aegis-akh22 failure: a PCRE construct such as \\d reaching a
  grep -E consumer, or an emitter that dropped the arm. The rest of patterns= still
  applies; the private-address arm below is this guard's own reconstruction."
    PATTERNS="$PATTERNS|$BUILTIN_RFC1918"
    return 0
  done
  return 0
}

warn_degraded() {
  [ -z "$CONFIG_DEGRADED" ] && return 0
  echo "⚠ pre-push-scrub-guard: THE CONFIG WAS ENFORCING LESS THAN IT SAYS." >&2
  echo "  $CONFIG_DEGRADED" >&2
  echo "  Fix: regenerate from a checkout you have JUST FETCHED (a stale emitter is" >&2
  echo "       how this breaks — aegis-akh22), then run:  $0 --selftest" >&2
  echo "  Refs: aegis-m3jpf, aegis-akh22, aegis-o4a3k" >&2
}

# ticket_files <from> <to|commit> — the changed files this rule GOVERNS, i.e.
# every changed file its exemption regex does not name. ONE definition, shared
# with hank, which matches the identical string against the same repo-relative
# path (aegis-rdclc). Unset regex -> every file, the pre-scope behaviour, so a
# stale config enforces MORE than the graph asks and never less.
ticket_files() {
  if [ -n "${2:-}" ]; then
    names=$(git diff --name-only "$1" "$2" -- . "${GUARD_EXCLUDE[@]}" 2>/dev/null || true)
  else
    names=$(git show --format= --name-only "$1" -- . "${GUARD_EXCLUDE[@]}" 2>/dev/null || true)
  fi
  [ -n "$TICKET_EXEMPT_RE" ] && names=$(printf '%s\n' "$names" | grep -vE "$TICKET_EXEMPT_RE" || true)
  printf '%s\n' "$names" | grep -v '^$' || true
}

if [ "${1:-}" = "--selftest" ]; then
  # Synthesises its own config, so the controls run without the real names ever
  # appearing in this repo — and so the test proves the MECHANISM, which is the
  # only thing this file is now responsible for.
  SELF=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")   # abs path; the reachability test cd's away
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
  # ── THE LIVE CONFIG, not a stand-in (aegis-m3jpf) ──────────────────────────
  # Everything above asserts on a SYNTHETIC pattern, and this file used to say
  # that proving the mechanism was "the only thing this file is now responsible
  # for". That scoping IS the defect it let through.
  #
  # The governed private-IPv4 rule reaches this guard as `\d{1,3}` — PCRE. POSIX
  # ERE has no `\d`, so grep -E reads it as a literal letter d, and the arm
  # required a `d` inside an IP address: it matched NOTHING, for five public
  # repos, while every control above passed. A test that cannot fail for the
  # reason the system can is not a test of the system.
  #
  # So these assert on the LOADED config. The probes stay generic — RFC1918 space
  # is universal and discloses nothing about any estate — because the failure
  # being caught is a DIALECT failure, not a content one. The estate's own names
  # are never needed and never appear.
  if [ -r "$CONF" ]; then
    LIVE=$(sed -n 's/^patterns=//p' "$CONF")
    if [ -z "$LIVE" ]; then
      echo "FAIL live config has no patterns= line"; fail=1
    else
      if printf '%s' "$LIVE" | grep -qE '\\[dDwWsSpPQEK]|\(\?'; then
        echo "FAIL live patterns carry a PCRE construct grep -E cannot read"; fail=1
      else
        echo "ok   live patterns are ERE-clean"
      fi
      # Non-vacuity. This is the assertion that was missing: the rule must FIRE.
      # One probe per RFC1918 arm, because it was ONE arm that was dead and a
      # single probe would have kept missing the other two.
      #
      # These addresses are deliberately ones NOT IN USE on any estate this guard
      # protects — top-of-range in each block. They exercise every arm and publish
      # nothing about anyone's topology, which is what lets them live in a public
      # repository without a path exclusion.
      probe_fail=0
      for probe in 10.255.255.1 172.31.255.1 192.168.255.1; do
        printf '%s\n' "$probe" | grep -qE "$LIVE" || { echo "FAIL live patterns miss a private-range arm"; probe_fail=1; }
      done
      if [ "$probe_fail" -eq 0 ]; then
        echo "ok   live patterns detect all three private ranges"
      else
        fail=1
      fi
      # ...and must still not cry wolf, or it gets switched off.
      if printf '8.8.8.8 is a public resolver\n' | grep -qE "$LIVE"; then
        echo "FAIL live patterns fire on public address space"; fail=1
      else
        echo "ok   live patterns silent on public space"
      fi
    fi
  else
    # An absent config is NOT a pass. A guard with no live config has nothing to
    # say about the live config and must say so rather than print ok.
    echo "SKIP live-config controls — no config at \$CONF (this is NOT a pass)"
  fi

  # The unconfigured path must be VISIBLE, never silent.
  out=$(SCRUB_PATTERNS_FILE=/nonexistent "$0" --check-unconfigured 2>&1 >/dev/null)
  case "$out" in *"NOT CONFIGURED"*) echo "ok   unconfigured is loud" ;; *) echo "FAIL unconfigured is silent"; fail=1 ;; esac

  # NEW-REF REACHABILITY (aegis-5c9z): a real bare remote + clone, because this
  # control is about ref-walking, not pattern matching. A tag pointing at
  # already-public history must ADD nothing; a new branch with a new leak must
  # still be caught. Reserved .invalid names only.
  if command -v git >/dev/null 2>&1; then
    r=$(mktemp -d); (
      cf="$r/scrub.conf"
      printf 'internal_host_re=forge\\.invalid\npatterns=[a-z0-9-]+\\.invalid\\b\n' > "$cf"
      git init -q --bare "$r/pub.git"
      git clone -q "$r/pub.git" "$r/w" 2>/dev/null
      cd "$r/w"
      git config user.email t@t; git config user.name t
      echo "host old-thing.invalid" > f.txt   # existing public debt
      git add f.txt; git commit -qm "seed old-thing.invalid"
      git push -q origin HEAD:main; git fetch -q origin 2>/dev/null
      Z=0000000000000000000000000000000000000000
      # tag on public commit -> allow
      git tag v1
      if echo "refs/tags/v1 $(git rev-parse v1) refs/tags/v1 $Z" | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" >/dev/null 2>&1; then
        echo "ok   tag on public history is allowed"
      else echo "FAIL tag on public history refused (aegis-5c9z)"; exit 1; fi
      # new branch adding a new leak -> refuse
      git checkout -q -b feat; echo "new bad-thing.invalid" > g.txt; git add g.txt; git commit -qm feat
      if echo "refs/heads/feat $(git rev-parse feat) refs/heads/feat $Z" | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" >/dev/null 2>&1; then
        echo "FAIL new leak in new branch was allowed"; exit 1
      else echo "ok   new leak in a new branch is refused"; fi
    ) || fail=1
    rm -rf "$r"
  fi

  # ── A BROKEN CONFIG MUST NOT DISARM THE GUARD (aegis-o4a3k) ────────────────
  # Every arm above proves the guard works when the config is SOUND. None of them
  # could fail for the reason the guard has actually failed twice: a config that
  # loads cleanly and enforces nothing. So these arms break the config on purpose,
  # three ways, and assert a real leak is still REFUSED — end to end, through the
  # hook's own stdin protocol, not by inspecting a variable.
  #
  # Each of the three was measured ALLOWING this exact push before the fix.
  # Reserved space only: 192.168.255.1 is top-of-range RFC1918, on no estate.
  if command -v git >/dev/null 2>&1; then
    r=$(mktemp -d); (
      set -e
      git init -q --bare "$r/pub.git"
      git clone -q "$r/pub.git" "$r/w" 2>/dev/null
      cd "$r/w"
      git config user.email t@t; git config user.name t
      echo "seed" > f.txt; git add f.txt; git commit -qm seed
      git push -q origin HEAD:main; git fetch -q origin 2>/dev/null
      git checkout -q -b leak
      echo "addr 192.168.255.1" > leak.txt; git add leak.txt; git commit -qm "a private address"
      Z=0000000000000000000000000000000000000000
      LINE="refs/heads/leak $(git rev-parse leak) refs/heads/leak $Z"
      # All three RFC1918 blocks: that is the invariant harden_config and the
      # live-config control both assert, and a fixture narrower than the invariant
      # would make the sound-config arm report a defect that is not one.
      GOOD='(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]{1,3}(\.[0-9]{1,3}){1,2}'

      # 1. NO internal_host_re. `grep -qE ""` matches every url, so the guard used
      #    to exit 0 here before scanning anything — fully off, silently, on a
      #    PUBLIC push. This is the widest of the three.
      cf="$r/c1"; printf 'patterns=%s\n' "$GOOD" > "$cf"
      out=$(echo "$LINE" | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" 2>&1); rc=$?
      # rc alone is not enough: ANY non-zero exit reads as a refusal, including a
      # crash. Require the guard's own banner AND the address it found, so the arm
      # can only pass for the reason it is testing.
      if [ "$rc" -eq 0 ]; then
        echo "FAIL empty internal_host_re let a public leak through (the guard was OFF)"; exit 1
      elif printf '%s' "$out" | grep -q '✗ REFUSED' && printf '%s' "$out" | grep -q '192.168.255.1'; then
        echo "ok   empty internal_host_re still scans as public"
      else echo "FAIL exited non-zero without refusing on the address — a crash, not a decision"; exit 1; fi

      # 1b. THE CONTROL, and it is the one that matters: enforcing more must not
      #     cost the internal forge its exemption. A named forge still exits 0.
      cf="$r/c1b"; printf 'internal_host_re=forge\\.invalid\npatterns=%s\n' "$GOOD" > "$cf"
      out=$(echo "$LINE" | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" internal 'ssh://git@forge.invalid/x/y.git' 2>&1); rc=$?
      if [ "$rc" -ne 0 ]; then echo "FAIL a named internal forge was refused"; exit 1
      elif printf '%s' "$out" | grep -q 'ENFORCING LESS'; then
        echo "FAIL a sound config was reported as degraded"; exit 1
      else echo "ok   a named internal forge is still exempt, and stays quiet"; fi

      # 2. patterns= is not a valid ERE. grep -E exits 2 at every call site and
      #    every one of them says `|| true`, so the push read CLEAN.
      cf="$r/c2"; printf 'internal_host_re=forge\\.invalid\npatterns=192\\.168\\.(\n' > "$cf"
      out=$(echo "$LINE" | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" 2>&1); rc=$?
      if [ "$rc" -eq 0 ]; then echo "FAIL an unparseable patterns= let a leak through"; exit 1
      elif printf '%s' "$out" | grep -q 'ENFORCING LESS' \
           && printf '%s' "$out" | grep -q '✗ REFUSED' \
           && printf '%s' "$out" | grep -q '192.168.255.1'; then
        echo "ok   an unparseable patterns= falls back and says so"
      else echo "FAIL refused, but not on the address and/or without naming the broken config"; exit 1; fi

      # 3. THE aegis-m3jpf/akh22 BREAK ITSELF, verbatim: PCRE \d in an ERE
      #    consumer. Compiles, matches nothing, five public repos, four weeks.
      cf="$r/c3"; printf 'internal_host_re=forge\\.invalid\npatterns=192\\.168\\.\\d{1,3}\\.\\d{1,3}\n' > "$cf"
      out=$(echo "$LINE" | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" 2>&1); rc=$?
      if [ "$rc" -eq 0 ]; then echo "FAIL the \\d break still lets a private address through"; exit 1
      elif printf '%s' "$out" | grep -q 'ENFORCING LESS' \
           && printf '%s' "$out" | grep -q '✗ REFUSED' \
           && printf '%s' "$out" | grep -q '192.168.255.1'; then
        echo "ok   a vacuous private-address arm is rebuilt and reported"
      else echo "FAIL refused, but not on the address and/or without naming the vacuous arm"; exit 1; fi

      # 4. CRY-WOLF CONTROL. A sound config on a clean push must be silent and
      #    exit 0 — otherwise the warnings above become noise and get ignored,
      #    which is the failure mode that ends with --no-verify.
      git checkout -q -b clean origin/main
      echo "nothing here" > ok.txt; git add ok.txt; git commit -qm "clean"
      cf="$r/c4"; printf 'internal_host_re=forge\\.invalid\npatterns=%s\n' "$GOOD" > "$cf"
      out=$(echo "refs/heads/clean $(git rev-parse clean) refs/heads/clean $Z" \
            | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" 2>&1); rc=$?
      if [ "$rc" -ne 0 ]; then echo "FAIL a sound config refused a clean push"; exit 1
      elif [ -n "$out" ]; then echo "FAIL a sound config printed: $out"; exit 1
      else echo "ok   a sound config is silent on a clean push"; fi
    ) || fail=1
    rm -rf "$r"
  fi

  # TICKET SCOPE (aegis-4boql). BOTH OUTCOMES, on a real repo, because the whole
  # ruling is that the rule must fire in one place and not another — and a scope
  # is exactly the kind of change that can silently degrade into "never fires".
  # A rule proven only to stay quiet is indistinguishable from a rule that was
  # switched off.
  if command -v git >/dev/null 2>&1; then
    r=$(mktemp -d); (
      cf="$r/scrub.conf"
      # Ticket rule SCOPED to user-facing artefacts; host rule unscoped as always.
      printf 'internal_host_re=forge\\.invalid\npatterns=[a-z0-9-]+\\.invalid\\b\nticket_patterns=\\bzz-[a-z0-9]{3,6}\\b\nticket_exempt_path_re=\\.(py|rs|sh)$\n' > "$cf"
      git init -q --bare "$r/pub.git"
      git clone -q "$r/pub.git" "$r/w" 2>/dev/null
      cd "$r/w"
      git config user.email t@t; git config user.name t
      echo seed > seed.txt; git add seed.txt; git commit -qm seed
      git push -q origin HEAD:main; git fetch -q origin 2>/dev/null
      Z=0000000000000000000000000000000000000000

      # 1. ticket id in a SOURCE COMMENT -> ALLOWED (the convention)
      git checkout -q -b src; mkdir -p pkg
      echo '# see zz-1a2b for why this is here' > pkg/mod.py
      git add pkg/mod.py; git commit -qm "src comment"
      if echo "refs/heads/src $(git rev-parse src) refs/heads/src $Z" | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" >/dev/null 2>&1; then
        echo "ok   ticket in a source comment is allowed"
      else echo "FAIL ticket in a source comment refused (aegis-4boql)"; exit 1; fi

      # 2. same id in a USER-FACING artefact -> ALLOWED, but it must WARN.
      #    This case asserted REFUSED until aegis-krlog, which is what blocked
      #    every shantytown push. The graph rates pattern_bead-reference `warn`
      #    while every other pattern is `block`, so refusing here was the guard
      #    disagreeing with its own source of truth.
      #    BOTH HALVES ARE ASSERTED, and the second is the one that matters: an
      #    exit-0 alone cannot tell "warned" from "the rule stopped firing", and
      #    the surrounding comment already names that as the failure mode a scope
      #    change degrades into. So the WARNING TEXT is the observable.
      git checkout -q main 2>/dev/null || git checkout -q -B main origin/main
      git checkout -q -b chg
      echo '- Fixed the thing (zz-1a2b)' > CHANGELOG.md
      git add CHANGELOG.md; git commit -qm changelog
      out=$(echo "refs/heads/chg $(git rev-parse chg) refs/heads/chg $Z" | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" 2>&1); rc=$?
      if [ "$rc" -ne 0 ]; then
        echo "FAIL ticket in a CHANGELOG was REFUSED — warn tier regressed to block (aegis-krlog)"; exit 1
      elif printf '%s' "$out" | grep -q 'bead references'; then
        echo "ok   ticket in a CHANGELOG is allowed AND warned about"
      else
        echo "FAIL ticket in a CHANGELOG was allowed SILENTLY — the rule stopped firing"; exit 1
      fi

      # 3. a HOSTNAME in that same source file -> STILL REFUSED. The scope is the
      #    TICKET rule's alone; narrowing it must not narrow the rule that matters.
      git checkout -q main 2>/dev/null || git checkout -q -B main origin/main
      git checkout -q -b host; mkdir -p pkg
      echo '# talks to secret-host.invalid' > pkg/other.py
      git add pkg/other.py; git commit -qm host
      if echo "refs/heads/host $(git rev-parse host) refs/heads/host $Z" | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" >/dev/null 2>&1; then
        echo "FAIL a hostname in source was allowed — the scope leaked into the host rule"; exit 1
      else echo "ok   hostname in source is still refused"; fi

      # 4. WHOSE LEAK IS IT? Both outcomes, because the whole point of the
      #    attribution is to tell them apart — and a banner that always prints is
      #    worse than none, since it would excuse a real leak.
      git remote add internal "$r/forge.git" 2>/dev/null || true
      git init -q --bare "$r/forge.git"
      git checkout -q main 2>/dev/null || git checkout -q -B main origin/main
      git checkout -q -b shared; mkdir -p pkg
      echo '# talks to other-host.invalid' > pkg/shared.py
      git add pkg/shared.py; git commit -qm "somebody else's leak"
      shared_sha=$(git rev-parse --short shared)
      git push -q internal shared:main; git fetch -q internal 2>/dev/null
      # A LATER commit of the pusher's own, clean, riding the same range.
      echo 'nothing to see' > pkg/mine.py
      git add pkg/mine.py; git commit -qm "my clean change"
      out=$(echo "refs/heads/shared $(git rev-parse shared) refs/heads/shared $Z" \
            | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" 2>&1); rc=$?
      if [ "$rc" -eq 0 ]; then
        echo "FAIL a leak already on another remote was ALLOWED — the guard must still refuse"; exit 1
      elif printf '%s' "$out" | grep -q 'NOT YOUR CHANGE' \
           && printf '%s' "$out" | grep -q "$shared_sha" \
           && printf '%s' "$out" | grep -q "already on 'internal'"; then
        echo "ok   a pre-existing leak refuses AND is named as somebody else's"
      else
        echo "FAIL pre-existing leak was refused without saying so or without naming the commit"; exit 1
      fi

      # ...and the control: the pusher's OWN leak must NOT get that excuse.
      git checkout -q main 2>/dev/null || git checkout -q -B main origin/main
      git checkout -q -b mine; mkdir -p pkg
      echo '# talks to my-host.invalid' > pkg/mine2.py
      git add pkg/mine2.py; git commit -qm "my own leak"
      out=$(echo "refs/heads/mine $(git rev-parse mine) refs/heads/mine $Z" \
            | SCRUB_PATTERNS_FILE="$cf" bash "$SELF" origin "$r/pub.git" 2>&1); rc=$?
      if [ "$rc" -eq 0 ]; then
        echo "FAIL the pusher's own leak was allowed"; exit 1
      elif printf '%s' "$out" | grep -q 'NOT YOUR CHANGE'; then
        echo "FAIL the pusher's own leak was excused as pre-existing"; exit 1
      elif printf '%s' "$out" | grep -q 'yours: this push adds it'; then
        echo "ok   the pusher's own leak is named as theirs"
      else
        echo "FAIL own leak refused without attributing the commit"; exit 1
      fi
    ) || fail=1
    rm -rf "$r"
  fi

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
  # A guard's own TEST must be able to name the patterns it asserts on — same
  # reason this script and the ratchet tests above are excluded. Added when the
  # guard refused the very commit that fixed it (aegis-gsbs1): the test needs a
  # literal internal-forge URL for its "internal remote stays permissive" case.
  # Its leak FIXTURE is still assembled from runtime fragments, so the only
  # literal here is the one the test cannot avoid.
  ':(exclude,glob)**/test-push-guard-range-scope.sh'
)

REMOTE_URL="${2:-}"
PUSH_REMOTE="${1:-}"
# An UNUSABLE forge regex names no forge. grep -E exits 2 and the test below reads
# false, which already sends us down the public path — correct, but silent about why.
if [ -n "$INTERNAL_HOST_RE" ] && ! ere_usable "$INTERNAL_HOST_RE"; then
  echo "⚠ pre-push-scrub-guard: internal_host_re= in $CONF is not a valid POSIX ERE," >&2
  echo "  so no remote can be recognised as the internal forge. Scanning as public." >&2
  INTERNAL_HOST_RE=""
fi
# EMPTY MEANS NO REMOTE IS INTERNAL — not every remote (aegis-o4a3k). `grep -qE ""`
# matches any url, so the earlier form exited 0 RIGHT HERE on a public push whenever
# the key was missing: the whole guard off, nothing scanned, nothing printed. Same
# choice as the ticket scope above — an unset field enforces MORE and never less.
if [ -n "$INTERNAL_HOST_RE" ] && printf '%s' "$REMOTE_URL" | grep -qE "$INTERNAL_HOST_RE"; then
  exit 0   # internal forge — internal names belong there
fi
if [ -z "$INTERNAL_HOST_RE" ]; then
  echo "⚠ pre-push-scrub-guard: $CONF names no internal_host_re, so every push is" >&2
  echo "  scanned as PUBLIC. If this refuses a push to the internal forge, that is" >&2
  echo "  this warning: regenerate the config, do not --no-verify." >&2
fi
harden_config
warn_degraded

# ── IS THIS COMMIT ALREADY ON ANOTHER REMOTE? ───────────────────────────────
# The question the refusal could not answer, and it is the difference between
# "you leaked something" and "you are standing behind somebody else's leak".
#
# On a repo with an internal forge and a public mirror, the public remote can sit
# far behind: the push range then contains dozens of commits the pusher never
# wrote, and a refusal naming a line from one of them reads as the pusher's own
# fault. Measured: a leak from 2026-08-27 blocked the public mirror for two days
# while every agent who pushed was told their push "would add" it.
#
# Authorship cannot answer this — a fleet of agents commits under one identity —
# but REACHABILITY can, and it is the honest test anyway: a commit already on the
# internal forge is shared history that this push did not introduce and that the
# pusher cannot amend away.
already_elsewhere() {
  local c=$1 r tip
  for r in $(git remote 2>/dev/null); do
    [ "$r" = "$PUSH_REMOTE" ] && continue
    for tip in $(git for-each-ref --format='%(objectname)' "refs/remotes/$r/" 2>/dev/null); do
      if git merge-base --is-ancestor "$c" "$tip" 2>/dev/null; then
        printf '%s' "$r"; return 0
      fi
    done
  done
  return 1
}

# stdin: <local ref> <local sha> <remote ref> <remote sha>
violations=0
offenders=""       # one line per offending commit: "<sha> <subject>\t<remote|>"
offenders_mine=0   # how many of them this push actually introduces
while read -r _lref lsha _rref rsha; do
  [ "$lsha" = "0000000000000000000000000000000000000000" ] && continue
  if [ "$rsha" = "0000000000000000000000000000000000000000" ]; then
    # NEW REF (branch or tag). The added content is the commits reachable from
    # lsha that are NOT already on the remote — NOT all of lsha's ancestry
    # (aegis-5c9z: a release tag pointing at already-public history walked the
    # whole repo as "added" and refused itself, training the --no-verify habit
    # the guard must not normalise).
    #
    # The "already public" boundary is the remote's ACTUAL refs via ls-remote,
    # not local remote-tracking refs — the latter can be stale or absent and a
    # push guard must not depend on their freshness. Keep only remote shas we
    # hold locally as objects (rev-list --not needs local objects). Offline / a
    # brand-new remote yields an empty boundary => scan all of lsha's history,
    # the correct conservative behaviour when nothing is known-public yet.
    remote_have=$(git ls-remote "$REMOTE_URL" 2>/dev/null | awk '{print $1}' \
                  | while read -r h; do git cat-file -e "$h^{commit}" 2>/dev/null && echo "$h"; done)
    newcommits=$(git rev-list "$lsha" ${remote_have:+--not $remote_have} 2>/dev/null)
    if [ -z "$newcommits" ]; then
      continue   # ref adds no new commits (e.g. a tag on public history) — nothing to scan
    fi
    # Diff + messages of EXACTLY the new commits (git show per commit: its patch
    # vs its parent, so '+' lines are what that commit genuinely added). Raw
    # here; the single PATTERNS grep below is the one matcher for both branches.
    addedlines=""
    rawmsgs=""
    ticketlines=""
    for c in $newcommits; do
      addedlines+=$(git show --format= "$c" -- . "${GUARD_EXCLUDE[@]}" 2>/dev/null | grep -E '^\+' || true)$'\n'
      tf=$(ticket_files "$c")
      [ -n "$tf" ] && ticketlines+=$(printf '%s\n' "$tf" | tr '\n' '\0' \
        | xargs -0 git show --format= "$c" -- 2>/dev/null | grep -E '^\+' || true)$'\n'
      rawmsgs+=$(git log -1 --format=%B "$c" 2>/dev/null)$'\n'
      # ATTRIBUTION, per commit: which commit carries the leak, and is it one
      # this push introduces? Same matcher as the aggregate below, so the two can
      # never disagree about what counts as a hit.
      if git show --format= "$c" -- . "${GUARD_EXCLUDE[@]}" 2>/dev/null | grep -E '^\+' | grep -qE "$PATTERNS" \
         || git log -1 --format=%B "$c" 2>/dev/null | grep -qE "$PATTERNS"; then
        where=$(already_elsewhere "$c" || true)
        [ -z "$where" ] && offenders_mine=$((offenders_mine + 1))
        offenders+="$(git log -1 --format='%h %s' "$c" 2>/dev/null)"$'\t'"$where"$'\n'
      fi
    done
  else
    # Branch update. PER-COMMIT, not the net diff of the range (aegis-gsbs1).
    #
    # This was `git diff "$rsha" "$lsha"`, and the difference is the whole bug.
    # A push publishes every OBJECT in the range, not the range's net result — so
    # a range containing (leak, then scrub) has a clean net diff while still
    # publishing the leaking blob, reachable by sha forever. Self-reported by
    # arnold after exactly that: the guard refused his first push correctly, he
    # added a scrub COMMIT because the leaking commit had already reached the
    # internal forge and could no longer be amended, and the second push PASSED
    # while `braino@vati` went into public history at bb40959.
    #
    # The trap punished the right instinct: a scrub commit is what a careful
    # author reaches for FIRST when refused, and the guard's own remedy text said
    # "amend" — which is unavailable once the commit exists on another remote.
    #
    # The NEW REF path above has always scanned per-commit for this same reason;
    # this branch was simply the weaker half of one guard. `rsha..lsha` is exactly
    # the set of commits this push would add, so pre-existing content stays
    # excluded by construction and the quietness that keeps the guard installed is
    # preserved.
    newcommits=$(git rev-list "$rsha..$lsha" 2>/dev/null)
    if [ -z "$newcommits" ]; then
      continue   # nothing new (e.g. a forced no-op) — nothing to scan
    fi
    addedlines=""
    rawmsgs=""
    ticketlines=""
    for c in $newcommits; do
      addedlines+=$(git show --format= "$c" -- . "${GUARD_EXCLUDE[@]}" 2>/dev/null | grep -E '^\+' || true)$'\n'
      tf=$(ticket_files "$c")
      [ -n "$tf" ] && ticketlines+=$(printf '%s\n' "$tf" | tr '\n' '\0' \
        | xargs -0 git show --format= "$c" -- 2>/dev/null | grep -E '^\+' || true)$'\n'
      rawmsgs+=$(git log -1 --format=%B "$c" 2>/dev/null)$'\n'
      # ATTRIBUTION, per commit: which commit carries the leak, and is it one
      # this push introduces? Same matcher as the aggregate below, so the two can
      # never disagree about what counts as a hit.
      if git show --format= "$c" -- . "${GUARD_EXCLUDE[@]}" 2>/dev/null | grep -E '^\+' | grep -qE "$PATTERNS" \
         || git log -1 --format=%B "$c" 2>/dev/null | grep -qE "$PATTERNS"; then
        where=$(already_elsewhere "$c" || true)
        [ -z "$where" ] && offenders_mine=$((offenders_mine + 1))
        offenders+="$(git log -1 --format='%h %s' "$c" 2>/dev/null)"$'\t'"$where"$'\n'
      fi
    done
  fi
  # ADDED lines only (+ prefix), so pre-existing occurrences never trip it.
  added=$(printf '%s\n' "$addedlines" | grep -nE "$PATTERNS" || true)
  msgs=$(printf '%s\n' "$rawmsgs" | grep -nE "$PATTERNS" || true)
  # Ticket IDs are checked in FILE CONTENT only (the diff), never in commit
  # messages — a bead ref in a subject is the fleet's deliberate internal habit,
  # but the same ref in a CHANGELOG or a source comment reaching a public repo is
  # a leak a stranger cannot resolve (aegis-9cr1, the quipu #38 CHANGELOG).
  # ...and only in the USER-FACING files the graph scopes the rule to. A bead ref
  # in a source comment is the convention, not a leak (aegis-4boql).
  tickets=""
  [ -n "$TICKET_PATTERNS" ] && tickets=$(printf '%s\n' "$ticketlines" | grep -nE "$TICKET_PATTERNS" || true)

  # ── BEAD REFS ARE **WARN** TIER, NOT BLOCK (aegis-krlog) ────────────────────
  # This used to add `tickets` to the refusal condition, and that CONTRADICTED
  # the policy graph this guard is projected from. Measured in the graph, which
  # aegis-mqnl makes the source of truth:
  #
  #     pattern_internal-lan-host   block      pattern_internal-home-path  block
  #     pattern_internal-svc-host   block      pattern_guard-canary        block
  #     pattern_private-ipv4        block      pattern_internal-node-name  block
  #     pattern_bead-reference      WARN   <-- the only warn-tier pattern
  #
  # So this was not a policy that was merely wider than intended — it was a
  # MIS-PROJECTION of a tier the graph already states, and the repo's own ratchet
  # test had it right all along (BLOCK_TIER excludes "internal ticket id", with
  # the comment "they are warn-tier in the graph rule (a bead reference leaks no
  # topology)"). Two mechanisms, one graph, and only this one disagreed.
  #
  # WHY IT HAD TO CHANGE RATHER THAN BE OVERRIDDEN CASE BY CASE. Citing the bead
  # in a comment is this codebase's documented convention, so the rule fired on
  # ordinary correct work: 172 distinct bead ids are ALREADY in public
  # origin/main file content and 191 in its commit messages (re-measured on
  # origin/main). It was not preventing publication of bead ids; it was
  # preventing the 173rd, at the price of a `--no-verify` on essentially every
  # push. A guard that must be routinely overridden is a guard that will be
  # overridden on the day it is right — and this same guard also catches real
  # hostnames, which is the thing we cannot afford to have people reflex past.
  #
  # A bead ref is an opaque slug: it maps no host, no address and no path, which
  # is why the graph rates it warn. Everything that DOES map the estate still
  # refuses, immediately below, unchanged.
  if [ -n "$tickets" ]; then
    echo "⚠ note: this push adds internal bead references to a public remote." >&2
    echo "  remote: $REMOTE_URL" >&2
    printf '%s\n' "$tickets" | head -10 | sed 's/^/    /' >&2
    echo "  Not refused — bead refs are warn-tier in the policy graph (they leak no" >&2
    echo "  topology) and citing one is this repo's convention. Drop it if a stranger" >&2
    echo "  could not act on the line without it." >&2
  fi

  if [ -n "$added" ] || [ -n "$msgs" ]; then
    violations=1
    echo "✗ REFUSED: this push would add internal identifiers to a PUBLIC remote." >&2
    echo "  remote: $REMOTE_URL" >&2
    [ -n "$added" ]   && { echo "  internal names in the diff:" >&2; printf '%s\n' "$added" | head -10 | sed 's/^/    /' >&2; }
    [ -n "$msgs" ]    && { echo "  internal names in commit messages:" >&2; printf '%s\n' "$msgs" | head -10 | sed 's/^/    /' >&2; }
    if [ -n "$offenders" ]; then
      echo "  offending commit(s):" >&2
      printf '%s' "$offenders" | while IFS=$'\t' read -r desc where; do
        [ -z "$desc" ] && continue
        if [ -n "$where" ]; then
          echo "    $desc   [already on '$where' — NOT introduced by this push]" >&2
        else
          echo "    $desc   [yours: this push adds it]" >&2
        fi
      done
    fi
  fi
done

if [ "$violations" -ne 0 ] && [ -n "$offenders" ] && [ "$offenders_mine" -eq 0 ]; then
  # EVERY offending commit is already on another remote. Say so first and plainly:
  # the pusher has leaked nothing, cannot amend shared history, and the one move
  # that would "work" here — --no-verify — is the only move that actually
  # publishes the identifier.
  cat >&2 <<'EOM'

  PRE-EXISTING BLOCK — NOT YOUR CHANGE. Every commit above is already on another
  remote, so this push introduces none of them; it is blocked by history that
  predates it, and the mirror will stay blocked for everyone until that history
  is dealt with.

  Do NOT reach for --no-verify. It is the one action that would actually put the
  identifier into public history, and the refusal you are reading is not about
  anything you did.

  What to do: your work is already on the internal forge, so nothing is lost.
  The remedy is a rewrite of the named commit, which is a decision about shared
  history — file it and get authority rather than improvising it under a push.
EOM
  exit 1
fi

if [ "$violations" -ne 0 ]; then
  cat >&2 <<'EOM'

  Fix by REWRITING the offending commit — amend, or rebase -i and edit it —
  or push to the internal forge instead.

  A LATER SCRUB COMMIT WILL NOT FIX THIS, and this text used to imply it would.
  A push publishes every OBJECT in the range, not the range's net result, so
  (leak, then scrub) still puts the leaking blob in public history where it is
  reachable by sha forever. This guard now scans each commit in the range, so it
  will keep refusing until the leak is out of the COMMITS, not merely out of the
  tip. That is deliberate: it is the difference between the identifier being
  published and not (aegis-gsbs1).

  If the leaking commit has already reached ANOTHER remote and cannot be
  rewritten, you are past what this guard can prevent — that is a decision about
  public history, not a hook to argue with. File it and get authority.

  Pre-existing occurrences are deliberately NOT flagged — this refuses only what
  the push ADDS, so it stays quiet enough to stay installed.
  Override for a deliberate, reviewed publish:  git push --no-verify
EOM
  exit 1
fi
exit 0
