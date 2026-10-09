"""Named account identity, independent meters and failover admission controls."""
from dataclasses import replace
from pathlib import Path
import json

import pytest

from shantytown import accounts, config, governor as gov
from shantytown.files import FilesRegistry
from shantytown.protocols import Agent


def raw(program='claude', *, pct=20, cap=2, **extra):
    return dict(harness=program, model='model-' + program,
                credential_ref='infisical:ACCOUNT_AUTH',
                governor=dict(source='stub', stub_pct=pct, max_agents=cap,
                              tier=[dict(at=80, min_priority=0),
                                    dict(at=95, action='drain')]), **extra)


def cfg():
    return config.Config(accounts=accounts.parse({
        'primary': raw(pct=99), 'backup': raw('codex'), 'third': raw(pct=30)}))


def verdicts(c):
    return {n: gov.Governor(a.governor, accounts.reader_for(a)).evaluate(persist=False)
            for n, a in c.accounts.items()}


def test_accounts_parse_into_independent_policy_and_identity(tmp_path):
    root = tmp_path / '.shanty'
    root.mkdir()
    root.joinpath('shantytown.toml').write_text('''
[accounts.team]
harness = "codex"
model = "model-codex"
credential_ref = "infisical:TEAM_AUTH"
default = true
[accounts.team.governor]
source = "stub"
stub_pct = 20
max_agents = 2
[[accounts.team.governor.tier]]
at = 95
action = "drain"
''')
    c = config.load(root)
    assert accounts.selected(Agent('alice', harness='codex'), c).name == 'team'
    assert c.accounts['team'].governor.max_agents == 2
    assert c.governor.by_harness == {}


@pytest.mark.parametrize('changes', [
    {'credential_ref': 'actual-secret-value'}, {'credential_ref': '{"token":"x"}'},
    {'credential_ref': 'env:X\ninjected'}, {'harness': 'unknown'}, {'model': ''},
    {'default': 'yes'}, {'unknown': 'ignored'},
    {'governor': {'source': 'stub', 'max_agents': 2}},
    {'governor': {'source': 'stub', 'tier': [{'at': 95, 'action': 'drain'}]}},
])
def test_invalid_account_is_refused_before_any_effect(changes):
    value = raw()
    value.update(changes)
    with pytest.raises((accounts.AccountError, gov.GovernorError)):
        accounts.parse({'team': value})


def test_duplicate_default_and_meter_refuse():
    with pytest.raises(accounts.AccountError, match='duplicate default'):
        accounts.parse({'one': raw(default=True), 'two': raw(default=True)})
    one = raw()
    one['governor'].update(source='textfile', path='/tmp/operator-meter')
    with pytest.raises(accounts.AccountError, match='usage coordinates'):
        accounts.parse({'one': one, 'two': one})


def test_switch_resets_old_provider_model_and_preserves_agent_work():
    c = cfg()
    old = Agent('alice', harness='claude', model='old-claude-model',
                account='primary', workspace='/work/alice', reports_to='lead')
    new = accounts.switch(old, c.accounts['backup'], cfg=c, auto_failover=True)
    assert (new.account, new.harness, new.model) == ('backup', 'codex', 'model-codex')
    assert (new.workspace, new.reports_to, new.auto_failover) == ('/work/alice', 'lead', True)
    assert old.account == 'primary'


def test_role_pin_cannot_be_bypassed_by_account_switch():
    c = replace(cfg(), harness_required_by_role={'lead': 'claude'})
    with pytest.raises(accounts.AccountError, match='pin'):
        accounts.switch(Agent('alice', role='lead'), c.accounts['backup'], cfg=c)


def test_projection_preserves_account_and_explicit_false(tmp_path):
    reg = FilesRegistry(tmp_path)
    reg.set(Agent('alice', account='primary', auto_failover=True))
    reg.set(Agent('alice', role='lead'))
    assert (reg.get('alice').account, reg.get('alice').auto_failover) == ('primary', True)
    reg.set(replace(reg.get('alice'), auto_failover=False))
    assert reg.get('alice').auto_failover is False
    assert 'credential_ref' not in json.loads(tmp_path.joinpath('alice.json').read_text())


def test_failover_compares_all_accounts_but_never_borrows_missing_headroom():
    c = cfg()
    card = Agent('alice', harness='claude', account='primary', auto_failover=True)
    vs = verdicts(c)
    assert accounts.receiver(card, c, vs, {}).name == 'backup'
    assert accounts.receiver(card, c, vs, {'backup': 2}).name == 'third'
    assert accounts.receiver(card, c, vs, {'backup': 2, 'third': 2}) is None
    vs['backup'] = replace(vs['backup'], signal_lost=True)
    vs['third'] = replace(vs['third'], frozen=True)
    assert accounts.receiver(card, c, vs, {}) is None
    assert accounts.receiver(card, c, {}, {}) is None


def test_p0_only_is_failover_trigger_but_lost_or_ordinary_floor_is_not():
    c = cfg()
    vs = verdicts(c)
    assert accounts.constrained(vs['primary'])
    assert not accounts.constrained(replace(vs['primary'], signal_lost=True))
    assert not accounts.constrained(vs['backup'])


def test_named_meter_cannot_read_other_accounts_max(monkeypatch):
    original = gov.PrometheusReader('https://metrics.example')
    samples = [('usage', {'account': 'one', 'window': 'five_hour'}, 99),
               ('usage', {'account': 'two', 'window': 'five_hour'}, 10),
               ('usage_max', {'window': 'five_hour'}, 99)]
    monkeypatch.setattr(gov.PrometheusReader, '_samples', lambda self: (samples, None))
    scoped = accounts.ScopedPrometheusReader(original, 'two')
    assert scoped._samples() == ([samples[1]], None)
    absent = accounts.ScopedPrometheusReader(original, 'missing')
    assert absent._samples() == ([], None)


def test_legacy_deployment_has_no_account_effect():
    c = config.Config()
    card = Agent('alice', harness='codex', model='old')
    assert accounts.selected(card, c) is None
    assert accounts.lane(card, c) == 'codex'


def test_native_auth_projects_once_and_never_overwrites_refresh(tmp_path, monkeypatch):
    from shantytown import account_auth
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    account = replace(cfg().accounts['backup'], credential_ref='env:TEST_NATIVE_AUTH')
    home = account_auth.initialize(tmp_path / 'store', account,
                                   environ={'TEST_NATIVE_AUTH': '{"fake_token":"fixture"}'})
    auth = home / 'auth.json'
    assert auth.stat().st_mode & 0o777 == 0o600
    assert home.stat().st_mode & 0o777 == 0o700
    auth.write_text('{"fake_token":"refreshed-fixture"}')
    account_auth.initialize(tmp_path / 'store', account, environ={})
    assert json.loads(auth.read_text())['fake_token'] == 'refreshed-fixture'
    other = account_auth.profile(tmp_path / 'store', cfg().accounts['third'])
    assert other != home


def test_auth_refusal_never_echoes_resolver_output(tmp_path, monkeypatch, capsys):
    from shantytown import account_auth
    from types import SimpleNamespace
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    def refused(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout='secret-fixture', stderr='secret-fixture')
    with pytest.raises(accounts.AccountError) as exc:
        account_auth.initialize(tmp_path, cfg().accounts['backup'], run=refused)
    assert 'secret-fixture' not in str(exc.value)
    assert capsys.readouterr().out == ''


def test_codex_settings_projection_is_account_specific_and_explicit(tmp_path, monkeypatch):
    from shantytown import account_auth
    c = cfg()
    monkeypatch.setattr(config, 'load', lambda root: c)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    original = tmp_path / 'base' / 'config.toml'
    original.parent.mkdir()
    original.write_text('model = "fixture"\n')
    card = Agent('alice', harness='codex', account='backup')
    dest = Path(account_auth.settings_path(card, str(original), tmp_path / 'store'))
    assert not dest.exists(), 'composition must not create account settings'
    account_auth.settings_path(card, str(original), tmp_path / 'store', prepare=True)
    assert dest.read_text() == original.read_text()
    assert (dest.parent / 'auth.json').is_symlink()
    assert not (original.parent / 'auth.json').exists()
    command = account_auth.wrap(card, 'codex fixture', tmp_path / 'store')
    assert 'SHANTY_ACCOUNT=backup' in command
    assert 'ACCOUNT_AUTH' not in command
    assert '-u OPENAI_API_KEY' in command


def test_account_cli_one_command_default_model_and_dryrun(tmp_path, monkeypatch):
    from shantytown import cli
    c = cfg()
    root = tmp_path / 'store'
    reg = FilesRegistry(root / 'crew')
    reg.set(Agent('alice', harness='claude', account='primary', model='old-model'))
    monkeypatch.setattr(config, 'load', lambda root: c)
    monkeypatch.setattr(cli, '_registry', lambda a: reg)
    from shantytown.tmux import NullPanes
    monkeypatch.setattr(cli, '_panes', lambda a: NullPanes())
    args = ['--root', str(root), '--backend', 'files', 'agent', 'account',
            'alice', 'backup', '--auto-failover']
    before = (root / 'crew' / 'alice.json').read_bytes()
    assert cli.main(args + ['--dry-run']) == cli.OK
    assert (root / 'crew' / 'alice.json').read_bytes() == before
    assert cli.main(args) == cli.OK
    card = reg.get('alice')
    assert (card.account, card.harness, card.model, card.auto_failover) == (
        'backup', 'codex', 'model-codex', True)


def test_failover_sweep_requires_optin_and_preserves_busy_request(tmp_path, monkeypatch):
    from shantytown import cli, account_switch
    from types import SimpleNamespace
    from shantytown.tmux import NullPanes
    c = cfg()
    reg = FilesRegistry(tmp_path / 'crew')
    reg.set(Agent('alice', harness='claude', account='primary'))
    args = SimpleNamespace(root=tmp_path, dry_run=False)
    monkeypatch.setattr(cli, '_registry', lambda a: reg)
    monkeypatch.setattr(cli, '_catalog', lambda a: None)
    monkeypatch.setattr(cli, '_live_by_governor', lambda *args: {})
    seen = []
    monkeypatch.setattr(cli, '_serve_account_switch', lambda a, card, req: seen.append(req) or None)
    assert cli._account_switch_sweep(args, reg.all().exact(), c, verdicts(c), NullPanes(live=set())) == set()
    assert seen == []
    reg.set(replace(reg.get('alice'), auto_failover=True))
    assert cli._account_switch_sweep(args, reg.all().exact(), c, verdicts(c), NullPanes(live=set())) == {'alice'}
    req = account_switch.Requests(tmp_path).get('alice')
    assert req['desired']['account'] == 'backup'
    assert reg.get('alice').account == 'primary'
    assert cli._account_switch_sweep(args, reg.all().exact(), c, {}, NullPanes(live=set())) == set()
    assert account_switch.Requests(tmp_path).get('alice') is None


def test_completed_failover_notices_once_and_dryrun_never_delivers(tmp_path, monkeypatch):
    from shantytown import cli, account_switch
    from types import SimpleNamespace
    from shantytown.tmux import NullPanes
    c = cfg()
    reg = FilesRegistry(tmp_path / 'crew')
    card = Agent('alice', harness='codex', account='backup', auto_failover=True, reports_to='lead')
    reg.set(card)
    store = account_switch.Requests(tmp_path)
    req = store.request(card, c.accounts['backup'], automatic=True, source='primary')
    store.put(dict(req, phase='completed'))
    monkeypatch.setattr(cli, '_registry', lambda a: reg)
    monkeypatch.setattr(cli, '_live_by_governor', lambda *args: {})
    monkeypatch.setattr(cli, '_me', lambda a: 'supervisor')
    sent = []
    monkeypatch.setattr(cli, '_inbox', lambda *args, **kwargs: SimpleNamespace(
        deliver=lambda *args, **kwargs: sent.append(args) or SimpleNamespace(id='receipt')))
    args = SimpleNamespace(root=tmp_path, dry_run=True)
    cli._account_switch_sweep(args, [card], c, verdicts(c), NullPanes())
    assert sent == []
    args.dry_run = False
    for _ in range(2):
        cli._account_switch_sweep(args, [card], c, verdicts(c), NullPanes())
    assert len(sent) == 1 and sent[0][0] == 'lead'
    assert store.get('alice')['notice_state'] == 'delivered'


@pytest.mark.parametrize('phase,allowed', [('queued', True), ('completed', False)])
def test_account_request_uses_stop_cancellation_guard(tmp_path, phase, allowed):
    from shantytown import cli, account_switch
    from types import SimpleNamespace
    card = Agent('alice', account='primary', harness='claude')
    store = account_switch.Requests(tmp_path)
    request = store.request(card, cfg().accounts['backup'])
    store.put(dict(request, phase=phase))
    args = SimpleNamespace(root=tmp_path, _automatic_cycle=True,
                           _account_request_id=request['id'])
    assert (cli._cycle_stop_refusal(args, 'alice') == '') is allowed
    store.clear('alice')
    assert 'cancelled' in cli._cycle_stop_refusal(args, 'alice')


def test_running_account_marker_survives_deferred_card_change(tmp_path, monkeypatch):
    from shantytown import account_state
    from shantytown.tmux import NullPanes
    c = cfg()
    monkeypatch.setattr(config, 'load', lambda root: c)
    panes = NullPanes(live={'p-alice'}, created={'p-alice': 42})
    original = Agent('alice', pane='p-alice', account='primary', harness='claude', model='source-model')
    account_state.record(tmp_path, original, c, panes)
    desired = accounts.switch(original, c.accounts['backup'], cfg=c)
    running = account_state.process_card(tmp_path, desired, c, panes)
    assert (running.account, running.harness, running.model) == ('primary', 'claude', 'source-model')
    assert desired.account == 'backup'


def test_automatic_switch_waits_for_positive_durable_checkpoint(tmp_path, monkeypatch):
    from shantytown import cli, account_switch
    from types import SimpleNamespace
    from shantytown.tmux import NullPanes
    c = cfg()
    card = Agent('alice', account='primary', harness='claude')
    req = account_switch.Requests(tmp_path).request(card, c.accounts['backup'], automatic=True)
    monkeypatch.setattr(config, 'load', lambda root: c)
    monkeypatch.setattr(cli, '_panes', lambda a: NullPanes(live=set()))
    monkeypatch.setattr(cli, '_runtime', lambda *args: object())
    monkeypatch.setattr(cli, '_cycle_anchor_bead', lambda *args: 'fixture-task')
    monkeypatch.setattr(cli, '_durable_checkpoint_gate', lambda *args: SimpleNamespace(
        ok=None, render=lambda: 'unknown fixture checkpoint'))
    called = []
    monkeypatch.setattr(cli, '_cmd_cycle', lambda args: called.append(args) or cli.OK)
    args = SimpleNamespace(root=tmp_path)
    assert cli._serve_account_switch(args, card, req) is None
    assert called == []
    monkeypatch.setattr(cli, '_durable_checkpoint_gate', lambda *args: SimpleNamespace(ok=True))
    assert cli._serve_account_switch(args, card, req) == cli.OK
    assert len(called) == 1
    assert called[0]._account_card.model == 'model-codex'
    assert account_switch.Requests(tmp_path).get('alice')['phase'] == 'completed'


def test_unexpected_auth_symlink_refuses_without_following_it(tmp_path, monkeypatch):
    from shantytown import account_auth
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    account = replace(cfg().accounts['backup'], credential_ref='env:FIXTURE')
    home = account_auth.profile(tmp_path, account)
    home.mkdir(parents=True)
    victim = tmp_path / 'other.json'
    victim.write_text('{"fixture":"preserve"}')
    (home / 'auth.json').symlink_to(victim)
    with pytest.raises(accounts.AccountError, match='unexpected symlink'):
        account_auth.initialize(tmp_path, account, environ={'FIXTURE': '{"fixture":"new"}'})
    assert victim.read_text() == '{"fixture":"preserve"}'


def test_cycle_completion_is_consumed_under_lock_before_second_server(tmp_path, monkeypatch):
    from shantytown import cli, account_switch, cycle
    from types import SimpleNamespace
    from shantytown.tmux import NullPanes
    card = Agent('alice', account='primary', harness='claude')
    store = account_switch.Requests(tmp_path)
    request = store.request(card, cfg().accounts['backup'], automatic=True)
    args = SimpleNamespace(root=tmp_path, _automatic_cycle=True,
                           _account_request_id=request['id'], _account_card=card)
    monkeypatch.setattr(cli, '_automatic_cycle_refusal', lambda *args, **kwargs: '')
    plan = cycle.Plan(cycle.RESPAWN, 'fixture private boundary')
    monkeypatch.setattr(cli, '_cycle_plan', lambda *args: plan)
    calls = []
    monkeypatch.setattr(cli, '_perform_cycle_locked', lambda *args:
                        calls.append('performed') or (cli.OK, cycle.RESPAWN))
    assert cli._perform_cycle(args, card, 'alice', 'p-alice', NullPanes(), object(), plan, object())[0] == cli.OK
    assert store.get('alice')['phase'] == 'completed'
    assert cli._perform_cycle(args, card, 'alice', 'p-alice', NullPanes(), object(), plan, object())[0] == cli.REFUSED
    assert calls == ['performed']


def test_target_credential_failure_preserves_card_and_process(tmp_path, monkeypatch):
    from shantytown import cli, account_auth, account_switch, cycle
    from types import SimpleNamespace
    from shantytown.tmux import NullPanes
    c = cfg()
    card = Agent('alice', pane='p-alice', account='primary', harness='claude')
    reg = FilesRegistry(tmp_path / 'crew')
    reg.set(card)
    before = (tmp_path / 'crew' / 'alice.json').read_bytes()
    target = accounts.switch(card, c.accounts['backup'], cfg=c)
    args = SimpleNamespace(root=tmp_path, _account_card=target,
                           _account_expected=account_switch.choice(reg.get('alice')))
    monkeypatch.setattr(config, 'load', lambda root: c)
    monkeypatch.setattr(cli, '_registry', lambda a: reg)
    monkeypatch.setattr(cli, '_launch_admitted', lambda *args, **kwargs: cli.OK)
    def fail(*args, **kwargs):
        raise accounts.AccountError('fixture missing credential')
    monkeypatch.setattr(account_auth, 'initialize', fail)
    panes = NullPanes(live={'p-alice'})
    plan = cycle.Plan(cycle.RESPAWN, 'fixture')
    assert cli._restart_cycle(args, card, 'alice', 'p-alice', panes, object(), plan, object())[0] == cli.REFUSED
    assert (tmp_path / 'crew' / 'alice.json').read_bytes() == before
    assert panes.exists('p-alice') and panes.respawned == []


def test_exact_selection_restore_removes_new_fields_and_preserves_other_metadata(tmp_path):
    reg = FilesRegistry(tmp_path)
    original = Agent('alice', harness='claude')
    reg.set(original)
    reg.set(replace(original, account='backup', harness='codex', model='target-model', auto_failover=True))
    reg.set(Agent('alice', role='lead', reports_to='administrator'))
    reg.restore_selection(original)
    restored = reg.get('alice')
    assert (restored.account, restored.model, restored.auto_failover, restored.harness) == (None, None, None, 'claude')
    assert (restored.role, restored.reports_to) == ('lead', 'administrator')


def test_late_launcher_refusal_restores_unnamed_legacy_card(tmp_path, monkeypatch):
    from shantytown import cli, account_auth, account_switch, cycle
    from types import SimpleNamespace
    from shantytown.tmux import NullPanes
    c = cfg()
    original = Agent('alice', pane='p-alice', harness='claude')
    reg = FilesRegistry(tmp_path / 'crew')
    reg.set(original)
    current = reg.get('alice')
    before = (tmp_path / 'crew' / 'alice.json').read_bytes()
    target = accounts.switch(current, c.accounts['backup'], cfg=c)
    args = SimpleNamespace(root=tmp_path, _account_card=target,
                           _account_expected=account_switch.choice(current))
    monkeypatch.setattr(config, 'load', lambda root: c)
    monkeypatch.setattr(cli, '_registry', lambda a: reg)
    monkeypatch.setattr(cli, '_launch_admitted', lambda *args, **kwargs: cli.OK)
    monkeypatch.setattr(account_auth, 'initialize', lambda *args: None)
    monkeypatch.setattr(cli, '_foreign_session_refusal', lambda *args: '')
    monkeypatch.setattr(cli, '_capture_history_before_kill', lambda *args: None)
    monkeypatch.setattr(cli, '_launch', lambda *args, **kwargs: cli.REFUSED)
    panes = NullPanes(live={'p-alice'})
    plan = cycle.Plan(cycle.RESPAWN, 'fixture')
    assert cli._restart_cycle(args, current, 'alice', 'p-alice', panes, object(), plan, object())[0] == cli.REFUSED
    assert (tmp_path / 'crew' / 'alice.json').read_bytes() == before
    assert panes.exists('p-alice') and panes.respawned == []


def test_account_replacement_reaps_old_daemons_after_pane_change_before_new_runtime(tmp_path, monkeypatch):
    from shantytown import cli, codex_daemon
    from types import SimpleNamespace
    from shantytown.tmux import NullPanes
    card = Agent('account-fixture', pane='p-account-fixture', harness='claude')
    args = SimpleNamespace(root=tmp_path, _account_card=card)
    timeline = []
    panes = NullPanes(live={card.pane})
    original = panes.respawn
    def respawn(*args, **kwargs):
        original(*args, **kwargs)
        timeline.append('pane-replaced')
    monkeypatch.setattr(panes, 'respawn', respawn)
    monkeypatch.setattr(cli, '_window_launch_gate', lambda *args: None)
    monkeypatch.setattr(cli, '_account_launch_refusal', lambda *args, **kwargs: '')
    monkeypatch.setattr(cli, 'provision_ws', lambda *args, **kwargs: [])
    monkeypatch.setattr(cli, '_launched_now', lambda *args: None)
    monkeypatch.setattr(cli, '_record_launch_unretirement', lambda *args: True)
    monkeypatch.setattr(cli, '_observe_live', lambda *args: True)
    monkeypatch.setattr(cli, '_verify_live_hooks', lambda *args: cli.OK)
    monkeypatch.setattr(cli, '_deliver_startup_inbox', lambda *args: None)
    def stop_owned(agent):
        assert agent == card.name
        assert timeline == ['pane-replaced']
        timeline.append('source-daemons-stopped')
        return (123,)
    monkeypatch.setattr(codex_daemon, 'stop_owned', stop_owned)
    runtime = SimpleNamespace(compose=lambda card: 'fixture launch', settings_path=lambda card: None,
                              start=lambda *args: timeline.append('target-started'))
    assert cli._launch_admitted(args, card, panes, runtime, reuse_session=True) == cli.OK
    assert timeline == ['pane-replaced', 'source-daemons-stopped', 'target-started']
