"""st start — booting the town by MODE (Stiwi, owner-directed).

The property this whole command lives or dies on: it is IDEMPOTENT and
DECLARATIVE. `already-up` is a SUCCESS, not a refusal, and a live agent is never
launched over — because the operator who most needs a boot command is the one who
does not know what is currently running. A boot built out of `st new` calls
reports failure for the healthy half of a half-up fleet, which is the same defect
as a supervisor that cannot tell "died" from "was stopped on purpose".

The exit code is the other half: 0 = the selected fleet IS up, 1 = refused before
launching ANYTHING, 2 = the pass ran and somebody is not known to be up. A boot
you cannot script on is not a boot.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shantytown import bootstrap, cli, config
from shantytown.protocols import Agent


class _Panes:
    """A tmux stand-in. `launched` records what a launcher created, so a test can
    tell "left alone" from "relaunched" — the distinction the command exists for."""

    def __init__(self, live=()):
        self.live = set(live)

    def exists(self, pane):
        return pane in self.live


def _world(tmp_path, cards: dict, cfg: str | None = None) -> Path:
    """A .shanty root: crew cards, and optionally a shantytown.toml."""
    crew = tmp_path / "crew"
    crew.mkdir(parents=True, exist_ok=True)
    for name, spec in cards.items():
        (crew / f"{name}.json").write_text(json.dumps(spec))
    if cfg is not None:
        (tmp_path / config.CONFIG_NAME).write_text(cfg)
    return tmp_path


ADMIN = {"role": "administrator", "pane": "shanty-sattler"}
LEAD = {"role": "lead", "reports_to": "sattler", "pane": "shanty-arnold"}
WORKER = {"role": "worker", "reports_to": "arnold", "pane": "shanty-billy"}


def _run(monkeypatch, root, argv, *, live=(), launch_rc=cli.OK, comes_up=True):
    """Drive the REAL parser + `_cmd_start` with the launcher stubbed.

    Through main() rather than _cmd_start directly, so the parser wiring (--mode,
    the variadic agent list, -n) is pinned too — a command whose handler works and
    whose flags are not wired is a command nobody can run.
    """
    panes = _Panes(live)
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    monkeypatch.setattr(cli, "_runtime", lambda *_a, **_k: object())
    launched: list[str] = []

    def fake_launch(_a, card, _panes, _runtime, *, dry_run=False):
        launched.append(card.name)
        if comes_up:
            panes.live.add(card.pane)
        return launch_rc

    monkeypatch.setattr(cli, "_launch", fake_launch)
    rc = cli.main(["--root", str(root), "start", *argv])
    return rc, launched


# --- lite is the default, and lite is the administrator ---------------------

def test_no_config_no_flags_starts_the_administrator_ALONE(tmp_path, monkeypatch, capsys):
    """THE token-conservation floor, and the answer to Stiwi's first priority: one
    command brings up the one agent that can decide who else is needed."""
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD, "billy": WORKER})
    rc, launched = _run(monkeypatch, root, [])
    assert rc == cli.OK
    assert launched == ["sattler"], "lite must start the admin and NOBODY else"
    out = capsys.readouterr().out
    assert "lite" in out and "sattler" in out
    assert "no config file" in out, "it must say the defaults are in force"


def test_heavy_starts_every_card_admin_first(tmp_path, monkeypatch):
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD, "billy": WORKER})
    rc, launched = _run(monkeypatch, root, ["--mode", "heavy"])
    assert rc == cli.OK
    assert launched == ["sattler", "arnold", "billy"], (
        "tier order: a worker started before its lead escalates on its first stop")


def test_the_configured_mode_is_used_with_no_flag(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD, "billy": WORKER},
                  cfg='[startup]\nmode = "heavy"\n')
    rc, launched = _run(monkeypatch, root, [])
    assert rc == cli.OK and len(launched) == 3
    assert config.CONFIG_NAME in capsys.readouterr().out, "name the file it obeyed"


def test_a_custom_mode_from_the_config(tmp_path, monkeypatch):
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD, "billy": WORKER},
                  cfg='[modes.night]\ncrew = ["administrator", "lead"]\n')
    rc, launched = _run(monkeypatch, root, ["--mode", "night"])
    assert rc == cli.OK and launched == ["sattler", "arnold"]


# --- idempotence: the whole point ------------------------------------------

def test_an_already_up_agent_is_a_SUCCESS_and_is_never_relaunched(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD, "billy": WORKER})
    rc, launched = _run(monkeypatch, root, ["--mode", "heavy"],
                        live={"shanty-sattler"})
    assert rc == cli.OK, "a live agent must not make a boot fail"
    assert launched == ["arnold", "billy"], "the live admin must be left ALONE"
    assert bootstrap.ALREADY_UP in capsys.readouterr().out


def test_running_it_twice_launches_nothing_the_second_time(tmp_path, monkeypatch):
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD})
    panes = _Panes()
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: panes)
    monkeypatch.setattr(cli, "_runtime", lambda *_a, **_k: object())
    launched: list[str] = []

    def fake_launch(_a, card, _panes, _runtime, *, dry_run=False):
        launched.append(card.name)
        panes.live.add(card.pane)
        return cli.OK

    monkeypatch.setattr(cli, "_launch", fake_launch)
    first = cli.main(["--root", str(root), "start", "--mode", "heavy"])
    n_after_first = len(launched)
    second = cli.main(["--root", str(root), "start", "--mode", "heavy"])
    assert (first, second) == (cli.OK, cli.OK)
    assert n_after_first == 2
    assert len(launched) == 2, "the second boot must launch NOTHING"


# --- explicit agents -------------------------------------------------------

def test_explicit_names_start_exactly_those(tmp_path, monkeypatch):
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD, "billy": WORKER})
    rc, launched = _run(monkeypatch, root, ["billy", "arnold"])
    assert rc == cli.OK
    assert launched == ["arnold", "billy"], "still lead-before-worker"


def test_mode_AND_explicit_agents_is_refused_as_ambiguous(tmp_path, monkeypatch, capsys):
    """Two readings ('heavy plus sattler' / 'sattler from heavy') and picking one
    quietly means the operator who meant the other brings up a fleet they did not
    ask for. Ambiguity about how many agents to bill is not the place to guess."""
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD})
    rc, launched = _run(monkeypatch, root, ["--mode", "heavy", "sattler"])
    assert rc == cli.REFUSED
    assert launched == [], "a refusal must launch NOTHING"
    assert "two different asks" in capsys.readouterr().err


def test_an_unknown_agent_refuses_before_launching_anything(tmp_path, monkeypatch, capsys):
    """A refusal is not a partial boot: it must not start the half it recognised."""
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD})
    rc, launched = _run(monkeypatch, root, ["sattler", "nobody"])
    assert rc == cli.REFUSED
    assert launched == [], "must not start sattler and then discover the typo"
    assert "nobody" in capsys.readouterr().err


# --- refusals from config --------------------------------------------------

def test_an_unknown_mode_is_refused_and_lists_the_real_ones(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"sattler": ADMIN})
    rc, launched = _run(monkeypatch, root, ["--mode", "medium"])
    assert rc == cli.REFUSED and launched == []
    err = capsys.readouterr().err
    assert "medium" in err and "lite" in err and "heavy" in err


def test_a_malformed_config_refuses_rather_than_booting_the_wrong_set(tmp_path, monkeypatch, capsys):
    """`st start` launches agents. Starting the WRONG SET because a key was
    misspelled is worse than starting nothing — so this path uses config.load
    (which raises), never load_or_default."""
    root = _world(tmp_path, {"sattler": ADMIN}, cfg="[startup\nmode = 'lite'")
    rc, launched = _run(monkeypatch, root, [])
    assert rc == cli.REFUSED and launched == []
    assert config.CONFIG_NAME in capsys.readouterr().err


def test_lite_with_no_administrator_says_how_to_make_one(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"billy": WORKER})
    rc, launched = _run(monkeypatch, root, [])
    assert rc == cli.REFUSED and launched == []
    err = capsys.readouterr().err
    assert "roles set" in err and "administrator" in err


def test_a_retired_agent_is_reported_not_started(tmp_path, monkeypatch, capsys):
    """`--mode heavy` is the exact command that would resurrect a considered
    shutdown. It must skip it AND say so — silence here would look like the config
    line was ignored."""
    root = _world(tmp_path, {"sattler": ADMIN,
                             "ellie": {**WORKER, "pane": "shanty-ellie",
                                       "retired": True}})
    rc, launched = _run(monkeypatch, root, ["--mode", "heavy"])
    assert rc == cli.OK
    assert launched == ["sattler"], "a RETIRED agent must never be started"
    out = capsys.readouterr().out
    assert "ellie" in out and bootstrap.RETIRED in out


# --- dry run ---------------------------------------------------------------

def test_dry_run_launches_nothing_and_shows_who_would_start(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD})
    rc, launched = _run(monkeypatch, root, ["--mode", "heavy", "-n"],
                        live={"shanty-sattler"})
    assert rc == cli.OK
    assert launched == []
    out = capsys.readouterr().out
    assert bootstrap.WOULD in out and bootstrap.ALREADY_UP in out


# --- the exit code carries the finding -------------------------------------

def test_a_refused_launch_mid_pass_exits_could_not_tell(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"sattler": ADMIN, "arnold": LEAD})
    rc, launched = _run(monkeypatch, root, ["--mode", "heavy"],
                        launch_rc=cli.REFUSED, comes_up=False)
    assert rc == cli.CANNOT_TELL, (
        "a boot that could not bring the fleet up must not exit 0")
    assert launched == ["sattler", "arnold"], "one refusal does not abort the rest"
    assert bootstrap.REFUSED in capsys.readouterr().out


def test_an_unverified_launch_is_not_counted_as_up(tmp_path, monkeypatch, capsys):
    """The session exists; the runtime was never observed live. Reporting that as
    started is how `st start` becomes unscriptable."""
    root = _world(tmp_path, {"sattler": ADMIN})
    rc, launched = _run(monkeypatch, root, [], launch_rc=cli.CANNOT_TELL)
    assert rc == cli.CANNOT_TELL
    out = capsys.readouterr().out
    assert bootstrap.UNVERIFIED in out


def test_a_card_with_no_pane_is_a_fault_not_an_invented_session(tmp_path, monkeypatch, capsys):
    """`st new` falls back to an `st-<name>` session; a BOOT must not, because a
    session absent from the card is invisible to crew/stop/tend."""
    root = _world(tmp_path, {"sattler": {"role": "administrator"}})
    rc, launched = _run(monkeypatch, root, [])
    assert rc == cli.CANNOT_TELL
    assert launched == []
    assert bootstrap.NO_PANE in capsys.readouterr().out


def test_it_never_attaches(tmp_path, monkeypatch):
    """A boot that attached would block the shell that ran it — the systemd/cron
    caller cannot afford a foreground tmux client. Attaching is `st attach`."""
    root = _world(tmp_path, {"sattler": ADMIN})
    called = []
    monkeypatch.setattr(cli, "_exec_attach",
                        lambda *a, **k: called.append(a) or cli.OK)
    rc, _ = _run(monkeypatch, root, [])
    assert rc == cli.OK and called == []


def test_it_tells_the_operator_how_to_get_in(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"sattler": ADMIN})
    _run(monkeypatch, root, [])
    assert "st attach sattler" in capsys.readouterr().out


def test_the_hibernate_policy_is_reported_when_it_is_on(tmp_path, monkeypatch, capsys):
    """The operator reading `st start` output is holding the config in their head
    at that exact moment. A policy that only manifests as 'the admin went quiet at
    3am' is one nobody connects to a file."""
    root = _world(tmp_path, {"sattler": ADMIN},
                  cfg='[hibernate]\nenabled = true\nmax_quiet_minutes = 70\n')
    _run(monkeypatch, root, [])
    out = capsys.readouterr().out
    assert "hibernate ON" in out and "70 min" in out
    assert "Rule Zero still overrides it" in out, (
        "the precedence must be stated where the policy is announced")


def test_no_hibernate_line_when_it_is_off(tmp_path, monkeypatch, capsys):
    root = _world(tmp_path, {"sattler": ADMIN})
    _run(monkeypatch, root, [])
    assert "hibernate" not in capsys.readouterr().out


# --- the report itself (no CLI, no tmux) -----------------------------------

def test_report_up_counts_launched_and_already_running():
    rep = bootstrap.BootReport(mode="heavy", findings=[
        bootstrap.Started("a", bootstrap.STARTED, acted=True),
        bootstrap.Started("b", bootstrap.ALREADY_UP),
        bootstrap.Started("c", bootstrap.REFUSED, "no settings"),
    ])
    assert rep.up() == ["a", "b"]
    assert not rep.healthy()
    assert [f.agent for f in rep.faults] == ["c"]


def test_a_dry_run_report_is_healthy():
    rep = bootstrap.BootReport(mode="lite", dry_run=True, findings=[
        bootstrap.Started("a", bootstrap.WOULD)])
    assert rep.healthy(), "WOULD is an answer, not a fault"


def test_the_dry_run_tally_counts_WOULD_not_acted():
    """A dry run has acted on nothing by construction, so the tally under a "would
    start" header must count WOULD verdicts. A summary that contradicts the list
    directly above it is worse than no summary."""
    rep = bootstrap.BootReport(mode="heavy", dry_run=True, findings=[
        bootstrap.Started(n, bootstrap.WOULD) for n in ("a", "b", "c")])
    line = rep.render().splitlines()[-1]
    assert "would start 3" in line


def test_the_render_is_in_LAUNCH_order_not_alphabetical():
    """The order is the boot order (admin, leads, workers) — the one thing about a
    boot a reader might want to check, so it must not be sorted away."""
    rep = bootstrap.BootReport(mode="heavy", findings=[
        bootstrap.Started("zia", bootstrap.STARTED, acted=True),
        bootstrap.Started("arnold", bootstrap.STARTED, acted=True),
    ])
    rows = [l.split()[1] for l in rep.render().splitlines() if l.strip() and "mode" not in l]
    assert rows == ["zia", "arnold"]


def test_an_unverified_launch_still_counts_as_ACTED():
    """The session exists, so a second `st start` will find it already-up. A report
    claiming nothing happened would send the operator back to a command whose
    behaviour has already changed."""
    boot = bootstrap.Bootstrapper(
        _Panes(), launch=lambda card: (bootstrap.UNVERIFIED, "not observed live"))
    rep = boot.bring_up([Agent(name="a", pane="p-a")])
    assert rep.findings[0].acted is True
    assert rep.up() == [], "and it is NOT counted as up"
