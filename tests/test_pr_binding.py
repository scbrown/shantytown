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
