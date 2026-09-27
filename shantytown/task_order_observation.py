"""Supplemental observations; never inputs to a legacy verdict or enforcement."""
from collections import Counter


BUCKETS = ('read_before', 'reverse_or_concurrent', 'incomplete', 'no_matched_read')


def supplemental(contexts, evidence):
    """Keep the weaker ordering and strict population independent of each other."""
    buckets = dict.fromkeys(BUCKETS, 0)
    observations, excluded = [], []
    strict = Counter(PASS=0, FAIL=0, UNKNOWN=0)
    incomplete_without_operation = 0
    for context, rows in zip(contexts, evidence):
        reads = [r['ts'] for r in rows if r['kind'] == 'read_completed']
        operations = [r['ts'] for r in rows
                      if r['kind'] in ('material_start', 'ambiguous_start')]
        incomplete = not context['paired_start'] or any(
            r['kind'] in ('missing_start', 'capture_error') for r in rows)
        # Missing capture is not proof of read-only work. Keep that UNKNOWN in
        # the strict denominator even if its lost operation has no start row.
        if not operations and not incomplete:
            excluded.append(context['id'])
            continue
        strict[context['verdict']] += 1
        incomplete_without_operation += int(incomplete and not operations)

        # This second observation does NOT feed strict counts above, verdicts,
        # denominators or any policy. A shell command remains ambiguous.
        if incomplete:
            bucket = 'incomplete'
        elif not reads:
            bucket = 'no_matched_read'
        elif min(reads) < min(operations):
            bucket = 'read_before'
        else:
            bucket = 'reverse_or_concurrent'
        buckets[bucket] += 1
        observations.append({'context_id': context['id'], 'bucket': bucket})

    population = sum(strict.values())
    known = strict['PASS'] + strict['FAIL']
    return {
        'weak_observation': {
            'label': 'read-before-first-possible-op (weak)',
            'scope': 'recorded operations only; not an adoption verdict',
            'use_for_gates': False,
            'buckets': buckets,
            'contexts': observations,
        },
        'strict_population': {
            'scope': 'declared contexts with possibly-material operations, including CLI-only; '
                     'incomplete capture retained, not presumed read-only',
            'counts': dict(strict),
            'population': population,
            'headline_pass_fraction_including_unknown': strict['PASS'] / population if population else None,
            'secondary_pass_fraction_excluding_unknown': strict['PASS'] / known if known else None,
            'read_only_or_no_operation_excluded': len(excluded),
            'excluded_context_ids': excluded,
            'incomplete_without_observed_operation': incomplete_without_operation,
        },
    }
