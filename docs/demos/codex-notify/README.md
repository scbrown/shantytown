# Remove a bundle's orphaned Codex notify

This is an isolated library demonstration, not an installed hook or a live
bundle unregister. The same render input emits a notify command and then
removes its owner. Before the change, notify remains; after the change, it
disappears. An unowned operator command and an operator edit both survive.

Run from the repository root:

```sh
python3 docs/demos/codex-notify/reproduce.py
```

The script loads the exact base revision named in its output and the current
candidate module. The candidate file hash identifies the demonstrated source.
The small output and timing files retained here form a terminal recording:

```sh
scriptreplay --log-out docs/demos/codex-notify/output.log \
  --log-timing docs/demos/codex-notify/timing.log
```

Download both files to replay; no inline player is claimed. The readable output
also provides a transcript. CLI registration/unregistration, all three roles,
per-agent ownership, and workspace/trust rewrites are covered by the tests.
Older files without ownership evidence are deliberately preserved; this change
does not identify an existing unmarked orphan as safe to delete.
