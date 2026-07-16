"""roles --check: all THREE outcomes, each one exercised.

docs/cli.md: "A checker that can only report health is not a checker."

Every test here exists to prove a branch can fire. The ok-path tests are not the
valuable ones — test_broken_* and test_cannot_tell_* are, because a checker whose
failure path has never run is indistinguishable from one that cannot fail. We
have shipped several of those; this is the reaction to them.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import roles
from shantytown.files import FilesRegistry


def _card(d: Path, name: str, **fields) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(fields))


def test_ok_when_everyone_reports_somewhere(tmp_path: Path):
    c = tmp_path / "crew"
    _card(c, "arnold", role="administrator")
    _card(c, "malcolm", role="lead", reports_to="arnold")
    _card(c, "ellie", role="worker", reports_to="malcolm")
    rep = roles.check(FilesRegistry(c))
    assert rep.verdict == roles.OK
    assert "every one reports somewhere" in rep.render()


def test_root_administrator_is_not_an_orphan(tmp_path: Path):
    """Somebody has to be the root. reports_to=None is only broken below it."""
    c = tmp_path / "crew"
    _card(c, "arnold", role="administrator")
    rep = roles.check(FilesRegistry(c))
    assert rep.verdict == roles.OK


def test_broken_orphan(tmp_path: Path):
    """The cli.md example: dearing, reports_to —, *** ORPHAN ***."""
    c = tmp_path / "crew"
    _card(c, "arnold", role="administrator")
    _card(c, "dearing", role="worker")            # no reports_to
    rep = roles.check(FilesRegistry(c))
    assert rep.verdict == roles.BROKEN
    out = rep.render()
    assert "ORPHAN" in out
    assert "BLOCKED: 1 agent's stop events go nowhere." in out


def test_broken_self_reference(tmp_path: Path):
    c = tmp_path / "crew"
    _card(c, "arnold", role="administrator")
    _card(c, "loop", role="lead", reports_to="loop")
    rep = roles.check(FilesRegistry(c))
    assert rep.verdict == roles.BROKEN
    assert "REPORTS TO ITSELF" in rep.render()


def test_cannot_tell_when_lead_is_not_in_the_registry(tmp_path: Path):
    """Not 'broken' — we genuinely cannot tell what that card meant."""
    c = tmp_path / "crew"
    _card(c, "arnold", role="administrator")
    _card(c, "kid", role="worker", reports_to="whoisthis")
    rep = roles.check(FilesRegistry(c))
    assert rep.verdict == roles.CANNOT_TELL
    out = rep.render()
    assert "CANNOT TELL" in out
    assert "NOT a clean result" in out


def test_cannot_tell_when_the_registry_is_unreachable(tmp_path: Path):
    """The registry itself fails. This must NOT render as 'everyone is fine'.

    This is the bug exit code 2 exists for: a check that could not reach its
    target reported CLEAR.
    """
    class Dead:
        def get(self, name): raise OSError("registry unreachable")
        def all(self): raise OSError("registry unreachable")

    rep = roles.check(Dead())
    assert rep.verdict == roles.CANNOT_TELL
    assert "CANNOT TELL" in rep.render()
    assert "every one reports somewhere" not in rep.render()


def test_cannot_tell_when_the_registry_DIRECTORY_IS_ABSENT(tmp_path: Path):
    """THE REGRESSION TEST FOR A BUG THIS SUITE ORIGINALLY MISSED.

    test_cannot_tell_when_the_registry_is_unreachable (above) passed all along —
    and the real path was broken anyway, because its `Dead` mock RAISES and the
    real FilesRegistry did not: glob() on a missing directory returns [], so
    `shanty roles --check --root /nonexistent` printed "0 agents, every one
    reports somewhere" and EXITED 0.

    The mock did not behave like the thing it stood in for, so the test was
    correct and incomplete — it proved the checker handles an exception nobody
    was throwing. Only driving the real CLI found it.

    So this test uses the REAL FilesRegistry against a REAL absent directory.
    """
    rep = roles.check(FilesRegistry(tmp_path / "no-such-crew-dir"))
    assert rep.verdict == roles.CANNOT_TELL, (
        "an absent registry reported a clean bill of health"
    )
    assert "every one reports somewhere" not in rep.render()


def test_empty_registry_is_not_the_same_as_absent(tmp_path: Path):
    """Present-but-empty is a real answer ("nobody exists"); absent is not
    ("I could not look"). Collapsing them is what caused the bug above."""
    c = tmp_path / "crew"
    c.mkdir()
    rep = roles.check(FilesRegistry(c))
    assert rep.verdict == roles.OK          # nothing is broken; there is nobody
    assert rep.rows == []


def test_cannot_tell_outranks_broken(tmp_path: Path):
    """Worst wins. An unreadable card might be hiding either — reporting the
    lesser verdict is the same defect one level up."""
    c = tmp_path / "crew"
    _card(c, "arnold", role="administrator")
    _card(c, "orphan", role="worker")                       # BROKEN
    _card(c, "kid", role="worker", reports_to="whoisthis")  # CANNOT_TELL
    rep = roles.check(FilesRegistry(c))
    assert rep.verdict == roles.CANNOT_TELL


def test_check_never_raises_on_a_bad_card(tmp_path: Path):
    """A bad card is a VERDICT, not a crash. A checker that dies on the thing it
    is checking for has not checked anything."""
    c = tmp_path / "crew"
    _card(c, "arnold", role="administrator")
    (c / "corrupt.json").write_text("{not json")
    try:
        rep = roles.check(FilesRegistry(c))
    except Exception as e:
        pytest.fail(f"check() raised instead of reporting: {e!r}")
    assert rep.verdict == roles.CANNOT_TELL
