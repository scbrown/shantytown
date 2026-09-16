"""Reversible gaming slowdown for verified, exclusive crew pane scopes only."""
from __future__ import annotations

import fcntl
import json
import subprocess
from pathlib import Path

from . import panemem
from .files import write_json_atomic


def _read(scope, run):
    result = run(['systemctl', '--user', 'show', scope, '-p', 'CPUWeight',
                  '-p', 'CPUQuotaPerSecUSec'], capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise OSError(result.stderr.strip())
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    # systemctl's timespan renderer uses these units for CPU quota. Refuse an
    # unknown representation rather than lose the operator's original limit.
    quota = values['CPUQuotaPerSecUSec']
    if quota == 'infinity':
        percent = None
    else:
        import re
        match = re.fullmatch(r'([0-9.]+)(us|ms|s)', quota)
        if not match:
            raise ValueError(f'unrecognized CPU quota {quota!r}')
        percent = float(match[1]) * {'us': .0001, 'ms': .1, 's': 100}[match[2]]
    weight = values['CPUWeight']
    if weight == '[not set]':
        weight = ''
    elif not weight.isdecimal():
        raise ValueError(f'unrecognized CPU weight {weight!r}')
    return {'quota': percent, 'weight': weight}


def _set(scope, values, run):
    quota = '' if values['quota'] is None else f"{values['quota']:g}%"
    result = run(['systemctl', '--user', 'set-property', '--runtime', scope,
                  f'CPUQuota={quota}', f"CPUWeight={values['weight']}"],
                 capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise OSError(result.stderr.strip())
    if _read(scope, run) != values:
        raise OSError('CPU property read-back differs from requested values')


def owned_scope(root, agent, pane_pid, cgroup_path):
    """Accept pane descendants and reparented runtimes with matching provenance.

    Codex's persistent processes can be adopted by the user service manager.
    Their immutable launch environment identifies both agent AND deployment;
    process names alone cannot establish ownership. MCP children may strip that
    environment, so check descendants of the attributed runtime roots too.
    """
    exclusive, why = panemem.scope_is_exclusive_to(pane_pid, cgroup_path)
    if exclusive:
        return True, why
    try:
        pids = panemem.scope_pids(cgroup_path)
        anchors = [int(pane_pid)]
        for pid in pids:
            try:
                env = dict(part.split(b'=', 1) for part in
                           Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
                           if b'=' in part)
            except FileNotFoundError:
                continue
            if (env.get(b'SHANTY_AGENT') == agent.encode()
                    and env.get(b'SHANTY_ROOT') == str(root).encode()):
                anchors.append(pid)
        foreign = [pid for pid in pids if not any(
            panemem._is_descendant(pid, anchor) for anchor in anchors)]
        if foreign:
            return False, f'{len(foreign)} processes lack matching crew provenance'
        return True, 'pane tree plus attributed reparented runtime trees'
    except (OSError, ValueError) as exc:
        return False, f'cannot verify reparented runtime ownership: {exc}'


def reconcile(root, held, pane_pids, *, run=subprocess.run):
    """Persist originals before applying; lift only the exact settings we applied."""
    folder = Path(root) / 'gaming'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'scopes.json'
    messages = []
    with (folder / 'scopes.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            records = json.loads(path.read_text())
        except FileNotFoundError:
            records = {}
        # Corrupt recovery data is a refusal, never permission to overwrite it.
        if not isinstance(records, dict):
            raise ValueError('invalid gaming scope recovery ledger')
        if held:
            for agent, pid in pane_pids:
                scope = panemem.scope_of_pid(pid)
                group = panemem._cgroup_path_of_pid(pid)
                if not scope or not scope.startswith('tmux-spawn-') or not group:
                    messages.append(f'slowdown UNKNOWN: pane {pid} has no private tmux scope')
                    continue
                exclusive, why = owned_scope(root, agent, pid, group)
                if not exclusive:
                    messages.append(f'slowdown UNKNOWN: {scope}: {why}')
                    continue
                try:
                    current = _read(scope, run)
                    if scope not in records:
                        quota = current['quota']
                        target = {'quota': min(quota, 25) if quota is not None else 25,
                                  'weight': str(min(int(current['weight'] or 100), 10))}
                        records[scope] = {'original': current, 'applied': target}
                        write_json_atomic(path, records)
                    record = records[scope]
                    if current not in (record['original'], record['applied']):
                        messages.append(f'slowdown UNKNOWN: {scope} changed by another controller')
                        continue
                    _set(scope, record['applied'], run)
                    messages.append(f'slowdown verified: {scope} CPUQuota={record["applied"]["quota"]}%')
                except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                    messages.append(f'slowdown UNKNOWN: {scope}: {exc}')
        else:
            for scope, record in list(records.items()):
                try:
                    current = _read(scope, run)
                    if current == record['applied']:
                        _set(scope, record['original'], run)
                        messages.append(f'slowdown restored: {scope}')
                    elif current != record['original']:
                        messages.append(f'slowdown UNKNOWN: {scope} changed externally; preserving current values')
                    del records[scope]
                    write_json_atomic(path, records)
                except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                    messages.append(f'slowdown restore UNKNOWN: {scope}: {exc}')
    return messages
