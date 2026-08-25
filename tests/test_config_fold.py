"""ONE hand-edited file: deployment config lives in shantytown.toml.

Measured complaint (aegis-8calr): config lived in seven places — env.json,
shantytown.toml, settings/<role>.settings.json, crew/<name>.json,
settings/tmux-socket, hierarchy.*, ~/.config/shantytown/root — plus 19 SHANTY_*
env vars, so "where do I configure this?" had no single answer.

The legacy pair completed its deprecation window and is no longer read.

The tests that matter here are the PRECEDENCE ones. A fold that quietly reordered
which source wins would break deployments in a way no config file mentions.
"""
from __future__ import annotations

from shantytown import config
from shantytown.deployment import deployment_default
from shantytown.tmux import declared_socket


def _toml(root, text):
    (root / "shantytown.toml").write_text(text)


# --- [env] -------------------------------------------------------------------

def test_env_table_answers(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_BACKEND", raising=False)
    _toml(tmp_path, '[env]\nSHANTY_BACKEND = "beads"\n')
    assert deployment_default(tmp_path, "SHANTY_BACKEND") == "beads"


def test_env_json_is_not_configuration(tmp_path, monkeypatch):
    """Deletion lands the reduction: a legacy file cannot remain a second answer."""
    monkeypatch.delenv("SHANTY_BACKEND", raising=False)
    (tmp_path / "env.json").write_text('{"SHANTY_BACKEND": "files"}')
    assert deployment_default(tmp_path, "SHANTY_BACKEND") is None


def test_the_FILE_still_beats_the_environment(tmp_path, monkeypatch):
    """Where a value is authored moved; which source wins did NOT. Flipping this
    would let a stray shell export override a deployment's pinned value."""
    monkeypatch.setenv("SHANTY_BACKEND", "files")
    _toml(tmp_path, '[env]\nSHANTY_BACKEND = "beads"\n')
    assert deployment_default(tmp_path, "SHANTY_BACKEND") == "beads"


def test_the_environment_still_answers_when_no_file_does(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_BACKEND", "forgejo")
    assert deployment_default(tmp_path, "SHANTY_BACKEND") == "forgejo"


def test_an_unknown_env_key_is_NOT_refused(tmp_path, monkeypatch):
    """The one open table in the file: a deployment's own plumbing keys are the
    point, and refusing an unrecognised one would make [env] useless."""
    monkeypatch.delenv("MY_OWN_GUARD", raising=False)
    _toml(tmp_path, '[env]\nMY_OWN_GUARD = "/opt/guard.sh"\n')
    assert config.load(tmp_path).env["MY_OWN_GUARD"] == "/opt/guard.sh"


def test_numbers_and_bools_are_stringified(tmp_path):
    _toml(tmp_path, "[env]\nN = 8000\nB = true\n")
    env = config.load(tmp_path).env
    assert env["N"] == "8000" and env["B"] == "true"


def test_a_TABLE_valued_env_key_is_REFUSED(tmp_path):
    """It would stringify into nonsense far from the file that caused it."""
    _toml(tmp_path, '[env]\n[env.QUIPU_SERVER]\nhost = "graph.example"\n')
    try:
        config.load(tmp_path)
    except config.ConfigError as e:
        assert "QUIPU_SERVER" in str(e) and "string" in str(e)
    else:
        raise AssertionError("a table where a string belongs must refuse")


def test_a_config_TYPO_does_not_wedge_the_hook_path(tmp_path, monkeypatch):
    """deployment_default runs inside the Stop hook: it uses load_or_default, so a
    malformed toml degrades to 'the file said nothing' instead of raising."""
    monkeypatch.setenv("SHANTY_BACKEND", "files")
    _toml(tmp_path, "[env\nbroken")
    assert deployment_default(tmp_path, "SHANTY_BACKEND") == "files"


# --- [tmux] socket -----------------------------------------------------------

def test_tmux_socket_from_the_toml(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_TMUX_SOCKET", raising=False)
    _toml(tmp_path, '[tmux]\nsocket = "shanty"\n')
    assert declared_socket(tmp_path) == "shanty"


def test_the_legacy_socket_file_is_not_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("SHANTY_TMUX_SOCKET", raising=False)
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "tmux-socket").write_text("old\n")
    assert declared_socket(tmp_path) is None


def test_a_declaration_still_beats_the_ambient_env(tmp_path, monkeypatch):
    """The original argument, unchanged: a fleet whose identity depends on which
    pane you ran the command from is the bug."""
    monkeypatch.setenv("SHANTY_TMUX_SOCKET", "whatever-this-shell-holds")
    _toml(tmp_path, '[tmux]\nsocket = "declared"\n')
    assert declared_socket(tmp_path) == "declared"


def test_no_declaration_falls_back_to_the_env_then_None(tmp_path, monkeypatch):
    monkeypatch.setenv("SHANTY_TMUX_SOCKET", "from-env")
    assert declared_socket(tmp_path) == "from-env"
    monkeypatch.delenv("SHANTY_TMUX_SOCKET")
    assert declared_socket(tmp_path) is None


def test_an_unknown_tmux_key_IS_refused(tmp_path):
    """Unlike [env], this table's key set is closed — a silently-dropped `sockett`
    would leave the operator believing a socket was declared."""
    _toml(tmp_path, '[tmux]\nsockett = "typo"\n')
    try:
        config.load(tmp_path)
    except config.ConfigError as e:
        assert "sockett" in str(e)
    else:
        raise AssertionError("an unknown [tmux] key must refuse")
