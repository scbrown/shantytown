"""st new — bring up a HOOKED agent session. shantytown #5.

The command chains the two seams: new_session (empty pane) -> Runtime.start
(compose w/ --settings, send) -> verify PROCESS live -> 0/1/2. Every exit code
has a both-outcomes test; the negative control that matters is arnold's: a launch
that never comes up MUST be 2, not 0.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import cli
from shantytown.tmux import NullPanes


READY = "… Welcome to Claude Code …\n? for shortcuts"


def _world(tmp_path: Path, *, role="worker", pane="crew-ellie", settings=True,
           **card_fields):
    """A crew card, and (optionally) the role's settings file — the thing #6
    emits. Present = compose can materialize; absent = compose refuses."""
    crew = tmp_path / "crew"; crew.mkdir()
    card = {"role": role}
    card.update({k: v for k, v in card_fields.items() if v is not None})
    if pane is not None:
        card["pane"] = pane
    (crew / "ellie.json").write_text(json.dumps(card))
    if settings:
        sdir = tmp_path / "settings"; sdir.mkdir()
        (sdir / f"{role}.settings.json").write_text("{}")
    return tmp_path


class _Args:
    def __init__(self, **kw):
        self.root = kw.pop("root")
        self.agent = kw.pop("agent", "ellie")
        self.dry_run = kw.pop("dry_run", False)
        self.backend = "files"; self.repo = None
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    # a real launch takes seconds; a test must not wait.
    monkeypatch.setattr(cli, "_LIVE_ATTEMPTS", 1)
    monkeypatch.setattr(cli, "_LIVE_DELAY", 0)


# --- exit 0: session created, --settings composed, runtime observed live ----

def test_new_starts_and_verifies_live(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    panes = NullPanes(screen=READY, live=set())   # banner already up -> live
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_new(_Args(root=root))
    assert rc == cli.OK
    out = capsys.readouterr().out
    assert "started ellie" in out
    # the seam actually delivered a launch carrying --settings + identity
    assert panes.sent, "st new claimed started but sent nothing"
    _, text = panes.sent[-1]
    assert "SHANTY_AGENT=ellie" in text and "--settings" in text
    assert panes.exists("crew-ellie")


# --- exit 2: launched but never observed live (THE negative control) --------

def test_new_returns_2_when_runtime_never_comes_up(tmp_path, monkeypatch, capsys):
    """A launch that never comes up MUST be could-not-tell, never a cheerful 0.
    NullPanes with no banner: send lands, but the ready marker never appears."""
    root = _world(tmp_path)
    panes = NullPanes(screen="user@host:~$", live=set())   # bare shell, no banner
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_new(_Args(root=root))
    assert rc == cli.CANNOT_TELL
    assert "could not tell" in capsys.readouterr().err
    # it DID try to launch (this is could-not-confirm, not refuse)
    assert panes.sent


# --- exit 1: refusals, each creating NOTHING --------------------------------

def test_new_refuses_unknown_agent(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    monkeypatch.setattr(cli, "Tmux", lambda: NullPanes(live=set()))
    rc = cli._cmd_new(_Args(root=root, agent="nobody"))
    assert rc == cli.REFUSED
    assert "refused" in capsys.readouterr().err


def test_new_refuses_when_settings_cannot_be_materialized(tmp_path, monkeypatch, capsys):
    """No settings file (not yet emitted) -> compose refuses -> NOTHING created.
    The invariant: no --settings, no launch."""
    root = _world(tmp_path, settings=False)
    panes = NullPanes(live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_new(_Args(root=root))
    assert rc == cli.REFUSED
    assert "refused" in capsys.readouterr().err
    assert not panes.exists("crew-ellie"), "refuse must create no session"
    assert panes.sent == [], "refuse must launch nothing"


def test_new_refuses_to_clobber_a_live_session(tmp_path, monkeypatch, capsys):
    """The clobber guard — never replace a running agent."""
    root = _world(tmp_path)
    panes = NullPanes(screen=READY, live={"crew-ellie"})   # already live
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_new(_Args(root=root))
    assert rc == cli.REFUSED
    assert "already exists" in capsys.readouterr().err
    assert panes.sent == [], "clobber-refuse must launch nothing"


# --- the workspace leg: ensure the dir, or refuse -------------

def test_new_refuses_when_the_workspace_does_not_exist(tmp_path, monkeypatch, capsys):
    """The launch string `cd`s into card.workspace. If that directory is missing
    and the card carries no source to clone it from, REFUSE — before tmux. Before
    this, compose cd'd into nothing and the break surfaced downstream, as shell
    noise inside a session that had already been created."""
    root = _world(tmp_path, workspace=str(tmp_path / "gone"))
    panes = NullPanes(screen=READY, live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_new(_Args(root=root))
    assert rc == cli.REFUSED
    assert "does not exist" in capsys.readouterr().err
    assert not panes.exists("crew-ellie"), "refuse must create no session"
    assert panes.sent == [], "refuse must launch nothing"


def test_new_clones_an_absent_workspace_then_launches(tmp_path, monkeypatch, capsys):
    ws = tmp_path / "ws" / "ellie"
    root = _world(tmp_path, workspace=str(ws), workspace_source="src")
    panes = NullPanes(screen=READY, live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    monkeypatch.setattr(cli, "ensure_workspace",
                        lambda card: (ws.mkdir(parents=True), str(ws))[1])
    rc = cli._cmd_new(_Args(root=root))
    assert rc == cli.OK
    assert ws.is_dir()
    assert f"cd {ws} &&" in panes.sent[-1][1]


def test_new_dry_run_does_not_ensure_the_workspace(tmp_path, monkeypatch, capsys):
    """Dry-run composes and prints; it must not clone. A dry-run that touches the
    disk is the thing design.md says must never happen."""
    root = _world(tmp_path, workspace=str(tmp_path / "gone"), workspace_source="src")
    monkeypatch.setattr(cli, "Tmux", lambda: NullPanes(live=set()))
    called = []
    monkeypatch.setattr(cli, "ensure_workspace", lambda card: called.append(card))
    rc = cli._cmd_new(_Args(root=root, dry_run=True))
    assert rc == cli.OK
    assert called == [], "dry-run cloned"


def test_new_dry_run_prints_and_creates_nothing(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path)
    panes = NullPanes(live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_new(_Args(root=root, dry_run=True))
    assert rc == cli.OK
    out = capsys.readouterr().out
    assert "would launch" in out and "--settings" in out
    assert not panes.exists("crew-ellie"), "dry-run must create no session"
    assert panes.sent == [], "dry-run must launch nothing"


def test_new_falls_back_to_an_st_prefixed_session_when_the_card_names_no_pane(
        tmp_path, monkeypatch, capsys):
    """A card without a `pane` still has to land somewhere, and WHERE matters.

    The fallback name is the one thing `st new` invents rather than reads, so it
    is the one name that can collide with a session somebody else's tooling is
    already running under. `st-` is reserved for sessions st created; anything
    more generic (or borrowed from whatever crew convention happens to be local)
    puts a live agent one name-match away from being targeted.
    """
    root = _world(tmp_path, pane=None)
    panes = NullPanes(live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    rc = cli._cmd_new(_Args(root=root, dry_run=True))
    assert rc == cli.OK
    assert "st-ellie" in capsys.readouterr().out
