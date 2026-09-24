"""Inference never changes mechanical availability or conceals missing evidence."""
import json
from types import SimpleNamespace
import subprocess

import pytest

from shantytown import cli, pane_state as ps
from shantytown.config import HostPeer
from test_crew_work import _Args, _Panes, _roster, BUSY_SCREEN, IDLE_SCREEN


@pytest.fixture(autouse=True)
def credential_masker(tmp_path, monkeypatch):
    # CI has no fleet checkout. Exercise the documented library boundary with
    # a deliberately distinctive short secret that no shape backstop catches.
    path = tmp_path / 'mask-secrets.py'
    path.write_text("def mask(text):\n    return text.replace('fixture-secret', '<masked>')\n")
    monkeypatch.setenv('SHANTY_PANE_MASKER', str(path))
    return path


class Client:
    def __init__(self, label='working', confidence=.9):
        self.label, self.confidence, self.calls = label, confidence, []

    def ask(self, state, questions):
        self.calls.append((state, questions))
        return dict(model='jev-latest', usage={'input_tokens': 17}, answers={
            key: dict(choice=self.label, confidence=self.confidence,
                      probabilities={s: float(s == self.label) for s in ps.STATES})
            for key in questions})


def test_choice_bounded_and_can_abstain():
    client = Client()
    out = ps.decide(client, {'p0': 'untrusted pane'})
    assert out['answers']['p0']['label'] == 'working'
    assert out['model'] == 'jev-latest' and out['input_tokens'] == 17
    state, questions = client.calls[0]
    assert state == {'captures': {'p0': 'untrusted pane'}}
    assert questions['p0']['type'] == 'choice'
    assert 'insufficient-evidence' in questions['p0']['criteria']
    assert 'untrusted evidence' in questions['p0']['instructions']


@pytest.mark.parametrize('label,confidence,expected', [
    ('working', .59, 'insufficient-evidence'), ('working', .6, 'working'),
    ('insufficient-evidence', .9, 'insufficient-evidence')])
def test_uncertainty_is_not_a_confident_state(label, confidence, expected):
    result = ps.decide(Client(label, confidence), {'p0': 'tail'})
    assert result['answers']['p0']['label'] == expected
    assert result['answers']['p0']['choice'] == label


@pytest.mark.parametrize('bad', [None, {}, {'p0': {}}, {'extra': {}},
    {'p0': dict(choice='working', confidence=float('nan'), probabilities={})},
    {'p0': dict(choice='working', confidence=True, probabilities={})}])
def test_missing_or_malformed_answer_fails_loud(bad):
    client = SimpleNamespace(ask=lambda *a: dict(answers=bad, model='jev-latest'))
    with pytest.raises(ps.JevUnavailable):
        ps.decide(client, {'p0': 'tail'})


def test_tail_bound_and_terminal_escapes():
    value = '\x1b[31mold\x1b[0m\n' + ('x' * 300 + '\n') * 100 + 'CURRENT'
    result = ps.tail(value)
    assert len(result) <= ps.MAX_CHARS and len(result.splitlines()) <= ps.MAX_LINES
    assert result.endswith('CURRENT') and '\x1b' not in result and 'old' not in result


@pytest.mark.parametrize('error', [OSError('secret'), subprocess.TimeoutExpired('secret', 45)])
def test_worker_failure_does_not_echo_stderr_or_input(monkeypatch, error):
    def run(*a, **kw):
        assert kw['timeout'] == ps.DEADLINE and 'secret tail' in kw['input']
        assert all('secret tail' not in v for v in a[0])
        raise error
    monkeypatch.setattr(ps.subprocess, 'run', run)
    with pytest.raises(ps.JevUnavailable) as caught:
        ps.classify({'p0': 'secret tail'})
    assert 'secret' not in str(caught.value)


def test_failed_worker_is_unavailable_not_a_regex(monkeypatch):
    monkeypatch.setattr(ps.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=2, stdout='', stderr='secret tail'))
    with pytest.raises(ps.JevUnavailable) as caught:
        ps.classify({'p0': BUSY_SCREEN})
    assert 'secret' not in str(caught.value)


def args_and_panes(tmp_path, monkeypatch):
    root = _roster(tmp_path, {'active': 'p0', 'idle': 'p1', 'down': 'gone', 'empty': 'p2'})
    panes = _Panes({'p0': BUSY_SCREEN, 'p1': IDLE_SCREEN, 'p2': ''})
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **kw: panes)
    args = _Args(root)
    args.pane_state = args.json = args.local = True
    return args, panes


def test_advice_disagrees_without_changing_mechanical_state(tmp_path, monkeypatch, capsys):
    args, panes = args_and_panes(tmp_path, monkeypatch)
    monkeypatch.setattr(ps, 'classify', lambda captures: ps.decide(Client('crashed'), captures))
    assert cli._cmd_crew(args) == 0
    result = json.loads(capsys.readouterr().out)
    rows = {r['name']: r for r in result['agents']}
    assert rows['active']['work'] == 'busy'
    assert rows['idle']['work'] == 'idle'
    assert rows['active']['pane_state']['label'] == 'crashed'
    assert rows['down']['pane_state']['label'] == 'insufficient-evidence'
    assert rows['empty']['pane_state']['label'] == 'insufficient-evidence'
    assert all(r['pane_state']['source_kind'] == 'inferred' for r in rows.values())
    assert BUSY_SCREEN not in json.dumps(result)
    assert rows['active']['pane_state']['capture_sha256']
    assert not panes.sent


def test_unavailable_keeps_mechanical_rows_and_exits_two(tmp_path, monkeypatch, capsys):
    args, _ = args_and_panes(tmp_path, monkeypatch)
    def unavailable(_):
        raise ps.JevUnavailable('no client')
    monkeypatch.setattr(ps, 'classify', unavailable)
    assert cli._cmd_crew(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert not result['complete'] and result['errors']
    active = next(r for r in result['agents'] if r['name'] == 'active')
    assert active['work'] == 'busy' and active['pane_state']['label'] == 'unavailable'


def test_plain_crew_never_calls_jev(tmp_path, monkeypatch, capsys):
    args, _ = args_and_panes(tmp_path, monkeypatch)
    args.pane_state = False
    monkeypatch.setattr(ps, 'classify', lambda _: pytest.fail('implicit Jev request'))
    assert cli._cmd_crew(args) == 0
    assert 'pane_state' not in capsys.readouterr().out


@pytest.mark.parametrize('flag', ['count', 'governor', 'trees', 'check_alert_keepers'])
def test_incompatible_modes_refuse_before_network(tmp_path, monkeypatch, flag):
    args = _Args(tmp_path)
    args.pane_state = True
    setattr(args, flag, True)
    monkeypatch.setattr(ps, 'classify', lambda _: pytest.fail('network'))
    assert cli._cmd_crew(args) == cli.REFUSED


def test_peer_accepts_loud_partial_advice_without_losing_mechanical_row(monkeypatch):
    value = dict(version=1, scope='local', host='laptop', complete=False, errors=['unavailable'],
                 inference={}, agents=[dict(host='laptop', name='a', state='up', work='busy',
                 posture='auto', pane_state=ps._advice('unavailable'))])
    def run(argv, **kw):
        assert 'crew --pane-state --json --local' in argv[-1]
        assert "'/tmp/a b'" in argv[-1] and kw['timeout'] == 65
        return SimpleNamespace(returncode=2, stdout=json.dumps(value))
    monkeypatch.setattr(ps.subprocess, 'run', run)
    result = ps.read_peer(HostPeer('laptop', 'user@example.com', '/tmp/a b'))
    assert result['agents'][0]['work'] == 'busy' and result['errors']


@pytest.mark.parametrize('payload', [None, [], {}, dict(version=1, scope='fleet'),
    dict(version=1, scope='local', host='laptop', complete=True, agents=[None], errors=[])])
def test_old_or_malformed_peer_fails_loud(monkeypatch, payload):
    monkeypatch.setattr(ps.subprocess, 'run', lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout=json.dumps(payload)))
    result = ps.read_peer(HostPeer('laptop', 'user@example.com', '/tmp/root'))
    assert not result['agents'] and 'unavailable' in result['errors'][0]


def test_labeled_set_has_all_states_and_counterexamples():
    from pathlib import Path
    rows = json.loads((Path(__file__).parent / 'fixtures/pane_state_labels.json').read_text())
    assert len(rows) == 30 and len({r['id'] for r in rows}) == 30
    assert {r['label'] for r in rows} == set(ps.STATES)
    assert any('Traceback' in r['tail'] and r['label'] != 'crashed' for r in rows)
    assert any('expired' in r['tail'] and r['label'] != 'login-expired' for r in rows)


def test_real_worker_bad_client_is_bounded_and_sanitized(tmp_path, monkeypatch):
    client = tmp_path / 'jev.py'
    client.write_text('raise RuntimeError("sensitive provider error")')
    monkeypatch.setenv('SHANTY_JEV_CLIENT', str(client))
    with pytest.raises(ps.JevUnavailable) as caught:
        ps.classify({'p0': 'sensitive capture'})
    assert 'sensitive' not in str(caught.value)


def test_human_view_groups_stuck_and_shows_mechanical(tmp_path, monkeypatch, capsys):
    args, _ = args_and_panes(tmp_path, monkeypatch)
    args.json = False
    monkeypatch.setattr(ps, 'classify', lambda captures: ps.decide(Client('login-expired'), captures))
    assert cli._cmd_crew(args) == 0
    out = capsys.readouterr().out
    assert 'inferred advisory only' in out and 'busy' in out
    assert 'login-expired: local/active, local/idle' in out
    assert 'model=jev-latest' in out


def test_peer_is_included_and_never_recursively_polled(tmp_path, monkeypatch, capsys):
    args, _ = args_and_panes(tmp_path, monkeypatch)
    args.local = False
    (tmp_path / 'shantytown.toml').write_text(
        '[host]\nname="desktop"\n[host.peers.laptop]\nssh="user@example.com"\nroot="/tmp/root"\n')
    monkeypatch.setattr(ps, 'classify', lambda captures: ps.decide(Client(), captures))
    monkeypatch.setattr(ps, 'read_peer', lambda peer: dict(inference={}, errors=[], agents=[
        dict(host=peer.name, name='remote', state='up', work='idle', posture='auto',
             pane_state=ps._advice('login-expired'))]))
    assert cli._cmd_crew(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['scope'] == 'fleet'
    assert any(r['name'] == 'remote' for r in result['agents'])


def test_parser_opt_in():
    args = cli.build_parser().parse_args(['crew', '--pane-state', '--local', '--json'])
    assert args.pane_state and args.local and args.json


def test_planted_secrets_and_identifiers_never_reach_client(tmp_path, monkeypatch, capsys):
    import hashlib
    args, panes = args_and_panes(tmp_path, monkeypatch)
    token = 'ghp_' + 'x' * 36
    host = 'database.' + 'svc'
    private_ip = '.'.join(['192', '168', '20', '31'])
    home = '/home/' + 'example/private/project'
    auth = 'small-auth-value'
    raw = (f'token={token}\npassword=fixture-secret\nAuthorization: Bearer {auth}\n'
           f'connect {host} {private_ip} {home}\n' + BUSY_SCREEN)
    panes._screens['p0'] = raw
    client = Client()
    monkeypatch.setattr(ps, 'classify', lambda c: ps.decide(client, c))
    assert cli._cmd_crew(args) == 0
    payload = json.dumps(client.calls)
    for secret in (token, 'fixture-secret', host, private_ip, home, auth):
        assert secret not in payload
    assert 'Envisioning' in payload  # redaction did not erase the state evidence
    result = json.loads(capsys.readouterr().out)
    active = next(r for r in result['agents'] if r['name'] == 'active')
    assert active['pane_state']['capture_sha256'] == hashlib.sha256(ps.tail(raw).encode()).hexdigest()
    assert active['pane_state']['capture_sha256'] != hashlib.sha256(raw.encode()).hexdigest()


def test_masker_missing_refuses_before_client(tmp_path, monkeypatch):
    monkeypatch.setenv('SHANTY_PANE_MASKER', str(tmp_path / 'missing.py'))
    client = Client()
    with pytest.raises(ps.JevUnavailable):
        ps.decide(client, {'p0': BUSY_SCREEN})
    assert client.calls == []


@pytest.mark.parametrize('code', ['raise RuntimeError("sensitive")', 'return None'])
def test_masker_failure_never_falls_back(credential_masker, code):
    credential_masker.write_text('def mask(text):\n    ' + code + '\n')
    client = Client()
    with pytest.raises(ps.JevUnavailable):
        ps.decide(client, {'p0': 'secret capture'})
    assert client.calls == []


def test_redaction_precedes_line_and_character_cutoff():
    # Credential-shaped long material is protected even when the header could
    # otherwise have fallen outside the selected tail.
    pem = '-----BEGIN PRIVATE KEY-----\n' + ('A' * 70 + '\n') * 80
    assert 'AAAA' not in ps.tail(pem)
    assert 'Bearer' not in ps.tail('Authorization: Bearer ' + 't' * 10000 + '\n' + BUSY_SCREEN)
    assert ps.tail('x' * 10000 + '\n' + BUSY_SCREEN).endswith(BUSY_SCREEN)


def test_classify_masks_stdin_before_worker(monkeypatch):
    client = Client()
    def run(argv, **kw):
        assert 'fixture-secret' not in kw['input']
        assert '<masked>' in kw['input']
        return SimpleNamespace(returncode=0, stdout=json.dumps(ps.decide(client, json.loads(kw['input']))))
    monkeypatch.setattr(ps.subprocess, 'run', run)
    ps.classify({'p0': 'fixture-secret\n' + BUSY_SCREEN})
    assert client.calls
