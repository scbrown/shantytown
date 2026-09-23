import json
import sys
import shlex
from types import SimpleNamespace as NS

import pytest

from shantytown import board_triage as bt, br
from shantytown.answer import Answer
from shantytown.protocols import Agent, WorkItem


def row(id='a', **kw):
    return dict(id=id, title='repair backup', description='failed backup check',
                status='open', assignee=None, **kw)


class Client:
    def call(self, tool, args):
        answer = {'confidence': .8, 'choice': 'none-of-these'}
        if tool == 'jev_noul':
            answer = {'noul': .02, 'type': 'noul'}
        elif tool == 'jev_score':
            answer = {'score': 1.0, 'confidence': .9}
        return dict(answer=answer, model='test-model', usage={}, request=args)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_candidates_exclude_self_inbox_and_unrelated_and_bound_top_five():
    others = [row(str(i)) for i in range(8)]
    others += [dict(id='inbox', title='inbox: repair backup'), dict(id='other', title='zebra')]
    found = bt.candidates(row(), [row(), *others])
    assert [r['id'] for r in found] == ['0', '1', '2', '3', '4']
    assert bt.candidates(dict(id='z', title='weather'), [row()]) == []


def test_abstention_and_raw_noul_probability_preserve_provenance():
    result = bt.suggest(row(), [row('b')], {'worker':'storage'}, Client())
    assert result['routing']['answer']['choice'] == 'none-of-these'
    assert result['duplicate_candidates'][0]['verdict']['answer']['noul'] == .02
    assert result['sourceKind'] == 'inferred' and result['plane'] == 'quarantine'
    assert 'test-model' in bt.comment(result)
    assert bt.comment(result) == bt.comment(result)


def test_labels_are_not_features_and_benchmark_reports_disagreement():
    data = row(); data.update(assignee='owner', priority=0, comments=['choose owner'])
    assert 'owner' not in bt.state(data)
    result = bt.benchmark([data], {'owner':'storage'}, Client())
    assert result['agreement'] == 0 and result['abstain_rate'] == 1
    assert result['confidence_disagree'] == [.8]


@pytest.mark.parametrize('answer', [{'confidence':float('nan')}, {'confidence':True},
                                    {'confidence':.8,'score':99}])
def test_bad_typed_verdict_refuses(answer):
    client=Client()
    client.call=lambda *a: dict(answer=answer,request={},usage={},model='test')
    with pytest.raises(bt.TriageError):
        bt.verdict(client,'jev_score',{})


def setup(monkeypatch, rows):
    tracker=br.BrTracker('/unused')
    monkeypatch.setattr(tracker,'_bd',lambda *a:NS(returncode=0,stdout=json.dumps(rows)))
    registry=NS(all=lambda:Answer.complete_read([Agent('owner',domain='storage')],how='test graph'),
                onto='urn:crew:', _query_answer=lambda q:Answer.complete_read([],how='domain query'))
    args=NS(benchmark=None,items=[],limit=5,dry_run=False,jev_command='test',publish=True)
    monkeypatch.setattr(bt,'JevMCP',lambda *a:Client())
    return args,tracker,registry


def test_publish_comments_only_and_readback(monkeypatch):
    args,trk,reg=setup(monkeypatch,[row()]); stored=[]
    monkeypatch.setattr(trk,'get',lambda id:WorkItem(id=id,title='repair',status='open'))
    monkeypatch.setattr(br,'comments',lambda *a:stored)
    monkeypatch.setattr(br,'append_comment',lambda tr,id,body:stored.append({'text':body}))
    out=bt.run(args,trk,reg)
    assert len(stored)==1 and out['suggestions'][0]['published']
    assert '[jev board-triage ' in stored[0]['text']


def test_assignment_race_refuses_without_comment(monkeypatch):
    args,trk,reg=setup(monkeypatch,[row()])
    monkeypatch.setattr(trk,'get',lambda id:WorkItem(id=id,title='repair',status='open',assignee='new'))
    monkeypatch.setattr(br,'append_comment',lambda *a:pytest.fail('published stale suggestion'))
    with pytest.raises(bt.TriageError,match='changed'):
        bt.run(args,trk,reg)


def test_failed_publication_readback_is_indeterminate(monkeypatch):
    args,trk,reg=setup(monkeypatch,[row()])
    monkeypatch.setattr(trk,'get',lambda id:WorkItem(id=id,title='repair',status='open'))
    monkeypatch.setattr(br,'comments',lambda *a:[])
    monkeypatch.setattr(br,'append_comment',lambda *a:None)
    with pytest.raises(bt.TriageError,match='indeterminate'):
        bt.run(args,trk,reg)


def test_dry_run_no_jev_and_missing_domains_reported(monkeypatch):
    args,trk,reg=setup(monkeypatch,[row()]); args.dry_run=True
    reg.all=lambda:Answer.complete_read([Agent('worker')],how='graph')
    monkeypatch.setattr(bt,'JevMCP',lambda *a:pytest.fail('live call'))
    out=bt.run(args,trk,reg)
    assert out['sent'] is False and out['missing_domains']==['worker']


@pytest.mark.parametrize('mode',['ok','error','eof','timeout'])
def test_stdio_protocol_and_failures(tmp_path, mode):
    script=tmp_path/'server.py'
    script.write_text('''import json,sys,time
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if m['method']=='initialize': result={'protocolVersion':'2025-06-18','capabilities':{}}
 else:
  if sys.argv[1]=='eof': break
  if sys.argv[1]=='timeout': time.sleep(10)
  result={'isError':sys.argv[1]=='error','structuredContent':{'answer':{'noul':0.1}}}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
''')
    with bt.JevMCP(shlex.join([sys.executable,str(script),mode]),timeout=.3) as client:
        if mode=='ok': assert client.call('jev_noul',{})['answer']['noul']==.1
        else:
            with pytest.raises(bt.TriageError): client.call('jev_noul',{})


def test_explicit_ineligible_target_refuses(monkeypatch):
    item=row(); item['assignee']='owner'
    args,trk,reg=setup(monkeypatch,[item]); args.items=['a']
    with pytest.raises(bt.TriageError,match='open, unassigned'):
        bt.run(args,trk,reg)


def test_duplicate_benchmark_labels_are_not_thirty_examples(tmp_path, monkeypatch):
    args,trk,reg=setup(monkeypatch,[row()]); item=row(); item['assignee']='owner'
    path=tmp_path/'labels.jsonl'; path.write_text((json.dumps(item)+'\n')*30)
    args.benchmark=str(path)
    with pytest.raises(bt.TriageError,match='30'):
        bt.run(args,trk,reg)


def test_graph_domain_is_read_even_when_roster_does_not_project_it():
    reg=NS(onto='urn:crew:', all=lambda:Answer.complete_read([Agent('worker')],how='roster'),
           _query_answer=lambda q:Answer.complete_read([{'s':'urn:crew:worker','domain':'storage'}],how=q))
    assert bt.graph_agents(reg)[0].domain=='storage'


def test_partial_domain_query_cannot_look_like_absence():
    reg=NS(onto='urn:crew:', all=lambda:Answer.complete_read([Agent('worker')],how='roster'),
           _query_answer=lambda q:Answer.capped([],how=q,caveat='truncated'))
    with pytest.raises(Exception):
        bt.graph_agents(reg)
