"""The package must actually BUILD (aegis-daoh follow-on).

THE GAP THIS CLOSES. Every other test in this suite imports `shantytown` from the
SOURCE TREE, so all 497 of them stay green while the package is completely
uninstallable. That is not hypothetical — it was true on main:

    error: Multiple top-level packages discovered in a flat-layout:
           ['assets', 'shantytown']

`assets/logo.svg` was added for the README (41d9fc2). setuptools' flat-layout
discovery scans the repo ROOT, found a second top-level directory, and refused to
build. From that commit on, `pipx install .` could not produce a wheel — and the
only symptom was a deploy failing, days later, with an error naming the packaging
layout rather than the docs commit that caused it.

`st` is installed NON-EDITABLE from this tree, so "can it build" is not a packaging
nicety here: it is whether the fleet's harness can be deployed at all. A green test
suite that cannot answer that question is the same shape as everything else on
aegis-daoh — the artifact looked fine, the running thing was something else.

This calls setuptools' REAL build backend (~0.2s, no network, no isolation), so it
fails for the same reason a real `pip install` would, rather than asserting a
config string that merely correlates with buildability.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_metadata_actually_builds():
    """The mechanism, not a proxy. If this fails, `pipx install .` fails too."""
    build_meta = pytest.importorskip(
        "setuptools.build_meta",
        reason="setuptools unavailable — cannot exercise the real build backend")
    import os
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        with tempfile.TemporaryDirectory() as d:
            dist_info = build_meta.prepare_metadata_for_build_wheel(d)
        assert dist_info.startswith("shantytown-")
    finally:
        os.chdir(cwd)


def test_the_st_entry_point_is_declared():
    """The package must actually produce the `st` binary.

    Earned the hard way: while reconciling a concurrent edit to pyproject I
    deleted `[project.scripts]` outright. Metadata still built, all 522 tests
    stayed green, and the wheel would have installed cleanly — with no `st` on
    PATH. Every test here imports the module directly, so not one of them
    exercises the console script. "It builds" is not "it installs something
    usable", which is the same gap one notch smaller.
    """
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    scripts = data.get("project", {}).get("scripts", {})
    assert scripts.get("st") == "shantytown.cli:main", (
        "pyproject must declare the `st` console script; without it the package "
        "installs successfully and provides no binary")


def test_the_declared_entry_point_actually_resolves():
    """...and it must point at something that EXISTS. A console script naming a
    missing callable installs fine and fails at first invocation."""
    import importlib
    mod = importlib.import_module("shantytown.cli")
    assert callable(getattr(mod, "main", None))


def test_packages_are_declared_explicitly_not_auto_discovered():
    """Guards the FIX, which the build test alone cannot: with `packages` pinned,
    the build passes whether or not discovery would have worked, so a future
    revert to auto-discovery would not be caught by the test above until someone
    adds another root directory — i.e. exactly when it is expensive to find.

    Any new top-level directory (docs/, examples/, more assets) must be inert to
    the build, not a break.
    """
    text = (ROOT / "pyproject.toml").read_text()
    assert "[tool.setuptools]" in text, (
        "pyproject must declare packages explicitly; flat-layout auto-discovery "
        "breaks the build the moment a second top-level directory exists")
    assert 'packages = ["shantytown"]' in text


def test_a_second_top_level_package_would_not_break_the_build(tmp_path):
    """POSITIVE CONTROL, and the specific regression. Reproduce the condition that
    broke main — a second importable top-level directory — against a copy of the
    real pyproject, and assert the build still succeeds because packages is pinned.

    Without this, `test_the_package_metadata_actually_builds` passes on a tree that
    simply has no second directory, and would go on passing right up until someone
    adds one.
    """
    build_meta = pytest.importorskip("setuptools.build_meta")
    import os
    import shutil

    (tmp_path / "shantytown").mkdir()
    (tmp_path / "shantytown" / "__init__.py").write_text("")
    # the intruder: a second top-level package, exactly like `assets/`
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "__init__.py").write_text("")
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy(ROOT / "LICENSE", tmp_path / "LICENSE")

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with tempfile.TemporaryDirectory() as d:
            dist_info = build_meta.prepare_metadata_for_build_wheel(d)
        assert dist_info.startswith("shantytown-")
    finally:
        os.chdir(cwd)


# --- THE TWO VERSION CONSTANTS MUST AGREE (aegis-k7j6u) ----------------------
# Recorded as a follow-up when v0.1.0 was cut and left unguarded: "The two
# version constants are kept in sync BY HAND with no guard. A first release
# should not also change how versions are resolved, so I did not refactor
# `__version__` to read from package metadata in the same commit. Next bump is
# the moment that drift bites."
#
# This is that next bump, so here is the guard. It deliberately does NOT do the
# refactor either — reading `__version__` from importlib.metadata is the right
# end state and it changes how a SOURCE-TREE run resolves its version, which is
# exactly the thing `deployed_sha()` exists to keep honest. A cheap assert buys
# the safety now without touching resolution, and it fails on the bump rather
# than in whatever later session notices `st --version` disagreeing with the
# wheel it came from.

def test_the_two_version_constants_agree():
    """`pyproject.toml: version` and `shantytown.__version__` are independently
    hardcoded, and NOTHING made them agree.

    Bumping one and forgetting the other ships a wheel whose metadata says one
    version while the CLI it installs prints another — and the Release workflow
    would not catch it: its "Tag matches the declared version" step reads
    pyproject, so a stale `__version__` sails through a fully green release.
    That is the same-output-two-worlds shape this repo keeps finding, in the one
    number whose whole job is to tell two builds apart.
    """
    import re
    declared = re.search(r'(?m)^version\s*=\s*"([^"]+)"',
                         (ROOT / "pyproject.toml").read_text())
    assert declared, "pyproject.toml has no top-level version"
    module = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"',
                       (ROOT / "shantytown" / "__init__.py").read_text())
    assert module, "shantytown/__init__.py has no __version__"
    assert declared.group(1) == module.group(1), (
        f'pyproject.toml says {declared.group(1)!r} but '
        f'shantytown.__version__ says {module.group(1)!r}. Bump BOTH — the '
        f'Release workflow only checks the tag against pyproject, so a stale '
        f'__version__ ships a wheel whose CLI misreports its own version.')
