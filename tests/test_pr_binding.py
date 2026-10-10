from copy import deepcopy
import pytest
from shantytown.pr_binding import Registry, primary, register, reconcile
from shantytown.pr_preflight import Refused, Indeterminate

BOARD='https://seeds.example/project/test'
class Forge:
    def __init__(self):
        self.pull={'head':'a'*40,'body':'Bead: project-abc','state':'open','draft':False}
        self.messages=[]; self.writes=[]; self.lose=None
    def read(self,*args): return deepcopy(self.pull)
    def comments(self,*args): return list(self.messages)
    def comment(self,repo,number,text):
        self.messages.append(text); self.writes.append('comment')
        if self.lose=='comment': raise TimeoutError()
    def close(self,*args):
        self.pull['state']='closed'; self.writes.append('close')
        if self.lose=='close': raise TimeoutError()
    def draft(self,*args):
        self.pull['draft']=True; self.writes.append('draft')
        if self.lose=='draft': raise TimeoutError()

@pytest.fixture
def setup(tmp_path):
    r=Registry(tmp_path/'registry',BOARD); f=Forge()
    item={'id':'project-abc','assignee':'alice','status':'in_progress','notes':'private reason'}
    read=lambda bead:deepcopy(item)
    register(r,'example/project',1,'alice',BOARD,read,f)
    return r,f,item,read

@pytest.mark.parametrize('body',['',None,'Bead: project-abc\nBead: project-abc',
 'Bead: invalid','mentions project-abc without marker'])
def test_primary_refuses(body):
    with pytest.raises(Refused):primary(body)

@pytest.mark.parametrize('state,action',[('deferred','draft'),('closed','close')])
def test_lifecycle_once_with_private_reason(setup,state,action):
    r,f,item,read=setup;item['status']=state;item['close_reason']='private close'
    assert len(reconcile(r,BOARD,read,f))==1
    assert f.writes==['comment',action]
    assert 'private' not in f.messages[0]
    assert reconcile(r,BOARD,read,f)==[]
    assert f.writes==['comment',action]
    assert (r.root/'registry.json').stat().st_mode & 0o077 == 0

@pytest.mark.parametrize('lost',['comment','close','draft'])
def test_lost_response_reconciles_no_duplicate_write(setup,lost):
    r,f,item,read=setup;item['status']='deferred' if lost=='draft' else 'closed'
    item['close_reason']='private close';f.lose=lost
    with pytest.raises(Indeterminate):reconcile(r,BOARD,read,f)
    f.lose=None
    restarted=Registry(r.root,BOARD)
    reconcile(restarted,BOARD,read,f)
    assert f.writes.count('comment')==1
    assert f.writes.count('draft' if lost=='draft' else 'close')==1

@pytest.mark.parametrize('change',[{'assignee':'bob'},{'status':'mystery'},
 {'status':'closed','close_reason':None},{'id':'project-other'}])
def test_bad_board_state_performs_zero_writes(setup,change):
    r,f,item,read=setup;item.update(change)
    with pytest.raises(Refused):reconcile(r,BOARD,read,f)
    assert f.writes==[]

@pytest.mark.parametrize('change',[{'head':'b'*40},{'body':'Bead: project-other'},
 {'draft':None},{'head':{}},{'state':'mystery'}])
def test_changed_pr_refuses_zero_writes(setup,change):
    r,f,item,read=setup;item['status']='deferred';f.pull.update(change)
    with pytest.raises(Refused):reconcile(r,BOARD,read,f)
    assert f.writes==[]

def test_wrong_board_and_registry_identity(setup):
    r,f,item,read=setup
    with pytest.raises(Refused):reconcile(r,'different',read,f)
    with pytest.raises(Refused):
        with Registry(r.root,'different').locked():pass
    assert f.writes==[]

def test_event_cannot_close_current_open_bead(setup):
    r,f,item,read=setup
    assert reconcile(r,BOARD,read,f)==[] and f.writes==[]

def test_register_refuses_closed_or_other_owner(tmp_path):
    r=Registry(tmp_path/'registry',BOARD);f=Forge()
    for item in [{'id':'project-abc','assignee':'bob','status':'open'},
                 {'id':'project-abc','assignee':'alice','status':'closed'}]:
        with pytest.raises(Refused):register(r,'example/project',1,'alice',BOARD,lambda _:item,f)
    assert f.writes==[]

def test_changed_pending_reason_held(setup):
    r,f,item,read=setup;item['status']='closed';item['close_reason']='one';f.lose='comment'
    with pytest.raises(Indeterminate):reconcile(r,BOARD,read,f)
    item['close_reason']='two';f.lose=None
    with pytest.raises(Refused):reconcile(r,BOARD,read,f)
    assert f.writes==['comment']

def test_github_draft_uses_graphql_and_never_merge(monkeypatch):
    from shantytown.pr_binding import GitHubAdapter
    f=GitHubAdapter();calls=[]
    def api(path,body=None,method=None):
        calls.append((path,body,method))
        if body is None:
            return {'head':{'sha':'a'*40},'body':'Bead: project-abc',
                    'state':'open','draft':False,'node_id':'PR_node'}
        return {'data':{}}
    monkeypatch.setattr(f,'api',api)
    f.draft('example/project',1)
    assert calls[1][0]=='graphql'
    assert 'convertPullRequestToDraft' in calls[1][1]['query']
    assert calls[1][1]['variables']=={'id':'PR_node'}
    assert not hasattr(f,'merge')

def test_seeds_reader_refuses_route_drift_and_reads_known_id(monkeypatch):
    from shantytown.pr_binding import SeedsReader
    calls=[]
    def command(self,*args):
        calls.append(args)
        if args[0]=='where':return {'mode':'remote','graph':BOARD,'quipu_url':'https://graph.example'}
        return [{'id':'project-abc','assignee':'alice','status':'open'}]
    monkeypatch.setattr(SeedsReader,'command',command)
    read=SeedsReader('https://graph.example',BOARD)
    assert read('project-abc')['id']=='project-abc'
    assert calls==[('where','--json'),('show','project-abc','--json')]
    with pytest.raises(Refused):SeedsReader('https://different.example',BOARD)


def test_bad_post_response_cli_stays_indeterminate(setup,monkeypatch,capsys):
    from shantytown import pr_binding as p
    r,f,item,read=setup;item['status']='closed';item['close_reason']='private';f.lose='close'
    monkeypatch.setattr(p,'SeedsReader',lambda *args:read)
    monkeypatch.setattr(p,'GitHubAdapter',lambda:f)
    assert p.main(['--registry',str(r.root),'--quipu','https://graph.example',
                   '--graph',BOARD,'reconcile'])==2
    assert 'indeterminate' in capsys.readouterr().out
    assert f.writes==['comment','close']

@pytest.mark.parametrize('status,owner,draft',[('closed','alice',False),
 ('open','bob',False),('deferred','alice',False)])
def test_bound_creator_owner_gate_before_forge_call(tmp_path,status,owner,draft):
    from shantytown.pr_binding import create_bound
    r=Registry(tmp_path/'registry',BOARD);f=Forge()
    item={'id':'project-abc','assignee':owner,'status':status}
    with pytest.raises(Refused):
        create_bound(r,'example/project','unused','project-abc','alice',BOARD,
                     lambda _:item,f,'title','body',[],draft=draft)
    assert f.writes==[]

def test_bound_creator_lost_response_never_replays(tmp_path,monkeypatch):
    from shantytown.pr_binding import create_bound
    from shantytown import pr_preflight as pre
    r=Registry(tmp_path/'registry',BOARD);f=Forge();f.api=lambda *args:None
    item={'id':'project-abc','assignee':'alice','status':'open'}
    monkeypatch.setattr(pre,'candidate',lambda *args:{'head':'a'*40,'head_ref':'branch'})
    writes=[]
    def lost(*args,**kw):writes.append('post');raise Indeterminate('lost')
    monkeypatch.setattr(pre,'run',lost)
    for _ in range(2):
        with pytest.raises(Indeterminate):
            create_bound(r,'example/project','unused','project-abc','alice',BOARD,
                         lambda _:item,f,'title','body',[])
    assert writes==['post']

def test_bound_creator_post_commit_binding_failure_indeterminate(tmp_path,monkeypatch):
    from shantytown import pr_binding as binding
    from shantytown import pr_preflight as pre
    r=Registry(tmp_path/'registry',BOARD);f=Forge();f.api=lambda *args:None
    item={'id':'project-abc','assignee':'alice','status':'open'}
    monkeypatch.setattr(pre,'candidate',lambda *args:{'head':'a'*40,'head_ref':'branch'})
    monkeypatch.setattr(pre,'run',lambda *args,**kw:{'number':1,'created':'https://github.com/example/project/pull/1'})
    def fail(*args):raise Refused('bead changed after create')
    monkeypatch.setattr(binding,'register',fail)
    with pytest.raises(Indeterminate):
        binding.create_bound(r,'example/project','unused','project-abc','alice',BOARD,
                             lambda _:item,f,'title','body',[])
    with r.locked() as data:assert len(data['creates'])==1

def test_manual_register_reconciles_matching_pending_create(setup):
    r,f,item,read=setup
    with r.locked() as data:
        data['creates']={'key':{'repo':'example/project','bead':'project-abc',
                               'author':'alice','head':'a'*40,'branch':'branch'}}
        r.save(data)
    register(r,'example/project',1,'alice',BOARD,read,f)
    with r.locked() as data:assert data['creates']=={}
    assert f.writes==[]

def test_registry_corruption_refuses_before_forge_write(setup):
    import json
    r,f,item,read=setup;item['status']='deferred'
    p=r.root/'registry.json';value=json.loads(p.read_text())
    value['bindings']['example/project#1']['head']={}
    p.write_text(json.dumps(value))
    with pytest.raises(Refused):reconcile(r,BOARD,read,f)
    assert f.writes==[]
