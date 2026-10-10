from copy import deepcopy
import pytest
from shantytown.pr_binding_check import check,main
from shantytown.pr_preflight import Refused

def event():
    return {'repository':{'full_name':'crew/project'},'pull_request':{
        'body':'Bead: project-abc','base':{'repo':{'full_name':'crew/project'}},
        'head':{'repo':{'full_name':'crew/project'}}}}

def test_crew_positive_and_external_contributor_scope():
    value=event();assert check(value,'crew')['bead']=='project-abc'
    value['pull_request']['head']['repo']['full_name']='contributor/project'
    value['pull_request']['body']=None
    assert check(value,'crew')=={'scope':'external-contributor','required':False}

@pytest.mark.parametrize('body',[None,'','Bead: invalid','Bead: project-abc\nBead: project-def'])
def test_crew_missing_or_ambiguous_marker_refused(body):
    value=event();value['pull_request']['body']=body
    with pytest.raises(Refused):check(value,'crew')

@pytest.mark.parametrize('part',['repository','head','base'])
def test_unknown_repository_identity_refuses(part):
    value=event()
    if part=='repository':value[part]=None
    else:value['pull_request'][part]['repo']=None
    with pytest.raises(Refused):check(value,'crew')

def test_wrong_configured_owner_refuses():
    with pytest.raises(Refused):check(event(),'different')

def test_actual_ci_entry_positive_and_negative(tmp_path,capsys):
    import json
    path=tmp_path/'event.json';path.write_text(json.dumps(event()))
    assert main(['--event-file',str(path),'--owner','crew'])==0
    value=event();value['pull_request']['body']='No marker';path.write_text(json.dumps(value))
    assert main(['--event-file',str(path),'--owner','crew'])==2
    assert 'refused' in capsys.readouterr().out
