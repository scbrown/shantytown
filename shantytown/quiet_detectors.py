"""Read-only, config-selected quiet-time signals. No shell or player control."""
from __future__ import annotations

import configparser
import json
import math
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import gaming_activity


@dataclass(frozen=True)
class Detector:
    name: str
    kind: str = 'process'
    enabled: bool | None = True
    enter_delay: float = 0
    grace: float = 0
    lift_delay: float = 120
    lift_rule: str = 'inactive'
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Policy:
    detectors: tuple[Detector, ...] = (Detector('gaming', 'steam', None, grace=300, lift_rule='absent'),)


COMMON = {'name', 'kind', 'enabled', 'enter_delay', 'grace', 'lift_delay', 'lift_rule'}
OPTIONS = {
    'steam': {'game_executable', 'game_arguments', 'shader_executable', 'shader_grace'},
    'process': {'executable', 'arguments', 'min_cpu_percent', 'min_gpu_percent', 'activity_rule'},
    'http_json': {'url', 'items_path', 'count_path', 'match', 'state_path', 'active_values',
                  'auth_header', 'token_file', 'token_ini_section', 'token_ini_key', 'timeout'},
}


def parse(raw: dict) -> Policy:
    if set(raw) - {'detector'}:
        raise ValueError('quiet_time: expected only detector tables')
    rows = raw.get('detector', [])
    if not isinstance(rows, list) or len(rows) > 16:
        raise ValueError('quiet_time.detector must be an array of at most 16 tables')
    found = {'gaming': Policy().detectors[0]}
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('quiet_time.detector must contain tables')
        name, kind = row.get('name'), row.get('kind', 'process')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', name) or name == 'all':
            raise ValueError('quiet_time detector name must be a short lowercase identifier, not all')
        if name in seen:
            raise ValueError(f'quiet_time duplicate detector {name}')
        seen.add(name)
        if not isinstance(kind, str) or kind not in OPTIONS or (name == 'gaming') != (kind == 'steam'):
            raise ValueError(f'quiet_time.{name}: gaming uses steam; other names use process or http_json')
        unknown = set(row) - COMMON - OPTIONS[kind]
        if unknown:
            raise ValueError(f'quiet_time.{name}: unknown keys {sorted(unknown)}')
        enabled = row.get('enabled', None if kind == 'steam' else True)
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError(f'quiet_time.{name}.enabled must be boolean')
        times = {k: row.get(k, default) for k, default in
                 [('enter_delay', 0), ('grace', 300 if kind == 'steam' else 0), ('lift_delay', 120)]}
        for key, value in times.items():
            number(value, f'{name}.{key}', 86400)
        lift = row.get('lift_rule', 'absent' if kind == 'steam' else 'inactive')
        if not isinstance(lift, str) or lift not in {'absent', 'inactive'}:
            raise ValueError(f'quiet_time.{name}.lift_rule must be absent or inactive')
        if kind == 'steam' and (lift != 'absent' or times['enter_delay']):
            raise ValueError('Steam presence is authoritative: absent lift_rule and zero enter_delay required')
        options = {k: v for k, v in row.items() if k in OPTIONS[kind]}
        if kind in {'process', 'steam'}:
            patterns = (options.get('arguments', []) if kind == 'process' else
                        options.get('game_arguments', ['SteamLaunch', r'AppId=(?P<appid>[0-9]+)']))
            if not isinstance(patterns, list) or not all(isinstance(v, str) for v in patterns):
                raise ValueError(f'quiet_time.{name}: argument patterns must be strings')
            executable = options.get('executable' if kind == 'process' else 'game_executable',
                                     '' if kind == 'process' else 'reaper')
            if not isinstance(executable, str) or not executable:
                raise ValueError(f'quiet_time.{name}: executable regex required')
            try:
                for pattern in [executable, *patterns, options.get('shader_executable', 'fossilize_replay')]:
                    re.compile(pattern)
            except (re.error, TypeError) as exc:
                raise ValueError(f'quiet_time.{name}: invalid regex') from exc
            if kind == 'steam':
                if not any('appid' in re.compile(p).groupindex for p in patterns):
                    raise ValueError('Steam argument patterns need a named appid capture')
                number(options.get('shader_grace', 1200), name + '.shader_grace', 86400)
            else:
                for key in ('min_cpu_percent', 'min_gpu_percent'):
                    if key in options:
                        number(options[key], name + '.' + key, 100 if 'gpu' in key else 100000)
                if options.get('activity_rule', 'any') not in {'any', 'all'}:
                    raise ValueError('activity_rule must be any or all')
        else:
            for key in ('url', 'items_path', 'count_path', 'state_path'):
                if not isinstance(options.get(key), str) or not options[key]:
                    raise ValueError(f'quiet_time.{name}.{key} is required')
            if not options['url'].startswith(('http://', 'https://')):
                raise ValueError('http_json URL must use http or https')
            match = options.get('match')
            if not isinstance(match, dict) or not match or not all(
                    isinstance(k, str) and k and isinstance(v, str) and v for k, v in match.items()):
                raise ValueError('http_json match must select the intended client with nonempty string fields')
            values = options.get('active_values', ['playing', 'buffering'])
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
                raise ValueError('http_json active_values must be nonempty strings')
            for key in ('auth_header', 'token_file', 'token_ini_section', 'token_ini_key'):
                if key in options and (not isinstance(options[key], str) or not options[key]):
                    raise ValueError(f'http_json {key} must be a nonempty string')
            if bool(options.get('auth_header')) != bool(options.get('token_file')):
                raise ValueError('http_json auth_header and token_file must be supplied together')
            if bool(options.get('token_ini_section')) != bool(options.get('token_ini_key')):
                raise ValueError('http_json token_ini_section and token_ini_key must be supplied together')
            number(options.get('timeout', 5), name + '.timeout', 10, positive=True)
        found[name] = Detector(name, kind, enabled, **times, lift_rule=lift, options=options)
    if len(found) > 16:
        raise ValueError("quiet_time supports at most 16 detectors including gaming")
    return Policy(tuple(found.values()))


def number(value, key, ceiling, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or value > ceiling or (positive and value == 0)):
        raise ValueError(f'quiet_time.{key} must be a finite number in range')


@dataclass(frozen=True)
class Signal:
    active: bool | None
    present: bool = False
    data: dict = field(default_factory=dict)
    error: str = ''


def processes(proc: Path, executable: str, arguments=()) -> dict[int, dict]:
    found = {}
    for entry in Path(proc).iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            argv = (entry / 'cmdline').read_bytes().decode(errors='replace').split('\0')
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if not argv or not re.fullmatch(executable, Path(argv[0]).name):
            continue
        if len(argv) < len(arguments) + 1:
            continue
        matches = [re.fullmatch(pattern, value) for pattern, value in zip(arguments, argv[1:])]
        if all(matches):
            found[int(entry.name)] = {k: v for m in matches for k, v in m.groupdict().items()}
    return found


def process_signal(spec: Detector, proc: Path, previous: dict, now: float) -> Signal:
    options = spec.options
    roots = processes(proc, options['executable'], options.get('arguments', []))
    if not roots:
        return Signal(False)
    thresholds = [(key, field) for key, field in
                  [('min_cpu_percent', 'game_cpu'), ('min_gpu_percent', 'gpu_busy')] if key in options]
    if not thresholds:
        return Signal(True, True, {'pids': sorted(roots)})
    data = gaming_activity.observe(proc, set(roots), previous, now)
    checks = [None if data.get(field) is None else data[field] >= options[key] for key, field in thresholds]
    if options.get('activity_rule', 'any') == 'any':
        active = True if True in checks else None if None in checks else False
    else:
        active = False if False in checks else None if None in checks else True
    return Signal(active, True, data, 'activity sample unavailable' if active is None else '')


def at(data, path):
    for key in path.split('.'):
        data = data[key]
    return data


def json_signal(spec: Detector, proc: Path, previous: dict, now: float) -> Signal:
    options = spec.options
    headers = {'Accept': 'application/json'}
    if options.get('token_file'):
        token_path = Path(options['token_file']).expanduser()
        if options.get('token_ini_section'):
            ini = configparser.ConfigParser(interpolation=None)
            with token_path.open() as stream:
                ini.read_file(stream)
            token = ini[options['token_ini_section']][options['token_ini_key']].strip('"')
        else:
            token = token_path.read_text().strip()
        if not token or '\n' in token or '\r' in token:
            raise ValueError('credential unavailable')
        headers[options['auth_header']] = token
    request = urllib.request.Request(options['url'], headers=headers)
    # Redirects could forward a credential to another server. They are not probes.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(request, timeout=options.get('timeout', 5)) as response:
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError('session response exceeds 1 MiB')
        data = json.loads(body)
    count = at(data, options['count_path'])
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError('invalid session count')
    try:
        rows = at(data, options['items_path'])
    except KeyError:
        if count != 0:
            raise ValueError('session list missing despite nonzero count')
        rows = []
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError('session count/list disagree')
    matched = []
    for row in rows:
        if all(at(row, path) == value for path, value in options['match'].items()):
            state = at(row, options['state_path'])
            if not isinstance(state, str) or not state:
                raise ValueError('session state missing')
            matched.append(state)
    return Signal(any(s in options.get('active_values', ['playing', 'buffering']) for s in matched),
                  bool(matched), {'matching_sessions': len(matched)})


READERS = {'process': process_signal, 'http_json': json_signal}
