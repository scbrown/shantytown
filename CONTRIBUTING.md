# Contributing to shantytown

shantytown is a small harness for running a crew of coding agents. It is a pure
Python package with **no runtime dependencies**, installed as an editable
package by the crews that use it.

That last detail shapes almost everything below. Because consumers install with
`pip install -e .`, `main` is what the next `git pull` becomes: a bad merge here
reaches every consumer at once rather than one service. Please run the checks
before you push.

## Prerequisites

- Python **3.11 or newer** (`requires-python = ">=3.11"`)
- `pytest` — the only development dependency; the package itself declares none

Nothing else. There is no compiler step, no lockfile to honour, and no service
to stand up in order to run the suite.

## Setup

```bash
git clone <this repository>
cd shantytown
python3 -m pip install -e .
python3 -m pip install pytest
```

`pip install -e .` installs the package **and** its console script, `st`.

## The four checks CI runs

CI runs on every push to `main` and on every pull request, across Python 3.11,
3.12, 3.13 and 3.14. Run the same four locally and you will not be surprised:

```bash
python3 -m pytest -q                 # 1. the suite (2,571 tests, ~95s)
st --version                        # 2. the console script actually runs
python3 -c "import shantytown, tomllib, pathlib; \
  v = tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version']; \
  assert shantytown.__version__ == v, (shantytown.__version__, v)"
                                    # 3. the two version constants agree
```

The matrix is deliberately `fail-fast: false`. "3.11 broke" and "every version
broke" are different diagnoses, and running all four is the only thing that can
tell them apart — so please do not read one red cell as a general failure.

### Why check 2 exists

A package that imports cleanly but whose entry point does not run is a broken
install that the entire suite still passes, because the tests import modules
directly rather than invoking the script. `st --version` is the cheapest thing
that would have caught it.

### Why check 3 exists

The version is a hand-maintained constant in **two** places — `pyproject.toml`
and `shantytown/__init__.py` — kept in sync by hand with no guard. A bump that
updates one and forgets the other ships a CLI whose `--version` disagrees with
its own package metadata. If you bump the version, bump both.

## Two standing constraints

**Zero runtime dependencies.** `dependencies = []` is a promise, not an
accident: it is what lets a consumer install this into any environment without
negotiating a dependency graph. A change that adds a runtime import from outside
the standard library needs to argue for itself in the pull request. Test-only
and development dependencies are a different question and are fine.

**3.11 is a floor, not a formality.** It is a promise to anyone installing on an
older interpreter. Please do not reach for syntax or standard-library APIs newer
than 3.11 without saying so — the matrix will catch it, but it is cheaper to
know before CI does.

## Tests

The suite lives in `tests/` and runs with no network, no fixtures to provision
and no external services.

```bash
python3 -m pytest -q                       # everything
python3 -m pytest tests/test_worktree.py   # one file
python3 -m pytest -k worktree              # by name
```

New behaviour should come with a test that fails before the change and passes
after it. A test that passes both ways documents an intention rather than
verifying one.

## Pull requests

- Branch off `main` and keep the change focused on one thing.
- Explain **why** in the commit message, not just what. This codebase's comments
  carry their own reasoning — a reader six months out needs the argument more
  than the diff, which git already has.
- Make sure the four checks above pass locally.
- If a change alters agent-facing behaviour, say so explicitly in the PR
  description: consumers pick it up on their next pull, without a release step.

## Reporting a problem

Please open an issue with:

- what you ran and what happened, including the exact command and its output;
- what you expected instead;
- your Python version and how you installed the package (`st --version` and
  `python3 --version` cover most of it).

A report that lets someone else reproduce the behaviour is worth several that
describe it.

## Agent-facing documentation

`AGENTS.md` and `CLAUDE.md` in the repository root are instructions for coding
agents working **in** this repository, not documentation of the tool. If you are
a human contributor you can ignore them; if you are an agent, read them first —
they take precedence over this file where the two overlap.

## Licence

MIT — see `LICENSE`. Contributions are accepted under the same licence.
