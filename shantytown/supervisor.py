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

# Supervisors known to tend the same crew. Presence is a REFUSAL, not a warning:
# two things respawning the same agents fight, and the fight looks like flapping
# nobody can attribute.
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


def foreign_supervisor(is_active) -> str | None:
    """Is something else already tending this crew? Returns its unit name."""
    for unit in FOREIGN_UNITS:
        if is_active(unit):
            return unit
    return None


def install(st_bin: str, root: Path, *, interval: str = "5min", run=None,
            is_active=lambda unit: False, dry_run: bool = False) -> tuple[bool, str]:
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

    other = foreign_supervisor(is_active)
    if other:
        return False, (
            f"REFUSED: {other} is active and supervises the same crew. Two "
            f"supervisors respawning the same agents is worse than none — they "
            f"fight, and the fight looks like flapping nobody can attribute. "
            f"Decide which one owns this crew (that is a human's call, not "
            f"this command's), then re-run."
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
