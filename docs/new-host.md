# New-host setup and acceptance

`st fleet init` scaffolds one host. `st ops doctor --deploy` checks the resulting
setup, including each peer's local checklist and its return entry. Run the
checklist **from both hosts over non-interactive SSH**: a return entry alone does
not prove the reverse SSH connection works. The check writes nothing and launches
no agents. Exit 0 means the inspected setup checks pass, 1 means incomplete, and
2 means an observation could not be made. This is configuration acceptance;
actual session launch, hook delivery and message delivery need separate smoke
checks after setup.

## Scaffold each host

Choose distinct host and agent names, the existing fleet's exact ontology
namespace, and absolute store/workspace paths. For example, on the second host:

```sh
st --root /opt/second/store fleet init -y \
  --host second --admin second-admin --crew second-worker \
  --workspaces /opt/second/workspaces \
  --peer first=operator@first.example,/opt/first/store \
  --quipu-server https://graph.example \
  --ontology-namespace https://fleet.example/ontology/
mkdir -p /opt/second/workspaces/second-admin /opt/second/workspaces/second-worker
```

Cards default to **manual** permissions. Choose `--unattended` at init only when
you want harness approval and sandbox bypass. An absent `dangerous` field also means manual. An existing card with a null
or malformed value needs a boolean decision in its crew JSON; init
`--force` deliberately preserves existing cards. Plain workspace directories
are supported; initialize and configure Git only when the workspace needs it.

On a fresh first host, use its own `fleet init` with the inverse peer:
`--host first --peer second=operator@second.example,/opt/second/store`.
For an **existing** first host, preserve its config and add this table to its
`shantytown.toml` (correct an existing table instead of duplicating it):

```toml
[host.peers.second]
ssh = "operator@second.example"
root = "/opt/second/store"
```

The second host needs the inverse table. `--force` does not rewrite an existing
configuration: it cannot add a peer to it. The checklist checks both entries,
remote host identity, and exact absolute store roots; an unreachable host is
UNKNOWN, never an empty successful result.

## Register the Host identity

The graph must contain the **exact** `[host] name` under the configured ontology
namespace, directly typed `Host`. Search existing identities first and reuse the
canonical name; do not mint an alias to make the check pass. Confirm `Host` is
in the graph server's loaded vocabulary before writing. Use your graph's governed
provisioning workflow, or submit an episode through its authenticated API:

```json
{
  "name": "register-second-host",
  "group_id": "YOUR_DEPLOYMENT_GROUP",
  "source": "new-host setup; session YOUR_SESSION_ID",
  "episode_body": "Registered the second deployment host after verifying its identity.",
  "nodes": [{"name": "second", "type": "Host",
             "description": "Second host of this deployment."}],
  "edges": []
}
```

Use the graph's actual group/namespace mapping and keep credentials out of files,
command output and shell history. Do not change an existing description by
reposting an episode. A write acknowledgement alone is not proof: the doctor
runs a graph control followed by a separate asserted-type query for the exact
Host IRI. Missing namespace, an unavailable graph or a truncated result cannot
pass. No graph credentials are copied to the peer by the doctor.

## Settings, capture and harness choice

Init emits administrator and worker settings through the normal role emitter.
For missing or old settings, re-emit through the supported operation:

```sh
st --root /opt/second/store fleet roles set second-admin administrator
st --root /opt/second/store fleet roles set second-worker worker
```

Choose a harness with `st agent harness NAME claude|codex`; do not edit settings
from inside an agent to bootstrap its own permissions. The doctor checks the
**selected** settings, including per-agent overrides, for PostToolUse and Stop
capture wiring. A valid role file cannot hide a broken override. This checks
wiring, not whether a running session has loaded it.

Codex Remote Control defaults off. If enabled, install its managed standalone
executable in the selected settings home, or set the deployment's
`[env] SHANTY_REMOTE_CONTROL = "false"`. A local Codex launch falling back from
missing Remote Control prerequisites does not satisfy an enabled-RC checklist.

## Prove incoming SSH exports and run both directions

Run `st --root /opt/second/store ops doctor --relay` for one combined, quoted
recipe for PATH (including st and tmux), SHANTY_ROOT and SHANTY_BACKEND. For zsh
put the exports in `${ZDOTDIR:-$HOME}/.zshenv`; use the appropriate non-interactive
startup mechanism for other shells. Select the intended tracker backend; do not
inherit a peer's backend by accident.

From first to second, and then second to first:

```sh
ssh -o BatchMode=yes operator@second.example \
  'st --root /opt/second/store ops doctor --deploy'
ssh -o BatchMode=yes operator@first.example \
  'st --root /opt/first/store ops doctor --deploy'
```

The doctor checks incoming exports **before** applying `[env]`, so TOML cannot
hide a missing shell export. Peer inspection uses bounded SSH with
`--deploy --local --json`, never recursive peer sweeps or injected PATH fixes.
For an intentionally isolated inspection use `--local`; it does not certify
reciprocal peers. `--json` provides the versioned checklist for automation and
omits deployment environment values and credentials.

After both checklists pass, launch a scratch agent through the deployed `st`,
observe readiness, perform a tool call, and inspect `st agent stats NAME` for
capture. Test any desired message route with an explicitly authorized message.
Configuration checks do not claim these runtime outcomes.
