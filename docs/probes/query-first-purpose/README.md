# Query-first purpose attribution

The same SessionStart trigger previously emitted a search recipe without a purpose. The candidate emits explicit CONTEXT usage and fallback HTTP headers. The hook itself performs zero graph reads.

Recorded source: baseline ce995db60403bb1ccfd084a15cf2133f69b04ebc; candidate 2ccaf32. This is an actual terminal recording of both source modules emitting their directives, followed by assertions on the output. It does not demonstrate an installed hook or a live receipt.

Reproduce from a repository containing both commits:

    python3 docs/probes/query-first-purpose/reproduce.py

Download terminal.log and timing.log together, then replay with util-linux:

    scriptreplay --log-timing timing.log --log-out terminal.log --divisor 10

The raw recording is downloadable; it is not an inline animation.
