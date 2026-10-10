"""Launch-time credential projection. No credential values in cards or argv.

The operator provisions the reference; this module never creates an account or
logs in. Native authentication refreshes stay in an account-specific private
profile. An Infisical/environment reference bootstraps that profile once, so a
later launch does not overwrite a token the harness has refreshed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import shutil
import sys
import tempfile

from .accounts import AccountError, selected


def profile(root, account) -> Path:
    base = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state'))
    identity = hashlib.sha256((str(Path(root).resolve()) + '\0' + account.name
                               + '\0' + account.credential_ref).encode()).hexdigest()[:20]
    return base / 'shantytown' / 'account-profiles' / identity


def auth_name(account) -> str:
    return 'auth.json' if account.harness == 'codex' else '.credentials.json'


def available(root, account):
    if (profile(root, account) / auth_name(account)).is_file():
        return True
    kind, reference = account.credential_ref.split(':', 1)
    if kind == 'file':
        return Path(reference).is_file()
    if kind == 'env':
        return bool(os.environ.get(reference))
    return kind == 'infisical' and shutil.which('infisical') is not None


def _private_directory(path):
    if path.is_symlink():
        raise AccountError('account profile must not be a symlink')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    if hasattr(os, 'getuid') and path.stat().st_uid != os.getuid():
        raise AccountError('account profile has another owner')
    resolved = path.resolve()
    for parent in (resolved, *resolved.parents):
        if (parent / '.git').exists():
            raise AccountError('account credentials cannot be projected into a Git checkout')


@contextmanager
def _profile_lock(path):
    import fcntl
    descriptor = os.open(path / '.credential.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as handle:
        os.chmod(handle.name, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def initialize(root, account, *, run=subprocess.run, environ=None):
    """Resolve once, under a lock. Fail closed without printing resolver output."""
    env = os.environ if environ is None else environ
    home = profile(root, account)
    _private_directory(home)
    destination = home / auth_name(account)
    with _profile_lock(home):
        if destination.is_file():
            if destination.is_symlink():
                kind, reference = account.credential_ref.split(':', 1)
                if kind != 'file' or destination.resolve() != Path(reference).resolve():
                    raise AccountError('account credential projection is an unexpected symlink')
            return home
        kind, reference = account.credential_ref.split(':', 1)
        if kind == 'file':
            source = Path(reference)
            if not source.is_file():
                raise AccountError('account credential file is unavailable')
            # Preserve the operator's refresh authority; never copy into the store.
            if destination.is_symlink():
                destination.unlink()
            destination.symlink_to(source.resolve())
            return home
        if destination.is_symlink():
            raise AccountError('account credential projection is an unexpected symlink')
        if kind == 'env':
            payload = env.get(reference, '')
        elif kind == 'infisical':
            try:
                result = run(['infisical', 'secrets', 'get', reference, '--plain'],
                             capture_output=True, text=True, timeout=15, check=False)
            except (OSError, subprocess.SubprocessError):
                raise AccountError('account credential resolver is unavailable') from None
            if result.returncode != 0:
                raise AccountError('account credential resolver refused the reference')
            payload = result.stdout
        else:
            raise AccountError('unsupported account credential reference')
        # Native files are JSON objects, not API-key command-line arguments.
        if not payload or len(payload.encode()) > 1024 * 1024:
            raise AccountError('account credential payload is missing or too large')
        try:
            value = json.loads(payload)
        except (ValueError, TypeError):
            raise AccountError('account reference must contain native credential JSON') from None
        if not isinstance(value, dict) or not value:
            raise AccountError('account reference must contain native credential JSON')
        descriptor, temporary = tempfile.mkstemp(dir=home, prefix='.auth-')
        try:
            with os.fdopen(descriptor, 'w') as handle:
                json.dump(value, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return home


def settings_path(card, path, root, *, prepare=False):
    """A Codex settings home for THIS account; never rewrite role-shared auth."""
    if not path or root is None:
        return path
    from .config import load
    account = selected(card, load(root))
    if account is None or account.harness != 'codex':
        return path
    from .files import write_text_atomic
    if not card.name or Path(card.name).name != card.name or card.name in {'.', '..'}:
        raise AccountError('agent name is unsafe for an account profile')
    original = Path(path).resolve()
    home = Path(root) / 'settings' / 'accounts' / account.name / card.name
    dest = home / original.name
    if not prepare:
        return str(dest)
    home.mkdir(parents=True, exist_ok=True)
    text = original.read_text()
    if not dest.is_file() or dest.read_text() != text:
        write_text_atomic(dest, text)
        os.chmod(dest, stat.S_IMODE(original.stat().st_mode))
    # Credential links point outside the tracked deployment, while config and
    # managed packages continue to come from its selected settings artifact.
    for name in ('packages', 'plugins'):
        source = original.parent / name
        link = home / name
        if source.exists() and not link.exists():
            link.symlink_to(source, target_is_directory=True)
    link = home / 'auth.json'
    target = profile(root, account) / auth_name(account)
    if link.is_symlink() and link.readlink() == target:
        return str(dest)
    if link.exists() and not link.is_symlink():
        raise AccountError('account settings contain an unexpected credential file')
    if link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    return str(dest)


def wrap(card, launch, root):
    if root is None:
        return launch
    from .config import load
    account = selected(card, load(root))
    if account is None:
        return launch
    prepare = shlex.join([sys.executable, '-m', 'shantytown.account_auth',
                          '--root', str(Path(root).resolve()), '--account', account.name])
    # Ambient API keys or another account's config directory must never win over
    # the selected subscription. All values here are identity/path references.
    command = ['env']
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY',
                 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CONFIG_DIR', 'CODEX_HOME'):
        command += ['-u', name]
    command += ['SHANTY_ACCOUNT=' + account.name]
    if account.harness == 'claude':
        command += ['CLAUDE_CONFIG_DIR=' + str(profile(root, account))]
    command += ['/bin/sh', '-c', launch]
    return prepare + ' && ' + shlex.join(command)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Project a named account credential reference')
    parser.add_argument('--root', required=True)
    parser.add_argument('--account', required=True)
    args = parser.parse_args(argv)
    try:
        from .config import load
        account = load(args.root).accounts[args.account]
        initialize(args.root, account)
    except (KeyError, ValueError, OSError):
        # Resolver diagnostics and JSON parse exceptions may contain credentials.
        print('account authentication could not be established; nothing launched',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
