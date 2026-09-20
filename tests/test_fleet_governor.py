"""Probe freshness is independent of receiving host and transport arrival time."""
import pytest

from shantytown.fleet_governor import FreshestReader
from shantytown.governor import GovernorError, Reading


def test_selects_each_windows_freshest_producer_observation():
    first = {'five_hour': Reading(pct=20, at=100),
             'seven_day': Reading(pct=30, at=200)}
    second = {'five_hour': Reading(pct=80, at=200),
              'seven_day': Reading(pct=90, at=100)}
    reader = FreshestReader({'desktop': first, 'laptop': second})
    assert reader.read_all() == {'five_hour': second['five_hour'],
                                 'seven_day': first['seven_day']}
    assert reader.provenance == {'five_hour': 'laptop', 'seven_day': 'desktop'}


def test_new_failure_cannot_be_hidden_by_older_success():
    failed = Reading(pct=12, at=200, ok=False, probe_http_status=429)
    reader = FreshestReader({'desktop': {'five_hour': Reading(pct=10, at=100)},
                             'laptop': {'five_hour': failed}})
    assert reader.read_all()['five_hour'] == failed
    assert reader.read_all()['five_hour'].lost(now=201, max_age=300)


def test_ties_have_same_provenance_from_either_host():
    first = {'five_hour': Reading(pct=20, at=100)}
    second = {'five_hour': Reading(pct=40, at=100)}
    one = FreshestReader({'desktop': first, 'laptop': second})
    two = FreshestReader({'laptop': second, 'desktop': first})
    assert one.read_all() == two.read_all()
    assert one.provenance == two.provenance == {'five_hour': 'desktop'}


def test_undated_observation_does_not_supplant_dated_one():
    dated = Reading(pct=80, at=100)
    reader = FreshestReader({'desktop': {'five_hour': Reading(pct=10)},
                             'laptop': {'five_hour': dated}})
    assert reader.read_all()['five_hour'] == dated
    unaged = FreshestReader({'desktop': {'five_hour': Reading(pct=10)}})
    assert unaged.read_all()['five_hour'].at is None
    assert unaged.read_all()['five_hour'].lost(now=201, max_age=300)


@pytest.mark.parametrize('stamp', [float('nan'), float('inf'), True, '200'])
def test_invalid_timestamp_refuses_instead_of_winning_freshness(stamp):
    with pytest.raises(GovernorError):
        FreshestReader({'desktop': {'five_hour': Reading(pct=10, at=stamp)}})


def test_retains_cache_age_and_does_not_refresh_stale_data_on_read():
    observation = Reading(pct=10, at=100, cache_age=5000)
    reader = FreshestReader({'desktop': {'five_hour': observation}})
    assert reader.read_all()['five_hour'] == observation
    assert reader.read_all()['five_hour'].lost(now=101, max_age=300)
    returned = reader.read_all()
    returned.clear()
    assert reader.read_all()['five_hour'] == observation
