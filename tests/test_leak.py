"""THE LEAK TEST IS THE INTERFACE.

docs/adapters.md: the suite runs the whole harness on files-registry +
files-tracker + none-context + none-knowledge + bare tmux. No quipu, no beads,
no bobbin, no multiplexer. If this goes red, something leaked into the core.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

CORE = ["shantytown/dispatch.py", "shantytown/protocols.py"]
FORBIDDEN = ["beads", "quipu", "bobbin", "dolt", "mysql", "tmux", "requests"]


def test_core_imports_nothing_first_class():
    """dispatch must not know beads, quipu, bobbin or even tmux exist."""
    root = Path(__file__).resolve().parents[1]
    for f in CORE:
        src = (root / f).read_text().lower()
        for bad in FORBIDDEN:
            assert f"import {bad}" not in src, f"{f} imports {bad} — the adapter is decorative"
            assert f"from {bad}" not in src, f"{f} imports from {bad} — leaked"


def test_harness_runs_with_no_backends_at_all():
    """The negative control. files + none + none, zero services."""
    code = (
        "import json,tempfile,pathlib;"
        "from shantytown.files import FilesRegistry,FilesTracker;"
        "from shantytown.tmux import NullPanes;"
        "from shantytown.dispatch import Dispatcher;"
        "d=pathlib.Path(tempfile.mkdtemp());"
        "(d/'c').mkdir();"
        "(d/'c'/'a.json').write_text(json.dumps({'pane':'%1'}));"
        "t=FilesTracker(d/'i');t.update('x',title='t');"
        "p=NullPanes();"
        "r=Dispatcher(FilesRegistry(d/'c'),t,p).go('x','a');"
        "assert p.sent and t.get('x').status=='in_progress';"
        "print('OK')"
    )
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=root)
    assert r.returncode == 0, f"harness cannot run bare: {r.stderr}"
    assert "OK" in r.stdout
