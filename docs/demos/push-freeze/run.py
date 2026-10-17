"""Private bare-Git reproducer; no network or production writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from shantytown import workspace

BASE = '22c563e64d2026389df15c043f4f55943bca91af'


def git(where, *args):
    return subprocess.check_output(['git', '-C', str(where), *args], text=True).strip()


def measure(push):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repo = root / 'work'
        repo.mkdir()
        git(repo, 'init', '-q')
        git(repo, 'config', 'user.name', 'fixture')
        git(repo, 'config', 'user.email', 'fixture@example.invalid')
        (repo / 'f').write_text('private fixture')
        git(repo, 'add', 'f')
        git(repo, 'commit', '-qm', 'fixture')
        for name in ('forge', 'origin'):
            git(root, 'init', '-q', '--bare', str(root / name))
            git(repo, 'remote', 'add', name, str(root / name))
            git(repo, 'config', f'remote.{name}.st-push-allowed', 'true')
        git(repo, 'config', 'st.push-freeze.local', 'private maintenance window')
        outcomes = push(repo, 'HEAD', 'feature')
        held = {name: bool(git(root / name, 'for-each-ref', '--format=%(refname)',
                               'refs/heads/feature')) for name in ('forge', 'origin')}
        print(json.dumps({'phase': 'frozen', 'refs_advanced': held,
                          'refused': any(not o.ok for o in outcomes)}))
        git(repo, 'config', '--unset-all', 'st.push-freeze.local')
        outcomes = push(repo, 'HEAD', 'feature')
        assert all(o.ok for o in outcomes)
        assert all(git(root / name, 'rev-parse', 'feature') == git(repo, 'rev-parse', 'HEAD')
                   for name in ('forge', 'origin'))
        print('unfrozen positive control: BOTH refs match HEAD')


with tempfile.TemporaryDirectory() as directory:
    source = subprocess.check_output(['git', 'show', f'{BASE}:shantytown/workspace.py'])
    path = Path(directory) / 'base.py'
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location('shantytown._demo_base', path)
    base = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = base
    spec.loader.exec_module(base)
    print('BASE', BASE)
    measure(base.push_every_remote)
    print('CANDIDATE workspace.py SHA256', hashlib.sha256(Path(workspace.__file__).read_bytes()).hexdigest())
    measure(workspace.push_every_remote)
