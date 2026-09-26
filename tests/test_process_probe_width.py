"""Routing must see the full process arguments even in a narrow hook environment."""
import json
import os
import subprocess
import sys

import pytest

from shantytown.files import FilesRegistry
from shantytown.runtime import settings_for_role
from shantytown.stop_event import _lead_is_up
from shantytown.tier import route_stop
from shantytown.tmux import Tmux


@pytest.mark.parametrize("columns", ["80", "120"])
@pytest.mark.parametrize("wired", [True, False])
def test_narrow_process_probe_routes_by_actual_wiring(tmp_path, monkeypatch, columns, wired):
    crew = tmp_path / "crew"
    crew.mkdir()
    for name, role, lead in [("admin", "administrator", None),
                             ("lead", "lead", "admin"),
                             ("worker", "worker", "lead")]:
        (crew / f"{name}.json").write_text(json.dumps(
            {"role": role, "reports_to": lead, "pane": f"p-{name}"}))
    settings = tmp_path / "lead.settings.json"
    settings.write_text(json.dumps(settings_for_role("lead", root=tmp_path)))
    # A real process with the pointer beyond both widths. No agent is launched.
    args = [sys.executable, "-c", "import time; time.sleep(60)", "x" * 200]
    if wired:
        args += ["--settings", str(settings)]
    env = dict(os.environ)
    env.pop("CODEX_HOME", None)
    child = subprocess.Popen(args, env=env)
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "tmux":
            return subprocess.CompletedProcess(command, 0, f"{child.pid} p-lead\n", "")
        return real_run(command, **kwargs)

    monkeypatch.setenv("COLUMNS", columns)
    monkeypatch.setattr(subprocess, "run", run)
    panes = Tmux()
    monkeypatch.setattr(panes, "exists", lambda _: True)
    try:
        route = route_stop(FilesRegistry(crew), "worker",
                           lead_is_up=_lead_is_up(FilesRegistry(crew), panes))
        assert route.to == ("lead" if wired else "admin")
        assert route.rose is (not wired)
    finally:
        child.terminate()
        child.wait(timeout=5)
