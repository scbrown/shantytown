"""Priority/rank ordering agrees across tracker plate and fleet feed."""
import pytest
from shantytown.protocols import plate_key, rank_order
from shantytown.sd import SdTracker
from test_plate_readiness import FakeStore, row
from shantytown.beads import plate


def test_rank_drives_actual_plate_without_moving_priority_or_lifecycle():
    items = [row('old', priority=1), row('ranked', priority=1), row('urgent', priority=0)]
    items[1]['rank'] = '2'
    assert plate(FakeStore(items[:2], ready_ids=['old', 'ranked']), 'ada').id == 'ranked'
    assert plate(FakeStore(items, ready_ids=[x['id'] for x in items]), 'ada').id == 'urgent'
    assert plate_key('in_progress', 4, 'held', rank='9') < plate_key('open', 0, 'new', rank='-9')


def test_feed_exact_decimals_and_unranked_oldest():
    rows = [{'id': ident, 'priority': priority, 'rank': rank, 'created_at': created}
            for ident, priority, rank, created in [
                ('old', 1, None, '2026-01-01T00:00:00Z'),
                ('new', 1, None, '2026-01-02T00:00:00Z'),
                ('high', 0, None, '2026-01-03T00:00:00Z'),
                ('fraction', 1, '900000000000000000.000000001', '2026-01-03T00:00:00Z'),
                ('integer', 1, '900000000000000000', '2026-01-04T00:00:00Z')]]
    assert [r['id'] for r in SdTracker._tend_order(rows)] == ['high', 'integer', 'fraction', 'old', 'new']


@pytest.mark.parametrize('rank', ['NaN', '1e3', 3, '1.0000000001', '9000000000000000001'])
def test_malformed_rank_is_unknown_not_unranked(rank):
    with pytest.raises(ValueError):
        rank_order(rank)


def test_anchor_top_list_uses_rank_but_coming_due_keeps_deadlines():
    from types import SimpleNamespace
    from datetime import datetime, timezone
    from shantytown.answer import Answer
    from shantytown.due_brief import projected
    rows = [dict(id='older',title='Old',status='open',priority=1,created_at='2026-01-01T00:00:00Z',due_at='2026-10-17T00:00:00Z'),
            dict(id='ranked',title='Ranked',status='open',priority=1,rank='1',created_at='2026-01-02T00:00:00Z',due_at='2026-10-18T00:00:00Z')]
    tracker=SimpleNamespace(decision_rows=lambda _: Answer.complete_read(rows,how='private fixture'))
    answer=projected(tracker,'worker',now=datetime(2026,10,17,tzinfo=timezone.utc)).exact()
    assert [r['id'] for r in answer['top']]==['ranked','older']
    assert [r['id'] for r in answer['coming']]==['older','ranked']
