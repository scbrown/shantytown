"""Submission evidence must be stronger than a successful send-keys call."""
import subprocess

import pytest

from shantytown import tmux

FOOTER = '  gpt-6 high · /workspace'
READY = '\x1b[1m›\x1b[0m \x1b[2mAsk Codex to do anything\x1b[0m\n' + FOOTER
BUSY = 'Working (1s • esc to interrupt)\n' + READY
BODY = 'Delivery control: continue the assigned work.'
OWNED = '\x1b[1m›\x1b[0m ' + BODY + '\n\n' + FOOTER
QUEUED = ('Working (1s • esc to interrupt)\n'
          'Messages to be submitted after next tool call\n  ↳ ' + BODY + '\n' + READY)


def transport(monkeypatch, frames, foreground='codex'):
    panes = tmux.Tmux()
    calls = []
    frames = iter(frames)
    last = ['']

    def capture(_pane, **_kw):
        last[0] = next(frames, last[0])
        if isinstance(last[0], Exception):
            raise last[0]
        return last[0]

    monkeypatch.setattr(panes, 'capture', capture)
    monkeypatch.setattr(panes, 'foreground', lambda _: foreground)
    monkeypatch.setattr(tmux, '_journal_send', lambda *_: None)
    monkeypatch.setattr(tmux, '_CODEX_POLLS', 2)
    monkeypatch.setattr(tmux.time, 'sleep', lambda seconds: calls.append(('sleep', seconds)))
    monkeypatch.setattr(tmux.subprocess, 'run', lambda args, **kw: calls.append(args))
    return panes, calls


def keys(calls):
    return [c for c in calls if 'send-keys' in c]


def test_live_turn_is_observed_after_a_separate_settle(monkeypatch):
    panes, calls = transport(monkeypatch, [READY, BUSY])
    panes.send('worker', BODY)
    first_enter = next(i for i, c in enumerate(calls) if c[-1] == 'Enter')
    assert calls[first_enter - 1] == ('sleep', tmux._CODEX_SETTLE_S)
    assert len(keys(calls)) == 2


def test_one_enter_retry_recovers_only_our_pending_body(monkeypatch):
    panes, calls = transport(monkeypatch, [READY, OWNED, OWNED, BUSY])
    panes.send('worker', BODY)
    sends = keys(calls)
    assert [c[-1] for c in sends if '-l' not in c] == ['Enter', 'Enter']
    assert [c[-1] for c in sends if '-l' in c] == [BODY]


@pytest.mark.parametrize('retry_foreground', [None, 'bash', 'claude', 'unrecognized', 'node'])
def test_retry_requires_positive_codex_identity(monkeypatch, retry_foreground):
    panes, calls = transport(monkeypatch, [READY, OWNED, OWNED, BUSY])
    foregrounds = iter(['codex', retry_foreground])
    monkeypatch.setattr(panes, 'foreground', lambda _: next(foregrounds))
    monkeypatch.setattr(panes, 'cmdline', lambda _: None)
    with pytest.raises(tmux.PaneSubmissionUnverified):
        panes.send('worker', BODY)
    assert sum(c[-1] == 'Enter' for c in keys(calls)) == 1


def test_retry_accepts_node_only_with_live_codex_identity(monkeypatch):
    panes, calls = transport(monkeypatch, [READY, OWNED, OWNED, BUSY])
    foregrounds = iter(['codex', 'node'])
    monkeypatch.setattr(panes, 'foreground', lambda _: next(foregrounds))
    monkeypatch.setattr(panes, 'cmdline', lambda _: 'CODEX_HOME=/tmp/probe node /opt/codex')
    panes.send('worker', BODY)
    assert sum(c[-1] == 'Enter' for c in keys(calls)) == 2


def test_persistent_stranding_is_unverified_and_never_resends_the_body(monkeypatch):
    panes, calls = transport(monkeypatch, [READY, OWNED])
    with pytest.raises(tmux.PaneSubmissionUnverified, match='UNVERIFIED'):
        panes.send('worker', BODY)
    assert sum(c[-1] == 'Enter' for c in keys(calls)) == 2
    assert [c[-1] for c in keys(calls) if '-l' in c] == [BODY]


def test_someone_elses_input_never_gets_a_retry_enter(monkeypatch):
    foreign = OWNED.replace(BODY, 'Unrelated operator input')
    panes, calls = transport(monkeypatch, [READY, foreign])
    with pytest.raises(tmux.PaneSubmissionUnverified):
        panes.send('worker', BODY)
    assert sum(c[-1] == 'Enter' for c in keys(calls)) == 1


def test_existing_input_is_not_appended_to_or_submitted(monkeypatch):
    panes, calls = transport(monkeypatch, [OWNED])
    with pytest.raises(tmux.PaneSubmissionUnverified, match='nothing added'):
        panes.send('worker', BODY)
    assert keys(calls) == []


def test_clipped_composer_is_not_treated_as_empty(monkeypatch):
    clipped = '\n'.join(['typed text wrapped beyond the prompt'] * 20) + '\n' + FOOTER
    panes, calls = transport(monkeypatch, [clipped])
    with pytest.raises(tmux.PaneSubmissionUnverified, match='nothing added'):
        panes.send('worker', BODY)
    assert keys(calls) == []


def test_old_activity_with_no_new_queue_evidence_is_not_success(monkeypatch):
    panes, calls = transport(monkeypatch, [BUSY, BUSY])
    with pytest.raises(tmux.PaneSubmissionUnverified):
        panes.send('worker', BODY)
    assert sum(c[-1] == 'Enter' for c in keys(calls)) == 1


def test_observed_queue_with_our_preview_is_submission(monkeypatch):
    panes, _ = transport(monkeypatch, [BUSY, QUEUED])
    panes.send('worker', BODY)


def test_other_queue_message_is_not_our_receipt(monkeypatch):
    panes, _ = transport(monkeypatch, [BUSY, QUEUED.replace(BODY, 'Another message')])
    with pytest.raises(tmux.PaneSubmissionUnverified):
        panes.send('worker', BODY)


def test_a_preexisting_queue_preview_is_not_a_new_receipt(monkeypatch):
    panes, _ = transport(monkeypatch, [QUEUED, QUEUED])
    with pytest.raises(tmux.PaneSubmissionUnverified):
        panes.send('worker', BODY)


def test_node_hosted_codex_is_identified_from_its_live_settings(monkeypatch):
    panes, _ = transport(monkeypatch, [READY, BUSY], foreground='node')
    monkeypatch.setattr(panes, 'cmdline', lambda _: 'CODEX_HOME=/tmp/probe node /opt/codex')
    panes.send('worker', BODY)


def test_capture_timeout_is_unverified_without_enter_retry(monkeypatch):
    panes, calls = transport(monkeypatch, [READY, subprocess.TimeoutExpired('tmux', 2)])
    with pytest.raises(tmux.PaneSubmissionUnverified):
        panes.send('worker', BODY)
    assert sum(c[-1] == 'Enter' for c in keys(calls)) == 1


def test_launcher_and_other_harnesses_keep_their_transport(monkeypatch):
    panes, calls = transport(monkeypatch, [READY], foreground='claude')
    panes.send('worker', BODY)
    assert not any(c == ('sleep', tmux._CODEX_SETTLE_S) for c in calls)
    assert len(keys(calls)) == 2


def test_ghost_and_partial_input_cannot_authorize_enter():
    assert not tmux._codex_owned_input(READY, 'Ask Codex to do anything')
    assert not tmux._codex_owned_input(OWNED.replace(BODY, BODY[:10]), BODY)
    assert tmux._codex_owned_input(OWNED, BODY)
    assert not tmux._codex_owned_input(OWNED.replace('assigned work', 'assignedwork'), BODY)
