# Pause feeds, keep the agent live

The same CLI request creates a feed hold for a private fixture agent, then
dispatches explicit work to its live modeled pane.

Before: the command exits 2 (absent), automatic feed is not held.
After: it exits 0, automatic feed is held, explicit dispatch still exits 0,
the pane remains live and no after-turn stop is requested. Clearing resumes
feeding. The final clock-controlled library probe holds just before expiry
and releases exactly at expiry.

Base: `f8b788eb5c0e2df182285c0f6a8e001004f82b66`.
Candidate implementation: `d78a4c8`; the following recording commit adds
only these demo artifacts. A later host-placement refusal adds no change to
this local, host-less fixture sequence. Peer/process observations use NullPanes and all
writes stay in disposable private registry/tracker files. No real agent,
inbox recipient, scheduler or board is changed. This proves source behavior,
not installed or natural-schedule acceptance.

Run `python3 docs/probes/pr-demos/feed-hold/record.py` from the repository root.
Download [output.log](output.log) and [timing.log](timing.log), then use
`scriptreplay --log-out output.log --log-timing timing.log`.
Playback requires util-linux and is verified locally; no inline player is
provided. The output log is also a readable transcript. These small sanitized
artifacts follow the repository's existing demo-directory retention.
