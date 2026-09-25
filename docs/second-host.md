# Joining an existing fleet from a second host

Use the same Quipu URL and ontology namespace as the first host, and a distinct,
explicit host name. A host name is an identity shared by the graph, cards and
configuration; do not derive it from the machine's current network name.

## Install and verify the sync floor first

Install from your fleet's approved source checkout. For the upstream project:

```sh
git clone https://github.com/scbrown/shantytown.git ~/src/shantytown
pipx install --editable ~/src/shantytown
st --version
```

Host-scoped projection and administrator-demotion protection first landed in
commit `08895073`. Development builds shared `0.4.0+dev`, so that package version
alone does not prove either capability. The explicit host-sync protocol is now
version **1**. Keep this requirement on every sync invocation, including previews:

```sh
st --root "/opt/fleet/.shanty" fleet roles sync --require-host-sync 1 --dry-run
```

A legacy CLI that does not recognize the flag rejects it before projection
(some older parsers print a traceback for an unknown nested option). A
protocol-aware build refuses a floor above what it supports. Do not retry without
the flag after a refusal: upgrade that host first. This is also a useful remote
preflight over SSH before allowing that host to sync.

The generated config also records `[host] min_sync_version = 1`. A supported
binary checks the config and explicit flag before querying or writing cards; the
higher requirement wins, even with `--force` or `--allow-breakage`. Invalid config
refuses rather than falling back to defaults. **A config field cannot retrofit
protection into a legacy binary that ignores it.** Upgrade all hosts and retain
the flag in automation; an old, unflagged sync remains unsafe.

## Scaffold the second host

Substitute your actual graph namespace, host identities, paths and SSH target:

```sh
st --root "/opt/fleet/.shanty" fleet init -y \
  --host secondary --admin secondary-admin --crew worker-b \
  --quipu-server https://graph.example \
  --ontology-namespace https://fleet.example/ontology/ \
  --canonical-source "$HOME/src/shantytown" \
  --peer primary=operator@primary.example,/opt/fleet/.shanty \
  --workspaces "$HOME/workspaces"
```

For unattended operation, add `--unattended` to init. It explicitly enables
harness approval/sandbox bypass on new cards. Without it, cards use manual
approval prompts. The wizard offers the same choice; `--force` preserves existing
cards even when this flag is supplied. Init itself launches no agents.

Init queries the configured graph before writing anything. If members already
exist, missing `--host` refuses. A failed graph read also refuses; it does not
mean the fleet is empty. A configured graph requires its exact namespace so the
example namespace cannot silently hide its members. `QUIPU_SERVER` and
`SHANTY_ONTO_NS` may also come from the environment or existing deployment config.
A standalone init with no configured graph remains available offline.

The generated TOML carries `[host] name`, `min_sync_version`, declared peers, and
`[env]` graph/source settings. New cards carry the local host. Repeat `--peer` for
other hosts. On the first host, declare the reciprocal peer using the second
host's SSH target and store root. Verify SSH access with `BatchMode=yes` and
ensure `st` is on the remote PATH. `--force` preserves existing config and cards;
it is not a migration or a way to change an existing host's identity.

Before launching on either host, set `[host] admission_owner` to the same always-on
host name on both. Both hosts must run a build that exports `admission_owner` in
`st crew --governor --json --local`. Missing or disagreeing authority declarations
hold growth. For an existing fleet, pause admissions and drain in-flight launches
before upgrading and changing the owner; see [account admission](cli.md) for the
lock and peer-census boundaries.

`SHANTY_CANONICAL_SOURCE` is an absolute **local checkout path**, not a Git URL or
the first host's path. `st ops doctor` compares the installed package with that
checkout. A source-less package needs a local approved checkout and an editable
install before this comparison can be meaningful.

## Place members and inspect projection

Through your fleet's graph administration path, declare each member's `runsOn`
host using that exact identity, and its reporting relationships. Init scaffolds
local cards; it does not publish graph membership. Once any member has placement,
sync skips members on other hosts and unplaced members. A host with no placed
members refuses. A sync cannot demote an existing administrator; correct the
source hierarchy instead of trying to override the refusal.

```sh
st --root "/opt/fleet/.shanty" fleet roles sync --require-host-sync 1 --dry-run
st --root "/opt/fleet/.shanty" fleet roles sync --require-host-sync 1
st --root "/opt/fleet/.shanty" crew
st --root "/opt/fleet/.shanty" ops doctor
```

Review the preview: it should list only this host's intended members and explicitly
name skipped remote members. An error or unknown verdict is not successful setup.

The `--workspaces` option records paths; it does not clone repositories or add
remotes. Clone the intended workspace repository into each path, or configure and
fetch its real upstream before expecting currency checks to report current.
Check `git -C <workspace> remote -v` and `git -C <workspace> status -sb`. Do not add
a fictitious remote merely to remove an UNKNOWN verdict. A deliberately nongit
workspace has no Git currency measurement.

## Use one board on every host

Declare the fleet's tracker in each host's `shantytown.toml`. A host that leaves
`SHANTY_BACKEND=files` uses its own local items even if a separate board command
can reach the primary. For a shared `br` board reached through an installed
transport executable:

```toml
[env]
SHANTY_BACKEND = "br"
SHANTY_BR_BIN = "/opt/fleet/bin/shared-board"
```

`shared-board` must accept ordinary `br` arguments, preserve stdout and exit
status, and pin the authoritative store on the primary. For example, it can
forward arguments over SSH to `br --db /srv/board/beads.db`; quote every argument
and do not retry an indeterminate write. This is the existing tracker executable
adapter, not a second database. Do not put the primary's filesystem path in
`SHANTY_BR_REPO` on a host where that path does not exist. No per-command backend
flag is required: `st task`, `st go`, `st work` and durable inboxes use the declared
transport. Config overrides a stale shell backend export.

`st go <item> <agent>` resolves the agent's host from its card (or the configured
graph for an off-host member without a local card), then invokes `st go` on that
host through its declared peer. The receiving host runs its usual triage,
governor, workspace preparation, pane verification and tracker read-back. Both
hosts must use the same board and run a version supporting host dispatch.
A colliding local pane name is never a fallback destination.

Notes travel on SSH stdin; graph context, reassign, worktree and dry-run options
travel with the command. `--worktree` is interpreted on the destination. A
cross-host `--repo` is refused because a caller-local store path has no defined
meaning on another host. `--dry-run` contacts the destination and runs its normal
preview without dispatching or assigning. A mismatched receiving host refuses
without forwarding again. SSH failure or timeout means delivery is unconfirmed;
inspect the destination and board before retrying.

## Prove messaging across the boundary

First check the receiving host's **non-interactive** SSH environment. An interactive
terminal can work while SSH sends cannot find `tmux`, cannot find the intended
store for journaling, or choose an unintended beads backend for durable mail:

```sh
ssh -o BatchMode=yes operator@peer.example \
  'st --root "/opt/fleet/.shanty" ops doctor --relay'
```

If SSH cannot find `st` yet, use its absolute installed path for this first check.
The diagnostic checks incoming `PATH` for `st` and `tmux`, an exported absolute
`SHANTY_ROOT` matching the selected directory, and an explicit `SHANTY_BACKEND`.
It runs before deployment `[env]` settings are applied, so those settings cannot
hide a missing shell export. It prints the three exports as one safely quoted
recipe for `${ZDOTDIR:-$HOME}/.zshenv`, including Homebrew paths on macOS. Put
them in the shell's non-interactive startup file, then repeat the SSH check.
With no backend declaration it suggests `files`; use `--backend` before `ops`
to select your intended backend, and reconcile any conflicting deployment setting.

Exit 0 means the environment checks passed, 1 means a requirement is missing or
wrong, and 2 means configuration could not be read reliably. No files are changed,
no tracker is opened, and no message is sent. A local run proves only the local
environment; neither a local nor SSH pass proves message delivery or backend
connectivity. Continue with the delivery checks below.

Use `st inbox <remote-agent> '<short message>'` for ephemeral delivery and
`st inbox -d <remote-agent> '<short message>'` when it must survive. Declared peers
supply the relay; no custom SSH wrapper is needed. Inspect the delivery line for
the destination host and backend. A files-backed durable inbox persists **on that
host**, not on a fleet-wide board. Configure the deployment's shared board backend
and repo (or the intended remote host's backend) before claiming shared durability.
Do not copy a first-host-only filesystem path into the second host's board config.
Verify a durable test pointer from the recipient's inbox; sender acceptance alone
is not receipt. Live delivery can retire the unread pointer immediately.

For the current 500-byte tracker title cap, the `inbox:` prefix and separator leave
**493 UTF-8 bytes for the attributed message**. The tool-added `[from wu] ` is 10
bytes, leaving **483 bytes** for that sender's own text. Other sender names have
different budgets, and multibyte punctuation consumes more than one byte. The
refusal reports your exact allowance. Put longer content in a bead and send its
ID or URL in a short inbox message; a file or multiline argument does not bypass
the cap. Use single quotes for short shell messages and file/stdin interfaces
where supported for longer prose.
