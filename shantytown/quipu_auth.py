"""Credentials and a private session refusal latch; public reads stay available."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import urllib.parse

HELP = ('Configure QUIPU_AUTH_TOKEN > QUIPU_AUTH_TOKEN_FILE > ~/.config/quipu/token; '
        'ask the Quipu administrator for an accepted credential and run caboodle doctor.')
_disabled: set[str] = set()


class CredentialRefused(Exception):
    """No request was sent, or the server definitively refused authentication."""


def token() -> str:
    value = os.environ.get('QUIPU_AUTH_TOKEN', '').strip()
    if not value:
        path = os.environ.get('QUIPU_AUTH_TOKEN_FILE') or str(Path.home() / '.config/quipu/token')
        try:
            value = Path(path).read_text(encoding='utf-8').strip()
        except FileNotFoundError:
            return ''
    if '\n' in value or '\r' in value:
        raise ValueError('invalid credential format')
    return value


def _identity(server: str) -> tuple[str, Path | None]:
    parsed = urllib.parse.urlsplit(server)
    endpoint = urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                                      parsed.path.rstrip('/'), '', ''))
    session = next((os.environ[k] for k in ('QUIPU_SESSION', 'CODEX_SESSION_ID',
                    'CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID') if os.environ.get(k)), None)
    key = hashlib.sha256(f'{endpoint}\0{session or "process"}'.encode()).hexdigest()
    root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return key, root / 'shantytown/quipu-auth' / (key + '.disabled') if session else None


def status(server: str) -> str:
    key, marker = _identity(server)
    return 'writes-disabled' if key in _disabled or (marker and marker.exists()) else 'enabled'


def refuse(server: str, reason: str) -> CredentialRefused:
    key, marker = _identity(server)
    first = key not in _disabled
    _disabled.add(key)
    scope = 'process only (no harness session ID)'
    if first and marker:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            marker.parent.chmod(0o700)
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(reason)
            scope = 'harness session'
        except FileExistsError:
            first = False
        except OSError:
            scope = 'process only (session latch unavailable)'
    if first:
        print(f'st: Quipu credential {reason}; {HELP} Writes disabled for this {scope}.', file=sys.stderr)
    return CredentialRefused(f'Quipu writes disabled: {reason}; {HELP}')


def write_headers(server: str) -> dict[str, str]:
    if status(server) == 'writes-disabled':
        raise CredentialRefused('Quipu writes disabled for this session; repair credential and start a new session.')
    try:
        value = token()
    except (OSError, UnicodeError, ValueError):
        raise refuse(server, 'unreadable or invalid') from None
    if not value:
        raise refuse(server, 'missing')
    return {'Content-Type': 'application/json', 'Authorization': f'Bearer {value}'}
