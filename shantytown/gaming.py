"""Local gaming hold: read-only Steam observation, independent of usage budgets.

The scheduled probe owns debounce. Consumers never extend a hold by reading it.
Manual holds have no expiry; stale automatic evidence is UNKNOWN, not gaming.
"""
from __future__ import annotations

import fcntl
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .files import write_json_atomic
from . import gaming_activity

MAX_AGE = 180
LIFT_DELAY = 120
LAUNCH_GRACE = 300

#: Shader replay with no reaper is evidence of an IMMINENT launch only for as
#: long as a player would sit in front of the progress dialog waiting for it.
#: Steam ALSO runs fossilize_replay as background library maintenance — after
#: downloads, game updates and driver changes — with no launch coming at all,
#: and that work is unbounded. Measured on this box 2026-09-15: a maintenance
#: run held the whole crew for 76 minutes, five workers pegged and ~800 forks a
#: second, while `appids` stayed empty from the first probe to the last. Treating
#: shaders as a game with no ceiling means Steam merely being OPEN can hold the
#: town indefinitely. An appid lifts the ceiling: a real game is held as long as
#: it runs, and one that appears after the ceiling expires re-arms the hold.
SHADER_GRACE = 1200

#: The operator's way through a hold, for ONE command. Named here rather than in
#: the CLI so the flag and the text that advertises it cannot drift apart.
OVERRIDE_FLAG = "--despite-hold"


@dataclass(frozen=True)
class Status:
    state: str = "off"
    appids: tuple[str, ...] = ()
    since: float = 0
    observed: float = 0
    error: str = ""
    gpu_busy: float | None = None
    game_cpu: float | None = None
    idle_since: float | None = None
    game_present_idle: bool = False

    @property
    def held(self) -> bool:
        return self.state in {"gaming", "manual", "ending"}

    @property
    def refusal(self) -> str:
        return ("GOVERNOR HOLD — gaming; launches, dispatch and respawns held. "
                "Wait for the session to end or clear the manual hold.") if self.held else ""

    def override_lines(self, invocation: str = "") -> tuple[str, ...]:
        """How an OPERATOR gets past this hold — the refusal's own remedy.

        A refusal that states a rule but no way through it sends the reader to
        the source, which is exactly where this one kept sending people. The
        right remedy DEPENDS on which hold is in force: `--clear` removes a
        manual marker and does nothing whatever to an automatic one, because the
        next scheduled probe re-asserts that 60 seconds later. Saying "clear the
        manual hold" to somebody holding an automatic one is worse than silence.

        Deliberately NOT folded into `refusal`: the agent-facing surfaces
        (dispatch, feed_check) quote that string, and an agent must not be handed
        the override for a hold that exists to protect somebody's game. The flag
        is for the person at the keyboard, who can see whether they are playing.
        """
        if not self.held:
            return ()
        retry = f"{invocation or 'st <command>'} {OVERRIDE_FLAG}"
        if self.state == "manual":
            return (f"launch anyway, this once:  {retry}",
                    "lift the hold for good:    st fleet hold gaming --clear")
        return (f"launch anyway, this once:  {retry}",
                "`st fleet hold gaming --clear` will NOT lift this one — the hold is "
                "automatic, and the next probe re-asserts it within a minute.")

    def render(self) -> str:
        if self.held and self.game_present_idle and self.state != "manual":
            minutes = int((self.observed - self.idle_since) / 60)
            return (f"game_present_idle — game present but idle {minutes} min — your call. "
                    "GAMING HOLD remains active; activity telemetry never auto-lifts it.")
        if self.held:
            return self.refusal + " Recommend leads only; defer heavy local work."
        if self.state == "clear":
            return "GAMING HOLD LIFTED — resume per the budget governor, not automatically to cap."
        if self.state == "unknown":
            return f"gaming observation UNKNOWN — {self.error}; automatic hold not applied"
        return "gaming detection off"


def game_roots(proc: Path = Path("/proc")) -> dict[int, str]:
    """Match argv tokens, never a shell/pgrep command mentioning the signature."""
    found = {}
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue  # A process exiting during the scan is ordinary.
        except PermissionError:
            continue  # Other users' processes need not be readable.
        if len(argv) < 3 or Path(argv[0].decode(errors="replace")).name != "reaper":
            continue
        if argv[1] != b"SteamLaunch":
            continue
        match = re.fullmatch(rb"AppId=([0-9]+)", argv[2])
        if match:
            found[int(entry.name)] = match[1].decode()
    return found


def shader_pids(proc: Path = Path("/proc")) -> tuple[int, ...]:
    """Shader replay precedes the game reaper; argv mentions are not evidence."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            executable = (entry / "cmdline").read_bytes().split(b"\0", 1)[0]
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if Path(executable.decode(errors="replace")).name == "fossilize_replay":
            found.append(int(entry.name))
    return tuple(sorted(found))


def game_appids(proc: Path = Path("/proc")) -> tuple[str, ...]:
    return tuple(sorted(set(game_roots(proc).values())))


def read(root: Path, *, now: float | None = None, spec=None) -> Status:
    folder = Path(root) / "gaming"
    now = time.time() if now is None else now
    try:
        if (folder / "manual").exists():
            return Status("manual", since=(folder / "manual").stat().st_mtime, observed=now)
        if not detection_enabled(folder, spec):
            return Status()
        data = json.loads((folder / "state.json").read_text())
        observed = float(data["observed"])
        if not 0 <= now - observed <= MAX_AGE:
            raise ValueError("scheduled probe is stale or future-dated")
        if data["state"] not in {"gaming", "ending", "clear", "unknown"}:
            raise ValueError("invalid probe state")
        return Status(data["state"], tuple(data["appids"]), float(data["since"]),
                      observed, data.get("error", ""), data.get("gpu_busy"),
                      data.get("game_cpu"), data.get("idle_since"),
                      bool(data.get("game_present_idle", False)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return Status("unknown", error=str(exc))


def manual(root: Path, *, clear: bool = False) -> None:
    folder = Path(root) / "gaming"
    folder.mkdir(parents=True, exist_ok=True)
    if clear:
        (folder / "manual").unlink(missing_ok=True)
    else:
        (folder / "manual").touch()


def probe(root: Path, *, proc: Path = Path("/proc"), now: float | None = None, spec=None) -> Status:
    folder = Path(root) / "gaming"
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    launch_grace = spec.grace if spec else LAUNCH_GRACE
    lift_delay = spec.lift_delay if spec else LIFT_DELAY
    shader_grace = spec.options.get('shader_grace', SHADER_GRACE) if spec else SHADER_GRACE
    # A delayed cron and an operator probe must not overwrite newer debounce state.
    with (folder / "probe.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = read(root, now=now, spec=spec)
        if not detection_enabled(folder, spec):
            return old
        try:
            if spec is None or not spec.options:
                roots = game_roots(proc)
                shaders = shader_pids(proc)
            else:
                from .quiet_detectors import processes
                opts = spec.options
                roots = {pid: fields['appid'] for pid, fields in processes(
                    proc, opts.get('game_executable', 'reaper'),
                    opts.get('game_arguments', ['SteamLaunch', r'AppId=(?P<appid>[0-9]+)'])).items()
                         if re.fullmatch(r'[0-9]+', fields.get('appid') or '')}
                shaders = tuple(sorted(processes(proc, opts.get('shader_executable', 'fossilize_replay'))))
            appids = tuple(sorted(set(roots.values())))
            try:
                previous = json.loads((folder / "state.json").read_text())
            except (OSError, ValueError):
                previous = {}
            absent = previous.get("absent_since")
            automatic = previous.get("state") if 0 <= now - previous.get("observed", 0) <= MAX_AGE else None
            since = previous.get("since", now) if automatic in {"gaming", "ending"} else now
            launch_until = previous.get("launch_until", since + launch_grace)
            if automatic not in {"gaming", "ending"} or (appids and not previous.get("appids")):
                launch_until = now + launch_grace
            # Shader-only evidence ages out; an appid never does. The clock starts
            # when a shader phase begins with no game and is dropped the moment one
            # appears, so a launch that follows a long precompile is still held.
            shader_since = previous.get("shader_since") if automatic else None
            shader_absent_since = previous.get("shader_absent_since") if automatic else None
            if appids:
                shader_since = shader_absent_since = None
            elif shaders:
                shader_absent_since = None
                if shader_since is None:
                    shader_since = now
            else:
                # Steam precompiles in BATCHES with gaps between them. Clearing the
                # ceiling clock the instant a batch ended let the NEXT batch restart
                # it, so the 20-minute ceiling never elapsed and the hold stood for
                # 22 h after play ended (aegis-6ikuk8). Only a SUSTAINED absence ends
                # the phase; a gap shorter than the lift delay keeps the clock running,
                # while a genuinely new precompile later still earns its own ceiling.
                shader_absent_since = (now if shader_absent_since is None
                                       else float(shader_absent_since))
                if now - shader_absent_since > lift_delay:
                    shader_since = None
            fresh_shaders = bool(shaders) and (
                shader_since is None or now - float(shader_since) <= shader_grace)
            if appids or fresh_shaders:
                state, absent = "gaming", None
            elif automatic in {"gaming", "ending"}:
                absent = now if absent is None else float(absent)
                # Brief reaper disappearance during launch must not release workers.
                if now < launch_until:
                    state = "gaming"
                else:
                    state = "ending" if now - absent <= lift_delay else "clear"
            else:
                state = "clear"
            data = dict(state=state, appids=appids, since=since if state != "clear" else 0,
                        observed=now, absent_since=absent, shader_pids=shaders,
                        launch_until=launch_until, shader_since=shader_since,
                        shader_absent_since=shader_absent_since)
            try:
                data.update(gaming_activity.observe(proc, set(roots) | set(shaders), previous, now))
            except (OSError, ValueError, TypeError):
                pass  # Corroboration cannot turn a known game into signal loss.
        except (OSError, ValueError, TypeError) as exc:
            data = dict(state="unknown", appids=[], since=0, observed=now, error=str(exc))
        write_json_atomic(folder / "state.json", data)
        return read(root, now=now, spec=spec)


def metrics(status: Status) -> str:
    # Bounded labels: app IDs are reported separately, never retained as inactive
    # per-game series. The alert consumes the single unlabeled hold gauge.
    base = ("# HELP aegis_gaming_session_active Local gaming hold including manual override.\n"
            "# TYPE aegis_gaming_session_active gauge\n"
            f"aegis_gaming_session_active {int(status.held)}\n"
            "# HELP aegis_gaming_probe_ok Whether current automatic evidence is readable.\n"
            "# TYPE aegis_gaming_probe_ok gauge\n"
            f"aegis_gaming_probe_ok {int(status.state not in {'unknown', 'off'})}\n"
            "# HELP aegis_gaming_probe_timestamp_seconds Last scheduled observation.\n"
            "# TYPE aegis_gaming_probe_timestamp_seconds gauge\n"
            f"aegis_gaming_probe_timestamp_seconds {status.observed}\n"
            "# HELP aegis_gaming_session_start_timestamp_seconds Current hold start.\n"
            "# TYPE aegis_gaming_session_start_timestamp_seconds gauge\n"
            f"aegis_gaming_session_start_timestamp_seconds {status.since}\n")

    extra = ("# HELP aegis_gaming_present_idle Weak idle advisory; never lifts the hold.\n"
             "# TYPE aegis_gaming_present_idle gauge\n"
             f"aegis_gaming_present_idle {int(status.game_present_idle)}\n")
    for name, value in [('gpu_busy_percent', status.gpu_busy), ('tree_cpu_percent', status.game_cpu)]:
        if value is not None:
            extra += f"# TYPE aegis_gaming_{name} gauge\naegis_gaming_{name} {value}\n"
    for appid in status.appids:
        if re.fullmatch(r'[0-9]+', appid):
            extra += f'aegis_gaming_app_present{{appid="{appid}"}} 1\n'
    return base + extra


def detection_enabled(folder: Path, spec=None) -> bool:
    if (folder / 'disabled').exists():
        return False
    if spec is not None and spec.enabled is not None:
        return spec.enabled
    return (folder / 'enabled').exists()
