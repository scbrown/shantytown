#!/usr/bin/env python3
"""Evaluate labeled tails through the real shared Jev client (explicit API use).

# arming: agent-invoked evaluation; no periodic collection or pane persistence
Output contains IDs and aggregate results, never the captures. Local real-tail
sets stay outside the repository. Unknown/absent labels have null accuracy.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shantytown import pane_state as ps


def evaluate(rows):
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('need nonempty uniquely identified labeled rows')
    if any(r['label'] not in ps.STATES or not isinstance(r['tail'], str) for r in rows):
        raise ValueError('invalid labeled row')
    captures = {f'p{i}': ps.tail(r['tail']) for i, r in enumerate(rows) if ps.tail(r['tail'])}
    result = ps.classify(captures) if captures else {'answers': {}}
    predictions = []
    for i, row in enumerate(rows):
        answer = result['answers'].get(f'p{i}', {'label': 'insufficient-evidence', 'confidence': None})
        predictions.append(dict(id=row['id'], expected=row['label'], predicted=answer['label'],
                                confidence=answer['confidence']))
    metrics = {}
    for label in ps.STATES:
        labeled = [r for r in predictions if r['expected'] == label]
        predicted = [r for r in predictions if r['predicted'] == label]
        correct = sum(r['predicted'] == label for r in labeled)
        metrics[label] = dict(support=len(labeled), correct=correct,
                             accuracy=correct / len(labeled) if labeled else None,
                             precision=correct / len(predicted) if predicted else None)
    return dict(per_label=metrics, predictions=predictions,
                correct=sum(r['expected'] == r['predicted'] for r in predictions), total=len(rows),
                confusion=dict(Counter(r['expected'] + ' -> ' + r['predicted'] for r in predictions)),
                model=result.get('model'), input_tokens=result.get('input_tokens'),
                prompt_sha256=ps.PROMPT_SHA)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('labels', type=Path, nargs='+')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    reports = {}
    for path in args.labels:
        try:
            reports[path.name] = evaluate(json.loads(path.read_text()))
        except ps.JevUnavailable as exc:
            reports[path.name] = {'error': f'JevUnavailable: {exc}'}
    args.output.write_text(json.dumps(reports, indent=2) + '\n')
    print(json.dumps({name: {k: v for k, v in report.items() if k != 'predictions'}
                      for name, report in reports.items()}, indent=2))
    return 2 if any('error' in r for r in reports.values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
