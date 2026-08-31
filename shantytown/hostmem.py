"""hostmem — a PHYSICAL brake beside the usage brake (aegis-do672).

THIS IS A THIRD AXIS, and the split is the same one session_budget.py argues for:

    governor.py     the FLEET by CLAUDE USAGE. "We are burning the budget; run
                    fewer agents." Its question is how much budget is left.
    session_budget  ONE SESSION by WHAT IT HAS DONE. "You alone have been going
                    six hours and deployed three times; stop and report."
    hostmem         the FLEET by the HOST IT RUNS ON. "There is not enough RAM
                    left for another agent and the build it will start."

WHY A THIRD ONE, MEASURED. 2026-08-30, the crew host: two global OOM kills in six
hours (CONSTRAINT_NONE), both rustc, anon-rss 11.9 GiB and 10.5 GiB. Crew agents
were not crashing — the kernel was reaping them, because it reaps whatever it hits
and agent sessions were what it hit. Every repo in this stack is Rust, so N agents
running cargo concurrently is N x ~12 GiB against a 61 GiB box.

The usage governor was ARMED and CORRECT throughout. It scaled the fleet to its
configured cap on a token-green reading, which is exactly its job, and it cannot
see this: eleven idle agents and four concurrent rustc processes read the same on
a usage gauge. Same shape as the incident that produced session_budget — a
correct governor on the wrong axis — and the same fix: not more caution inside the
existing brake, a brake on the axis that was unmeasured.

    A TOKEN BUDGET AND A MEMORY BUDGET ARE NOT SUBSTITUTES. Tokens are refilled
    by waiting. RAM is refilled by something finishing. A governor that can only
    see the first will admit an agent onto a box that cannot hold it and then
    report, accurately, that there was plenty of budget.

WHY /proc AND NOT PROMETHEUS. The usage governor reads a number over HTTP because
Claude usage exists nowhere else. Host memory is under our feet — `/proc/meminfo`
is local, synchronous, costs nothing, and is still readable when Prometheus is
down, which is precisely when a host is thrashing. A memory brake that needs the
monitoring stack to be healthy is a brake that fails during the incident.

The Prometheus side is not redundant with this and is not owned here: alerting
tells a HUMAN before the OOM (`agent-host-memory-alerts.yml`), and this refuses an
ADMISSION. Same thresholds and the same MemTotal gate on purpose — two enforcement
points that disagree about when a host is full would be worse than either alone.

IT NEVER KILLS AND IT NEVER DRAINS, which is a narrower promise than the usage
governor's and deliberately so. Draining the fleet does not free memory on any
useful timescale: agents are not what is resident, `rustc` is, and asking ten
sessions to stop does nothing about the four compilers already holding 40 GiB.
What this does is refuse to make it WORSE — no new agent, no new build, until
headroom returns. The correct response to a full box is to stop adding to it.

FAIL-SAFE DIRECTION: OPEN, LOUDLY. An unreadable `/proc/meminfo` resolves to
"admit, and alarm", never to "refuse everything". That matches governor.py's
standing invariant that no probe bug may stop the whole crew, and the asymmetry is
real — a broken probe that refuses every admission is an outage we caused, while a
broken probe that admits is the status quo ante plus an alarm. On Linux this path
is close to unreachable; it is written down because the ONE time it happens is not
the time to discover which way it falls.

DECLARING A FLOOR IS THE ENABLING ACT — no `enabled` key, matching governor.Policy
and session_budget.Limits. And note what that cost both of them: the usage governor
shipped correct and INERT for weeks because the live root had no config. Arming
this in the deployment is part of the work that adds it, not a later step for
somebody else to remember.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

GIB = 1024 ** 3

#: Where the numbers come from. Overridable so a test can hand this module a file
#: rather than monkeypatching a reader — the same reason governor has StubReader.
MEMINFO = Path("/proc/meminfo")

#: The measured peak of a single `rustc` on this stack, rounded up from 11.9 GiB.
#: Not a round number chosen for looking sensible: it is the larger of the two
#: anon-rss figures in the kernel log of the incident this module exists for.
BUILD_GIB = 12.0

#: Below this total, an absolute build-sized floor is meaningless and would refuse
#: every admission forever. The Prometheus rules use the SAME gate for the same
#: reason; if one changes, change both, or the alert and the brake will disagree
#: about whether a host is full.
MIN_TOTAL_GIB = 24.0


class HostMemError(ValueError):
    """A [hostmem] table that cannot be honoured. Raised only where one is PARSED."""


@dataclass(frozen=True)
class Reading:
    """What the host says about itself, in bytes, plus how we failed to ask.

    `error` is not an exception because a failed read is a NORMAL outcome that the
    caller must be able to render: the verdict it produces admits and alarms, and a
    caller that had to catch an exception to learn that would be free to forget.
    """
    available: int | None = None
    total: int | None = None
    swap_free: int | None = None
    swap_total: int | None = None
    error: str = ""

    @property
    def readable(self) -> bool:
        return self.error == "" and self.available is not None and self.total is not None

    @property
    def available_gib(self) -> float | None:
        return None if self.available is None else self.available / GIB

    @property
    def total_gib(self) -> float | None:
        return None if self.total is None else self.total / GIB

    @property
    def swap_free_pct(self) -> float | None:
        """Reported, never gated on.

        Swap exhaustion is real on this host and it is a CONSEQUENCE — seven days
        of history put swap-free at 18.7% on average with excursions to 0%, so a
        swap threshold would refuse most of the time and teach an operator to
        override it. It earns a place in the refusal TEXT, where it explains the
        pressure, and no place in the decision.
        """
        if not self.swap_total:
            return None
        return 100.0 * (self.swap_free or 0) / self.swap_total


def read(path: Path | None = None) -> Reading:
    """Read `/proc/meminfo`. Never raises — an unreadable host is a Reading too."""
    src = path or MEMINFO
    try:
        text = src.read_text()
    except OSError as e:
        return Reading(error=f"cannot read {src}: {e}")

    fields: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        # meminfo is in kB unless a unit says otherwise; the three keys we want are
        # always kB. Anything unitless is a count and not one of ours.
        fields[key.strip()] = value * 1024 if len(parts) > 1 else value

    if "MemAvailable" not in fields or "MemTotal" not in fields:
        # MemAvailable has existed since Linux 3.14. Its absence means this is not
        # a meminfo we understand, and GUESSING it from MemFree + Cached is exactly
        # the kind of derived number that reads as a measurement — MemFree alone is
        # routinely near zero on a healthy box with a warm page cache.
        return Reading(error=f"{src} has no MemAvailable/MemTotal — not a meminfo we can read")

    return Reading(
        available=fields["MemAvailable"],
        total=fields["MemTotal"],
        swap_free=fields.get("SwapFree"),
        swap_total=fields.get("SwapTotal"),
    )


@dataclass(frozen=True)
class Limits:
    """The `[hostmem]` table, resolved.

    `floor_gib` is the whole feature. `warn_gib` exists so a caller can say "this
    is the last one I will admit" before it has to say no, and defaults to two
    builds because that is the point at which concurrency stops fitting.
    """
    #: Refuse a new admission below this much MemAvailable. None = OFF.
    floor_gib: float | None = None
    #: Admit, but say the next one will be refused. None = derive 2x floor.
    warn_gib: float | None = None
    #: Hosts smaller than this are not gated at all.
    min_total_gib: float = MIN_TOTAL_GIB

    @property
    def active(self) -> bool:
        return self.floor_gib is not None

    @property
    def warn_at(self) -> float | None:
        if self.floor_gib is None:
            return None
        return self.warn_gib if self.warn_gib is not None else self.floor_gib * 2


@dataclass(frozen=True)
class Verdict:
    """Whether to admit, and the sentence that explains it.

    Carries the reading for the same reason governor.Verdict does: a refusal that
    cannot state the number behind it is indistinguishable from a bug.
    """
    reading: Reading
    limits: Limits
    refusal: str = ""     # non-empty -> DO NOT ADMIT, and this is why
    warning: str = ""     # non-empty -> admitted, but headroom is nearly gone
    alarm: str = ""       # non-empty -> say it LOUDLY: we could not measure

    @property
    def admits(self) -> bool:
        return self.refusal == ""


def _pressure(reading: Reading) -> str:
    swap = reading.swap_free_pct
    if swap is None:
        return ""
    return f", swap {swap:.0f}% free"


def check(limits: Limits, reading: Reading | None = None) -> Verdict:
    """Should another agent (and the build it may start) be admitted right now?"""
    got = read() if reading is None else reading

    if not limits.active:
        return Verdict(reading=got, limits=limits)

    if not got.readable:
        # Open, loudly. See the module docstring: a probe bug must never be able to
        # stop the whole crew, and this direction is the one we can recover from.
        return Verdict(
            reading=got, limits=limits,
            alarm=("hostmem SIGNAL LOST: " + (got.error or "no reading") +
                   " — admitting UNGOVERNED by memory. The physical brake is off "
                   "until this reads again."))

    total = got.total_gib or 0.0
    if total < limits.min_total_gib:
        return Verdict(reading=got, limits=limits)

    available = got.available_gib or 0.0
    floor = limits.floor_gib or 0.0
    warn = limits.warn_at or floor

    if available < floor:
        return Verdict(
            reading=got, limits=limits,
            refusal=(f"hostmem FLOOR: {available:.1f} GiB available of {total:.1f} GiB"
                     f"{_pressure(got)}, under the {floor:.1f} GiB floor — one Rust "
                     f"build is ~{BUILD_GIB:.0f} GiB, so admitting here is what takes "
                     f"the host into global OOM, and the kernel reaps whatever it hits. "
                     f"Nothing is killed or drained; this refuses to make it worse. "
                     f"Wait for a build to finish."))

    if available < warn:
        return Verdict(
            reading=got, limits=limits,
            warning=(f"hostmem LOW: {available:.1f} GiB available of {total:.1f} GiB"
                     f"{_pressure(got)} — under {warn:.1f} GiB, so a second concurrent "
                     f"Rust build will not fit. Admitted; the next one may not be."))

    return Verdict(reading=got, limits=limits)


_KEYS = frozenset({"floor_gib", "warn_gib", "min_total_gib"})


def _number(tbl: dict, key: str, default):
    if key not in tbl:
        return default
    value = tbl[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HostMemError(f"[hostmem] {key} must be a number, got {value!r}")
    if value <= 0:
        raise HostMemError(f"[hostmem] {key} must be positive, got {value!r}")
    return float(value)


def parse(tbl: dict) -> Limits:
    """Validate the `[hostmem]` table. Owns what a valid limit IS; config.py owns
    putting the file name on the complaint — the same split as governor/parse."""
    unknown = sorted(set(tbl) - _KEYS)
    if unknown:
        raise HostMemError(
            f"[hostmem] unknown key(s): {', '.join(unknown)}. Known: "
            f"{', '.join(sorted(_KEYS))}")

    limits = Limits(
        floor_gib=_number(tbl, "floor_gib", None),
        warn_gib=_number(tbl, "warn_gib", None),
        min_total_gib=_number(tbl, "min_total_gib", MIN_TOTAL_GIB),
    )
    if (limits.floor_gib is not None and limits.warn_gib is not None
            and limits.warn_gib < limits.floor_gib):
        # A warn below the floor can never be reached: the refusal fires first, so
        # the warning would be dead configuration that looks live.
        raise HostMemError(
            f"[hostmem] warn_gib ({limits.warn_gib}) is below floor_gib "
            f"({limits.floor_gib}) — the floor refuses first, so the warning could "
            f"never fire. warn_gib must be the LARGER number.")
    if limits.warn_gib is not None and limits.floor_gib is None:
        raise HostMemError(
            "[hostmem] warn_gib is set but floor_gib is not. Declaring a floor is "
            "what enables this brake; a warning with nothing to warn about admits "
            "everything while reading as though it governs.")
    return limits


def env_override() -> Limits | None:
    """`SHANTY_HOSTMEM_FLOOR_GIB` — a one-run override, for an operator who needs
    to get past the brake without editing deployment policy.

    Present because the alternative an operator reaches for is worse: commenting
    out the table, which disarms it for everyone and stays disarmed. `0` disables.
    """
    raw = os.environ.get("SHANTY_HOSTMEM_FLOOR_GIB")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return Limits()
    return Limits(floor_gib=value)
