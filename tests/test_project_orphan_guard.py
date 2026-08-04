"""st roles sync — the guard that stops a sync from MANUFACTURING an orphan.

aegis-ftmfn. Measured on the live store 2026-08-04, `roles sync --dry-run`
printed this among seven rows:

    LIVE ~ grant      worker -> worker, reports_to sattler -> —

That row is the smallest in the diff and it is the only catastrophic one: it
leaves an agent with no lead, which `roles --check` calls BROKEN precisely
because its stop events have nowhere to go. Nothing said so. The existing
guards could not: the live-restructure guard keys on liveness (and `--force`
clears it), and the dangling-supervisor check looks at a different fault, in
cards the graph does not mention.

The contract these pin:
  · a sync that would newly break a card SAYS SO, in the diff, on --dry-run —
    the run an operator makes in order to decide
  · it REFUSES (1) on a real run and writes nothing
  · --force does NOT clear it. That is the whole point: `--force` consents to
    restructuring RUNNING agents, which is a statement about timing, not about
    leaving a card unattached. The near-miss was an operator who would have
    answered only the first question.
  · --allow-breakage does clear it — a speed bump, not a wall
  · NEWLY, not merely, broken: a store that already holds an orphan can still
    be synced, or the guard would wedge the fix for the fault it guards
  · both refusals are reported together; neither hides behind the other
  · and the positive controls: an ordinary re-parent, and a role the deployment
    declares UNATTACHED, must not trip it. A check that always fires is not a
    check.
"""
from __future__ import annotations

import json
from pathlib import Path

from shantytown import cli
from shantytown.cli import main, OK, REFUSED
from shantytown.protocols import Agent

from test_project_guard import crew, graph, panes


def card(root: Path, name: str) -> dict:
    return json.loads((root / "crew" / f"{name}.json").read_text())


# The shape of the incident: an administrator, a lead, and a worker under the
# lead. The graph has forgotten the lead's report, so syncing it cuts the worker
# loose.
def _incident(tmp_path, monkeypatch, *, live=()):
    root = crew(
        tmp_path,
        sattler={"role": "administrator", "pane": "shanty-sattler"},
        dearing={"role": "lead", "reports_to": "sattler", "pane": "shanty-dearing"},
        grant={"role": "worker", "reports_to": "dearing", "pane": "shanty-grant"},
    )
    graph(monkeypatch,
          Agent(name="sattler", role="administrator"),
          Agent(name="dearing", role="lead", reports_to="sattler"),
          Agent(name="grant", role="worker"))          # <- no lead in the graph
    panes(monkeypatch, *live)
    return root


def test_refuses_to_manufacture_an_orphan(tmp_path, monkeypatch, capsys):
    root = _incident(tmp_path, monkeypatch)            # nothing live: --force is not in play

    rc = main(["--root", str(root), "roles", "sync"])

    assert rc == REFUSED
    assert card(root, "grant")["reports_to"] == "dearing", "refusal must write NOTHING"
    err = capsys.readouterr().err
    assert "NEWLY BROKEN" in err and "grant" in err


def test_force_does_not_clear_it(tmp_path, monkeypatch, capsys):
    """THE ONE THAT MATTERS. --force is the flag an operator reaches for when the
    live-restructure guard fires, and in the measured incident that guard was the
    only thing in the way. If --force cleared this too, clearing the first
    refusal would silently answer a second question nobody asked."""
    root = _incident(tmp_path, monkeypatch, live=("shanty-grant",))

    rc = main(["--root", str(root), "roles", "sync", "--force"])

    assert rc == REFUSED
    assert card(root, "grant")["reports_to"] == "dearing"
    assert "NEWLY BROKEN" in capsys.readouterr().err


def test_allow_breakage_clears_it(tmp_path, monkeypatch):
    root = _incident(tmp_path, monkeypatch)

    rc = main(["--root", str(root), "roles", "sync", "--allow-breakage"])

    assert rc == OK
    assert card(root, "grant").get("reports_to") is None


def test_both_refusals_are_reported_together(tmp_path, monkeypatch, capsys):
    """A live restructure AND a manufactured orphan. Reporting only the first
    teaches the operator that --force is the answer, and the retry lands the
    orphan — which is the exact sequence this guard exists to interrupt."""
    root = _incident(tmp_path, monkeypatch, live=("shanty-grant",))

    rc = main(["--root", str(root), "roles", "sync"])

    assert rc == REFUSED
    err = capsys.readouterr().err
    assert "LIVE agent(s) would be restructured" in err
    assert "NEWLY BROKEN" in err
    assert "--force does NOT cover this" in err


def test_dry_run_names_the_orphan_it_would_manufacture(tmp_path, monkeypatch, capsys):
    """The dry-run is the run you make to DECIDE. Before this, it printed
    `grant  worker -> worker, reports_to dearing -> —` and left the reader to
    notice that an em-dash in that column is an unattached agent."""
    root = _incident(tmp_path, monkeypatch)

    rc = main(["--root", str(root), "roles", "sync", "-n"])

    assert rc == OK, "a dry-run reports; it does not refuse"
    out = capsys.readouterr().out
    assert "NEWLY BROKEN" in out and "grant" in out and "ORPHAN" in out
    assert card(root, "grant")["reports_to"] == "dearing", "dry-run writes nothing"


def test_a_new_card_with_no_lead_is_manufacturing_one_too(tmp_path, monkeypatch, capsys):
    """Minting an unattached card is the same fault as demoting one into it. The
    live graph really does declare a HOST and a mayor-that-does-not-exist as
    crew, so this is the shape a dirty graph arrives in."""
    root = crew(tmp_path, sattler={"role": "administrator", "pane": "shanty-sattler"})
    graph(monkeypatch,
          Agent(name="sattler", role="administrator"),
          Agent(name="a-backup-host", role="worker"))          # a HOST, per the live graph
    panes(monkeypatch)

    rc = main(["--root", str(root), "roles", "sync"])

    assert rc == REFUSED
    assert not (root / "crew" / "a-backup-host.json").exists()
    assert "a-backup-host" in capsys.readouterr().err


def test_an_ordinary_reparent_does_not_trip_it(tmp_path, monkeypatch, capsys):
    """POSITIVE CONTROL. grant moves from dearing to sattler — a real
    restructure, every row changes, and nobody is left without a lead. If this
    refused, the guard would be a wall against all syncing rather than against
    one outcome."""
    root = crew(
        tmp_path,
        sattler={"role": "administrator", "pane": "shanty-sattler"},
        dearing={"role": "lead", "reports_to": "sattler", "pane": "shanty-dearing"},
        grant={"role": "worker", "reports_to": "dearing", "pane": "shanty-grant"},
    )
    graph(monkeypatch,
          Agent(name="sattler", role="administrator"),
          Agent(name="dearing", role="worker", reports_to="sattler"),
          Agent(name="grant", role="worker", reports_to="sattler"))
    panes(monkeypatch)

    rc = main(["--root", str(root), "roles", "sync"])

    assert rc == OK
    assert card(root, "grant")["reports_to"] == "sattler"
    assert "NEWLY BROKEN" not in capsys.readouterr().out


def test_an_already_broken_card_does_not_wedge_the_sync(tmp_path, monkeypatch):
    """NEWLY broken, not broken. grant has no lead BEFORE the sync — a store in
    exactly the state this guard warns about. Syncing must still work, or the
    guard would block the only supported way to repair the fault it names."""
    root = crew(
        tmp_path,
        sattler={"role": "administrator", "pane": "shanty-sattler"},
        grant={"role": "worker", "pane": "shanty-grant"},          # already an ORPHAN
    )
    graph(monkeypatch,
          Agent(name="sattler", role="administrator"),
          Agent(name="grant", role="worker"))                      # still orphaned
    panes(monkeypatch)

    # Nothing about grant changes, so the sync is a no-op for it; what must NOT
    # happen is a refusal citing a fault the sync did not cause.
    rc = main(["--root", str(root), "roles", "sync"])
    assert rc == OK


def test_a_sync_that_FIXES_an_orphan_is_never_refused(tmp_path, monkeypatch):
    """The other half of the same argument, and the one a `result is clean`
    guard would get wrong: the store holds an orphan and the graph repairs it."""
    root = crew(
        tmp_path,
        sattler={"role": "administrator", "pane": "shanty-sattler"},
        grant={"role": "worker", "pane": "shanty-grant"},          # ORPHAN
    )
    graph(monkeypatch,
          Agent(name="sattler", role="administrator"),
          Agent(name="grant", role="worker", reports_to="sattler"))
    panes(monkeypatch)

    rc = main(["--root", str(root), "roles", "sync"])

    assert rc == OK
    assert card(root, "grant")["reports_to"] == "sattler"


def test_an_unattached_role_is_not_an_orphan(tmp_path, monkeypatch, capsys):
    """POSITIVE CONTROL for the definition, not the plumbing. A role the
    deployment declares `attachment = unattached` has no lead BY DEFINITION.
    Refusing it would make a declared role permanently unsyncable — the
    exists-not-acts shape `roles.check` already refuses to commit, reached here
    through the same catalog rather than a second rule.

    It also pins the field-preservation detail in `_would_break`: the stacked
    role set lives on the CARD, and the graph Agent carries none. Take the graph
    agent wholesale and `advisor` resolves as a bare worker — an orphan — while
    the sync itself would have preserved the stack.
    """
    root = crew(
        tmp_path,
        sattler={"role": "administrator", "pane": "shanty-sattler"},
        malcolm={"role": "worker", "roles": ["advisor"], "reports_to": "sattler",
                 "pane": "shanty-malcolm"},
    )
    (tmp_path / "shantytown.toml").write_text(
        "[roles.advisor]\nattachment = \"unattached\"\n")
    graph(monkeypatch,
          Agent(name="sattler", role="administrator"),
          Agent(name="malcolm", role="advisor"))       # no lead — and that is correct
    panes(monkeypatch)

    rc = main(["--root", str(root), "roles", "sync"])

    assert rc == OK, capsys.readouterr().err
    assert card(root, "malcolm").get("reports_to") is None
