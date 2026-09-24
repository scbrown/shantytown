"""Guard probe must refuse absent/invalid TOML, without launching Codex."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('toml', ['[env]\nSHANTY_BASH_GUARD = "/missing-probe-guard"\n', '[broken', ''])
def test_probe_does_not_select_legacy_or_conflicting_guard(tmp_path, toml):
    root = tmp_path / 'store'
    root.mkdir()
    if toml:
        (root / 'shantytown.toml').write_text(toml)
    (root / 'env.json').write_text('{"SHANTY_BASH_GUARD":"/bin/false"}')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    marker = tmp_path / 'codex-ran'
    codex = bindir / 'codex'
    codex.write_text('#!/bin/sh\ntouch "$PROBE_MARKER"\nexit 99\n')
    codex.chmod(0o755)
    env = dict(os.environ, HOME=str(tmp_path), SHANTY_ROOT=str(root),
               PATH=str(bindir) + ':' + os.environ['PATH'], PROBE_MARKER=str(marker))
    env.pop('SHANTY_BASH_GUARD', None)
    if toml:
        env['SHANTY_BASH_GUARD'] = '/bin/false'
    script = Path(__file__).resolve().parents[1] / 'scripts/probe-codex-bash-guard.sh'
    result = subprocess.run(['bash', str(script)], env=env, cwd=tmp_path,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not marker.exists()
    assert 'could not resolve deployment guard' in result.stdout if toml == '[broken' else 'no executable SHANTY_BASH_GUARD' in result.stdout
