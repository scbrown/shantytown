# AGENTS.md

Instructions for coding agents working in this repository. `CLAUDE.md` is a symlink to
this file, so the two instruction surfaces cannot drift.

Repo-generic only: build, test and conventions. Deployment specifics belong wherever a
given deployment documents itself, not here.

## What this is

`shantytown` is a small harness for running a crew of coding agents. It installs one
console script, `st` (`shantytown.cli:main`).

## Setup

```bash
python -m pip install -e .      # editable install; also installs the `st` script
python -m pip install pytest    # the only extra — see below
```

**The project declares zero runtime dependencies, and that is a design constraint, not an
accident.** `dependencies = []` in `pyproject.toml`. Do not add one casually: consumers
install this as an editable package, so a new dependency becomes a new install-time
failure mode for everyone at once. If you genuinely need one, say so explicitly in the
change rather than slipping it into the table.

Requires Python >= 3.11. CI runs the matrix 3.11, 3.12, 3.13, 3.14 — the declared floor
through the newest version, because testing only one end lets the other rot silently.

## Tests

```bash
python3 -m pytest -q            # testpaths = ["tests"]
```

Measured 2026-08-26: **2404 passed, 3 skipped, ~119s**. Two places in the repo quote
older counts (a CI comment says 1770/~57s, the README Principles section said 325); treat
any hard-coded count as a number that has already drifted once, and re-measure rather
than repeating it.

**A check must be able to fail.** This is the repo's own principle and it is the one most
worth honouring in new code: anything that reports health must be demonstrated returning
red. A test that cannot fail proves nothing. When you add a check, add the test that shows
it saying no.

## The version constant lives in TWO places

`pyproject.toml` (`project.version`) and `shantytown/__init__.py` (`__version__`). They
are kept in sync **by hand**, and CI asserts they agree. A bump that updates one and
forgets the other ships a CLI whose `--version` disagrees with its own package metadata.
Update both in the same commit.

## Packaging notes that have each broken something

- `[tool.setuptools] packages = ["shantytown"]` is declared explicitly because a
  top-level `assets/` directory gives flat-layout auto-discovery two candidate packages,
  and it refuses to guess.
- The `[build-system]` table is required for PEP 660 editable installs. Without it, an
  editable install fails in a way that silently freezes the installed version, so pulling
  the repo stops deploying anything.
- `setuptools>=77` is required by the PEP 639 `license = "MIT"` string form.

## Conventions

- Comments in this codebase explain **why**, usually by naming the failure that motivated
  the code. Match that: a comment that restates the line above it is noise, one that
  records what went wrong is why the file is readable a year later.
- Prefer small, direct functions over indirection. The stated principle is "lean, not
  absent" — orchestration is welcome, but no bus, no daemon zoo.
- Adapters are pluggable and the defaults are first-class; see `docs/adapters.md` before
  adding a hard-coded integration.

## Docs

`docs/` carries the design surface — `design.md` (dispatch, triage, trackers, panes),
`cli.md` (commands and boot modes), `roles.md`, `adapters.md`, `harnesses.md`. If you
change behaviour a doc describes, change the doc in the same commit.
