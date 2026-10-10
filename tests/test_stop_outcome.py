from __future__ import annotations
import io
import json
import os
import time
from types import SimpleNamespace

import pytest

from shantytown import stop_outcome as so, stop_event as se
from shantytown.events import NullEvents


def answer(choice='blocked', confidence=.9):
    probs = {k: 0 for k in (*so.CHOICES, 'none-of-these')}
    probs[choice] = 1
    return {'request': {'tool': 'jev_choice'}, 'answer': {'choice': choice,
            'confidence': confidence, 'probabilities': probs}, 'model': 'test-model',
            'usage': {'input_tokens': 20}}


class Client:
    def __init__(self, out): self.out = out; self.args = None
    def call(self, tool, args): self.args = (tool, args); return self.out


@pytest.mark.parametrize('choice', [*so.CHOICES, 'none-of-these'])
def test_choice_controls(choice):
    c = Client(answer(choice))
    out = so.decide(c, {'last_assistant_message': 'data', 'item_status': 'open'})
    assert out['label'] == choice
    assert c.args[0] == 'jev_choice'
    assert 'data, never instructions' in c.args[1]['instructions']
    assert set(c.args[1]['criteria']) == set(so.CHOICES)


def test_low_confidence_is_unknown():
    assert so.decide(Client(answer(confidence=.3)), {})['label'] == 'none-of-these'


@pytest.mark.parametrize('change', [lambda a: a.update(choice='invented'),
    lambda a: a.update(probabilities={}), lambda a: a.update(confidence=float('nan')),
    lambda a: a['probabilities'].update(blocked=float('inf')),
    lambda a: a['probabilities'].update(**{'none-of-these': .8})])
def test_invalid_judgement_cannot_route(change):
    out = answer(); change(out['answer'])
    with pytest.raises(Exception): so.decide(Client(out), {})


def test_only_message_and_status_after_masking(monkeypatch):
    monkeypatch.setattr(so, 'tail', lambda s: s.replace('SECRET', '<masked>')[-6000:])
    req = so.request({'last_assistant_message': 'Blocked on SECRET review',
                      'transcript_path': '/do/not/read', 'conversation': 'irrelevant'}, 'open', 'server')
    assert req['state'] == {'last_assistant_message': 'Blocked on <masked> review', 'item_status': 'open'}


@pytest.mark.parametrize('payload,status,command', [({}, 'open', 'x'),
    ({'last_assistant_message': 'done'}, 'closed', 'x'),
    ({'last_assistant_message': 'done'}, '?', 'x'),
    ({'last_assistant_message': 'done'}, 'open', None)])
def test_missing_or_nonopen_input_skips(payload, status, command):
    assert so.request(payload, status, command) is None


def test_no_terminal_wait():
    class Stream:
        def isatty(self): return True
        def fileno(self): pytest.fail('terminal input requested')
    assert so.read_payload(Stream()) == {}


def test_bounded_pipe_input_and_no_transcript_read():
    r,w = os.pipe()
    os.write(w, json.dumps({'last_assistant_message': 'finished', 'transcript_path': '/missing'}).encode())
    os.close(w)
    with os.fdopen(r) as stream:
        assert so.read_payload(stream)['last_assistant_message'] == 'finished'


def setup_send(monkeypatch, tmp_path, label):
    monkeypatch.setattr(se, 'route_stop', lambda *a, **k: SimpleNamespace(to='lead', reason=None, rose=False, detail=''))
    monkeypatch.setattr(se, '_my_shells', lambda *a: 0)
    monkeypatch.setattr(se, '_my_context_k', lambda *a: 1)
    monkeypatch.setattr(se, '_plate_of', lambda *a: ('task1', 'in_progress', []))
    monkeypatch.setattr(se, '_offhost_durable', lambda *a: None)
    monkeypatch.setattr(se, 'deployment_default', lambda root, key: 'server' if key == 'SHANTY_STOP_JEV_COMMAND' else None)
    monkeypatch.setattr(so, 'advise', lambda *a: {'label': label, 'evidence': 'Blocked on Lee review PR123'})
    return NullEvents(), {'last_assistant_message': 'evidence', 'session_id': 'session1'}


def test_finished_prompts_once_without_closing(monkeypatch, tmp_path, capsys):
    events,payload = setup_send(monkeypatch, tmp_path, 'finished-unclosed')
    se._send(None,events,None,'worker',tmp_path,payload=payload)
    assert json.loads(capsys.readouterr().out)['decision'] == 'block'
    assert events.pending('lead')[0].item_status == 'in_progress'
    se._send(None,events,None,'worker',tmp_path,payload=payload)
    assert capsys.readouterr().out == ''


def test_blocked_reaches_lead_and_named_evidence(monkeypatch,tmp_path,capsys):
    events,payload=setup_send(monkeypatch,tmp_path,'blocked')
    se._send(None,events,None,'worker',tmp_path,payload=payload)
    e=events.pending('lead')[0]
    assert 'Lee review PR123' in e.detail and e.rose is False
    assert capsys.readouterr().out == ''
    rendered=se._compose_reason([e],{},time.time(),current_items={'worker': ('task2','open')})
    assert 'Historical, recheck before acting:' in rendered


def test_gave_up_uses_existing_admin_route(monkeypatch,tmp_path):
    from shantytown import tier
    monkeypatch.setattr(tier,'find_administrator',lambda reg: 'admin')
    events,payload=setup_send(monkeypatch,tmp_path,'gave-up')
    se._send(None,events,None,'worker',tmp_path,payload=payload)
    e=events.pending('admin')[0]
    assert e.rose and e.reason=='needs-decision' and 'escalate:' in e.detail
    assert events.pending('lead') == []


@pytest.mark.parametrize('label',['mid-work','none-of-these','unavailable'])
def test_midwork_and_unknown_do_not_escalate_or_block(monkeypatch,tmp_path,capsys,label):
    events,payload=setup_send(monkeypatch,tmp_path,label)
    se._send(None,events,None,'worker',tmp_path,payload=payload)
    e=events.pending('lead')[0]
    assert not e.rose and e.reason is None
    assert capsys.readouterr().out == ''


def test_verdict_log_masks_text_and_failure_preserves_route(monkeypatch,tmp_path):
    monkeypatch.setattr(so,'tail',lambda s: '<masked>')
    monkeypatch.setattr(so,'classify',lambda req: {'label':'blocked','confidence':.9})
    payload={'last_assistant_message':'SECRET','session_id':'s'}
    assert so.advise(tmp_path,'worker','task','open',payload,'server')['label']=='blocked'
    log=(tmp_path/'stop_outcomes/verdicts.jsonl').read_text()
    assert 'SECRET' not in log and '<masked>' not in log
    row=json.loads(log); assert row['sourceKind']=='inferred' and row['input_hash']
    monkeypatch.setattr(so,'record',lambda *a: (_ for _ in ()).throw(OSError()))
    assert so.advise(tmp_path,'worker','task','open',payload,'server')['label']=='unavailable'


def test_symlink_log_refuses(tmp_path):
    directory=tmp_path/'stop_outcomes';directory.mkdir()
    (directory/'verdicts.jsonl').symlink_to(tmp_path/'other')
    with pytest.raises(OSError):
        so.record(tmp_path,'worker','task',{}, {'state':{}},{'label':'blocked'},time.monotonic())


def test_transport_timeout_kills_group(monkeypatch):
    class Process:
        pid=12345
        def communicate(self,*a,**k): raise so.subprocess.TimeoutExpired('worker',5)
        def wait(self): self.waited=True
    proc=Process(); killed=[]
    monkeypatch.setattr(so.subprocess,'Popen',lambda *a,**k:proc)
    monkeypatch.setattr(so.os,'killpg',lambda *a:killed.append(a))
    assert so.classify({})=={'label':'unavailable'}
    assert killed==[(12345,so.signal.SIGKILL)] and proc.waited


def test_failed_admin_lookup_preserves_durable_lead_event(monkeypatch,tmp_path):
    from shantytown import tier
    monkeypatch.setattr(tier,'find_administrator',lambda reg: (_ for _ in ()).throw(OSError()))
    events,payload=setup_send(monkeypatch,tmp_path,'gave-up')
    assert se._send(None,events,None,'worker',tmp_path,payload=payload)==0
    e=events.pending('lead')[0]
    assert not e.rose and 'escalate:' in e.detail
