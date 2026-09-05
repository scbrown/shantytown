#!/usr/bin/env bash
# install-scrub-guard — arm the pre-push scrub guard on every repo with a PUBLIC
# remote, and make that arming a re-runnable MECHANISM rather than a one-time
# manual state (aegis-oauk).
#
# WHY THIS EXISTS AND NOT JUST `cp`. The bead this closes is about decay: a scrub
# is a state and states rot. A hook hand-installed into .git/hooks is exactly such
# a state — it does not survive a reclone, and nobody re-does it by morning. This
# script is the anti-decay step for the INSTALL itself: run it any time (after a
# clone, on a new host, from cron) and every public-remote repo is armed again.
# It is idempotent and reports what it changed.
#
# WHAT COUNTS AS PUBLIC. A repo is public if ANY of its remotes is NOT the
# internal forge. The internal-forge pattern is read from the SAME generated
# config the guard uses (internal_host_re), so this installer and the guard agree
# on "internal" by construction — the aegis-mqnl one-rule-one-place requirement
# applied to the installer too.
#
# INSTALLATION BY REFERENCE. Every repo hook is an absolute symlink to one
# ownership-neutral, read-only source under ~/.local/share. A guard update is
# published there once and every armed repo sees the same inode immediately;
# there are no per-repo copies to decay independently. --check also compares the
# neutral source with this reviewed source, so a missed publish is loud.
#
# Usage:
#   install-scrub-guard.sh [--root DIR]... [--check] [--selftest]
#     (no args)   arm every public-remote checkout under $HOME, at ANY depth
#     --root DIR  repeatable; REPLACES the default root rather than adding to it
#     --check     report armed / unarmed / not-public, change nothing; exit 1 if
#                 any public repo is unarmed
#     --selftest  prove the discovery + arm logic on throwaway repos, no network
#
# WHY THE SWEEP IS HOST-WIDE AND UNBOUNDED IN DEPTH (aegis-yi7o93). This walked
# `<root>/*/.git` — ONE level — so a repo whose only checkout is a worktree at
# `~/gt/<repo>-wt/<agent>` was INVISIBLE. On 2026-09-04 that put six public
# checkouts (five under ~/gt/camayoc-wt, one under ~/gt/desire-path-wt) outside
# the sweep entirely, while it reported `repos seen: 51 ... unarmed: 0`. A commit
# carrying .svc hostnames reached camayoc's own pre-commit and would have reached
# a PUBLIC remote unguarded.
#
# `unarmed: 0` was TRUE and meaningless: it is computed over what the sweep
# FOUND, so a checkout it cannot see is not a gap — it is absent from the
# denominator. That is the same shape as a coverage check that passes while zero
# guards compile (aegis-cd7rw) and a collector reading green over blind hosts
# (aegis-dg9ckz). The fix is therefore NOT another named root — a root list you
# must remember to extend is the thing that failed twice (aegis-v7joru, then
# this) — it is to make the default root $HOME and the depth unbounded, so a new
# tree cannot appear outside the sweep's scope without leaving $HOME.
#
# COVERAGE IS PUBLISHED, NOT JUST LOGGED. Every run writes
# scrub_guard_*.prom (see emit_metrics) with ABSOLUTE counts, not a ratio: a
# ratio would have read 32/32 = 100% while five repos were invisible, i.e. it
# would have reproduced the bug it exists to catch. A drop in
# scrub_guard_repos_total is itself the alertable event, because a tree going
# invisible shows up as a smaller denominator and nothing else.
set -uo pipefail

CONF="${SCRUB_PATTERNS_FILE:-$HOME/.config/aegis/scrub-patterns.conf}"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$SELF/pre-push-scrub-guard.sh"
SOURCE_DIR="${SCRUB_GUARD_SOURCE_DIR:-$HOME/.local/share/shantytown-guard}"
LIVE_GUARD="$SOURCE_DIR/pre-push-scrub-guard.sh"

internal_re() {
  # The forge that may receive internal names, from the generated config. If the
  # config is absent we cannot tell internal from public, so we refuse to guess
  # (a wrong guess arms nothing or arms the internal forge pointlessly).
  local re=""
  [ -r "$CONF" ] && while IFS='=' read -r k v; do
    [ "$k" = "internal_host_re" ] && re="$v"
  done < "$CONF"
  printf '%s' "$re"
}

is_public() {
  # A repo is public if it has a remote whose URL does NOT match the internal
  # forge. No remotes -> not public (nothing to leak to). We read every remote URL
  # from config (fetch and push URLs both), because `remote get-url` needs a name
  # and a repo can have several remotes.
  local dir="$1" re="$2" url
  while read -r _k url; do
    [ -n "$url" ] || continue
    printf '%s' "$url" | grep -qE "$re" || return 0
  done < <(git -C "$dir" config --get-regexp '^remote\..*\.(push)?url$' 2>/dev/null)
  return 1
}

publish_source() {
  local staged="$SOURCE_DIR/.pre-push-scrub-guard.sh.new"
  mkdir -p "$SOURCE_DIR"
  chmod u+w "$LIVE_GUARD" 2>/dev/null || true
  install -m 0555 "$GUARD" "$staged" || return
  mv -f "$staged" "$LIVE_GUARD"
  chmod a-w "$LIVE_GUARD"
}

hook_path() {
  local common
  common="$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return
  printf '%s/hooks/pre-push\n' "$common"
}

# Trees never walked into: dependency and build caches hold no checkout we push.
# Pruning them is what makes a $HOME-wide sweep cost ~0.3s rather than minutes.
PRUNE_NAMES=(node_modules target .venv venv __pycache__ .cargo .rustup .terraform .tox .mypy_cache)

# Third-party upstreams this host never pushes to: a plugin manager's clones and
# pre-commit's hook cache. These are EXCLUDED BY NAME, COUNTED, and reported —
# never silently dropped. An uncounted skip is the exact defect this file is
# about, so the exclusion is two literal path prefixes rather than a wildcard
# over ~/.local/share (which would also swallow ~/.local/share/creel-src, a repo
# that does need arming).
EXCLUDE_PREFIXES=("$HOME/.local/share/nvim/lazy/" "$HOME/.cache/pre-commit/")

find_checkouts() {
  # Every .git under $1, at ANY depth, dir or file (a linked worktree's .git is a
  # FILE). Prints one working-tree path per line.
  local root="$1" prune=() n
  for n in "${PRUNE_NAMES[@]}"; do prune+=(-name "$n" -o); done
  unset 'prune[${#prune[@]}-1]'
  find "$root" -xdev \( "${prune[@]}" \) -prune -o -name .git -print -prune 2>/dev/null \
    | while IFS= read -r g; do dirname "$g"; done
}

is_excluded() {
  local dir="$1/" p
  for p in ${EXCLUDE_PREFIXES[@]+"${EXCLUDE_PREFIXES[@]}"}; do
    case "$dir" in "$p"*) return 0 ;; esac
  done
  return 1
}

repo_label() {
  # A stable, topology-free name for a metric label: the repo name from any
  # remote URL. Basenames of checkouts are agent names under a *-wt/ tree and
  # would collide across repos.
  local url
  url="$(git -C "$1" config --get-regexp '^remote\..*\.url$' 2>/dev/null | awk 'NR==1{print $2}')"
  [ -n "$url" ] || { printf 'unknown'; return; }
  url="${url%.git}"
  printf '%s' "${url##*[/:]}" | tr -c 'A-Za-z0-9_.-' '_'
}

emit_metrics() {
  # Prometheus textfile. ABSOLUTE counts by design — see the header.
  #
  # TWO PUBLISH PATHS, AND THE SECOND ONE IS NOT OPTIONAL (sattler, 2026-09-05).
  # The first version gated on `[ -w "$dir" ]` alone. On this host the textfile
  # directory is root-owned and NOT writable while the .prom file inside it is
  # braino-owned and IS — the established shape, because collectors render to
  # /tmp and a `sudo mv` in the crontab does the one privileged step. So a
  # direct run against the real path found the DIRECTORY unwritable, printed
  # "not writable — coverage not published", and skipped a file it could have
  # written. Only runs that went through the render+sudo path published, and a
  # reader of the log could not tell that from a metric that never publishes at
  # all — the note said the coverage was absent when the coverage was fine.
  # That is this bead's own defect wearing a different hat: an instrument
  # reporting its own inability as the estate's condition.
  #
  # Rename first (atomic, needs a writable DIR). Failing that, write in place
  # when the FILE is writable — one small write of a pre-built buffer, so a
  # concurrent scrape sees whole content in practice, and node_exporter's own
  # node_textfile_scrape_error reports it if it ever does not. Only when
  # NEITHER works is the note true.
  local out="${SCRUB_GUARD_TEXTFILE:-/var/lib/node_exporter/textfile/scrub_guard.prom}"
  local dir tmp
  dir="$(dirname "$out")"
  tmp="$(mktemp)" || return 0
  {
    echo "# HELP scrub_guard_repos_total Distinct public-remote repos the sweep discovered."
    echo "# TYPE scrub_guard_repos_total gauge"
    echo "scrub_guard_repos_total $repos"
    echo "# HELP scrub_guard_armed_total Discovered public repos whose pre-push hook is the current guard."
    echo "# TYPE scrub_guard_armed_total gauge"
    echo "scrub_guard_armed_total $armed"
    echo "# HELP scrub_guard_missing_total Discovered public repos with NO current guard. Alert on > 0."
    echo "# TYPE scrub_guard_missing_total gauge"
    echo "scrub_guard_missing_total $unarmed"
    echo "# HELP scrub_guard_excluded_total Checkouts skipped by a NAMED third-party exclusion, not by blindness."
    echo "# TYPE scrub_guard_excluded_total gauge"
    echo "scrub_guard_excluded_total $excluded"
    echo "# HELP scrub_guard_internal_skipped_total Repos with no remote outside the internal forge."
    echo "# TYPE scrub_guard_internal_skipped_total gauge"
    echo "scrub_guard_internal_skipped_total $skipped"
    echo "# HELP scrub_guard_checkouts_scanned_total Working trees walked, including several sharing one repo."
    echo "# TYPE scrub_guard_checkouts_scanned_total gauge"
    echo "scrub_guard_checkouts_scanned_total $checkouts"
    echo "# HELP scrub_guard_source_current 1 if the neutral guard source matches the reviewed source."
    echo "# TYPE scrub_guard_source_current gauge"
    echo "scrub_guard_source_current $source_current"
    echo "# HELP scrub_guard_last_run_timestamp_seconds Unix time of the last completed sweep."
    echo "# TYPE scrub_guard_last_run_timestamp_seconds gauge"
    echo "scrub_guard_last_run_timestamp_seconds $(date +%s)"
    # COUNT per repo name, not one series per repo. Several distinct checkouts
    # legitimately share a repo NAME (four independent hank clones, two
    # desire-path, two tapestry), and node_exporter DISCARDS THE WHOLE FILE on a
    # duplicate series — so the naive one-line-per-repo form would have silenced
    # every metric here at exactly the moment it had something to report.
    # Measured on the first real run: 18 unarmed collapsed to 12 names, 6 dupes.
    echo "# HELP scrub_guard_repo_unarmed Public repos of this name left without a current guard."
    echo "# TYPE scrub_guard_repo_unarmed gauge"
    printf '%s\n' ${unarmed_labels[@]+"${unarmed_labels[@]}"} \
      | sort | uniq -c \
      | while read -r n r; do [ -n "$r" ] && echo "scrub_guard_repo_unarmed{repo=\"$r\"} $n"; done
  } > "$tmp"
  chmod 0644 "$tmp" 2>/dev/null
  if [ -d "$dir" ] && [ -w "$dir" ] && mv -f "$tmp" "$out" 2>/dev/null; then
    echo "  coverage published: $out"
  elif [ -f "$out" ] && [ -w "$out" ] && cat "$tmp" > "$out" 2>/dev/null; then
    rm -f "$tmp"
    echo "  coverage published in place (directory read-only, file writable): $out"
  else
    rm -f "$tmp"
    echo "  note: $out not writable — coverage not published"
  fi
}

arm_one() {
  local dir="$1" hook
  hook="$(hook_path "$dir")" || return 1
  mkdir -p "$(dirname "$hook")"
  ln -sfn "$LIVE_GUARD" "$hook"
}

is_armed() {
  local dir="$1" hook target
  hook="$(hook_path "$dir")" || return 1
  [ -L "$hook" ] || return 1
  target="$(readlink -f "$hook")" || return 1
  [ "$target" = "$LIVE_GUARD" ] || return 1
  [ -x "$target" ] && [ ! -w "$target" ] || return 1
  cmp -s "$GUARD" "$target"
}

if [ "${1:-}" = "--selftest" ]; then
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  export SCRUB_GUARD_TEXTFILE="$tmp/metrics/scrub_guard.prom"
  mkdir -p "$tmp/metrics"
  # HERMETIC, OR IT IS NOT A TEST YOU CAN WIRE INTO CI (aegis-n5b1rd).
  # The in-process arms below pass their own `re`, but every SUB-INVOCATION
  # re-reads the config from $HOME and REFUSES when internal_host_re is absent —
  # arming nothing and reporting it politely. So this selftest passed on a
  # developer box purely because ~/.config/aegis/scrub-patterns.conf happened to
  # exist there, and failed three assertions under `env -i` with a clean HOME.
  # Measured before wiring it into CI, which is the only reason it was found:
  # the whole point of adding it to CI is that it runs somewhere I do not control.
  printf 'internal_host_re=forge\.invalid\n' > "$tmp/scrub-patterns.conf"
  export SCRUB_PATTERNS_FILE="$tmp/scrub-patterns.conf"
  SOURCE_DIR="$tmp/neutral-source"
  LIVE_GUARD="$SOURCE_DIR/pre-push-scrub-guard.sh"
  re='forge\.invalid'
  fail=0
  # A "public" repo: remote on a non-invalid host.
  git init -q "$tmp/pub"; git -C "$tmp/pub" remote add origin https://example.com/x.git
  # An "internal" repo: remote on the reserved internal forge only.
  git init -q "$tmp/int"; git -C "$tmp/int" remote add origin ssh://git@forge.invalid/x.git
  # A repo with no remote.
  git init -q "$tmp/bare"
  is_public "$tmp/pub" "$re" && echo "ok   public repo detected" || { echo "FAIL public missed"; fail=1; }
  is_public "$tmp/int" "$re" && { echo "FAIL internal treated as public"; fail=1; } || echo "ok   internal repo skipped"
  is_public "$tmp/bare" "$re" && { echo "FAIL no-remote treated as public"; fail=1; } || echo "ok   no-remote skipped"
  publish_source || { echo "FAIL neutral source publish"; exit 1; }
  arm_one "$tmp/pub" || { echo "FAIL public repo arm"; exit 1; }
  if is_armed "$tmp/pub"; then
    echo "ok   by-reference hook is current and locked"
  else
    echo "FAIL by-reference hook did not arm"; fail=1
  fi

  # Recreate the old defect: replace the link with a byte-identical copy. It
  # passes the guard's own behavioural selftest, but installation validation
  # must reject it because it can now rot independently.
  pub_hook="$(hook_path "$tmp/pub")"
  rm -f "$pub_hook"
  cp "$LIVE_GUARD" "$pub_hook"
  chmod 0755 "$pub_hook"
  if is_armed "$tmp/pub"; then
    echo "FAIL stale copied hook reported armed"; fail=1
  else
    echo "ok   stale copied hook is detected"
  fi

  # A changed reviewed source without republishing must also go red. This is
  # the cadence acceptance: source drift cannot hide behind healthy symlinks.
  GUARD="$tmp/reviewed-new"
  cp "$LIVE_GUARD" "$GUARD"
  chmod u+w "$GUARD"
  printf '\n# synthetic source update\n' >> "$GUARD"
  arm_one "$tmp/pub"
  if is_armed "$tmp/pub"; then
    echo "FAIL unpublished source change reported current"; fail=1
  else
    echo "ok   unpublished source change is detected"
  fi
  # ── MULTI-ROOT DISCOVERY (aegis-v7joru) ────────────────────────────────────
  # The arms above all call arm_one on a known path, so NONE of them exercise
  # the discovery loop — which is exactly why a single default root sat here
  # unnoticed while ~/workspace went unswept and the summary reported green.
  # A selftest that never asks "did you FIND it" cannot catch a blind sweep.
  #
  # Two throwaway roots, one public repo in each. The sweep must see BOTH, and
  # must report a nonzero repos-seen — a scan of nothing also prints 0 unarmed.
  mkdir -p "$tmp/rootA" "$tmp/rootB"
  git init -q "$tmp/rootA/alpha"; git -C "$tmp/rootA/alpha" remote add origin https://example.com/a.git
  git init -q "$tmp/rootB/beta";  git -C "$tmp/rootB/beta"  remote add origin https://example.com/b.git
  sweep=$("$SELF/install-scrub-guard.sh" --root "$tmp/rootA" --root "$tmp/rootB" --check 2>&1)
  if printf '%s' "$sweep" | grep -q 'alpha' && printf '%s' "$sweep" | grep -q 'beta'; then
    echo "ok   discovery reaches BOTH roots (repeatable --root)"
  else
    echo "FAIL a repo outside the first root was not discovered"; fail=1
  fi
  if printf '%s' "$sweep" | grep -qE 'public: [1-9]'; then
    echo "ok   sweep reports a nonzero public-repo count (a scan of nothing cannot pass)"
  else
    echo "FAIL sweep did not report a public-repo count"; fail=1
  fi
  # The DEFAULT must be $HOME, or the fix is a flag nobody passes rather than a
  # behaviour change — and $HOME is what subsumes ~/gt, ~/workspace and ~/src,
  # the three roots aegis-v7joru had to name one at a time. Checked against the
  # source text because DEFAULT_ROOTS is not assigned this early in the script.
  if grep -q '^DEFAULT_ROOTS=("\$HOME")$' "$SELF/install-scrub-guard.sh"; then
    echo "ok   default root is \$HOME (subsumes gt / workspace / src)"
  else
    echo "FAIL default root is not \$HOME"; fail=1
  fi

  # ── DEPTH: A REPO WHOSE ONLY CHECKOUT IS A WORKTREE (aegis-yi7o93) ─────────
  # THE regression. Every arm above, and every discovery assertion above, uses a
  # repo sitting one level under its root — which is why a one-level glob passed
  # this selftest for weeks while five public camayoc checkouts and one
  # desire-path checkout were invisible to the real sweep. The fixture is the
  # real shape: the repository lives OUTSIDE the swept root and the only thing
  # inside it is a linked worktree two levels down, whose .git is a FILE.
  mkdir -p "$tmp/outside" "$tmp/wtroot"
  git init -q "$tmp/outside/proj"
  git -C "$tmp/outside/proj" remote add origin https://example.com/proj.git
  git -C "$tmp/outside/proj" -c user.email=s@t -c user.name=s commit -q --allow-empty -m seed
  git -C "$tmp/outside/proj" worktree add -q -b wt/agentx "$tmp/wtroot/proj-wt/agentx" 2>/dev/null

  # NEGATIVE CONTROL: prove the fixture actually reproduces the bug. If a .git
  # existed one level under the root, the old glob would have found it and this
  # test would pass for the wrong reason.
  if [ -e "$tmp/wtroot/proj-wt/.git" ]; then
    echo "FAIL fixture does not reproduce the depth bug (a depth-1 .git exists)"; fail=1
  else
    echo "ok   fixture has no depth-1 .git (the old glob would find nothing here)"
  fi

  "$SELF/install-scrub-guard.sh" --root "$tmp/wtroot" >/dev/null 2>&1
  common="$(git -C "$tmp/wtroot/proj-wt/agentx" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  if [ -L "$common/hooks/pre-push" ]; then
    echo "ok   worktree-only repo two levels deep is DISCOVERED and armed"
  else
    echo "FAIL worktree-only repo two levels deep was not armed"; fail=1
  fi

  # ── THE COVERAGE METRIC ───────────────────────────────────────────────────
  # It must exist, and it must carry ABSOLUTE counts. A ratio would have read
  # 32/32 = 100% while those six repos were invisible.
  m="$SCRUB_GUARD_TEXTFILE"
  if [ -s "$m" ] && grep -q '^scrub_guard_missing_total ' "$m" \
     && grep -q '^scrub_guard_repos_total ' "$m" \
     && grep -q '^scrub_guard_excluded_total ' "$m"; then
    echo "ok   coverage metric published with absolute counts"
  else
    echo "FAIL coverage metric missing or incomplete"; fail=1
  fi
  if grep -qE '^scrub_guard_repos_total [1-9]' "$m"; then
    echo "ok   the worktree-only repo is IN the denominator"
  else
    echo "FAIL the worktree-only repo is not counted in scrub_guard_repos_total"; fail=1
  fi

  # NO DUPLICATE SERIES, EVER. node_exporter discards an entire textfile that
  # contains one, so a duplicate would blank every metric above at precisely the
  # moment the sweep had something to report — the metric would fail in the same
  # reassuring direction as the sweep it exists to watch. Measured on the first
  # real host run: 18 unarmed repos collapsed to 12 distinct names, 6 duplicates.
  dupes=$(grep -v '^#' "$m" | awk '{print $1}' | sort | uniq -d)
  if [ -z "$dupes" ]; then
    echo "ok   metric file has no duplicate series"
  else
    echo "FAIL duplicate series would make node_exporter drop the file: $dupes"; fail=1
  fi

  # A DENOMINATOR OF ZERO MUST BE PUBLISHED AS ZERO, NOT OMITTED. This is the
  # whole point of the metric: an absent series is invisible in Prometheus,
  # whereas scrub_guard_repos_total dropping to 0 — or from 84 to 30 — is the
  # alertable event that a tree has gone out of the sweep's sight. A metric that
  # only appears when there is something to report cannot report disappearance.
  mkdir -p "$tmp/emptyroot"
  "$SELF/install-scrub-guard.sh" --root "$tmp/emptyroot" >/dev/null 2>&1
  if grep -q '^scrub_guard_repos_total 0$' "$m"; then
    echo "ok   an empty sweep publishes repos_total 0 (disappearance is visible)"
  else
    echo "FAIL an empty sweep did not publish a zero denominator"; fail=1
  fi

  # ── PUBLISHING INTO A ROOT-OWNED DIRECTORY (sattler, 2026-09-05) ──────────
  # The real textfile directory is root-owned and NOT writable; the .prom file
  # inside it is braino-owned and IS. The first gate tested the directory only,
  # so a direct run printed "not writable — coverage not published" over a file
  # it could have written, and every metric assertion above still PASSED —
  # because they all run against a tmp dir the selftest itself created writable.
  # A test that only ever exercises the easy permission shape cannot see this.
  mkdir -p "$tmp/rodir"
  : > "$tmp/rodir/scrub_guard.prom"
  chmod 0644 "$tmp/rodir/scrub_guard.prom"
  chmod a-w "$tmp/rodir"
  out=$(SCRUB_GUARD_TEXTFILE="$tmp/rodir/scrub_guard.prom" \
        "$SELF/install-scrub-guard.sh" --root "$tmp/emptyroot" 2>&1)
  chmod u+w "$tmp/rodir"
  if grep -q '^scrub_guard_repos_total ' "$tmp/rodir/scrub_guard.prom" 2>/dev/null; then
    echo "ok   publishes into an existing writable file inside a read-only dir"
  else
    echo "FAIL a writable target file inside a read-only dir was not published"; fail=1
  fi
  if printf '%s' "$out" | grep -q 'coverage not published'; then
    echo "FAIL claimed 'coverage not published' about a file it could write"; fail=1
  else
    echo "ok   does not claim 'not published' about a file it can write"
  fi
  # And the note must still be TRUE when neither path works.
  mkdir -p "$tmp/nodir"; chmod a-w "$tmp/nodir"
  out=$(SCRUB_GUARD_TEXTFILE="$tmp/nodir/absent.prom" \
        "$SELF/install-scrub-guard.sh" --root "$tmp/emptyroot" 2>&1)
  chmod u+w "$tmp/nodir"
  if printf '%s' "$out" | grep -q 'coverage not published'; then
    echo "ok   the note is still emitted when nothing is writable"
  else
    echo "FAIL a genuinely unwritable target was reported as published"; fail=1
  fi

  # ── NAMED EXCLUSIONS ARE COUNTED, NEVER SILENTLY DROPPED ──────────────────
  # An uncounted skip is the defect this whole file is about, one layer down.
  EXCLUDE_PREFIXES=("$tmp/vendorland/")
  if is_excluded "$tmp/vendorland/plugin" && ! is_excluded "$tmp/outside/proj"; then
    echo "ok   exclusion matches its prefix and nothing else"
  else
    echo "FAIL exclusion prefix matching is wrong"; fail=1
  fi
  if printf '%s' "$sweep" | grep -q 'third-party excluded:'; then
    echo "ok   the summary states the excluded count and the prefixes"
  else
    echo "FAIL the summary does not report exclusions"; fail=1
  fi

  [ "$fail" -eq 0 ] && echo "selftest PASSED" || echo "selftest FAILED"
  exit "$fail"
fi

# DEFAULT ROOTS ARE PLURAL (aegis-v7joru). This was a single ROOT defaulting to
# the dir holding sibling repos — i.e. ~/gt — and NOTHING ELSE WAS EVER SWEPT.
# Public-remote repos also live under ~/workspace, and on 2026-08-31 all 13 of
# them were unarmed while the default sweep reported a clean 16 armed / 0 unarmed:
#
#     --check                      public armed: 16   unarmed:  0   EXIT 0
#     --check --root ~/workspace   public armed:  0   unarmed: 13   EXIT 1
#
# 16-for-16 was a true number and a green report that was structurally blind to a
# whole tree. ~/workspace/gastown is a PUBLIC scbrown repo and had no pre-push
# hook at all; a push went to it that day with no scrub guard in the path.
#
# A default you must remember to override is the thing that failed here, so the
# fix is to sweep both by default rather than to document the flag harder.
# --root is now REPEATABLE and REPLACES the defaults when given, so an explicit
# invocation still means exactly what it says.
# The roots are NAMED, not derived from where this file happens to sit. The old
# default was `dirname(dirname($SELF))`, so the sweep followed the SCRIPT: run
# from the canonical ~/gt/shantytown it swept ~/gt, but run from a worktree at
# ~/gt/shantytown-wt/<agent> it swept ~/gt/shantytown-wt — a tree of worktrees —
# and reported a confident green about the wrong thing. Measured 2026-08-31 while
# fixing the ~/workspace hole; same defect class, found only because this version
# prints the roots it swept.
#
# Script-relative survives only as a LAST RESORT, used when neither named root
# exists on this host. Keeping it in the normal list would reinstate the very
# location-dependence described above: run from a worktree it appends a tree of
# worktrees, so the sweep's scope would again depend on which copy you invoked.
# The list is de-duplicated by real path.
# ONE ROOT, AND IT IS $HOME (aegis-yi7o93). The previous list — ~/gt, ~/workspace,
# ~/src — was itself the second attempt at naming the right places, and it was
# wrong again: it named the right TREES and still missed six public checkouts,
# because the miss was in DEPTH, not in breadth. Naming a fourth root would fix
# the six we happened to stumble into and nothing else. $HOME plus an unbounded
# depth is the only scope a new checkout cannot appear outside of without leaving
# the account. Measured: the full walk costs ~0.3s and finds ~300 checkouts.
DEFAULT_ROOTS=("$HOME")
FALLBACK_ROOT="$(dirname "$(dirname "$SELF")")"
ROOTS=()
CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root)  ROOTS+=("$2"); shift 2 ;;
    --check) CHECK=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done
[ ${#ROOTS[@]} -eq 0 ] && ROOTS=("${DEFAULT_ROOTS[@]}")

# A root that does not exist is skipped quietly (not every host has ~/workspace),
# but a root that exists and holds NO repo at all is reported: silence there is
# indistinguishable from the blindness this change exists to remove.
kept=()
for r in "${ROOTS[@]}"; do
  [ -d "$r" ] || continue
  r=$(cd "$r" 2>/dev/null && pwd -P) || continue
  dup=0
  for k in ${kept[@]+"${kept[@]}"}; do [ "$k" = "$r" ] && dup=1 && break; done
  [ "$dup" -eq 0 ] && kept+=("$r")
done
if [ ${#kept[@]} -eq 0 ] && [ -d "$FALLBACK_ROOT" ]; then
  echo "⚠ neither default root exists; falling back to $FALLBACK_ROOT" >&2
  kept=("$(cd "$FALLBACK_ROOT" && pwd -P)")
fi
if [ ${#kept[@]} -eq 0 ]; then
  echo "no sweepable root exists (looked in: ${ROOTS[*]})" >&2
  exit 1
fi
ROOTS=("${kept[@]}")

RE="$(internal_re)"
if [ -z "$RE" ]; then
  echo "⚠ no internal_host_re in $CONF — cannot tell internal from public." >&2
  echo "  Regenerate it (policy/emit-scrub-config.py) and re-run. Nothing armed." >&2
  exit 1
fi

source_current=0
if [ -x "$LIVE_GUARD" ] && [ ! -w "$LIVE_GUARD" ] && cmp -s "$GUARD" "$LIVE_GUARD"; then
  source_current=1
elif [ "$CHECK" -eq 1 ]; then
  echo "  SOURCE STALE  $LIVE_GUARD" >&2
else
  publish_source || { echo "failed to publish locked guard source" >&2; exit 1; }
  source_current=1
fi

unarmed=0 armed=0 skipped=0 repos=0 checkouts=0 excluded=0
unarmed_labels=()
declare -A SEEN_COMMON=()

for ROOT in "${ROOTS[@]}"; do
  printf "  [%s]\n" "$ROOT"
  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    checkouts=$((checkouts+1))
    if is_excluded "$dir"; then
      excluded=$((excluded+1)); continue
    fi
    # DEDUPE BY REPOSITORY, NOT BY CHECKOUT. Linked worktrees share one hooks
    # directory, so arming any one of them arms all — but the repo must still be
    # FOUND, and a repo whose only checkout is a worktree is found only here.
    common="$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || continue
    [ -n "${SEEN_COMMON[$common]:-}" ] && continue
    SEEN_COMMON[$common]=1

    if ! is_public "$dir" "$RE"; then
      skipped=$((skipped+1)); continue
    fi
    repos=$((repos+1))
    name="${dir#$HOME/}"
    if [ "$source_current" -eq 1 ] && is_armed "$dir"; then
      printf "  armed    %s\n" "$name"; armed=$((armed+1)); continue
    fi
    if [ "$CHECK" -eq 1 ]; then
      printf "  UNARMED  %s\n" "$name"
      unarmed=$((unarmed+1)); unarmed_labels+=("$(repo_label "$dir")")
    elif arm_one "$dir"; then
      printf "  armed    %s (installed)\n" "$name"; armed=$((armed+1))
    else
      printf "  UNARMED  %s (arm FAILED)\n" "$name"
      unarmed=$((unarmed+1)); unarmed_labels+=("$(repo_label "$dir")")
    fi
  done < <(find_checkouts "$ROOT")
done

echo
# Name the roots AND the depth in the summary. A count with no scope is what
# made "16 armed, 0 unarmed" read as "the host is covered" (aegis-v7joru) and
# "51 seen, 0 unarmed" read the same way while six public repos were invisible
# (aegis-yi7o93). Both numbers were true; neither could state what it did not
# look at. Print the denominator's provenance next to the denominator.
echo "  roots swept: ${ROOTS[*]} (any depth)"
echo "  checkouts scanned: $checkouts   third-party excluded: $excluded (${EXCLUDE_PREFIXES[*]})"
echo "  distinct repos: $((repos + skipped))   public: $repos   internal/none: $skipped"
echo "  public armed: $armed   unarmed: $unarmed"
emit_metrics
if [ "$CHECK" -eq 1 ] && { [ "$unarmed" -gt 0 ] || [ "$source_current" -eq 0 ]; }; then
  echo "  run without --check to arm them." >&2
  exit 1
fi
[ "$unarmed" -eq 0 ] || exit 1
exit 0
