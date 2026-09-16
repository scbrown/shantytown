"""Quiet-time contract: independent signals, one admission/slowdown decision."""
import contextlib
import io
import json
from pathlib import Path

import pytest

from shantytown import cli, config, gaming, quiet_detectors as detectors, quiet_time as quiet


def configure(root, extra=''):
    (root / 'shantytown.toml').write_text('''
[[quiet_time.detector]]
name = "media"
kind = "process"
executable = "Plex"
lift_delay = 60
''' + extra)


def process(proc, pid, *args):
    path = proc / str(pid)
    path.mkdir(parents=True, exist_ok=True)
    (path / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in args) + b'\0')


def test_signals_use_executable_and_positional_regex_not_mentions(tmp_path):
    process(tmp_path, 1, '/opt/media/Plex', '--play=42')
    process(tmp_path, 2, 'bash', '-c', 'Plex --play=42')
    process(tmp_path, 3, 'pgrep', 'Plex')
    process(tmp_path, 4, '/opt/media/PlexHelper', '--play=42')
    process(tmp_path, 5, '/opt/media/Plex', '--idle')
    assert detectors.processes(tmp_path, 'Plex', [r'--play=\d+']) == {1: {}}


def test_continuous_entry_grace_exit_staleness_and_config_change(tmp_path):
    configure(tmp_path, 'enter_delay = 60\ngrace = 180\n')
    proc = tmp_path / 'proc'
    process(proc, 1, 'Plex')
    assert not quiet.probe(tmp_path, proc=proc, now=1000).held
    assert quiet.probe(tmp_path, proc=proc, now=1060).reasons == ('media',)
    (proc / '1/cmdline').unlink()
    assert quiet.probe(tmp_path, proc=proc, now=1120).held
    assert quiet.probe(tmp_path, proc=proc, now=1180).held
    assert not quiet.probe(tmp_path, proc=proc, now=1240).held
    process(proc, 1, 'Plex')
    assert not quiet.probe(tmp_path, proc=proc, now=1300).held
    assert quiet.probe(tmp_path, proc=proc, now=1360).held
    assert quiet.read(tmp_path, now=1541).state == 'unknown'
    assert quiet.read(tmp_path, now=1359).state == 'unknown'
    configure(tmp_path, 'enter_delay = 120\n')
    assert quiet.read(tmp_path, now=1361).state == 'unknown'


def test_one_lift_cannot_cancel_other_hold_or_hide_unknown(tmp_path):
    configure(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, 'Plex')
    gaming.manual(tmp_path)
    status = quiet.probe(tmp_path, proc=proc, now=1000)
    assert status.reasons == ('gaming', 'media')
    gaming.manual(tmp_path, clear=True)
    assert quiet.read(tmp_path, now=1001).reasons == ('media',)
    gaming.manual(tmp_path)
    assert quiet.read(tmp_path, now=1200).reasons == ('gaming',)
    assert 'UNKNOWN detectors: media' in quiet.read(tmp_path, now=1200).render()
    assert 'aegis_quiet_time_probe_ok{reason="media"} 0' in quiet.metrics(quiet.read(tmp_path, now=1200))


def test_manual_works_when_detection_disabled_and_clear_preserves_auto(tmp_path):
    configure(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, 'Plex')
    quiet.probe(tmp_path, proc=proc, now=1000)
    quiet.manual(tmp_path, 'media')
    quiet.manual(tmp_path, 'media', clear=True)
    assert quiet.read(tmp_path, now=1001).held
    (quiet.folder(tmp_path, 'media') / 'disabled').touch()
    assert not quiet.read(tmp_path, now=1001).held
    quiet.manual(tmp_path, 'media')
    assert quiet.read(tmp_path, now=1001).held


@pytest.mark.parametrize('command', ['new', 'start', 'cycle'])
def test_media_alone_reaches_launch_and_feed_gates(tmp_path, monkeypatch, capsys, command):
    from shantytown.feed_check import governor_admits
    configure(tmp_path)
    quiet.manual(tmp_path, 'media')
    monkeypatch.setattr(cli, '_warn_if_no_store', lambda a: None)
    assert cli.main(['--root', str(tmp_path), command, 'worker']) == 1
    assert 'media' in capsys.readouterr().err
    assert 'media' in governor_admits(tmp_path)(None)


def test_legacy_cli_status_observes_media_too(tmp_path, monkeypatch):
    configure(tmp_path)
    quiet.manual(tmp_path, 'media')
    monkeypatch.setattr(cli, '_warn_if_no_store', lambda a: None)
    assert cli.main(['--root', str(tmp_path), 'hold', 'gaming', '--status']) == 1
    assert cli.main(['--root', str(tmp_path), 'hold', 'all', '--status']) == 1
    assert cli.main(['--root', str(tmp_path), 'hold', 'typo', '--status']) == 1


def http_spec(**options):
    return detectors.parse({'detector': [dict(name='media', kind='http_json',
        url='http://localhost/sessions', items_path='MediaContainer.Metadata',
        count_path='MediaContainer.size', state_path='Player.state',
        match={'Player.machineIdentifier': 'desktop-fixture'}, **options)]}).detectors[1]


def response(monkeypatch, body):
    class Opener:
        def open(self, request, timeout):
            return contextlib.closing(io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(detectors.urllib.request, 'build_opener', lambda *args: Opener())


@pytest.mark.parametrize('state,client,active,present', [
    ('playing', 'desktop-fixture', True, True),
    ('buffering', 'desktop-fixture', True, True),
    ('paused', 'desktop-fixture', False, True),
    ('playing', 'other-client', False, False),
    ('paused', 'other-client', False, False),
])
def test_session_states_are_scoped_to_configured_client(tmp_path, monkeypatch, state, client, active, present):
    response(monkeypatch, {'MediaContainer': {'size': 1, 'Metadata': [
        {'Player': {'machineIdentifier': client, 'state': state}}]}})
    signal = detectors.json_signal(http_spec(), tmp_path, {}, 1000)
    assert (signal.active, signal.present) == (active, present)
    assert signal.data == {'matching_sessions': int(present)}


def test_empty_is_clear_but_missing_schema_is_unknown(tmp_path, monkeypatch):
    response(monkeypatch, {'MediaContainer': {'size': 0}})
    assert not detectors.json_signal(http_spec(), tmp_path, {}, 0).active
    response(monkeypatch, {'MediaContainer': {'size': 1}})
    with pytest.raises(ValueError):
        detectors.json_signal(http_spec(), tmp_path, {}, 0)
    response(monkeypatch, {})
    with pytest.raises(KeyError):
        detectors.json_signal(http_spec(), tmp_path, {}, 0)


@pytest.mark.parametrize('lift,held', [('inactive', False), ('absent', True)])
def test_paused_lift_rule_is_configured(tmp_path, monkeypatch, lift, held):
    spec = http_spec()
    from dataclasses import replace
    spec = replace(spec, lift_rule=lift, lift_delay=0)
    monkeypatch.setitem(detectors.READERS, 'http_json', lambda *args: detectors.Signal(True, True))
    assert quiet._probe(tmp_path, spec, tmp_path, 1000).held
    monkeypatch.setitem(detectors.READERS, 'http_json', lambda *args: detectors.Signal(False, True))
    assert quiet._probe(tmp_path, spec, tmp_path, 1060).held == held
    monkeypatch.setitem(detectors.READERS, 'http_json', lambda *args: detectors.Signal(False, False))
    assert not quiet._probe(tmp_path, spec, tmp_path, 1120).held


def test_activity_unknown_then_sustained_cpu_signal(tmp_path, monkeypatch):
    spec = detectors.Detector('media', options={'executable': 'Plex', 'min_cpu_percent': 2})
    process(tmp_path, 1, 'Plex')
    monkeypatch.setattr(detectors.gaming_activity, 'observe', lambda *a: {'game_cpu': None})
    assert detectors.process_signal(spec, tmp_path, {}, 1000).active is None
    monkeypatch.setattr(detectors.gaming_activity, 'observe', lambda *a: {'game_cpu': 4})
    assert detectors.process_signal(spec, tmp_path, {}, 1060).active
    monkeypatch.setattr(detectors.gaming_activity, 'observe', lambda *a: {'game_cpu': 0})
    assert not detectors.process_signal(spec, tmp_path, {}, 1120).active


def test_credentials_and_http_error_details_never_persist(tmp_path, monkeypatch):
    spec = http_spec()
    def fail(*args):
        raise ValueError('secret-token fictional-private-title')
    monkeypatch.setitem(detectors.READERS, 'http_json', fail)
    assert quiet._probe(tmp_path, spec, tmp_path, 1000).state == 'unknown'
    assert 'secret' not in (quiet.folder(tmp_path, 'media') / 'state.json').read_text()


@pytest.mark.parametrize('row', [
    {'name': 'all'}, {'name': '../media'}, {'name': 'media', 'kind': 'typo'},
    {'name': 'media', 'kind': []}, {'name': 'media', 'executable': '['},
    {'name': 'media', 'executable': 'Plex', 'enter_delay': float('nan')},
    {'name': 'media', 'executable': 'Plex', 'lift_rule': 'typo'},
    {'name': 'media', 'executable': 'Plex', 'grace': True},
    {'name': 'media', 'executable': 'Plex', 'enabled': 'false'},
    {'name': 'media', 'executable': 'Plex', 'typo': 0},
    {'name': 'gaming', 'kind': 'steam', 'lift_rule': 'inactive'},
])
def test_bad_registry_refused(row):
    with pytest.raises(ValueError):
        detectors.parse({'detector': [row]})


def test_config_load_preserves_legacy_and_exposes_registry(tmp_path):
    assert config.load(tmp_path).quiet_time == detectors.Policy()
    configure(tmp_path)
    assert [d.name for d in config.load(tmp_path).quiet_time.detectors] == ['gaming', 'media']
    configure(tmp_path, 'typo = 1\n')
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)


def test_probe_slowdown_uses_aggregate_and_restores_only_after_last_hold(tmp_path, monkeypatch):
    from shantytown import gaming_scopes
    from types import SimpleNamespace
    configure(tmp_path)
    quiet.manual(tmp_path, 'media')
    gaming.manual(tmp_path)
    decisions = []
    monkeypatch.setattr(cli, '_warn_if_no_store', lambda a: None)
    monkeypatch.setattr(cli, '_panes', lambda a: object())
    monkeypatch.setattr(cli, '_registry', lambda a: SimpleNamespace(
        all=lambda: SimpleNamespace(exact=lambda: [])))
    monkeypatch.setattr(cli, '_gaming_advisory', lambda *a, **k: [])
    monkeypatch.setattr(gaming_scopes, 'reconcile', lambda root, held, pids: decisions.append(held) or [])
    monkeypatch.setitem(detectors.READERS, 'process', lambda *a: detectors.Signal(False))
    argv = ['--root', str(tmp_path), 'hold', 'gaming', '--probe', '--slowdown']
    assert cli.main(argv) == 0
    gaming.manual(tmp_path, clear=True)
    assert cli.main(argv) == 0
    quiet.manual(tmp_path, 'media', clear=True)
    assert cli.main(argv) == 0
    assert decisions == [True, True, False]


def test_reason_transition_reaches_coordinator_while_still_held(tmp_path, monkeypatch):
    from shantytown.creel_advisory import Alerter
    from types import SimpleNamespace
    sent = []
    monkeypatch.setattr(cli.creel_advisory_mod, 'Alerter', lambda *a, **kw:
        Alerter(*a, **kw, push=lambda r, p, text: sent.append(text) or True))
    a = SimpleNamespace(root=tmp_path)
    call = lambda status: cli._gaming_advisory(a, status, reg=object(), panes=object())
    both = quiet.Status((('gaming', gaming.Status('manual')), ('media', quiet.Observation('active'))))
    media = quiet.Status((('gaming', gaming.Status()), ('media', quiet.Observation('active'))))
    assert call(both) == ['local']
    assert call(media) == ['local']
    assert call(media) == []
    assert 'media' in sent[-1] and 'LIFTED' not in sent[-1]


def test_configured_steam_regex_and_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(gaming.gaming_activity, 'gpu_busy', lambda: None)
    spec = detectors.parse({'detector': [dict(name='gaming', kind='steam', enabled=True,
        game_executable='launcher', game_arguments=[r'--id=(?P<appid>[0-9]+)'],
        grace=0, lift_delay=0)]}).detectors[0]
    proc = tmp_path / 'proc'
    process(proc, 1, '/opt/game/launcher', '--id=42')
    assert gaming.probe(tmp_path, spec=spec, proc=proc, now=1000).held
    (proc / '1/cmdline').unlink()
    gaming.probe(tmp_path, spec=spec, proc=proc, now=1060)
    assert not gaming.probe(tmp_path, spec=spec, proc=proc, now=1120).held


@pytest.mark.parametrize('other', ['unknown', 'off'])
def test_healthy_clear_detector_keeps_aggregate_usable(tmp_path, monkeypatch, capsys, other):
    configure(tmp_path)
    # The cold-start deployment window: gaming has a fresh clear observation,
    # media has no observation yet. Existing scheduled consumers need rc=0.
    monkeypatch.setattr(gaming, 'read', lambda *a, **k: gaming.Status('clear', observed=1000))
    monkeypatch.setattr(quiet, '_read', lambda *a, **k: quiet.Observation(other))
    status = quiet.read(tmp_path, now=1000)
    assert status.state == 'clear' and not status.held
    assert 'aegis_quiet_time_probe_ok{reason="media"} 0' in quiet.metrics(status)
    if other == 'unknown':
        assert 'UNKNOWN detectors: media' in status.render()
    monkeypatch.setattr(cli, '_warn_if_no_store', lambda a: None)
    assert cli.main(['--root', str(tmp_path), 'hold', 'gaming', '--status']) == 0


def test_no_healthy_observation_still_reports_aggregate_unknown():
    status = quiet.Status((('gaming', gaming.Status('unknown')), ('media', quiet.Observation('unknown'))))
    assert status.state == 'unknown' and not status.held
    assert quiet.Status((('gaming', gaming.Status()), ('media', quiet.Observation('unknown')))).state == 'unknown'
