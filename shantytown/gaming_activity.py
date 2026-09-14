"""Weak activity corroboration; these readings can never lift a gaming hold."""
from __future__ import annotations

import math
import os
import subprocess
from collections import defaultdict
from pathlib import Path

IDLE_DELAY = 600


def gpu_busy():
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3)
        if result.returncode:
            return None
        values = [float(line) for line in result.stdout.splitlines()]
        return max(values) if values and all(math.isfinite(v) and 0 <= v <= 100 for v in values) else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def tree_ticks(proc, roots):
    children = defaultdict(list)
    stats = {}
    for entry in Path(proc).iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            # comm may contain spaces or parentheses; fields start after the
            # LAST closing paren. Include starttime in keys to exclude PID reuse.
            fields = (entry / 'stat').read_text().rsplit(') ', 1)[1].split()
            pid = int(entry.name)
            children[int(fields[1])].append(pid)
            stats[pid] = (fields[19], int(fields[11]) + int(fields[12]))
        except (OSError, ValueError, IndexError):
            continue
    if not all(pid in stats for pid in roots):
        return None
    selected = set()
    pending = list(roots)
    while pending:
        pid = pending.pop()
        if pid in selected:
            continue
        selected.add(pid)
        pending.extend(children[pid])
    return {f'{pid}:{stats[pid][0]}': stats[pid][1] for pid in selected}


def observe(proc, roots, previous, now):
    gpu = gpu_busy()
    ticks = tree_ticks(proc, roots) if roots else {}
    elapsed = now - previous.get('observed', 0)
    old_ticks = previous.get('ticks')
    cpu = None
    if not roots:
        cpu = 0.0
    elif ticks is not None and isinstance(old_ticks, dict) and 0 < elapsed <= 180:
        # Count only continuing process identities. New/exited processes are
        # unknown for this interval, never a reason to declare the game idle.
        if ticks.keys() == old_ticks.keys():
            delta = sum(max(0, value - old_ticks[key]) for key, value in ticks.items())
            cpu = delta / os.sysconf('SC_CLK_TCK') / elapsed * 100
    low = bool(roots) and gpu is not None and cpu is not None and gpu <= 5 and cpu <= 2
    idle_since = (previous.get('idle_since') if 0 < elapsed <= 180 else None) if low else None
    if low and idle_since is None:
        idle_since = now
    return dict(gpu_busy=gpu, game_cpu=cpu, ticks=ticks, idle_since=idle_since,
                game_present_idle=idle_since is not None and now - idle_since >= IDLE_DELAY)
