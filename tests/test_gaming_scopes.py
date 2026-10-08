from types import SimpleNamespace
from shantytown import gaming_scopes as scopes


def setup(monkeypatch):
    monkeypatch.setattr(scopes.panemem, 'scope_of_pid', lambda p: 'tmux-spawn-test.scope')
    monkeypatch.setattr(scopes.panemem, '_cgroup_path_of_pid', lambda p: '/test')
    monkeypatch.setattr(scopes.panemem, 'scope_is_exclusive_to', lambda p, g: (True, 'ok'))
    state = {'quota': None, 'weight': ''}
    writes = []
    def run(args, **kwargs):
        if 'set-property' in args:
            writes.append(args)
            quota = args[-2].split('=', 1)[1]
            state['quota'] = float(quota.rstrip('%')) if quota else None
            state['weight'] = args[-1].split('=', 1)[1]
        quota = 'infinity' if state['quota'] is None else f"{state['quota'] * 10:g}ms"
        return SimpleNamespace(returncode=0, stderr='', stdout=
            f"CPUWeight={state['weight'] or '[not set]'}\nCPUQuotaPerSecUSec={quota}\n")
    return state, writes, run


def test_hold_and_lift_restore_original_limits(tmp_path, monkeypatch):
    state, writes, run = setup(monkeypatch)
    assert 'verified' in scopes.reconcile(tmp_path, True, [("worker", 1)], run=run)[0]
    assert state == {'quota': 25, 'weight': '10'}
    assert 'restored' in scopes.reconcile(tmp_path, False, [], run=run)[0]
    assert state == {'quota': None, 'weight': ''}
    assert len(writes) == 2


def test_shared_scope_is_untouched(tmp_path, monkeypatch):
    _, writes, run = setup(monkeypatch)
    monkeypatch.setattr(scopes.panemem, 'scope_is_exclusive_to', lambda p, g: (False, 'shared'))
    assert 'UNKNOWN' in scopes.reconcile(tmp_path, True, [("worker", 1)], run=run)[0]
    assert not writes


def test_external_change_is_preserved_on_lift(tmp_path, monkeypatch):
    state, writes, run = setup(monkeypatch)
    scopes.reconcile(tmp_path, True, [("worker", 1)], run=run)
    state['quota'] = 5
    assert 'preserving' in scopes.reconcile(tmp_path, False, [], run=run)[0]
    assert state['quota'] == 5 and len(writes) == 1


def test_existing_stricter_limits_survive_hold(tmp_path, monkeypatch):
    state, _, run = setup(monkeypatch)
    state.update(quota=10, weight='5')
    scopes.reconcile(tmp_path, True, [("worker", 1)], run=run)
    assert state == {'quota': 10, 'weight': '5'}


def test_reparented_runtime_requires_both_identity_fields(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(scopes.panemem, 'scope_is_exclusive_to', lambda *a: (False, 'reparented'))
    monkeypatch.setattr(Path, 'read_text', lambda p: '1 2 3')
    environments = {1: b'', 2: b'SHANTY_AGENT=worker\0SHANTY_ROOT=/test\0', 3: b''}
    monkeypatch.setattr(Path, 'read_bytes', lambda p: environments[int(p.parts[-2])])
    monkeypatch.setattr(scopes.panemem, '_is_descendant',
                        lambda pid, parent: pid == parent or (pid == 3 and parent == 2))
    assert scopes.owned_scope('/test', 'worker', 1, '/test-scope')[0]
    assert not scopes.owned_scope('/other-deployment', 'worker', 1, '/test-scope')[0]
    assert not scopes.owned_scope('/test', 'other-agent', 1, '/test-scope')[0]


def test_protected_pane_child_needs_no_environment_read(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(scopes.panemem, 'scope_is_exclusive_to', lambda *a: (False, 'reparented'))
    monkeypatch.setattr(scopes.panemem, 'scope_pids', lambda *a: [1, 3, 2])
    monkeypatch.setattr(scopes.panemem, '_is_descendant',
                        lambda pid, parent: pid == parent or (pid == 3 and parent == 1))

    def environment(path):
        pid = int(path.parts[-2])
        if pid in (1, 3):
            raise AssertionError('proven pane descendants must not need environment access')
        return b'SHANTY_AGENT=worker\0SHANTY_ROOT=/test\0'

    monkeypatch.setattr(Path, 'read_bytes', environment)
    assert scopes.owned_scope('/test', 'worker', 1, '/test-scope')[0]


def test_protected_child_of_later_attributed_runtime_is_owned(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(scopes.panemem, 'scope_is_exclusive_to', lambda *a: (False, 'reparented'))
    # The protected child appears before the runtime whose identity proves it.
    monkeypatch.setattr(scopes.panemem, 'scope_pids', lambda *a: [3, 2, 1])
    monkeypatch.setattr(scopes.panemem, '_is_descendant',
                        lambda pid, parent: pid == parent or (pid == 3 and parent == 2))

    def environment(path):
        if int(path.parts[-2]) == 3:
            raise PermissionError('protected process environment')
        return b'SHANTY_AGENT=worker\0SHANTY_ROOT=/test\0'

    monkeypatch.setattr(Path, 'read_bytes', environment)
    assert scopes.owned_scope('/test', 'worker', 1, '/test-scope')[0]
    assert not scopes.owned_scope('/other-deployment', 'worker', 1, '/test-scope')[0]
    assert not scopes.owned_scope('/test', 'other-agent', 1, '/test-scope')[0]


def test_unproven_protected_process_still_refuses_scope(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(scopes.panemem, 'scope_is_exclusive_to', lambda *a: (False, 'shared'))
    monkeypatch.setattr(scopes.panemem, 'scope_pids', lambda *a: [3, 2, 1])
    monkeypatch.setattr(scopes.panemem, '_is_descendant', lambda pid, parent: pid == parent)

    def environment(path):
        if int(path.parts[-2]) == 3:
            raise PermissionError('protected foreign process environment')
        return b'SHANTY_AGENT=worker\0SHANTY_ROOT=/test\0'

    monkeypatch.setattr(Path, 'read_bytes', environment)
    assert not scopes.owned_scope('/test', 'worker', 1, '/test-scope')[0]
