"""A roster reads the store once, preserving readiness and degraded warnings."""
from shantytown import br
from shantytown.protocols import WorkItem


def test_snapshot_is_bounded_and_next_reader_observes_new_work(monkeypatch):
    rows = [dict(id='a', title='blocked', assignee='worker', status='open'),
            dict(id='z', title='ready', assignee='worker', status='open'),
            dict(id='h', title='held', assignee='other', status='in_progress')]
    calls = []
    def load(_):
        calls.append('list')
        return list(rows), []
    def ready(_):
        calls.append('ready')
        return {'z'}
    monkeypatch.setattr(br, 'rows_partial', load)
    monkeypatch.setattr(br, 'ready_ids_or_none', ready)
    reader = br.plate_reader(object())
    assert calls == []
    assert reader('worker').id == 'z'
    assert reader('other').id == 'h'
    assert reader('nobody') is None
    assert calls == ['list', 'ready']
    rows[1] = dict(rows[1], title='new title')
    assert reader('worker').title == 'ready'
    assert br.plate_reader(object())('worker').title == 'new title'
    assert calls == ['list', 'ready', 'list', 'ready']


def test_snapshot_keeps_incomplete_store_and_unknown_readiness_honest(monkeypatch, capsys):
    rows = [dict(id='a', title='one', assignee='worker', status='open'),
            dict(id='b', title='two', assignee='worker', status='open')]
    monkeypatch.setattr(br, 'rows_partial', lambda _: (rows, ['extra store unreadable']))
    monkeypatch.setattr(br, 'ready_ids_or_none', lambda _: None)
    monkeypatch.setattr(br, 'name_the_blocker', lambda *_: (_ for _ in ()).throw(AssertionError('unknown is not blocked')))
    reader = br.plate_reader(object())
    assert reader('worker').id == 'a'
    assert reader('missing') is None
    assert capsys.readouterr().err.count('extra store unreadable') == 2


def test_snapshot_preserves_blocker_lookup(monkeypatch):
    rows = [dict(id='a', title='blocked', assignee='worker', status='open')]
    monkeypatch.setattr(br, 'rows_partial', lambda _: (rows, []))
    monkeypatch.setattr(br, 'ready_ids_or_none', lambda _: set())
    named = WorkItem(id='a', title='blocked by dependency')
    monkeypatch.setattr(br, 'name_the_blocker', lambda tracker, item: named)
    assert br.plate_reader(object())('worker') is named
