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
FOREIGN_UNITS = ("gastown-crew-watchdog.timer",)


def unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(rep.as_record(), indent=2, sort_keys=True))

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
