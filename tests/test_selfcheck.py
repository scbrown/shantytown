"""st doctor asking the question about ITSELF (aegis-daoh, dearing's ruling).

doctor reported installed-vs-available for beads/bobbin/quipu/reactor and never
once about `st`. The tool that audits deployment drift was the only tool exempt
from the audit — and a stale `st doctor` reports a stale world confidently.

The tests that matter are NOT the ok-path one. They are:

  * test_a_private_worktree_source_is_BROKEN — the exact condition that occurred
    on 2026-07-20: a deploy re-pointed the FLEET's recorded source at one crew
    member's private worktree, and nothing in the system could say so.
  * the CANNOT_TELL tests — requirement 2: a failure to LOOK is never a pass.
  * test_positive_control_* — defeat the check and the failures must vanish.
"""
from __future__ import annotations

import json

from shantytown import selfcheck as sc

CANON = "/home/braino/gt/shantytown"
PRIVATE = "/home/braino/gt/shantytown-wt/dearing"


def _meta(source: str) -> str:
    return json.dumps({"venvs": {"shantytown": {"metadata": {
        "main_package": {"package_or_url": source}}}}})


def _fresh(_canonical):
    """Default fake: installed bytes match the checkout."""
    return set()


def _stale(*names):
    return lambda _canonical: set(names)


def _unreadable(_canonical):
    return None


def _run_for(source: str | None, heads: dict | None = None, *,
             pipx_rc: int = 0, pipx_out: str | None = None,
             dirty_out: str | None = None):
    """A fake `run` covering both commands check_self issues."""
    heads = heads or {}

    def run(argv):
        if argv[0] == "pipx":
            if pipx_out is not None:
                return pipx_rc, pipx_out
            return pipx_rc, _meta(source) if source else "{}"
        if argv[0] == "git":
            path = argv[2]
            if "status" in argv:
                return (0, dirty_out) if dirty_out is not None else (0, "")
            if path in heads:
                return 0, heads[path] + "\n"
            return 1, "fatal: not a git repository"
        raise AssertionError(f"unexpected argv {argv}")
    return run


# --- the defect this exists for -----------------------------------------

def test_a_private_worktree_source_is_BROKEN():
    """THE REAL INCIDENT. `pipx install --force <my own worktree>` re-pointed the
    fleet's recorded source at a private directory. Every agent's `st` became a
    build of one crew member's uncommitted tree, and a later `pipx reinstall`
    faithfully rebuilt THAT — not the shared checkout somebody had just pulled.

    dearing's requirement 1: this is an ERROR, not a note."""
    h = sc.check_self(run=_run_for(PRIVATE), canonical=CANON, stale_files=_fresh)
    assert h.verdict == sc.BROKEN
    assert PRIVATE in h.note
    assert CANON in h.note                      # the note must carry the FIX
    assert "pipx install --force" in h.note


def test_the_broken_note_says_why_it_matters_not_just_that_it_differs():
    """A path mismatch reads as pedantry unless the consequence is stated: the
    whole fleet is running whatever is in that directory, uncommitted included."""
    h = sc.check_self(run=_run_for(PRIVATE), canonical=CANON, stale_files=_fresh)
    assert "fleet" in h.note
    assert "uncommitted" in h.note


def test_a_STALE_INSTALL_is_BROKEN():
    """THE EVERYDAY FAILURE, and the one an earlier version of this module could
    not detect. `git pull` does NOT deploy `st` — it is installed non-editable —
    so the checkout moves forward and the running code silently does not.

    The first implementation compared _head(recorded) with _head(canonical). Once
    the path check passes those are THE SAME DIRECTORY, so they were always equal
    and that branch was UNREACHABLE in production; its test passed only because
    the fake returned two different values for one path. Caught by running the
    real thing and noticing it could not fail.

    Measured when the replacement first ran for real: the deployed `st` differed
    from the checkout in 5 files while every path-and-HEAD check said green.
    """
    h = sc.check_self(run=_run_for(CANON, {CANON: "c" * 40}), canonical=CANON,
                      stale_files=_stale("cli.py", "runtime.py"))
    assert h.verdict == sc.BROKEN
    assert "cli.py" in h.note and "runtime.py" in h.note
    assert "pipx install --force" in h.note          # carries the fix
    assert "does NOT deploy" in h.note               # and the reason


def test_the_stale_note_does_not_dump_every_filename():
    """A note that lists 40 files is a wall nobody reads. Three, then a count."""
    many = _stale(*[f"m{i}.py" for i in range(9)])
    h = sc.check_self(run=_run_for(CANON, {CANON: "c" * 40}), canonical=CANON,
                      stale_files=many)
    assert "+6 more" in h.note
    assert "9 file(s)" in h.note


def test_an_unreadable_comparison_is_CANNOT_TELL_not_ok():
    """Requirement 2 at the new seam: if we cannot compare the installed files,
    that is not health."""
    h = sc.check_self(run=_run_for(CANON, {CANON: "c" * 40}), canonical=CANON,
                      stale_files=_unreadable)
    assert h.verdict == sc.CANNOT_TELL


# --- requirement 2: failing to LOOK is never a pass ----------------------

def test_unreadable_pipx_is_CANNOT_TELL_not_ok():
    h = sc.check_self(run=_run_for(None, pipx_rc=1), canonical=CANON, stale_files=_fresh)
    assert h.verdict == sc.CANNOT_TELL


def test_unparseable_pipx_json_is_CANNOT_TELL():
    h = sc.check_self(run=_run_for(None, pipx_out="not json at all"), canonical=CANON, stale_files=_fresh)
    assert h.verdict == sc.CANNOT_TELL


def test_no_recorded_source_is_CANNOT_TELL():
    h = sc.check_self(run=_run_for(None), canonical=CANON, stale_files=_fresh)
    assert h.verdict == sc.CANNOT_TELL
    assert "cannot tell" in h.note.lower() or "no recorded source" in h.note


def test_an_unreadable_HEAD_alone_does_not_condemn_a_matching_install():
    """The sha is DISPLAY. The claim is "the installed files equal the checkout",
    and that is answered by the byte comparison, not by git.

    Deliberately not cannot-tell: an install whose files provably match should not
    be reported as suspect because `git rev-parse` failed. Over-firing is how a
    check gets switched off — the note just shows `?` for the sha.
    """
    h = sc.check_self(run=_run_for(CANON, heads={}), canonical=CANON,
                      stale_files=_fresh)
    assert h.verdict == sc.OK
    assert h.installed_head is None
    assert "?" in sc.render(h)          # renders the gap rather than inventing one


# --- the ok path, and the controls that make it mean something -----------

def test_canonical_source_at_matching_head_is_OK():
    h = sc.check_self(run=_run_for(CANON, {CANON: "c" * 40}), canonical=CANON, stale_files=_fresh)
    assert h.verdict == sc.OK
    assert h.recorded_source == CANON


def test_positive_control_the_check_is_not_a_constant():
    """Three inputs, three distinct verdicts. A checker that returns one word is
    a column header — which is exactly what `hooks: ok` was before today."""
    ok = sc.check_self(run=_run_for(CANON, {CANON: "c" * 40}), canonical=CANON, stale_files=_fresh)
    broken = sc.check_self(run=_run_for(PRIVATE), canonical=CANON, stale_files=_fresh)
    unknown = sc.check_self(run=_run_for(None, pipx_rc=1), canonical=CANON, stale_files=_fresh)
    assert {ok.verdict, broken.verdict, unknown.verdict} == {
        sc.OK, sc.BROKEN, sc.CANNOT_TELL}


def test_render_marks_a_failure_visibly():
    broken = sc.check_self(run=_run_for(PRIVATE), canonical=CANON, stale_files=_fresh)
    assert sc.render(broken).lstrip().startswith("✗")
    unknown = sc.check_self(run=_run_for(None, pipx_rc=1), canonical=CANON, stale_files=_fresh)
    assert sc.render(unknown).lstrip().startswith("?")
    ok = sc.check_self(run=_run_for(CANON, {CANON: "c" * 40}), canonical=CANON, stale_files=_fresh)
    assert sc.render(ok).lstrip().startswith("•")


def test_a_trailing_slash_is_not_a_divergence():
    """Path comparison must be normalised — `/x/` and `/x` are the same install,
    and a checker that cried wolf on a slash would be turned off within a day."""
    h = sc.check_self(run=_run_for(CANON + "/", {CANON: "c" * 40, CANON + "/": "c" * 40}),
                      canonical=CANON, stale_files=_fresh)
    assert h.verdict == sc.OK


# --- the exit code, where requirement 2 actually bites -------------------

def _mk(verdict):
    return sc.SelfHealth(verdict, "n")


def test_exit_code_folding():
    """dearing's requirement 2 in the place it matters: a self-check that could
    not read its own metadata forces exit 2 even when every TOOL row is green.
    Uncertainty dominates — a report you cannot trust is worse than one that says
    'fix this'."""
    from shantytown import cli
    from shantytown import doctor as doc

    clean: list = []                      # no tools -> doc.exit_code == 0
    assert doc.exit_code(clean) == 0

    assert cli._doctor_exit(doc, clean, None) == 0            # not run
    assert cli._doctor_exit(doc, clean, _mk(sc.OK)) == 0      # green
    assert cli._doctor_exit(doc, clean, _mk(sc.BROKEN)) == 1  # actionable
    assert cli._doctor_exit(doc, clean, _mk(sc.CANNOT_TELL)) == 2


def test_positive_control_exit_code_is_not_constant():
    """Four inputs, three distinct codes. Without this the folding could be
    hardwired to 0 and every test above would still pass."""
    from shantytown import cli
    from shantytown import doctor as doc
    got = {cli._doctor_exit(doc, [], s)
           for s in (None, _mk(sc.OK), _mk(sc.BROKEN), _mk(sc.CANNOT_TELL))}
    assert got == {0, 1, 2}


# --- the canonical checkout is SHARED, so "canonical" is not "safe" ------

def test_a_DIRTY_canonical_checkout_BLOCKS_the_deploy():
    """`/home/braino/gt/shantytown` is a SHARED working copy — any crew member can
    leave uncommitted work in it, and one had five modified files there while this
    was being written.

    So "always deploy from the canonical checkout", the rule adopted after the
    private-worktree incident, guarantees the PATH is right and says nothing about
    the TREE. Without this branch the module's own advice becomes the hazard: a
    dirty tree reads as a stale install and the printed fix (`pipx install
    --force`) would ship that WIP to every agent. A check must not recommend the
    incident it exists to prevent.
    """
    h = sc.check_self(
        run=_run_for(CANON, {CANON: "c" * 40},
                     dirty_out=" M shantytown/cli.py\n M shantytown/tend.py\n"),
        canonical=CANON, stale_files=_fresh)
    assert h.verdict == sc.BROKEN
    assert "UNCOMMITTED" in h.note
    assert "cli.py" in h.note and "tend.py" in h.note
    assert "Do NOT deploy" in h.note
    # and it must NOT print the reinstall command as the remedy here
    assert "pipx install --force" not in h.note.split("Do NOT deploy")[0]


def test_untracked_build_artifacts_do_not_count_as_dirty():
    """`build/` and `*.egg-info/` are produced BY installing. Counting them would
    make every run red immediately after a successful deploy — noise, and noise is
    how a check gets switched off."""
    h = sc.check_self(
        run=_run_for(CANON, {CANON: "c" * 40},
                     dirty_out="?? build/\n?? shantytown.egg-info/\n"),
        canonical=CANON, stale_files=_fresh)
    assert h.verdict == sc.OK


def test_an_unreadable_status_is_CANNOT_TELL():
    def run(argv):
        if argv[0] == "pipx":
            return 0, _meta(CANON)
        if "status" in argv:
            return 1, "fatal: not a git repository"
        return 0, "c" * 40 + "\n"
    h = sc.check_self(run=run, canonical=CANON, stale_files=_fresh)
    assert h.verdict == sc.CANNOT_TELL
