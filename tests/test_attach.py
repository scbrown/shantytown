"""st attach — attach to a crew member by name (aegis-de9f).

st already knows the socket (declared_socket) and the pane (registry), so the
operator never types `tmux -L gt-ae5f35 attach -t shanty-weaver`. These tests pin
the argv/env st hands off — THROUGH shanty when present, bare tmux otherwise — the
refusal discipline (unknown agent, by name, never a raw tmux error), and that -r
produces a read-only attach.

A DOWN AGENT IS LAUNCHED, not refused (owner-directed): the human who typed
`st attach weaver` has already decided they want weaver's pane. The refuse-only
behaviour stays reachable under `--no-start` and is pinned below, because a script
that attaches to whatever is running must be able to promise it creates nothing.
"""
from __future__ import annotations
import json
from pathlib import Path

from shantytown import cli
from shantytown.cli import _attach_argv


# --- the pure argv/env builder ----------------------------------------------

def test_through_shanty_when_present_sets_the_socket_env():
    argv, env = _attach_argv("shanty-weaver", "gt-ae5f35", read_only=False,
                             has_shanty=True)
    assert argv == ["shanty", "attach", "shanty-weaver"]
    # The socket rides in the env shanty honours — the operator never typed it.
    assert env == {"SHANTY_TMUX_SOCKET": "gt-ae5f35"}


def test_through_shanty_read_only_passes_r():
    argv, _ = _attach_argv("shanty-weaver", "gt-ae5f35", read_only=True,
                           has_shanty=True)
    assert argv == ["shanty", "attach", "-r", "shanty-weaver"]


def test_falls_back_to_bare_tmux_when_shanty_absent():
    argv, env = _attach_argv("aegis-crew-arnold", "gt-ae5f35", read_only=False,
                             has_shanty=False)
    assert argv == ["tmux", "-L", "gt-ae5f35", "attach-session",
                    "-t", "aegis-crew-arnold"]
    assert env == {}


def test_bare_tmux_read_only_appends_r():
    argv, _ = _attach_argv("p", "sock", read_only=True, has_shanty=False)
    assert argv[-1] == "-r"


def test_no_socket_omits_the_L_flag_and_env():
    # A store with no declared socket -> the default tmux server, no -L, no env.
    argv, env = _attach_argv("p", None, read_only=False, has_shanty=True)
    assert env == {}
    argv2, _ = _attach_argv("p", None, read_only=False, has_shanty=False)
    assert "-L" not in argv2


# --- the command: resolution, refusal, hand-off -----------------------------

class _Panes:
    def __init__(self, live):
        self._live = set(live)

    def exists(self, pane):
        return pane in self._live


class _Args:
    def __init__(self, root, agent=None, read_only=False, no_start=False):
        self.root = Path(root)
        self.agent = agent
        self.read_only = read_only
        self.no_start = no_start
        self.registry = "files"; self.backend = None; self.repo = None


def _world(tmp_path, cards, socket="gt-ae5f35"):
    crew = tmp_path / "crew"; crew.mkdir()
    for name, pane in cards.items():
        crew / f"{name}.json"
        (crew / f"{name}.json").write_text(
            json.dumps({"role": "worker", "pane": pane} if pane else {"role": "worker"}))
    if socket is not None:
        (tmp_path / "shantytown.toml").write_text(
            f'[tmux]\nsocket = "{socket}"\n')
    return tmp_path


def _run(monkeypatch, a, live, has_shanty=True):
    """Run _cmd_attach with the exec + tmux + shutil.which stubbed. Returns
    (exit_code, argv, env) — argv/env None when it refused/listed before hand-off."""
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(live))
    captured = {}

    def fake_exec(argv, env):
        captured["argv"] = argv
        captured["env"] = env
        return cli.OK

    rc = cli._cmd_attach(a, execer=fake_exec,
                         which=lambda name: "/bin/shanty" if has_shanty else None)
    return rc, captured.get("argv"), captured.get("env")


def test_attach_resolves_the_agents_pane_and_socket(tmp_path, monkeypatch):
    root = _world(tmp_path, {"weaver": "shanty-weaver"})
    rc, argv, env = _run(monkeypatch, _Args(root, "weaver"), live={"shanty-weaver"})
    assert rc == cli.OK
    assert argv == ["shanty", "attach", "shanty-weaver"]
    assert env == {"SHANTY_TMUX_SOCKET": "gt-ae5f35"}


def test_attach_read_only(tmp_path, monkeypatch):
    root = _world(tmp_path, {"weaver": "shanty-weaver"})
    rc, argv, _ = _run(monkeypatch, _Args(root, "weaver", read_only=True),
                       live={"shanty-weaver"})
    assert rc == cli.OK
    assert "-r" in argv


def test_attach_a_foreign_pane_name_via_shanty(tmp_path, monkeypatch):
    # arnold's pane is aegis-crew-arnold, not shanty-arnold; st passes the REAL
    # pane and shanty (companion change) attaches it literally.
    root = _world(tmp_path, {"arnold": "aegis-crew-arnold"})
    rc, argv, _ = _run(monkeypatch, _Args(root, "arnold"), live={"aegis-crew-arnold"})
    assert rc == cli.OK
    assert argv == ["shanty", "attach", "aegis-crew-arnold"]


def test_attach_falls_back_to_tmux_without_shanty(tmp_path, monkeypatch):
    root = _world(tmp_path, {"weaver": "shanty-weaver"})
    rc, argv, _ = _run(monkeypatch, _Args(root, "weaver"), live={"shanty-weaver"},
                       has_shanty=False)
    assert rc == cli.OK
    assert argv == ["tmux", "-L", "gt-ae5f35", "attach-session", "-t", "shanty-weaver"]


def test_attach_refuses_an_unknown_agent_by_name(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"weaver": "shanty-weaver"})
    rc, argv, _ = _run(monkeypatch, _Args(root, "nobody"), live={"shanty-weaver"})
    assert rc == cli.REFUSED
    assert argv is None, "must not exec on a refusal"
    err = capsys.readouterr().err
    assert "nobody" in err and "refused" in err


def test_no_start_refuses_a_down_agent_by_name_not_a_raw_tmux_error(tmp_path, monkeypatch, capsys):
    """--no-start must never create a session."""
    root = _world(tmp_path, {"weaver": "shanty-weaver"})
    # weaver is on the card but NOT live in tmux.
    launched = []
    monkeypatch.setattr(cli, "_launch", lambda *a, **k: launched.append(a) or cli.OK)
    rc, argv, _ = _run(monkeypatch, _Args(root, "weaver", no_start=True), live=set())
    assert rc == cli.REFUSED
    assert argv is None
    assert launched == [], "--no-start must not launch anything"
    err = capsys.readouterr().err
    assert "weaver is down" in err and "--no-start" in err


# --- a down agent is STARTED, then attached ---------------------------------

def _run_launching(monkeypatch, a, live, *, launch_rc=cli.OK, comes_up=True):
    """_cmd_attach with a STUBBED launcher. Returns (rc, argv, launched-names).

    The launcher is stubbed rather than exercised because `_launch` is `st new`'s
    whole seam (workspace, provisioning, hooks verification) and it has its own
    tests; what THIS module has to pin is that attach calls it, honours its
    verdict, and hands off to the same argv builder afterwards.
    """
    panes = _Panes(live)
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    monkeypatch.setattr(cli, "_runtime", lambda *_a, **_k: object())
    launched = []

    def fake_launch(_a, card, _panes, _runtime, *, dry_run=False):
        launched.append(card.name)
        if comes_up:
            panes._live.add(card.pane)       # the session now exists
        return launch_rc

    monkeypatch.setattr(cli, "_launch", fake_launch)
    captured = {}

    def fake_exec(argv, env):
        captured["argv"] = argv
        return cli.OK

    rc = cli._cmd_attach(a, execer=fake_exec, which=lambda _n: "/bin/shanty")
    return rc, captured.get("argv"), launched


def test_a_down_agent_is_launched_then_attached(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"weaver": "shanty-weaver"})
    rc, argv, launched = _run_launching(monkeypatch, _Args(root, "weaver"), live=set())
    assert rc == cli.OK
    assert launched == ["weaver"], "a down agent must be started"
    assert argv == ["shanty", "attach", "shanty-weaver"], "and then attached"
    assert "starting it" in capsys.readouterr().out


def test_a_live_agent_is_never_relaunched(tmp_path, monkeypatch):
    """Idempotence at the attach seam: a running agent is attached to, never
    launched over. Typing into a working agent is the one thing every surface in
    this repo refuses to do."""
    root = _world(tmp_path, {"weaver": "shanty-weaver"})
    rc, argv, launched = _run_launching(monkeypatch, _Args(root, "weaver"),
                                       live={"shanty-weaver"})
    assert rc == cli.OK
    assert launched == [], "a LIVE agent must not be relaunched"
    assert argv == ["shanty", "attach", "shanty-weaver"]


def test_a_refused_launch_does_not_attach(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"weaver": "shanty-weaver"})
    rc, argv, launched = _run_launching(monkeypatch, _Args(root, "weaver"), live=set(),
                                        launch_rc=cli.REFUSED, comes_up=False)
    assert rc == cli.REFUSED
    assert launched == ["weaver"]
    assert argv is None, "must not attach into a session the launcher refused to make"
    assert "not attaching" in capsys.readouterr().err


def test_an_unverified_launch_attaches_anyway_but_says_so(tmp_path, monkeypatch, capsys):
    """The session EXISTS and the runtime could not be confirmed live. The useful
    action is the human's eyes on that pane — but it must never be reported as a
    clean start."""
    root = _world(tmp_path, {"weaver": "shanty-weaver"})
    rc, argv, launched = _run_launching(monkeypatch, _Args(root, "weaver"), live=set(),
                                        launch_rc=cli.CANNOT_TELL)
    assert launched == ["weaver"]
    assert argv == ["shanty", "attach", "shanty-weaver"], "attach anyway"
    err = capsys.readouterr().err
    assert "NOT verified" in err


def test_a_card_with_no_pane_is_refused_not_invented(tmp_path, monkeypatch, capsys):
    """`_launch` would fall back to an `st-<name>` session; attach must NOT take
    that path. A session absent from the card is invisible to crew/stop/tend."""
    root = _world(tmp_path, {"ghost": None})
    rc, argv, launched = _run_launching(monkeypatch, _Args(root, "ghost"), live=set())
    assert rc == cli.REFUSED
    assert launched == [] and argv is None
    err = capsys.readouterr().err
    assert "NO pane on its card" in err


def test_bare_attach_starts_a_DOWN_administrator(tmp_path, monkeypatch):
    """THE BOOTSTRAP CASE (Stiwi's first priority): a cold host. `st attach` with
    no argument resolves the administrator from the registry — liveness is not part
    of that resolution — starts it, and hands over the terminal. One command."""
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "sattler.json").write_text(
        json.dumps({"role": "administrator", "pane": "shanty-sattler"}))
    (tmp_path / "shantytown.toml").write_text(
        '[tmux]\nsocket = "gt-ae5f35"\n')
    rc, argv, launched = _run_launching(monkeypatch, _Args(tmp_path, agent=None),
                                        live=set())
    assert rc == cli.OK
    assert launched == ["sattler"]
    assert argv == ["shanty", "attach", "shanty-sattler"]


def test_no_arg_defaults_to_the_administrator_not_SHANTY_AGENT(tmp_path, monkeypatch):
    # The coordinator is the useful default — NOT the operator's own pane. Even
    # with SHANTY_AGENT set to a worker, no-arg opens the administrator.
    crew = tmp_path / "crew"; crew.mkdir()
    (crew / "weaver.json").write_text(json.dumps({"role": "worker", "pane": "shanty-weaver"}))
    (crew / "sattler.json").write_text(json.dumps({"role": "administrator", "pane": "shanty-sattler"}))
    (tmp_path / "shantytown.toml").write_text(
        '[tmux]\nsocket = "gt-ae5f35"\n')
    monkeypatch.setenv("SHANTY_AGENT", "weaver")
    rc, argv, _ = _run(monkeypatch, _Args(tmp_path, agent=None),
                       live={"shanty-weaver", "shanty-sattler"})
    assert rc == cli.OK
    assert argv == ["shanty", "attach", "shanty-sattler"], "no-arg must open the admin"


def test_no_arg_no_administrator_lists_choices(tmp_path, monkeypatch, capsys):
    # No administrator in the registry -> list, don't error.
    root = _world(tmp_path, {"weaver": "shanty-weaver", "ellie": "shanty-ellie"})
    monkeypatch.delenv("SHANTY_AGENT", raising=False)
    rc, argv, _ = _run(monkeypatch, _Args(root, agent=None), live={"shanty-weaver"})
    assert rc == cli.OK
    assert argv is None, "listing choices must not exec"
    out = capsys.readouterr().out
    assert "no administrator" in out
    assert "ellie" in out and "weaver" in out
