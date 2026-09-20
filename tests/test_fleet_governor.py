"""Probe freshness is independent of receiving host and transport arrival time."""
import pytest

from shantytown.fleet_governor import FreshestReader
from shantytown.governor import GovernorError, Reading


def test_selects_each_windows_freshest_producer_observation():
    first = {'five_hour': Reading(pct=20, at=100),
             'seven_day': Reading(pct=30, at=200)}
    second = {'five_hour': Reading(pct=80, at=200),
              'seven_day': Reading(pct=90, at=100)}
    reader = FreshestReader({'desktop': first, 'laptop': second})
    assert reader.read_all() == {'five_hour': second['five_hour'],
                                 'seven_day': first['seven_day']}
    assert reader.provenance == {'five_hour': 'laptop', 'seven_day': 'desktop'}


def test_new_failure_cannot_be_hidden_by_older_success():
    failed = Reading(pct=12, at=200, ok=False, probe_http_status=429)
    reader = FreshestReader({'desktop': {'five_hour': Reading(pct=10, at=100)},
                             'laptop': {'five_hour': failed}})
    assert reader.read_all()['five_hour'] == failed
    assert reader.read_all()['five_hour'].lost(now=201, max_age=300)


def test_ties_have_same_provenance_from_either_host():
    first = {'five_hour': Reading(pct=20, at=100)}
    second = {'five_hour': Reading(pct=40, at=100)}
    one = FreshestReader({'desktop': first, 'laptop': second})
    two = FreshestReader({'laptop': second, 'desktop': first})
    assert one.read_all() == two.read_all()
    assert one.provenance == two.provenance == {'five_hour': 'desktop'}


def test_undated_observation_does_not_supplant_dated_one():
    dated = Reading(pct=80, at=100)
    reader = FreshestReader({'desktop': {'five_hour': Reading(pct=10)},
                             'laptop': {'five_hour': dated}})
    assert reader.read_all()['five_hour'] == dated
    unaged = FreshestReader({'desktop': {'five_hour': Reading(pct=10)}})
    assert unaged.read_all()['five_hour'].at is None
    assert unaged.read_all()['five_hour'].lost(now=201, max_age=300)


@pytest.mark.parametrize('stamp', [float('nan'), float('inf'), True, '200'])
def test_invalid_timestamp_refuses_instead_of_winning_freshness(stamp):
    with pytest.raises(GovernorError):
        FreshestReader({'desktop': {'five_hour': Reading(pct=10, at=stamp)}})


def test_retains_cache_age_and_does_not_refresh_stale_data_on_read():
    observation = Reading(pct=10, at=100, cache_age=5000)
    reader = FreshestReader({'desktop': {'five_hour': observation}})
    assert reader.read_all()['five_hour'] == observation
    assert reader.read_all()['five_hour'].lost(now=101, max_age=300)
    returned = reader.read_all()
    returned.clear()
    assert reader.read_all()['five_hour'] == observation


from dataclasses import replace
import json
from types import SimpleNamespace
from shantytown import cli, fleet_governor as fg, governor as gov
from shantytown.config import HostPeer
from test_crew_work import _Args, _Panes, _roster, IDLE_SCREEN


def agent(host, name, harness='claude', live=True):
    return dict(host=host, name=name, harness=harness, live=live)


def sample(host='desktop', cap=2, pct=10, at=None, agents=None):
    import time
    at = time.time() if at is None else at
    policy = gov.Policy(tiers=(gov.Tier(at=50, min_priority=1),), max_agents=cap)
    reader = FreshestReader({host: {'five_hour': Reading(pct=pct, at=at)}})
    return fg.snapshot(host, {'base': gov.Governor(policy, reader)}, agents or [], now=at)


def test_roundtrip_portable_policy_omits_reader_coordinates_and_credentials():
    policy = gov.parse({'source': 'prometheus', 'url': 'https://secret.example.test',
                        'username': 'private-user', 'password_file': '/private/token',
                        'tier': [{'at': 50, 'traits': ['support']}],
                        'burndown': [{'window': 'five_hour', 'within': 900}],
                        'pace': [{'window': 'five_hour', 'ratio': 1.1}]})
    encoded = json.dumps(fg.policy_wire(policy))
    assert 'secret' not in encoded and 'private' not in encoded
    portable = gov.parse(json.loads(encoded))
    assert portable.tiers == policy.tiers
    assert portable.burndowns == policy.burndowns and portable.paces == policy.paces
    assert portable.url is None and portable.password_file is None


def test_peer_policy_governs_host_with_no_local_policy_and_counts_both(tmp_path):
    local = fg.snapshot('laptop', {}, [agent('laptop', 'local')])
    peer = sample(agents=[agent('desktop', 'remote')])
    fleet = fg.FleetGovernor(local, [(peer, '')], tmp_path)
    assert fleet.policy_hosts == {'base': 'desktop'}
    assert fleet.counts() == {'base': 2}
    assert '3/2' in fleet.admits_launch('claude')
    assert fleet.admits_launch('claude', []) == ''


def test_both_hosts_derive_same_freshest_verdict_and_hysteresis(tmp_path):
    first = sample(at=1000, pct=51)
    second = sample('laptop', at=1001, pct=49)
    first['governors']['base']['state'] = dict(at=50, since=990, by_window={})
    a = fg.FleetGovernor(first, [(second, '')], tmp_path / 'a')
    b = fg.FleetGovernor(second, [(first, '')], tmp_path / 'b')
    for fleet in (a, b):
        fleet.governors['base']._now = lambda: 1002
    va = a.governors['base'].evaluate(persist=False)
    vb = b.governors['base'].evaluate(persist=False)
    assert va == vb
    assert va.pct == 49 and va.held and va.floor == 1


def test_failed_peer_holds_growth_but_retains_local_reading(tmp_path):
    fleet = fg.FleetGovernor(sample(), [(None, 'laptop governor unavailable')], tmp_path)
    assert fleet.governors['base'].evaluate(persist=False).pct == 10
    assert 'remote spend UNKNOWN' in fleet.admits_launch('claude')
    assert 'fallback' in fleet.fallback()


def test_disagreeing_policy_cannot_silently_choose_larger_cap(tmp_path):
    fleet = fg.FleetGovernor(sample(cap=8), [(sample('laptop', cap=2), '')], tmp_path)
    assert 'policies disagree' in fleet.admits_launch('claude')


@pytest.mark.parametrize('mutate', [
    lambda s: s.update(observed_at=0),
    lambda s: s.update(scope='fleet'),
    lambda s: s.update(complete=False),
    lambda s: s['governors']['base']['readings']['five_hour'].update(pct=float('nan')),
    lambda s: s['governors']['base']['readings']['five_hour'].update(ok=1),
    lambda s: s['governors']['base']['policy'].update(password_file='/private/token'),
    lambda s: s['agents'].append(agent('wrong-host', 'intruder')),
])
def test_invalid_peer_envelopes_refuse(mutate):
    data = sample()
    mutate(data)
    with pytest.raises((ValueError, TypeError, GovernorError)):
        fg.validate(data, 'desktop')


def test_peer_transport_is_bounded_local_and_does_not_leak_stderr(monkeypatch):
    peer = HostPeer('desktop', 'user@example.test', '/tmp/a b')
    def run(argv, **kw):
        assert kw['timeout'] == 20
        assert "--root '/tmp/a b' crew --governor --json --local" in argv[-1]
        return SimpleNamespace(returncode=0, stdout=json.dumps(sample()), stderr='')
    monkeypatch.setattr(fg.subprocess, 'run', run)
    result, error = fg.read_peer(peer)
    assert result['host'] == 'desktop' and not error
    monkeypatch.setattr(fg.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout='', stderr='credential-secret'))
    result, error = fg.read_peer(peer)
    assert result is None and 'unavailable' in error and 'secret' not in error


def fleet_setup(tmp_path, monkeypatch, peer=None):
    root = _roster(tmp_path, {'local': 'local-pane'})
    (root / 'shantytown.toml').write_text(
        '[host]\nname="laptop"\n[host.peers.desktop]\nssh="user@example.test"\nroot="/tmp/root"\n')
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **k: _Panes({'local-pane': IDLE_SCREEN}))
    monkeypatch.setattr(fg, 'collect', lambda peers: [(peer or sample(
        agents=[agent('desktop', 'remote')]), '')])
    return _Args(root)


def test_local_json_is_nonrecursive_and_peerless_policy_is_not_off(tmp_path, monkeypatch, capsys):
    args = fleet_setup(tmp_path, monkeypatch)
    args.governor = args.json = args.local = True
    monkeypatch.setattr(fg, 'collect', lambda p: pytest.fail('recursive governor poll'))
    assert cli._cmd_crew(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['scope'] == 'local' and output['governors'] == {}
    fg.validate(output, 'laptop')


def test_fleet_json_and_human_display_show_remote_agents_and_policy(tmp_path, monkeypatch, capsys):
    args = fleet_setup(tmp_path, monkeypatch)
    args.governor = args.json = True
    assert cli._cmd_crew(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['governors']['base']['live'] == 2
    assert output['governors']['base']['policy_host'] == 'desktop'
    args.json = False
    assert cli._cmd_crew(args) == 0
    output = capsys.readouterr().out
    assert 'desktop remote claude live' in output
    assert 'laptop local claude live' in output
    assert 'live 2/2' in output


def test_launch_refusal_precedes_daemon_repair_and_workspace_mutation(tmp_path, monkeypatch, capsys):
    args = fleet_setup(tmp_path, monkeypatch)
    card = cli._registry(args).all().exact()[0]
    monkeypatch.setattr(cli, '_window_launch_gate', lambda a: None)
    monkeypatch.setattr(cli, '_refresh_clone', lambda *a, **k: pytest.fail('mutated workspace'))
    assert cli._launch_admitted(args, card, None, None, dry_run=True) == cli.REFUSED
    assert '3/2' in capsys.readouterr().err


def test_account_admission_recounts_local_launches_in_same_tend_pass(tmp_path, monkeypatch):
    args = fleet_setup(tmp_path, monkeypatch, sample(cap=3, agents=[agent('desktop', 'remote')]))
    card = cli._registry(args).all().exact()[0]
    assert cli._account_launch_refusal(args, card) == ''
    monkeypatch.setattr(cli, '_governor_agents', lambda a: [
        agent('laptop', 'local'), agent('laptop', 'just-launched')])
    assert '4/3' in cli._account_launch_refusal(args, card)


def test_dispatch_holds_on_missing_peer_instead_of_no_local_governor(tmp_path, monkeypatch):
    args = fleet_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(fg, 'collect', lambda _: [(None, 'desktop governor unavailable')])
    gate = cli._dispatch_gate(args)
    assert 'remote spend UNKNOWN' in gate(None, 'local')


def test_local_admission_lock_excludes_another_process(tmp_path):
    import subprocess
    import sys
    peer = HostPeer('laptop', 'unused', '/unused')
    script = '''import fcntl, pathlib, sys
with (pathlib.Path(sys.argv[1]) / 'governor' / 'admission.lock').open('a') as f:
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(7)
'''
    with fg.admission_lock(tmp_path, 'desktop', {'laptop': peer}):
        result = subprocess.run([sys.executable, '-c', script, str(tmp_path)])
        assert result.returncode == 7
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)])
    assert result.returncode == 0


def test_tend_entrypoint_holds_down_agent_at_remote_cap(tmp_path, monkeypatch, capsys):
    from test_tend import _Args as TendArgs, _Panes as TendPanes, _roster as tend_roster
    root = tend_roster(tmp_path, {'local': {'role': 'worker', 'pane': 'local-pane'}})
    (root / 'shantytown.toml').write_text(
        '[host]\nname="desktop"\n[host.peers.laptop]\nssh="user@example.test"\nroot="/tmp/root"\n')
    panes = TendPanes(live=set())
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **k: panes)
    monkeypatch.setattr(fg, 'collect', lambda _: [(sample('laptop', cap=1,
        agents=[agent('laptop', 'remote')]), '')])
    args = TendArgs(root, dry_run=True)
    assert cli._tend_once(args) == cli.OK
    output = capsys.readouterr().out
    assert '2/1' in output and 'governed' in output
    assert 'would-respawn' not in output
    assert not list((root / 'governor').glob('state*.json'))


def test_remote_lock_uses_same_authority_file_and_releases_on_exception(tmp_path, monkeypatch):
    import subprocess
    import fcntl
    original = subprocess.Popen
    def launch(argv, **kw):
        assert argv[0] == 'ssh' and argv[-2] == 'user@example.test'
        return original(['/bin/sh', '-c', argv[-1]], **kw)
    monkeypatch.setattr(fg.subprocess, 'Popen', launch)
    peer = HostPeer('desktop', 'user@example.test', str(tmp_path))
    with pytest.raises(RuntimeError, match='caller failed'):
        with fg.admission_lock('/unused', 'laptop', {'desktop': peer}):
            with (tmp_path / 'governor' / 'admission.lock').open('a') as handle:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise RuntimeError('caller failed')
    with (tmp_path / 'governor' / 'admission.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_unreachable_authority_never_falls_back_to_local_lock(tmp_path, monkeypatch):
    def unavailable(*a, **k):
        raise OSError('no ssh')
    monkeypatch.setattr(fg.subprocess, 'Popen', unavailable)
    peer = HostPeer('desktop', 'user@example.test', '/remote')
    with pytest.raises(fg.AdmissionUnavailable):
        with fg.admission_lock(tmp_path, 'laptop', {'desktop': peer}):
            pytest.fail('launch authorized without authority')
    assert not (tmp_path / 'governor' / 'admission.lock').exists()
