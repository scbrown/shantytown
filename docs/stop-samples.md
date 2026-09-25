# Private stop samples

An optional collection records up to 30 stop-hook boundaries for a human-labelled
benchmark. It does not classify stops, call a model, change supervision, or send
pane text to the tracker, inbox or a knowledge graph. A hook boundary is not proof
of a process exit or of deliberate intent.

Set `SHANTY_STOP_SAMPLES` in the deployment's `[env]` to an **absolute, local,
private evidence directory outside repositories and graph ingestion roots**. The
directory must already exist. Unset means disabled; no collection is created.
Use a dedicated directory per deployment. The collection lives in its
`stop_samples/collection.json` child (0600), inside a 0700 directory. Existing
insecure files or symlinks are refused. This is not isolation between agents
sharing the same Unix account; pane text may contain credentials.

Each sample contains event identity/time, agent, held item ID/status, the explicit
operator stop record if present, and the last 8192 UTF-8 bytes of the visible pane.
Truncation is marked. Missing panes consume no slot. The capture deadline is two
seconds; a busy collector skips rather than delaying a hook. Failed collection
does not prevent normal event delivery. Logs carry status/error class only.

The cap is 30 total across concurrent hooks, deduplicated by event ID. Samples
expire **30 days after the first capture**. Expiry erases pane text and retains a
closed marker, so later hooks do not refill the collection. A retention sweep is
required when enabling capture, including after disabling it:

```sh
python3 -m shantytown.stop_samples --root /absolute/private/evidence
```

Run this from the ownership-neutral installed package on a daily scheduled path.
Expiry is enforced on the next sweep or enabled hook: with a daily sweep, maximum
retention is 31 days. If neither runs, deletion cannot be guaranteed. The command
prints only status and removes staging files left by an interrupted writer.
Do not enable collection before arranging and verifying that sweep. No timer is
installed by this library. Remove the collection only after review/disposal;
removing its marker while capture is enabled starts a new collection.

Lead labels must precede any model run. `expected` and `label_author` start null;
missing evidence should be labelled unknown, never inferred as a crash. Raw
samples and labels remain local: report aggregate counts and results only. Explicit
operator stop stamps remain authoritative regardless of any later model score.
