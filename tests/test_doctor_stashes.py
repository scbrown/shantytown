"""`st doctor` surfaces stashes in SHARED repos (aegis-pxzi4).

`refs/stash` is shared across every linked worktree — the isolation that makes a
worktree safe covers the index, HEAD and the branch, and stops there. So one
agent's `git stash list` shows another's entries as if they were its own, and
`pop`/`drop` would take or destroy them. Measured 2026-08-04: I listed a sibling's
stash from my own worktree, twelve minutes after they made it.

There is NO stash hook, so this cannot be guarded the way commit/rebase/merge are.
Discovery is the only lever, and nobody runs `git stash list` in a repo they did
not stash in — which is exactly the case that matters.

THE LOAD-BEARING TEST IS `test_the_finding_says_INSPECT_not_clean_up`. Age here
reads as IMPORTANCE, not staleness, which is the opposite of every other age in
doctor: the live fleet's two entries are a preserved orphan from a pre-ff-pull
rescue and a deliberate pre-push set-aside. A check that told an operator to tidy
these away would destroy the only copy of the work it was built to protect.
"""
from __future__ import annotations

import time

from shantytown import doctor


def _run(rows_by_repo):
    def run(argv):
        repo = argv[2]
        rows = rows_by_repo.get(repo)
        if rows is None:
            return 1, ""            # no stash ref: the common case
        return 0, "\n".join(rows)
    return run


def _row(ref, age_days, label):
    return f"{ref}\t{int(time.time() - age_days * 86400)}\t{label}"


def test_a_stash_in_a_shared_repo_is_reported_with_age_and_label():
    found = doctor.stray_stashes(
        ["/gt/quipu"], run=_run({"/gt/quipu": [
            _row("stash@{0}", 3, "On main: sattler: orphaned pre-ff work")]}))
    assert len(found) == 1
    repo, ref, age, label = found[0]
    assert (repo, ref) == ("quipu", "stash@{0}")
    assert 2.9 < age < 3.1
    assert "sattler" in label


def test_repos_with_NO_stash_are_silent_not_errors():
    """A missing `refs/stash` exits non-zero. That is the overwhelmingly common
    case and must not read as a failure to check."""
    assert doctor.stray_stashes(["/gt/a", "/gt/b"], run=_run({})) == []


def test_every_entry_is_reported_not_just_the_top_one():
    """`git stash list` is a stack. Reporting only stash@{0} would hide the older
    entries — which, per the docstring above, are the ones most likely to be the
    only copy of something."""
    found = doctor.stray_stashes(["/gt/x"], run=_run({"/gt/x": [
        _row("stash@{0}", 1, "recent"), _row("stash@{1}", 40, "ancient")]}))
    assert [f[1] for f in found] == ["stash@{0}", "stash@{1}"]


def test_a_malformed_reflog_line_is_skipped_not_fatal():
    found = doctor.stray_stashes(["/gt/x"], run=_run({"/gt/x": [
        "garbage", _row("stash@{0}", 1, "real")]}))
    assert [f[1] for f in found] == ["stash@{0}"]


# --- the rendering ------------------------------------------------------------


def test_the_finding_says_INSPECT_not_clean_up():
    """THE test. Age is IMPORTANCE here, not staleness. A check that invited
    tidying would destroy the only copy of the work it exists to protect."""
    line = doctor.render_stashes([("quipu", "stash@{0}", 3.0, "sattler: orphan")], 4)
    assert "INSPECT" in line
    assert "only copy" in line
    assert not any(w in line.lower() for w in ("clean up", "remove", "delete", "prune"))


def test_the_finding_names_the_SHARING_which_is_the_whole_hazard():
    line = doctor.render_stashes([("quipu", "stash@{0}", 3.0, "x")], 4)
    assert "EVERY" in line and "worktree" in line


def test_clean_is_STATED_not_self_hidden():
    """'Checked and clean' and 'never checked' must not render identically —
    the rule this module is built on."""
    line = doctor.render_stashes([], 4)
    assert "none" in line and "4 shared repo" in line


def test_a_long_label_is_truncated_but_the_repo_and_age_survive():
    line = doctor.render_stashes(
        [("goldblum", "stash@{0}", 16.0, "x" * 400)], 4)
    assert "goldblum 16d" in line
    assert len(line) < 400
