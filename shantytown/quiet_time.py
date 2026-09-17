"""Aggregate quiet-time holds; only probes advance automatic state.

One confirmed hold wins over clear/unknown peers. Stale evidence fails open,
with UNKNOWN exported, matching the original gaming governor contract.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import gaming
from .files import write_json_atomic
from .quiet_detectors import Detector, Policy, READERS


@dataclass(frozen=True)
class Observation:
    state: str = 'off'
    since: float = 0
    observed: float = 0
    error: str = ''

    @property
    def held(self):
        return self.state in {'active', 'manual', 'ending'}


@dataclass(frozen=True)
class Status:
    entries: tuple[tuple[str, object], ...]

    @property
    def reasons(self):
        return tuple(name for name, status in self.entries if status.held)

    @property
    def held(self):
        return bool(self.reasons)

    @property
    def state(self):
        if self.held:
            return 'manual' if all(s.state == 'manual' for _, s in self.entries if s.held) else 'active'
        # A healthy negative is a usable admission decision. An unavailable
        # peer remains visible in its own metrics/rendering, not an outage of
        # every consumer (notably while a new detector awaits its first probe).
        return ('clear' if any(s.state == 'clear' for _, s in self.entries) else
                'unknown' if any(s.state == 'unknown' for _, s in self.entries) else 'off')

    @property
    def game_present_idle(self):
        return any(getattr(s, 'game_present_idle', False) for _, s in self.entries)

    @property
    def refusal(self):
        return (f"GOVERNOR HOLD — {', '.join(self.reasons)}; launches, dispatch and respawns held. "
                'Wait for quiet time to end or clear the corresponding manual hold.') if self.held else ''

    def render(self):
        failures = ', '.join(name for name, s in self.entries if s.state == 'unknown')
        text = (self.refusal + ' Recommend leads only; defer heavy local work.' if self.held else
                'quiet-time observation UNKNOWN — automatic hold not applied' if self.state == 'unknown' else
                'QUIET-TIME HOLD LIFTED — resume per the budget governor, not automatically to cap.'
                if self.state == 'clear' else 'quiet-time detection off')
        if self.game_present_idle:
            text += ' game_present_idle — weak activity evidence never lifts gaming.'
        return text + (f' UNKNOWN detectors: {failures}.' if failures else '')

    def override_lines(self, invocation=''):
        if not self.held:
            return ()
        lines = [f"launch anyway, this once:  {invocation or 'st <command>'} {gaming.OVERRIDE_FLAG}"]
        for name, status in self.entries:
            if status.held:
                lines.append(f'st fleet hold {name} --clear' if status.state == 'manual' else
                             f'{name} is automatic; --clear cannot lift it.')
        return tuple(lines)


def folder(root, name):
    return Path(root) / 'gaming' if name == 'gaming' else Path(root) / 'quiet_time' / name


def fingerprint(spec):
    return hashlib.sha256(json.dumps(asdict(spec), sort_keys=True).encode()).hexdigest()


def enabled(root, spec):
    path = folder(root, spec.name)
    return not (path / 'disabled').exists() and bool(spec.enabled)


def _read(root, spec, now):
    path = folder(root, spec.name)
    try:
        if (path / 'manual').exists():
            return Observation('manual', (path / 'manual').stat().st_mtime, now)
        if not enabled(root, spec):
            return Observation()
        data = json.loads((path / 'state.json').read_text())
        if data['config'] != fingerprint(spec) or not 0 <= now - data['observed'] <= gaming.MAX_AGE:
            raise ValueError('stale observation or changed config')
        if data['state'] not in {'active', 'ending', 'clear', 'unknown'}:
            raise ValueError('invalid state')
        return Observation(data['state'], data['since'], data['observed'], data.get('error', ''))
    except (OSError, ValueError, KeyError, TypeError):
        return Observation('unknown', error='scheduled observation missing, stale or invalid')


def _probe(root, spec, proc, now):
    path = folder(root, spec.name)
    path.mkdir(parents=True, exist_ok=True)
    with (path / 'probe.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not enabled(root, spec):
            return _read(root, spec, now)
        try:
            previous = json.loads((path / 'state.json').read_text())
            if previous['config'] != fingerprint(spec) or not 0 <= now - previous['observed'] <= gaming.MAX_AGE:
                previous = {}
        except (OSError, ValueError, TypeError, KeyError):
            previous = {}
        data = dict(config=fingerprint(spec), observed=now, since=0, state='unknown')
        try:
            signal = READERS[spec.kind](spec, proc, previous, now)
            data.update(signal.data)
            if signal.active is None:
                data['error'] = signal.error or 'signal unavailable'
            else:
                was_held = previous.get('state') in {'active', 'ending'}
                active = signal.active or (was_held and spec.lift_rule == 'absent' and signal.present)
                if active:
                    pending = previous.get('pending_since', now)
                    data['pending_since'] = pending
                    if was_held or now - pending >= spec.enter_delay:
                        data.update(state='active', since=previous['since'] if was_held else now)
                    else:
                        data['state'] = 'clear'
                elif was_held:
                    absent = previous.get('absent_since', now)
                    data.update(absent_since=absent, since=previous['since'])
                    data['state'] = ('ending' if now < previous['since'] + spec.grace or
                                     now - absent < spec.lift_delay else 'clear')
                    if data['state'] == 'clear':
                        data['since'] = 0
                else:
                    data['state'] = 'clear'
        except Exception as exc:
            # Credentials, URLs and response bodies must never reach state or logs.
            data['error'] = f'{spec.kind} observation failed ({type(exc).__name__})'
        write_json_atomic(path / 'state.json', data)
        return _read(root, spec, now)


def _collect(root, *, probing=False, proc=Path('/proc'), now=None):
    from .config import load, ConfigError
    now = time.time() if now is None else now
    try:
        policy = load(root).quiet_time
    except ConfigError:
        # Invalid new config cannot hide a manual gaming hold.
        return Status((('gaming', gaming.read(root, now=now)),
                       ('config', Observation('unknown', error='invalid quiet-time configuration'))))
    entries = []
    for spec in policy.detectors:
        if spec.kind == 'steam':
            # Preserve the old API and status for unconfigured installations.
            kwargs = {} if spec == Policy().detectors[0] else {'spec': spec}
            value = (gaming.probe(root, proc=proc, now=now, **kwargs) if probing else
                     gaming.read(root, now=now, **kwargs))
        else:
            value = _probe(root, spec, proc, now) if probing else _read(root, spec, now)
        entries.append((spec.name, value))
    return entries[0][1] if len(entries) == 1 else Status(tuple(entries))


def read(root, *, now=None):
    return _collect(root, now=now)


def probe(root, *, proc=Path('/proc'), now=None):
    return _collect(root, probing=True, proc=proc, now=now)


def manual(root, name, *, clear=False):
    path = folder(root, name)
    path.mkdir(parents=True, exist_ok=True)
    if clear:
        (path / 'manual').unlink(missing_ok=True)
    else:
        (path / 'manual').touch()


def metrics(status):
    entries = status.entries if isinstance(status, Status) else (('gaming', status),)
    text = gaming.metrics(dict(entries).get('gaming', gaming.Status()))
    text += '# TYPE aegis_quiet_time_hold_active gauge\n'
    text += f'aegis_quiet_time_hold_active {int(status.held)}\n'
    for name, value in entries:
        text += f'aegis_quiet_time_detector_active{{reason="{name}"}} {int(value.held)}\n'
        text += f'aegis_quiet_time_probe_ok{{reason="{name}"}} {int(value.state not in {"unknown", "off"})}\n'
        text += f'aegis_quiet_time_probe_timestamp_seconds{{reason="{name}"}} {value.observed}\n'
    return text
