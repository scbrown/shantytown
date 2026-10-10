"""Route discrimination and actual message/help consumers, with no board writes."""
import json
import subprocess
from pathlib import Path

import pytest

from shantytown import cli, feed_check, help_topics
from shantytown.br import BrTracker
from shantytown.sd import SdTracker
from shantytown.tracker_examples import for_tracker


def remote(monkeypatch, **changes):
    route = dict(mode="remote", prefix="sample", quipu_url="https://board.example",
                 graph="https://seeds.example/project/sample", database_path=None)
    route.update(changes)
    calls = []
    original_run = subprocess.run
    def run(args, **kw):
        if args[0] == "git":
            return original_run(args, **kw)
        calls.append(args)
        assert args[-2:] == ["where", "--json"]
        return subprocess.CompletedProcess(args, 0, json.dumps(route), "")
    monkeypatch.setattr('shantytown.tracker_examples.subprocess.run', run)
    trk = SdTracker(quipu="https://board.example", prefix="sample")
    return trk, calls


def test_remote_examples_pin_the_served_graph_and_all_consumers(monkeypatch):
    tracker, calls = remote(monkeypatch)
    examples = for_tracker(tracker)
    prefix = 'sd --quipu https://board.example --graph https://seeds.example/project/sample'
    assert examples.show('sample-1') == prefix + ' show sample-1'
    msg = feed_check.haul_feed_message('sample-1', 'work', 0, examples=examples)
    resume = feed_check.haul_resume_message('sample-1', 'work', examples=examples)
    help_page = help_topics.render('haul', examples=examples)
    for text in [msg, resume, help_page]:
        assert prefix in text
        assert 'br ' not in text
    assert "close sample-1 --reason '<what landed and proof>'" in msg
    assert "update sample-1 --assignee ''" in msg
    assert len(calls) == 1


@pytest.mark.parametrize('changes', [dict(prefix='other'), dict(graph=None),
    dict(quipu_url='https://other.example'), dict(mode='local')])
def test_unproven_seed_route_omits_all_mutation_recipes(monkeypatch, changes):
    tracker, _ = remote(monkeypatch, **changes)
    examples = for_tracker(tracker)
    assert not examples.argv
    for text in [feed_check.haul_feed_message('sample-1', 'work', 0, examples=examples),
                 feed_check.haul_resume_message('sample-1', 'work', examples=examples),
                 help_topics.render('haul', examples=examples)]:
        assert 'routing is unproven' in text
        assert 'br ' not in text and 'sd close' not in text
        assert 'st work defer' not in text


def test_transport_failure_is_not_echoed_or_a_br_fallback(monkeypatch):
    def fail(*args, **kw):
        raise RuntimeError('SYNTHETIC_SECRET')
    monkeypatch.setattr('shantytown.tracker_examples.subprocess.run', fail)
    examples = for_tracker(SdTracker(quipu='https://board.example', prefix='sample'))
    assert not examples.argv and 'SYNTHETIC_SECRET' not in examples.diagnostic


def test_br_examples_keep_the_explicit_repo_and_quote_values(tmp_path):
    repo = tmp_path / 'board with spaces'
    examples = for_tracker(BrTracker(repo=str(repo)))
    assert examples.show('sample-1') == f"(cd '{repo}' && br show sample-1)"
    assert 'br close sample-1 --reason' in examples.close('sample-1')
    assert "--assignee ''" in examples.unassign('sample-1')
    assert not for_tracker(BrTracker(repo=str(repo), extra_repos=[str(tmp_path / "other")])).argv
    assert not for_tracker(BrTracker()).argv
    assert not for_tracker(object()).argv


def test_local_store_examples_require_an_existing_exact_store(tmp_path, monkeypatch):
    store = tmp_path / 'board.db'
    store.touch()
    def run(args, **kw):
        return subprocess.CompletedProcess(args, 0, json.dumps(dict(
            mode='local', database_path=str(store), prefix='sample')), '')
    monkeypatch.setattr('shantytown.tracker_examples.subprocess.run', run)
    tracker = SdTracker(store=str(store), prefix='sample')
    assert for_tracker(tracker).show('sample-1') == f'sd --store {store} show sample-1'
    store.unlink()
    assert not for_tracker(tracker).argv


def test_cli_help_uses_the_explicit_tracker(monkeypatch, tmp_path, capsys):
    tracker, _ = remote(monkeypatch)
    monkeypatch.setattr(cli, '_tracker', lambda a: tracker)
    assert cli.main(['--root', str(tmp_path), '--backend', 'seeds', 'ops', 'help', 'haul']) == 0
    assert 'sd --quipu https://board.example --graph https://seeds.example/project/sample' in capsys.readouterr().out
