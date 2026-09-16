import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from shantytown import gaming_scopes, mcp_limits, panemem


def test_projection_is_explicit_and_preserves_http_and_credentials(tmp_path):
    data = {'mcpServers': {'playwright': {'command': 'server', 'args': ['--headless'],
                          'env': {'TOKEN': 'fixture'}}, 'remote': {'url': 'https://example.test'}, 'lookup': {'command':'lookup'}}}
    assert mcp_limits.project(data, tmp_path, 'one') == data
    (tmp_path/'provision').mkdir()
    (tmp_path/'provision/mcp-limits.json').write_text(json.dumps({'enabled': True, 'agents': ['one']}))
    assert mcp_limits.project(data, tmp_path, 'two') == data
    got = mcp_limits.project(data, tmp_path, 'one')
    assert got['mcpServers']['remote'] == data['mcpServers']['remote']
    server = got['mcpServers']['playwright']
    assert server['env'] == {'TOKEN': 'fixture'}
    assert server['args'][server['args'].index('--idle-seconds')+1] == '300'
    other=got['mcpServers']['lookup']['args']
    assert other[other.index('--idle-seconds')+1] == '0'
    assert server['args'][-3:] == ['--', 'server', '--headless']
    assert data['mcpServers']['playwright']['command'] == 'server'
    (tmp_path/'provision/mcp-limits.json').write_text(json.dumps({'enabled': True, 'agents': ['*']}))
    assert mcp_limits.enabled(tmp_path, 'future-card')
    assert mcp_limits.project(data, tmp_path, 'future-card')['mcpServers']['playwright']['command'] != 'server'


def test_missing_controllers_refuses_before_child_creation(tmp_path):
    (tmp_path/'cgroup.subtree_control').write_text('memory')
    with pytest.raises(ValueError, match='unbounded'):
        mcp_limits.child_group(tmp_path)
    assert len(list(tmp_path.iterdir())) == 1


def test_descendant_lookup_keeps_parent_scope(monkeypatch):
    monkeypatch.setattr(Path, 'read_text', lambda self: '0::/user.slice/tmux-spawn-fixture.scope/st-mcp-child\n')
    assert panemem.scope_of_pid(1) == 'tmux-spawn-fixture.scope'
    assert panemem._cgroup_path_of_pid(1) == '/user.slice/tmux-spawn-fixture.scope'


def test_recursive_ownership_rejects_foreign_child(monkeypatch, tmp_path):
    base = tmp_path/'scope'
    (base/'child').mkdir(parents=True)
    (base/'cgroup.procs').write_text('')
    (base/'child/cgroup.procs').write_text('777\n')
    original = panemem.Path
    monkeypatch.setattr(panemem, 'Path', lambda path: base if str(path) == '/sys/fs/cgroup/scope' else original(path))
    monkeypatch.setattr(panemem, '_is_descendant', lambda pid, ancestor: pid == ancestor)
    assert panemem.scope_pids('/scope') == [777]
    assert not panemem.scope_is_exclusive_to(123, '/scope')[0]


def test_prepare_rejects_shared_scope_before_property_write(monkeypatch, tmp_path):
    monkeypatch.setattr(panemem, 'scope_of_pid', lambda pid: 'tmux-spawn-fixture.scope')
    monkeypatch.setattr(panemem, '_cgroup_path_of_pid', lambda pid: '/fixture')
    monkeypatch.setattr(gaming_scopes, 'owned_scope', lambda *args: (False, 'foreign child'))
    run = Mock()
    with pytest.raises(ValueError, match='foreign child'):
        mcp_limits.prepare(tmp_path, 'one', 123, run=run)
    run.assert_not_called()


def test_launch_waits_for_exclusive_scope_before_delegation(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_limits, 'enabled', lambda *args: True)
    monkeypatch.setattr(panemem, 'resolve_pane_scope', lambda pid: (None, 'launcher scope'))
    prepare = Mock()
    monkeypatch.setattr(mcp_limits, 'prepare', prepare)
    with pytest.raises(ValueError, match='launcher scope'):
        mcp_limits.prepare_launch(tmp_path, 'one', Mock(pane_pid=lambda _: '123'), 'session')
    prepare.assert_not_called()
