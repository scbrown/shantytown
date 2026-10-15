from argparse import Namespace
from pathlib import Path
import os
import subprocess

import pytest
from shantytown import work_item
from shantytown.sd import SdTracker


@pytest.mark.real_store
@pytest.mark.skipif(not os.environ.get("SPEC_TEST_SD"), reason="explicit candidate Seeds CLI required")
def test_actual_selected_sdk_rescope_ack_and_lint(tmp_path, monkeypatch):
    binary = os.environ["SPEC_TEST_SD"]
    fixture = tmp_path / "project"
    fixture.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for key in list(os.environ):
        if key.startswith("SEEDS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("SHANTY_AGENT", "owner")
    monkeypatch.setenv("SHANTY_SD_BIN", binary)
    (fixture / ".seeds").mkdir()
    (fixture / ".seeds" / "config.toml").write_text('[project]\nprefix="fixture"\n')
    store = fixture / "fixture.db"
    result = subprocess.run([binary,"--store",str(store),"--actor","owner","create","scoped work",
                             "--assignee","owner","--description","old spec","--json"],
                            cwd=fixture,capture_output=True,text=True,check=True)
    import json
    identifier=json.loads(result.stdout)["id"]
    tracker=SdTracker(repo=str(fixture),store=str(store),prefix="fixture")
    body=fixture/"spec.md";body.write_text("Consolidated replacement.")
    args=Namespace(item=identifier,item_action="rescope",spec_file=body,quote="replace the scope",
                   requester="requester",summary="consolidated",urgent=False,session="fixture-session",
                   dry_run=True,root=fixture)
    planned=work_item.command(args,tracker)
    assert planned["version"]==1 and tracker.get(identifier).description=="old spec"
    args.dry_run=False
    changed=work_item.command(args,tracker)
    assert changed["version"]==1
    assert tracker.get(identifier).description=="SPEC v1\nConsolidated replacement."
    from shantytown import cli
    from shantytown.tmux import NullPanes
    root = tmp_path / "fleet"
    (root / "crew").mkdir(parents=True)
    (root / "crew" / "owner.json").write_text(json.dumps({"role":"worker"}))
    monkeypatch.setenv("SHANTY_SD_STORE",str(store))
    monkeypatch.setenv("SHANTY_SD_PREFIX","fixture")
    monkeypatch.setattr(cli,"Tmux",lambda *a,**k:NullPanes())
    from contextlib import redirect_stdout
    from io import StringIO
    output=StringIO()
    with redirect_stdout(output):
        assert cli.main(["--root",str(root),"--backend","seeds","--repo",str(fixture),"anchor","owner"])==0
    assert "SPEC CHANGED v0 -> v1 (unacked)" in output.getvalue()
    args.item_action="ack"
    assert work_item.command(args,tracker)["result"]=="verified"
    output=StringIO()
    with redirect_stdout(output):
        assert cli.main(["--root",str(root),"--backend","seeds","--repo",str(fixture),"anchor","owner"])==0
    assert "SPEC CHANGED" not in output.getvalue()
    assert not tracker.spec_state(identifier)["unacked"]
    args.item_action="rescope";body.write_text("ADDITION: layered")
    with pytest.raises(Exception):work_item.command(args,tracker)
    assert tracker.spec_state(identifier)["version"]==1
