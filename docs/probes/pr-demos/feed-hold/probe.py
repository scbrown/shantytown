"""Same CLI request on both source versions; all effects stay in a fixture."""
import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from shantytown import agent_hold, cli, feed_check
from shantytown.tmux import NullPanes
from test_go_cli import _root

with TemporaryDirectory(prefix='feed-hold-demo-') as directory, pytest.MonkeyPatch.context() as mp:
    root = _root(Path(directory))
    note = Path(directory) / 'reason'
    note.write_text('coordinate the release; available by explicit dispatch')
    panes = NullPanes(screen='', live={'%5'}, owned={'%5'})
    mp.setattr(cli, 'Tmux', lambda *a, **k: panes)
    common = ['--root', str(root), '--backend', 'files']
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        try:
            held = cli.main([*common, 'work', 'hold-feed', 'ellie', '--until',
                             '2099-01-01', '--reason-file', str(note)])
        except SystemExit as exc:
            held = exc.code
        automatic_held = bool(feed_check.haul_hold_reason(root, 'ellie'))
        stop_requested = bool(agent_hold.reason(root, 'ellie'))
        dispatched = cli.main([*common, 'go', 'item-1', 'ellie'])
    print(json.dumps({'hold_command_exit': held,
                      'automatic_feed_held': automatic_held,
                      'after_turn_stop_requested': stop_requested,
                      'explicit_dispatch_exit': dispatched,
                      'pane_still_live': panes.exists('%5')}, indent=2))
    if held == 0:
        from shantytown import feed_hold
        with contextlib.redirect_stdout(io.StringIO()):
            cleared = cli.main([*common, 'work', 'hold-feed', 'ellie', '--clear'])
        print(json.dumps({'clear_exit': cleared,
                          'automatic_feed_after_clear': bool(feed_check.haul_hold_reason(root, 'ellie'))}))
        until = datetime.fromtimestamp(200, timezone.utc).isoformat()
        feed_hold.hold(root, 'ellie', 'operator', until, 'bounded duty', now=100)
        print(json.dumps({'before_expiry_held': bool(feed_hold.reason(root, 'ellie', now=199)),
                          'at_expiry_held': bool(feed_hold.reason(root, 'ellie', now=200))}))
