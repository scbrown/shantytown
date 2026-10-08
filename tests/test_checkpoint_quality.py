import argparse
import json
import shlex
import sys
import time
from types import SimpleNamespace

import pytest

from shantytown import checkpoint_quality as q, cli, cycle


class Judge:
    def __init__(self, probabilities):
        self.probabilities = iter(probabilities)
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        return dict(answer=dict(noul=next(self.probabilities)),
                    model='test-model', usage=dict(input_tokens=12, output_tokens=1), request=args)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_complete_control_only_asks_one_bounded_question(monkeypatch):
    client = Judge([.95])
    monkeypatch.setattr(q, 'JevMCP', lambda *a, **kw: client)
    result = q.evaluate(dict(checkpoint='complete handoff', command='test'))
    assert result['outcome'] == 'complete' and len(client.calls) == 1
    assert client.calls[0][1]['state'] == 'complete handoff'
    assert 'If any is missing, answer no' in client.calls[0][1]['instructions']


def test_thin_handoff_names_missing_piece_and_retains_verdicts(monkeypatch):
    client = Judge([.02, .1, .9, .9])
    monkeypatch.setattr(q, 'JevMCP', lambda *a, **kw: client)
    result = q.evaluate(dict(checkpoint='landed X; local Y; decision Z', command='test'))
    assert result['outcome'] == 'thin' and result['missing'] == ['exact next step']
    assert all(args['state'] == 'landed X; local Y; decision Z' for _, args in client.calls)
    assert result['overall']['model'] == 'test-model'
    assert len(result['checks']) == 3


def thin(*args):
    return dict(outcome='thin', missing=['exact next step'])


def test_one_rewrite_credit_belongs_to_launch_not_checkpoint(tmp_path):
    def run(text='thin', session='launch-1'):
        return q.review(tmp_path, 'agent', text, session, 'test', 100, 400, evaluator=thin)
    assert run()[0] is False
    assert run()[0] is True
    assert run('different text')[0] is True
    assert run(session='launch-2')[0] is False
    records = [json.loads(line) for line in (tmp_path / 'checkpoint-quality.jsonl').read_text().splitlines()]
    assert [r['rewrite_offered'] for r in records] == [True, False, False, True]
    assert all(r['sourceKind'] == 'inferred' and r['checkpoint_sha256'] for r in records)


@pytest.mark.parametrize('depth,session', [(400, 's'), (900, 's'), (None, 's'), (100, '')])
def test_saturated_or_unmeasured_agent_is_never_held(tmp_path, depth, session):
    ok, message = q.review(tmp_path, 'agent', 'thin', session, 'test', depth, 400, evaluator=thin)
    assert ok and 'Cycling proceeds' in message


def test_logging_failure_cannot_veto_cycle(tmp_path):
    root = tmp_path / 'file'; root.write_text('not a directory')
    ok, message = q.review(root, 'agent', 'thin', 's', 'test', 10, 400, evaluator=thin)
    assert ok and 'telemetry unavailable' in message


def test_bad_retry_ledger_proceeds(tmp_path):
    p = tmp_path / 'notify/checkpoint-quality/agent.json'; p.parent.mkdir(parents=True)
    p.write_text('invalid')
    assert q.review(tmp_path, 'agent', 'thin', 's', 'test', 10, 400, evaluator=thin)[0]


@pytest.mark.parametrize('text,command', [('', 'test'), ('x' * (q.MAX_CHARS + 1), 'test'), ('text', None)])
def test_missing_or_oversized_input_never_starts_worker(monkeypatch, text, command):
    monkeypatch.setattr(q.subprocess, 'Popen', lambda *a, **kw: pytest.fail('worker started'))
    assert q.judge(text, command)['outcome'] == 'unknown'


def test_hung_server_is_bounded_and_descendants_are_killed(tmp_path, monkeypatch):
    script = tmp_path / 'hang.py'
    child_pid = tmp_path / 'child.pid'
    script.write_text("import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\nPath(" + repr(str(child_pid)) + ").write_text(str(p.pid))\ntime.sleep(60)\n")
    monkeypatch.setattr(q, 'BUDGET_SECONDS', .3)
    start = time.monotonic()
    result = q.judge('checkpoint', shlex.join([sys.executable, str(script)]))
    assert result['outcome'] == 'unknown' and 'budget exhausted' in result['reason']
    assert time.monotonic() - start < 2
    assert child_pid.exists(), 'positive control: server must have launched its child'
    pid = int(child_pid.read_text())
    from pathlib import Path
    proc = Path(f'/proc/{pid}/stat')
    deadline = time.monotonic() + 1
    while proc.exists() and proc.read_text().split()[2] != 'Z' and time.monotonic() < deadline:
        time.sleep(.01)
    assert not proc.exists() or proc.read_text().split()[2] == 'Z', 'descendant survived timeout' 


def test_malformed_server_verdict_is_unknown(tmp_path):
    script = tmp_path / 'bad.py'
    script.write_text('''import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 result={} if m['method']=='initialize' else {'structuredContent':{'answer':{'noul':2}}}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
''')
    assert q.judge('checkpoint', shlex.join([sys.executable, str(script)]))['outcome'] == 'unknown'


def test_latest_own_durable_text_is_selected_not_other_agent_or_stale():
    comments = [dict(author='a', created_at='2026-10-08T01:00:00Z', text='old'),
                dict(author='b', created_at='2026-10-08T04:00:00Z', text='not ours'),
                dict(author='a', created_at='2026-10-08T02:00:00Z', text='latest'),
                dict(author='a', created_at='2026-10-07T23:00:00Z', text='stale')]
    gate = cycle.durable_gate('a', 'bead', '2026-10-08T00:00:00Z', comments)
    assert gate.ok and gate.checkpoint == 'latest'


def test_self_cycle_does_not_record_request_until_quality_allows(tmp_path, monkeypatch):
    from tests.test_self_cycle_performs import _args
    card = SimpleNamespace(role='worker', name='hammond', pane='p')
    monkeypatch.setattr(cli, '_registry', lambda a: SimpleNamespace(get=lambda n: card))
    monkeypatch.setattr(cli, '_cycle_stop_refusal', lambda *a: '')
    monkeypatch.setattr(cli, '_graph_context', lambda a: (SimpleNamespace(nodes=['n'], exemption=''), ''))
    monkeypatch.setattr(cli.graph_adoption, 'record', lambda *a, **kw: None)
    monkeypatch.setattr(cli, '_spawn_self_cycle_server', lambda *a: '')
    monkeypatch.setattr(cli, '_durable_checkpoint_gate', lambda *a: cycle.DurableGate('hammond', ok=True, checkpoint='actual readback', since='launch'))
    seen = []
    def quality(a, who, text, session):
        seen.append((text, session))
        return len(seen) > 1, 'rewrite once'
    monkeypatch.setattr(cli, '_checkpoint_quality_gate', quality)
    assert cli._cmd_cycle(_args(tmp_path, reason='reason-only')) == cli.REFUSED
    assert cycle.Requests(tmp_path).pending() == {}
    assert cli._cmd_cycle(_args(tmp_path, reason='reason-only')) == cli.OK
    assert seen == [('actual readback', 'launch')] * 2


def test_busy_rewrite_lock_proceeds_without_waiting(tmp_path):
    import fcntl
    lock = tmp_path / 'notify/checkpoint-quality/agent.lock'
    lock.parent.mkdir(parents=True)
    with lock.open('w') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert q.review(tmp_path, 'agent', 'thin', 's', 'test', 100, 400, evaluator=thin)[0]
    # The competing request did not spend the rewrite credit.
    assert not q.review(tmp_path, 'agent', 'thin', 's', 'test', 100, 400, evaluator=thin)[0]


@pytest.mark.parametrize('allow_loss,expected', [(False, 450), (True, None)])
def test_cli_uses_measured_pane_depth_and_existing_jev_command(tmp_path, monkeypatch, allow_loss, expected):
    seen = []
    monkeypatch.setattr(cli, '_deployment_default', lambda a, k: 'test-command' if k == 'SHANTY_CREATE_JEV_COMMAND' else None)
    monkeypatch.setattr(cli, '_registry', lambda a: SimpleNamespace(get=lambda n: SimpleNamespace(pane='p')))
    monkeypatch.setattr(cli, '_panes', lambda a: SimpleNamespace(capture=lambda p: 'screen'))
    monkeypatch.setattr(cli.triage_mod, 'context_tokens_k', lambda text: 450)
    monkeypatch.setattr(q, 'review', lambda *args: seen.append(args) or (True, ''))
    a = argparse.Namespace(root=tmp_path, allow_loss=allow_loss)
    assert cli._checkpoint_quality_gate(a, 'agent', 'checkpoint only', 'launch') == (True, '')
    assert seen[0][2:6] == ('checkpoint only', 'launch', 'test-command', expected)
