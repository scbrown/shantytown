"""Native deferral selection keeps candidate scope and shadow refusal."""
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

import pytest
from shantytown import seeds_plate
from shantytown.answer import PartialAnswer
from shantytown.sd import StoreProof

BINARY = os.environ.get('SHANTYTOWN_TEST_QUIPU_SERVER')
G = 'https://example.test/deferred'
N = seeds_plate.SEEDS
S = seeds_plate.SCHEMA


@pytest.mark.skipif(not BINARY, reason='isolated native Quipu binary required')
def test_native_status_first_candidates_and_shadow(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='deferred-native-') as d:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        child = subprocess.Popen([BINARY, '--db', d+'/q.db', '--bind', f'127.0.0.1:{port}'],
                                 cwd=d, env=dict(os.environ, HOME=d),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(url+'/health', timeout=1).close()
                    break
                except OSError:
                    time.sleep(.05)
            def update(body):
                request = urllib.request.Request(url+'/update',
                    data=urllib.parse.urlencode({'update':body}).encode(),
                    headers={'Content-Type':'application/x-www-form-urlencoded',
                             'X-Quipu-Client':'seeds'})
                urllib.request.urlopen(request, timeout=5).close()
            def item(i, status, extra='', action=True):
                sub=f'<{N}item/test-{i}>'
                return (f'{sub} '+(f'a <{S}Action> ; ' if action else '')+
                        f'<{S}identifier> "test-{i}" ; <{S}name> "held" ; '
                        f'<{N}ontology/status> "{status}" ; <{N}ontology/priority> 1 ; '
                        f'<{S}dateCreated> "2026-01-01T00:00:00Z" . '+extra.replace('$s',sub))
            facts = [item('defer','deferred'),
                     item('date','open',f'$s <{S}scheduledTime> "2026-10-20" .'),
                     item('alias','open',f'$s <{N}ontology/deferUntil> "2026-10-20" .'),
                     item('ask','in_progress',f'$s <{N}ontology/notes> "steve_quote: hold" .'),
                     item('gate','blocked',f'$s <{N}ontology/notes> "resume_when: closed:test-z" .'),
                     item('irrelevant','open'), item('not-action','deferred',action=False)]
            update('INSERT DATA { GRAPH <'+G+'> { '+' '.join(facts)+' } }')
            proof=StoreProof('fixture',None,url,'test',G)
            queries=[]; original=seeds_plate._query
            def counted(*args, **kwargs):
                queries.append(args[1]); return original(*args, **kwargs)
            monkeypatch.setattr(seeds_plate,'_query',counted)
            rows=seeds_plate.deferred_rows(proof,include_asks=True).exact()
            assert {r['id'] for r in rows} == {'test-defer','test-date','test-alias','test-ask','test-gate'}
            assert len(queries)==2
            # These joins are after the distinct indexed candidate selection.
            assert queries[0].count('SELECT DISTINCT ?s')==2
            assert queries[0].count('a <'+S+'Action>')==2
            update('INSERT DATA { GRAPH <'+G+'#seeds-ephemeral> { '+item('defer','closed')+' } }')
            with pytest.raises(PartialAnswer):
                seeds_plate.deferred_rows(proof,include_asks=True).exact()
        finally:
            child.terminate()
            child.wait(timeout=5)


def test_candidate_budget_refuses_without_field_read(monkeypatch):
    calls=[]
    def capped(proof, query, timeout):
        calls.append(query)
        return {'rows':[{'subject':N+'item/test-one','graph':G}]*10000}
    monkeypatch.setattr(seeds_plate,'_query',capped)
    proof=StoreProof('fixture',None,'https://example.test','test',G)
    with pytest.raises(PartialAnswer,match='budget reached'):
        seeds_plate.deferred_rows(proof,include_asks=True).exact()
    assert len(calls)==1
