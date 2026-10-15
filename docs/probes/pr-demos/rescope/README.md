# Consolidated specification and owner ACK

Run `python3 docs/probes/pr-demos/rescope/reproduce.py /path/to/candidate/sd`
from the source checkout. The recorded candidate replaces `Old scope` with
`SPEC v1`, leaves it unacknowledged until its owner ACKs, and refuses a layered
replacement without advancing the generation. It uses the actual candidate CLI
and a private temporary local store. No scheduled sender, fleet store or running
installation is involved. `transcript.json` retains the observed output.
