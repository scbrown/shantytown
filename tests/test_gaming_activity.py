from shantytown import gaming_activity as activity


def test_sustained_low_activity_is_weak_and_resets_on_missing_gpu(monkeypatch):
    monkeypatch.setattr(activity, 'gpu_busy', lambda: 1.0)
    monkeypatch.setattr(activity, 'tree_ticks', lambda *a: {'1:42': 100})
    previous = {'observed': 940, 'ticks': {'1:42': 100}}
    for now in range(1000, 1601, 60):
        reading = activity.observe('/proc', {1: '42'}, previous, now)
        assert reading['game_present_idle'] == (now >= 1600)
        previous = dict(reading, observed=now)
    monkeypatch.setattr(activity, 'gpu_busy', lambda: None)
    assert activity.observe('/proc', {1: '42'}, previous, 1660)['idle_since'] is None


def test_pid_reuse_and_cpu_activity_cannot_report_idle(monkeypatch):
    monkeypatch.setattr(activity, 'gpu_busy', lambda: 0.0)
    monkeypatch.setattr(activity, 'tree_ticks', lambda *a: {'1:43': 100})
    previous = {'observed': 1000, 'ticks': {'1:42': 100}, 'idle_since': 1}
    assert activity.observe('/proc', {1: '42'}, previous, 1060)['game_cpu'] is None
    monkeypatch.setattr(activity, 'tree_ticks', lambda *a: {'1:42': 100000})
    reading = activity.observe('/proc', {1: '42'}, previous, 1060)
    assert reading['game_cpu'] > 2 and reading['idle_since'] is None


def test_tree_ticks_excludes_unrelated_processes_and_handles_comm(tmp_path):
    def stat(pid, parent, cpu):
        folder = tmp_path / str(pid)
        folder.mkdir()
        fields = ['S', str(parent)] + ['0'] * 20
        fields[11], fields[12], fields[19] = str(cpu), '0', '42'
        (folder / 'stat').write_text(f'{pid} (game (child)) ' + ' '.join(fields))
    stat(1, 0, 3)
    stat(2, 1, 5)
    stat(3, 0, 999)
    assert activity.tree_ticks(tmp_path, {1: '42'}) == {'1:42': 3, '2:42': 5}
