"""untracked_health — out-of-band answer to "has the untracked-work nudge ever
run for this agent?" (aegis-06ue4).

THE GAP THIS CLOSES. The untracked-work PreToolUse hook (untracked.py, aegis-fv2zc)
fails OPEN and SILENT by design: `untracked.main` catches everything and exits 0
with no output, because a governance nudge must NEVER be the reason an agent cannot
edit a file (aegis-w1nd's lesson). The unavoidable cost of that choice is that
"wired and working" and "wired and crashing on every call" look IDENTICAL from
outside — the exact "running, wired, and inert" failure this repo names in
runtime.py, harness.py and roles.py. This module is the out-of-band probe that
tells the two apart. It does NOT make the hook fail loudly — that would trade the
w1nd lesson away; it reads an observable the hook ALREADY leaves behind.

THE OBSERVABLE, which already exists. On EVERY check — including the silent ones —
untracked.py writes its ledger `<root>/untracked/<agent>.json` (the poll cache: a
hooked agent stores `{checked_at, hooked:true}`). So the file's PRESENCE is proof
the hook ran at least once, and its mtime is when it last did.

    ledger present  -> the hook has RUN for that agent; mtime says when.
    ledger absent   -> it has NEVER run. Two sub-cases, and telling them apart is
                       the whole point:
                         (a) the agent simply has not made an acting tool call
                             since launch — benign, expected;
                         (b) the hook is dying before its first write — a real,
                             currently-invisible fault.

DISTINGUISHING (a) FROM (b) needs one more bit than "file absent": whether the
agent has actually been DOING anything. `no ledger AND launched a while ago AND the
pane has been active` is the honest "this hook may be dead" verdict; the same
without pane activity is just an idle agent and must not be alarmed about.

This module takes every input by injection (no tmux, no clock, no filesystem walk
of its own beyond the two json reads) so it is unit-testable the way roles.check
is, and so the CANNOT-TELL paths are exercisable. It reports; it never mutates.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Verdicts. Deliberately their own vocabulary rather than roles.py's
# OK/BROKEN/CANNOT_TELL — this is a diagnostic, not a hierarchy check, and folding
# it into doctor's exit code is done explicitly in worst_exit() below.
RAN = "ran"              # ledger present — the hook has executed
IDLE = "idle"            # no ledger, but the agent has not been acting: expected
TOO_SOON = "too-soon"    # no ledger, launched within the grace window: don't judge yet
SUSPECT = "suspect"      # no ledger, launched long ago, AND the pane is active: may be dead
NEVER_WIRED = "never-wired"   # non-admin whose consent file carries no untracked hook
CANNOT_TELL = "cannot-tell"   # consent unreadable / workspace absent — never a pass

# The grace window: below this since launch, an absent ledger says nothing — a
# freshly-launched agent that has only read files (Read/Grep are NOT in the hook's
# matcher, on purpose) legitimately has no ledger yet. 10 minutes matches the
# order of the bead's own measurement (tim relaunched 23:57, checked ~00:18).
GRACE_S = 600.0


@dataclass
class Row:
    agent: str
    verdict: str
    detail: str = ""


def _ledger_mtime(root: Path, agent: str, stat=None) -> float | None:
    """mtime of the agent's untracked ledger, or None if it does not exist.

    `stat` is injectable (path -> float | None) for tests; the default reads the
    real filesystem. An unreadable-but-present ledger returns None the same as an
    absent one — we cannot claim it ran on a file we could not stat, and this
    check must never over-claim."""
    p = Path(root) / "untracked" / f"{agent}.json"
    if stat is not None:
        return stat(p)
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def _consent_has_hook(workspace, read=None) -> bool | None:
    """Does this agent's consent file wire the untracked hook?

    Returns True/False, or None if the workspace or consent file could not be
    read at all — None is CANNOT-TELL, never folded into a pass. `read` is an
    injectable (path -> str) for tests.

    Mirrors provision._with_untracked_hook's own shape test: a PreToolUse entry
    whose command mentions `shantytown.untracked`. Kept structurally identical so
    the detector and the injector cannot drift on what "wired" means."""
    if not workspace:
        return None
    consent = Path(workspace) / ".claude" / "settings.local.json"
    try:
        text = read(consent) if read is not None else consent.read_text()
    except OSError:
        return None
    if not isinstance(text, str):
        # A reader that hands back None/non-str is saying "could not read" — that
        # is cannot-tell, never a False that would read as "hook not wired".
        return None
    try:
        cfg = json.loads(text)
    except ValueError:
        # A consent file that is present but not JSON is a real problem, but it is
        # not THIS check's problem to diagnose — say cannot-tell, do not guess.
        return None
    if not isinstance(cfg, dict):
        return None
    for entry in cfg.get("hooks", {}).get("PreToolUse", []):
        for h in entry.get("hooks", []):
            if "shantytown.untracked" in h.get("command", ""):
                return True
    return False


def _fmt_age(seconds: float) -> str:
    s = int(seconds)
    if s < 90:
        return f"{s}s ago"
    m = s // 60
    if m < 90:
        return f"{m}m ago"
    return f"{m // 60}h{m % 60:02d}m ago"


def check(agents, root, *, now, ledger_stat=None, launch_time=None,
          pane_active=None, consent_read=None, is_admin=None) -> list[Row]:
    """One Row per NON-ADMIN agent. All I/O is injected; nothing here mutates.

    agents        iterable of cards with .name, .role, .workspace.
    root          the shanty root (holds untracked/ and launched/).
    now           float, the current time (injected — never time.time() inline,
                  same reason launched.verdict reads live).
    ledger_stat   optional path->(mtime|None); default reads the real fs.
    launch_time   REQUIRED-in-practice reader name->(mtime|None): when the agent
                  was launched. The launched/ stamp's own file mtime is this
                  (launched.record writes it atomically at launch). None => we
                  cannot apply the grace window, so an absent ledger degrades to
                  cannot-tell rather than a false SUSPECT.
    pane_active   optional reader name->(bool|None): is the agent's pane doing
                  anything? None (or absent reader) => we will not assert SUSPECT,
                  because dead-hook and idle-agent are indistinguishable without
                  it — we say NEVER-RUN-indeterminate instead.
    consent_read  optional path->str for the consent file (tests).
    is_admin      optional name->bool; default reads card.role == 'administrator'.
    """
    def admin(card) -> bool:
        if is_admin is not None:
            return is_admin(card.name)
        return getattr(card, "role", None) == "administrator"

    rows: list[Row] = []
    for card in sorted(agents, key=lambda c: c.name):
        # ADMIN EXEMPT — structurally. provision._with_untracked_hook never wires
        # the hook for an administrator, so an admin with no ledger is correct,
        # not broken. Skip it entirely rather than emit a reassuring row that
        # invites someone to "fix" a non-problem.
        if admin(card):
            continue

        wired = _consent_has_hook(getattr(card, "workspace", None), read=consent_read)
        if wired is None:
            rows.append(Row(card.name, CANNOT_TELL,
                            "consent file unreadable or workspace missing — "
                            "cannot tell whether the hook is even wired"))
            continue
        if wired is False:
            # A non-admin whose consent carries no untracked hook. The governance
            # nudge NEVER reaches it — a distinct, worse finding than "wired but
            # never ran", and exactly the reachability failure aegis-06ue4's
            # parent (fv2zc) is about.
            rows.append(Row(card.name, NEVER_WIRED,
                            "non-admin, but the consent file wires NO untracked "
                            "hook — re-provision (the hook self-heals on launch)"))
            continue

        mtime = _ledger_mtime(root, card.name, stat=ledger_stat)
        if mtime is not None:
            rows.append(Row(card.name, RAN,
                            f"ledger last written {_fmt_age(now - mtime)}"))
            continue

        # No ledger. Wired, but never written. Decide benign-vs-suspect with the
        # extra bits, and REFUSE to over-assert when we lack them.
        lt = launch_time(card.name) if launch_time is not None else None
        if lt is None:
            rows.append(Row(card.name, CANNOT_TELL,
                            "wired but no ledger, and launch time is unknown — "
                            "cannot tell 'never acted' from 'hook dead'"))
            continue

        since_launch = now - lt
        if since_launch < GRACE_S:
            rows.append(Row(card.name, TOO_SOON,
                            f"wired, no ledger yet, but only launched "
                            f"{_fmt_age(since_launch)} — too soon to judge"))
            continue

        active = pane_active(card.name) if pane_active is not None else None
        if active is True:
            rows.append(Row(card.name, SUSPECT,
                            f"wired, pane ACTIVE, launched {_fmt_age(since_launch)}, "
                            f"yet NO ledger has ever been written — the hook may be "
                            f"dying before its first write. Shell it by hand to "
                            f"confirm: python -m shantytown.untracked --root {root}"))
        elif active is False:
            rows.append(Row(card.name, IDLE,
                            f"wired, launched {_fmt_age(since_launch)}, no ledger — "
                            f"but the pane is idle, so it simply has not made an "
                            f"acting tool call. Benign."))
        else:
            rows.append(Row(card.name, CANNOT_TELL,
                            f"wired, launched {_fmt_age(since_launch)}, no ledger — "
                            f"but pane activity is unreadable, so 'idle' and "
                            f"'hook dead' cannot be told apart here"))
    return rows


def worst_exit(rows) -> int:
    """Fold into doctor's exit meanings: 0 ok, 1 a real fault, 2 could-not-tell.

    CANNOT_TELL outranks a fault, same rule as roles.Report.verdict and for the
    same reason: an unread input might be hiding either, and reporting the lesser
    is the exit-2 bug again."""
    verdicts = {r.verdict for r in rows}
    if CANNOT_TELL in verdicts:
        return 2
    if verdicts & {SUSPECT, NEVER_WIRED}:
        return 1
    return 0


def render(rows) -> str:
    """Human-readable block for `st doctor`. Only the findings are loud; the
    healthy rows are one line each so the block is scannable."""
    if not rows:
        return ("untracked-hook liveness: no non-admin agents to check "
                "(the hook is admin-exempt by design).")
    label = {RAN: "ran", IDLE: "idle", TOO_SOON: "too-soon",
             SUSPECT: "SUSPECT", NEVER_WIRED: "NOT WIRED",
             CANNOT_TELL: "cannot tell"}
    L = ["untracked-hook liveness (has the fail-open nudge actually run?):"]
    for r in sorted(rows, key=lambda x: (x.verdict not in (SUSPECT, NEVER_WIRED,
                                                            CANNOT_TELL), x.agent)):
        mark = "***" if r.verdict in (SUSPECT, NEVER_WIRED) else \
               "???" if r.verdict == CANNOT_TELL else "  -"
        L.append(f"  {mark} {r.agent:<11} {label[r.verdict]:<11} {r.detail}")
    faults = sum(1 for r in rows if r.verdict in (SUSPECT, NEVER_WIRED))
    unknown = sum(1 for r in rows if r.verdict == CANNOT_TELL)
    L.append("")
    if faults:
        L.append(f"  {faults} agent(s) may be running an INERT governance hook — "
                 f"see above.")
    if unknown:
        L.append(f"  COULD NOT TELL for {unknown}: an input was unreadable. NOT a "
                 f"clean result.")
    if not faults and not unknown:
        L.append(f"  {len(rows)} non-admin agent(s), every wired hook accounted for.")
    return "\n".join(L)
