"""Compare the same isolated render transition on base and candidate source."""
import hashlib
import subprocess
import sys
import tempfile
import tomllib
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from shantytown import codex  # noqa: E402

BASE = "31e677317458f02380e11a8aa1c5d7005c9e634a"
source = subprocess.check_output(["git", "show", BASE + ":shantytown/codex.py"])
before = types.ModuleType("shantytown._notify_demo_base")
before.__package__ = "shantytown"
exec(compile(source, "base/shantytown/codex.py", "exec"), before.__dict__)
argv = ["example-tool", "notify"]

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    for label, module in (("before", before), ("after", codex)):
        managed = module.render({"notify": argv}, root=root)
        retired = tomllib.loads(module.render({}, managed, root=root))
        operator = module.dumps({"notify": argv})
        retained = tomllib.loads(module.render({}, operator, root=root))
        changed = managed.replace('"example-tool"', '"operator-tool"')
        edited = tomllib.loads(module.render({}, changed, root=root))
        print(f"{label}: emitted notify present = {tomllib.loads(managed).get('notify') == argv}")
        print(f"{label}: removed-owner notify present = {'notify' in retired}")
        print(f"{label}: unowned operator notify preserved = {retained.get('notify') == argv}")
        print(f"{label}: operator edit preserved = {edited.get('notify') == ['operator-tool', 'notify']}")

print("base revision:", BASE)
print("candidate codex.py SHA256:", hashlib.sha256(Path(codex.__file__).read_bytes()).hexdigest())
