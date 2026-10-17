"""Private tracker fixtures on the base and candidate source paths supplied in env."""
import os
from pathlib import Path
import subprocess
import sys

PROBE = '''
import json
from types import SimpleNamespace
from shantytown.beads import BeadsTracker, plate
class Fixture(BeadsTracker):
 def __init__(self, rows): super().__init__(); self.rows=rows
 def _bd(self, *args): return SimpleNamespace(returncode=0,stdout=json.dumps(self.rows),stderr='')
rows=[dict(id='old',title='Old unranked',status='open',assignee='worker',priority=1,created_at='2026-01-01T00:00:00Z'),dict(id='ranked',title='Ranked',status='open',assignee='worker',priority=1,rank='1',created_at='2026-01-02T00:00:00Z')]
print('same-priority selection:',plate(Fixture(rows),'worker').id)
rows.append(dict(id='urgent',title='Urgent',status='open',assignee='worker',priority=0))
print('higher-priority control:',plate(Fixture(rows),'worker').id)
assert plate(Fixture(rows),'worker').id=='urgent'
'''
for label,source in [('base',os.environ['RANK_BASE_SOURCE']),('head',os.environ['RANK_HEAD_SOURCE'])]:
    source=Path(source)
    revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    print(label,'source',revision,flush=True)
    print('$ same mocked tracker rows -> plate(worker)',flush=True)
    env={**os.environ,'PYTHONPATH':str(source)}
    subprocess.run([sys.executable,'-c',PROBE],env=env,cwd=source,check=True)
