"""Deployment publication policy survives role-settings generation."""
from shantytown.runtime import claude_settings_for_role


def groups(role):
    return [group for group in claude_settings_for_role(role)['hooks']['PreToolUse']
            if group.get('matcher') == 'Artifact']


def test_artifact_guard_only_administrator(monkeypatch):
    monkeypatch.setenv('SHANTY_ADMIN_ARTIFACT_GUARD', '/opt/policy/artifact-check')
    assert groups('administrator') == [{
        'matcher': 'Artifact', 'hooks': [{'type': 'command',
        'command': '/opt/policy/artifact-check', 'timeout': 120}]}]
    assert groups('worker') == []
    assert groups('lead') == []


def test_no_implicit_artifact_policy():
    assert groups('administrator') == []
