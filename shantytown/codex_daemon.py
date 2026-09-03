"""Per-card Codex app-server daemon health and repair.

Process ownership comes only from ``SHANTY_AGENT`` in ``/proc/PID/environ``.
argv is used to identify the daemon kind, never the card it belongs to.
"""
from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path


FLAG = "codex-daemon-wedged"
STALE_LOCK_S = 10 * 60


@dataclass(frozen=True)
class Health:
    agent: str
    daemon_pids: tuple[int, ...] = ()
    zombie_pids: tuple[int, ...] = ()
    stale_lock: Path | None = None

    @property
    def blocked(self) -> bool:
        return bool(self.zombie_pids or self.stale_lock)

    def reason(self) -> str:
        bits = []
        if self.zombie_pids:
            bits.append(
                "defunct app-server child " + ",".join(map(str, self.zombie_pids))
            )
        if self.stale_lock:
            bits.append(f"stale startup lock {self.stale_lock}")
        return "; ".join(bits)


def _environ(pid: int, proc: Path) -> dict[str, str]:
    try:
        fields = (proc / str(pid) / "environ").read_bytes().split(b"\0")
        return {k.decode(errors="replace"): v.decode(errors="replace")
                for item in fields if b"=" in item
                for k, v in (item.split(b"=", 1),)}
    except (OSError, ValueError):
        return {}


def _stat(pid: int, proc: Path) -> tuple[str, int] | None:
    try:
        raw = (proc / str(pid) / "stat").read_text()
        tail = raw[raw.rfind(")") + 2:].split()
        return tail[0], int(tail[1])
    except (OSError, ValueError, IndexError):
        return None


def _cmdline(pid: int, proc: Path) -> str:
    try:
        raw = (proc / str(pid) / "cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def _recorded_pid(home: Path) -> int | None:
    """Read Codex's own control-server identity record."""
    try:
        value = json.loads(
            (home / "app-server-daemon" / "app-server.pid").read_text()
        )["pid"]
        return value if isinstance(value, int) and value > 0 else None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _is_control_server(cmd: str) -> bool:
    """Match the production remote-control argv, not the updater daemon."""
    words = cmd.split()
    return (
        "app-server" in words
        and "--remote-control" in words
        and "--listen" in words
        and "unix://" in words
    )


def _is_updater(cmd: str) -> bool:
    words = cmd.split()
    return "app-server" in words and "daemon" in words and "pid-update-loop" in words


def stop_owned(agent: str, *, proc: Path = Path("/proc"), kill=os.kill) -> tuple[int, ...]:
    """Terminate every Remote Control process proven to belong to ``agent``.

    Codex's updater is a sibling daemon, not a child of the tmux-hosted TUI, so
    killing the pane leaves both it and app-server alive.  Environment identity
    plus a known daemon argv is the ownership proof; a PID record alone is not.
    """
    stopped: list[int] = []
    try:
        pids = sorted(int(p.name) for p in proc.iterdir() if p.name.isdigit())
    except OSError:
        pids = []
    for pid in pids:
        cmd = _cmdline(pid, proc)
        if (_environ(pid, proc).get("SHANTY_AGENT") == agent
                and (_is_control_server(cmd) or _is_updater(cmd))):
            try:
                kill(pid, signal.SIGTERM)
                stopped.append(pid)
            except ProcessLookupError:
                pass
    return tuple(sorted(stopped))


def inspect(agent: str, *, runtime_dir: Path | None = None,
            proc: Path = Path("/proc"), now: float | None = None) -> Health:
    """Return the named card's launch blocker; report only proven facts."""
    runtime_dir = runtime_dir or Path(
        os.environ.get("XDG_RUNTIME_DIR", Path.home() / ".cache")
    )
    home = runtime_dir / "shantytown" / "codex" / agent
    daemons: list[int] = []
    zombies: list[int] = []
    parents: dict[int, tuple[str, int]] = {}
    try:
        pids = [int(p.name) for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        pids = []
    recorded_pid = _recorded_pid(home)
    for pid in pids:
        st = _stat(pid, proc)
        if st:
            parents[pid] = st
        cmd = _cmdline(pid, proc)
        owned = _environ(pid, proc).get("SHANTY_AGENT") == agent
        if pid == recorded_pid and _is_control_server(cmd) and owned:
            daemons.append(pid)
    daemon_set = set(daemons)
    for pid, (state, ppid) in parents.items():
        if state == "Z" and ppid in daemon_set:
            zombies.append(pid)
    lock = home / "app-server-control" / "app-server-startup.lock"
    stale = None
    try:
        age = (time.time() if now is None else now) - lock.stat().st_mtime
        # The lock itself is empty and survives healthy launches.  Its age is
        # therefore evidence only when Codex's recorded control-server PID is
        # absent; an old lock beside a live recorded server is healthy.
        if age > STALE_LOCK_S and recorded_pid is not None and not daemon_set:
            stale = lock
    except OSError:
        pass
    return Health(agent, tuple(sorted(daemons)), tuple(sorted(zombies)), stale)


def repair(agent: str, *, runtime_dir: Path | None = None,
           proc: Path = Path("/proc"), kill=os.kill,
           now: float | None = None) -> Health:
    """Terminate only a proven unhealthy daemon for ``agent`` and clear its lock."""
    runtime_dir = runtime_dir or Path(
        os.environ.get("XDG_RUNTIME_DIR", Path.home() / ".cache")
    )
    home = runtime_dir / "shantytown" / "codex" / agent
    found = inspect(agent, runtime_dir=runtime_dir, proc=proc, now=now)
    if not found.blocked:
        return found
    for pid in found.daemon_pids:
        # Re-check identity immediately before signalling: PIDs can be reused.
        if (
            _recorded_pid(home) == pid
            and _is_control_server(_cmdline(pid, proc))
            and _environ(pid, proc).get("SHANTY_AGENT") == agent
        ):
            try:
                kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    if found.stale_lock:
        try:
            found.stale_lock.unlink()
        except FileNotFoundError:
            pass
    return found
