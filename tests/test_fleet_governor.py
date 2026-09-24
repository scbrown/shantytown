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
    return fg.snapshot(host, {'base': gov.Governor(policy, reader)}, agents or [], now=at, hosts=['desktop', 'laptop'], admission_owner='desktop')


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
    local = fg.snapshot('laptop', {}, [agent('laptop', 'local')], admission_owner='desktop')
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
    assert 'peer occupancy or agreement UNKNOWN' in fleet.admits_launch('claude')
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
        '[host]\nname="laptop"\nadmission_owner="desktop"\n[host.peers.desktop]\nssh="user@example.test"\nroot="/tmp/root"\n')
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
    assert 'peer occupancy or agreement UNKNOWN' in gate(None, 'local')


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
    with fg.admission_lock(tmp_path, 'desktop', {'laptop': peer}, owner='desktop'):
        result = subprocess.run([sys.executable, '-c', script, str(tmp_path)])
        assert result.returncode == 7
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)])
    assert result.returncode == 0


def test_tend_entrypoint_holds_down_agent_at_remote_cap(tmp_path, monkeypatch, capsys):
    from test_tend import _Args as TendArgs, _Panes as TendPanes, _roster as tend_roster
    root = tend_roster(tmp_path, {'local': {'role': 'worker', 'pane': 'local-pane'}})
    (root / 'shantytown.toml').write_text(
        '[host]\nname="desktop"\nadmission_owner="desktop"\n[host.peers.laptop]\nssh="user@example.test"\nroot="/tmp/root"\n')
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


@pytest.mark.parametrize('owner', ['desktop', 'z-server'])
def test_remote_lock_uses_same_authority_file_and_releases_on_exception(tmp_path, monkeypatch, owner):
    import subprocess
    import fcntl
    original = subprocess.Popen
    def launch(argv, **kw):
        assert argv[0] == 'ssh' and argv[-2] == 'user@example.test'
        return original(['/bin/sh', '-c', argv[-1]], **kw)
    monkeypatch.setattr(fg.subprocess, 'Popen', launch)
    peer = HostPeer(owner, 'user@example.test', str(tmp_path))
    with pytest.raises(RuntimeError, match='caller failed'):
        with fg.admission_lock('/unused', 'laptop', {owner: peer}, owner=owner):
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
        with fg.admission_lock(tmp_path, 'laptop', {'desktop': peer}, owner='desktop'):
            pytest.fail('launch authorized without authority')
    assert not (tmp_path / 'governor' / 'admission.lock').exists()


def test_asymmetric_membership_cannot_choose_two_lock_authorities(tmp_path):
    local = sample()
    peer = sample('laptop')
    peer['hosts'] = ['another', 'desktop', 'laptop']
    f = fg.FleetGovernor(local, [(peer, '')], tmp_path)
    assert 'host membership disagrees' in f.admits_launch('claude')


def test_explicit_local_owner_ignores_alphabetically_earlier_peer(tmp_path, monkeypatch):
    monkeypatch.setattr(fg.subprocess, 'Popen', lambda *a, **k: pytest.fail('contacted sleeping peer'))
    peer = HostPeer('a-laptop', 'unused', '/unused')
    with fg.admission_lock(tmp_path, 'z-server', {'a-laptop': peer}, owner='z-server'):
        assert (tmp_path / 'governor' / 'admission.lock').is_file()


@pytest.mark.parametrize('owner', [None, 'not-declared'])
def test_multi_host_owner_must_be_deliberate_and_known(tmp_path, monkeypatch, owner):
    monkeypatch.setattr(fg.subprocess, 'Popen', lambda *a, **k: pytest.fail('contacted peer'))
    peer = HostPeer('laptop', 'unused', '/unused')
    with pytest.raises(fg.AdmissionUnavailable):
        with fg.admission_lock(tmp_path, 'server', {'laptop': peer}, owner=owner):
            pytest.fail('admitted without a declared authority')
    assert not (tmp_path / 'governor').exists()


@pytest.mark.parametrize('host', [None, 'standalone'])
def test_single_host_needs_no_authority_configuration(tmp_path, monkeypatch, host):
    monkeypatch.setattr(fg.subprocess, 'Popen', lambda *a, **k: pytest.fail('contacted peer'))
    with fg.admission_lock(tmp_path, host, {}):
        pass


@pytest.mark.parametrize('peer_owner', [None, 'laptop'])
def test_old_or_disagreeing_peer_authority_holds_launch_and_dispatch(tmp_path, peer_owner):
    local, peer = sample(), sample('laptop')
    if peer_owner is None:
        del peer['admission_owner']  # an old binary, not an empty new config
    else:
        peer['admission_owner'] = peer_owner
    fleet = fg.FleetGovernor(local, [(peer, '')], tmp_path)
    assert 'admission authority' in fleet.admits_launch('claude')
    assert 'admission authority' in fleet.fallback()


def test_config_loads_explicit_authority(tmp_path):
    from shantytown import config
    (tmp_path / 'shantytown.toml').write_text(
        '[host]\nname="z-server"\nadmission_owner="z-server"\n'
        '[host.peers.a-laptop]\nssh="user@example.test"\nroot="/tmp/root"\n')
    assert config.load(tmp_path).host_admission_owner == 'z-server'


@pytest.mark.parametrize('owner', ['""', '42', 'false', '"unknown"'])
def test_config_rejects_invalid_authority(tmp_path, owner):
    from shantytown import config
    (tmp_path / 'shantytown.toml').write_text(
        f'[host]\nname="server"\nadmission_owner={owner}\n')
    with pytest.raises(config.ConfigError, match='admission_owner'):
        config.load(tmp_path)


def offline_fleet(tmp_path, monkeypatch, local=None, declared=None, now=None):
    peers = {'laptop': HostPeer('laptop', 'user@example.test', '/remote')}
    def disconnected(*args, **kwargs):
        raise ConnectionRefusedError('peer asleep')
    monkeypatch.setattr(fg.subprocess, 'run', disconnected)
    return fg.FleetGovernor(local or sample(), fg.collect(peers), tmp_path,
                            peer_config=peers, declared_agents=declared or [], now=now)


def cache_peer(tmp_path, peer):
    config = {'laptop': HostPeer('laptop', 'user@example.test', '/remote')}
    return fg.FleetGovernor(sample(), [(peer, '')], tmp_path,
                            peer_config=config, declared_agents=[])


def test_owner_admits_with_cached_count_after_collect_connection_error(tmp_path, monkeypatch):
    import time
    now = time.time()
    cache_peer(tmp_path, sample('laptop', at=now - 600,
                               agents=[agent('laptop', 'remote')]))
    fleet = offline_fleet(tmp_path, monkeypatch, now=now)
    assert fleet.errors == []
    assert fleet.counts() == {'base': 1}
    assert fleet.admits_launch('claude') == ''
    assert '3/2' in fleet.admits_launch('claude', [agent('desktop', 'local')])
    assert fleet.warnings == ['laptop unreachable; cached count age 600s; 1 counted live']
    # Cached readings cannot win freshest-observation selection or get restamped.
    reader = fleet.governors['base'].reader
    assert reader.provenance == {'five_hour': 'desktop'}
    assert reader.read_all()['five_hour'].at > now - 600


def test_owner_counts_never_seen_peer_cards_live(tmp_path, monkeypatch):
    fleet = offline_fleet(tmp_path, monkeypatch, declared=[
        agent('laptop', 'one', live=False), agent('laptop', 'two', live=False),
        agent('other', 'unrelated')])
    assert fleet.errors == []
    assert fleet.counts() == {'base': 2}
    assert '3/2' in fleet.admits_launch('claude')
    assert 'never seen; count age unknown; 2 declared cards counted live' in fleet.warnings[0]


@pytest.mark.parametrize('disagreement', ['policy', 'hosts', 'owner'])
def test_disagreeing_peer_holds_even_when_later_unreachable(tmp_path, monkeypatch, disagreement):
    peer = sample('laptop')
    if disagreement == 'policy':
        peer['governors']['base']['policy']['max_agents'] = 10
    elif disagreement == 'hosts':
        peer['hosts'] = ['laptop']
    else:
        peer['admission_owner'] = 'laptop'
    live = cache_peer(tmp_path, peer)
    assert live.errors and live.admits_launch('claude')
    offline = offline_fleet(tmp_path, monkeypatch)
    assert offline.errors and offline.admits_launch('claude')
    assert 'disagree' in offline.fallback()


@pytest.mark.parametrize('age', [fg.PEER_COUNT_MAX_AGE + 1, -31])
def test_expired_or_future_cache_holds_instead_of_using_declared_cards(tmp_path, monkeypatch, age):
    import time
    now = time.time()
    cache_peer(tmp_path, sample('laptop', at=now - age))
    fleet = offline_fleet(tmp_path, monkeypatch, now=now)
    assert 'cached count unusable' in fleet.admits_launch('claude')
    assert 'never seen' not in str(fleet.warnings)


@pytest.mark.parametrize('owner', [None, 'laptop'])
def test_offline_fallback_is_only_for_explicit_local_owner(tmp_path, monkeypatch, owner):
    local = sample()
    local['admission_owner'] = owner
    fleet = offline_fleet(tmp_path, monkeypatch, local=local)
    assert 'unreachable' in fleet.admits_launch('claude')
    assert not fleet.warnings


@pytest.mark.parametrize('bad_local', ['absent', 'stale', 'failed'])
def test_offline_fallback_requires_usable_local_usage(tmp_path, monkeypatch, bad_local):
    local = sample()
    if bad_local == 'absent':
        local['governors'] = {}
    else:
        reading = local['governors']['base']['readings']['five_hour']
        reading.update(at=0) if bad_local == 'stale' else reading.update(ok=False)
    fleet = offline_fleet(tmp_path, monkeypatch, local=local)
    assert 'requires' in fleet.admits_launch('claude')


def test_invalid_reachable_snapshot_is_not_offline_permission(tmp_path, monkeypatch):
    peers = {'laptop': HostPeer('laptop', 'user@example.test', '/remote')}
    monkeypatch.setattr(fg.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout='not-json', stderr=''))
    results = fg.collect(peers)
    assert not isinstance(results[0][1], fg.PeerUnreachable)
    fleet = fg.FleetGovernor(sample(), results, tmp_path,
                             peer_config=peers, declared_agents=[])
    assert fleet.admits_launch('claude')
    offline = offline_fleet(tmp_path, monkeypatch)
    assert 'last reachable census was invalid' in offline.admits_launch('claude')


def test_corrupt_cache_does_not_mean_never_seen(tmp_path, monkeypatch):
    cache_peer(tmp_path, sample('laptop'))
    next((tmp_path / 'governor' / 'peers').glob('*.json')).write_text('broken')
    fleet = offline_fleet(tmp_path, monkeypatch)
    assert 'cached count unusable' in fleet.admits_launch('claude')


def test_ssh_transport_exit_is_distinct_from_remote_command_failure(monkeypatch):
    peer = HostPeer('laptop', 'user@example.test', '/remote')
    for code in (255, 1):
        monkeypatch.setattr(fg.subprocess, 'run', lambda *a, **k:
                            SimpleNamespace(returncode=code, stdout='', stderr='secret'))
        _, error = fg.read_peer(peer)
        assert isinstance(error, fg.PeerUnreachable) == (code == 255)
        assert 'secret' not in error


def test_cli_owner_offline_cards_warnings_and_dispatch(tmp_path, monkeypatch, capsys):
    from shantytown import config
    root = _roster(tmp_path, {'local': 'local-pane'})
    (root / 'shantytown.toml').write_text(
        '[host]\nname="desktop"\nadmission_owner="desktop"\n'
        '[host.peers.laptop]\nssh="user@example.test"\nroot="/remote"\n')
    for name, retired in [('remote', False), ('retired', True)]:
        (root / 'crew' / (name + '.json')).write_text(json.dumps(
            dict(role='worker', host='laptop', harness='claude', retired=retired)))
    cfg = config.load(root)
    policy = gov.Policy(tiers=(gov.Tier(at=50, min_priority=1),), max_agents=3)
    import time
    local = gov.Governor(policy, FreshestReader({
        'desktop': {'five_hour': Reading(pct=10, at=time.time())}}))
    monkeypatch.setattr(cli, '_local_governors', lambda a: (cfg, {'base': local}))
    monkeypatch.setattr(cli, 'Tmux', lambda *a, **k: _Panes({'local-pane': IDLE_SCREEN}))
    def disconnected(*args, **kwargs):
        raise ConnectionRefusedError('asleep')
    monkeypatch.setattr(fg.subprocess, 'run', disconnected)
    args = _Args(root)
    args.governor = args.json = True
    assert cli._cmd_crew(args) == cli.OK
    output = json.loads(capsys.readouterr().out)
    assert output['errors'] == []
    assert output['governors']['base']['live'] == 2
    assert '1 declared cards counted live' in output['warnings'][0]
    assert [r['name'] for r in output['agents'] if r.get('estimated')] == ['remote']
    card = next(c for c in cli._registry(args).all().exact() if c.name == 'local')
    assert cli._account_launch_refusal(args, card) == ''
    gate = cli._dispatch_gate(args)
    assert gate is None or not gate(None, 'local')
    args.json = False
    assert cli._cmd_crew(args) == cli.OK
    output = capsys.readouterr().out
    assert 'laptop unreachable' in output
    assert 'counted live (offline estimate)' in output


def test_replacement_at_cap_reuses_only_its_local_slot(tmp_path, monkeypatch):
    args = fleet_setup(tmp_path, monkeypatch)
    card = cli._registry(args).all().exact()[0]
    assert '3/2' in cli._account_launch_refusal(args, card)
    assert cli._account_launch_refusal(args, card, replacing=True) == ''
    monkeypatch.setattr(cli, '_governor_agents', lambda a: [
        agent('laptop', 'local'), agent('laptop', 'extra')])
    assert '3/2' in cli._account_launch_refusal(args, card, replacing=True)


@pytest.mark.parametrize('mode', ['respawn', 'relaunch'])
def test_cycle_admission_refusal_never_stops_session(tmp_path, monkeypatch, mode):
    from shantytown import cycle
    from contextlib import contextmanager
    args = fleet_setup(tmp_path, monkeypatch)
    card = cli._registry(args).all().exact()[0]
    monkeypatch.setattr(cli, '_governor_agents', lambda a: [
        agent('laptop', 'local'), agent('laptop', 'extra')])
    monkeypatch.setattr(cli, '_window_launch_gate', lambda a: None)
    monkeypatch.setattr(cli, '_foreign_session_refusal', lambda *a: '')
    monkeypatch.setattr(cli, '_capture_history_before_kill', lambda *a: None)
    monkeypatch.setattr(cli, '_cmd_stop', lambda *a: pytest.fail('stopped live agent'))
    monkeypatch.setattr(cli, '_refresh_clone', lambda *a, **k: pytest.fail('mutated workspace'))
    held = []
    @contextmanager
    def lock(*a, **kw):
        assert not held, 'nested admission lock'
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr(fg, 'admission_lock', lock)
    panes = SimpleNamespace(exists=lambda s: True)
    chosen = cycle.Plan(getattr(cycle, mode.upper()), 'fixture')
    rc, _ = cli._perform_cycle(args, card, card.name, 'fixture', panes, None,
                               chosen, SimpleNamespace(checkpoint='saved'))
    assert rc == cli.REFUSED
    assert not held and not hasattr(args, '_cycle_admission_locked')


def test_cycle_holds_admission_lock_from_preflight_through_relaunch(tmp_path, monkeypatch):
    from shantytown import cycle
    from contextlib import contextmanager
    args = fleet_setup(tmp_path, monkeypatch)
    card = cli._registry(args).all().exact()[0]
    monkeypatch.setattr(cli, '_window_launch_gate', lambda a: None)
    held, actions = [], []
    @contextmanager
    def lock(*a, **kw):
        assert not held
        held.append(True)
        try:
            yield
        finally:
            held.pop()
    monkeypatch.setattr(fg, 'admission_lock', lock)
    def stop(a):
        assert held
        actions.append('stop')
        monkeypatch.setattr(cli, '_governor_agents', lambda a: [])
        return cli.OK
    monkeypatch.setattr(cli, '_cmd_stop', stop)
    admitted = cli._launch_admitted
    def launch(a, card, panes, runtime, **kw):
        assert held
        if kw.get('dry_run'):
            actions.append('preflight')
            return admitted(a, card, panes, runtime, **kw)
        assert cli._account_launch_refusal(a, card) == ''
        actions.append('launch')
        return cli.OK
    monkeypatch.setattr(cli, '_launch_admitted', launch)
    panes = SimpleNamespace(exists=lambda s: True)
    runtime = SimpleNamespace(compose=lambda card: 'fixture launch')
    rc, mode = cli._perform_cycle(args, card, card.name, 'fixture', panes, runtime,
        cycle.Plan(cycle.RELAUNCH, 'fixture'), SimpleNamespace(checkpoint='saved'))
    assert rc == cli.OK and mode == cycle.RELAUNCH
    assert actions == ['preflight', 'stop', 'launch']
    assert not held
