# Explicit source, caller checkout and the refusal remedy

Request a push from the primary checkout while naming a registered linked
worktree and its checked-out source branch. Before this change, the refusal
suggested supplying `--source-branch` even though it was already present.
Afterward, the same refusal explains the caller-checkout guard and gives an
exact, shell-quoted `cd` command for the selected checkout, with an absolute
source path for the repeated push even when the original argument was relative.

Both refusals return 1 without contacting peers. The positive control repeats
the same command from the selected checkout and publishes only the topic to
two isolated local bare peers; both main refs remain unchanged. Resolver,
caller, source-branch, remote-trust and non-fast-forward guards stay in force.

The baseline is exact source `f3d23bfdb2344d491f3fafb8da19f5e26ba922b6`.
The candidate CLI source SHA256 is `7d31694ee58bf8860d2363b93e3453347d5d810b4c249bee78b0c3ca1f9c9dc5`.
Both halves call the actual CLI entry function against real disposable Git
repositories. This is a source demonstration, not installed acceptance or a
production push.

The small [output recording](output.txt), [timing](timing.txt) and
[repeat script](run-demo.py) are retained here with the change under the existing
`docs/demos` tree. The output is also the readable transcript. Replay was
verified with (the exact recording retains PTY CRLF line endings; its
path-specific Git whitespace rule preserves all other whitespace checks):

```sh
scriptreplay --log-out output.txt --log-timing timing.txt
```

This is a terminal recording, not an inline video. To repeat, set `BASE_SOURCE`
and `CANDIDATE_SOURCE` to the two source checkouts and run `python3 run-demo.py`.
The script uses generic disposable paths under `/var/tmp` and filesystem-only
Git remotes; no credentials, crew configuration or network service is needed.
