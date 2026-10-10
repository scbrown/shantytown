from copy import deepcopy
import pytest
from shantytown.pr_preflight import Refused
from shantytown.pr_queue import evaluate, admission, pending_events

NOW = '2026-10-10T06:00:00Z'

def snapshot():
    return {'repo': 'example/project', 'complete': True, 'observed_at': NOW,
            'pulls': [{'number': 1, 'head': 'a'*40, 'base': 'b'*40,
                       'state': 'open', 'draft': False,
                       'created_at': '2026-10-08T06:00:00Z', 'author': 'alice',
                       'reviewer': 'bob', 'head_green': True, 'base_green': True,
                       'review_valid': True, 'binding_valid': True,
                       'approval_digest': 'c'*64, 'holds': []}]}

def test_positive_and_cap_and_exact_boundary():
    r = evaluate(snapshot(), now=NOW, cap=1)
    assert r['rows'][0]['eligible'] and not r['execution_authority']
    assert r['open_count'] == 1 and r['median_age_hours'] == 48
    assert not admission(r, 'alice') and admission(r, 'bob')
    events = pending_events(r, set())
    assert len(events) == 1
    assert pending_events(r, {events[0]['key']}) == []
    assert pending_events(r, set()) == events
    s = snapshot(); s['pulls'][0]['head'] = 'd'*40
    assert evaluate(s, now=NOW, cap=1)['stale_events'][0]['key'] != events[0]['key']

@pytest.mark.parametrize('key,value', [('head_green',False),('head_green',None),
 ('base_green',False),('review_valid',False),('binding_valid',False),
 ('reviewer','alice'),('reviewer',None),('approval_digest',None),('holds',None),
 ('holds',['blackout']),('draft',True),('base',None),('author',None)])
def test_missing_or_negative_evidence_never_eligible(key,value):
    s = snapshot(); s['pulls'][0][key] = value
    assert not evaluate(s, now=NOW, cap=2)['rows'][0]['eligible']

def test_unknown_owner_blocks_admission_and_routes_no_event():
    s = snapshot(); s['pulls'][0]['author'] = None
    r = evaluate(s, now=NOW, cap=2)
    assert not admission(r,'alice') and pending_events(r,set()) == []

@pytest.mark.parametrize('key,value', [('complete',False),('complete',None),
 ('observed_at','2026-10-10T05:54:59Z'),('observed_at','2026-10-10T06:00:01Z'),
 ('observed_at','2026-10-10T06:00:00'),('repo','invalid'),('pulls',None)])
def test_refuse_unknown_coverage_or_clock(key,value):
    s=snapshot(); s[key]=value
    with pytest.raises(Refused): evaluate(s,now=NOW,cap=2)

@pytest.mark.parametrize('key,value', [('number',True),('number',0),('head',{}),
 ('state','closed'),('draft',None),('created_at','2026-10-11T06:00:00Z')])
def test_malformed_rows_refuse(key,value):
    s=snapshot(); s['pulls'][0][key]=value
    with pytest.raises(Refused): evaluate(s,now=NOW,cap=2)

def test_duplicate_number_and_empty_control():
    s=snapshot(); s['pulls'].append(deepcopy(s['pulls'][0]))
    with pytest.raises(Refused): evaluate(s,now=NOW,cap=2)
    s['pulls']=[]; r=evaluate(s,now=NOW,cap=2)
    assert r['open_count']==0 and r['median_age_hours']==0

@pytest.mark.parametrize('cap',[True,0,-1,None])
def test_invalid_cap(cap):
    with pytest.raises(Refused): evaluate(snapshot(),now=NOW,cap=cap)
