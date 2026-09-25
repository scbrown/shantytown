import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from shantytown import create_advisory as a


@pytest.fixture
def agent(monkeypatch, tmp_path):
    monkeypatch.setenv('SHANTY_AGENT', 'tester')
    log = tmp_path / 'events.jsonl'
    monkeypatch.setenv('SHANTY_CREATE_ADVISORY_LOG', str(log))
    return log


def payload(command):
    return dict(session_id='test', cwd='/tmp', tool_input=dict(command=command))


@pytest.mark.parametrize('command,title,description', [
    ("br create 'fix connection leak'", 'fix connection leak', ''),
    ("br --db /tmp/test.db create 'same work' -d 'more detail' -p 2", 'same work', 'more detail'),
    ("/bin/br create --title='same work' --description=detail --json", 'same work', 'detail'),
    ("env A=foo command br-aegis create 'same work'", 'same work', ''),
])
def test_literal_create(agent, command, title, description):
    request = a.hook_request(payload(command))
    assert request['title'] == title
    assert request.get('description', '') == description
    assert not request.get('skip')


@pytest.mark.parametrize('command', [
    "echo 'br create foo'", "br show item", "printf '%s' 'br create foo'",
    "cat > /tmp/body <<'EOF'\nbr create foo\nEOF",
    "python3 producer.py", "bash producer.sh",
])
def test_prose_and_scripts_are_not_creates(agent, command):
    assert a.hook_request(payload(command)) is None


@pytest.mark.parametrize('command', [
    "cd /tmp && br create 'foo'", "br create \"$(cat file)\"",
    "br create --file /tmp/body", "br create foo --unknown", "br create",
    "br --config other create foo", "br create foo; br create bar",
])
def test_uncertain_create_is_loud_skip(agent, command):
    assert a.hook(payload(command)).startswith('advisory skipped:')
    assert json.loads(agent.read_text())['outcome'] == 'skipped'


def test_non_agent_is_untouched(monkeypatch, tmp_path):
    monkeypatch.delenv('SHANTY_AGENT', raising=False)
    monkeypatch.setenv('SHANTY_CREATE_ADVISORY_LOG', str(tmp_path / 'no'))
    assert a.advise(dict(title='foo')) == ''
    assert a.hook(payload('br create foo')) == ''
    assert not (tmp_path / 'no').exists()


def test_non_session_is_untouched(agent):
    p = payload('br create foo'); p.pop('session_id')
    assert a.hook(p) == ''
    assert not agent.exists()


def test_unconfigured_is_skip_not_no_match(agent, monkeypatch):
    monkeypatch.delenv('SHANTY_CREATE_JEV_COMMAND', raising=False)
    started = time.monotonic()
    assert a.advise(dict(title='private text', source='test')).startswith('advisory skipped:')
    assert time.monotonic() - started < 2
    row = json.loads(agent.read_text())
    assert row['outcome'] == 'skipped'
    assert 'private text' not in agent.read_text()
    assert row['latency_ms'] < 2000


def test_hung_worker_and_descendant_are_killed(agent, monkeypatch, tmp_path):
    original = subprocess.Popen
    pidfile = tmp_path / 'pid'
    def hung(*args, **kwargs):
        code = "import subprocess,time; p=subprocess.Popen(['sleep','30']); open(%r,'w').write(str(p.pid)); time.sleep(30)" % str(pidfile)
        return original([sys.executable, '-c', code], **kwargs)
    monkeypatch.setattr(a.subprocess, 'Popen', hung)
    started = time.monotonic()
    assert 'time budget' in a.advise(dict(title='foo'))
    assert time.monotonic() - started < 2
    # A killed child may be a zombie until its subreaper reaps it; it cannot run.
    stat = Path('/proc') / pidfile.read_text() / 'stat'
    if stat.exists():
        assert stat.read_text().split()[2] == 'Z'


def test_hook_preserves_previous_permission_and_context(agent, monkeypatch):
    monkeypatch.delenv('SHANTY_CREATE_JEV_COMMAND', raising=False)
    prior = dict(hookSpecificOutput=dict(hookEventName='PreToolUse',
        permissionDecision='allow', additionalContext='existing advisory'))
    env = dict(os.environ, SHANTY_ADVISORY_PRIOR=json.dumps(prior))
    r = subprocess.run([sys.executable, '-m', 'shantytown.create_advisory', '--envelope'],
        input=json.dumps(payload('br create foo')), text=True, capture_output=True, env=env)
    assert r.returncode == 0
    out = json.loads(r.stdout)['hookSpecificOutput']
    assert out['permissionDecision'] == 'allow'
    assert out['additionalContext'].startswith('existing advisory\nadvisory skipped:')


def test_task_creates_even_if_advisory_crashes(agent, monkeypatch, tmp_path, capsys):
    from shantytown.cli import main
    def crash(_):
        raise RuntimeError('do not block')
    monkeypatch.setattr(a, 'advise', crash)
    assert main(['--root', str(tmp_path / 'root'), '--backend', 'files', 'task', 'test']) == 0
    assert len(list((tmp_path / 'root/items').glob('*.json'))) == 1
    assert 'advisory skipped:' in capsys.readouterr().err


def test_dry_run_does_not_advise(agent, monkeypatch, tmp_path):
    from shantytown.cli import main
    monkeypatch.setattr(a, 'advise', lambda _: pytest.fail('dry-run called advisory'))
    assert main(['--root', str(tmp_path / 'root'), 'task', '--dry-run', 'test']) == 0
    assert not agent.exists()


def test_no_match_is_silent_and_logged(agent, monkeypatch):
    original = subprocess.Popen
    def worker(*args, **kwargs):
        return original([sys.executable, '-c', 'print(\'{"outcome":"no-match","candidates":[]}\')'], **kwargs)
    monkeypatch.setattr(a.subprocess, 'Popen', worker)
    assert a.advise(dict(title='foo')) == ''
    assert json.loads(agent.read_text())['outcome'] == 'no-match'


def test_fired_warns_and_logs(agent, monkeypatch):
    original = subprocess.Popen
    result = dict(outcome='fired', candidates=[dict(id='test-42', probability=.95)])
    def worker(*args, **kwargs):
        return original([sys.executable, '-c', 'print(%r)' % json.dumps(result)], **kwargs)
    monkeypatch.setattr(a.subprocess, 'Popen', worker)
    assert 'test-42' in a.advise(dict(title='foo'))
    assert json.loads(agent.read_text())['outcome'] == 'fired'


@pytest.fixture
def services(monkeypatch):
    import io
    from types import SimpleNamespace
    monkeypatch.setenv('BOBBIN_SERVER', 'http://example.invalid')
    monkeypatch.setenv('SHANTY_CREATE_JEV_COMMAND', 'fake-jev')
    state = dict(search=dict(count=1, results=[dict(bead_id='test-42')]),
                 bead=dict(id='test-42', title='fix leak', description='pool connections', status='open'),
                 probability=.95, calls=[])
    def search(url, timeout):
        assert 'status=open' in url and 'enrich=false' in url
        return io.StringIO(json.dumps(state['search']))
    def store(args, **kwargs):
        assert '--no-auto-import' in args and '--no-auto-flush' in args
        assert args[-3:] == ['show', 'test-42', '--json']
        state['store_args'] = args
        return SimpleNamespace(returncode=state.get('rc', 0), stdout=json.dumps(state['bead']))
    class Jev:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call(self, tool, arguments):
            state['calls'].append((tool, arguments))
            return dict(request=arguments, model='test', usage={}, answer=dict(noul=state['probability']))
    monkeypatch.setattr(a.urllib.request, 'urlopen', search)
    monkeypatch.setattr(a.subprocess, 'run', store)
    monkeypatch.setattr(a, 'JevMCP', Jev)
    return state


def test_live_candidate_and_typed_judgment(services):
    out = a.evaluate(dict(title='repair leak', store_args=['--db', '/tmp/test.db']))
    assert out['outcome'] == 'fired'
    assert services['store_args'][1:3] == ['--db', '/tmp/test.db']
    tool, args = services['calls'][0]
    assert tool == 'jev_noul'
    assert json.loads(args['state'])['second']['description'] == 'pool connections'
    assert out['candidates'][0]['model'] == 'test'


def test_closed_candidate_never_warns(services):
    services['bead']['status'] = 'closed'
    assert a.evaluate(dict(title='fix leak'))['outcome'] == 'no-match'
    assert services['calls'] == []


@pytest.mark.parametrize('fault', ['error-envelope', 'bad-count', 'unsafe-id', 'store-error', 'wrong-id', 'nan'])
def test_errors_cannot_become_no_match(services, fault):
    if fault == 'error-envelope': services['search'] = {'error': 'down'}
    if fault == 'bad-count': services['search']['count'] = 2
    if fault == 'unsafe-id': services['search']['results'][0]['bead_id'] = '--help'
    if fault == 'store-error': services['rc'] = 1
    if fault == 'wrong-id': services['bead']['id'] = 'other-42'
    if fault == 'nan': services['probability'] = float('nan')
    with pytest.raises(a.TriageError):
        a.evaluate(dict(title='fix leak'))


def test_telemetry_failure_is_visible(services, agent, monkeypatch):
    monkeypatch.setenv('SHANTY_CREATE_ADVISORY_LOG', str(agent.parent))
    assert a.record(dict(outcome='no-match'), dict(title='secret'), time.monotonic()) == 'advisory skipped: telemetry unavailable'


def test_precision_does_not_count_missing_labels_as_correct(tmp_path):
    log=tmp_path/'events'
    log.write_text('\n'.join(json.dumps(dict(event_id=str(i),outcome=o,latency_ms=100))
                             for i,o in enumerate(['fired','fired','fired','skipped','no-match'])))
    assert a.precision_report(log)['precision'] is None
    labels=tmp_path/'labels'
    labels.write_text('\n'.join(json.dumps(dict(event_id=i,duplicate=d)) for i,d in [('0',True),('1',False)]))
    out=a.precision_report(log,labels)
    assert out['precision']==.5 and out['unlabelled']==1 and out['skipped']==1
    labels.write_text(json.dumps(dict(event_id='3',duplicate=True)))
    with pytest.raises(ValueError): a.precision_report(log,labels)


def test_task_uses_resolved_tracker_store(agent, monkeypatch, tmp_path):
    from shantytown import cli
    from shantytown.br import BrTracker
    from shantytown.protocols import WorkItem
    tracker=BrTracker(repo=str(tmp_path/'configured-store'))
    monkeypatch.setattr(cli, '_tracker', lambda _: tracker)
    monkeypatch.setattr(tracker, 'create', lambda title, **kw: WorkItem(id='test-1',title=title))
    seen=[]
    monkeypatch.setattr(a, 'advise', lambda req: seen.append(req) or '')
    assert cli.main(['--root',str(tmp_path/'root'),'task','test']) == 0
    assert seen[0]['cwd'] == str(tmp_path/'configured-store')
    assert seen[0]['skip'] is None
