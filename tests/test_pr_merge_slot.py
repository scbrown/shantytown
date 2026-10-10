import json
from types import SimpleNamespace
import pytest
from shantytown.pr_binding import Registry,register
from shantytown.pr_queue import enqueue_review
from shantytown.pr_merge_slot import execute_one
from shantytown.pr_preflight import Refused,Indeterminate

@pytest.fixture
def setup(tmp_path,monkeypatch):
    r=Registry(tmp_path/'registry','https://board.example/graph')
    class Forge:
        def read(self,*args):return {'head':'a'*40,'body':'Bead: project-abc','state':'open','draft':False}
    register(r,'example/project',1,'alice',r.board,
             lambda _:{'id':'project-abc','assignee':'alice','status':'open'},Forge())
    enqueue_review(r,'example/project',1,'alice','bob')
    record=tmp_path/'record.json';record.write_text(json.dumps({'id':'c'*64,'author':'alice',
        'snapshot':{'repo':'example/project','number':1,'head':'a'*40}}))
    from pathlib import Path
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    helper=tmp_path/'.local/share/aegis-exec-src/scripts/pr-risk.py'
    helper.parent.mkdir(parents=True);helper.write_text('# owned trusted-helper fixture')
    remote={'verified':True,'head':'a'*40,'merged':False};calls=[]
    def read(*args):return dict(remote)
    def runner(argv,**kw):
        calls.append(argv)
        if '--execute' in argv:remote.update(merged=True,merge_sha='d'*40,commit_verified=True)
        return SimpleNamespace(returncode=0,stdout='TOOL SOURCE neutral sha='+ 'e'*40 +' origin/main='+ 'e'*40 +' state=current',stderr='')
    kwargs=dict(enabled=True,writer='writer',designated_writer='writer',holds_clear=True,
                blackout=False,rollback='named rollback packet',runner=runner,
                read_gate=lambda:{'verified':True,'enabled':True,'writer':'writer',
                                  'holds_clear':True,'blackout':False,'rollback':'named rollback packet'})
    return r,record,helper,remote,calls,read,runner,kwargs


def run(s,**over):
    r,record,helper,remote,calls,read,runner,kwargs=s
    return execute_one(r,record,'checkout','receipt',helper,read,**(kwargs|over))

@pytest.mark.parametrize('over',[{'enabled':False},{'writer':'other'},
 {'holds_clear':None},{'holds_clear':False},{'blackout':True},{'rollback':None}])
def test_activation_guards_zero_helper_calls(setup,over):
    with pytest.raises(Refused):run(setup,**over)
    assert setup[4]==[]

def test_positive_helper_exact_entry_and_once_reconcile(setup):
    result=run(setup)
    assert result['sha']=='d'*40
    assert len(setup[4])==2 and '--execute' not in setup[4][0] and '--execute' in setup[4][1]
    assert run(setup)['reconciled'] and len(setup[4])==2

def test_preflight_failure_never_executes(setup):
    def failed(argv,**kw):setup[4].append(argv);return SimpleNamespace(returncode=2)
    with pytest.raises(Refused):run(setup,runner=failed)
    assert len(setup[4])==1 and '--execute' not in setup[4][0]

def test_lost_response_read_reconcile_no_second_execute(setup):
    original=setup[6]
    def lost(argv,**kw):
        value=original(argv,**kw)
        if '--execute' in argv:raise TimeoutError()
        return value
    with pytest.raises(Indeterminate):run(setup,runner=lost)
    assert run(setup)['reconciled'] and len(setup[4])==2

def test_unmerged_after_attempt_never_blind_retry(setup):
    def unknown(argv,**kw):
        setup[4].append(argv)
        if '--execute' in argv:raise TimeoutError()
        return SimpleNamespace(returncode=0,stdout='TOOL SOURCE neutral sha='+ 'e'*40 +' origin/main='+ 'e'*40 +' state=current',stderr='')
    with pytest.raises(Indeterminate):run(setup,runner=unknown)
    with pytest.raises(Indeterminate):run(setup,runner=unknown)
    assert len(setup[4])==2

def test_changed_remote_head_refuses(setup):
    setup[3]['head']='e'*40
    with pytest.raises(Refused):run(setup)
    assert setup[4]==[]


def test_hold_added_during_preflight_blocks_execute(setup):
    state={'verified':True,'enabled':True,'writer':'writer','holds_clear':True,
           'blackout':False,'rollback':'named rollback packet'}
    original=setup[6]
    def preflight(argv,**kw):
        result=original(argv,**kw);state['holds_clear']=False;return result
    with pytest.raises(Refused):run(setup,runner=preflight,read_gate=lambda:dict(state))
    assert len(setup[4])==1 and '--execute' not in setup[4][0]
    with setup[0].locked() as data:assert data.get('merge_slot') is None


def test_proposed_or_other_helper_path_refused(setup,tmp_path):
    r,record,helper,remote,calls,read,runner,kwargs=setup
    other=tmp_path/'proposed-helper.py';other.write_text('# wrong execution source')
    with pytest.raises(Refused):execute_one(r,record,'checkout','receipt',other,read,**kwargs)
    assert calls==[]


def test_helper_currency_unknown_refuses_before_execute(setup):
    def unknown(argv,**kw):setup[4].append(argv);return SimpleNamespace(returncode=0,stdout='',stderr='')
    with pytest.raises(Refused):run(setup,runner=unknown)
    assert len(setup[4])==1 and '--execute' not in setup[4][0]
