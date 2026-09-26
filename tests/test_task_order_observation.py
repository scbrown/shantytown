"""Weaker observations must never manufacture passing work or a smaller population."""
import json

import pytest

from shantytown import task_order
from shantytown.task_order_observation import BUCKETS, supplemental
from test_task_order import action, boundary, read, record  # noqa: F401


def shell(record):
    record('Bash', 'PreToolUse', call='shell', command='possibly-modifying-command')
    record('Bash', call='shell', command='possibly-modifying-command', response={})


def test_legacy_output_byte_identical(record, tmp_path):
    boundary(record)
    read(record)
    action(record)
    report = task_order.report(tmp_path)
    legacy = {k: v for k, v in report.items()
              if k not in ('weak_observation', 'strict_population')}
    expected = {
        'scope': 'explicit session-local task declarations; not all dispatches',
        'contexts': [{'id': 1, 'ts': 2.0, 'agent': 'worker', 'session': 's1',
                      'task': 'test-1', 'paired_start': 1, 'verdict': 'PASS',
                      'reason': 'read completed before first definite action start',
                      'reads': 1, 'first_read': 3.0, 'first_material_action': 4.0}],
        'unbound_events': 0, 'status': 'OBSERVED',
    }
    assert json.dumps(legacy, sort_keys=True) == json.dumps(expected, sort_keys=True)


@pytest.mark.parametrize('read_first,bucket', [(True, 'read_before'),
                                             (False, 'reverse_or_concurrent')])
def test_cli_only_stays_unknown_and_in_strict_population(record, tmp_path, read_first, bucket):
    boundary(record)
    if read_first:
        read(record)
    shell(record)
    if not read_first:
        read(record)
    report = task_order.report(tmp_path)
    assert report['contexts'][0]['verdict'] == 'UNKNOWN'
    assert report['weak_observation']['buckets'] == {k: int(k == bucket) for k in BUCKETS}
    assert report['weak_observation']['use_for_gates'] is False
    population = report['strict_population']
    assert population['population'] == population['counts']['UNKNOWN'] == 1
    assert population['headline_pass_fraction_including_unknown'] == 0
    assert population['secondary_pass_fraction_excluding_unknown'] is None


@pytest.mark.parametrize('mode,bucket', [('missing', 'incomplete'),
                                      ('failed', 'no_matched_read'),
                                      ('unpaired', 'incomplete')])
def test_incomplete_and_failed_reads_never_become_weak_success(record, tmp_path, mode, bucket):
    boundary(record, paired=mode != 'unpaired')
    read(record, response={'isError': True} if mode == 'failed' else None)
    action(record, paired=mode != 'missing')
    report = task_order.report(tmp_path)
    assert report['weak_observation']['buckets'][bucket] == 1
    assert report['strict_population']['population'] == 1
    assert report['strict_population']['counts']['PASS'] == 0


def test_duplicate_declaration_cannot_erase_earlier_operation(record, tmp_path):
    boundary(record)
    action(record)
    boundary(record)
    read(record)
    report = task_order.report(tmp_path)
    assert len(report['contexts']) == 1
    assert report['contexts'][0]['verdict'] == 'FAIL'
    assert report['weak_observation']['buckets']['reverse_or_concurrent'] == 1


def test_concurrent_bindings_do_not_lend_a_read(record, tmp_path):
    boundary(record, 'test-1', 's1')
    boundary(record, 'test-2', 's2')
    read(record, 'test-1', 's2')
    action(record, 's2')
    read(record, 'test-1', 's1')
    action(record, 's1')
    report = task_order.report(tmp_path)
    assert report['weak_observation']['buckets'] == {
        'read_before': 1, 'reverse_or_concurrent': 0, 'incomplete': 0, 'no_matched_read': 1}
    assert report['strict_population']['headline_pass_fraction_including_unknown'] == 0.5
    assert report['strict_population']['secondary_pass_fraction_excluding_unknown'] == 1


def test_read_only_excluded_but_explicitly_counted(record, tmp_path):
    boundary(record)
    read(record)
    record('Read', 'PreToolUse', call='file')
    record('Read', call='file', response={})
    report = task_order.report(tmp_path)
    assert report['strict_population']['read_only_or_no_operation_excluded'] == 1
    assert report['strict_population']['excluded_context_ids'] == [1]
    assert report['strict_population']['population'] == 0
    assert report['strict_population']['headline_pass_fraction_including_unknown'] is None
    assert report['weak_observation']['buckets'] == dict.fromkeys(BUCKETS, 0)
    assert report['contexts'][0]['verdict'] == 'UNKNOWN'


def test_equal_timestamps_reverse_and_inputs_unchanged():
    contexts = [{'id': 1, 'paired_start': 1, 'verdict': 'UNKNOWN'}]
    events = [[{'ts': 1, 'kind': 'read_completed'}, {'ts': 1, 'kind': 'ambiguous_start'}]]
    before = json.dumps([contexts, events], sort_keys=True)
    assert supplemental(contexts, events)['weak_observation']['buckets']['reverse_or_concurrent'] == 1
    assert json.dumps([contexts, events], sort_keys=True) == before


def test_empty_store_still_reports_four_buckets_without_creating_it(tmp_path):
    report = task_order.report(tmp_path)
    assert report['weak_observation']['buckets'] == dict.fromkeys(BUCKETS, 0)
    assert not (tmp_path / 'stats.sqlite').exists()
