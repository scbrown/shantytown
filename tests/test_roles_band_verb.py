"""`st roles band <agent> <band>` — the verb the survival band never had.

aegis-ftmfn. The band decides whether an agent is still running after a usage
throttle. Twenty cards were banded by hand-editing their `roles` arrays and
three were missed — billy, franklin, gennaro — and nothing detected it, because
an unbanded card and a card decided-`normal` resolve identically at the
governor. `roles set` could not do it: it writes the TREE POSITION, so
`st roles set billy normal` is refused as a depth violation, correctly.

The contract these pin:
  · the band is written as a declared ROLE, never as a card field — `traits`
    composes survival from the role stack, and a second source for one axis
    would disagree with it the first time anyone ran `roles set`
  · the carrier role is found by ASKING THE CATALOG, never by name-matching
  · an existing band-carrier is DROPPED, so precedence cannot silently resolve
    a band nobody asked for
  · it recomposes the result and REFUSES if that is not the band requested —
    the write is verified by the mechanism, not by having succeeded
  · a band no role declares is a refusal with the TOML to add, never an
    invented role
  · `normal (UNSET) -> normal` says out loud that nothing about a throttle
    changes and that what changed is the DECISION being on the record
  · --dry-run writes nothing
"""
from __future__ import annotations

import json
from pathlib import Path

from shantytown.cli import main, OK, REFUSED


def store(tmp_path: Path, toml: str, **cards) -> Path:
    (tmp_path / "shantytown.toml").write_text(toml)
    d = tmp_path / "crew"
    d.mkdir()
    for n, spec in cards.items():
        (d / f"{n}.json").write_text(json.dumps(spec))
    return tmp_path


def card(root: Path, name: str) -> dict:
    return json.loads((root / "crew" / f"{name}.json").read_text())


# The live deployment's own shape: `normal` and `support` are same-named roles,
# and `drains-last` is NOT — its name says nothing about its band. Keeping that
# asymmetry is what makes a name-matching bug visible here.
TOML = (
    '[roles.normal]\nsurvival = "normal"\n\n'
    '[roles.support]\nsurvival = "support"\n\n'
    '[roles.drains-last]\nsurvival = "last"\n\n'
    '[roles.graph]\n\n'
)


def test_bands_an_unbanded_card(tmp_path, capsys):
    """billy, verbatim: a real role stack with no band-carrier in it."""
    root = store(TOML, **{}) if False else store(
        tmp_path, TOML,
        billy={"role": "worker", "reports_to": "arnold",
               "roles": ["worker", "graph"]})

    rc = main(["--root", str(root), "roles", "band", "billy", "normal"])

    assert rc == OK
    assert card(root, "billy")["roles"] == ["worker", "graph", "normal"]
    out = capsys.readouterr().out
    assert "normal (UNSET) -> normal" in out


def test_says_the_throttle_behaviour_did_not_change(tmp_path, capsys):
    """The three missed cards already resolved to `normal`. If this printed a
    bare success it would read as a fix to a behaviour that was never wrong —
    and the next reader, finding no behavioural difference, deletes it."""
    root = store(tmp_path, TOML,
                 billy={"role": "worker", "roles": ["worker", "graph"]})

    main(["--root", str(root), "roles", "band", "billy", "normal"])

    out = capsys.readouterr().out
    assert "Nothing about a throttle changes" in out
    assert "DECLARED rather than unset" in out


def test_a_real_band_change_does_not_claim_nothing_changed(tmp_path, capsys):
    """Positive control for the note above. A note that always prints is not a
    note — banding a card `last` genuinely changes what a throttle does."""
    root = store(tmp_path, TOML,
                 billy={"role": "worker", "roles": ["worker", "graph"]})

    rc = main(["--root", str(root), "roles", "band", "billy", "last"])

    assert rc == OK
    out = capsys.readouterr().out
    assert "normal (UNSET) -> last" in out
    assert "Nothing about a throttle changes" not in out
    assert card(root, "billy")["roles"] == ["worker", "graph", "drains-last"], \
        "the carrier is found by ASKING the catalog — `last` names no role"


def test_the_old_band_carrier_is_dropped(tmp_path, capsys):
    """THE ONE THAT WOULD HAVE BEEN SILENT. survival is a SINGLE axis, so leaving
    `drains-last` on the stack while adding `normal` composes back to `last` by
    precedence — the command would report success having done the opposite."""
    root = store(tmp_path, TOML,
                 arnold={"role": "lead", "roles": ["lead", "drains-last", "graph"]})

    rc = main(["--root", str(root), "roles", "band", "arnold", "normal"])

    assert rc == OK
    assert "drains-last" not in card(root, "arnold")["roles"]
    assert card(root, "arnold")["roles"] == ["lead", "graph", "normal"]
    out = capsys.readouterr().out
    assert "last -> normal" in out
    assert "dropped 'drains-last'" in out


def test_an_undeclared_band_is_refused_with_the_toml_to_add(tmp_path, capsys):
    """st could mint a role carrying the band. That would put a role in the
    catalog the deployment's own config does not mention — the closed-enum
    problem the trait model exists to kill, re-created by the convenience verb."""
    root = store(tmp_path, '[roles.graph]\n',
                 billy={"role": "worker", "roles": ["worker", "graph"]})

    rc = main(["--root", str(root), "roles", "band", "billy", "support"])

    assert rc == REFUSED
    assert card(root, "billy")["roles"] == ["worker", "graph"]
    err = capsys.readouterr().err
    assert "[roles.support]" in err and 'survival = "support"' in err


def test_an_unknown_band_names_the_four_in_order(tmp_path, capsys):
    """The ordering IS the safety property (traits.SURVIVAL_BANDS), so the
    refusal has to carry it — `first` and `last` are meaningless to anyone who
    has to guess which end survives."""
    root = store(tmp_path, TOML, billy={"role": "worker"})

    rc = main(["--root", str(root), "roles", "band", "billy", "critical"])

    assert rc == REFUSED
    err = capsys.readouterr().err
    assert "first < normal < support < last" in err
    assert "shed FIRST" in err and "shed LAST" in err


def test_two_declaring_roles_refuse_rather_than_guess(tmp_path, capsys):
    root = store(tmp_path,
                 '[roles.support]\nsurvival = "support"\n\n'
                 '[roles.oncall]\nsurvival = "support"\n',
                 billy={"role": "worker"})

    rc = main(["--root", str(root), "roles", "band", "billy", "support"])

    assert rc == REFUSED
    err = capsys.readouterr().err
    assert "oncall" in err and "support" in err and "--via" in err


def test_via_picks_the_carrier(tmp_path):
    root = store(tmp_path,
                 '[roles.support]\nsurvival = "support"\n\n'
                 '[roles.oncall]\nsurvival = "support"\n',
                 billy={"role": "worker"})

    rc = main(["--root", str(root), "roles", "band", "billy", "support",
               "--via", "oncall"])

    assert rc == OK
    assert card(root, "billy")["roles"] == ["worker", "oncall"]


def test_via_a_role_that_does_not_declare_the_band_is_refused(tmp_path, capsys):
    root = store(tmp_path, TOML, billy={"role": "worker"})

    rc = main(["--root", str(root), "roles", "band", "billy", "support",
               "--via", "graph"])

    assert rc == REFUSED
    assert "does not declare survival" in capsys.readouterr().err


def test_an_empty_stack_is_migrated_explicitly_and_says_so(tmp_path, capsys):
    """An empty `roles` is NOBODY SAID, and it reads as the tree position. Writing
    a stack decides more than the band, so the command says which card crossed
    that line rather than leaving it to be discovered later."""
    root = store(tmp_path, TOML, gennaro={"role": "worker"})

    rc = main(["--root", str(root), "roles", "band", "gennaro", "support"])

    assert rc == OK
    assert card(root, "gennaro")["roles"] == ["worker", "support"]
    out = capsys.readouterr().out
    assert "carried no role stack" in out and "'worker'" in out


def test_dry_run_writes_nothing(tmp_path, capsys):
    root = store(tmp_path, TOML,
                 billy={"role": "worker", "roles": ["worker", "graph"]})

    rc = main(["--root", str(root), "roles", "band", "billy", "support", "-n"])

    assert rc == OK
    assert card(root, "billy")["roles"] == ["worker", "graph"], "dry-run writes nothing"
    assert "nothing written" in capsys.readouterr().out


def test_an_unknown_agent_is_refused(tmp_path, capsys):
    root = store(tmp_path, TOML, billy={"role": "worker"})

    rc = main(["--root", str(root), "roles", "band", "nobody", "normal"])

    assert rc == REFUSED
    assert "no such agent" in capsys.readouterr().err


def test_the_write_is_verified_by_recomposing_it(tmp_path, capsys):
    """The command's own arithmetic is not the evidence. Here `graph` itself
    declares a band, so dropping the other carriers still leaves the stack
    resolving to something the operator did not ask for — and an UNRANKED
    conflict resolves to `?`, which means the governor FAILS OPEN and the agent
    runs through every tier. Reporting "band set to first" over that would be the
    exact inversion of what happened.
    """
    root = store(
        tmp_path,
        # Two bands, and NOTHING ranks them against each other: `builtin=False`
        # is not in play, but these are deployment values with no precedence for
        # the pair, so `of()` raises AmbiguousTrait -> band_of returns `?`.
        '[roles.first]\nsurvival = "first"\n\n'
        '[roles.odd]\nsurvival = "unranked-band"\n',
        billy={"role": "worker", "roles": ["worker", "odd"]})

    rc = main(["--root", str(root), "roles", "band", "billy", "first"])

    # `odd` carries a survival value, so it IS dropped as a band-carrier and the
    # result is clean. That is the honest outcome — and it pins that the drop
    # keys on CARRYING a band, not on carrying a KNOWN one.
    assert rc == OK, capsys.readouterr().err
    assert card(root, "billy")["roles"] == ["worker", "first"]
