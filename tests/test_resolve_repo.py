"""A bare repo name means $GT_ROOT/<name> in EVERY cwd (aegis-k3i8t).

The resolver used to try `Path(repo).exists()` — evaluated against the CWD —
before the $GT_ROOT branch, so a bare name silently became `./<name>` whenever
the cwd held a directory of that name. That is the normal case, not a corner
one: a Python repo contains a package directory named after the repo, so
`./shantytown` exists inside every shantytown checkout and worktree. The
documented `st push shantytown <agent>` therefore failed specifically in the
tree you push from, and `st go --worktree` — which does not refuse, it
provisions — created a NESTED worktree and handed the agent a wrong path.

These tests run with the cwd set to a directory that contains the decoy, because
that is the only condition under which the bug is observable at all.
"""
from __future__ import annotations
import os
from pathlib import Path

import pytest

from shantytown.cli import _resolve_repo


@pytest.fixture
def gt_root(tmp_path, monkeypatch):
    root = tmp_path / "gt"
    (root / "shantytown").mkdir(parents=True)
    monkeypatch.setenv("GT_ROOT", str(root))
    return root


def test_a_bare_name_resolves_under_gt_root(gt_root, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert _resolve_repo("shantytown") == gt_root / "shantytown"


def test_the_regression_a_same_named_dir_in_the_cwd_does_not_capture_it(
        gt_root, monkeypatch, tmp_path):
    """THE BUG ITSELF: standing in a worktree whose package dir shares the repo
    name. The decoy exists and is a directory — everything the old `p.exists()`
    clause asked — and it must still lose to $GT_ROOT."""
    wt = tmp_path / "shantytown-wt" / "franklin"
    (wt / "shantytown").mkdir(parents=True)     # the package dir: the decoy
    monkeypatch.chdir(wt)
    got = _resolve_repo("shantytown")
    assert got.is_absolute(), f"resolved CWD-relative: {got}"
    assert got == gt_root / "shantytown", (
        f"the cwd's ./shantytown captured the bare name: {got}")


def test_an_absolute_path_is_returned_verbatim(gt_root, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "elsewhere" / "quipu"
    assert _resolve_repo(str(other)) == other


@pytest.mark.parametrize("spec", ["./quipu", "../quipu", "sub/quipu"])
def test_a_relative_path_still_means_that_path(gt_root, monkeypatch, tmp_path, spec):
    """The escape hatch survives, and the separator is what marks it. Without
    this the fix could have been 'bare names always win' applied too widely."""
    monkeypatch.chdir(tmp_path)
    assert _resolve_repo(spec) == Path(spec)


def test_gt_root_defaults_to_home_gt_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("GT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _resolve_repo("shantytown") == Path.home() / "gt" / "shantytown"


def test_a_bare_name_that_exists_NOWHERE_still_resolves_under_gt_root(
        gt_root, monkeypatch, tmp_path):
    """Resolution must not depend on the target existing — st worktree's whole
    job is to create it. A resolver that only answers for extant paths would
    reintroduce the same class of cwd-sensitivity one call up."""
    monkeypatch.chdir(tmp_path)
    assert _resolve_repo("not-cloned-yet") == gt_root / "not-cloned-yet"
