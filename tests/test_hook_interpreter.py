"""The emitted Stop hook must name an interpreter that can RUN it (internal-ref).

`_PY = sys.executable` carried the comment "sys.executable is by construction an
interpreter that exists and can import shantytown". That is an ASSUMPTION, and it
is false exactly when it matters: sys.executable is whatever ran the EMITTER. Emit
from a source checkout with the system python — `python3 -m shantytown.cli role
set`, i.e. how you regenerate settings while developing — and you bake in
/usr/bin/python3, which cannot import shantytown at all.

MEASURED on the live store 2026-07-20: lead.settings.json carried
    /usr/bin/python3 -m shantytown.stop_event send|drain
while `/usr/bin/python3 -c "import shantytown"` is a ModuleNotFoundError. The
lead's hooks were dead — the same silent outcome as the earlier `python: not
found` bug, reintroduced through a different door, in the one file the whole
stop-event route depends on.
"""
from __future__ import annotations

import pytest

from shantytown import runtime as rt


def test_a_usable_interpreter_is_accepted(monkeypatch):
    monkeypatch.setattr(rt, "_usable", lambda py: py == "/good/python")
    monkeypatch.setattr(rt.sys, "executable", "/good/python")
    assert rt._hook_interpreter() == "/good/python"


def test_an_UNUSABLE_sys_executable_falls_back_to_the_installed_one(monkeypatch):
    """THE REAL CASE. The dev shell's python cannot import shantytown; the
    settings being written are for the DEPLOYED agents, not for that shell."""
    monkeypatch.setattr(rt.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(rt.shutil, "which", lambda n: "/venv/bin/st")
    monkeypatch.setattr(rt, "_usable", lambda py: py == "/venv/bin/python")
    assert rt._hook_interpreter() == "/venv/bin/python"


def test_no_usable_interpreter_REFUSES_rather_than_emitting_a_dead_hook(monkeypatch):
    """Consistent with the rest of this module: refuse, never emit something that
    cannot run. A hook that cannot start is indistinguishable from no hooks."""
    monkeypatch.setattr(rt.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(rt.shutil, "which", lambda n: None)
    monkeypatch.setattr(rt, "_usable", lambda py: False)
    with pytest.raises(rt.SettingsError) as e:
        rt._hook_interpreter()
    assert "cannot run" in str(e.value)


def test_usable_does_not_let_the_CWD_satisfy_the_import():
    """The check must model where the hook RUNS, not where the emitter sits.

    My first version ran `python -c "import shantytown"` with the inherited CWD.
    Python prepends the CWD to sys.path for -c, so from a source checkout EVERY
    interpreter looked usable — it returned True for /usr/bin/python3, which
    cannot import shantytown anywhere else. The emitted hook executes in the
    AGENT'S workspace, which has no shantytown/ directory.

    Positive control on the control: a real interpreter that genuinely cannot
    import it must come back False even while we sit in the source tree.
    """
    import subprocess
    import sys
    # /usr/bin/python3 is not the venv; from the repo root it CAN import via CWD.
    from_cwd = subprocess.run([sys.executable, "-c", "import shantytown"],
                              capture_output=True).returncode
    isolated = rt._usable(sys.executable)
    if from_cwd == 0 and not isolated:
        return                      # the CWD leak is real and _usable resists it
    # Otherwise sys.executable genuinely can (or cannot) import it independent of
    # CWD; either way _usable must agree with the isolated answer.
    real = subprocess.run([sys.executable, "-c", "import shantytown"], cwd="/",
                          capture_output=True).returncode == 0
    assert isolated == real


def test_the_emitted_command_uses_the_checked_interpreter(monkeypatch, tmp_path):
    monkeypatch.setattr(rt, "_hook_interpreter", lambda: "/checked/python")
    d = rt.settings_for_role("lead", root=tmp_path)
    cmds = [h["command"] for b in d["hooks"]["Stop"] for h in b["hooks"]]
    assert cmds and all(c.startswith("/checked/python ") for c in cmds)
