"""Account observations merged across hosts without inventing a fresh timestamp."""
from __future__ import annotations

import math
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from contextlib import contextmanager
import json
from pathlib import Path
import select
import shlex
import subprocess
import time

from . import governor as gov
from .governor import GovernorError, Reading

VERSION = 1


class AdmissionUnavailable(GovernorError):
    pass


@contextmanager
def admission_lock(root, host, peers):
    """Serialize census-to-launch on the lexically first configured host.

    Every peer must list the same host set. There is no authority failover:
    choosing a second lock when the first is unreachable would split the cap.
    SSH holds the remote flock through stdin until the local launch completes.
    This is coordination for a trusted, connected fleet, not a consensus service.
    """
    if not peers:
        yield
        return
    if not host or host in peers:
        raise AdmissionUnavailable('account admission needs a unique local host name')
    owner = min([host, *peers])
    if owner == host:
        import fcntl
        path = Path(root) / 'governor' / 'admission.lock'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as handle:
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise AdmissionUnavailable('account admission busy; retry after the current launch')
                    time.sleep(.05)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        return
    peer = peers[owner]
    script = '''import fcntl, pathlib, sys, time
p = pathlib.Path(sys.argv[1]) / 'governor' / 'admission.lock'
p.parent.mkdir(parents=True, exist_ok=True)
with p.open('a') as f:
    deadline = time.monotonic() + 10
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                sys.exit(2)
            time.sleep(.05)
    print('locked', flush=True)
    sys.stdin.buffer.read(1)
'''
    command = 'python3 -u -c ' + shlex.quote(script) + ' ' + shlex.quote(peer.root)
    try:
        process = subprocess.Popen(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
             '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
             '--', peer.ssh, command], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise AdmissionUnavailable(f'account admission authority {owner} unreachable') from exc
    try:
        ready, _, _ = select.select([process.stdout], [], [], 16)
        if not ready or process.stdout.readline() != b'locked\n' or process.poll() is not None:
            raise AdmissionUnavailable(f'account admission authority {owner} unavailable or busy')
        yield
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()
# Policy is portable; reader coordinates and credentials emphatically are not.
POLICY_FIELDS = ('window', 'on_signal_lost', 'relax_margin', 'max_age_seconds',
                 'max_agents', 'delegation_reserve_pct', 'exempt', 'limit_id')


def policy_wire(policy):
    data = {key: getattr(policy, key) for key in POLICY_FIELDS}
    data.update(tier=[asdict(t) for t in policy.tiers],
                burndown=[asdict(b) for b in policy.burndowns],
                pace=[asdict(p) for p in policy.paces])

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if v is not None}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value
    return clean(data)


def observations(governor):
    """Read once, preserving failed probes without exporting transport secrets."""
    reader = governor.reader
    try:
        if hasattr(reader, 'read_all_detailed'):
            readings, error, fault = reader.read_all_detailed()
            if error and not readings:
                readings = {w: Reading(ok=False, error='local usage reader failed',
                                       fault=fault) for w in governor.policy.windows()}
        elif hasattr(reader, 'read_all'):
            readings = reader.read_all()
        else:
            readings = {governor.policy.window: reader.read()}
    except Exception:
        readings = {w: Reading(ok=False, error='local usage reader failed')
                    for w in governor.policy.windows()}
    return {w: asdict(replace(r, source='host-observation',
                              error='local usage reader failed' if r.error else ''))
            for w, r in readings.items()}


def snapshot(host, governors, agents, *, now=None, hosts=None):
    value = dict(version=VERSION, scope='local', host=host, complete=True,
                observed_at=time.time() if now is None else now, agents=agents,
                governors={name: dict(policy=policy_wire(g.policy),
                                      readings=observations(g),
                                      state=asdict(g.state.get() if g.state else gov.Engaged()))
                           for name, g in governors.items()})
    if hosts is not None:
        value['hosts'] = sorted(hosts)
    return value


def _number(value, *, optional=False):
    if optional and value is None:
        return
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('invalid numeric observation')


def validate(value, host, *, now=None):
    clock = time.time() if now is None else now
    if (not isinstance(value, dict) or value.get('version') != VERSION
            or value.get('scope') != 'local' or value.get('host') != host
            or value.get('complete') is not True
            or not isinstance(value.get('agents'), list)
            or not isinstance(value.get('governors'), dict)):
        raise ValueError('incompatible or incomplete governor snapshot')
    _number(value.get('observed_at'))
    if not -30 <= clock - value['observed_at'] <= 60:
        raise ValueError('stale or future governor census')
    names = set()
    for row in value['agents']:
        if (not isinstance(row, dict) or type(row.get('live')) is not bool
                or not isinstance(row.get('name'), str) or not row['name']
                or row['name'] in names or row.get('host') != host
                or not isinstance(row.get('harness'), str) or not row['harness']):
            raise ValueError('invalid or duplicate governor agent row')
        names.add(row['name'])
    for name, entry in value['governors'].items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise ValueError('invalid governor lane')
        policy = entry.get('policy')
        if not isinstance(policy, dict) or set(policy) - set(POLICY_FIELDS) - {'tier', 'burndown', 'pace'}:
            raise ValueError('invalid portable policy')
        gov.parse(policy)
        readings = entry.get('readings')
        if not isinstance(readings, dict):
            raise ValueError('missing usage readings')
        for window, raw in readings.items():
            if not isinstance(window, str) or not isinstance(raw, dict):
                raise ValueError('invalid window observation')
            reading = Reading(**raw)
            for key in ('pct', 'at', 'cache_age', 'reset_at', 'probe_http_status'):
                _number(getattr(reading, key), optional=True)
            if (type(reading.ok) is not bool or not isinstance(reading.error, str)
                    or not isinstance(reading.source, str)
                    or reading.pct is not None and not 0 <= reading.pct <= 100
                    or reading.at is not None and reading.at > clock + 30):
                raise ValueError('invalid usage observation')
        state = entry.get('state')
        if not isinstance(state, dict) or not isinstance(state.get('by_window'), dict):
            raise ValueError('invalid governor hold')
        engaged = gov.Engaged(**state)
        _number(engaged.at, optional=True)
        _number(engaged.since)
        for held in engaged.by_window.values():
            if not isinstance(held, dict):
                raise ValueError('invalid window hold')
            for key in ('at', 'since', 'reset'):
                _number(held.get(key), optional=True)
    return value


def read_peer(peer):
    command = ('PATH="$HOME/.local/bin:$PATH" st --root '
               + shlex.quote(peer.root) + ' crew --governor --json --local')
    try:
        result = subprocess.run(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '--',
             peer.ssh, command], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError(f'ssh/governor exit {result.returncode}')
        if len(result.stdout) > 1024 * 1024:
            raise ValueError('governor snapshot exceeds 1 MiB')
        return validate(json.loads(result.stdout), peer.name), ''
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, GovernorError) as exc:
        return None, f'{peer.name} governor unavailable ({type(exc).__name__}: {exc})'


def collect(peers):
    ordered = sorted(peers.values(), key=lambda p: p.name)
    if not ordered:
        return []
    with ThreadPoolExecutor(max_workers=min(16, len(ordered))) as pool:
        return list(pool.map(read_peer, ordered))


class MergedState:
    def __init__(self, states, destination, windows):
        self.destination = destination
        held = {}
        for window in windows:
            # The strictest remembered hold survives; either host may have seen
            # a crossing the other missed. Governor.evaluate still releases it
            # on the merged reading using the ordinary margin/rollover rules.
            state = max(states, key=lambda s: (s.hold(window)[0] or -1,
                                               s.hold(window)[1]))
            at, since = state.hold(window)
            held[window] = dict(at=at, since=since, reset=state.reset_of(window))
        self.engaged = gov.Engaged(by_window=held)

    def get(self):
        return self.engaged

    def set(self, value):
        self.engaged = value
        self.destination.set(value)


class FleetGovernor:
    """One invocation's account view. No peer failure becomes zero occupancy."""
    def __init__(self, local, peers, root):
        self.local = local['host']
        self.snapshots = [local] + [s for s, error in peers if s is not None and not error]
        self.errors = [error for _, error in peers if error]
        if 'hosts' in local:
            for item in self.snapshots:
                if item.get('hosts') != local['hosts']:
                    self.errors.append(f'{item["host"]} host membership disagrees; '
                                       'cannot establish one admission authority')
        self.governors = {}
        self.policy_hosts = {}
        lanes = sorted({name for s in self.snapshots for name in s['governors']})
        for name in lanes:
            entries = {s['host']: s['governors'][name] for s in self.snapshots
                       if name in s['governors']}
            host = min(entries)
            data = entries[host]['policy']
            if any(entry['policy'] != data for entry in entries.values()):
                self.errors.append(f'{name} governor policies disagree across hosts; growth held')
            policy = gov.parse(data)
            reader = FreshestReader({h: {w: Reading(**r) for w, r in e['readings'].items()}
                                     for h, e in entries.items()})
            states = [gov.Engaged(**e['state']) for e in entries.values()]
            destination = gov.FilesGovernorState(root, None if name == 'base' else name)
            state = MergedState(states, destination, policy.windows())
            self.governors[name] = gov.Governor(policy, reader, state, name=name)
            self.policy_hosts[name] = host

    @property
    def agents(self):
        return [row for snapshot in self.snapshots for row in snapshot['agents']]

    def lane(self, harness):
        if harness in self.governors:
            return harness
        if len(self.governors) == 1 or harness == 'claude':
            return 'base'
        return harness

    def counts(self, local_agents=None):
        rows = [r for r in self.agents if local_agents is None or r['host'] != self.local]
        if local_agents is not None:
            rows += local_agents
        result = {name: 0 for name in self.governors}
        for row in rows:
            if row['live']:
                lane = self.lane(row['harness'])
                result[lane] = result.get(lane, 0) + 1
        return result

    def fallback(self):
        if not self.errors:
            return ''
        return ('; '.join(self.errors) + '; local/available usage fallback; '
                'remote spend UNKNOWN; new launches and dispatch held')

    def admits_launch(self, harness, local_agents=None):
        if self.errors:
            return self.fallback()
        lane = self.lane(harness)
        governor = self.governors.get(lane)
        if governor is None:
            return 'account governor unconfigured for ' + harness if self.governors else ''
        verdict = governor.evaluate(persist=False)
        if verdict.frozen or verdict.drains:
            return verdict.alarm or verdict.why or 'account governor holds launches'
        count = self.counts(local_agents).get(lane, 0)
        if verdict.max_agents is not None and count >= verdict.max_agents:
            return (f'account governor {lane}: {count} live across hosts; '
                    f'launch would count {count + 1}/{verdict.max_agents} (max_agents cap)')
        return ''


class FreshestReader:
    """Immutable per-invocation observations with reproducible host provenance.

    The producer's timestamp decides freshness, never the time an SSH response
    arrives. A newer FAILED probe must remain a failed probe; filtering it out
    would turn a transport recovery mechanism into stale-success laundering.
    Governor.evaluate remains responsible for age, rotation grace and fail-safe
    policy. Missing timestamps survive only when no dated observation exists,
    and remain unaged (therefore unusable) at that gate.
    """

    def __init__(self, observations: Mapping[str, Mapping[str, Reading]]):
        selected: dict[str, tuple[float, str, Reading]] = {}
        for host, windows in sorted(observations.items()):
            for window, reading in windows.items():
                if not isinstance(reading, Reading):
                    raise GovernorError(f"invalid reading for {host}/{window}")
                stamp = reading.at
                if stamp is not None and (
                        isinstance(stamp, bool) or not isinstance(stamp, (float, int))
                        or not math.isfinite(stamp)):
                    raise GovernorError(f"invalid probe timestamp for {host}/{window}")
                rank = float('-inf') if stamp is None else stamp
                # Equal timestamps select the same host regardless of which
                # machine is asking or which response finished first.
                if window not in selected or rank > selected[window][0]:
                    selected[window] = (rank, host, reading)
        self._readings = {window: value[2] for window, value in selected.items()}
        self.provenance = {window: value[1] for window, value in selected.items()}

    def read_all(self) -> dict[str, Reading]:
        return dict(self._readings)
