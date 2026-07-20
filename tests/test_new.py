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


# --- launch-time HOOK verification (aegis-8p0j gap 1, aegis-05up) ------------
#
# arnold: "the NEGATIVE control is the deliverable here. A test that only proves
# the happy path is not evidence." That is literal — while this bug was live,
# `st new` returned 0 and every happy-path test was green. The bug WAS the
# happy path. So each test below launches an agent whose graph position REQUIRES
# a direction, and varies only what the live process actually carries.

def _hooked_world(tmp_path, *, directions=("send",), role="worker"):
    """A crew where ellie REPORTS TO lead (so the graph requires `send`), and the
    role's settings carry exactly `directions`."""
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps(
        {"role": role, "pane": "crew-ellie", "reports_to": "lead"}))
    (crew / "lead.json").write_text(json.dumps({"role": "lead"}))
    sdir = tmp_path / "settings"; sdir.mkdir()
    hooks = [{"hooks": [{"command": f"python -m shantytown.stop_event {d}"}]}
             for d in directions]
    (sdir / f"{role}.settings.json").write_text(
        json.dumps({"hooks": {"Stop": hooks}}))
    return tmp_path


def test_new_FAILS_when_the_live_process_came_up_hookless(tmp_path, monkeypatch, capsys):
    """THE aegis-05up FAILURE MODE. The pane is live, the process is running, and
    it carries no stop hooks. Before this, that was exit 0 and the word
    'started'."""
    root = _hooked_world(tmp_path, directions=())        # settings emit NOTHING
    panes = NullPanes(screen=READY, live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)

    rc = cli._cmd_new(_Args(root=root))

    err = capsys.readouterr().err
    assert rc == cli.REFUSED, "a hookless launch reported success"
    assert "WITHOUT the stop hooks" in err
    assert "send" in err, "did not name the missing direction"
    # dearing's 0v97 correction: never claim "no hooks at all" — say what it HAS.
    assert "no `stop_event` hook" in err
    assert "NO stop hooks at all" not in err


def test_new_FAILS_naming_only_the_direction_that_is_MISSING(tmp_path, monkeypatch, capsys):
    """A lead that can send but cannot drain strands its reports, not itself.
    The message must name drain — the one that is missing — not just 'hooks'."""
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "ellie.json").write_text(json.dumps(
        {"role": "lead", "pane": "crew-ellie", "reports_to": "admin"}))
    (crew / "admin.json").write_text(json.dumps({"role": "admin"}))
    (crew / "w1.json").write_text(json.dumps({"role": "worker", "reports_to": "ellie"}))
    sdir = tmp_path / "settings"; sdir.mkdir()
    (sdir / "lead.settings.json").write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"command": "python -m shantytown.stop_event send"}]}]}}))
    panes = NullPanes(screen=READY, live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)

    rc = cli._cmd_new(_Args(root=tmp_path))

    err = capsys.readouterr().err
    assert rc == cli.REFUSED
    assert "drain" in err
    assert "['send']" in err, "did not report what it DOES carry"


def test_new_is_OK_when_the_live_process_carries_what_the_graph_needs(tmp_path, monkeypatch, capsys):
    """The positive control. Same code path, same graph requirement — the only
    difference is that the process really is hooked."""
    root = _hooked_world(tmp_path, directions=("send",))
    panes = NullPanes(screen=READY, live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)

    rc = cli._cmd_new(_Args(root=root))

    assert rc == cli.OK
    assert "VERIFIED" in capsys.readouterr().out


def test_new_is_CANNOT_TELL_when_the_hooks_cannot_be_READ(tmp_path, monkeypatch, capsys):
    """Unreadable is not hookless, and must never be reported as either a pass
    or a failure — the same contract live_stop_directions already holds."""
    root = _hooked_world(tmp_path)
    panes = NullPanes(screen=READY, live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)
    monkeypatch.setattr(panes, "cmdline", lambda pane: None)   # cannot look

    rc = cli._cmd_new(_Args(root=root))

    err = capsys.readouterr().err
    assert rc == cli.CANNOT_TELL
    assert "UNVERIFIED" in err
    assert "WITHOUT the stop hooks" not in err, "reported a cannot-tell as a definite failure"


def test_a_FAILED_verification_still_leaves_the_pane_for_inspection(tmp_path, monkeypatch, capsys):
    """Documented choice, pinned so it cannot change silently: we do NOT reap on
    a verdict. The pane is the evidence of what went wrong, and a launcher that
    kills on a bad verdict is one bad verdict away from killing healthy agents.
    The operator is told to run `st stop`."""
    root = _hooked_world(tmp_path, directions=())
    panes = NullPanes(screen=READY, live=set())
    monkeypatch.setattr(cli, "Tmux", lambda: panes)

    assert cli._cmd_new(_Args(root=root)) == cli.REFUSED
    assert panes.exists("crew-ellie"), "reaped the evidence"
    assert "st stop ellie" in capsys.readouterr().err, "did not name the remedy"

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
