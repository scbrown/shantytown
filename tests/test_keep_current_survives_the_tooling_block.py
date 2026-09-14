"""The keep-current ff-pull must not be refused by st's OWN projected block.

THE FAILURE (aegis-6s9lxf). `st provision` injects a generated tooling block into
CLAUDE.md — a TRACKED file touched by ~76% of commits on main. `git merge
--ff-only` aborts when incoming commits touch a dirty file, so 12 of 12 crew
clones were permanently dirty and the great majority of keep-current pulls
refused. st's provisioning was defeating st's own keep-current mechanism, and the
fleet sat on stale trees from -2 to -97 commits.

The fix sets the block aside across the pull and puts it back, exactly as
.mcp.json already is. These tests pin the two properties that make that safe:
the pull must actually SUCCEED where it previously refused, and a CLAUDE.md
carrying any other edit must be left completely alone.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from shantytown.cli import _refresh_clone
from shantytown.tooling import instruction_text, is_committed_plus_block, BEGIN, END

INSTRUCTIONS = "## Canonical crew tooling\n\nUse the provisioned tools."


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout


def _origin_and_clone(tmp_path, body="# rulebook\n\noriginal line\n"):
    """A bare origin, one commit, and a clone of it."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@example.invalid")
    _git(seed, "config", "user.name", "t")
    (seed / "CLAUDE.md").write_text(body)
    _git(seed, "add", "CLAUDE.md")
    _git(seed, "commit", "-qm", "seed")
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    _git(clone, "config", "user.email", "t@example.invalid")
    _git(clone, "config", "user.name", "t")
    return origin, seed, clone


def _advance_origin(seed, origin, text):
    """A new upstream commit that TOUCHES CLAUDE.md — the refusing condition."""
    (seed / "CLAUDE.md").write_text(text)
    _git(seed, "add", "CLAUDE.md")
    _git(seed, "commit", "-qm", "upstream change")
    _git(seed, "push", "-q", str(origin), "main")


def test_the_pull_REFUSES_without_the_fix(tmp_path):
    """CONTROL. Without this, every other test could pass on a pull that was
    never going to refuse, and would prove nothing at all."""
    origin, seed, clone = _origin_and_clone(tmp_path)
    # dirty with something that is NOT our block, so the fix does not apply
    (clone / "CLAUDE.md").write_text("# rulebook\n\na hand edit\n")
    _advance_origin(seed, origin, "# rulebook\n\nupstream line\n")
    err = _refresh_clone(clone)
    assert err and "would be overwritten by merge" in err, err


def test_the_block_alone_no_longer_refuses_the_pull(tmp_path):
    origin, seed, clone = _origin_and_clone(tmp_path)
    committed = (clone / "CLAUDE.md").read_text()
    (clone / "CLAUDE.md").write_text(instruction_text(committed, INSTRUCTIONS))
    _advance_origin(seed, origin, "# rulebook\n\nupstream line\n")

    err = _refresh_clone(clone)

    assert err is None, f"pull should have succeeded, got: {err}"
    now = (clone / "CLAUDE.md").read_text()
    # the upstream commit landed ...
    assert "upstream line" in now
    # ... and the block came back ...
    assert BEGIN in now and END in now
    assert "Use the provisioned tools." in now
    # ... on top of the NEW committed content, not the old.
    assert is_committed_plus_block("# rulebook\n\nupstream line\n", now) == INSTRUCTIONS


def test_a_real_edit_beside_the_block_is_NEVER_discarded(tmp_path):
    """The load-bearing refusal. Making a pull succeed is not worth an edit."""
    origin, seed, clone = _origin_and_clone(tmp_path)
    committed = (clone / "CLAUDE.md").read_text()
    mixed = instruction_text(committed, INSTRUCTIONS).replace(
        "original line", "original line, EDITED BY THE AGENT")
    (clone / "CLAUDE.md").write_text(mixed)
    _advance_origin(seed, origin, "# rulebook\n\nupstream line\n")

    err = _refresh_clone(clone)

    assert err and "would be overwritten by merge" in err, err
    assert (clone / "CLAUDE.md").read_text() == mixed, "the agent's edit was touched"


def test_the_block_is_restored_even_when_the_pull_FAILS(tmp_path):
    """A pull that achieves nothing must not cost the agent its tooling block."""
    origin, seed, clone = _origin_and_clone(tmp_path)
    committed = (clone / "CLAUDE.md").read_text()
    before = instruction_text(committed, INSTRUCTIONS)
    (clone / "CLAUDE.md").write_text(before)
    # diverge locally so the ff-pull cannot succeed for a reason unrelated to us
    (clone / "other.txt").write_text("x")
    _git(clone, "add", "other.txt")
    _git(clone, "commit", "-qm", "local divergence")
    _advance_origin(seed, origin, "# rulebook\n\nupstream line\n")

    err = _refresh_clone(clone)

    assert err, "this pull was supposed to fail"
    assert (clone / "CLAUDE.md").read_text() == before, "block not restored after a failed pull"


def test_a_clean_clone_is_untouched(tmp_path):
    origin, seed, clone = _origin_and_clone(tmp_path)
    _advance_origin(seed, origin, "# rulebook\n\nupstream line\n")
    err = _refresh_clone(clone)
    assert err is None, err
    assert (clone / "CLAUDE.md").read_text() == "# rulebook\n\nupstream line\n"
    assert BEGIN not in (clone / "CLAUDE.md").read_text()
