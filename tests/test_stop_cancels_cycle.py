"""A queued self-cycle must never undo an operator's deliberate stop."""
from unittest.mock import Mock

import pytest

from shantytown import agent_hold, cli, cycle
from tests.test_agent_hold import world, args


@pytest.mark.parametrize('after_turn', [False, True])
@pytest.mark.parametrize('live', [False, True])
def test_stop_cancels_pending_cycle_even_when_already_down(tmp_path, monkeypatch,
                                                         after_turn, live):
    _, panes = world(tmp_path, monkeypatch)
    if not live:
        panes.kill_session('crew-worker')
    requests = cycle.Requests(tmp_path)
    requests.request('worker', 'saved checkpoint')
    a = args(tmp_path)
    a.after_turn = after_turn
    assert cli._cmd_stop(a) == cli.OK
    assert 'worker' not in requests.pending()
    assert (agent_hold.reason(tmp_path, 'worker') if after_turn
            else cli._stops(a).get('worker'))
    assert panes.exists('crew-worker') == (live and after_turn)


@pytest.mark.parametrize('after_turn', [False, True])
def test_dry_run_preserves_request(tmp_path, monkeypatch, after_turn):
    world(tmp_path, monkeypatch)
    requests = cycle.Requests(tmp_path)
    requests.request('worker', 'saved checkpoint')
    a = args(tmp_path)
    a.after_turn, a.dry_run = after_turn, True
    assert cli._cmd_stop(a) == cli.OK
    assert 'worker' in requests.pending()


@pytest.mark.parametrize('held', [False, True])
def test_tend_cycle_refuses_stale_request_before_preflight(tmp_path, monkeypatch, held):
    world(tmp_path, monkeypatch)
    a = args(tmp_path)
    a._automatic_cycle = True
    if held:
        agent_hold.hold(tmp_path, 'worker', 'lead')
    else:
        cli._stops(a).record('worker', 1, reason='budget stop')
    # Models a request read before stop cancelled it, or a legacy pending entry.
    cycle.Requests(tmp_path).request('worker', 'saved checkpoint')
    preflight = Mock(side_effect=AssertionError('must not reach preflight'))
    monkeypatch.setattr(cli, '_agent_trees', preflight)
    assert cli._cmd_cycle(a) == cli.REFUSED
    preflight.assert_not_called()


def test_internal_cycle_stop_keeps_request_for_failed_launch_retry(tmp_path, monkeypatch):
    _, panes = world(tmp_path, monkeypatch)
    a = args(tmp_path)
    a.after_turn = False
    a._cycle_internal_stop = True
    a.reason = cycle.CYCLE_REASON + ': saved checkpoint'
    cycle.Requests(tmp_path).request('worker', 'saved checkpoint')
    assert cli._cmd_stop(a) == cli.OK
    assert not panes.exists('crew-worker')
    assert 'worker' in cycle.Requests(tmp_path).pending()


@pytest.mark.parametrize('after_turn', [False, True])
def test_stop_then_real_tend_pass_never_serves_cycle(tmp_path, monkeypatch, after_turn):
    from tests.test_tend import _Args

    _, panes = world(tmp_path, monkeypatch)
    a = args(tmp_path)
    a.after_turn = after_turn
    cycle.Requests(tmp_path).request('worker', 'checkpoint')
    assert cli._cmd_stop(a) == cli.OK
    # A surviving sibling stamp makes this a live managed deployment.
    settings = tmp_path / 'settings.json'
    settings.write_text('{}')
    cli._launches(a).record('sibling', settings)
    # Reinsert a legacy request to exercise tend's guard, not only cancellation.
    cycle.Requests(tmp_path).request('worker', 'checkpoint')
    launch = Mock(side_effect=AssertionError('must stay stopped'))
    monkeypatch.setattr(cli, '_launch', launch)
    preflight = Mock(side_effect=AssertionError('must not serve stopped cycle'))
    monkeypatch.setattr(cli, '_agent_trees', preflight)
    cli._tend_once(_Args(tmp_path, backend='files'))
    launch.assert_not_called()
    preflight.assert_not_called()
    assert panes.exists('crew-worker') == after_turn


def test_stop_during_preflight_is_rechecked_at_mutation(tmp_path, monkeypatch):
    _, panes = world(tmp_path, monkeypatch)
    a = args(tmp_path)
    a._automatic_cycle = True
    a.after_turn = False
    cycle.Requests(tmp_path).request('worker', 'checkpoint')
    assert not cli._cycle_stop_refusal(a, 'worker')
    assert cli._cmd_stop(a) == cli.OK
    perform = Mock(side_effect=AssertionError('must not mutate pane'))
    monkeypatch.setattr(cli, '_perform_cycle_locked', perform)
    chosen = cycle.Plan(cycle.RELAUNCH, 'test')
    assert cli._perform_cycle(a, None, 'worker', 'crew-worker', panes,
                              None, chosen, None)[0] == cli.REFUSED
    perform.assert_not_called()


def test_stop_waits_for_cycle_mutation_then_wins(tmp_path, monkeypatch):
    import threading
    _, panes = world(tmp_path, monkeypatch)
    a = args(tmp_path)
    a.after_turn = False
    entered, finish = threading.Event(), threading.Event()
    cycle.Requests(tmp_path).request('worker', 'checkpoint')

    def mutate(*args):
        entered.set()
        assert finish.wait(5)
        return cli.OK, cycle.RELAUNCH

    monkeypatch.setattr(cli, '_perform_cycle_locked', mutate)
    chosen = cycle.Plan(cycle.RELAUNCH, 'test')
    cy = threading.Thread(target=cli._perform_cycle,
                          args=(a, None, 'worker', 'crew-worker', panes,
                                None, chosen, None))
    stopped = threading.Event()
    def stop():
        assert cli._cmd_stop(a) == cli.OK
        stopped.set()
    cy.start()
    assert entered.wait(5)
    st = threading.Thread(target=stop)
    st.start()
    try:
        assert not stopped.wait(0.05)
    finally:
        finish.set()
        cy.join(5)
        st.join(5)
    assert stopped.is_set()
    assert not panes.exists('crew-worker')
    assert not cycle.Requests(tmp_path).pending()


def test_refused_cycle_stop_new_does_not_report_old_context_blocked(
        tmp_path, monkeypatch, capsys):
    from shantytown.tmux import NullPanes
    from tests.test_new import _world, _Args, READY
    root = _world(tmp_path)
    panes = NullPanes(screen=READY, live={'crew-ellie'}, owned={'crew-ellie'})
    monkeypatch.setattr(cli, 'Tmux', lambda *_a, **_k: panes)
    monkeypatch.setattr(cli, '_LIVE_ATTEMPTS', 1)
    monkeypatch.setattr(cli, '_LIVE_DELAY', 0)
    requests = cycle.Requests(root)
    requests.request('ellie', 'old full context checkpoint')
    requests.mark_refused('ellie', 'unpublished work')
    requests.request('sibling', 'keep this request')
    a = _Args(root=root, after_turn=False, reason='operator restart')
    assert cli._cmd_stop(a) == cli.OK
    assert cli._cmd_new(a) == cli.OK
    assert panes.exists('crew-ellie')
    assert set(requests.pending()) == {'sibling'}
    capsys.readouterr()
    assert cli._cmd_crew(_Args(root=root)) == cli.OK
    output = capsys.readouterr().out
    assert 'ellie' in output
    assert 'cycle-blocked' not in output
