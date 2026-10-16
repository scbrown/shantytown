"""Actual CLI, isolated Git peers; BASE_SOURCE and CANDIDATE_SOURCE are checkouts."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def git(path, *args):
    return subprocess.check_output(
        ['git', '-C', str(path), *args], text=True, stderr=subprocess.PIPE
    ).strip()


with tempfile.TemporaryDirectory(prefix='st-push-remedy-', dir='/var/tmp') as scratch:
    root = Path(scratch)
    repo = root / 'project'
    repo.mkdir()
    crew = root / 'crew'
    crew.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'user.name', 'fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'file').write_text('base\n')
    git(repo, 'add', 'file')
    git(repo, 'commit', '-qm', 'base')
    peers = []
    for name in ('origin', 'peer'):
        peer = root / (name + '.git')
        git(root, 'init', '-q', '--bare', '-b', 'main', str(peer))
        git(repo, 'remote', 'add', name, str(peer))
        git(repo, 'config', 'remote.' + name + '.st-push-allowed', 'true')
        git(repo, 'push', '-q', name, 'main')
        peers.append(peer)
    selected = root / 'selected worktree'
    git(repo, 'worktree', 'add', '-qb', 'worker/topic', str(selected))
    (selected / 'file').write_text('topic\n')
    git(selected, 'add', 'file')
    git(selected, 'commit', '-qm', 'topic')
    base = git(repo, 'rev-parse', 'main')
    command = ['--root', str(crew), 'repo', 'push', str(selected), 'worker',
               '--source-branch', 'worker/topic', '--branch', 'worker/topic']
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('SHANTY_', 'ST_')) and k != 'QUIPU_SERVER'}
    env.update(SHANTY_ENTITY_LINK='off', GIT_ALLOW_PROTOCOL='file')
    for label, source, cwd, expected in (
        ('BEFORE: explicit source from primary main', os.environ['BASE_SOURCE'], repo, 1),
        ('AFTER: same refusal, actionable remedy', os.environ['CANDIDATE_SOURCE'], repo, 1),
        ('CONTROL: candidate run from selected checkout', os.environ['CANDIDATE_SOURCE'], selected, 0),
    ):
        print('\n' + label, flush=True)
        code = ('import sys; sys.path.insert(0,sys.argv.pop(1)); '
                'from shantytown.cli import main; raise SystemExit(main(sys.argv[1:]))')
        result = subprocess.run([sys.executable, '-I', '-c', code, source, *command],
                                cwd=cwd, env=env)
        print('exit:', result.returncode, '(expected', expected, ')', flush=True)
        assert result.returncode == expected
    assert all(git(p, 'rev-parse', 'main') == base for p in peers)
    assert all(git(p, 'rev-parse', 'worker/topic') == git(selected, 'rev-parse', 'HEAD')
               for p in peers)
    print('\nBoth local peers received only worker/topic; main refs unchanged.', flush=True)
