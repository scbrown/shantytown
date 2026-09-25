import argparse, json, hashlib, time
from pathlib import Path
from shantytown.create_advisory import same_work, THRESHOLD
from shantytown.board_triage import JevMCP
parser=argparse.ArgumentParser(description='Evaluate frozen duplicate pairs; no store writes.')
parser.add_argument('--jev-command', required=True)
parser.add_argument('--pairs', default='tests/fixtures/create_advisory_pairs.jsonl')
args=parser.parse_args()
p=Path(args.pairs)
rows=[json.loads(x) for x in p.read_text().splitlines()]
results=[]
with JevMCP(args.jev_command,timeout=10) as client:
    for row in rows:
        start=time.monotonic()
        try:
            out=same_work(row['first'],row['second'],client)
            results.append(dict(id=row['id'],expected=row['duplicate'],predicted=out['answer']['noul']>=THRESHOLD,probability=out['answer']['noul'],latency_ms=round(1000*(time.monotonic()-start)),model=out['model'],request_hash=hashlib.sha256(json.dumps(out['request'],sort_keys=True).encode()).hexdigest()))
        except Exception as e:
            results.append(dict(id=row['id'],error=type(e).__name__))
valid=[r for r in results if 'error' not in r]
print(json.dumps(dict(label_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),threshold=THRESHOLD,label_basis='authored synthetic acceptance pairs, frozen before inference; not real-create precision',tp=sum(r['expected'] and r['predicted'] for r in valid),fp=sum(not r['expected'] and r['predicted'] for r in valid),tn=sum(not r['expected'] and not r['predicted'] for r in valid),fn=sum(r['expected'] and not r['predicted'] for r in valid),errors=len(rows)-len(valid),results=results),indent=2))
