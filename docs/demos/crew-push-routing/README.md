# Crew worktree push routing

The same CLI command resolves a crew container's provisioned worktree. Before the
fix it refuses a nonexistent conventional path; afterward it publishes exactly
the selected topic commit to the private bare remote.

Base: 0c0820fbc89885239c8662b6c1c4041eb32f14c5.
Demonstrated source: 97c844daf539115d621ab8b4bf4dae1a3ad0cc83.
Target: source checkouts and temporary local repositories, with no network.
This does not demonstrate an installed update or a production publication.

The small sanitized terminal recording is retained here with the source.
Download output.log.gz and timing.log, then replay:

~~~sh
gzip -dc output.log.gz > output.log
scriptreplay --log-timing timing.log --log-out output.log
~~~

The raw output and timing were captured together using util-linux script.
Replay was verified locally; no browser embed is claimed.
To reproduce either revision, check it out separately and run this revision's
probe.py with PYTHONPATH pointing to the selected source checkout. The probe
constructs fresh repositories and calls the real CLI without external writes.

Negative controls additionally cover wrong source branches, unrelated registered
repositories, ambiguous conventional/crew paths, and missing-worktree guidance
under both supported roots.
