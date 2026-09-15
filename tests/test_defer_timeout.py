"""A partial br update must never hide work before its condition is durable."""
import json
import subprocess

import pytest

from shantytown import cli
from shantytown.br import BrTracker
from shantytown.dispatch import DeferUnconfirmed, Dispatcher, TrackerWriteLost
from shantytown.files import FilesRegistry
from shantytown.tmux import NullPanes


class PartialBr(BrTracker):
    def __init__(self, fail_write=None, after_commit=False, drop=None):
        super().__init__(repo=None)
        self.row = {'id': 'item-1', 'title': 'test', 'status': 'open', 'labels': []}
        self.writes = []
        self.fail_write = fail_write
        self.after_commit = after_commit
        self.drop = drop
        self.reads = 0

    def _bd_for(self, item_id, *args):
        if args[0] == 'show':
            self.reads += 1
            return subprocess.CompletedProcess(args, 0, json.dumps([self.row]), '')
        assert args[0] == 'update'
        self.writes.append(args)
        failing = len(self.writes) == self.fail_write
        if failing and not self.after_commit:
            raise subprocess.TimeoutExpired('br update', 30)
        for arg in args[2:]:
            key, _, value = arg.partition('=')
            if key == self.drop:
                continue
            if key == '--status':
                self.row['status'] = value
                # Reproduce the interrupted status-first write from the incident.
                if failing:
                    raise subprocess.TimeoutExpired('br update', 30)
            elif key == '--notes':
                self.row['notes'] = value
            elif key == '--defer':
                self.row['defer_until'] = value
            elif key == '--add-label':
                self.row['labels'].append(value)
            elif key == '--remove-label':
                self.row['labels'] = [x for x in self.row['labels'] if x != value]
        if failing:
            raise subprocess.TimeoutExpired('br update', 30)
        return subprocess.CompletedProcess(args, 0, '', '')


def dispatcher(tmp_path, tracker):
    return Dispatcher(FilesRegistry(tmp_path / 'crew'), tracker, NullPanes())


@pytest.mark.parametrize('marker', [False, True])
@pytest.mark.parametrize('fail_write,after_commit', [(1, False), (1, True), (2, False), (2, True)])
def test_timeout_at_each_write_boundary_keeps_a_resume_path(tmp_path, marker, fail_write, after_commit):
    t = PartialBr(fail_write, after_commit)
    reason = 'resume_when: closed:item-2' if marker else 'wait for the window'
    with pytest.raises(DeferUnconfirmed, match='No automatic retry'):
        dispatcher(tmp_path, t).defer('item-1', 'human', reason,
                                      until='' if marker else '2026-12-01')
    assert len(t.writes) == fail_write  # no blind retry after an indeterminate result
    assert t.row['status'] != 'deferred' or (
        t.row.get('defer_until') or 'resume_when:' in t.row.get('notes', ''))
    assert t.reads >= 3  # includes the read after the timeout


@pytest.mark.parametrize('drop', ['--notes', '--defer'])
def test_missing_preparation_never_reaches_status_write(tmp_path, drop):
    t = PartialBr(drop=drop)
    with pytest.raises(TrackerWriteLost):
        dispatcher(tmp_path, t).defer('item-1', 'human', 'reason', until='2026-12-01')
    assert t.row['status'] == 'open'
    assert len(t.writes) == 1


def test_stale_date_does_not_satisfy_requested_condition(tmp_path):
    t = PartialBr(drop='--defer')
    t.row['defer_until'] = '2026-10-01'
    with pytest.raises(TrackerWriteLost):
        dispatcher(tmp_path, t).defer('item-1', 'human', 'reason', until='2026-12-01')
    assert t.row['status'] == 'open'


def test_already_deferred_without_condition_is_not_a_successful_noop(tmp_path):
    t = PartialBr()
    t.row.update(status='deferred', labels=['blocked:human'])
    r = dispatcher(tmp_path, t).defer('item-1', 'human', 'reason', until='2026-12-01')
    assert not r.noop
    assert t.row['defer_until'] == '2026-12-01'


def test_cli_reports_indeterminate_write_without_success(tmp_path, monkeypatch, capsys):
    t = PartialBr(2, True)
    monkeypatch.setattr(cli, '_wire', lambda a: dispatcher(tmp_path, t))
    rc = cli.main(['defer', 'item-1', 'human', '--reason', 'reason', '--until', '2026-12-01'])
    out = capsys.readouterr()
    assert rc == cli.CANNOT_TELL
    assert 'COULD NOT CONFIRM' in out.err
    assert '✓' not in out.out
    assert t.row['defer_until'] == '2026-12-01'
