"""Actual candidate Seeds CLI in a private local store; no fleet calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

binary = Path(sys.argv[1]).resolve(strict=True)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    home = root / "home"
    home.mkdir()
    env = {k:v for k,v in os.environ.items() if not k.startswith("SEEDS_")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
    (root / ".seeds").mkdir()
    (root / ".seeds/config.toml").write_text('[project]\nprefix="example"\n')
    def run(*args):
        result = subprocess.run([str(binary),"--store",str(root / "private.db"),
                                 "--actor","owner",*args,"--json"],
                                cwd=root,env=env,capture_output=True,text=True)
        return result.returncode,json.loads(result.stdout)
    rc,created=run("create","Example work","--assignee","owner","--description","Old scope")
    assert rc == 0
    identifier=created["id"]
    spec=root / "new.md"
    spec.write_text("One consolidated replacement.")
    rc,_=run("--session","example-session","rescope",identifier,"--spec-file",str(spec),
             "--quote","Use the consolidated replacement.","--summary","consolidated")
    assert rc == 0
    _,state=run("spec-state",identifier)
    _,items=run("show",identifier)
    item=items[0]
    rc,_=run("--session","example-session","ack",identifier,"--version","1")
    assert rc == 0
    _,acked=run("spec-state",identifier)
    spec.write_text("ADDITION: layered scope")
    refused,_=run("--session","example-session","rescope",identifier,"--spec-file",str(spec),
                 "--quote","Layer this.","--summary","layered")
    _,after=run("spec-state",identifier)
    assert refused != 0 and after["version"] == 1
    print(json.dumps({"target":"candidate CLI / private store", "before":"Old scope",
                      "after":item["description"],"version":state["version"],
                      "unacked_before_ack":state["unacked"],"unacked_after_ack":acked["unacked"],
                      "layering_refused":True,"version_after_refusal":after["version"],
                      "production_delivery":"not invoked"},indent=2))
