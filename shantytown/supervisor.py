"""supervisor — the systemd --user units `st tend --install` writes, and the
health signal it leaves behind.

Split from tend.py on purpose: tend.py decides WHAT to do about agents and can
be tested with no systemd at all. This module is the only place that knows a
timer exists.

TWO SUPERVISORS ARE WORSE THAN NONE. If something else is already supervising
this crew, --install REFUSES and says which unit — it never clobbers a unit it
did not write, and it never disables one either. Deciding that the other
supervisor should stop is not an install-time decision; it is a human's, and a
tool that quietly wins that argument is a tool that will one day quietly lose it.
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import time
from pathlib import Path

# Every unit we write carries this line. It is the ONLY thing that makes
# "ours" answerable: a name match is not ownership (tmux.py states the same rule
# for the kill path), so a unit at our path WITHOUT this marker is somebody
# else's and we refuse rather than overwrite it.
MARKER = "# written-by: st tend --install"

SERVICE = "st-tend.service"
TIMER = "st-tend.timer"

# --- the governor's ON ramp (aegis-9mehy) ------------------------------------
# One transient one-shot per window, armed from the published reset timestamp,
# whose whole job is to run an ORDINARY tend pass at the moment the budget is
# due to refill. It carries no policy and makes no decision: it makes us LOOK,
# and the reading that pass takes decides whether anything changes.
WAKE_PREFIX = "st-governor-wake"
# Set on the woken pass so the log can say WHICH PATH re-engaged the crew. Without
# it a timer that quietly stopped working is invisible — every re-engagement
# would still happen, just five minutes late, and nothing would ever say so.
WAKE_ENV = "SHANTY_GOVERNOR_WAKE"
# Re-arming is free but not silent (each one writes journal lines), and the
# published reset timestamp jitters by a second or two between probes. Only
# re-arm when the target moved by more than this.
WAKE_TOLERANCE_S = 30

# Supervisors known to tend the same crew. Presence is a REFUSAL, not a warning:
# two things respawning the same agents fight, and the fight looks like flapping
# nobody can attribute.
#
# ARMED, not merely RUNNING — and that distinction is the whole of aegis-np4x1.
# gastown-crew.service is a boot-time oneshot: between boots it is INACTIVE, so an
# is-active check sees nothing, --install proceeds, and the NEXT boot starts both
# fleets. That is what happened. The unit survived the rename to shanty-* still
# enabled, the host almost never reboots, and so the collision stayed invisible
# for as long as the host stayed up — six duplicate agents, three of them sharing a
# workspace with their own twin, and `st crew` reporting zero faults throughout.
#
# So a unit that is ENABLED counts. It will supervise this crew again; that it is
# not doing so this second is timing, not safety. Listing the name alone would not
# have caught this — the predicate had to change with it.
FOREIGN_UNITS = ("gastown-crew-watchdog.timer", "gastown-crew.service")


def unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def resolve_st_bin(which=None, argv0=None) -> str | None:
    """The ABSOLUTE path of the `st` we are running, or None if we cannot tell.

    THE NAME THAT WORKS IN A SHELL IS NOT THE NAME THAT WORKS IN A UNIT.
    systemd --user does not resolve a bare `st` against ~/.local/bin, so a unit
    written with ExecStart=st can never exec. It 203/EXEC's on every single
    fire — while `systemctl --user list-timers` keeps printing a healthy
    LAST/PASSED for the timer, because the TIMER is fine; it is the service
    that dies. A oneshot that fails at exec is indistinguishable from one that
    ran unless you read the service journal, which nobody does while things
    look fine.

    Measured on a live crew host: 687 consecutive failures across two days,
    with no supervision pass, no governor pass and no idle-fleet alert in any
    of them. The fleet was unsupervised and the only symptom was silence.

    So we resolve here, and install() REFUSES on a non-absolute path rather
    than writing a unit that is born broken.
    """
    which = which or shutil.which
    # PATH first: that is the binary the human typed, so the unit supervises
    # the same st they are running. which() already vouches for existence and
    # the executable bit, so we do not second-guess it — only that it is
    # absolute, which is the property systemd actually needs.
    cand = which("st")
    if cand:
        p = os.path.realpath(cand)
        if os.path.isabs(p):
            return p

    # Fallback for `python -m shantytown`, where there may be no console
    # script on PATH at all. Nothing vouches for argv0, so check it ourselves.
    a0 = argv0 if argv0 is not None else (sys.argv[0] if sys.argv else None)
    if a0:
        p = os.path.realpath(a0)
        if os.path.isabs(p) and os.path.exists(p):
            return p
    return None


def _service(st_bin: str, root: Path) -> str:
    return f"""{MARKER}
[Unit]
Description=shantytown crew supervision (st tend)
Documentation=man:st(1)

[Service]
Type=oneshot
# One pass. Non-zero means it found a FAULT (a resurrected retiree, a deaf
# agent, a refusal) — not that it failed to run, which is what systemd's own
# failure state means. Both are visible in `st tend --status`.
ExecStart={st_bin} --root {root} tend
"""


def _timer(interval: str) -> str:
    return f"""{MARKER}
[Unit]
Description=run shantytown crew supervision every {interval}

[Timer]
OnBootSec=2min
OnUnitActiveSec={interval}
# Persistent so a pass missed while the host was off runs once on boot rather
# than being silently skipped — a supervisor that quietly does nothing after a
# reboot is the failure this whole command exists to make visible.
Persistent=true

[Install]
WantedBy=timers.target
"""


def ours(path: Path) -> bool:
    """Did WE write this unit? Content, not filename."""
    try:
        return MARKER in path.read_text()
    except OSError:
        return False


def foreign_supervisor(is_active, is_enabled=lambda unit: False):
    """Is something else already tending this crew? Returns (unit, why) or None.

    TWO predicates, because a supervisor has two ways to exist and only one of
    them is visible right now: it is running, or it is armed to run at the next
    boot. The second is the one that bit us (see FOREIGN_UNITS), and it is the
    one a live check cannot see — which is exactly why it has to be asked for
    separately rather than inferred.

    `why` is returned rather than reconstructed by the caller because the two
    cases want different things from a human: an ACTIVE competitor is fighting
    you now, an ENABLED one is a trap set for the next reboot, and the second
    reads as a false alarm unless the message says otherwise.
    """
    for unit in FOREIGN_UNITS:
        if is_active(unit):
            return unit, "active now"
        if is_enabled(unit):
            return unit, "enabled — it starts at the next boot"
    return None


def install(st_bin: str, root: Path, *, interval: str = "5min", run=None,
            is_active=lambda unit: False, is_enabled=lambda unit: False,
            dry_run: bool = False) -> tuple[bool, str]:
    """Write + enable the units. IDEMPOTENT, and refuses rather than clobbers.

    Returns (changed, message). changed=False with a message is the second run:
    a no-op, which is the whole requirement — re-running must not stack timers.
    """
    # A unit is only as good as its ExecStart. Refuse at INSTALL time, where a
    # human is reading output, rather than at every fire, where nobody is.
    if not st_bin or not os.path.isabs(st_bin):
        return False, (
            f"REFUSED: {st_bin!r} is not an absolute path. systemd --user does "
            f"not search ~/.local/bin, so this unit would fail 203/EXEC on "
            f"every fire while the timer still reported itself healthy — days "
            f"of silent non-supervision is what that cost last time. Pass the "
            f"absolute path to the st you are running."
        )

    other = foreign_supervisor(is_active, is_enabled)
    if other:
        unit, why = other
        return False, (
            f"REFUSED: {unit} is {why}, and it supervises the same crew. Two "
            f"supervisors respawning the same agents is worse than none — they "
            f"fight, and the fight looks like flapping nobody can attribute. "
            f"Decide which one owns this crew (that is a human's call, not "
            f"this command's), then re-run. To retire the other: "
            f"`systemctl --user disable --now {unit}` (add `mask` to make it "
            f"survive a redeploy that would re-enable it)."
        )

    d = unit_dir()
    svc, tmr = d / SERVICE, d / TIMER
    for p in (svc, tmr):
        if p.exists() and not ours(p):
            return False, (
                f"REFUSED: {p} exists and was NOT written by st tend (no "
                f"{MARKER!r}). Refusing to overwrite a unit somebody else "
                f"installed."
            )

    want = {svc: _service(st_bin, Path(root).resolve()), tmr: _timer(interval)}
    if all(p.exists() and p.read_text() == text for p, text in want.items()):
        return False, "already installed and current — nothing to do."

    if dry_run:
        return False, f"would write {svc} and {tmr}, then enable {TIMER}."

    d.mkdir(parents=True, exist_ok=True)
    for p, text in want.items():
        p.write_text(text)
    if run is not None:
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", "--now", TIMER])
    return True, f"installed {SERVICE} + {TIMER} (every {interval})."


def uninstall(*, run=None) -> tuple[bool, str]:
    """Remove OUR units. Never touches one we did not write."""
    d = unit_dir()
    svc, tmr = d / SERVICE, d / TIMER
    present = [p for p in (svc, tmr) if p.exists()]
    if not present:
        return False, "not installed — nothing to remove."
    foreign = [p for p in present if not ours(p)]
    if foreign:
        return False, (f"REFUSED: {', '.join(str(p) for p in foreign)} was not "
                       f"written by st tend. Leaving it alone.")
    if run is not None:
        run(["systemctl", "--user", "disable", "--now", TIMER])
    for p in present:
        p.unlink()
    if run is not None:
        run(["systemctl", "--user", "daemon-reload"])
    return True, f"removed {', '.join(p.name for p in present)}."


class GovernorWake:
    """Arm a one-shot wake per window, so a refreshed budget re-engages the crew
    without a human noticing (aegis-9mehy).

    THE TIMER IS FOR PROMPTNESS AND IS ALLOWED TO BE MISSING. tend's five-minute
    pass already re-reads the number and already re-engages; this exists because
    the five-hour window refills at a specific minute and every minute after it
    is capacity bought and not used. So EVERY failure in here — no systemd, a
    refused unit, an unwritable state file — logs and returns. It can cost a
    delay of one tend interval and it can never cost more than that. A
    supervision feature that could stop supervision is a bad trade, and the
    governor's own module says so in the fail-safe it opens with.

    IT NEVER DECIDES ANYTHING. The unit runs a plain `st tend`; the pass it wakes
    takes a fresh reading and that reading decides. Nothing in this class knows
    what a tier is. That separation is decision 1 of the bead, expressed as
    structure rather than as a rule somebody has to remember: re-engaging on a
    PREDICTED drop would spend a whole refreshed budget in minutes, and the only
    way to make that unrepresentable is for the scheduler to have no opinion.

    TRANSIENT UNITS, deliberately, not files in ~/.config/systemd/user. A one-
    shot whose fire time changes every five minutes is not configuration; it is
    state. `systemd-run` owns the lifecycle and a reboot forgets the lot, which
    is correct — after a reboot the next tend pass re-reads and re-arms from a
    fresh number rather than honouring a wake computed before the outage.
    """

    def __init__(self, st_bin: str, root, *, run=None, is_active=None,
                 now=time.time, log=None):
        self._st = st_bin
        self._root = Path(root)
        self.path = self._root / "governor" / "wake.json"
        # run(argv) -> returncode. Injected for the usual reason: this must be
        # provable with no systemd, and CI has none.
        self._run = run or (lambda argv: 1)
        self._is_active = is_active or (lambda unit: False)
        self._now = now
        self._log = log or (lambda msg: None)

    def unit(self, window: str) -> str:
        # systemd unit names take no underscores gracefully in every tool that
        # reads them back; the window label is the producer's (`five_hour`).
        return f"{WAKE_PREFIX}-{window.replace('_', '-')}"

    # --- the record of what is armed -----------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        try:
            from .files import write_json_atomic
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.path, data)
        except OSError:
            pass          # best-effort, same rule as the launch stamp

    def armed(self) -> dict:
        """{window: fire_epoch} — what we believe is armed. BELIEF, not truth:
        `sync` re-checks against systemd, because a record of a timer somebody
        killed is exactly the case that must self-heal."""
        out = {}
        for w, rec in self._load().items():
            try:
                out[w] = float((rec or {}).get("fire"))
            except (TypeError, ValueError):
                continue
        return out

    # --- arming ---------------------------------------------------------------

    def sync(self, plan: dict) -> list[str]:
        """Make the armed set match `plan` ({window: seconds from now}).

        Returns one line per CHANGE, for the caller to print. A pass that changed
        nothing says nothing — this runs every five minutes and a supervisor that
        narrates its own no-ops teaches operators to skim.
        """
        now = self._now()
        record = self._load()
        lines: list[str] = []
        for window, delay in sorted(plan.items()):
            fire = now + float(delay)
            unit = self.unit(window)
            prior = record.get(window) or {}
            try:
                prior_fire = float(prior.get("fire"))
            except (TypeError, ValueError):
                prior_fire = None
            # SKIP only when the target has not moved AND the timer is really
            # still there. Trusting the record alone would let a killed timer
            # stay "armed" forever, silently downgrading us to the fallback with
            # nothing saying so — the failure this whole bead is about, one
            # layer down.
            if (prior_fire is not None
                    and abs(prior_fire - fire) <= WAKE_TOLERANCE_S
                    and self._is_active(f"{unit}.timer")):
                continue
            if self._arm(unit, window, int(delay)):
                record[window] = {"fire": fire, "unit": unit}
                lines.append(f"governor wake ARMED: {unit} fires in "
                             f"{int(delay)}s to re-read the {window} budget "
                             f"(a plain tend pass; the reading decides)")
            else:
                # Loud, and NOT recorded as armed: an unarmed wake must be
                # retried next pass, and recording it would make us skip the
                # retry on the strength of a unit that does not exist.
                record.pop(window, None)
                lines.append(f"⚠ governor wake: could NOT arm {unit} — falling "
                             f"back to tend's own interval, so re-engagement is "
                             f"late, not lost")
        for window in [w for w in record if w not in plan]:
            self._disarm(self.unit(window))
            record.pop(window, None)
            lines.append(f"governor wake DISARMED for {window} — nothing is "
                         f"engaged on that budget, so there is nothing to "
                         f"re-engage")
        self._save(record)
        return lines

    def _arm(self, unit: str, window: str, delay: int) -> bool:
        # THE SAME REFUSAL install() MAKES, for the same measured reason
        # (aegis-408qs): systemd --user does not search ~/.local/bin, so a unit
        # written with a bare name fails 203/EXEC on every fire while the timer
        # reports itself healthy. systemd-run resolves the CALLER's PATH, which
        # makes a bare name work from a shell and fail from the st-tend unit's
        # minimal environment — a difference that only shows up in production.
        # Refusing here degrades to tend's own interval, loudly, which is a
        # thing the operator can read.
        if not os.path.isabs(self._st):
            self._log(f"governor wake: {self._st!r} is not an absolute path — "
                      f"systemd --user cannot exec it, and the wake would look "
                      f"armed while never running. Not arming {unit}")
            return False
        # STOP FIRST. systemd-run refuses a unit name that already exists, and
        # the whole point is that this is re-armed as the timestamp moves.
        self._disarm(unit)
        try:
            rc = self._run([
                "systemd-run", "--user", "--quiet", f"--unit={unit}",
                f"--on-active={delay}",
                # AccuracySec, because systemd's default (1min) would let a wake
                # land before the reset it was computed from — which reads as
                # "the reset did not happen" and holds the tier for a further
                # interval. The skew in wake_plan and this go together.
                "--timer-property=AccuracySec=1s",
                f"--setenv={WAKE_ENV}={window}",
                # No ticket id in the description: it is a VALUE this program
                # emits into a public-facing unit file, and the citation belongs
                # in the comments above, where it leaks nothing.
                f"--description=st governor: re-read usage after the {window} "
                f"budget resets",
                self._st, "--root", str(self._root), "tend",
            ])
        except Exception as e:      # noqa: BLE001 — no systemd is not a crash
            self._log(f"governor wake: {type(e).__name__} arming {unit}: "
                      f"{str(e)[:80]}")
            return False
        return rc == 0

    def _disarm(self, unit: str) -> None:
        for suffix in (".timer", ".service"):
            try:
                self._run(["systemctl", "--user", "stop", f"{unit}{suffix}"])
            except Exception:       # noqa: BLE001 — best effort, always
                pass


class PassLog:
    """WHEN did a pass last run, and what did it do?

    A watchdog with no watchdog is a silent single point of recovery failure:
    when the supervisor stops, nothing gets worse immediately — it just stops
    getting better, and nobody can see that from the inside. This makes the
    ABSENCE of a recent pass a readable fact, which is the only way it is ever
    noticed. `--status` prints the age, so "last pass: 4 days ago" is as loud as
    a failure.
    """

    def __init__(self, root: Path):
        self.path = Path(root) / "tend" / "last.json"

    def record(self, rep) -> None:
        from .files import write_json_atomic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, rep.as_record())

    def last(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None          # never ran, or unreadable — NOT "ran fine"

    def age_seconds(self, now=None) -> float | None:
        rec = self.last()
        if not rec or not rec.get("at"):
            return None
        return (now or time.time()) - float(rec["at"])


class CrashLog:
    """<root>/crashes.json — consecutive deaths per agent, for tend's backoff.

    Deliberately NOT the launch stamp store: a stamp says "st launched this once",
    and this says "st has launched this N times and it keeps dying". Conflating
    them would make a healthy relaunch look like a crash.

    Every read failure is (0, 0.0) and every write failure is swallowed. A
    supervisor must not stop supervising because a counter file is unreadable —
    the worst case of a lost counter is one extra launch attempt, and the worst
    case of a crashed supervisor is an unsupervised fleet.
    """

    def __init__(self, root) -> None:
        self.path = Path(root) / "crashes.json"

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        try:
            from .files import write_json_atomic
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.path, data)
        except OSError:
            pass

    def get(self, agent: str) -> tuple[int, float]:
        row = self._load().get(agent) or {}
        try:
            return int(row.get("deaths") or 0), float(row.get("last") or 0.0)
        except (TypeError, ValueError):
            return 0, 0.0

    def died(self, agent: str, now: float) -> None:
        data = self._load()
        deaths, _last = self.get(agent)
        data[agent] = {"deaths": deaths + 1, "last": now}
        self._save(data)

    def clear(self, agent: str) -> None:
        """Seen alive: the episode is over. An agent that recovers must not be
        punished for an old one."""
        data = self._load()
        if agent in data:
            del data[agent]
            self._save(data)
