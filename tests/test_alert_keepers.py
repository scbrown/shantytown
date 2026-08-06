"""The alert-owner join must catch durable defects without paging on scale-down."""
from __future__ import annotations

import json
from types import SimpleNamespace

from shantytown import cli


def _root(tmp_path, **cards):
    crew = tmp_path / "crew"
    crew.mkdir()
    for name, card in cards.items():
        (crew / f"{name}.json").write_text(json.dumps(card))
    return tmp_path


def _run(root, rules):
    return cli._cmd_crew(SimpleNamespace(
        root=root, check_alert_keepers=[rules], governor=False,
    ))


def test_alert_keeper_check_accepts_a_stopped_but_launchable_roster_member(tmp_path, capsys):
    root = _root(tmp_path, ellie={"role": "worker", "workspace": str(tmp_path),
                                  "dangerous": True, "pane": "gone"})
    rules = tmp_path / "alerts.yml"
    rules.write_text("""groups:
  - name: test
    rules:
      - alert: KeptAlert
        expr: vector(1)
        labels:
          keeper: ellie
""")

    assert _run(root, rules) == cli.OK
    assert "OK: 1 alert rule" in capsys.readouterr().out


def test_alert_keeper_check_fails_for_missing_unknown_and_unlaunchable_keepers(tmp_path, capsys):
    root = _root(tmp_path,
                 manual={"role": "worker", "workspace": str(tmp_path)},
                 ellie={"role": "worker", "workspace": str(tmp_path), "dangerous": True})
    rules = tmp_path / "alerts.yml"
    rules.write_text("""groups:
  - name: test
    rules:
      - alert: NoKeeper
        expr: vector(1)
      - alert: UnknownKeeper
        expr: vector(1)
        labels:
          keeper: nobody
      - alert: ManualKeeper
        expr: vector(1)
        labels:
          keeper: manual
""")

    assert _run(root, rules) == cli.REFUSED
    out = capsys.readouterr().out
    assert "NoKeeper): missing keeper label" in out
    assert "UnknownKeeper): keeper 'nobody' is not on the roster" in out
    assert "ManualKeeper): keeper 'manual' cannot work unattended (MANUAL MODE)" in out


def test_alert_keeper_check_ignores_comment_text_and_rejects_an_unreadable_input(tmp_path, capsys):
    root = _root(tmp_path, ellie={"role": "worker", "workspace": str(tmp_path),
                                  "dangerous": True})
    rules = tmp_path / "alerts.yml"
    rules.write_text("# - alert: CommentOnly\n#     keeper: nobody\n")

    assert _run(root, rules) == cli.CANNOT_TELL
    assert "no alert stanzas" in capsys.readouterr().err

    assert _run(root, tmp_path / "absent.yml") == cli.CANNOT_TELL
    assert "cannot read" in capsys.readouterr().err
