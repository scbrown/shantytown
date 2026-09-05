"""Converting one agent from one harness to another, and the cross-lane
recommendation that tells an operator when to.

Stiwi, 2026-09-04: *"it should be easy for you to convert crew to claude and
governor should recommend"*. Both halves of that sentence live here, and they
are deliberately PURE — a planner and a recommender that decide from values,
with the CLI supplying the values and performing the effects.

Why pure. The evening that produced the directive ran three Claude leads while
nine codex workers sat governor-held and the base lane read "fill toward cap
+3/+4" for hours. Neither half of that was hard to compute; both were
unexpressible, because the conversion was a hand edit of a card and each
governor lane only ever spoke for itself. A rule nobody can state is a rule
nobody can test, so the statements come first and the effects hang off them.

## The conversion is the card

`harness.name_for` reads the card at LAUNCH time, so editing the card *is* the
conversion — there is no separate migration. What an operator actually chooses
is *when the agent restarts*, which is why [`Plan.takes_effect`] is part of the
answer rather than a footnote. A verb that silently restarted a working agent
would be the more dangerous default, so the restart is opt-in (`--now`).

## What it refuses, and what it does not

It refuses a role the deployment PINS to a harness (`harness.required_by_role`
— leads are pinned to claude here), and it refuses moving an agent ONTO a lane
whose governor is holding, because that is the move the governor is currently
saying not to make. Neither refusal is a safety boundary: `--force` exists for
both, and the point is that the exception becomes deliberate and logged rather
than accidental.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Refusal:
    """Why the conversion did not happen. Carries the reason, always.

    A refusal that cannot state its cause is indistinguishable from a bug, and
    the operator's next action differs completely between "leads are pinned"
    and "the lane you are moving onto is held".
    """

    reason: str
    #: Stable code, so a caller can branch without parsing prose.
    code: str
    #: What would lift it, when anything would.
    remedy: str = ""


@dataclass(frozen=True)
class Plan:
    """The conversion to perform. `None` fields mean "do not write this".

    `from_harness == to_harness` is not represented here — that is
    [`NoChange`], because an idempotent re-run must be distinguishable from a
    real edit by the caller that logs it.
    """

    agent: str
    from_harness: str
    to_harness: str
    model: str | None = None
    #: Restart now, or let the card take effect at the next launch.
    restart_now: bool = False
    #: Notes worth printing — a forced refusal that was overridden, say.
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def takes_effect(self) -> str:
        """When this becomes true of the RUNNING agent.

        The distinction the operator needs: the card is authoritative at launch,
        so an un-restarted agent keeps running the old harness with a card that
        says otherwise. Saying "converted" of that state would be a lie of
        exactly the kind this repo keeps cataloguing.
        """
        return ("on relaunch, starting now" if self.restart_now
                else "at this agent's next relaunch — it is still running "
                     f"{self.from_harness} until then")


@dataclass(frozen=True)
class NoChange:
    """The card already says this. Reported, and nothing is written."""

    agent: str
    harness: str


def plan_switch(*, agent: str, current: str, target: str, role: str,
                required_by_role: dict, known: tuple, model: str | None = None,
                current_model: str | None = None,
                lane_held: bool = False, restart_now: bool = False,
                force: bool = False):
    """Decide one conversion. Returns [`Plan`], [`NoChange`] or [`Refusal`].

    Ordering is the design, and it is the same discipline the landing guard
    uses: settle what is REPRESENTABLE before what is POLICY. An unknown
    harness is refused before the role pin is consulted, because "codx" is a
    typo and telling its author about lead pinning sends them somewhere useless.
    """
    if target not in known:
        return Refusal(
            code="unknown-harness",
            reason=f"{target!r} is not a harness this deployment hosts",
            remedy=f"one of: {', '.join(sorted(known))}")

    # Idempotence BEFORE the policy gates, deliberately. Re-running a conversion
    # that already happened must not be able to fail on a pin or a hold that
    # would forbid making the change today — the change is already made, and a
    # verb that refuses to confirm the state it produced is not idempotent.
    if current == target and (model is None or model == current_model):
        return NoChange(agent=agent, harness=target)

    pinned = required_by_role.get(role)
    if pinned is not None and pinned != target and not force:
        return Refusal(
            code="role-pinned",
            reason=(f"role {role!r} is pinned to the {pinned!r} harness by this "
                    f"deployment, so {agent} cannot move to {target!r}"),
            remedy="change harness.required_by_role, or --force to override once")

    warnings: list[str] = []
    if pinned is not None and pinned != target and force:
        warnings.append(
            f"FORCED past the {role!r} -> {pinned!r} pin; this agent now "
            "violates the deployment's own admission rule and `st new` will "
            "refuse to launch it until the pin or the card changes")

    if lane_held and not force:
        return Refusal(
            code="target-lane-held",
            reason=(f"the {target!r} governor is holding, so moving {agent} onto "
                    "that lane is the move it is currently advising against"),
            remedy="wait for the lane to relax, or --force to override once")
    if lane_held and force:
        warnings.append(
            f"FORCED onto a HELD {target!r} lane — the governor is advising "
            "against exactly this move")

    return Plan(agent=agent, from_harness=current, to_harness=target,
                model=model, restart_now=restart_now,
                warnings=tuple(warnings))


# --- the governor's half -------------------------------------------------------

@dataclass(frozen=True)
class Lane:
    """One governor lane, reduced to what a cross-lane comparison needs."""

    name: str
    #: Creel's recommended agent delta for this lane; None = no recommendation.
    delta: int | None = None
    #: The lane is restricting — held by hysteresis or over its bound.
    held: bool = False
    #: Agents currently RUNNING on this lane (harness-detected, not card-declared).
    live: int = 0
    #: Names on this lane that could move, most-worth-moving first.
    candidates: tuple[str, ...] = field(default_factory=tuple)
    #: The HARNESS this lane governs, when its name differs from the lane's.
    #:
    #: They differ for the compatibility lane: the governor is called `base` and
    #: the program is `claude`. An operator converts an agent to a HARNESS, so a
    #: sentence reading "convert 3 codex workers to base" names something that
    #: is not a valid argument to `st harness` — which is how a recommendation
    #: becomes unactionable while looking complete.
    harness: str | None = None

    @property
    def program(self) -> str:
        """What to actually pass to `st harness`."""
        return self.harness or self.name

    @property
    def wants_fewer(self) -> bool:
        """Is this lane asking to shrink, or refusing to grow?"""
        return self.held or (self.delta is not None and self.delta < 0)

    @property
    def room(self) -> int:
        """How many more agents this lane says it can take. Never negative."""
        if self.held or self.delta is None:
            return 0
        return max(0, self.delta)


def cross_harness_advice(lanes) -> str | None:
    """One sentence recommending a CONVERSION, or `None` when none applies.

    This is the line the operator wanted and never got. Each lane's setpoint
    advisory speaks only for its own budget, so a fleet with one lane held and
    another with room reads as two unrelated facts — "codex -1" and "base +3" —
    when it is one actionable move.

    The three cases, and each is a test:

    * one lane wants fewer AND another has room -> recommend converting, capped
      by the receiving lane's room and by how many agents are actually there to
      move;
    * every lane wants fewer -> `None`. There is nowhere to put anyone, and a
      "convert" line here would be advice to shuffle a fleet that should shrink;
    * every lane has room -> `None`. The move is to LAUNCH, which the existing
      per-lane advisories already say; recommending a conversion instead would
      trade a free agent for a moved one.

    Returns None rather than an empty string so a caller cannot print a blank
    line where a recommendation would go — absence is not a quiet answer here.
    """
    lanes = [lane for lane in lanes if lane is not None]
    if len(lanes) < 2:
        return None

    donors = [lane for lane in lanes if lane.wants_fewer and lane.live > 0]
    receivers = [lane for lane in lanes if lane.room > 0 and not lane.wants_fewer]
    if not donors or not receivers:
        return None

    # The most-constrained donor and the roomiest receiver: with only two lanes
    # this is just "the held one" and "the open one", and it stays correct if a
    # third lane ever appears rather than silently picking by dict order.
    donor = min(donors, key=lambda x: (x.delta if x.delta is not None else 0))
    receiver = max(receivers, key=lambda x: x.room)

    movable = min(receiver.room, donor.live)
    if movable <= 0:
        return None

    def _delta(lane) -> str:
        if lane.held and lane.delta is None:
            return f"{lane.name} held"
        if lane.delta is None:
            return f"{lane.name} ?"
        return f"{lane.name} {lane.delta:+d}"

    line = (f"{_delta(donor)}, {_delta(receiver)} -> convert up to {movable} "
            f"{donor.program} worker{'s' if movable != 1 else ''} to "
            f"{receiver.program}")
    named = donor.candidates[:movable]
    if named:
        line += f" (candidates: {', '.join(named)})"
    else:
        # SAY that the names are missing rather than printing a bare
        # recommendation: an operator who cannot see who to move has to go and
        # find them, and a line that omits the list silently looks like a line
        # whose list was empty for a reason.
        line += " (candidates: none with ready work — check `st crew`)"
    return line
