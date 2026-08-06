"""codex — the Codex CLI half of the harness split, and nothing else.

Everything in this file is a claim about SOMEBODY ELSE'S PROGRAM, which is the
kind of claim this repo has been burned by twice (the unmeasured READY_MARKERS,
the `python` vs `python3` hook interpreter). harness.py's own docstring set the
bar: *"a guess about somebody else's CLI flags is exactly the kind of thing that
looks shipped and has never run"*. So every fact below was READ OUT OF CODEX'S
SOURCE — openai/codex, branch `main`, fetched 2026-08-06 — and the file it came
from is named beside it. There is no codex binary on the host that wrote this,
so source is the strongest evidence available; where source could not answer, we
DO NOT EMIT (see MATCHERS_NOT_EMITTED below) rather than guess.

The four facts this file rests on:

1.  THE CONFIG FILE.  codex has no `--settings <file>` flag. It reads
    `config.toml` out of its home directory, and `$CODEX_HOME` names that
    directory (codex-rs/config/src/lib.rs: `CONFIG_TOML_FILE = "config.toml"`;
    the `--profile` help text in codex-rs/utils/cli/src/shared_options.rs reads
    "Layer $CODEX_HOME/<name>.config.toml on top of the base user config"). So
    the settings artifact for a codex card is a config.toml, and the launch
    "points at it" by exporting CODEX_HOME to its DIRECTORY.

2.  THE HOOKS SCHEMA.  codex takes hooks in `[hooks]`, keyed by event name, and
    the shape is matcher-group-identical to Claude Code's
    (codex-rs/config/src/hook_config.rs):

        HookEventsToml { "PreToolUse" | "SessionStart" | "Stop" | ... :
                         Vec<MatcherGroup> }
        MatcherGroup   { matcher: Option<String>, hooks: Vec<HookHandlerConfig> }
        HookHandlerConfig::Command { type = "command", command: String, ... }

    That is why role_stop_hooks/session_start_hooks live in runtime.py and are
    shared: WHICH events a role needs and WHAT commands they run is
    SHANTYTOWN's decision (it is our own `python -m shantytown.stop_event …`),
    and only the FILE those events are written into is the harness's.

3.  THE STOP CONTRACT IS BLOCKING.  This is the one that changes what codex is
    allowed to be, so it is quoted precisely. codex-rs/hooks/src/events/stop.rs
    parses a Stop hook's stdout for `{"decision":"block","reason":"…"}` (or exit
    code 2 with the reason on stderr) and turns the reason into a
    `HookPromptFragment` continuation prompt — i.e. it REACHES THE MODEL, which
    is exactly the capability docs/adapters.md defines. A `decision:block` with
    a blank reason is rejected as invalid rather than silently swallowed. So
    CodexHarness declares blocking_stop=True and a codex lead/administrator is
    hostable. See harness.CodexHarness.hooks for what that costs if the codex on
    the host is older than the hooks system.

4.  HOOK TRUST.  codex will not run hooks it has no persisted trust record for;
    `--dangerously-bypass-hook-trust` is documented in shared_options.rs as "Run
    enabled hooks without requiring persisted hook trust for this invocation."
    The trust record is a `trusted_hash` in `[hooks.state]`
    (hook_config.rs::HookStateToml) whose hash function we cannot compute from
    outside. Emitting a Stop hook the program then declines to run is the
    declared-but-inert failure this repo exists to not ship — so the launch
    carries the flag. See harness.CodexHarness.launch for why that flag is a
    default here while every other `dangerously-` flag is opt-in per card.

WHAT A CODEX AGENT DOES NOT GET, so nobody has to discover it from a missing
metric: the PostToolUse capture, the untracked-work nudge and the stale guard are
delivered through provision.py's `<ws>/.claude/settings.local.json` rather than
through the emitted settings, deliberately (that file is re-applied on every
launch, so it self-heals — aegis-rcyd). It is Claude Code's file. A codex agent
therefore carries its stop routing and its session-start directive and none of
those three. Named in docs/adapters.md as a gap with a shape, not a TODO.

The TOML writer at the bottom is ours because the package has ZERO dependencies
and the stdlib reads TOML (tomllib) but does not write it.
"""
from __future__ import annotations
import re
import tomllib
from typing import Any

# The env var that names codex's home directory — the directory config.toml
# lives in. This is the whole "how does the launch point at the settings file"
# answer for codex, and it is an ENV EXPORT rather than a flag because codex
# offers no flag (fact 1 above).
HOME_VAR = "CODEX_HOME"
CONFIG_FILE = "config.toml"

# WHAT WE DELIBERATELY DO NOT EMIT, and why it is a constant rather than a
# comment: the hank edit guard, the deployment Bash guard and the MCP guard are
# all MATCHER-SCOPED PreToolUse hooks, and a matcher is a claim about the host
# program's TOOL NAMES. Claude Code's are "Bash" and "mcp__.*". codex's tool
# vocabulary is its own and we have not measured it, and this repo has already
# paid for one matcher that looked specific and fired zero times (aegis-ac5x/
# 18e0, the `Bash(pattern)` matcher that was permissions syntax). A guard
# emitted with the wrong program's vocabulary is not a weaker guard, it is a
# guard that never runs while reading as wired — so codex settings carry the
# events that need NO matcher (SessionStart, Stop) and name this gap out loud
# at emit time instead.
MATCHERS_NOT_EMITTED = ("PreToolUse",)

# HOW MUCH OF THE PROJECT DOC THE AGENT ACTUALLY GETS, and why this is not a
# tuning knob but a correctness fix (aegis-ovffp).
#
# codex reads AGENTS.md from its cwd and TRUNCATES IT AT `project_doc_max_bytes`,
# whose default is 32 KiB. MEASURED on codex-cli 0.146.1 with `codex debug
# prompt-input`, which renders the model-visible prompt at zero model cost:
#
#     crew rulebook on disk                          65367 B
#     delivered to the model at the default          32690 B   (50% LOST)
#     a canary on the last line                      ABSENT
#     any truncation notice, in the prompt or stderr NONE
#
# It cuts MID-WORD ("`~/gt/aegis` IS NOT TH") and says nothing to anybody. That
# is the failure mode this repo keeps paying for — a thing that reads as wired
# and is half-inert — and it is worse than the stale AGENTS.md that prompted the
# bead, because a stale file is at least a file somebody can diff. Nothing on
# either side of this reports a byte count.
#
# The number is deliberately far above the rulebook rather than snug to it: the
# rulebook GROWS (it grew the day this was measured), and a snug limit means the
# newest rule — the one added because something just went wrong — is the first
# one silently dropped. selfcheck asserts headroom rather than trusting it.
PROJECT_DOC_MAX_BYTES = 262144

# ⚠️ NOT EMITTED, and this one is a MEASUREMENT rather than a caution:
# `project_doc_fallback_filenames = ["CLAUDE.md"]` is real and it works — with
# AGENTS.md absent, codex reads CLAUDE.md in full. But it is consulted ONLY when
# AGENTS.md is ABSENT: measured with a stale AGENTS.md beside a configured
# fallback, the STALE FILE WINS and CLAUDE.md is never read. So it cannot be the
# mechanism that keeps codex and claude on one rulebook — `bd init` regenerates
# AGENTS.md (`--skip-agents` exists precisely because it writes one), and the day
# it does, the fallback goes quiet and the agent is back on the generic file with
# nothing announcing the change. The rulebook is delivered by AGENTS.md being a
# SYMLINK to CLAUDE.md instead: one inode, so there is no second file to drift.
# Emitting a fallback here as "belt and braces" would be decoration — it can
# never fire while the symlink is intact, and it cannot save us when it is not.
FALLBACK_NOT_EMITTED = ("project_doc_fallback_filenames",)


def settings_for_role(role: str, root=None) -> dict:
    """The codex config.toml a role needs, as a dict (render() turns it to TOML).

    The hook COMMANDS are the same ones Claude Code's settings carry — they are
    shantytown's own CLI, not Claude Code's — so they come from the shared
    builders in runtime.py. What differs is the container: no
    `enableAllProjectMcpServers` (a Claude Code key), no `env` block (codex has
    its own `shell_environment_policy`; the launch line already exports
    BOBBIN_ROLE and friends into the process the hooks are re-exec'd from), and
    no matcher-scoped guards (MATCHERS_NOT_EMITTED).

    Call-time import, same one-directional reason as harness.py's: runtime
    imports harness imports codex, never back.

    `project_doc_max_bytes` is a ROOT scalar, not a hook, and it is here rather
    than left to the operator because the thing it fixes is invisible: without it
    the agent is handed half its rulebook and neither the agent nor the operator
    is told (PROJECT_DOC_MAX_BYTES). It merges the same way everything else does —
    merge_one_level takes emitted-wins per key — so an operator who has set their
    own is overridden on `roles set`, which is the same contract the hook events
    already carry and the reason `roles set` exists.
    """
    from .runtime import role_stop_hooks, session_start_hooks
    return {
        # Root scalars first: TOML puts bare keys before any [table] header, and
        # dumps() emits scalars ahead of children for exactly that reason.
        "project_doc_max_bytes": PROJECT_DOC_MAX_BYTES,
        "hooks": {
            "SessionStart": session_start_hooks(),
            "Stop": [{"hooks": role_stop_hooks(role, root=root)}],
        },
    }


def render(settings: dict, existing: str = "") -> str:
    """The bytes to write, merging what the operator already had.

    SAME MERGE RULE AS CLAUDE'S (harness.merge_one_level): st replaces the hook
    EVENTS it emits, and every other key — including `[hooks.state]`, which is
    codex's trust ledger and emphatically not ours to rewrite — survives.

    THE COST, stated because it is real and TOML-specific: this is a
    parse-and-re-emit, so an operator's COMMENTS and key ORDER in config.toml do
    not survive a re-emission. JSON had nothing to lose here; TOML does. The
    alternative — a surgical text edit of somebody's TOML — is a parser we would
    have to be right about every time, and being wrong there corrupts the file
    that decides how the agent runs. Losing a comment is recoverable; losing the
    file is not.
    """
    from .harness import merge_one_level
    try:
        current = tomllib.loads(existing) if existing.strip() else {}
    except (ValueError, TypeError):
        # Unparseable existing content is not a merge base. Treat it as empty
        # and let the emission stand: a config we cannot read is a config we
        # cannot honour, and the alternative is refusing to emit hooks at all.
        current = {}
    return dumps(merge_one_level(current, settings))


def stop_directions(text: str) -> set[str] | None:
    """Which stop directions this config.toml carries — {"send"}, {"send",
    "drain"}, … — or None for CANNOT TELL.

    None is not an empty set, the same contract as the Claude Code reader
    (runtime.stop_directions_in): a file we could not parse is not a file with
    no hooks. Unparseable TOML, or TOML not shaped like settings we emitted,
    both return None.
    """
    try:
        data = tomllib.loads(text)
    except (ValueError, TypeError):
        return None
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or "Stop" not in hooks:
        return None
    found: set[str] = set()
    try:
        for group in hooks["Stop"]:
            for hook in group["hooks"]:
                cmd = hook.get("command", "")
                # Identical rules to the Claude Code reader, deliberately: the
                # unified stop_policy entry PROVIDES drain, and a mode must be
                # its own TOKEN rather than a substring of some path.
                if "shantytown.stop_policy" in cmd:
                    found.add("drain")
                    continue
                if "shantytown.stop_event" not in cmd:
                    continue
                for mode in ("send", "drain"):
                    if mode in cmd.split():
                        found.add(mode)
    except (KeyError, TypeError, AttributeError):
        return None
    return found


# --- the TOML writer -----------------------------------------------------------
# Small on purpose. It emits exactly the shapes settings_for_role produces —
# tables, arrays of tables, and scalar values — and RAISES on anything else
# rather than inventing a syntax for it. A writer that silently mis-encodes a
# value it did not expect would produce a config.toml codex rejects at launch,
# which is a failure a long way from its cause.

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\f": "\\f",
            "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def dumps(data: dict) -> str:
    """A dict -> TOML. Deterministic (insertion order preserved), newline-ended."""
    lines: list[str] = []
    _table(data, [], lines)
    return "\n".join(lines).strip("\n") + "\n"


def _table(table: dict, path: list[str], lines: list[str], *, header: bool = True) -> None:
    """One table's body. `header=False` for a table whose header the caller has
    already written — the ROOT (which has none) and each `[[array]]` element
    (whose header IS the `[[…]]` line, and which would otherwise be declared
    twice: `[[hooks.Stop.hooks]]` followed by `[hooks.Stop.hooks]`, which tomllib
    rejects outright — caught by the round-trip test, which is why the writer's
    output is parsed back rather than eyeballed)."""
    scalars = [(k, v) for k, v in table.items() if not _is_table(v) and not _is_tables(v)]
    children = [(k, v) for k, v in table.items() if _is_table(v)]
    arrays = [(k, v) for k, v in table.items() if _is_tables(v)]
    # A header is emitted for any non-root table that has scalars of its own, or
    # that has nothing at all (an empty table is still a fact the file states).
    if header and path and (scalars or not (children or arrays)):
        lines.append(f"[{'.'.join(path)}]")
    for key, value in scalars:
        lines.append(f"{_key(key)} = {_value(value)}")
    if scalars:
        lines.append("")
    for key, value in children:
        _table(value, path + [_key(key)], lines)
    for key, value in arrays:
        for item in value:
            lines.append(f"[[{'.'.join(path + [_key(key)])}]]")
            _table(item, path + [_key(key)], lines, header=False)
            if lines[-1] != "":
                lines.append("")


def _is_table(v) -> bool:
    return isinstance(v, dict)


def _is_tables(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(i, dict) for i in v)


def _key(key: str) -> str:
    return key if _BARE_KEY.match(key) else f'"{_escape(key)}"'


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return f'"{_escape(v)}"'
    if isinstance(v, list):
        return "[" + ", ".join(_value(i) for i in v) + "]"
    raise TypeError(
        f"codex.dumps cannot encode {type(v).__name__} — refusing to guess a TOML "
        f"syntax for it rather than write a config codex will reject at launch."
    )


def _escape(s: str) -> str:
    out = []
    for ch in s:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return "".join(out)
