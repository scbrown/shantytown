"""bootstrap — bringing the town UP. `st start`.

`start` is the DECLARATIVE launch surface: it takes "the crew I want tonight" and
converges the fleet on it. It is IDEMPOTENT — running it twice is not an error and
does not touch a live agent, the one property a boot command has to have, because
the operator who most needs it is the one who does not know what is currently up.

WHY IT IS NOT `st tend` AND NOT `st new`, since both also launch agents:

  tend is a SUPERVISOR. It answers "did something die?", it is driven by a timer,
  and it refuses to touch an agent it has no launch stamp for (aegis-2j2r:
  another orchestrator's crew). A cold boot has no stamps and nothing has died —
  tend's gate structure is wrong for a first launch, and loosening it to fit
  would loosen it for the timer too.

  new is a PRIMITIVE, and its clobber guard is load-bearing: it REFUSES when the
  session already exists ("never replace a live agent"). That is right for one
  explicit launch and wrong for a boot, where "already up" is a SUCCESS. A boot
  built out of `st new` calls reports failure for the healthy half of a half-up
  fleet — the same defect as a supervisor that cannot tell "died" from "was
  stopped on purpose".

TOKEN CONSERVATION IS THE POINT, not a side effect. `lite` starts the
administrator alone — one agent's context, one agent's bill — and the admin
decides who else is needed and dispatches to them; `heavy` starts every card.
That is why a mode is a NAMED SET in config rather than a `--count`: "how much
fleet" is a decision an operator makes once and re-uses, and it wants a name.

WHAT IT WILL NOT DO:

  It will not start a RETIRED agent — not even under `heavy`, not even when the
  mode names it explicitly. Resolved and reported in config.resolve_crew; the
  reason is written there.

  It will not report a launch it could not verify as a launch. An agent whose
  runtime never appeared in the pane is UNVERIFIED, exits could-not-tell, and is
  never counted in the started tally. `st start` returning 0 has to mean the
  fleet is up, or nobody will ever be able to script a boot on it.

  It will not attach. Attaching is `st attach` (which launches on demand), and a
  boot that attached would block the shell that ran it — the systemd/cron case is
  exactly the one that cannot afford a foreground tmux client.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# One agent's outcome. Deliberately NOT tend.py's verdict vocabulary: these are
# different questions ("is it up now?" vs "did it die?") and sharing the strings
# would make two reports look like one ledger.
ALREADY_UP = "already-up"     # a live pane. A SUCCESS, not a refusal
STARTED = "started"           # it was down; it is up and verified
WOULD = "would-start"         # --dry-run stopped here
RETIRED = "retired"           # deliberately stopped: never started by a boot
UNVERIFIED = "unverified"     # launched, runtime not observed live -> cannot tell
REFUSED = "refused"           # could not launch, and says why
NO_PANE = "no-pane"           # no pane on the card: nothing to start into

# Anything that means "this agent is NOT known to be up when the pass ended".
_FAULTS = frozenset({UNVERIFIED, REFUSED, NO_PANE})


@dataclass(frozen=True)
class Started:
    agent: str
    verdict: str
    why: str = ""
    acted: bool = False       # did this pass actually launch something?


@dataclass
class BootReport:
    mode: str = ""
    findings: list[Started] = field(default_factory=list)
    # Carried from the roster so the report can say what config asked for and did
    # NOT get. A boot that quietly starts a subset is the failure this reports on.
    skipped_retired: list[str] = field(default_factory=list)
    started_at: float = 0.0
    dry_run: bool = False

    @property
    def acted(self) -> list[Started]:
        return [f for f in self.findings if f.acted]

    @property
    def faults(self) -> list[Started]:
        return [f for f in self.findings if f.verdict in _FAULTS]

    def up(self) -> list[str]:
        """Who is up now — launched by us or already running. The answer to the
        question the operator actually asked."""
        return [f.agent for f in self.findings
                if f.verdict in (STARTED, ALREADY_UP)]

    def healthy(self) -> bool:
        """Every selected agent is up (or was a deliberate retirement).

        A dry run is healthy by construction — WOULD is not a fault, it is the
        answer to a question. The exit code has to distinguish "the town is up"
        from "the town is partly up", because the operator's next action differs.
        """
        return not self.faults

    @property
    def would(self) -> list[Started]:
        return [f for f in self.findings if f.verdict == WOULD]

    def render(self) -> str:
        # IN PASS ORDER, deliberately NOT sorted by name. The order IS the launch
        # order (administrator, then leads, then workers — config.resolve_crew
        # explains why that matters), so an alphabetical render would hide the one
        # thing about a boot that a reader might want to check. tend.py's report
        # sorts by name because a supervision pass has no meaningful order; this
        # one does.
        lines = []
        for f in self.findings:
            mark = "!" if f.verdict in _FAULTS else ("+" if f.acted else " ")
            lines.append(f"  {mark} {f.agent:<12} {f.verdict:<12} {f.why}")
        # `mode` is EMPTY when the agents were named on the command line rather
        # than selected by a mode. Naming one anyway would credit a config that was
        # never consulted.
        by = f"selected by mode {self.mode!r}" if self.mode else "named"
        for name in sorted(self.skipped_retired):
            lines.append(f"    {name:<12} {RETIRED:<12} "
                         f"{by} but RETIRED — not started")
        # THE TALLY MUST COUNT WHAT THE LABEL SAYS. A dry run has acted on nothing
        # by construction, so counting `acted` under a "would start" header
        # reports 0 directly beneath a list of would-start rows — a summary that
        # contradicts the list above it is worse than no summary. The dry-run
        # count is the WOULD verdicts.
        head, n = (("would start", len(self.would)) if self.dry_run
                   else ("started", len(self.acted)))
        prefix = f"mode {self.mode!r} · " if self.mode else ""
        lines.append("")
        lines.append(f"  {prefix}{len(self.findings)} selected · "
                     f"{head} {n} · {len(self.up())} up · {len(self.faults)} fault(s)")
        return "\n".join(lines)


class Bootstrapper:
    """One `st start` pass. Every dependency injected, for the same reason
    tend.Tender's are: the branch that MATTERS is the one that launches, and a
    test that cannot reach it tests the report renderer instead.

    launch(card) -> (verdict, why): the real launcher lives in cli (it needs the
    runtime, the workspace and the provisioner), and this module stays free of
    all three so the ordering rules below are testable on their own.
    """

    def __init__(self, panes, *, launch, log=None):
        self._panes = panes
        self._launch = launch
        self._log = log or (lambda msg: None)

    def bring_up(self, cards, *, mode: str = "", dry_run: bool = False,
                 skipped_retired=None) -> BootReport:
        rep = BootReport(mode=mode, started_at=time.time(), dry_run=dry_run,
                         skipped_retired=list(skipped_retired or []))
        # IN THE ORDER GIVEN. config.resolve_crew hands cards back
        # administrator-first for a stated reason (a worker whose lead is down
        # escalates), so this must not helpfully re-sort them.
        for card in cards:
            rep.findings.append(self._one(card, dry_run))
        return rep

    def _one(self, card, dry_run: bool) -> Started:
        if not card.pane:
            # Same finding tend makes for the same card, and for the same reason:
            # a card with no pane names no session, so there is nothing to start
            # into. Not a crash, and not silence either.
            return Started(card.name, NO_PANE,
                           f"no pane on the card — nothing to start into. Add a "
                           f"`pane` to the card (e.g. \"shanty-{card.name}\"); "
                           f"neither `roles sync` nor `roles set` assigns one")

        # ALREADY UP IS THE FIRST QUESTION, and it is asked before anything can
        # decide to act. This is the difference between a boot and N `st new`
        # calls: a live agent is a success and is not touched. We deliberately do
        # NOT judge whether it is busy, wired or stale — those are `st crew` and
        # `st tend`'s verdicts, and a boot writing a second opinion about a
        # running agent is how two surfaces start disagreeing about who is
        # healthy.
        if self._panes.exists(card.pane):
            return Started(card.name, ALREADY_UP,
                           f"live in {card.pane!r} — left alone")

        if dry_run:
            return Started(card.name, WOULD,
                           f"down; would launch into {card.pane!r}")

        verdict, why = self._launch(card)
        if verdict == STARTED:
            self._log(f"STARTED {card.name}: {why or card.pane}")
            return Started(card.name, STARTED, why or f"launched into {card.pane!r}",
                           acted=True)
        # A launch that got as far as creating a session but could not be VERIFIED
        # still acted — the session exists, and a report that says otherwise sends
        # the operator to `st start` again, which will now find a live pane and
        # call it already-up. acted=True keeps the two runs consistent.
        self._log(f"{verdict.upper()} {card.name}: {why}")
        return Started(card.name, verdict, why, acted=(verdict == UNVERIFIED))
