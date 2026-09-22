import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shantytown import cli, gaming
from shantytown.creel_advisory import Alerter
from shantytown.protocols import Agent
from shantytown.tend import Tender, GOVERNED
from shantytown.tmux import NullPanes


@pytest.fixture(autouse=True)
def no_physical_gpu(monkeypatch):
    monkeypatch.setattr(gaming.gaming_activity, 'gpu_busy', lambda: None)


def enabled(root):
    (root / 'gaming').mkdir()
    (root / 'gaming/enabled').touch()


def process(proc, pid, *args):
    path = proc / str(pid)
    path.mkdir(parents=True)
    (path / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in args) + b'\0')


def test_only_real_reaper_with_numeric_appid_matches(tmp_path):
    process(tmp_path, 1, '/steam/reaper', 'SteamLaunch', 'AppId=42', '--', 'game')
    process(tmp_path, 2, '/steam/reaper', 'SteamLaunch', '--', 'steamwebhelper')
    process(tmp_path, 3, 'bash', '-c', 'reaper SteamLaunch AppId=999')
    process(tmp_path, 4, '/steam/reaper', 'SteamLaunch', 'AppId=abc')
    process(tmp_path, 5, 'pgrep', '-af', 'reaper SteamLaunch AppId=5')
    assert gaming.game_appids(tmp_path) == ('42',)


def test_start_end_debounce_and_restart(tmp_path):
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, '/steam/reaper', 'SteamLaunch', 'AppId=42')
    assert gaming.probe(tmp_path, proc=proc, now=1000).held
    (proc / '1/cmdline').unlink()
    assert gaming.probe(tmp_path, proc=proc, now=1060).state == 'gaming'
    assert gaming.probe(tmp_path, proc=proc, now=1120).held
    assert gaming.probe(tmp_path, proc=proc, now=1180).held
    assert gaming.probe(tmp_path, proc=proc, now=1181).held
    assert gaming.probe(tmp_path, proc=proc, now=1301).state == 'clear'
    (proc / '1/cmdline').write_bytes(b'reaper\0SteamLaunch\0AppId=42\0')
    assert gaming.probe(tmp_path, proc=proc, now=1320).held
    assert gaming.read(tmp_path, now=1501).state == 'unknown'
    assert not gaming.read(tmp_path, now=1319).held


def test_manual_survives_probe_and_clear_does_not_cancel_a_real_game(tmp_path):
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, 'reaper', 'SteamLaunch', 'AppId=42')
    gaming.manual(tmp_path)
    assert gaming.probe(tmp_path, proc=proc).state == 'manual'
    gaming.manual(tmp_path, clear=True)
    assert gaming.read(tmp_path).state == 'gaming'


def test_missing_proc_is_unknown_not_clean(tmp_path):
    enabled(tmp_path)
    s = gaming.probe(tmp_path, proc=tmp_path / 'missing')
    assert s.state == 'unknown' and not s.held
    assert 'aegis_gaming_probe_ok 0' in gaming.metrics(s)


@pytest.mark.parametrize('cmd', ['new', 'start', 'cycle'])
def test_cli_refuses_before_touching_runtime(tmp_path, monkeypatch, capsys, cmd):
    gaming.manual(tmp_path)
    monkeypatch.setattr(cli, '_warn_if_no_store', lambda a: None)
    assert cli.main(['--root', str(tmp_path), cmd, 'worker']) == 1
    assert 'GOVERNOR HOLD' in capsys.readouterr().err


@pytest.mark.parametrize('cmd', ['new', 'start', 'cycle'])
def test_refusal_names_the_override_and_echoes_the_command(tmp_path, monkeypatch,
                                                          capsys, cmd):
    """A refusal has to say how to proceed, in a form that can be pasted."""
    gaming.manual(tmp_path)
    monkeypatch.setattr(cli, '_warn_if_no_store', lambda a: None)
    argv = ['--root', str(tmp_path), cmd, 'worker']
    assert cli.main(argv) == 1
    err = capsys.readouterr().err
    assert 'GOVERNOR HOLD' in err
    # The operator's OWN command line, with the flag appended.
    assert f'st {" ".join(argv)} --despite-hold' in err
    # A manual hold is the one `--clear` actually lifts, so it is offered.
    assert 'st fleet hold gaming --clear' in err


@pytest.mark.parametrize('cmd', ['new', 'start', 'cycle'])
def test_despite_hold_passes_the_command_gate(tmp_path, monkeypatch, cmd):
    """The override must reach every launch surface, not just the one we tested."""
    gaming.manual(tmp_path)
    monkeypatch.setattr(cli, '_warn_if_no_store', lambda a: None)
    reached = []
    for name in ('_cmd_new', '_cmd_start', '_cmd_cycle'):
        monkeypatch.setattr(cli, name, lambda a, _n=name: reached.append(_n) or 0)
    assert cli.main(['--root', str(tmp_path), cmd, 'worker',
                     '--despite-hold']) == 0
    assert reached == [f'_cmd_{cmd}']


def test_automatic_hold_does_not_offer_a_clear_that_would_not_work(tmp_path):
    """`--clear` removes a manual marker and nothing else.

    Offering it against an automatic hold sends the operator to run a command
    that reports success and changes nothing, because the next probe re-asserts
    the hold a minute later.
    """
    auto = gaming.Status('gaming').override_lines('st fleet start')
    assert 'st fleet start --despite-hold' in auto[0]
    assert not any('st fleet hold gaming --clear' == line.strip() for line in auto)
    assert 'will NOT lift this one' in auto[1]
    assert gaming.Status('clear').override_lines('st fleet start') == ()


def test_override_reaches_the_shared_launcher_and_says_so(tmp_path, capsys):
    """_launch is the seam `new`, `start` and `attach` share.

    Internal callers hand it a namespace with no flag on it and MUST stay
    guarded; an operator who passed the flag gets through, loudly.
    """
    gaming.manual(tmp_path)
    guarded = SimpleNamespace(root=tmp_path)
    assert cli._launch(guarded, None, None, None, window_restore=True) == 1

    override = SimpleNamespace(root=tmp_path, despite_hold=True)
    capsys.readouterr()
    # Past the gate is all this test claims; what the stub runtime does further
    # down the seam belongs to the launcher's own tests, not to the governor's.
    with contextlib.suppress(Exception):
        cli._launch(override, Agent(name='worker', pane='p-worker'), NullPanes(),
                    SimpleNamespace(name='fake'), window_restore=True)
    err = capsys.readouterr().err
    assert 'GOVERNOR HOLD' not in err
    assert '--despite-hold' in err and 'THROUGH a gaming hold' in err


def test_agents_are_never_offered_the_override(tmp_path):
    """Dispatch and the feed check are how an AGENT asks for work.

    The governor exists so an agent cannot decide the game is over, so neither
    surface may advertise a flag that would let it.
    """
    from shantytown.feed_check import governor_admits
    gaming.manual(tmp_path)
    a = SimpleNamespace(root=tmp_path)
    assert '--despite-hold' not in cli._dispatch_gate(a)(None, 'worker')
    assert '--despite-hold' not in governor_admits(tmp_path)(None)


def test_internal_launch_and_dispatch_have_independent_gates(tmp_path):
    gaming.manual(tmp_path)
    a = SimpleNamespace(root=tmp_path)
    assert cli._launch(a, None, None, None, window_restore=True) == 1
    assert 'gaming' in cli._dispatch_gate(a)(None, 'worker')


def test_tender_keeps_dead_agent_down_and_never_stops_live_agent(tmp_path):
    gaming.manual(tmp_path)
    status = gaming.read(tmp_path)
    started = []
    runtime = SimpleNamespace(name='fake', start=lambda c, s: started.append(c.name),
                              shows_ready_ui=lambda s: True)
    class Panes(NullPanes):
        def capture(self, *args, **kwargs):
            return 'ready'
    cards = [Agent(name='worker', pane='p-worker'), Agent(name='lead', pane='p-lead')]
    report = Tender(Panes(live={'p-lead'}), runtime, None,
                    ensure=lambda c: c.workspace,
                    governed=lambda c: status.refusal).pass_over(cards)
    assert not started
    assert any(f.agent == 'worker' and f.verdict == GOVERNED for f in report.findings)


def test_delivery_retries_and_both_transitions_are_deduped(tmp_path, monkeypatch):
    pushed = []
    delivered = [False]
    def push(reg, panes, text):
        pushed.append(text)
        return delivered[0]
    monkeypatch.setattr(cli.creel_advisory_mod, 'Alerter',
                        lambda *a, **kw: Alerter(*a, **kw, push=push))
    a = SimpleNamespace(root=tmp_path)
    call = lambda s: cli._gaming_advisory(a, s, reg=object(), panes=object())
    assert call(gaming.Status('clear')) == []
    assert not pushed
    assert call(gaming.Status('gaming')) == []
    delivered[0] = True
    assert call(gaming.Status('gaming')) == ['local']
    assert call(gaming.Status('ending')) == []
    assert call(gaming.Status('clear')) == ['local']
    assert call(gaming.Status('clear')) == []
    assert len(pushed) == 3 and 'LIFTED' in pushed[-1]


def test_manual_only_lift_reaches_coordinator(tmp_path, monkeypatch):
    pushed = []
    monkeypatch.setattr(cli.creel_advisory_mod, 'Alerter', lambda *a, **kw:
                        Alerter(*a, **kw, push=lambda r, p, t: pushed.append(t) or True))
    a = SimpleNamespace(root=tmp_path)
    cli._gaming_advisory(a, gaming.Status('manual'), reg=object(), panes=object())
    cli._gaming_advisory(a, gaming.Status('off'), reg=object(), panes=object())
    assert len(pushed) == 2 and 'LIFTED' in pushed[-1]


def test_rule_zero_yields_to_gaming_without_usage_governor(tmp_path):
    from shantytown.feed_check import governor_admits
    gaming.manual(tmp_path)
    assert 'gaming' in governor_admits(tmp_path)(None)


def test_weak_idle_advisory_never_lifts_and_manual_wins(tmp_path, monkeypatch):
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, 'reaper', 'SteamLaunch', 'AppId=42')
    monkeypatch.setattr(gaming.gaming_activity, 'observe', lambda *a:
                        dict(game_present_idle=True, idle_since=1000, gpu_busy=0, game_cpu=0))
    status = gaming.probe(tmp_path, proc=proc, now=1600)
    assert status.held and status.game_present_idle
    assert 'idle 10 min' in status.render() and 'your call' in status.render()
    gaming.manual(tmp_path)
    assert gaming.read(tmp_path).state == 'manual'
    assert 'your call' not in gaming.read(tmp_path).render()


def test_recorded_shader_launch_holds_without_appid(tmp_path):
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    rows = json.loads((Path(__file__).parent / 'fixtures/shader-processes.json').read_text())
    for row in rows:
        process(proc, row['pid'], *row['argv'])
    assert gaming.game_appids(proc) == ()
    assert len(gaming.shader_pids(proc)) == len(rows)
    gaming.manual(tmp_path)
    assert gaming.probe(tmp_path, proc=proc, now=1000).state == 'manual'
    # Only the test clears its own sandbox manual marker.
    gaming.manual(tmp_path, clear=True)
    for now in range(1060, 1901, 60):
        assert gaming.probe(tmp_path, proc=proc, now=now).state == 'gaming'
    data = json.loads((tmp_path / 'gaming/state.json').read_text())
    assert data['absent_since'] is None and data['shader_pids']
    for row in rows:
        (proc / str(row['pid']) / 'cmdline').unlink()
    assert gaming.probe(tmp_path, proc=proc, now=1960).state == 'ending'
    assert gaming.probe(tmp_path, proc=proc, now=2080).held
    assert gaming.probe(tmp_path, proc=proc, now=2081).state == 'clear'


def test_background_shader_maintenance_releases_the_crew(tmp_path):
    """The 2026-09-15 incident: Steam precompiling with no game ever launching.

    Held for 76 minutes because shader evidence had no ceiling. The crew must be
    released once the shader phase outlives any plausible pre-launch wait, even
    though fossilize_replay is still burning cores.
    """
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, '/steam/fossilize_replay')
    assert gaming.game_appids(proc) == ()
    # Inside the ceiling the hold stands: this may still be a launch precursor.
    for now in range(1000, 2201, 60):
        assert gaming.probe(tmp_path, proc=proc, now=now).state == 'gaming'
    # Past it, the absence clock finally starts even with shaders still present.
    assert gaming.probe(tmp_path, proc=proc, now=2260).state == 'ending'
    assert gaming.probe(tmp_path, proc=proc, now=2380).held
    assert gaming.probe(tmp_path, proc=proc, now=2381).state == 'clear'
    # A game arriving after the ceiling re-arms the hold with no ceiling at all.
    process(proc, 2, '/steam/reaper', 'SteamLaunch', 'AppId=42')
    for now in range(2440, 4241, 60):
        assert gaming.probe(tmp_path, proc=proc, now=now).state == 'gaming'


def test_intermittent_shader_batches_cannot_restart_the_ceiling(tmp_path):
    """The 2026-09-22 incident: the hold stood 22 h after play ended.

    Steam precompiles in BATCHES with gaps between them. Clearing the ceiling
    clock the instant a batch ended let the next batch restart it, so the
    20-minute ceiling never elapsed however long the phase ran. A gap must not
    buy the phase a fresh ceiling.
    """
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    now = 1000
    # Six batches of four minutes, each separated by a single minute with no
    # shaders: thirty minutes of phase, never more than a minute of absence.
    # Steam starts a fresh worker per batch, so each gets its own pid.
    for batch in range(6):
        process(proc, batch + 1, '/steam/fossilize_replay')
        for _ in range(4):
            gaming.probe(tmp_path, proc=proc, now=now)
            now += 60
        (proc / str(batch + 1) / 'cmdline').unlink()
        gaming.probe(tmp_path, proc=proc, now=now)
        now += 60
    process(proc, 99, '/steam/fossilize_replay')
    # The phase has outlived the ceiling, so a returning batch must not re-hold.
    assert gaming.probe(tmp_path, proc=proc, now=now).state != 'gaming'


def test_a_new_precompile_after_a_real_gap_earns_its_own_ceiling(tmp_path):
    """The counterpart: the fix must not make the ceiling permanent.

    A precompile the next day is a new phase and gets the full pre-launch grace,
    because it may genuinely precede a launch.
    """
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, '/steam/fossilize_replay')
    for now in range(1000, 2201, 60):
        assert gaming.probe(tmp_path, proc=proc, now=now).state == 'gaming'
    (proc / '1' / 'cmdline').unlink()
    for now in range(2260, 2521, 60):
        gaming.probe(tmp_path, proc=proc, now=now)
    assert gaming.probe(tmp_path, proc=proc, now=2600).state == 'clear'
    # Hours later Steam precompiles again; that phase is held on its own merits.
    process(proc, 2, '/steam/fossilize_replay')
    assert gaming.probe(tmp_path, proc=proc, now=20000).state == 'gaming'


def test_shader_ceiling_is_dropped_once_a_game_is_seen(tmp_path):
    """A long precompile that DOES end in a launch keeps the uninterrupted hold."""
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, '/steam/fossilize_replay')
    for now in range(1000, 2001, 60):
        assert gaming.probe(tmp_path, proc=proc, now=now).state == 'gaming'
    process(proc, 2, '/steam/reaper', 'SteamLaunch', 'AppId=42')
    # The reaper lands before the ceiling; shaders keep running beside the game
    # for well past it, and the hold never blinks.
    for now in range(2060, 5001, 60):
        assert gaming.probe(tmp_path, proc=proc, now=now).state == 'gaming'


def test_shader_mentions_and_similar_names_do_not_hold(tmp_path):
    process(tmp_path, 1, 'bash', '-c', '/steam/fossilize_replay')
    process(tmp_path, 2, 'pgrep', '-af', 'fossilize_replay')
    process(tmp_path, 3, '/steam/fossilize_replay_helper')
    assert gaming.shader_pids(tmp_path) == ()


def test_game_to_shader_transition_restarts_absence_clock(tmp_path):
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, '/steam/reaper', 'SteamLaunch', 'AppId=42')
    gaming.probe(tmp_path, proc=proc, now=1000)
    (proc / '1/cmdline').unlink()
    assert gaming.probe(tmp_path, proc=proc, now=1120).state == 'gaming'
    process(proc, 2, '/steam/fossilize_replay')
    assert gaming.probe(tmp_path, proc=proc, now=1240).state == 'gaming'
    (proc / '2/cmdline').unlink()
    assert gaming.probe(tmp_path, proc=proc, now=1360).state == 'ending'
    assert gaming.probe(tmp_path, proc=proc, now=1480).held
    assert gaming.probe(tmp_path, proc=proc, now=1481).state == 'clear'


def test_first_appid_after_long_shader_phase_gets_launch_grace(tmp_path):
    enabled(tmp_path)
    proc = tmp_path / 'proc'
    process(proc, 1, '/steam/fossilize_replay')
    for now in range(1000, 1661, 60):
        assert gaming.probe(tmp_path, proc=proc, now=now).held
    process(proc, 2, '/steam/reaper', 'SteamLaunch', 'AppId=42')
    gaming.probe(tmp_path, proc=proc, now=1720)
    for pid in (1, 2):
        (proc / str(pid) / 'cmdline').unlink()
    for now in (1780, 1900, 2019):
        assert gaming.probe(tmp_path, proc=proc, now=now).state == 'gaming'
    assert gaming.probe(tmp_path, proc=proc, now=2020).state == 'clear'
