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


# --- the declared-absence sentinel (aegis-jcr0g) -----------------------------
#
# Two CORRECT guards collided. This check wants every alert owned; aegis-fyxsx
# requires EscalationLadderCanary to carry no keeper for ever, because
# alert-comms-bridge promotes a keeper into chain[0] and would turn a sink-only
# canary into a real page. Neither is wrong and they could not both be satisfied,
# so the check paged `-s high` every 15 minutes for days — and the repair its own
# failure text invited (add a keeper) would have re-armed the bug.
#
# sattler's ruling: a DECLARED absence is not a MISSING label.

def test_a_declared_absence_with_a_reason_is_satisfied(tmp_path, capsys):
    root = _root(tmp_path, ellie={"role": "worker", "workspace": str(tmp_path),
                                  "dangerous": True})
    rules = tmp_path / "alerts.yml"
    rules.write_text("""groups:
  - name: test
    rules:
      - alert: SinkOnlyCanary
        expr: vector(1)
        labels:
          severity: canary
          # keeper: none — sink-only by design; a keeper becomes chain[0] (aegis-fyxsx)
""")

    assert _run(root, rules) == cli.OK
    out = capsys.readouterr().out
    # the count stays HONEST: not silently one fewer alert
    assert "0 alert rule(s) have roster keepers" in out
    assert "1 explicitly unowned (SinkOnlyCanary)" in out


def test_a_bare_none_is_REFUSED_because_it_states_nothing(tmp_path, capsys):
    """The sentinel must not become a quieter way to skip the check. Without a
    reason it expresses no decision, which is the unowned-alert hole this
    function exists to close."""
    root = _root(tmp_path, ellie={"role": "worker", "workspace": str(tmp_path),
                                  "dangerous": True})
    rules = tmp_path / "alerts.yml"
    rules.write_text("""groups:
  - name: test
    rules:
      - alert: LazilySilenced
        expr: vector(1)
        labels:
          # keeper: none
""")

    assert _run(root, rules) == cli.REFUSED
    assert "needs its reason" in capsys.readouterr().out


def test_a_real_keeper_none_LABEL_is_REFUSED_because_the_bridge_reads_it(
        tmp_path, capsys):
    """⛔ THE TRAP ONE LEVEL DOWN. `keeper: none` as a LABEL looks like the
    obvious way to declare an absence, and it is the form the ruling named — but
    alert-comms-bridge truthy-tests labels['keeper'] and seats ANY non-empty
    value as chain[0]. So the label does not declare an absence: it routes tier
    zero to a recipient called 'none', which is the aegis-fyxsx class of defect
    the sentinel exists to avoid."""
    root = _root(tmp_path, ellie={"role": "worker", "workspace": str(tmp_path),
                                  "dangerous": True})
    rules = tmp_path / "alerts.yml"
    rules.write_text("""groups:
  - name: test
    rules:
      - alert: WouldMisroute
        expr: vector(1)
        labels:
          keeper: none  # sink-only by design
""")

    assert _run(root, rules) == cli.REFUSED
    out = capsys.readouterr().out
    assert "must be a COMMENT, not a label" in out
    assert "chain[0]" in out


def test_a_truly_missing_keeper_still_FAILS(tmp_path, capsys):
    """NEGATIVE CONTROL. The sentinel must not have weakened the original check —
    an alert with no keeper label at all is still a defect."""
    root = _root(tmp_path, ellie={"role": "worker", "workspace": str(tmp_path),
                                  "dangerous": True})
    rules = tmp_path / "alerts.yml"
    rules.write_text("""groups:
  - name: test
    rules:
      - alert: Unowned
        expr: vector(1)
        labels:
          severity: page
""")

    assert _run(root, rules) == cli.REFUSED
    assert "missing keeper label" in capsys.readouterr().out


def test_the_failure_text_STEERS_to_the_sentinel(tmp_path, capsys):
    """The whole incident: a diligent reader followed 'missing keeper label' to
    the obvious repair, which for a sink-only canary re-arms aegis-fyxsx. The
    failure must name the right fix, or the next reader makes the same move."""
    root = _root(tmp_path, ellie={"role": "worker", "workspace": str(tmp_path),
                                  "dangerous": True})
    rules = tmp_path / "alerts.yml"
    rules.write_text("""groups:
  - name: test
    rules:
      - alert: Unowned
        expr: vector(1)
        labels:
          severity: page
""")

    _run(root, rules)
    out = capsys.readouterr().out
    assert "# keeper: none — <why>" in out, "does not name the sentinel's form"
    assert "declared absence is not a missing label" in out
    assert "reason is required" in out.lower()
    # and it must steer AWAY from the label form, which is the damaging one
    assert "must be a comment" in out.lower()
    assert "chain[0]" in out


def test_a_trailing_comment_is_not_part_of_the_keeper_name(tmp_path, capsys):
    """Previously the comment was parsed INTO the name, so an ordinary annotated
    keeper failed as 'not on the roster'. Fixed as a side effect of reading the
    sentinel's reason, and pinned so it stays fixed."""
    root = _root(tmp_path, ellie={"role": "worker", "workspace": str(tmp_path),
                                  "dangerous": True})
    rules = tmp_path / "alerts.yml"
    rules.write_text("""groups:
  - name: test
    rules:
      - alert: Annotated
        expr: vector(1)
        labels:
          keeper: ellie  # owns the alerting domain
""")

    assert _run(root, rules) == cli.OK
    assert "OK: 1 alert rule" in capsys.readouterr().out
