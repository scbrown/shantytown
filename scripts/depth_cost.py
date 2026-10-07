#!/usr/bin/env python3
"""Token cost by context depth band, from Claude Code transcripts.

Usage: depth_cost.py [HOURS]. One row per assistant message id. Depth is
input + cache_read + cache_creation on that turn; cost is depth + output.
A token-VOLUME measure (rate limits), not dollars: cache reads bill cheaper."""
import glob, json, os, sys, time
from datetime import datetime, timezone
# usage: depthcost.py HOURS
H = float(sys.argv[1]) if len(sys.argv) > 1 else 24
cut = time.time() - H*3600
root = os.path.expanduser('~/.claude/projects')
buckets = {}  # band -> [turns, tokens_read, seconds-ish]
LINE = 400_000
per_agent = {}
for f in glob.glob(root + '/*/*.jsonl'):
    if os.path.getmtime(f) < cut: continue
    proj = os.path.basename(os.path.dirname(f))
    agent = proj.split('-crew-')[-1] if '-crew-' in proj else proj[-24:]
    try:
        fh = open(f, errors='replace')
    except OSError: continue
    seen = set()
    for ln in fh:
        if '"usage"' not in ln: continue
        try: r = json.loads(ln)
        except Exception: continue
        m = r.get('message') or {}
        u = m.get('usage'); ts = r.get('timestamp')
        if not u or not ts: continue
        mid = m.get('id')
        if mid in seen: continue
        seen.add(mid)
        t = datetime.fromisoformat(ts.replace('Z','+00:00')).timestamp()
        if t < cut: continue
        ctx = u.get('input_tokens',0)+u.get('cache_read_input_tokens',0)+u.get('cache_creation_input_tokens',0)
        cost = ctx + u.get('output_tokens',0)
        band = 'deep>400k' if ctx > LINE else ('mid200-400k' if ctx > 200_000 else 'fresh<200k')
        b = buckets.setdefault(band, [0,0]); b[0]+=1; b[1]+=cost
        a = per_agent.setdefault(agent, {}); ab = a.setdefault(band,[0,0]); ab[0]+=1; ab[1]+=cost
tot = sum(v[1] for v in buckets.values())
print(f"window {H}h, all transcripts under ~/.claude/projects")
for k in sorted(buckets):
    n,c = buckets[k]; print(f"  {k:12s} turns={n:6d} tokens={c/1e6:9.1f}M share={c/tot:6.1%} per-turn={c/max(n,1)/1e3:7.0f}k")
print("per agent (deep share of tokens):")
for a,d in sorted(per_agent.items(), key=lambda x:-sum(v[1] for v in x[1].values()))[:15]:
    t = sum(v[1] for v in d.values()); dp = d.get('deep>400k',[0,0])[1]
    print(f"  {a:30s} {t/1e6:8.1f}M deep={dp/max(t,1):5.1%}")
