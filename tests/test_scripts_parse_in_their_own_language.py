"""Every script in scripts/ must PARSE in the language its name and shebang claim.

THE INSTANCE (aegis-xfmon3, found by sattler within an hour of the push):
st-history-timer-gate was written in Python and named `.sh`. sattler did the
correct thing for a file with that name — `bash -n` — and got a syntax error,
which reads as "this script is broken" rather than "this script is not bash".

Two costs, and the second is the one that mattered:

  * a reviewer's correct reflex produced a WRONG conclusion about the file, and
    the reviewer then had to reason about why a broken script had returned a
    real verdict;
  * the mismatch hid a genuine defect. Invoked as `./script`, the shebang chose
    the SYSTEM python3, which cannot import shantytown, so the gate answered
    CANNOT TELL and exited 2 — fail-safe and completely inert. The only
    measurement ever taken had explicitly named a venv python: a green obtained
    through an interpreter nobody else would type proves the CODE and says
    nothing about the LANE.

So this file tests the CLASS. A name is an interface: it tells the next person
which checker to run, and one that lies costs them a wrong answer before it
costs them anything else.
"""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SH = sorted(SCRIPTS.glob("*.sh"))
PY = sorted(SCRIPTS.glob("*.py"))


def _shebang(p: Path) -> str:
    try:
        return p.read_text(errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return ""


def test_there_are_scripts_to_check():
    """The control. A glob that silently matches nothing turns every assertion
    below into a vacuous pass — which is the same shape of defect this whole
    file is about."""
    assert len(SH) >= 5 and len(PY) >= 1


@pytest.mark.parametrize("p", SH, ids=lambda p: p.name)
def test_a_dot_sh_is_actually_shell(p):
    assert "python" not in _shebang(p).lower(), (
        f"{p.name} is Python but named .sh — rename it .py; a name is the "
        f"instruction for which checker to run")
    r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("p", PY, ids=lambda p: p.name)
def test_a_dot_py_is_actually_python(p):
    sb = _shebang(p).lower()
    assert "bash" not in sb and not sb.endswith("/sh"), (
        f"{p.name} is shell but named .py")
    r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("p", SH + PY, ids=lambda p: p.name)
def test_an_executable_script_declares_an_interpreter(p):
    """A script with the execute bit and no shebang runs under whatever the
    caller's shell happens to be — the same class of ambiguity, one level down."""
    if p.stat().st_mode & 0o111:
        assert _shebang(p).startswith("#!"), f"{p.name} is executable with no shebang"
