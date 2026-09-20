"""Bounded, non-recursive peer census. A missing host is not an empty fleet."""
from concurrent.futures import ThreadPoolExecutor
import json
import shlex
import subprocess

VERSION = 1


def read_peer(peer):
    command = ('PATH="$HOME/.local/bin:$PATH" st --root '
               + shlex.quote(peer.root) + ' crew --json --local')
    try:
        result = subprocess.run(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '--',
             peer.ssh, command], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError(f"ssh/crew exit {result.returncode}: "
                             + (result.stderr.strip() or 'no diagnostic')[:300])
        value = json.loads(result.stdout)
        if (not isinstance(value, dict) or value.get('version') != VERSION
                or value.get('scope') != 'local' or value.get('complete') is not True
                or value.get('host') != peer.name
                or not isinstance(value.get('agents'), list)):
            raise ValueError('incompatible or incomplete local crew snapshot')
        names = set()
        for row in value['agents']:
            if (not isinstance(row, dict)
                    or any(not isinstance(row.get(k), str) for k in
                           ('name', 'role', 'state', 'work', 'posture', 'harness',
                            'settings', 'tree', 'pane'))
                    or type(row.get('live')) is not bool
                    or not row['name'] or row['name'] in names):
                raise ValueError('invalid or duplicate agent row')
            names.add(row['name'])
            row['host'] = peer.name
        return {'host': peer.name, 'agents': value['agents'], 'error': None}
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        reason = ' '.join(str(exc).split())[:400]
        return {'host': peer.name, 'agents': [],
                'error': f'{type(exc).__name__}: {reason}'}


def collect(peers):
    # Parallel reads bound a failed fleet sweep by one peer deadline, rather
    # than hiding a later healthy host behind each preceding timeout.
    peers = sorted(peers.values(), key=lambda peer: peer.name)
    if not peers:
        return []
    with ThreadPoolExecutor(max_workers=min(16, len(peers))) as pool:
        return list(pool.map(read_peer, peers))


def rows(results):
    return [row for result in results for row in result['agents']]


def errors(results):
    return [f"{r['host']} UNREACHABLE ({r['error']})"
            for r in results if r['error']]


def live_counts(results, governors):
    counts = {name: 0 for name in governors}
    for row in rows(results):
        if row['live']:
            if row['harness'] != 'claude' and row['harness'] not in governors:
                continue
            lane = row['harness'] if row['harness'] in governors else 'base'
            counts[lane] = counts.get(lane, 0) + 1
    return counts
