"""st — the CLI. Thirty commands, and the count is load-bearing: each earns its slot.

    anchor [--short|--events|--harness] · go · repool · defer · inbox [--count] · task
    · crew [--count|--governor] · input [--show|--clear|--dismiss] · ask · answer
    · roles [--check|set|band|sync] · init · new · start [--mode]
    · stop · log · context · doctor [--install] · dream [--run]
    · tend [--install|--status|--reauth|--target] · attach [-r|--no-start]
    · dashboard [admin] · subscribe · cycle [--self|--allow-loss] · worktree [--gc]
    · push [--branch] · window {plan|drain|clear|release|abort} · stats · help <topic>
    · history <agent>

`help` earned the twenty-eighth slot in aegis-x6yoq. The recurring pane messages
were essays because the WHY had nowhere else to live, so every reason anyone might
need was re-pushed into every pane every few minutes — which is how the one
safety-critical line in them got skipped. Cutting them to pointers only works if
the pointer resolves, so the rationale needed a home that is READ ON DEMAND. A
command that exists solely to be pointed at is a real cost; an instruction nobody
finishes reading is a bigger one.

Six of those flags are MACHINE-READABLE modes, added for an external status bar
(anchor --short/--events/--harness, crew --count, crew --governor, inbox --count).
They are flags and not commands on purpose: the surface is the thesis, and "a
status bar wants this" does not earn a slot. Each prints ONE value and nothing
else — docs/cli.md.

TWO COMMANDS WERE RENAMED, and the count did not move (Stiwi, 2026-07-19):
  · prime -> anchor — an agent's anchor is what holds it to its work. `prime`
    named the HARNESS's act, and we inherited the word from the tool we left.
  · mail  -> inbox  — because it is now a REAL inbox: a pluggable protocol with a
    files and a tracker/beads implementation (shantytown/inbox.py), selected by
    the same --backend switch as the tracker, with a read side. `st mail -d`
    persisted a message nothing ever read back, onto the recipient's PLATE.

The binary is `st`, not `shanty`: `shanty` is Stiwi's tmux command and ours would
shadow it on PATH. A harness that steals the operator's own command name has
already made itself the centre of the world.

Gas Town ships ~110. This is not a smaller version of that list; it is the short
set we measurably use, and the discipline is the point (docs/cli.md). The surface
grew well past the original ten, each slot on a specific ask — not drift:
  · context — the bobbin Context protocol
  · doctor  — out-of-box tool detect/install, Stiwi's direct ask
  · tend    — crew supervision, native. Owner-directed, and it is a COMMAND and
              not a flag on `st crew` for one reason: `crew` is a read, and this
              is the only surface that can create a session and launch an agent.
              A consequence behind a flag on a read is a consequence somebody
              triggers by running the safe-looking thing.
  · attach  — attach to a crew member by name; st resolves the socket + pane so
              the operator never types `tmux -L <sock> attach -t <pane>`. Goes
              THROUGH shanty (themed) when present, bare tmux otherwise — this is
              where "use shanty, not raw tmux" becomes the default view.
  · dashboard — a live, tier-scoped observability panel: roster, current work, the
              REUSED state verdicts, last activity. The always-on sibling of the
              one-shot `crew`; refreshes on an interval in a second pane.
  · subscribe — watch quipu entity events and route governed workflows to the
              admin (the events adapter integrations.md sketched, built first-
              class on Quipu's cursored transaction log). Owner-directed.
  · start   — BOOT the town by mode: `lite` brings up the administrator alone and
              lets it decide who else is needed, `heavy` brings up every card.
              Owner-directed, and it is a command for the reason bootstrap.py
              argues: `new` REFUSES a live session (right for a primitive, wrong
              for a boot, where "already up" is success) and `tend` is a
              timer-driven supervisor that will not touch an agent it has no
              launch stamp for — a cold host has no stamps. Declarative and
              idempotent: it converges the fleet on a named crew set.
  · ask     — print the QUESTION a blocked agent is sitting on: prompt, the command
              being approved, and the numbered options VERBATIM. A read, and it
              exists because the read it replaces was `tmux -L <sock> capture-pane
              -p -t <pane> | tail -12` run by hand six times in one evening.
  · answer   — select an option BY NUMBER. Its own slot rather than a flag on `ask`
              for the reason `tend` and `attach` have theirs: `ask` is a READ, and
              this is the only verb in the repo that acts inside ANOTHER agent's
              decision. A consequence behind a flag on a read is a consequence
              somebody triggers by running the safe-looking thing.
  · repool  — hand an item back to the pool as ONE verified write: status ->
              open AND assignee cleared. The halves existed separately and the
              documented hand-back did only one of them, leaving the item
              in_progress-and-unassigned — outside `bd ready`, every haul, and
              every plate at once. A hand-back that drops work off the board is
              the defect; the command is the whole gesture.
  · defer   — park an item as ONE verified structured write: deferred status,
              exactly one blocker-kind label, and the reason naming its
              referent. Bare tracker deferral made classification optional.
  · init    — scaffold a NEW deployment by asking: the store, the crew cards (with
              generated panes), their hooks, and shantytown.toml. It writes through
              the EXISTING seams — the registry, tier.role_set, the same settings
              emitter `roles set` uses — so it is not a second way to declare a
              crew, it is the first way to get one without hand-authoring JSON.
  · dream   — schedule one bounded, reviewed reflection artifact on measured
              spare provider capacity. It is not a tend flag because operators
              need a read/preview surface for its due state and safety gates.
The count is PINNED by a test (tests/test_command_count.py): the next command
either updates this number or fails CI. This docstring used to say "ten" while the
code had eleven (context landed unannounced) — a count nobody enforces is a
comment, and in a repo whose whole thesis is the exact count, that is the bug.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
# MODULE LEVEL, not function-local. `_retire_card` referenced a bare `replace`
# while the only import of it sat INSIDE `_tend_retire`, so the crash-loop
# give-up raised NameError on every call and had never once written a card. It
# was invisible because the raise landed in that function's `except Exception`,
# which printed "⚠ could not retire <name>" into the middle of a tend pass —
# the one place a warning is guaranteed to scroll past unread. tend reported
# CRASH_LOOP ("RETIRED rather than respawned again") while the card said
# nothing of the kind: the supervisor's own report and the durable state
# disagreed, which is the same shape as the bug this bead is about, one caller
# over. Found by a test written for the provenance work (internal-ref).
from dataclasses import replace
from pathlib import Path

from . import beads as beads_mod
from . import cycle as cycle_mod
from . import dream as dream_mod
from . import bootstrap as boot_mod
from . import creel_advisory as creel_advisory_mod
from . import config
from . import harness as harness_mod
from . import launchable
from . import plate_publish
from . import roles as roles_mod
from . import scaffold
from . import traits as traits_mod
from . import triage as triage_mod
from .deployment import deployment_default, resolve_root, root_note
from .tmux import PaneNotAgent
from .dispatch import (Dispatcher, TriageRefused, SendUnverified,
                       DispatchedButUntracked, AlreadyAssigned, Blocked, Closed,
                       HasOpenBlocker,
                       GovernorRefused, RepoolRefused, DeferRefused,
                       BLOCKER_KIND_LABELS, TrackerWriteLost)
from . import forgejo as forgejo_mod
from . import governor as gov_mod
from . import governor_utilization as util_mod
from . import governor_metrics as gov_metrics_mod
from . import guard as guard_mod
from . import attribution as attribution_mod
from .attribution import attribute
from .events import FilesEvents
from .inbox import FilesInbox, MessageTooLong, TrackerInbox, is_message
from .triage import Action
from . import supervisor as sup_mod
from . import tend as tend_mod
from . import provision as prov_mod
from . import notify as notify_mod
from .files import (FilesRegistry, FilesTracker, plate as files_plate,
                    items as files_items)
from .launched import FilesLaunches, CURRENT, STALE, UNKNOWN
from .stopped import FilesStops
from .quipu import QuipuRegistry
from . import graph_adoption
from . import window as window_mod
from . import selfcheck
from .anchor import Unreachable, anchor as do_anchor
from . import handoff_text
from .runtime import (asks_a_question, auth_expired, bash_guard_command,
                      ClaudeRuntime, CapabilityError, SettingsError,
                      emitted_bash_guard, emitted_pre_edit_guard,
                      emitted_stop_directions, pre_edit_guard_command,
                      input_stranded, live_stop_directions, live_wiring,
                      settings_for_role)
from .tmux import Tmux, declared_socket
from .workspace import (WorkspaceError, agent_worktrees, cleanup_worktree,
                        ensure_workspace, ensure_worktree, push_every_remote,
                        tree_staleness, unlaunchable, upstream_ref, worktree_for)
from .provision import ProvisionError, provision as provision_ws

# `st new` liveness poll: how long to wait for the runtime to appear in the pane
# before returning could-not-tell (2). Module constants so tests can shrink them
# to (1, 0) — a real launch takes a few seconds, a test must not.
_LIVE_ATTEMPTS = 20
_LIVE_DELAY = 0.25

# 0 did it | 1 refused (precondition) | 2 could not tell (backend unreachable)
OK, REFUSED, CANNOT_TELL = 0, 1, 2


def _panes(a):
    """The pane adapter for this invocation, on the FLEET's declared socket.

    Never a bare Tmux(): bare tmux sees only the default server, so from inside
    any pane on a named socket — including the status-bar wrapper's — it reports
    EVERY LIVE AGENT AS DOWN, confidently and with exit 0. tmux.py has warned
    about exactly this since it was written; the CLI just never asked.
    """
    return Tmux(socket=declared_socket(getattr(a, "root", None) or "."))


def _registry(a):
    """Identity backend for this invocation, selected by --registry.

    quipu is the SOURCE OF TRUTH (Stiwi: "quipu should be the source of truth");
    files is the projection/cache and the leak detector. Default stays files so
    an offline invocation still resolves identity locally; --registry quipu reads
    it straight from the graph. Either way the SAME roles.check runs over it.
    """
    chosen = getattr(a, "registry", "files")
    if chosen == "quipu":
        # Pass the root so the ADDRESS and the NAMESPACE both come from the
        # deployment's shantytown.toml rather than whatever this shell exported. Without
        # it the client falls back to quipu's stock port — which on a host where
        # another service owns it means quietly querying a stranger — and to the
        # stock namespace, which means truthfully querying the real graph for
        # entities that do not exist. Both used to read as "an empty fleet".
        return QuipuRegistry(root=getattr(a, "root", None))
    if chosen == "toml":
        # GitHub #11: identity declared by hand, in the same file as the modes,
        # for a deployment that wants no ontology and no generated cards.
        return config.TomlRegistry(a.root)
    return FilesRegistry(a.root / "crew")


def _deployment_default(a, key: str) -> str | None:
    """A deployment-declared default for `key`: [env] in shantytown.toml,
    then the ambient env — the SAME source order the launch
    side already uses for carried env and SHANTY_BASH_GUARD (runtime.py), so
    "where deployment config lives" has one answer, not two.

    Why this exists (aegis-tisp): the shanty status-bar segment and the session
    picker both call PLAIN `st anchor <agent> --short` — by design, a public
    repo must not embed a tracker path. On a fleet whose plates live in beads,
    plain `st anchor` resolved to the files backend and rendered EMPTY, so both
    surfaces were blank, consistently and with exit 0. The deployment needs to
    say "my tracker is beads at <repo>" ONCE; this is where it says it.

    The READ itself now lives in deployment.deployment_default — the same six
    lines were also in runtime.py twice, and a fourth caller was coming. This
    stays as the CLI's args-shaped door onto it.
    """
    return deployment_default(getattr(a, "root", None), key)


def _backend(a, default="files") -> str:
    """The selected tracker backend: explicit --backend, else the deployment's
    SHANTY_BACKEND (shantytown.toml/env), else `default` (per-command).

    ONE resolver, because the sentinel only buys honesty if nothing re-guesses
    it. `--backend` now defaults to None so "the user said files" and "the user
    said nothing" stop being the same value — which they had to stop being for
    `mail -d` to default differently without overriding an explicit choice.
    A deployment default sits BETWEEN those: quieter than a flag, louder than
    the built-in guess — and an unrecognized value REFUSES rather than falling
    through to files, because a silent files fallback is the exact blank-plate
    bug this knob exists to fix (aegis-tisp).
    """
    explicit = getattr(a, "backend", None)
    if explicit:
        return explicit
    declared = _deployment_default(a, "SHANTY_BACKEND")
    if declared:
        if declared not in ("files", "beads", "br", "forgejo"):
            raise SystemExit(f"  refused: SHANTY_BACKEND={declared!r} is not a "
                             "backend (files|beads|br|forgejo). Fix [env] in "
                             "shantytown.toml (or the environment); a typo must "
                             "not silently mean files.")
        return declared
    return default


def _tracker(a, default="files"):
    """The tracker for this invocation, selected by --backend (#3).

    arnold added beads.plate() (the reader) but the CLI still wired FilesTracker
    unconditionally, so `st --backend beads` did not exist and his plate was
    unreachable. This wires it: the deployed ``beads`` name and the explicit
    ``br`` alias both reach BrTracker. Identity (registry) stays files — work
    lives in the live br store, identity does not.
    """
    b = _backend(a, default)
    if b in ("beads", "br"):
        from .br import BrTracker
        return BrTracker(
            repo=getattr(a, "repo", None)
            or _deployment_default(a, "SHANTY_BR_REPO")
            or _deployment_default(a, "SHANTY_BEADS_REPO")
            or _default_bd_repo(a),
            extra_repos=beads_mod.parse_extra_repos(
                _deployment_default(a, beads_mod.EXTRA_REPOS_KEY)))
    if b == "forgejo":
        # --repo is owner/name here (the forge's coordinates), not a directory.
        from .forgejo import ForgejoTracker
        repo = getattr(a, "repo", None)
        if not repo:
            raise SystemExit("  refused: --backend forgejo needs --repo owner/name")
        return ForgejoTracker(repo)
    return FilesTracker(a.root / "items")


def _default_bd_repo(a) -> str | None:
    """Where bd resolves its store when `--repo` was not given: feed_check's
    bd_cwd walk-up (the admin card's workspace, walked to the nearest .beads) —
    NEVER the ambient cwd (aegis-quvg).

    The ambient-cwd default on bd-backed paths is a measured recurring class:
    the tend loop ran two days with the idle-fleet push dead on it (bd5f55a),
    and `st inbox` read "no beads database found" from any non-store cwd —
    including ~/gt/shantytown, where an operator most naturally stands. ONE
    resolver, shared with feed_check, so the Rule Zero gate and every beads-
    backed read can never disagree about where the store lives. Explicit
    `--repo` always wins; a failed resolution falls back to None (ambient cwd,
    today's behavior) — fail toward the old default, never toward an invented
    path."""
    try:
        from .feed_check import bd_cwd
        return bd_cwd(_registry(a))
    except Exception:
        return None


def _plate(a):
    """The plate reader matching the selected tracker."""
    trk = _tracker(a)
    if _backend(a) in ("beads", "br"):
        from .br import plate as br_plate
        return lambda who: br_plate(trk, who)
    return lambda who: files_plate(trk, who)


def _tracker_items(trk):
    """Backend reader paired with ``trk`` without widening Tracker."""
    from .br import BrTracker, items as br_items
    if isinstance(trk, BrTracker):
        return br_items(trk)
    if isinstance(trk, FilesTracker):
        return files_items(trk)
    return beads_mod.items(trk)


def _tracker_rows(trk):
    """Raw rows paired with ``trk`` for reference scans."""
    from .br import BrTracker, rows as br_rows
    if isinstance(trk, BrTracker):
        return br_rows(trk)
    if isinstance(trk, FilesTracker):
        return [vars(item) for item in files_items(trk)]
    return beads_mod.rows(trk)


def _tracker_plate(trk, who):
    """Plate reader paired with ``trk`` for non-CLI call sites."""
    from .br import BrTracker, plate as br_plate
    if isinstance(trk, BrTracker):
        return br_plate(trk, who)
    if isinstance(trk, FilesTracker):
        return files_plate(trk, who)
    return beads_mod.plate(trk, who)


def _inbox(a, default="files"):
    """The inbox for this invocation, selected by the SAME --backend switch as the
    tracker (Stiwi: "an inbox concept we can map to beads or other ticket
    modules"). No second selection mechanism — one switch, or an operator ends up
    sending on one backend and reading on another.

        files  -> FilesInbox under the .shanty root, beside events/. Structurally
                  off the plate, and the leak detector for the other one.
        beads  -> TrackerInbox over the SELECTED tracker, so a durable message is
                  a real bead on the aegis store (dearing's qdal.2 parity ruling).

    The beads side needs a LISTER, which the three-function Tracker protocol does
    not have and must not grow (aegis-gqr8). It is injected per-backend, exactly
    like the plate reader two functions up.
    """
    # Resolve through _backend, NOT getattr(a,"backend","files"). `--backend`
    # defaults to None now (the sentinel that lets -d default differently), so
    # the old getattr read None and fell to files ALWAYS — including on the
    # durable path, which was printing "(beads)" while writing to files. A
    # command that reports a different store than it wrote to is the exact lie
    # this repo exists to refuse, and it is worse than the missing default:
    # you would go looking in beads for a message that is not there.
    if _backend(a, default) in ("beads", "br"):
        trk = _tracker(a, default)
        from .br import items as br_items
        return TrackerInbox(trk, lambda: br_items(trk))
    return FilesInbox(Path(a.root) / "inbox")


def _me(a) -> str | None:
    """Who am I, for the commands that default to the caller. One resolution —
    the positional if the command has one, else $SHANTY_AGENT (which the launcher
    exports, harness.py). Used by anchor and by the inbox read modes; a status bar
    calls both, and they must agree about whose plate and whose inbox."""
    import os
    return getattr(a, "me", None) or os.environ.get("SHANTY_AGENT")


def _verified_sender(a, panes) -> tuple[str | None, bool]:
    """Resolve a message sender without trusting a leaked daemon environment.

    Codex Remote Control tool shells inherit both ``SHANTY_AGENT`` and
    ``TMUX_PANE`` from the daemon.  Those are therefore one rail, not two.  A
    missing pane marker preserves the ordinary non-tmux fallback; a present but
    stale/unresolvable marker refuses attribution, exactly as the stop path does.
    """
    sender = _me(a)
    if not sender:
        return None, True
    if not os.environ.get("TMUX_PANE"):
        return sender, True
    from .stop_event import _stop_identity
    verified = _stop_identity(_registry(a), panes, sender)
    return verified, verified is not None


def _wire(a) -> Dispatcher:
    # sender=_me(a): `st go` signs the dispatch with whoever ran it (aegis-5vxmz).
    # One resolution, the same one `st inbox` attributes with — a coordinator must
    # not be one name on a message and another on the work it hands out.
    from .feed_audit import FeedAudit
    return Dispatcher(_registry(a), _tracker(a), _panes(a),
                      governor=_dispatch_gate(a), sender=_me(a),
                      audit=FeedAudit(Path(a.root)))


def _governors(a):
    """Build one reader/governor per declared policy, keyed by harness.

    ``base`` deliberately retains the legacy state file and is the only entry
    in an old one-governor deployment.  Siblings are independent readers and
    hysteresis records: sharing either would reintroduce cross-provider policy.
    """
    cfg, err = config.load_or_default(Path(a.root))
    if err:
        print(f"  ⚠ {err} — running on config DEFAULTS", file=sys.stderr)
    if not cfg.governor.active:
        return cfg, {}
    policies = {"base": cfg.governor, **cfg.governor.by_harness}
    out = {}
    for name, policy in policies.items():
        try:
            reader = gov_mod.reader_for(policy)
        except gov_mod.GovernorError as e:
            print(f"  ⚠ usage governor {name} DISABLED — {e}. The fleet is "
                  "running UNGOVERNED for this provider.", file=sys.stderr)
            continue
        out[name] = gov_mod.Governor(
            policy, reader,
            gov_mod.FilesGovernorState(Path(a.root),
                                       None if name == "base" else name),
            name=name)
    return cfg, out


def _governor(a):
    """The usage governor for this invocation, or None if none is configured.

    None means OFF, and off is the default: with no `[[governor.tier]]` in
    shantytown.toml there is nothing to enforce, and every command behaves
    exactly as it did before this feature existed.

    A MISCONFIGURED GOVERNOR IS NEVER SILENTLY OFF. `config.load_or_default`
    already refuses to raise on this path, but a source we cannot wire (a
    `prometheus` with no url) would leave the fleet running ungoverned at exactly
    the moment the operator believed they had turned the feature ON. So the
    failure is printed, every time, on stderr — the same rule the module's
    fail-safe section applies to a lost signal.
    """
    _cfg, governors = _governors(a)
    return governors.get("base")


def _governor_for(cfg, governors, card, root):
    """(harness, governor, synthetic signal-lost verdict-or-None) for card."""
    harness = harness_mod.name_for(card, root=root)
    # The base governor's provider is its compatibility default (Claude), not
    # [harness].default.  The latter chooses which program new cards launch;
    # using it here made a codex launch default falsely mark the still-metered
    # Claude base governor as unconfigured (aegis-5ve1h).
    if gov_mod.unconfigured(cfg.governor, harness):
        policy = cfg.governor
        frozen = policy.on_signal_lost == gov_mod.FREEZE
        verdict = gov_mod.Verdict(
            reading=gov_mod.Reading(source="unconfigured"), signal_lost=True,
            frozen=frozen,
            why=f"no governor is configured for harness {harness!r}",
            alarm=(f"USAGE SIGNAL LOST: harness {harness!r} has no configured "
                   "usage governor. " +
                   ("on_signal_lost = \"freeze\": no new work is being "
                    "dispatched for this provider. " if frozen else
                    "on_signal_lost = \"warn\": THE FLEET IS RUNNING "
                    "UNGOVERNED for this provider. ") +
                   "Configure [governor.by_harness." + harness + "] or "
                   "deliberately stop running it."))
        return harness, None, verdict
    return harness, governors.get(harness, governors.get("base")), None


def _dispatch_gate(a):
    """The `st go` half of the governor: `item -> "" | refusal`.

    A PURE READ (`persist=False`). Dispatching must not RATCHET fleet policy as a
    side effect of being run — `st tend` is the pass that already decides who
    lives and it is the one writer of the engaged tier. A dispatch that extended
    a hysteresis hold would mean the governor's state depended on how often
    somebody typed `st go`.

    Evaluated once per invocation and closed over, so a dispatch pays at most one
    metric read — not one per plan()/triage()/go() call on the same Dispatcher.
    """
    try:
        maintenance = window_mod.active(Path(a.root))
    except window_mod.WindowUnreadable as exc:
        return lambda item, agent=None: f"maintenance-window state unreadable — {exc}"
    if maintenance is not None:
        return lambda item, agent=None: (
            f"maintenance window {maintenance['id']!r} is {maintenance['state']} — "
            "dispatch/feed held until release or abort")
    cfg, governors = _governors(a)
    stood_down = bool(getattr(getattr(cfg, "fleet", None), "stood_down", False))
    if not governors and not stood_down:
        return None
    cards = {card.name: card for card in _registry(a).all().exact()}
    verdicts = {}
    sender = cards.get(_me(a))
    sender_harness = (harness_mod.name_for(sender, root=a.root)
                      if sender is not None else None)

    def cross_subscription(target) -> bool:
        if sender_harness is None or target is None:
            return False
        return harness_mod.name_for(target, root=a.root) != sender_harness

    # Said ONCE per gate, not once per agent. `st go` gates every candidate in a
    # loop, so an unlatched warning prints the same sentence N times and the
    # operator learns to scroll past the block that contains the refusal.
    _hostmem_said = [False]

    def gate(item, agent=None):
        """SAY IT WHEN A WAIVER IS WHAT LET THIS THROUGH (aegis-yegfx).

        A silent exemption is the one failure mode this feature can introduce:
        the fleet keeps spending under a floor an operator believes is holding,
        and nothing in the output distinguishes that from a governor that is
        simply off. Printed on stderr like `alarm`, for the same reason and at
        the same layer — the policy object decides, the CLI announces.
        """
        card = cards.get(agent)
        if card is None:
            return ""
        delegated = cross_subscription(card)
        if stood_down and not delegated:
            return ("FLEET STOOD DOWN — dispatch suppressed. Clear "
                    "`[fleet] stood_down` to resume. Cross-subscription "
                    "delegation remains available.")
        # THE PHYSICAL BRAKE, checked BEFORE `if not governors` on purpose
        # (aegis-do672). Host memory is not conditional on a usage governor
        # existing: a deployment with no [governor] table still runs on a box that
        # can be OOM-killed, and returning early above would skip the one brake
        # that is about the machine rather than about the budget.
        #
        # It is also checked before the usage tiers because its refusal is the more
        # actionable one — "wait for a build to finish" is a thing the operator can
        # do now, where a usage floor is a thing they wait out.
        mem = _hostmem_verdict(cfg)
        if mem is not None:
            if mem.alarm and not _hostmem_said[0]:
                _hostmem_said[0] = True
                print(f"  ⚠ {mem.alarm}", file=sys.stderr)
            if mem.refusal:
                return mem.refusal
            if mem.warning and not _hostmem_said[0]:
                _hostmem_said[0] = True
                print(f"  ⚠ {mem.warning}", file=sys.stderr)

        if not governors:
            return ""

        if sender is not None and cfg.governor.by_harness and not delegated:
            _sh, source_governor, source_unconfigured = _governor_for(
                cfg, governors, sender, a.root)
            if source_unconfigured is None and source_governor is not None:
                key = f"reserve:{sender_harness}"
                if key not in verdicts:
                    verdicts[key] = source_governor.evaluate(persist=False)
                source_verdict = verdicts[key]
                readings = [p for p in source_verdict.by_window.values()
                            if p is not None]
                pct = max(readings) if readings else source_verdict.pct
                reserve = source_governor.policy.delegation_reserve_pct
                if reserve and pct is not None and pct >= 100 - reserve:
                    return (f"the {sender_harness} usage governor is in its final "
                            f"{reserve}% delegation reserve ({pct:.0f}% used): "
                            "normal execution dispatch is held. Delegate to an "
                            "agent on another subscription; coordination messages "
                            "remain available.")
        harness, governor, unconfigured = _governor_for(cfg, governors, card, a.root)
        if harness not in verdicts:
            verdicts[harness] = (unconfigured if unconfigured is not None
                                 else governor.evaluate(persist=False))
            if verdicts[harness].alarm:
                print(f"  ⚠ {verdicts[harness].alarm}", file=sys.stderr)
        verdict = verdicts[harness]
        refusal = verdict.admits(item, agent)
        if not refusal and verdict.waives(item, agent):
            print(f"  ⚠ {verdict.waiver_says(item, agent)}", file=sys.stderr)
        return refusal

    return gate



def _hostmem_verdict(cfg):
    """The physical admission verdict, or None when the brake is off (aegis-do672).

    An env override beats the config table for one run — the alternative an
    operator reaches for under a brake they need to get past is commenting out the
    table, which disarms it for everyone and stays disarmed.
    """
    from . import hostmem

    limits = hostmem.env_override()
    if limits is None:
        limits = getattr(cfg, "hostmem", None)
    if limits is None or not limits.active:
        return None
    return hostmem.check(limits)



#: How long a tend pass may spend on the BEST-EFFORT sweeps that follow respawn
#: (aegis-qwadc). Not a timeout on the pass and not a timeout on any one sweep:
#: once the budget is spent, the NEXT sweep is skipped rather than started, and
#: whatever is already running finishes normally.
#:
#: 120s against a measured normal pass of 25-40s, and well inside the 5-minute
#: timer interval — the number that matters is not "how long is too long for a
#: sweep" but "how late may the next respawn be", and the answer is one interval.
#: The two measured blowouts were 27 and 36 MINUTES, so this is not a tight
#: squeeze on ordinary work; it is a ceiling on the pathological case.
_TEND_SWEEP_BUDGET_S = float(os.environ.get("SHANTY_TEND_SWEEP_BUDGET_S", "120"))

def _default_root() -> Path:
    """Where the store is when nobody said — the shared discovery chain.

    Resolved at CALL time, not at import: a test (and a shell) that sets the env
    after this module is imported must still be honoured, and a module-level
    default would freeze whatever the environment happened to be at import.
    """
    return resolve_root()[0]


def build_parser() -> argparse.ArgumentParser:
    """The full `st` parser. Exposed so tests/test_command_count.py can introspect
    the command surface and pin it to the docstring — the count is the thesis."""
    ap = argparse.ArgumentParser(prog="st")
    # --root, else $SHANTY_ROOT, else cwd/.shanty — the SAME precedence the Stop
    # hook uses, because the two must agree about where the store is. They did
    # NOT: the CLI resolved the root from the CURRENT WORKING DIRECTORY only,
    # while stop_event honoured the env AND its comment claimed "same default as
    # the CLI". An agent's cwd is its own workspace, which has no .shanty, so
    # every `st` call from an agent pane looked in a directory that does not
    # exist — and anything shelling out to `st` without --root (the status bar
    # segments) rendered EMPTY rather than erroring. Measured: the segments
    # produced nothing from any cwd except the checkout itself.
    # version + the deployed git SHA: __version__ is static, so
    # only the SHA distinguishes a fresh install from a stale one.
    from . import __version__, deployed_sha

    ap.add_argument(
        "--version",
        action="version",
        version=f"st {__version__} ({deployed_sha()})",
    )
    # DEFAULT None, resolved in main() — not computed here. Two commands need
    # different answers for "nobody said": every read/launch command DISCOVERS a
    # store (walk up, then the box's pointer), while `init` must answer cwd/.shanty
    # and never adopt a deployment it merely found. A default baked in at parse
    # time cannot tell "unset" from "set to the discovered value", so the choice
    # would be unmakeable.
    ap.add_argument("--root", type=Path, default=None,
                    help="the store. Unset: $SHANTY_ROOT, else a .shanty found by "
                         "walking up from here, else the box's deployment pointer "
                         "(~/.config/shantytown/root), else ./.shanty")
    ap.add_argument("--backend", choices=["files", "beads", "br", "forgejo"], default=None,
                    help="tracker backend (identity is always files). #3. "
                         "Unset means the deployment's SHANTY_BACKEND "
                         "([env] in shantytown.toml, then env), else per-command "
                         "default: files everywhere, EXCEPT `mail -d`, which "
                         "defaults to beads because a must-survive message "
                         "belongs in the shared store (dearing, qdal.2). Pass "
                         "--backend files to force local.")
    ap.add_argument("--repo", default=None,
                    help="store directory for --backend beads/br (unset: deployment's "
                         "SHANTY_BEADS_REPO/SHANTY_BR_REPO, else the .beads walk-up)")
    ap.add_argument("--registry", choices=["files", "quipu", "toml"], default="files",
                    help="identity backend: files (generated cards, the default), "
                         "quipu (the graph), or toml ([crew.<name>] in "
                         "<root>/shantytown.toml — hand-authored, read-only, for a "
                         "deployment that wants no ontology).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    an = sub.add_parser("anchor", help="who am I, what's on my plate")
    an.add_argument("me", nargs="?", help="defaults to $SHANTY_AGENT")
    # MACHINE-READABLE (aegis status bar). Each prints ONE value and nothing else
    # — no banner, no label, no prose — because the consumer is a Go program
    # rendering a segment, and "empty" has to mean "nothing to show".
    mr = an.add_mutually_exclusive_group()
    mr.add_argument("--short", action="store_true",
                    help="print ONLY the plate item's id (empty if the plate is empty)")
    mr.add_argument("--events", action="store_true",
                    help="print ONLY the number of UNDELIVERED stop events for me. "
                         "A READ: it never marks anything delivered (see events.py)")
    mr.add_argument("--harness", action="store_true",
                    help="print ONLY this agent's harness name (e.g. claude)")

    go = sub.add_parser("go", help="dispatch an item to an agent")
    go.add_argument("item")
    go.add_argument("agent")
    note = go.add_mutually_exclusive_group()
    note.add_argument("--note", default=None,
                      help="a caveat delivered IN the same payload as the "
                           "dispatch — it rides the triage gate with the work, "
                           "so it cannot arrive after the worker has acted. "
                           "Flattened to one line (the transport submits on "
                           "newline).")
    go.add_argument("--quipu-node", action="append", default=[], metavar="NAME",
                    help="quipu node(s) relevant to this bead (repeatable). Rides "
                         "the dispatch payload through the same triage gate as the "
                         "work, so the receiving agent starts from what the graph "
                         "already knows instead of re-deriving it")
    go.add_argument("--no-graph-context", default="", metavar="REASON",
                    help="dispatch with NO graph context, and say why (e.g. "
                         "'nothing modelled for this yet'). One of this or "
                         "--quipu-node is required: a dispatch that cites "
                         "neither is refused. The reason is never validated — "
                         "it is recorded, so the SHAPE of the exemptions is "
                         "measurable. A requirement that blocks real work gets "
                         "removed, so the escape hatch costs one flag.")
    note.add_argument("--note-file", type=Path, default=None,
                      help="read the note from a file (or - for stdin). Use this "
                           "for anything long or containing quotes/backticks — "
                           "shell expansion in a --note string is a real "
                           "footgun.")
    go.add_argument("-n", "--dry-run", action="store_true")
    go.add_argument("--reassign", action="store_true",
                    help="take an item another agent already holds. Without this, "
                         "dispatching an assigned item REFUSES rather than silently "
                         "stealing it.")
    go.add_argument("--worktree", metavar="REPO", default=None,
                    help="the work touches a SHARED project repo (a path, or a "
                         "bare name under $GT_ROOT): provision this agent an "
                         "ISOLATED worktree off it and deliver its path in the "
                         "dispatch, so two agents on the same repo never share an "
                         "index/HEAD. Refuses if the worktree cannot be made — "
                         "dispatching shared-repo work with no isolation is the "
                         "clobber bug, not a fallback.")

    # The whole hand-back, in one verified write (aegis-ap4gm fix #1): the
    # documented `bd update -a ""` clears the assignee and LEAVES the status at
    # in_progress, dropping the item off every feed mechanism at once.
    rp = sub.add_parser(
        "repool",
        help="hand an item back to the pool: status -> open AND assignee "
             "cleared, in one verified write. Clearing the assignee alone "
             "leaves the status at in_progress, which drops the item out of "
             "`br ready`, every haul, and every plate.")
    rp.add_argument("item")
    rp.add_argument("-n", "--dry-run", action="store_true")

    df = sub.add_parser(
        "defer",
        help="park an item with a required blocker kind and durable reason")
    df.add_argument("item")
    df.add_argument("kind", choices=tuple(BLOCKER_KIND_LABELS),
                    help="bead|human|access|external are blockers; parked means "
                         "deliberately set aside with no blocker")
    reason = df.add_mutually_exclusive_group(required=True)
    reason.add_argument("--reason", help="short reason; prefer --reason-file for prose")
    reason.add_argument("--reason-file", type=Path,
                        help="read the reason from a file, or - for stdin")
    df.add_argument("-n", "--dry-run", action="store_true")

    cr = sub.add_parser("crew", help="who exists, what state, what role")
    cr.add_argument("--count", action="store_true",
                    help="print ONLY `busy/total` — the same verdict the table "
                         "renders, for a status bar. Agents whose busy/idle state "
                         "is unknown are in NEITHER number")
    cr.add_argument("--governor", action="store_true",
                    help="print ONLY the capacity verdict, machine-readable, for a "
                         "status bar: `ok <five_hour> <seven_day> [tier label]`, or "
                         "the bare word `lost` / `off`. Both budgets, because they "
                         "exhaust independently")
    cr.add_argument("--trees", action="store_true",
                    help="also measure each agent's WORKTREE off every shared "
                         "repo, not just its workspace clone. Costs a few git "
                         "calls per agent per repo — this is the whole staleness "
                         "sweep in one command, instead of a hand-rolled loop "
                         "across directories")
    cr.add_argument("--check-alert-keepers", metavar="RULE", type=Path,
                    nargs="+",
                    help="fail when a Prometheus alert rule has no keeper, names "
                         "no roster card, or names a card that cannot work "
                         "unattended. A stopped but launchable keeper is silent.")

    # aegis-fagvi: the roles/role footgun (plural-vs-singular) is consolidated
    # into ONE noun with verbs: `st roles {show|set|sync}`. The old `role` and
    # `project` top-level commands stay as ALIASES below so nothing breaks.
    rl = sub.add_parser("roles", help="the role hierarchy: show | set | sync")
    # `roles --check` / bare `roles` == `roles show [--check]` (back-compat).
    rl.add_argument("--check", action="store_true")
    rl_sub = rl.add_subparsers(dest="roles_sub", required=False)
    rl_show = rl_sub.add_parser("show", help="the hierarchy, and whether it's real")
    # SUPPRESS default so `roles show` (no flag) keeps the parent's --check value.
    rl_show.add_argument("--check", action="store_true", default=argparse.SUPPRESS)
    rl_set = rl_sub.add_parser("set", help="set <agent> <role> [--reports a,b]")
    rl_set.add_argument("agent")
    # NO `choices=` (GitHub #37). argparse would reject a deployment-declared role
    # before any code could look it up, which would make [roles.<name>] decorative:
    # the file would parse, the role would exist, and the command to use it would
    # refuse with "invalid choice". The validation lives in tier.plan_role_set,
    # against the deployment's catalog, and names the roles that DO exist.
    rl_set.add_argument("role", metavar="ROLE",
                        help="worker | lead | administrator, or any role this "
                             "deployment declares under [roles.<name>]")
    rl_set.add_argument("--reports", default="", help="comma-separated reports for a lead/administrator")
    rl_set.add_argument("-n", "--dry-run", action="store_true")
    # aegis-ftmfn: THE BAND HAD NO VERB. `roles set` writes the TREE POSITION, so
    # `st roles set billy normal` is refused as a depth violation and the only way
    # to band a card was to hand-edit its `roles` array. Three of twenty were
    # missed that way — not decided differently, just never written down.
    rl_band = rl_sub.add_parser(
        "band", help="band <agent> <first|normal|support|last> — the SURVIVAL band")
    rl_band.add_argument("agent")
    # No `choices=` here either, for the reverse of rl_set's reason: the bands ARE
    # a closed vocabulary (traits.SURVIVAL_BANDS), but argparse's rejection would
    # print the four names without saying what they mean or which roles declare
    # them. _cmd_band refuses with the ordering and the deployment's own roles.
    rl_band.add_argument("band", metavar="BAND",
                         help="first (shed first) | normal | support | last "
                              "(shed last). The ORDER is the safety property — "
                              "these are names, never numbers.")
    rl_band.add_argument("--via", metavar="ROLE", default=None,
                         help="which declared role to carry the band, when this "
                              "deployment declares more than one")
    rl_band.add_argument("-n", "--dry-run", action="store_true")
    rl_sync = rl_sub.add_parser("sync", help="materialize the crew cards FROM a source")
    rl_sync.add_argument("-n", "--dry-run", action="store_true", help="show the diff, write nothing")
    rl_sync.add_argument("--force", action="store_true", help="sync even if it restructures LIVE agents")
    # aegis-ftmfn: a SECOND consent, deliberately not folded into --force. See
    # _cmd_project — the two refusals answer different questions, and the measured
    # incident is precisely an operator who would have answered only the first.
    rl_sync.add_argument("--allow-breakage", action="store_true",
                         help="sync even if it would NEWLY break a card's attachment "
                              "to the tier (e.g. manufacture an ORPHAN)")
    # aegis-t4eve: the tier comes FROM a source, and sync says which one answered.
    # Omitted => ontology-first, file-fallback. Named => never silently substituted.
    rl_sync.add_argument("--from", dest="from_source", default=None,
                         metavar="{quipu|file:<path>}",
                         help="where the hierarchy comes from: 'quipu' or "
                              "'file:<path.ttl|yaml|json>'. Default: quipu, "
                              "falling back to a hierarchy file only if the "
                              "graph cannot be read (and it tells you)")

    # The `role set ...` alias lived here and is GONE (deprecated 2026-07-24,
    # removed after the one-week window). `st roles set` is the spelling. Deletion
    # is what lands the count drop: consolidating and keeping an alias held the
    # surface at 19, because an alias IS a top-level command to argparse and to
    # anyone reading `st --help`.

    nw = sub.add_parser("new", help="create an agent from a card")
    nw.add_argument("agent")
    nw.add_argument("-n", "--dry-run", action="store_true")

    it = sub.add_parser("init",
                        help="scaffold a NEW deployment: asks a few questions, "
                             "then writes the store, the crew cards, their hooks "
                             "and shantytown.toml")
    it.add_argument("--admin", default=None,
                    help="the administrator's name (skips that question)")
    it.add_argument("--crew", default=None,
                    help="comma-separated worker names (skips that question)")
    it.add_argument("--workspaces", default=None, metavar="DIR",
                    help="parent directory for agent workspaces — each agent gets "
                         "DIR/<name>. Omitted: agents launch in the current dir")
    it.add_argument("--mode", default=None, choices=["lite", "heavy"],
                    help="the startup mode to write into the config (default lite)")
    it.add_argument("--hibernate", action="store_true",
                    help="let the administrator go quiet when there is nothing "
                         "to dispatch (default: off)")
    it.add_argument("-y", "--yes", action="store_true",
                    help="ask NOTHING: take the flags and the defaults. Required "
                         "when stdin is not a terminal, so a scripted init can "
                         "never block on a prompt")
    it.add_argument("--force", action="store_true",
                    help="scaffold into a store that already has crew cards or a "
                         "config (never overwrites a card; only fills gaps)")
    it.add_argument("-n", "--dry-run", action="store_true",
                    help="ask the questions, show every path it would write, "
                         "write nothing")

    sr = sub.add_parser("start",
                        help="bring the town UP by MODE: lite (the administrator "
                             "alone) or heavy (every card). Idempotent; never attaches")
    sr.add_argument("agent", nargs="*",
                    help="start exactly these agents (a card name each). The "
                         "mode's crew when omitted.")
    sr.add_argument("--mode", default=None,
                    help="which mode's crew to bring up. Unset: startup.mode from "
                         "<root>/shantytown.toml, else `lite`. Built-in modes are "
                         "lite (administrator only) and heavy (every non-retired "
                         "card); a config may define more.")
    sr.add_argument("-n", "--dry-run", action="store_true",
                    help="say who WOULD start, and who is already up. Launches "
                         "nothing, clones nothing.")

    st = sub.add_parser("stop", help="stop it")
    st.add_argument("agent")
    st.add_argument("-n", "--dry-run", action="store_true")
    st.add_argument("--reason", default="", metavar="TEXT",
                    help="why, recorded with the stop (GitHub #29). A deliberate "
                         "stop is INTENT, not a fault: `st crew` and the "
                         "administrator's drain report it as such instead of "
                         "demanding a re-dispatch. It also removes the launch "
                         "stamp, so `st tend` will NOT respawn it. Use `st new "
                         "<agent>` to bring it back. `st tend --retire` is the "
                         "stronger card-level state that also makes `st start` "
                         "skip it.")

    wn = sub.add_parser(
        "window", help="transactional fleet-maintenance drain / clear / restore ledger")
    wn_sub = wn.add_subparsers(dest="window_action", required=True)
    wn_plan = wn_sub.add_parser("plan", help="snapshot the fleet and acquire one window ID")
    wn_plan.add_argument("id")
    wn_plan.add_argument("--target-version", default=None, metavar="SHA",
                         help="refuse before drain when this version is already installed")
    wn_plan.add_argument("-n", "--dry-run", action="store_true",
                         help="print the manifest without acquiring the window ID")
    for action, help_text in (
        ("drain", "activate the relaunch lease and pause supervision"),
        ("clear", "fail closed unless every recorded worker/writer is gone"),
        ("release", "restore the exact snapshot after CLEAR"),
        ("abort", "roll back to the exact snapshot before CLEAR")):
        p = wn_sub.add_parser(action, help=help_text)
        p.add_argument("id")
        p.add_argument("-n", "--dry-run", action="store_true",
                       help="verify and describe the transition; change nothing")

    ss = sub.add_parser("stats", help="what the crew actually did: files, "
                                      "skills, tokens, activity (local store)")
    ss.add_argument("agent", nargs="?",
                    help="one agent's numbers; the whole crew if omitted")
    ss.add_argument("--files", action="store_true",
                    help="list the files an agent touched (needs agent)")
    ss.add_argument("--since", type=float, default=24.0, metavar="HOURS",
                    help="window in hours (default 24)")
    # NOT its own verb. The surface count is this repo's thesis and a new slot is
    # earned by a CONSEQUENCE, not by a report: this reads a ledger and mutates
    # nothing, which is exactly the case the `tend`/`input` arguments say belongs
    # behind a flag on an existing read.
    ss.add_argument("--graph", action="store_true",
                    help="graph-context adoption instead of token/file numbers: "
                         "what share of dispatches carried a quipu node, what "
                         "share stated a reason for having none, and which "
                         "agents have never cited one")
    ss.add_argument("--include-dry-run", action="store_true",
                    help="with --graph: count --dry-run previews too. Off by "
                         "default — a preview handed work to nobody, and counting "
                         "it inflates the denominator with dispatches that never "
                         "happened")
    ss.add_argument("--json", action="store_true",
                    help="with --graph: machine-readable output")

    lg = sub.add_parser("log", help="what happened")
    lg.add_argument("agent", nargs="?")

    ib = sub.add_parser("inbox",
                        help="put a message in an agent's inbox (send-keys; -d persists), "
                             "or read your own")
    ib.add_argument("agent", nargs="?",
                    help="the recipient when sending; whose inbox when reading "
                         "(defaults to $SHANTY_AGENT)")
    ib.add_argument("message", nargs="*")
    ib.add_argument("-d", "--durable", action="store_true",
                    help="must-survive: deliver to the recipient's INBOX (a bead "
                         "on the aegis store with --backend beads), then "
                         "best-effort live send. Default is ephemeral send-keys.")
    inbox_read_mode = ib.add_mutually_exclusive_group()
    inbox_read_mode.add_argument(
        "--count", action="store_true",
        help="print ONLY the number of unread messages. A READ: it marks nothing read")
    inbox_read_mode.add_argument(
        "--read", action="store_true",
        help="ACK: mark my unread messages read. The explicit act — listing and "
             "counting never do this")
    inbox_read_mode.add_argument(
        "--read-id", action="append", default=[], metavar="ID",
        help="ACK only this unread message ID (repeatable). Refuses the whole "
             "request if any ID is not unread for the recipient")
    ib.add_argument("-n", "--dry-run", action="store_true")

    tk = sub.add_parser("task", help="create a work item")
    tk.add_argument("title", nargs="+")
    tk.add_argument("-a", "--assignee")
    tk.add_argument("-n", "--dry-run", action="store_true")

    dm = sub.add_parser("dream", help="inspect or run one bounded spare-capacity reflection cycle")
    dm.add_argument("--run", action="store_true",
                    help="run one cycle now (still requires idle work queue and measured headroom)")
    dm.add_argument("-n", "--dry-run", action="store_true",
                    help="show the cycle that would be created; write nothing")

    cx = sub.add_parser("context", help="what code should I be looking at?")
    cx.add_argument("query", nargs="+")
    cx.add_argument("-b", "--budget", type=int, default=5)
    cx.add_argument("--repo", help="restrict to one indexed repo")
    cx.add_argument("--mode", default="hybrid", choices=["hybrid", "semantic", "keyword"])
    cx.add_argument("--none", action="store_true",
                    help="use the none-adapter (the leak test: harness works without bobbin)")

    dr = sub.add_parser("doctor", help="what tools are installed, what's stale, what's missing")
    dr.add_argument("tool", nargs="?", help="check one tool; all if omitted")
    dr.add_argument("--install", action="store_true",
                    help="install/upgrade the missing or stale tools (refuses if a toolchain is absent)")
    dr.add_argument("-n", "--dry-run", action="store_true",
                    help="with --install: show the plan, run nothing")
    dr.add_argument("--no-latest", action="store_true",
                    help="skip the release check (offline/fast) — detect local state only")

    # The `project` alias lived here and is GONE — see the `role` note above.
    # `st roles sync` is the spelling.

    td = sub.add_parser("tend", help="supervise the crew: respawn what DIED, "
                                     "never what was RETIRED")
    td.add_argument("--install", action="store_true",
                    help="install the systemd --user timer that runs a pass")
    td.add_argument("--uninstall", action="store_true",
                    help="remove the timer (only if st tend wrote it)")
    td.add_argument("--status", action="store_true",
                    help="is it installed, and when did a pass last run?")
    td.add_argument("--retire", metavar="AGENT",
                    help="mark an agent DELIBERATELY stopped — tend will never "
                         "respawn it (durable: it lives on the card)")
    td.add_argument("--unretire", metavar="AGENT",
                    help="undo --retire; the agent is tended again. REFUSES if "
                         "the card could not be launched into its own tree, or "
                         "would launch in MANUAL MODE — un-retiring arms it for "
                         "unattended respawn, and this is the last moment "
                         "anyone is watching")
    td.add_argument("--force", action="store_true",
                    help="with --unretire: arm the card anyway, past a "
                         "launchability refusal. The reason is still printed")
    td.add_argument("--interval", default="5min",
                    help="with --install: how often a pass runs (default 5min)")
    td.add_argument("--target", type=int, metavar="N",
                    help="respawn only toward N LIVE agents instead of the whole "
                         "roster. Scale-UP on loss and nothing else: it never "
                         "stops a surplus, because choosing who a fleet should "
                         "consist of is judgment and belongs to the "
                         "administrator, not to a supervisor. Fills the tier from "
                         "the root down, and reports the agents it held back.")
    td.add_argument("--loop", type=int, metavar="SECS",
                    help="run a pass every SECS forever — the blocked-worker "
                         "heartbeat. A blocked worker is pushed to "
                         "its coordinator within one interval, on its own.")
    td.add_argument("--reauth", action="store_true",
                    help="relaunch every AUTH-DEAD agent (login expired) in one "
                         "command — run it AFTER the operator re-logs in; a "
                         "relaunch re-reads the refreshed shared credential")
    td.add_argument("-n", "--dry-run", action="store_true",
                    help="say what would be respawned; touch NOTHING")

    at = sub.add_parser("attach", help="attach to a crew member by name — "
                                       "STARTING them first if they are down "
                                       "(socket + pane resolved for you)")
    at.add_argument("agent", nargs="?",
                    help="whose pane; defaults to the administrator (the coordinator)")
    at.add_argument("-r", "--read-only", action="store_true",
                    help="observe only — no keystroke can land in their work")
    at.add_argument("--no-start", action="store_true",
                    help="do NOT launch a down agent: refuse instead. The pure "
                         "observer's flag — for a script that wants to attach to "
                         "whatever is already running and must never create a "
                         "session as a side effect.")

    ib2 = sub.add_parser("input",
                         help="what is in an agent's input box: EMPTY | TYPED | "
                              "GHOST — and clear or dismiss it. NEVER submits.")
    ib2.add_argument("agent", help="whose input box")
    ib2_g = ib2.add_mutually_exclusive_group()
    ib2_g.add_argument("--show", action="store_true",
                       help="classify the box and print the SGR evidence (default)")
    ib2_g.add_argument("--clear", action="store_true",
                       help="clear TYPED text. REFUSES on a ghost-only box — a "
                            "suggestion's buffer is already empty, and "
                            "'clearing' one teaches the wrong model")
    ib2_g.add_argument("--dismiss", action="store_true",
                       help="dismiss the suggestion (Escape). Also the way to "
                            "CANCEL a tool call an agent is stopped on — Escape "
                            "is that key too, so there is no `st cancel`")

    ak = sub.add_parser("ask",
                        help="print the QUESTION an agent is blocked on: the "
                             "prompt, the command being approved, and the "
                             "numbered options verbatim. Read-only.")
    ak.add_argument("agent", help="who is blocked")

    aw = sub.add_parser("answer",
                        help="select option N on an agent's blocking picker, by "
                             "NUMBER. Refuses on a pane that is not on one.")
    aw.add_argument("agent", help="who to answer")
    aw.add_argument("n", type=int, metavar="N",
                    help="the option number, as `st ask` printed it")

    db = sub.add_parser("dashboard", help="a live, self-refreshing view of an "
                                          "admin's tier (roster/state/work)")
    db.add_argument("admin", nargs="?",
                    help="whose tier; defaults to the administrator")
    db.add_argument("--interval", type=int, default=5, metavar="SECS",
                    help="refresh every SECS (default 5)")
    db.add_argument("--once", action="store_true",
                    help="render one snapshot and exit (no refresh loop)")

    sb = sub.add_parser("subscribe",
                        help="watch quipu entity events; route assigned workflows to the admin")
    sb.add_argument("--once", action="store_true",
                    help="poll one batch and exit (default: loop)")
    sb.add_argument("--interval", type=float, default=10.0,
                    help="poll interval in seconds when looping")
    sb.add_argument("--server", default=None,
                    help="quipu server (default $QUIPU_SERVER)")

    hp = sub.add_parser("help",
                        help="rationale pages for the recurring instructions "
                             "(handoff/cycle, haul, inbox)")
    hp.add_argument("topic", nargs="?", default="",
                    help="handoff | cycle | haul | inbox; omit to list")

    cy = sub.add_parser("cycle",
                        help="clear an agent's context WITHOUT destroying its "
                             "runtime: checkpoint -> stop -> relaunch -> "
                             "re-dispatch. `/clear` drops bypass; this does not")
    cy.add_argument("agent", nargs="?",
                    help="who to cycle; with --self, defaults to $SHANTY_AGENT")
    cy.add_argument("-r", "--reason", default="",
                    help="THE CHECKPOINT, and it is required: what you are "
                         "mid-task on, decisions already made, the exact next "
                         "step. Unwritten context is the only thing a cycle "
                         "destroys")
    cy.add_argument("--checkpoint-bead", default="",
                    help="durable checkpoint bead id; required for administrators and read back before a self-cycle request")
    # --checkpoint-file is Stiwi's "an st command that does the needful"
    # (aegis-x6yoq). Before it, a handoff was TWO hand-composed commands — a
    # `bd comment ... --file` and then an `st cycle --self -r '...'` repeating the
    # gist — and that composition is exactly where agents fumbled: they wrote the
    # notes, then had to invent a one-line summary under context pressure, having
    # just been told their judgement is degrading. Now the file IS the checkpoint:
    # it is posted to the bead and its first line becomes the reason.
    cy.add_argument("--checkpoint-file", default="",
                    help="a file holding your checkpoint notes. Posted as a "
                         "comment on the checkpoint/anchor bead, and its first "
                         "line becomes the reason. Use INSTEAD of -r: one "
                         "command, no hand-composed summary")
    # Stiwi, same directive: "would be nice to specify a quipu node(s) for the
    # handoff cycle". The point is that the resuming session starts from the graph
    # rather than re-deriving context it just shed — query-first, mechanized at the
    # exact moment context is dropped.
    cy.add_argument("--quipu-node", action="append", default=[], metavar="NAME",
                    help="quipu node relevant to the in-flight work (repeatable). "
                         "Recorded on the request and named in the resume dispatch "
                         "so the fresh session queries the graph before re-deriving")
    cy.add_argument("--self", dest="self_", action="store_true",
                    help="REQUEST your own cycle. An agent cannot cycle itself "
                         "in-process (the stop kills the session doing the "
                         "stopping), so this records a request `st tend` honours")
    cy.add_argument("--allow-loss", action="store_true",
                    help="cycle even though a tree holds uncommitted or unpushed "
                         "work. Named separately from any --force ON PURPOSE, so "
                         "reaching past another refusal cannot disarm this one")
    # `--dry-run` is on every command that writes, from commit one — and this one
    # kills a live session, so it is not optional here. It also has to run the
    # GUARD before printing "would", or the preview an operator reads to authorise
    # a cycle would be silent about the work it is about to strand.
    cy.add_argument("-n", "--dry-run", action="store_true")
    cy.add_argument("--no-graph-context", default="", metavar="REASON",
                    help="request the cycle with NO graph context, and say why. "
                         "One of this or --quipu-node is required on a cycle "
                         "carrying a checkpoint, for the same reason it is on "
                         "`st go`: the resume dispatch is where the next "
                         "session starts, and starting from nothing is the "
                         "habit this requirement exists to break.")

    wt = sub.add_parser("worktree",
                        help="provision (or gc) an agent's isolated worktree off "
                             "a SHARED project repo — so agents never share an "
                             "index/HEAD")
    wt.add_argument("repo",
                    help="the shared checkout: a path, or a bare name under "
                         "$GT_ROOT (~/gt), e.g. `quipu` -> ~/gt/quipu")
    wt.add_argument("agent", nargs="?",
                    help="whose worktree; defaults to $SHANTY_AGENT")
    wt.add_argument("--gc", action="store_true",
                    help="remove the worktree IFF unchanged — never discards "
                         "uncommitted or unpushed work")

    ph = sub.add_parser("push",
                        help="push your worktree branch to EVERY remote — the "
                             "repo has two live peers and pushing one forks it")
    ph.add_argument("repo",
                    help="the shared checkout: a path, or a bare name under "
                         "$GT_ROOT (~/gt), e.g. `shantytown` -> ~/gt/shantytown")
    ph.add_argument("agent", nargs="?",
                    help="whose worktree; defaults to $SHANTY_AGENT")
    ph.add_argument("--branch", default="main",
                    help="destination branch on each remote (default: main)")

    hi = sub.add_parser("history",
                        help="list an agent's CAPTURED transcripts — the durable "
                             "archive of sessions incl. reasoning")
    hi.add_argument("agent", nargs="?",
                    help="whose sessions; omit to list every agent")
    hi.add_argument("--all", action="store_true",
                    help="list every session, not just the 20 most recent")

    return ap


def _parse_args(argv: list[str] | None):
    """parse_args, but tolerant of a flag that appears BEFORE a variadic positional
    in a subcommand — e.g. `inbox ian -n hi`. Plain argparse strands the trailing
    positional (`unrecognized arguments: hi`): a `nargs="*"` positional, once
    matched, does not reopen after an optional. argparse's parse_intermixed_args
    handles exactly this, but it does NOT support subparsers — so detect the
    stranded case (parse_known_args leaves extras) and re-run the CHOSEN subparser
    with intermixed parsing. The fast path (no extras) is unchanged.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    ns, extra = parser.parse_known_args(argv)
    if not extra:
        return ns
    subs = next((ac for ac in parser._actions
                 if isinstance(ac, argparse._SubParsersAction)), None)
    cmd = getattr(ns, "cmd", None)
    if subs is None or cmd not in subs.choices:
        return parser.parse_args(argv)          # not our case — let argparse report
    sub_argv = argv[argv.index(cmd) + 1:]
    # Re-parse the subcommand's args into a FRESH namespace (a populated one makes
    # parse_intermixed_args warn), then copy the subcommand values onto `ns`, which
    # already carries the globals + cmd from the top parse_known_args.
    fresh = subs.choices[cmd].parse_intermixed_args(sub_argv)
    for key, value in vars(fresh).items():
        setattr(ns, key, value)
    return ns



def _cmd_history(a) -> int:
    """List captured transcripts for an agent.

    The column that matters is SOURCE. Codex writes its rollouts under
    CODEX_HOME=/run/user/<uid>/... which is tmpfs, so a session whose source is
    GONE exists ONLY here — that row is the archive earning its keep. A session
    whose source is still present is merely backed up.
    """
    import os
    from pathlib import Path

    dest = Path(os.environ.get(
        "ST_HISTORY_DIR",
        Path.home() / "gt" / "shantytown" / ".shanty" / "history"))
    if not dest.is_dir():
        print(f"no transcript archive at {dest}")
        print("run scripts/st-history-capture.sh (or wait for the */30 timer)")
        return 1

    # Source paths come from the manifest, which is append-only: the LAST line
    # for a file is its most recent capture.
    sources: dict[str, tuple[str, str]] = {}
    man = dest / "manifest.tsv"
    if man.exists():
        for line in man.read_text(errors="replace").splitlines():
            f = line.split("\t")
            if len(f) >= 6:
                sources[f[3]] = (f[0], f[5])

    agents = [a.agent] if a.agent else sorted(
        d.name for d in dest.iterdir() if d.is_dir())
    if not agents:
        print(f"archive at {dest} holds no agents yet")
        return 1

    total = rescued = 0
    for name in agents:
        adir = dest / name
        if not adir.is_dir():
            print(f"no captured sessions for {name!r} under {dest}")
            return 1
        files = sorted(adir.glob("*.jsonl"),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            continue
        shown = files if a.all else files[:20]
        print(f"\n{name}  ({len(files)} session(s))")
        for f in shown:
            when, src = sources.get(f.name, ("?", ""))
            harness = "codex" if f.name.startswith("rollout-") else "claude"
            live = "source-gone" if src and not Path(src).exists() else "source-live"
            if live == "source-gone":
                rescued += 1
            print("  %-7s %-11s %7.1f MB  captured %s  %s"
                  % (harness, live, f.stat().st_size / 1048576, when, f.name[:44]))
        if not a.all and len(files) > 20:
            print(f"  ... {len(files) - 20} older (use --all)")
        total += len(files)

    print(f"\n{total} session(s) archived at {dest}")
    if rescued:
        print(f"{rescued} of them no longer exist at their source — "
              "those are recoverable ONLY from here")
    return 0

def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv)
    # RESOLVE THE STORE ONCE, here, so every handler downstream sees a real Path
    # and none of them re-derives one. `how` rides along so a surface that needs
    # to explain an empty or surprising store can say which leg answered.
    a.root, a.root_how = resolve_root(a.root, discover=(a.cmd != "init"))
    _warn_if_no_store(a)

    if a.cmd == "anchor":
        return _cmd_anchor(a)
    if a.cmd == "go":
        return _cmd_go(a)
    if a.cmd == "repool":
        return _cmd_repool(a)
    if a.cmd == "defer":
        return _cmd_defer(a)
    if a.cmd == "crew":
        return _cmd_crew(a)
    if a.cmd == "roles":
        # aegis-fagvi: one noun, three verbs. `set`/`sync` reuse the same
        # handlers as the `role`/`project` aliases so behaviour is identical.
        roles_sub = getattr(a, "roles_sub", None)
        if roles_sub == "set":
            return _cmd_role(a)
        if roles_sub == "sync":
            return _cmd_project(a)
        if roles_sub == "band":
            return _cmd_band(a)
        return _cmd_roles(a)  # bare `roles`, `roles --check`, or `roles show`
    if a.cmd == "inbox":
        return _cmd_inbox(a)
    if a.cmd == "task":
        return _cmd_task(a)
    if a.cmd == "context":
        return _cmd_context(a)
    if a.cmd == "doctor":
        return _cmd_doctor(a)
    if a.cmd == "stop":
        return _cmd_stop(a)
    if a.cmd == "log":
        return _cmd_log(a)
    if a.cmd == "stats":
        if a.graph:
            return _cmd_graph_adoption(a)
        from . import stats as stats_mod
        if a.files:
            if not a.agent:
                print("st stats --files needs an agent", file=sys.stderr)
                return 2
            return stats_mod.stats_files(a.root, a.agent, since_h=a.since)
        return stats_mod.stats_report(a.root, a.agent, since_h=a.since)
    if a.cmd == "new":
        return _cmd_new(a)
    if a.cmd == "init":
        return _cmd_init(a)
    if a.cmd == "start":
        return _cmd_start(a)
    if a.cmd == "window":
        return _cmd_window(a)
    if a.cmd == "tend":
        return _cmd_tend(a)
    if a.cmd == "dream":
        return _cmd_dream(a)
    if a.cmd == "attach":
        return _cmd_attach(a)
    if a.cmd == "input":
        return _cmd_input(a)
    if a.cmd == "ask":
        return _cmd_ask(a)
    if a.cmd == "answer":
        return _cmd_answer(a)
    if a.cmd == "dashboard":
        return _cmd_dashboard(a)
    if a.cmd == "subscribe":
        return _cmd_subscribe(a)
    if a.cmd == "help":
        from . import help_topics
        page = help_topics.render(a.topic) if a.topic else None
        if page:
            print(page)
            return OK
        if a.topic:
            print(f"  no such topic: {a.topic!r}", file=sys.stderr)
            print(help_topics.index(), file=sys.stderr)
            return REFUSED
        print(help_topics.index())
        return OK

    if a.cmd == "cycle":
        return _cmd_cycle(a)
    if a.cmd == "worktree":
        return _cmd_worktree(a)
    if a.cmd == "push":
        return _cmd_push(a)
    if a.cmd == "history":
        return _cmd_history(a)
    return _not_yet(a.cmd)


def _default_settings(root: Path, agents=()):
    """Resolve a card -> the settings file that wires its graph role's hooks.

    The file is EMITTED by `role set` / #6; #5 owns the launch seam,
    not the hook-file content. So this resolver READS: it returns the path if the
    role's settings file exists, else None -> compose refuses. That refusal IS the
    invariant working — no settings, no launch, never a settings-less fallback.
    """
    # A worker with direct reports is a stop-event destination even when its
    # card role deliberately remains worker.  It needs the lead hook profile
    # (drain) without a role/trait change that could affect governor tiers.
    receivers = {a.reports_to for a in agents if a.reports_to}

    def resolve(card):
        # PER-AGENT FIRST (GitHub #17). All workers sharing one file meant nothing
        # could differ per agent — so a card's own model, permissions or hooks had
        # nowhere to land. An agent file is used when it EXISTS; otherwise the
        # role file, which is what every card uses today. No file for either is
        # None, and compose REFUSES: no settings, no launch, never a fallback.
        #
        # THE NAMES ARE THE HARNESS'S (harness.settings_name). They were literals
        # here, which made this resolver quietly Claude-Code-only: a codex card
        # would have been handed a `.json` path for a program that reads
        # config.toml — a file that does not exist, so every codex launch would
        # refuse for a reason that had nothing to do with the card.
        program = harness_mod.for_card(card, root=root)
        profile = "lead" if card.role == "worker" and card.name in receivers else card.role
        for name in (program.agent_settings_name(card.name),
                     program.settings_name(profile)):
            p = Path(root) / "settings" / name
            if p.is_file():
                return str(p)
        return None
    return resolve


def _warn_if_no_store(a) -> bool:
    """Say NOTHING ANSWERED, once, before the command runs. Returns whether it did.

    THE MEASURED MISDIAGNOSIS (aegis-d94vb). CLAUDE.md tells every crew member to
    run `st anchor <you>` from their own workspace. There, st refused with:

        refused: no such agent: malcolm
          (looked in <workspace>/.shanty/crew/malcolm.json)

    which reads as a broken identity or a bad clone — so the natural recovery is to
    stop using st, one agent at a time. The actual cause is that NOTHING answered
    the where-is-the-store question: no --root, no $SHANTY_ROOT, no `.shanty` on the
    way up, and no pointer file. The agent's own name was never the problem.

    WHY THE WALK-UP CANNOT COVER THIS, and why the answer is the pointer: crew
    workspaces are SIBLINGS of the checkout, not children of it
    (~/gt/<rig>/crew/<agent> vs ~/gt/shantytown/.shanty). Walking up from a sibling
    tree never reaches the store, at any depth. The pointer exists for exactly this
    shape and `st init` writes it — a deployment that predates init has none, which
    is the state this fires in.

    A WARNING, NOT A REFUSAL, and before the command rather than after: commands
    that need no store still work, and the ones that do now fail with the cause
    already on screen instead of an identity error to be misread first.
    """
    from .deployment import BY_CWD, pointer_path
    if getattr(a, "cmd", None) == "init":
        return False                       # init is how a store comes to exist
    if a.root_how != BY_CWD or Path(a.root).is_dir():
        return False
    print(f"  ⚠ no store found. Nothing answered where it is: no --root, no "
          f"$SHANTY_ROOT, no .shanty walking up from here, and no pointer at "
          f"{pointer_path()}. Falling back to {a.root}, which does not exist — so "
          f"anything needing the crew will say 'no such agent: <name>', and the "
          f"name is not the problem. Fix with any one of: `st --root "
          f"<path>/.shanty ...`, `export SHANTY_ROOT=<path>/.shanty`, or write that "
          f"path into {pointer_path()} (one line). NOTE a crew workspace is a "
          f"SIBLING of the checkout, so the walk-up can never find it from there.",
          file=sys.stderr)
    return True


def _catalog(a):
    """The deployment's ROLE CATALOG (traits.py, GitHub #37) — the built-in three
    plus whatever `[roles.<name>]` declares, with `[precedence.<axis>]`'s ranks.

    load_or_default, not load: this feeds a REFUSAL path (`role set` validating a
    role), and a config typo must produce the config error at the top of a command,
    not a role that mysteriously stopped existing. An unreadable file therefore
    yields the built-in three, which is exactly what a deployment that declares
    nothing gets — a degradation to the previous behaviour, never to a wider one.
    """
    cfg, err = config.load_or_default(getattr(a, "root", None) or ".")
    if err:
        print(f"  ⚠ {err} — using the built-in roles", file=sys.stderr)
    return cfg.catalog()


def _stops(a) -> FilesStops:
    """The deliberate-stop store (stopped.py). Beside `launched/` because it is the
    same KIND of thing: per-launch runtime state, not identity."""
    return FilesStops(Path(a.root) / "stopped")


def _launched_now(a, card_name: str, settings_path=None) -> None:
    """ONE place that records 'this agent is now running'.

    Both halves must happen together and neither is allowed to be forgotten at a
    new launch site: stamp what it launched on, and DROP any deliberate-stop record
    (the stop is over — a record left behind would make its next real crash read as
    intentional, which is a fabricated decision and worse than no record).
    """
    if settings_path:
        _launches(a).record(card_name, settings_path)
    _stops(a).forget(card_name)


def _record_launch_unretirement(a, card) -> bool:
    """A sanctioned launch is an explicit re-arm; persist that transition.

    Retirement is durable precisely so an unattended supervisor cannot undo it.
    Conversely, a human/operator invoking the shared launch seam has explicitly
    asked for this card to run. Leaving ``retired=true`` after that launch makes
    Tender report a fake RESURRECTED fault forever and makes the card's shutdown
    state disagree with the process the same command just created.

    Called only AFTER runtime.start returned. Every refusal and dry-run therefore
    preserves retirement. Failure is loud and makes launch verification
    CANNOT_TELL; a live process plus an unchanged ledger must never be reported as
    a fully successful launch.
    """
    if not card.retired:
        return True
    actor, at = _actor(), _now_iso()
    try:
        reg = _registry(a)
        if not hasattr(reg, "set"):
            raise RuntimeError("crew registry is read-only")
        reg.set(replace(card, retired=False, retired_by=actor, retired_at=at))
    except Exception as e:  # noqa: BLE001 — report the split state, never hide it
        print(f"  could not tell: {card.name} was launched, but its RETIRED "
              f"ledger could not be cleared ({e}). The process is live while "
              f"the card still says stopped; tend will flag that split state.",
              file=sys.stderr)
        return False
    print(f"  {card.name}: sanctioned launch cleared RETIRED "
          f"(UN-RETIRED by {actor} at {at}).")
    return True


def _launches(a) -> FilesLaunches:
    """The launch-stamp store for this invocation. Beside events/."""
    return FilesLaunches(Path(a.root) / "launched")


# Not looked at. A down agent's settings verdict is not "current" and not
# "stale" — we did not ask, and rounding that to either is the bug nipg is about.
NOT_LIVE = "—"


def _settings_verdict(launches, name: str, live: bool) -> str:
    """THE definition of one agent's settings verdict: is it running the file we
    currently believe is deployed?

    Only a LIVE agent can be running stale settings. A down agent has no loaded
    settings to be stale and reads the current file when it next starts, so its
    verdict is NOT_LIVE — reporting on it would be noise that buries the real hits.

    `live` is passed IN rather than probed here on purpose: `crew` has already
    established it while building its table, and re-probing would double the tmux
    calls on the command an operator runs most.
    """
    return launches.verdict(name) if live else NOT_LIVE


def _reach_buckets(verdicts) -> tuple[list[str], list[str]]:
    """(stale, unknown) from an iterable of (name, verdict). Pure, and it is the
    ONLY place a verdict becomes a bucket.

    This exists because `crew` (which reports when asked) and `role set` (which
    reports when it CAUSES the drift) must never disagree. Two copies of this
    could, and the first symptom would be one surface calling an agent healthy
    while the other called it stale — the exact ambiguity aegis-nipg is about.
    That divergence was not hypothetical: this rule was written twice, once here
    and once inline in `_cmd_crew`, and unifying them is what this change is.
    """
    v = list(verdicts)
    return ([n for n, x in v if x == STALE],
            [n for n, x in v if x == UNKNOWN])


def _settings_reach(a, panes, agents) -> tuple[list[str], list[str]]:
    """(stale, unknown) among the LIVE agents — who is NOT on the current file.

    For callers that have NOT already computed liveness (`role set`). `crew`
    builds the same buckets from the verdicts it is already rendering, via
    _reach_buckets, so both surfaces share the verdict rule AND the bucket rule.
    """
    launches = _launches(a)
    return _reach_buckets(
        (ag.name,
         _settings_verdict(launches, ag.name,
                           bool(ag.pane and panes.exists(ag.pane))))
        for ag in sorted(agents, key=lambda x: x.name))


def _runtime(a, panes):
    """The runtime for this invocation.

    ONE RUNTIME, MANY HARNESSES, and the distinction is the aegis-85ox fix rather
    than an accident: WHICH PROGRAM an agent runs is the card's (harness.py, and
    ClaudeRuntime.compose asks the card's harness for the argv, the settings and
    the capability). What is left in the Runtime is the launcher seam itself —
    compose-or-refuse, deliver through Panes — plus the pane-reading predicates,
    which are still Claude Code's and are the one seam the codex work did not
    move (harness.py's header says why, and what it costs).
    """
    try:
        agents = _registry(a).all().exact()
    except Exception:
        agents = []
    return ClaudeRuntime(panes, _default_settings(a.root, agents), root=a.root)


def _observe_live(runtime, panes, session, card=None) -> bool:
    """Poll capture() until the runtime is OBSERVED live, or give up (-> 2).

    This proves the PROCESS came up — NOT that hooks fired. The hooks guarantee is
    enforced at COMPOSITION (the string provably carried --settings), not by pane
    inspection (arnold: that is GT's unanswerable 'did I get primed?'). A green
    verify here must never be read as 'hooks registered'."""
    answered = False
    chrome_answered = False
    for _ in range(_LIVE_ATTEMPTS):
        screen = panes.capture(session)
        if runtime.is_live(screen):
            return True
        # THE FOLDER-TRUST GATE (measured 2026-07-20). A fresh workspace blocks on
        # "Do you trust the files in this folder?" — before the ready UI, and NOT
        # bypassed by --dangerously-skip-permissions. Answer it ONCE, and say so:
        # the card already elected this workspace, so this re-affirms a decision
        # the operator made when they wrote the card; it does not make a new one.
        # Answering silently would be the wrong trade — the point is that a human
        # can see the launcher did it.
        if not answered and getattr(runtime, "trust_prompt", None) and \
                runtime.trust_prompt(screen):
            print(f"  first-run TRUST prompt in {session} — accepting the "
                  f"workspace the card already elected.", file=sys.stderr)
            # BARE BY DESIGN (aegis-5vxmz). This is an ANSWER to a prompt the
            # runtime is showing — a single keystroke's worth of text consumed by
            # a chooser, not a message anyone reads. Signing it would answer a
            # different question. Pinned in tests/test_attribution_inventory.py.
            panes.send(session, runtime.trust_answer())
            answered = True
        # THE CHROME CONSENT GATE (aegis-neffw), and it is the trust gate's twin.
        # Live-fire 2026-08-05: `claude --chrome` shows the folder-trust dialog,
        # then a SECOND screen consenting to the browser integration, and that one
        # blocks the ready UI too — is_live False, so `st new` returns
        # could-not-tell for an agent that is one keystroke from fine. That is the
        # aegis-84z1 0-path failure, reachable again the moment a card opts in.
        #
        # GATED ON card.chrome, deliberately. A card that did not ask for a browser
        # must never have one confirmed on its behalf; if that screen appears on a
        # non-chrome card it is a genuine surprise and `waiting_for_human` should
        # report it, not the launcher paper over it.
        elif (not chrome_answered and getattr(card, "chrome", False)
              and getattr(runtime, "chrome_prompt", None)
              and runtime.chrome_prompt(screen)):
            print(f"  first-run CHROME consent in {session} — accepting the "
                  f"browser integration this card already elected "
                  f"(chrome = true).", file=sys.stderr)
            panes.send(session, runtime.chrome_answer())
            chrome_answered = True
        if _LIVE_DELAY:
            time.sleep(_LIVE_DELAY)
    return False


def _session_for(card) -> str:
    """The session an agent launches into.

    Fallback name when the card names no pane. Deliberately prefixed `st-`: a
    session st creates must never collide with one somebody else's tooling already
    launched under a name we'd also pick.

    ONE resolver because THREE surfaces now need the same answer — `new` launches
    into it, `attach` has to attach to the session it just asked for, and
    `bootstrap` reports it. Two of those computing it separately is a bug where
    st launches an agent and then cannot find it.
    """
    return card.pane or f"st-{card.name}"


def _cmd_new(a) -> int:
    """new <agent> — bring up a HOOKED agent session (#5).

    The single-agent PRIMITIVE, and it REFUSES a live session (the clobber guard
    below) — `st new` on a running agent is a mistake, not an idempotent no-op.
    The boot command that wants "make it so, whatever is currently up" is
    `st start`; the difference is written up in bootstrap.py.
    """
    if (rc := _window_launch_gate(a)) is not None:
        return rc
    panes = _panes(a)
    try:
        card = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    runtime = _runtime(a, panes)
    return _launch(a, card, panes, runtime, dry_run=a.dry_run)


def _launch(a, card, panes, runtime, *, dry_run: bool = False,
            window_restore: bool = False) -> int:
    """LAUNCH ONE AGENT. The whole seam: refuse-first, then workspace, then kit,
    then session, then verify. Returns 0 (up + hooks verified) / 1 (refused) /
    2 (launched but not verified).

    new_session (empty pane) -> Runtime.start (compose w/ --settings, send) ->
    verify PROCESS live -> 0/1/2. The order is deliberate: everything that can
    REFUSE (unknown agent, capability, settings) runs BEFORE any tmux mutation, so
    a refusal creates nothing (arnold: 'write nothing, launch nothing').

    ONE launcher, shared by `new`, `start` and `attach`, so none of them can
    acquire a cheaper version of it: the pre-flight order above, the workspace
    guard, the equipped-or-not-created refusal and the hooks verification are each
    load-bearing, and a second launcher would have to re-earn all four.
    """
    if not window_restore and (rc := _window_launch_gate(a)) is not None:
        return rc
    # A dead app-server can outlive its pane behind Codex's per-card daemon and
    # make every subsequent launch time out. Repair only the daemon whose
    # /proc environment names this card; argv is never an ownership signal.
    if harness_mod.name_for(card, root=a.root) == "codex" and not dry_run:
        from . import codex_daemon
        fixed = codex_daemon.repair(card.name)
        if fixed.blocked:
            print(f"  repaired {codex_daemon.FLAG} for {card.name}: {fixed.reason()}",
                  file=sys.stderr)
    session = _session_for(card)
    # PRE-FLIGHT: compose refuses capability/settings/unknown-harness BEFORE we
    # touch tmux. UnknownHarness is a REFUSAL by design (harness.py) but was not in
    # this except, so a card naming a harness we cannot host exited with a
    # traceback instead of the `refused:` exit-1 path every other refusal uses
    # (aegis-85ox). It belongs here with the others: same seam, same outcome.
    try:
        harness_mod.require_role_harness(card, root=a.root)
        launch = runtime.compose(card)
    except (CapabilityError, SettingsError, harness_mod.UnknownHarness,
            harness_mod.Unsupported) as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if dry_run:
        print(f"  would launch in {session}: {launch}")
        return OK
    # WORKSPACE: the launch string `cd`s into card.workspace, so the
    # directory has to BE there. Ensure it (clone if absent, leave alone if
    # present) or REFUSE — before any tmux mutation, so a refusal still creates
    # nothing. Deliberately AFTER dry-run: dry-run must not clone.
    try:
        ensure_workspace(card)
    except WorkspaceError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    # KEEP CURRENT ON RELAUNCH (aegis-4zld): a fresh session must start on a
    # current tree — launch is the other safe pull moment (nothing is running
    # in the workspace yet). ff-only + kit-preserving; a refused pull is LOUD
    # and never blocks the launch (stale-but-working beats no agent).
    if card.workspace:
        if err := _refresh_clone(card.workspace):
            print(f"  ⚠ workspace not brought current (ff-only pull refused: "
                  f"{err.splitlines()[0]}) — launching on the existing tree.",
                  file=sys.stderr)
    # EQUIPPED OR NOT CREATED. A workspace is not a provisioned agent: a fresh
    # clone has no .mcp.json (it is uncommitted BY DESIGN — it carries a bearer
    # token), so an agent launched from one has no code search, no graph and no
    # ops tools, and looks identical to a healthy one on every surface. Five
    # agents worked P1 beads for a night that way. Refuse instead.
    try:
        servers = provision_ws(card, Path(a.root))
    except ProvisionError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if servers:
        # Say what it CAN reach, parsed back out of the file we wrote — "the file
        # is there" was true for every broken render.
        print(f"  provisioned {len(servers)} MCP server(s): {', '.join(servers)}")
    elif card.workspace:
        # No template = this store defines no kit. Say it, every time: silence
        # here is indistinguishable from the bug (an agent that launches with no
        # tools and looks fine), and a deleted template would restore it.
        print(f"  note: no provisioning template at {prov_mod.provision_dir(a.root)}"
              f" — launching {card.name} with NO MCP kit.")
    if card.workspace:
        # Skills, same claim shape as the servers: names read back out of the
        # links we just made, not "the directory is there". A crew that shipped
        # 22 correct skills to a runtime loading none had every other signal green.
        linked = prov_mod.skills_linked(card.workspace)
        if linked:
            print(f"  linked {len(linked)} skill(s): {', '.join(linked)}")
    # Clobber guard: never replace a live agent (RAISES if the session exists).
    # cwd=card.workspace so the SESSION starts in the agent's own directory, not
    # the launcher's (GitHub #18) — the launch string also cd's, but a pane whose
    # own cwd is the checkout misleads every later shell opened in it.
    try:
        panes.new_session(session, cwd=card.workspace)
    except RuntimeError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    # Deliver through the seam. Panes stays runtime-blind — sees a finished string.
    runtime.start(card, session)
    # STAMP WHAT IT LAUNCHED ON, before we report anything. The
    # agent has now read its --settings and will never read them again; this
    # records which bytes that was, so a later rewrite of the file is DETECTABLE
    # rather than silently unapplied. Best-effort on purpose: a stamp that cannot
    # be written leaves the agent reporting `unknown`, which is the truth. It must
    # never turn a successful launch into a failure.
    _launched_now(a, card.name, runtime.settings_path(card))
    ledger_ok = _record_launch_unretirement(a, card)
    if _observe_live(runtime, panes, session, card):
        verified = _verify_live_hooks(a, card, runtime, panes, session)
        if verified == OK:
            _deliver_startup_inbox(a, card, panes, session)
        return verified if ledger_ok else CANNOT_TELL
    # Not observed live. Distinguish "waiting for a human" (a first-run consent
    # prompt) from "unknown" — both are could-not-tell (2), but they need
    # different human actions (live-fire found the consent case).
    final = panes.capture(session)
    if getattr(runtime, "waiting_for_human", None) and runtime.waiting_for_human(final):
        print(f"  could not tell: {card.name} ({session}) is WAITING ON A PROMPT "
              f"(first-run consent), not up yet. Answer it: `st log {card.name}` to "
              f"see it, then `st attach {card.name}`.", file=sys.stderr)
        return CANNOT_TELL
    print(f"  could not tell: launched {card.name} but the runtime was not observed "
          f"live in {session} within the timeout. It may still be coming up; "
          f"check `st log {card.name}`.", file=sys.stderr)
    return CANNOT_TELL


# The startup prompt goes out in ONE send and is acknowledged only when its end
# marker is read back, so an oversized backlog fails verification -- correctly,
# because a truncated block must never be acknowledged as complete. The defect
# (aegis-t876k) was what happened NEXT: an unbounded batch failed IDENTICALLY on
# every subsequent launch, so the bigger a backlog grew the more certainly it
# stayed. The condition was self-reinforcing and the only recovery was manual,
# per message. Bounding the batch does not make delivery more likely -- it makes
# PROGRESS possible, which is what an unwedgeable queue needs.
_STARTUP_INBOX_MAX_CHARS = 8000
# Room for the end marker and the held-back notice, both appended after the
# budget loop. Charging for them afterwards would let a full batch push the
# finished prompt past the bound it was built to respect.
_STARTUP_INBOX_RESERVE = 300


def _startup_inbox_batch(unread, budget: int = _STARTUP_INBOX_MAX_CHARS):
    """Split unread mail into (deliver now, hold back) on a character budget.

    WHOLE messages only. Half a message under a marker asserting the block is
    complete is precisely the false acknowledgement the marker exists to
    prevent, so the bound may never fall inside one.

    The head message is ALWAYS included, even when it alone exceeds the budget.
    Dropping it would relocate the very defect this fixes rather than fix it: a
    single oversized message would block everything queued behind it, on every
    launch, for ever. Sent alone it may still fail the marker check -- but that
    is then reported as head-of-line blocking against a named id, instead of a
    queue that stalls in silence.
    """
    batch: list = []
    used = 0
    for msg in unread:
        # "\n[<id>]\n" framing plus the body, matching what is built below.
        cost = len(msg.body or "") + len(msg.id) + 4
        if batch and used + cost > budget - _STARTUP_INBOX_RESERVE:
            break
        batch.append(msg)
        used += cost
    return batch, list(unread[len(batch):])


def _deliver_startup_inbox(a, card, panes, session: str) -> None:
    """Inject unread durable mail into a verified new session, then acknowledge it.

    This is deliberately after process + hook verification.  A session existing is
    not delivery, and a successful ``send`` is not delivery either: the terminal
    may absorb or truncate the prompt.  The final marker is therefore the receipt.
    Only the snapshot whose complete marker is visible is acknowledged; every
    failure is loud and leaves the durable pointers open for ``st inbox``.

    Best-effort by design.  Inbox trouble must not turn a healthy agent launch into
    a failed launch, because the open pointers are precisely the recovery path.
    """
    try:
        box = _inbox(a, default="beads")
        unread = box.unread(card.name)
    except Exception as e:  # noqa: BLE001 -- launch succeeded; durability survives
        print(f"  ⚠ startup inbox unreadable for {card.name} "
              f"({type(e).__name__}: {str(e)[:100]}); pointers remain open.",
              file=sys.stderr)
        return
    if not unread:
        return

    batch, held = _startup_inbox_batch(unread)
    ids = [m.id for m in batch]
    marker = f"ST-STARTUP-INBOX-COMPLETE:{','.join(ids)}"
    lines = ["[startup inbox] Durable messages received while you were offline:"]
    for msg in batch:
        lines.append(f"\n[{msg.id}]")
        lines.extend((msg.body or "").splitlines() or [""])
    if held:
        # sattler's condition on the bounded-batch ruling: the remainder is
        # VISIBLE to the agent who received the batch. A silent remainder would
        # trade a loud permanent wedge for a quiet partial one, and an inbox
        # refilling faster than it drains has to read as a FINDING, not as an
        # ordinary delivery.
        lines += ["", f"[startup inbox] {len(held)} more message(s) held and still "
                      f"unread; they arrive on your next launch. To read them now: "
                      f"`st inbox {card.name}`."]
    lines += ["", marker]
    prompt = "\n".join(lines)

    try:
        panes.send(session, attribute(prompt, "st startup"))
        # Capture scrollback as well as the visible tail.  The end marker proves
        # the complete block, not merely its prefix, reached the initial context.
        time.sleep(0.35)
        screen = panes.capture(session, history=200)
    except Exception as e:  # noqa: BLE001 -- keep the pointers as the recovery
        print(f"  ⚠ startup inbox delivery failed for {card.name} "
              f"({type(e).__name__}: {str(e)[:100]}); {len(unread)} pointer(s) "
              f"remain open.", file=sys.stderr)
        return
    if marker not in screen or input_stranded(screen):
        print(f"  ⚠ startup inbox delivery not verified for {card.name} "
              f"(complete marker absent or input stranded); {len(unread)} "
              f"pointer(s) remain open.", file=sys.stderr)
        if len(batch) == 1 and held:
            # The batch could not be made smaller, so retrying changes nothing:
            # this one message blocks the queue behind it on every launch. Name
            # it and the per-message escape, rather than letting the agent read
            # a generic failure and wait for a self-healing that cannot come.
            print(f"  ⚠ {card.name}: [{batch[0].id}] is "
                  f"{len(batch[0].body or '')} chars and did not land ALONE, so "
                  f"it is blocking {len(held)} message(s) behind it on every "
                  f"launch. Read past it with "
                  f"`st inbox --read-id {batch[0].id} {card.name}`.",
                  file=sys.stderr)
        return

    try:
        marked = box.mark_read(card.name, ids=ids)
    except Exception as e:  # noqa: BLE001 -- delivered, but preserve on close doubt
        print(f"  ⚠ startup inbox reached {card.name}, but pointer close failed "
              f"({type(e).__name__}: {str(e)[:100]}); inspect with `st inbox`.",
              file=sys.stderr)
        return
    if {m.id for m in marked} != set(ids):
        print(f"  ⚠ startup inbox reached {card.name}, but only "
              f"{len(marked)}/{len(ids)} pointer(s) closed; inspect with `st inbox`.",
              file=sys.stderr)
        return
    held_note = f"; {len(held)} held for the next launch" if held else ""
    print(f"  startup inbox: delivered and closed {len(marked)} verified "
          f"pointer(s) for {card.name}{held_note}.")


def _verify_live_hooks(a, card, runtime, panes, session: str) -> int:
    """The launch is live — but is it HOOKED? (aegis-8p0j gap 1, aegis-05up.)

    THE GAP THIS CLOSES. runtime.py already states the boundary honestly:
    compose() guarantees --settings was REQUESTED; it does not guarantee hooks
    FIRED, and _observe_live only proves the PROCESS is up. So `st new` could
    print "started" and exit 0 for an agent that came up carrying no stop hooks
    at all. That is not a hypothetical shape of bug — measured 2026-07-20
    (aegis-0v97), all 8 gastown-launched crew were running RIGHT THEN with no
    stop hooks; they could not even SEND, and nothing detected it for the entire
    time it was true. `st roles --check` finds it, but only if someone runs it.
    Here it is caught at the moment of launch, by the process's own cmdline.

    Three outcomes, and the middle one is the whole point:

      hooks match the graph   -> OK. Say what was verified, not just "started".
      MEASURED missing        -> REFUSED. Loud, naming the missing direction.
      could not look (None)   -> CANNOT_TELL. Never rendered as a pass.

    WHY THIS DOES NOT KILL THE SESSION. A defective agent is left RUNNING and the
    operator is told to remove it. Two reasons: the pane is the evidence (killing
    it destroys the cmdline that proves what went wrong, which is exactly what
    made aegis-0v97 hard to see), and a launcher that reaps on a verdict is one
    bad verdict away from killing healthy agents. `st stop` already exists and is
    one command. If arnold rules teardown belongs here, it is a small change —
    but it should be a ruling, not a side effect of adding a check.
    """
    need = roles_mod.required_stop_directions(card, _registry(a).all().exact())
    if not need:
        # NOTHING REQUIRED -> nothing to verify, and we must not manufacture
        # doubt about a requirement that does not exist. An isolated agent (no
        # lead above, no reports below) has no stop event to route in either
        # direction; reporting could-not-tell here would be a false alarm on
        # every leaf agent, and false alarms are what teach an operator to stop
        # reading the output.
        print(f"  started {card.name} ({session}) — runtime live; the graph "
              f"requires no stop directions of this agent.")
        return OK
    # cmdline is deliberately NOT a Panes protocol method (arnold's non-goal for
    # this bead: Panes gains nothing). We read it off the adapter if it has one;
    # an adapter that cannot show a process cmdline genuinely cannot answer the
    # question, and that is a cannot-tell, not a pass.
    reader = getattr(panes, "cmdline", None)
    wiring = live_wiring(session, reader) if reader else None
    if wiring is None:
        print(f"  could not tell: {card.name} ({session}) is live, but its stop "
              f"hooks could NOT be read from the running process, so it is "
              f"UNVERIFIED — not confirmed hooked. Check `st roles --check`.",
              file=sys.stderr)
        return CANNOT_TELL
    missing = need - wiring.directions
    if missing:
        # SAY WHAT IT HAS, NOT ONLY WHAT IT LACKS — dearing's aegis-0v97
        # correction (205e492), which landed on roles.py while this was in
        # flight and applies verbatim here. "NO stop hooks at all" is false as
        # English and false in the expensive direction: a process launched by a
        # foreign launcher DOES carry hooks, just not a `stop_event` direction.
        # Read literally, that string is aegis-05up — "respawn dropped
        # --settings, the rm -rf and force-push guards are gone" — a real
        # emergency that is NOT what we measured. Naming the settings path makes
        # the foreign launcher self-evident instead of alarming.
        carries = (f"stop directions {sorted(wiring.directions)}"
                   if wiring.directions else "no `stop_event` hook")
        whence = (f", its --settings is {wiring.settings_path}"
                  if wiring.settings_path
                  else ", and its launch line carries NO --settings at all "
                       "(this one IS the hookless-zombie case)")
        print(f"  FAILED: {card.name} ({session}) came up WITHOUT the stop hooks "
              f"its position requires. The live process carries {carries}"
              f"{whence}, but this agent needs {sorted(need)} — missing "
              f"{sorted(missing)}. It is running and it is broken: remove it "
              f"with `st stop {card.name}`, fix the settings it launches with, "
              f"and start it again.", file=sys.stderr)
        return REFUSED
    verified = sorted(need) if need else "none required by the graph"
    print(f"  started {card.name} ({session}) — runtime live, stop hooks VERIFIED "
          f"on the live process: {verified}.")
    return OK


# The mode label `st start <agent>...` reports: agents named on the command line
# came from no mode, and printing one would credit a config that was never read.
EXPLICIT = "explicit"


def _prompt(prompt: str, default: str) -> str:
    """One wizard question on a real terminal.

    The QUESTION goes on its own line and the cursor sits on a short `>` below it.
    Several of these questions carry a clause explaining the choice, and a long
    question with the answer typed onto its right-hand end wraps into an unreadable
    paragraph — worse when stdin is piped, where nothing echoes to break the line.

    `default` is shown in brackets; Enter accepts it. EOF (a piped stdin that ran
    out) takes the default rather than raising: an init that dies on ^D after six
    answered questions throws away work the operator already did.
    """
    suffix = f" [{default}]" if default else ""
    print(f"  {prompt}{suffix}")
    try:
        return input("  > ")
    except EOFError:
        print()
        return ""


def _cmd_init(a, *, ask=_prompt, isatty=None) -> int:
    """init — scaffold a deployment: the store, the cards, the hooks, the config.

    It WRITES THROUGH THE EXISTING SEAMS and adds no new ones: cards go in via the
    registry (which is where a card gets its generated pane), roles and stop-hook
    routing via tier.role_set, hook files via the same emitter `roles set` uses.
    Nothing here is a second way to declare a crew — every artifact is one the
    operator would otherwise have hand-written, in the same place and format.

    0 wrote it · 1 refused (a bad name, an existing store, no terminal to ask in).
    """
    import sys as _sys
    root = Path(a.root)
    isatty = isatty if isatty is not None else _sys.stdin.isatty
    crew_dir = root / "crew"

    # AN EXISTING STORE IS A REFUSAL, not a merge. A second init over a live
    # deployment is far more likely to be a mistyped --root than an intention, and
    # the cost of guessing wrong is a crew card set nobody expected. --force is the
    # way to say it on purpose; even then no existing card is overwritten.
    existing = sorted(crew_dir.glob("*.json")) if crew_dir.is_dir() else []
    cfg_exists = config.config_path(root).is_file()
    if (existing or cfg_exists) and not a.force:
        what = []
        if existing:
            what.append(f"{len(existing)} crew card(s)")
        if cfg_exists:
            what.append(config.CONFIG_NAME)
        print(f"  refused: {root} is already a deployment ({', '.join(what)}). "
              f"`st crew` to see it. To add an agent to an existing store use "
              f"`st roles set <name> <role>`; pass --force if you really mean to "
              f"scaffold over this one (no existing card is overwritten).",
              file=_sys.stderr)
        return REFUSED

    # FLAGS PRE-ANSWER QUESTIONS; --yes skips the asking entirely. A non-tty with
    # no --yes REFUSES rather than calling input() — a wizard that blocks forever
    # inside a script or a hook is worse than one that says it cannot ask.
    defaults = scaffold.Answers(
        admin=a.admin or scaffold.DEFAULT_ADMIN,
        workers=tuple(w.strip() for w in (a.crew or "").split(",") if w.strip()),
        workspaces=a.workspaces,
        mode=a.mode or config.DEFAULT_MODE,
        hibernate=bool(a.hibernate))
    try:
        if a.yes:
            answers = scaffold.make_answers(
                admin=defaults.admin, workers=defaults.workers,
                workspaces=defaults.workspaces, mode=defaults.mode,
                hibernate=defaults.hibernate)
        elif not isatty():
            print(f"  refused: stdin is not a terminal, so `st init` cannot ask "
                  f"its questions. Pass -y/--yes to take the flags and defaults "
                  f"(`st init -y --admin <name> --crew a,b`).", file=_sys.stderr)
            return REFUSED
        else:
            print("\n  st init — a few questions. Enter accepts the [default].\n")
            answers = scaffold.ask_all(ask, defaults=defaults)
    except scaffold.ScaffoldError as e:
        print(f"  refused: {e}", file=_sys.stderr)
        return REFUSED

    plan = scaffold.plan(root, answers)
    # PREVIEW THEN CONFIRM, but only for the operator who is being asked. Under
    # --yes the answers came from flags and the write log below says the same
    # thing with real paths, so a preview there is noise ahead of the truth.
    if a.dry_run or not a.yes:
        print()
        print(plan.render())
        print()
    if a.dry_run:
        print("  --dry-run: nothing written.\n")
        return OK
    if not a.yes:
        if ask("Write this?", "yes").strip().lower() in ("n", "no"):
            print("  nothing written.")
            return REFUSED
        print()
    return _init_apply(a, root, plan, answers)


def _init_apply(a, root: Path, plan, answers) -> int:
    """Write the plan. Cards first (so role_set has agents to wire), then roles +
    hooks in ONE generative operation, then the config."""
    from . import tier
    from .protocols import Agent

    for d in plan.dirs:
        (root / d).mkdir(parents=True, exist_ok=True)

    reg = FilesRegistry(root / "crew")
    for name, _role, _pane in plan.cards:
        if (root / "crew" / f"{name}.json").is_file():
            # --force reached an existing card. Leave it EXACTLY as it is: init
            # scaffolds what is missing and is never a card rewriter.
            print(f"  kept     {name:<12} (card already exists — not touched)")
            continue
        ws = f"{answers.workspaces.rstrip('/')}/{name}" if answers.workspaces else None
        reg.set(Agent(name=name, role="worker", workspace=ws))
        print(f"  card     {name:<12} {root / 'crew' / f'{name}.json'}")

    # ROLES + ROUTING through the generative op, so the cards and the stop hooks
    # cannot disagree — the reason this does not write the hierarchy itself.
    try:
        rplan = tier.role_set(reg, answers.admin, "administrator",
                             reports=list(answers.workers))
    except (LookupError, ValueError, CapabilityError) as e:
        print(f"  refused: could not wire the tier: {e}", file=sys.stderr)
        return REFUSED
    for ag in rplan.writes:
        print(f"  role     {ag.name:<12} {ag.role}"
              f"{f' -> reports_to {ag.reports_to}' if ag.reports_to else ''}")

    for path in _emit_role_settings(root, {ag.role for ag in rplan.writes}):
        print(f"  hooks    {path}")

    cfg_path = config.config_path(root)
    if cfg_path.is_file():
        print(f"  kept     {cfg_path} (already there — not overwritten)")
    else:
        cfg_path.write_text(plan.config_text)
        print(f"  config   {cfg_path}")

    # PROVE IT PARSES. The file was just generated, and a config this command
    # writes but `st start` would refuse is the worst possible handoff.
    try:
        config.load(root)
    except config.ConfigError as e:
        print(f"  ⚠ the config just written does NOT parse: {e}", file=sys.stderr)
        return CANNOT_TELL

    print()
    print(f"  ready. {len(plan.cards)} card(s), mode {answers.mode!r}.")
    print(f"    st start          # bring up mode {answers.mode!r}")
    print(f"    st attach         # the admin's pane (starts it if it is down)")
    print(f"    st crew           # who exists, who is up")
    print()
    return OK


def _boot_launcher(a, panes, runtime):
    """launch(card) -> (bootstrap verdict, why), over the ONE launcher.

    The `why` deliberately POINTS AT the launcher's own output rather than
    restating it. _launch already prints the specific refusal (a missing settings
    file, an unequipped workspace, a harness we cannot host) and a summary written
    here would be a second, shorter account of the same event — which is how a
    report ends up disagreeing with the lines directly above it.
    """
    def launch(card):
        rc = _launch(a, card, panes, runtime, dry_run=False)
        if rc == OK:
            return boot_mod.STARTED, f"launched into {_session_for(card)!r}, hooks verified"
        if rc == REFUSED:
            return boot_mod.REFUSED, "launch REFUSED — the reason is printed above"
        return (boot_mod.UNVERIFIED,
                f"session {_session_for(card)!r} exists but the runtime was NOT "
                f"observed live — see above; `st log {card.name}`")
    return launch


def _start_roster(a, cfg, agents) -> tuple[list[str], str, list[str], str | None]:
    """(names, mode label, skipped-retired, refusal) — WHO `st start` will bring up.

    Pure resolution, separated from the launching so a test can pin the selection
    rules (retired exclusion, tier order, unknown names) without a runtime. The
    refusal string is returned rather than printed for the same reason.
    """
    if a.agent:
        # EXPLICIT NAMES WIN, AND THE MODE IS NOT CONSULTED. Naming both is
        # REFUSED rather than silently resolved: `st start --mode heavy sattler`
        # has two readings ("heavy, plus sattler" / "sattler, from the heavy set")
        # and picking one quietly means the operator who meant the other brings up
        # a fleet they did not ask for. Ambiguity about how many agents to bill is
        # not the place to guess.
        if a.mode:
            return [], "", [], (
                f"--mode {a.mode!r} and explicit agents ({', '.join(a.agent)}) are "
                f"two different asks — a mode IS a crew list. Pick one: "
                f"`st start --mode {a.mode}` for the mode's crew, or "
                f"`st start {' '.join(a.agent)}` for exactly these.")
        selectors, label = list(a.agent), EXPLICIT
    else:
        try:
            selectors = cfg.selectors(a.mode)
        except config.ConfigError as e:
            return [], "", [], str(e)
        label = a.mode or cfg.mode

    roster = config.resolve_crew(selectors, agents)
    if roster.unknown:
        return [], label, [], (
            f"no card for: {', '.join(sorted(roster.unknown))}. A selector is `*`, "
            f"a role ({', '.join(config.ROLE_SELECTORS)}) or an agent name; "
            f"`st crew` lists the cards that exist.")
    if not roster.names:
        # SELECTED NOBODY. The common shape is a `lite` boot on a store with no
        # administrator card — and the useful message names the store, not the
        # abstraction, because the fix is `st roles set <agent> administrator`.
        detail = (f" (the roster has {len(agents)} card(s), and none of them "
                  f"matched)" if agents else " (the roster is EMPTY — no cards)")
        retired_note = (f" {len(roster.skipped_retired)} matching card(s) are "
                        f"RETIRED and are never started: "
                        f"{', '.join(sorted(roster.skipped_retired))}."
                        if roster.skipped_retired else "")
        return [], label, roster.skipped_retired, (
            f"mode {label!r} selects {selectors} and that matched NO agent to "
            f"start{detail}.{retired_note} An administrator is what `lite` boots: "
            f"`st roles set <agent> administrator` writes one.")
    return roster.names, label, roster.skipped_retired, None


def _cmd_start(a) -> int:
    """start [agent...] [--mode M] — bring the town up. The boot command.

    Why this is a command and not `st new` in a loop, or a flag on `st tend`, is
    argued in bootstrap.py. Here, the three things the exit code has to mean:

      0  every selected agent is UP (launched now, or already running)
      1  REFUSED before launching anything — a bad mode, a bad config, a name
         with no card. A refusal launches NOTHING; it is not a partial boot.
      2  the pass ran and some agent is NOT known to be up (refused mid-flight,
         or launched-but-unverified). A boot you cannot script on is not a boot.
    """
    if (rc := _window_launch_gate(a)) is not None:
        return rc
    panes = _panes(a)
    try:
        agents = _registry(a).all().exact()
    except Exception as e:                       # noqa: BLE001 — registry unreachable
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL

    # CONFIG FIRST, and a malformed file REFUSES (config.load, not
    # load_or_default): this command launches agents, and starting the wrong SET
    # because a key was misspelled is worse than starting nothing.
    try:
        cfg = config.load(a.root)
    except config.ConfigError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED

    names, label, skipped, refusal = _start_roster(a, cfg, agents)
    if refusal:
        print(f"  refused: {refusal}", file=sys.stderr)
        return REFUSED

    reg = _registry(a)
    cards = [reg.get(n) for n in names]
    if label == EXPLICIT:
        print(f"  {len(cards)} agent(s) named on the command line: {', '.join(names)}")
    else:
        where = f"{cfg.path}" if cfg.path else "the built-in defaults (no config file)"
        print(f"  mode {label!r} from {where} — {len(cards)} agent(s): "
              f"{', '.join(names)}")
    runtime = _runtime(a, panes)
    boot = boot_mod.Bootstrapper(
        panes, launch=_boot_launcher(a, panes, runtime),
        log=lambda msg: print(f"  {msg}", file=sys.stderr))
    rep = boot.bring_up(cards, mode="" if label == EXPLICIT else label,
                        dry_run=a.dry_run, skipped_retired=skipped)
    print()
    print(rep.render())
    print()
    # WHERE THE OPERATOR GOES NEXT. A boot whose whole point is "start the admin"
    # should not make them look up how to reach it.
    if not a.dry_run and rep.up():
        head = rep.up()[0]
        print(f"  attach: `st attach {head}`   ·   roster: `st crew`")
    _report_hibernate(cfg)
    return OK if rep.healthy() else CANNOT_TELL


def _report_hibernate(cfg) -> None:
    """Say the hibernate policy in force, on the command that just applied the
    rest of the config. An operator reading `st start` output is holding the
    config in their head at that exact moment, and a policy that only ever
    manifests as "the admin went quiet at 3am" is one nobody connects to a file."""
    h = cfg.hibernate
    if not h.enabled:
        return
    valve = (f", or after {h.max_quiet_minutes} min of quiet"
             if h.max_quiet_minutes else "")
    print(f"  hibernate ON: the administrator goes quiet when nothing is urgent "
          f"and there is nothing to dispatch. Rule Zero still overrides it. It "
          f"wakes on a push{valve}.")


# Every path that KILLS a session takes the runtime down without a natural turn
# end, so the transcript archiver -- a Stop hook -- never fires for it.
_KILL_CAPTURE_TIMEOUT = 30


def _capture_history_before_kill(a, agent_name: str, why: str) -> None:
    """Archive an agent's transcripts BEFORE its session is killed.

    THE GAP THIS CLOSES (aegis-ay3gv2). The archiver is a Stop hook, so it fires
    on a NATURAL turn end. `st stop`, the cycle that `st tend` performs THROUGH
    it, and the auth-dead relaunch all kill the runtime instead, and no hook
    fires for any of them.

    That is survivable for claude, whose transcripts sit on durable disk and are
    picked up by any later capture. It is NOT survivable for codex: CODEX_HOME
    lives under /run/user/<uid>, measured tmpfs, so an uncaptured rollout is
    destroyed by the next reboot -- which is the incident aegis-xfmon3 exists
    for. Retiring the */30 safety net once the hook was proven (xfmon3 step 3)
    was right for the natural-stop path and turned a 30-minute worst case into an
    unbounded one for these three.

    Best-effort, and it NEVER changes the caller's verdict. A capture that could
    refuse a stop would be an archiver holding a shutdown hostage, and the whole
    point of a deliberate stop is that it happens.

    Logged to kill-capture.log, deliberately NOT to hook.log. The timer gate
    reads hook.log to decide whether the HOOK is live; a kill-capture written
    there would read as a hook fire and quietly destroy the one instrument that
    can tell "the hook works" from "something else is covering for it". Keeping
    the two logs apart is what makes the next retirement decision provable.
    """
    import subprocess
    # runtime.canonical_source, NOT selfcheck's: it is the same function, but
    # runtime's is the name the suite's ambient-checkout guard pins, so a test
    # cannot accidentally resolve the developer's real checkout and run a real
    # capture against the live archive.
    from . import runtime as _runtime

    log = Path(os.environ.get("ST_HISTORY_DIR") or (Path(a.root) / "history"))
    src = _runtime.canonical_source()
    script = Path(src) / "scripts" / "st-history-capture.sh" if src else None
    if script is None or not script.is_file():
        # Same doctrine as the hook itself: no guessed path. Say so rather than
        # log a success for work that never ran.
        note, rc = "skipped: canonical source unknown", "-"
    else:
        try:
            done = subprocess.run([str(script), "--agent", agent_name],
                                  capture_output=True, text=True,
                                  timeout=_KILL_CAPTURE_TIMEOUT)
            note, rc = "captured", str(done.returncode)
        except Exception as e:  # noqa: BLE001 -- a stop must never fail on this
            note, rc = f"FAILED {type(e).__name__}: {str(e)[:60]}", "-"
    if not note.startswith("captured"):
        print(f"  \u26a0 transcript capture before killing {agent_name}: {note}. "
              f"A codex rollout not yet archived is on tmpfs and does not "
              f"survive a reboot.", file=sys.stderr)
    try:
        log.mkdir(parents=True, exist_ok=True)
        with open(log / "kill-capture.log", "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\t"
                     f"{agent_name}\t{why}\t{note}\trc={rc}\n")
    except Exception:  # noqa: BLE001 -- an unwritable log never disturbs a stop
        pass


def _cmd_stop(a) -> int:
    """stop <agent> — kill the agent's session (#5).

    kill_session is idempotent, so this is honest about the two states: an agent
    that is not running is ALREADY the desired end state (exit 0, "was not
    running"); a running one is killed and VERIFIED gone (exit 0, "stopped") or,
    if it is somehow still there after the kill, exit 2 — never a cheerful "done"
    over a session that is still alive.
    """
    panes = _panes(a)
    try:
        agent = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    session = agent.pane      # the address; None/absent = not running
    if not session or not panes.exists(session):
        if harness_mod.name_for(agent, root=a.root) == "codex" and not a.dry_run:
            from . import codex_daemon
            fixed = codex_daemon.repair(agent.name)
            if fixed.blocked:
                print(f"  repaired {codex_daemon.FLAG} for {agent.name}: "
                      f"{fixed.reason()}", file=sys.stderr)
        print(f"  {a.agent} was not running.")
        return OK
    # OWNERSHIP GUARD. The session is live — but st only reaps what
    # st launched. The registry pane names can COLLIDE with sessions somebody
    # else already started under the same name, so on a shared socket
    # `st stop ellie` would kill a session st never launched.
    # A name match is not permission to kill: refuse unless st owns the session.
    if not panes.owns(session):
        print(f"  refused: {a.agent} ({session}) was not launched by st — refusing "
              f"to stop a session st does not own. A name match is not permission "
              f"to kill (the registry pane names collide with the live crew).",
              file=sys.stderr)
        return REFUSED
    # SECOND FACTOR: the LAUNCH STAMP. SHANTY_OWNED alone lied once, live
    # (wn7g pilot's negative control): tend's pre-gate respawns created
    # another orchestrator's crew sessions, so those panes carry the marker
    # while the OTHER fleet operates them — `st stop ellie` dry-ran straight
    # to "would kill" against the live foreign session. An st-MANAGED agent
    # has a launch stamp (st new writes it; st stop forgets it); a session st
    # merely created once does not. No stamp while other stamps exist =
    # created-but-not-managed = refuse. Empty store = cannot tell = the env
    # marker alone decides, as before (fresh deployments must still reap).
    try:
        launches = _launches(a)
        unstamped = (launches.get(a.agent) is None
                     and any(launches.root.glob("*.json")))
    except Exception:  # noqa: BLE001 — a broken store must not wedge the reap
        unstamped = False
    if unstamped:
        print(f"  refused: {a.agent} ({session}) carries st's session marker but "
              f"has NO launch stamp — st created this session once but does not "
              f"manage the agent in it (another orchestrator does). Refusing.",
              file=sys.stderr)
        return REFUSED
    if a.dry_run:
        print(f"  would: kill-session {session}")
        return OK
    if harness_mod.name_for(agent, root=a.root) == "codex":
        from . import codex_daemon
        fixed = codex_daemon.repair(agent.name)
        if fixed.blocked:
            print(f"  repaired {codex_daemon.FLAG} for {agent.name}: {fixed.reason()}",
                  file=sys.stderr)
    # BEFORE the kill, never after: after it, the codex rollout is gone.
    _capture_history_before_kill(a, a.agent, "stop")
    panes.kill_session(session)
    if panes.exists(session):
        print(f"  could not tell: killed {session} but it is still there",
              file=sys.stderr)
        return CANNOT_TELL
    # The stamp described a LIVE launch; that launch is now gone. Leaving it would
    # let `st crew` report `current` for the settings of a process that no longer
    # exists — a clean bill of health for nobody.
    _launches(a).forget(a.agent)
    # RECORD THE INTENT (GitHub #29). This is the only place st learns that a pane
    # went down BECAUSE SOMEBODY DECIDED SO. Without it the drain and the roster
    # read a deliberate shutdown and a crash identically, and the measured
    # consequence was nine agents an operator had just been told to stop coming
    # back as `re-dispatch <agent> — STOPPED`.
    _stops(a).record(a.agent, time.time(),
                     by=os.environ.get("SHANTY_AGENT", ""),
                     reason=getattr(a, "reason", "") or "")
    # WHAT TEND WILL ACTUALLY DO, asked rather than assumed (aegis-k9068).
    #
    # This line used to promise "`st tend` will still respawn it" unconditionally.
    # It is the same function that just called `_launches.forget()` above — and
    # tend REFUSES an agent with no stamp while any other agent has one. So the
    # promise was false for every deliberately stopped agent on a live fleet, and
    # a stop taken on the strength of it silently became permanent: measured at
    # ~2h of lost tier-1 alert cover.
    #
    # The remaining true case is narrow and worth keeping: with NO stamps left at
    # all, tend's ownership gate does not fire (a fresh deployment must still
    # self-heal), so it would respawn. Reporting the condition instead of a flat
    # promise means the sentence stays correct in both.
    others = [p for p in _launches(a).root.glob("*.json")] if _launches(a) else []
    if others:
        fate = (f"`st tend` will NOT bring it back — its launch stamp is gone, and "
                f"tend does not respawn an unstamped agent. Use `st new {a.agent}`.")
    else:
        fate = (f"`st tend` will respawn it (no launch stamps remain, so tend's "
                f"ownership gate does not apply); `st tend --retire {a.agent}` is "
                f"how you say do not bring it back.")
    print(f"  stopped {a.agent} ({session}) — recorded as DELIBERATE. {fate}")
    return OK


def _cmd_log(a) -> int:
    """log [agent] — what happened, = capture() on the session pane (arnold's #5
    ruling: log needs NOTHING new, it rides capture). Read-only."""
    panes = _panes(a)
    if not a.agent:
        print("  refused: log <agent> — whose log?", file=sys.stderr)
        return REFUSED
    try:
        agent = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    session = agent.pane
    if not session or not panes.exists(session):
        print(f"  {a.agent} is not running — no session to read.")
        return OK
    print(panes.capture(session))
    return OK


def _cmd_graph_adoption(a) -> int:
    """The denominator (aegis-rcyd.1).

    Adoption arguments on this fleet have repeatedly been made from impressions
    — "we don't use quipu enough" — with no number attached, and an impression
    cannot tell a fixed habit from an unfixed one. This reads the ledger every
    dispatch writes and reports what share carried a node, what share carried a
    stated reason instead, and which agents have never cited one.

    Exit 0 always: this is a report, not a gate. A gate on a habit measurement
    would make the measurement the thing people work around.
    """
    hours = float(getattr(a, "since", 24.0) or 0)
    cutoff = (time.time() - hours * 3600) if hours else 0.0
    window = f" in the last {hours:g}h" if hours else ""
    rows = graph_adoption.read_rows(a.root, cutoff)
    s = graph_adoption.summarize(rows, include_dry_run=a.include_dry_run)
    if a.json:
        print(json.dumps({
            "window_hours": hours,
            "eligible": s.eligible, "with_nodes": s.with_nodes,
            "exempt": s.exempt, "missing": s.missing, "verified": s.verified,
            "unverifiable": s.unverifiable,
            "coverage": s.coverage, "node_share": s.node_share,
            "by_agent": s.by_agent,
            "reasons": dict(s.reasons), "nodes": dict(s.nodes.most_common(20)),
            "zero_node_agents": s.zero_node_agents,
            "scope": graph_adoption.SCOPE_NOTE,
        }, indent=2, sort_keys=True))
        return OK
    if not s.eligible:
        # NOT "100% coverage". An empty ledger means nothing has been dispatched
        # since the requirement landed — or that the ledger is not being written,
        # which is a different problem and must not render as a perfect score.
        print(f"  no eligible dispatches recorded{window}. "
              f"Ledger: {Path(a.root) / 'logs' / graph_adoption.LEDGER}")
        print(f"  {graph_adoption.SCOPE_NOTE}")
        return OK
    pct = lambda v: "-" if v is None else f"{v * 100:.0f}%"  # noqa: E731
    print(f"  {s.eligible} eligible dispatches{window}"
          f"   (mode: {graph_adoption.mode(a.root)})")
    # THE SCOPE TRAVELS WITH THE NUMBER (aegis-5pchx). A reader who sees only
    # "coverage 100%" will take it to mean the fleet cites graph context for its
    # work. It does not: a haul self-advance never reaches the gate, and it is
    # how agents most often start. A scope note that lives only on a bead is a
    # note the number outruns.
    print(f"  {graph_adoption.SCOPE_NOTE}")
    print(f"  carrying a node:      {s.with_nodes:>4}  ({pct(s.node_share)})")
    print(f"  stated no-context:    {s.exempt:>4}")
    print(f"  coverage (either):    {s.with_nodes + s.exempt:>4}  ({pct(s.coverage)})")
    if s.missing:
        print(f"  NEITHER (advise-mode): {s.missing:>3}")
    print(f"  node verified:        {s.verified:>4}"
          + (f"   unverifiable: {s.unverifiable}" if s.unverifiable else ""))
    if s.zero_node_agents:
        # NAMED, not counted. "Zero-use distributions are explicit" is the
        # acceptance criterion, and a count hides which agents to go and ask.
        print(f"  never cited a node:   {', '.join(s.zero_node_agents)}")
    if s.reasons:
        print("  exemption reasons (a dominant one is a finding about the GRAPH):")
        for reason, n in s.reasons.most_common(5):
            print(f"    {n:>3}  {reason}")
    if s.nodes:
        print("  most-cited nodes:")
        for node, n in s.nodes.most_common(5):
            print(f"    {n:>3}  {node}")
    print("  server-side denominator: quipu_http_client_requests_total{client=...} "
          "in prometheus — this ledger counts DISPATCHES, not reads.")
    return OK


def _cmd_doctor(a) -> int:
    """st doctor [tool] [--install] [--dry-run] [--no-latest].

    Detect is the default and touches nothing. --install mutates; --dry-run makes
    even --install touch nothing (it prints the plan). Exit: 0 all present &
    current, 1 something absent/stale, 2 something could-not-tell (quipu's broken
    --version, or an unreachable release source)."""
    from . import doctor as doc
    from . import stats as stats_mod

    specs = doc.SPECS
    if getattr(a, "tool", None):
        specs = tuple(s for s in doc.SPECS if s.name == a.tool)
        if not specs:
            known = ", ".join(s.name for s in doc.SPECS)
            print(f"unknown tool {a.tool!r}. known: {known}", file=sys.stderr)
            return REFUSED

    healths = doc.detect_all(specs, check_latest=not a.no_latest)

    # ...and ask the question about ITSELF (aegis-daoh, dearing's ruling). doctor
    # reported installed-vs-available for four tools and never once about `st`.
    # The tool that audits deployment drift was the only one exempt, and it is the
    # one whose staleness silently corrupts every other row it prints. Only
    # rendered for a full run: `st doctor bobbin` asked about bobbin.
    # remote= rides --no-latest: both mean "no network lookups on this run". The
    # behind-upstream fetch is the same class of question as "0.6.0 available".
    self_h = (selfcheck.check_self(remote=not a.no_latest)
              if len(specs) == len(doc.SPECS) else None)

    sock_v, sock_why = _socket_check(a)

    # Is the metrics capture wired to the branch that writes TOKENS (aegis-u5u98)?
    # Asked here because `st stats` told a reader to ask `st doctor` and doctor had
    # nothing to say — a tell that points at a command which does not answer is a
    # dead end in the exact place it was meant to help. Never fatal to doctor: a
    # registry we cannot read is a `?` row, not a crash in the command an operator
    # reaches for when things are already going wrong.
    try:
        wire_v, wire_why = stats_mod.capture_wiring(_registry(a).all().exact())
    except Exception:          # noqa: BLE001 — doctor never fails on this leg
        wire_v, wire_why = stats_mod.WIRING_UNKNOWN, "registry unreadable"

    # Stray stashes in SHARED repos (aegis-pxzi4). `refs/stash` is shared across
    # every linked worktree and there is no stash hook, so DISCOVERY is the only
    # lever — and nobody runs `git stash list` in a repo they did not stash in.
    try:
        _repos = guard_mod.discover()
        stash_found, stash_n = doc.stray_stashes(_repos), len(_repos)
    except Exception:          # noqa: BLE001 — same rule as the leg above
        stash_found, stash_n = [], 0

    if not a.install:
        print(doc.report(healths))
        if self_h is not None:
            print(selfcheck.render(self_h))
        print(stats_mod.render_wiring(wire_v, wire_why))
        print(doc.render_stashes(stash_found, stash_n))
        print(_render_socket(sock_v, sock_why))
        code = _fold_socket(_doctor_exit(doc, healths, self_h), sock_v, doc)
        # The untracked-hook liveness leg (aegis-06ue4): out-of-band answer to
        # "has the fail-open governance nudge actually run?" Only on a full run —
        # `st doctor bobbin` asked about bobbin, not the fleet's hooks.
        if len(specs) == len(doc.SPECS):
            from . import codex_daemon
            try:
                daemon_blocks = [h for card in _registry(a).all().exact()
                                 if (h := codex_daemon.inspect(card.name)).blocked]
            except Exception:
                daemon_blocks = []
            if daemon_blocks:
                print("\n  CODEX DAEMON LAUNCH DEPTH")
                for h in daemon_blocks:
                    print(f"  ! {h.agent}: {codex_daemon.FLAG}: {h.reason()}")
                code = _fold_generic(code, 1)
            else:
                print("\n  CODEX DAEMON LAUNCH DEPTH\n  ✓ no proven per-card blockers")
            from . import provision as prov_mod
            try:
                uniform, uniform_broken = prov_mod.uniformity_report(
                    _registry(a).all().exact(), Path(a.root))
            except Exception as e:       # doctor reports uncertainty, never dies
                uniform, uniform_broken = (f"  TOOLING UNIFORMITY\n  ? {e}", True)
            print("\n" + uniform)
            if uniform_broken:
                code = _fold_generic(code, 1)
            from . import untracked_health as uh
            uh_rows, uh_text = _untracked_health(a)
            if uh_text is not None:
                print("\n" + uh_text)
                code = _fold_generic(code, uh.worst_exit(uh_rows))
            # SHARED-CHECKOUT GUARD COVERAGE (aegis-xig5m). st assists the
            # worktree protocol; this is the leg that says whether it is also
            # ENFORCED. Discovered per run, never a constant — a hardcoded repo
            # list is the failure this check exists to catch (the deployment's
            # own installer defaulted to ONE repo while twelve were in play).
            g_rows = guard_mod.survey()
            print("\n" + guard_mod.render(g_rows))
            code = _fold_generic(code, guard_mod.worst_exit(g_rows))
        return code

    plans = [doc.plan_install(h) for h in healths]
    print(doc.report(healths, plans=plans))
    if self_h is not None:
        print(selfcheck.render(self_h))
    if a.dry_run:
        return _doctor_exit(doc, healths, self_h)  # planned only — nothing ran

    failures = []
    for p in plans:
        try:
            doc.run_install(p)
        except RuntimeError as e:
            failures.append(str(e))
    if failures:
        for f in failures:
            print(f, file=sys.stderr)
        return CANNOT_TELL
    # re-detect so the post-install report is the observed state, not the intent
    observed = doc.detect_all(specs, check_latest=not a.no_latest)
    print(doc.report(observed))
    # Re-run the self-check too: --install can have just replaced `st` itself.
    self_after = selfcheck.check_self() if len(specs) == len(doc.SPECS) else None
    if self_after is not None:
        print(selfcheck.render(self_after))
    return _doctor_exit(doc, observed, self_after)


def _socket_check(a):
    """Ask tmux TWICE: on the declared socket, and on every socket present.

    Two questions, because one cannot separate "the fleet is down" from "we are
    looking at the wrong server", and those need different actions from a human.
    """
    from . import doctor as doc
    try:
        agents = _registry(a).all().exact()
    except Exception:
        return doc.SOCKET_UNKNOWN, "could not read the registry"
    panes = [ag.pane for ag in agents if ag.pane]
    if not panes:
        return doc.socket_health(0, 0, 0, declared_socket(a.root))

    declared = declared_socket(a.root)
    here = Tmux(socket=declared)
    on_declared = sum(1 for p in panes if here.exists(p))

    anywhere = 0
    if not on_declared:
        for sock in _sockets_present():
            other = Tmux(socket=sock)
            anywhere = max(anywhere, sum(1 for p in panes if other.exists(p)))
            if anywhere:
                break
    return doc.socket_health(len(panes), on_declared, anywhere, declared)


def _sockets_present() -> list[str]:
    """Every tmux server socket this user has. Used only to answer "is the fleet
    somewhere ELSE?" — never to pick one silently. Choosing for the operator is
    how the ambiguity that caused this becomes permanent."""
    d = Path(f"/tmp/tmux-{os.getuid()}")
    try:
        return sorted(p.name for p in d.iterdir() if p.is_socket())
    except OSError:
        return []


def _render_socket(verdict, why) -> str:
    from . import doctor as doc
    mark = {doc.SOCKET_OK: "  socket     ok",
            doc.SOCKET_WRONG: "  socket     WRONG",
            doc.SOCKET_UNKNOWN: "  socket     unknown"}[verdict]
    return f"\n{mark}   {why}\n"


def _untracked_health(a):
    """Rows + rendered block for `st doctor`'s untracked-hook-liveness leg
    (aegis-06ue4). Every input is read here and injected into the pure checker.

    All three readers fail toward cannot-tell, never toward a false pass or a
    false SUSPECT: a launched stamp that cannot be stat'd, a tmux that cannot be
    reached, a consent file that cannot be read all resolve to `None`, which the
    checker renders as "could not tell", not as "fine".
    """
    from . import untracked_health as uh
    import time
    from pathlib import Path
    try:
        agents = _registry(a).all().exact()
    except Exception as e:
        return [uh.Row("(registry)", uh.CANNOT_TELL, str(e))], None

    root = Path(a.root)

    def launch_time(name):
        # The launched/ stamp is written atomically AT launch (launched.record),
        # so the file's own mtime IS the launch time — no new field needed.
        try:
            return (root / "launched" / f"{name}.json").stat().st_mtime
        except OSError:
            return None

    # pane activity via tmux #{pane_activity} (epoch of last output). One
    # list-panes call, socket-aware through the same adapter `st crew` uses.
    # "active" = produced output within the checker's grace window; anything
    # older is an idle pane, which makes an absent ledger benign. Unreadable ->
    # None, which the checker treats as cannot-tell rather than guessing.
    panes = _panes(a)
    activity = {}
    try:
        argv = panes._cmd("list-panes", "-a", "-F",
                          "#{session_name} #{pane_activity}")
        import subprocess
        r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit():
                    activity[parts[0]] = float(parts[1])
        else:
            activity = None
    except Exception:
        activity = None

    def pane_active(name):
        if activity is None:
            return None
        card = next((c for c in agents if c.name == name), None)
        pane = getattr(card, "pane", None) if card else None
        if not pane or pane not in activity:
            return None
        return (time.time() - activity[pane]) < uh.GRACE_S

    rows = uh.check(agents, root, now=time.time(),
                    launch_time=launch_time, pane_active=pane_active)
    return rows, uh.render(rows)


def _fold_generic(code: int, extra: int) -> int:
    """Fold an extra domain's exit into doctor's. The constants are ordered
    OK(0) < REFUSED/actionable(1) < CANNOT_TELL(2), and that ordering is exactly
    the dominance doctor wants — could-not-tell outranks a fault outranks clean —
    so a plain max is the fold, no special-casing. Same reasoning as _fold_socket,
    which only needs its own branch because a WRONG socket is REFUSED regardless
    of the tool rows."""
    return max(code, extra)


def _fold_socket(code: int, verdict, doc) -> int:
    """A WRONG socket is ACTIONABLE (1), and it outranks a clean tool report: with
    it, every other answer st gives about the fleet is wrong in the confident
    direction. UNKNOWN forces could-not-tell (2) for the same reason the
    self-check does — a report you cannot trust is worse than one that says fix
    this."""
    if verdict == doc.SOCKET_WRONG:
        return REFUSED
    if verdict == doc.SOCKET_UNKNOWN and code == OK:
        return CANNOT_TELL
    return code


def _doctor_exit(doc, healths, self_h) -> int:
    """Fold the self-check into doctor's exit code, keeping its meanings:
    0 clean · 1 actionable · 2 could-not-tell. UNCERTAINTY DOMINATES — a report you
    cannot trust is worse than one that says "fix this" — so a self-check that
    could not read its own metadata forces 2 even when every tool row is green
    (dearing's requirement 2: it must fail toward cannot-tell).
    """
    base = doc.exit_code(healths)
    if self_h is None:
        return base
    if self_h.verdict == selfcheck.CANNOT_TELL or base == CANNOT_TELL:
        return CANNOT_TELL
    if self_h.verdict == selfcheck.BROKEN:
        return max(base, REFUSED)
    return base


def _cmd_role(a) -> int:
    """role set <agent> <role> [--reports a,b] — GENERATIVE.

    Writes the card AND emits the stop-hook routing in one operation, so a lead
    card and its routing cannot disagree. Refuses (exit 1) on any rule violation
    — orphaned reports, a lead under a lead (depth 2), an unknown agent — BEFORE
    writing anything, so a bad hierarchy never half-lands.
    """
    from . import tier
    reports = [r.strip() for r in a.reports.split(",") if r.strip()]
    try:
        plan = tier.role_set(_registry(a), a.agent, a.role,
                             reports=reports, dry_run=a.dry_run,
                             catalog=_catalog(a), root=a.root)
    except (LookupError, ValueError, CapabilityError) as e:
        # CapabilityError (aegis-w5l9): the new role needs a stop capability the
        # card's harness lacks. role_set raised it BEFORE writing, so this refusal
        # genuinely leaves the card and its settings untouched — same shape as the
        # hierarchy refusals above.
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    print(("  would write:" if a.dry_run else "  wrote:"))
    print(plan.render())
    if a.dry_run:
        print("\n  --dry-run: nothing written.")
        return OK
    # GENERATIVE (#6): emit each written role's settings in the SAME operation as
    # the card, so "declaring a role emits its stop hooks" is literal — the card
    # and its hooks cannot drift. This is the CONTENT st new's launch reads.
    #
    # PER (HARNESS, ROLE), not per role: which artifact a card reads is decided
    # by the program it runs, so a store with a codex lead and a claude lead
    # needs both files written. Grouping by the harness the CARDS name means we
    # never emit an artifact for a program nobody in this write asked for.
    # PER (HARNESS, ROLE) WITH THE CARDS' WORKSPACES, because provision needs
    # both halves: the artifact is per (harness, role), while codex's trust
    # record is per WORKSPACE, which is per AGENT (aegis-wc43h). Grouping to the
    # pair keeps a role's config carrying exactly its own agents' workspaces —
    # emitting every workspace into every role's home would record trust for
    # directories those agents never open.
    #
    # THE WORKSPACE COMES FROM THE REGISTRY, NOT FROM plan.writes. A write in the
    # plan is a PARTIAL — it carries the fields role_set is changing, so its
    # `workspace` is None even for a card that has one on disk. Reading it off
    # the plan silently recorded trust for nothing and the dialog still appeared:
    # a fix that emits no error and changes no behaviour, which is the exact
    # shape of the defect it was written to close. Read back AFTER the cards are
    # written, so this sees what the launch will actually use.
    reg_after = _registry(a)
    by_pair: dict[tuple[str, str], set[str]] = {}
    for ag in plan.writes:
        key = (harness_mod.name_for(ag, root=a.root), ag.role)
        by_pair.setdefault(key, set())
        try:
            ws = getattr(reg_after.get(ag.name), "workspace", None)
        except (LookupError, OSError):
            ws = getattr(ag, "workspace", None)
        if ws:
            by_pair[key].add(str(ws))
    for harness_name in sorted({h for h, _ in by_pair}):
        program = harness_mod.get(harness_name)
        for role in sorted(r for h, r in by_pair if h == harness_name):
            paths = _emit_role_settings(a.root, {role}, harness_name=harness_name)
            for path in paths:
                print(f"  hooks   {path}")
            # A harness may need more than the file (codex links the operator's
            # credentials into the home it just wrote, and records the workspaces
            # as trusted). Notes are the operator's to act on and NEVER fail the
            # write — the cards and the hooks are already on disk, and a
            # `role set` that succeeded must not report a failure.
            for path in paths:
                for note in program.provision(
                        str(path), root=a.root,
                        workspaces=sorted(by_pair[(harness_name, role)])):
                    print(f"  ⚠ {note}", file=sys.stderr)
    _report_who_the_rewrite_did_not_reach(a, {ag.role for ag in plan.writes})
    return OK


def _report_who_the_rewrite_did_not_reach(a, roles: set[str]) -> None:
    """aegis-nipg item 2: WRITING A SETTINGS FILE IS NOT DEPLOYING IT.

    Emitting settings changes bytes on disk and reaches NOBODY already running —
    `--settings` is read once, at launch. So the operator who just changed the
    hooks has, at this moment, changed nothing about the live fleet, and until now
    the command told them the opposite: it printed the paths it wrote and exited
    0, which reads as done.

    Both halves of the incident that produced this were invisible for exactly this
    reason. A Stop-hook FIX was emitted and two live agents kept the broken hook,
    staying deaf for the next hour. A PreToolUse guard that hard-blocks every edit
    was emitted and the fleet stayed green for half an hour — not because the
    guard was safe, but because nobody had relaunched into it; the first agent
    restarted, for an unrelated reason, found it with its body.

    So this prints at the moment of the change, unprompted. `st crew` can answer
    the same question, but only if you think to ask it, and nobody in that
    incident had any reason to. This is the half that does not require suspicion.

    Best-effort and never fatal: it reports on a mutation that has ALREADY
    succeeded and been printed. If the registry or tmux is unreachable we say we
    could not tell, and still exit 0 — a report that could turn a completed
    `role set` into a failure would be a worse bug than the one it warns about.
    """
    # EVERYTHING that can reach outside this process is inside the try, not just
    # the registry read. The recovered version guarded only `_registry(a).all().exact()`
    # while `_settings_reach` goes on to call `panes.exists()` per agent — so an
    # unreachable tmux raised straight out of a role set that had ALREADY written
    # the cards and emitted the hooks. Caught by test_report_is_never_fatal_when_
    # it_cannot_look. The docstring above promised "best-effort, never fatal"; it
    # was not, and a traceback there would tell an operator their hook emission
    # failed when it had in fact succeeded — the opposite of the reassurance this
    # function exists to give.
    try:
        panes = _panes(a)
        agents = _registry(a).all().exact()
        agents = [ag for ag in agents if ag.role in roles]
        stale, unknown = _settings_reach(a, panes, agents)
    except Exception as e:
        print(f"  ? could not tell which live agents this reached ({e}) — "
              f"check `st crew`.", file=sys.stderr)
        return
    # NOTE on the filter above: `role set franklin worker` emits
    # worker.settings.json and nothing else, so a stale administrator is genuinely
    # stale but was NOT missed by THIS rewrite — and this function claims, by its
    # own name, to report who the rewrite did not reach. Saying "not deployed to
    # sattler" after a write that never touched sattler's file is the same
    # over-claim, one level down, that this whole change is about.
    if not stale and not unknown:
        return
    print()
    if stale:
        print(f"  ⚠ NOT DEPLOYED to {len(stale)} live agent(s): {', '.join(stale)}")
        print(f"    They are still running the settings they launched with. The "
              f"file you just wrote reaches")
        print(f"    them only on relaunch: `st stop <agent> && st new <agent>`.")
    if unknown:
        print(f"  ? {len(unknown)} live agent(s) have no launch stamp, so whether "
              f"this reached them is UNKNOWN:")
        print(f"    {', '.join(unknown)}")
        print(f"    Treat as not-reached until relaunched — unknown is not fine.")


def _emit_role_settings(root: Path, roles: set[str],
                        harness_name: str | None = None) -> list[Path]:
    """Write each role's settings artifact FOR ONE HARNESS. Idempotent —
    settings are per-role (all workers on one harness share one file), so
    re-emitting is a no-op rewrite. Returns the paths written.

    harness_name defaults to Claude Code, so a caller that says nothing gets
    exactly what this always wrote. A store running two programs gets two
    artifacts per role, in two names the harnesses choose — which is why the
    caller loops over the (harness, role) pairs its cards actually name rather
    than over roles alone (_cmd_role).
    """
    program = harness_mod.get(harness_name)
    sdir = Path(root) / "settings"
    written = []
    for role in sorted(roles):
        p = sdir / program.settings_name(role)
        # mkdir per artifact, not once for sdir: a harness's name may carry
        # directories (codex needs a DIRECTORY per role — CODEX_HOME names one).
        p.parent.mkdir(parents=True, exist_ok=True)
        # Pass the root: the hook must reach THIS store, not cwd/.shanty (the
        # agent's own workspace, which has none) — see _stop_cmd.
        emitted = settings_for_role(role, root=root, harness_name=harness_name)
        # MERGE, NEVER CLOBBER (GitHub #15, #16). This was an unconditional full
        # overwrite, so anything an operator added — a permission, an env var, a
        # SessionStart self-prime — was silently dropped on the next `roles set`.
        # cli.md tells the reader to wire their own SessionStart hook; the emitter
        # then erased it, which made the documented escape hatch unkeepable.
        #
        # st OWNS the hook EVENTS it emits and replaces those wholesale (a stale
        # stop direction must never survive a rewrite); every other key, and every
        # hook event st does not emit, is the operator's and is preserved. The
        # merge AND the serialization are the harness's render() — one call,
        # because they are one decision and this emitter must not learn a second
        # file format to keep the rule.
        p.write_text(program.render(emitted, _read_text(p)))
        written.append(p)
    return written


def _read_text(path: Path) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


# _merge_settings MOVED to harness.merge_one_level (the merge rule is st's, but
# it has to run inside the harness's render() — the operator's keys can only be
# preserved by something that can parse the format they are written in, and this
# emitter deliberately knows only one of the two).


def _cmd_context(a) -> int:
    """what code should I be looking at?  (docs/adapters.md:89)

    THE EXIT CODES ARE THE FEATURE. Read-only, so there is nothing to --dry-run:
    the reason dispatch needs one is that it writes; this cannot.

        0  did it       — bobbin answered. The list may be EMPTY, and empty is
                          an answer: I asked, nothing matched.
        1  refused      — a precondition failed (no query). We did not ask.
        2  could not tell — bobbin was unreachable / unparseable / absent.

    2 and 0-with-nothing print differently and exit differently ON PURPOSE. They
    are the same bytes and opposite facts: "there is nothing there" vs "I could
    not look". Collapsing them is the defect this command exists to not have —
    a cheerful empty result from a service that is DOWN is how a 429 became 32
    fake findings.
    """
    # Imported here, not at module top: cli is not core, but keeping first-class
    # backends out of the import path until they are actually asked for is the
    # habit the leak test enforces one layer down.
    from .bobbin import BobbinContext, NoContext
    from .protocols import ContextUnavailable

    query = " ".join(a.query)
    ctx = NoContext() if a.none else BobbinContext(repo=a.repo, mode=a.mode)

    try:
        answer = ctx.relevant(query, a.budget)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except ContextUnavailable as e:
        # Say WHICH failure, in bobbin's own words. "unavailable" alone is a shrug.
        print(f"could not tell: {e}", file=sys.stderr)
        return 2

    # Displaying the available context is useful even when a budget capped the
    # search. The explicit accessor records that this caller accepts a lower
    # bound; the adjacent note keeps a top-N page from looking exhaustive.
    hits = answer.at_least()
    note = answer.note()
    if note:
        print(note, file=sys.stderr)

    if not hits:
        # THREE kinds of "nothing", and they must not wear the same sentence.
        # I wrote "asked, nothing matched" for both of these first and it was a
        # lie for the none-adapter, which never asked — the exact conflation this
        # command exists to prevent, in the command that prevents it. Exit code
        # alone is not enough: an operator reading stdout must be able to tell.
        if a.none:
            print(f"no context adapter configured (none) — did not look for {query!r}")
        else:
            print(f"no context found for {query!r} (bobbin answered; nothing matched)")
        return 0

    for h in hits:
        loc = f"{h.path}:{h.lines}" if h.lines else h.path
        tag = f" [{h.repo}]" if h.repo else ""
        print(f"{loc}{tag}  {h.name}".rstrip())
    return 0


def _cmd_inbox(a) -> int:
    """inbox — put a message in an agent's inbox, or read your own. THREE modes,
    and the positional shape tells them apart:

        st inbox <agent> <message...>   SEND (send-keys; -d persists first)
        st inbox [agent]                READ — list the unread. Marks nothing.
        st inbox --count [agent]        the machine-readable count (one integer)
        st inbox --read [agent]         ACK — mark all my unread messages read
        st inbox --read-id ID [agent]   ACK — mark only named unread IDs read

    Reading and acking are SEPARATE (inbox.py). `st inbox` shows you what is
    there and changes nothing; `--read` is the act. That split is the same one
    events.py makes between pending() and drain(), and it exists for the same
    reason: `--count` is polled by a status bar every few seconds, and a read
    that consumed what it reported would destroy the delivery it was reporting on.

    ROUTINE SEND (default) — Stiwi, 2026-07-16: "st mail should just be tmux send keys."
    There is no bus, no queue, no store — nothing between the sender and the pane.
    We measured what wrapping it costs: 47 nudges sat queued for a mayor that does
    not exist, oldest 25 days, across FIVE spellings of the recipient — a queue
    accepts a message for a reader that will never come. send-keys cannot: the
    pane is there or it is not, and you are told which. Failure modes stay the two
    honest ones — REFUSED (no agent / no pane), CANNOT_TELL (pane named but gone).

    DURABLE (-d) — the gap #7 closes, and the inbox is what CLOSED it. Routine
    send-keys VANISHES if the recipient is down, which is wrong for a must-survive
    message (a handoff, a protocol step). gt mail's durability is a bead+Dolt
    commit; parity here is: PERSIST to the INBOX first (the survival guarantee),
    THEN best-effort live send for immediacy. Persist-first is deliberate — the
    store is the source of truth, the live send is a bonus.

    Until the inbox existed, durable mail persisted a tracker item and NOTHING
    EVER READ IT BACK: the recipient was told "you'll pick it up on your next
    prime", and prime showed it on the PLATE, where it evicted their actual work
    (the plate holds one item). Both halves of that are fixed here — the message
    goes to the inbox, `st inbox` is the read side, and inbox.is_message keeps it
    off the plate.

    The ruling: beads-parity on the SHARED store, NOT a dedicated store.
    Honoured by the BACKEND SWITCH, not by a hardcoded store: `--backend beads
    --repo <repo>` gives TrackerInbox (a real bead, surviving cross-session and
    cross-host); the portable files default gives FilesInbox, a lesser-but-real
    local durability. We PRINT where it landed so the durability is never ambiguous.

    Durable exit codes:
      REFUSED (1)      no such agent — OR the message is too long for the durable
                       inbox. On the beads backend a message maps to a tracker
                       item whose TITLE holds it, and bd caps a title at 500 chars
                       (so ~493 of body). The inbox is a THIN POINTER CHANNEL, not
                       a document store: a real escalation goes in a BEAD, and the
                       inbox carries the pointer. The refusal names the remedy and
                       is a REFUSED (permanent), never a CANNOT_TELL (aegis-csuo).
      CANNOT_TELL (2)  could NOT persist (store unreachable) — the survival
                       guarantee failed, so we do NOT downgrade to a silent
                       routine send and report success
      OK (0)           persisted (+ delivered live if the pane was there)
    """
    # A send flag with nothing to send is a typo, not a request to read somebody's
    # inbox. Say so rather than quietly doing the other thing.
    if not a.message and (getattr(a, "durable", False) or a.dry_run) \
            and not (getattr(a, "count", False) or getattr(a, "read", False)
                     or getattr(a, "read_id", [])):
        print("  refused: nothing to send. `st inbox <agent> <message...>`.",
              file=sys.stderr)
        return REFUSED
    # READ MODES: they take no message, and the agent defaults to ME.
    if (getattr(a, "count", False) or getattr(a, "read", False)
            or getattr(a, "read_id", []) or not a.message):
        import os
        me = a.agent or os.environ.get("SHANTY_AGENT")
        if not me:
            print("  refused: no agent. `st inbox <you>` or set $SHANTY_AGENT.",
                  file=sys.stderr)
            return REFUSED
        return _inbox_read(a, me)

    msg = " ".join(a.message)
    # ATTRIBUTE THE SENDER (Stiwi, 2026-08-02): "crew comms could be more clear
    # as to who is saying what, some of those msgs could seem as they're from me."
    #
    # send-keys types into the recipient's pane at the SAME prompt the human uses,
    # so an unattributed agent message is INDISTINGUISHABLE FROM THE OPERATOR. That
    # is not a cosmetic problem: Stiwi's word carries authority no agent has —
    # approvals, one-way doors, permission to push a public repo, standing
    # directives. An unsigned coordinator message can be read as an operator
    # approval, and the recipient has no way to tell.
    #
    # Measured the same day: arnold's pane held the line "got the telegram,
    # confirming receipt — close 6te9n" — first person, naming the bead and the
    # action. It was model-generated GHOST text, and it read exactly like Stiwi
    # confirming the one fact aegis-6te9n was blocked on. Attribution does not fix
    # ghosts, but it makes "no prefix" a visible anomaly rather than the norm.
    #
    # Prefix only when we KNOW who is sending. An unattributable send stays bare
    # rather than claiming a name it cannot support — inventing a sender is worse
    # than omitting one.
    #
    # THE FORMAT MOVED OUT (aegis-5vxmz). It was an inline f-string here while
    # inbox was the only attributed path; it is now attribution.attribute,
    # because dispatch and every st tend push sign their sends too and a security
    # marker that is written twice is a security marker that can drift. The
    # behaviour is unchanged — the two differential tests below still hold.
    # KEEP WHAT THE CALLER ACTUALLY TYPED. The cap is checked against the
    # ATTRIBUTED text, so the refusal below has to be able to say how much of the
    # overrun is st's own signature — see _inbox_durable.
    typed = msg
    panes = _panes(a)
    sender, sender_ok = _verified_sender(a, panes)
    if not sender_ok:
        print("  refused: message sender identity could not be verified; "
              "nothing was sent", file=sys.stderr)
        return REFUSED
    msg = attribute(msg, sender)
    try:
        agent = _registry(a).get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if getattr(a, "durable", False):
        return _inbox_durable(a, agent, msg, panes, typed=typed)

    # ROUTINE — unchanged. send-keys only, ephemeral.
    if agent.pane is None:
        print(f"  refused: {agent.name} has no pane in the registry", file=sys.stderr)
        return REFUSED
    if a.dry_run:
        print(f"  would: send-keys -> pane {agent.pane}")
        print(f"  would: {msg}")
        print("\n  0 writes. 1 send-keys.")
        return OK
    if not panes.exists(agent.pane):
        # Do NOT send into the void and report success. The pane is named and
        # absent — that is "could not tell", not "delivered".
        print(f"  could not tell: pane {agent.pane} is not there (agent down?)",
              file=sys.stderr)
        return CANNOT_TELL
    try:
        panes.send(agent.pane, msg)
    except PaneNotAgent as e:
        # THE HAZARD THIS EXISTS FOR (aegis-ikj4t): its runtime has exited, so
        # the pane is a login shell and typing here would EXECUTE the message as
        # a shell command. Observed live — another agent's escalation text and an
        # ack recipe ran in bash. Nothing destructive ran by luck of the wording,
        # not by design. Refuse, and say the message was NOT delivered.
        print(f"  refused: {e}", file=sys.stderr)
        print(f"  remedy: st new {agent.name}, or use `st inbox -d` so the "
              f"message survives until it is back.", file=sys.stderr)
        return REFUSED
    # READ IT BACK. Sending the keystrokes is not delivering the message, and
    # this line reported the first as if it were the second (aegis-wcjuz): a
    # long body absorbed as a paste sits in the input box unsubmitted while the
    # sender is told it landed. That is the same false-success family as the
    # other checks this fleet has already paid for — the tool reported exactly
    # what it did, and what it did was not what the caller asked for.
    #
    # Best-effort and one-directional: it can only DOWNGRADE this to an honest
    # could-not-tell. A pane we cannot capture, or a program whose stranded
    # signature nobody has measured, leaves the old behaviour untouched.
    if _looks_stranded(panes, agent.pane):
        print(f"  could not tell: typed {len(msg)} chars into {agent.pane} but "
              f"they are STILL IN ITS INPUT BOX, unsubmitted — the agent has "
              f"not seen this. Do NOT assume it was read.", file=sys.stderr)
        return CANNOT_TELL
    print(f"  -> {agent.name}    sent to pane {agent.pane}")
    return OK


def _looks_stranded(panes, pane: str) -> bool:
    """Did what we just typed stay in the box? Never raises, never blocks a send.

    The settle is what makes this answerable at all: a TUI repaints after input,
    so capturing immediately reads the frame BEFORE the box was drawn and every
    message would look delivered. Short, because this runs on the interactive
    send path and a message that submits normally must not feel slow.
    """
    from .runtime import input_stranded
    try:
        time.sleep(0.35)
        return input_stranded(panes.capture(pane))
    except Exception:
        # A capture failure is not a finding. Reporting "stranded" because we
        # could not look would manufacture the opposite false answer.
        return False


def _inbox_durable(a, agent, msg: str, panes, typed: str | None = None) -> int:
    """Persist, deliver live when possible, then retire that delivered pointer."""
    # BEADS BY DEFAULT for -d (dearing, qdal.2 follow-up). `-d` is the flag you
    # reach for when the message MUST survive your session dying. A local files
    # store survives the session but NOT the host, not a clone being cleaned, and
    # is invisible to every `bd` query the rest of the crew uses to find it — so
    # a files default silently delivers the weaker half of the only guarantee the
    # flag exists to make. Printing where it landed is real mitigation, and it is
    # why this was a default worth changing rather than a bug: the person who most
    # needs -d is at a session tail and is not reading output carefully.
    # `--backend files` stays explicit and useful — when the store is unreachable,
    # local-and-known beats the CANNOT_TELL that persist-first would return.
    backend = _backend(a, default="beads")
    live = agent.pane is not None and panes.exists(agent.pane)
    if a.dry_run:
        print(f"  would: deliver a durable message to {agent.name}'s inbox via {backend}")
        print(f"  would: {'+ live send-keys -> ' + agent.pane if live else 'no live send (recipient down); survives in the inbox'}")
        print("\n  1 durable write." + (" 1 send-keys." if live else " 0 send-keys."))
        return OK
    # PERSIST FIRST — the survival guarantee. If this cannot be done, the durable
    # promise cannot be kept; say so (2) rather than silently downgrade to routine.
    try:
        box = _inbox(a, default="beads")
        item = box.deliver(a.agent, msg, frm=_me(a))
    except MessageTooLong as e:                  # PERMANENT: the message will never fit
        # Not a "could not tell" (2) — the store is fine; the message is too long,
        # and retrying it unchanged will fail identically. That is a REFUSED (1)
        # the agent must act on, and the exception says exactly how (aegis-csuo).
        print(f"  refused: {e}", file=sys.stderr)
        # NAME THE PART THE CALLER DID NOT WRITE. The cap is measured against the
        # ATTRIBUTED text, so a sender who trims to the advertised budget is
        # refused AGAIN, with a number they cannot reconcile against anything they
        # typed. Measured twice in a row while closing aegis-ftmfn: 491 typed chars
        # came back as "durable message is 505 chars; this inbox carries at most
        # 493", and the obvious repair — trim to 493 — fails identically.
        #
        # Nothing was lying. The refusal reported exactly what it measured, and it
        # did not answer the question the caller had, which is "how long may MY
        # text be". That is the same shape as every other trap this repo keeps
        # finding: a true report of the wrong quantity.
        # SUBTRACT IN THE SAME UNIT THE BUDGET IS QUOTED IN (aegis-2bjel). The
        # signature is ASCII today, so bytes and chars coincide FOR IT — and
        # relying on that is how the mismatch got here in the first place, so
        # the measure follows the exception's own unit rather than a habit.
        unit = getattr(e, "unit", "chars")
        size = (lambda t: len(t.encode("utf-8"))) if unit == "bytes" else len
        sig = msg[:len(msg) - len(typed)] if typed is not None else ""
        overhead = size(sig)
        if overhead and e.budget is not None:
            typed_size = size(typed)
            extra = (f" ({len(typed)} characters, but the cap counts bytes)"
                     if unit == "bytes" and typed_size != len(typed) else "")
            print(f"    minus the {sig!r} signature st adds: your budget is "
                  f"{e.budget - overhead} {unit}, you typed {typed_size}{extra}.",
                  file=sys.stderr)
        return REFUSED
    except beads_mod.BeadsValidationError as e:
        # bd REFUSED it, permanently — not a store outage (aegis-2bjel). This
        # used to fall into the handler below and print "could not tell", which
        # names a transient condition and invites a retry that reproduces the
        # failure exactly. Reported with bd's OWN error rather than whatever
        # happened to be on the first line of its stderr.
        print(f"  refused: durable persist for {agent.name} was REJECTED by the "
              f"store, not lost by it: {e}", file=sys.stderr)
        return REFUSED
    except Exception as e:                        # bd/store unreachable, etc. — TRANSIENT
        print(f"  could not tell: durable persist FAILED for {agent.name} "
              f"({type(e).__name__}: {str(e)[:100]}). Nothing guaranteed to "
              f"survive; not downgrading to an ephemeral send.", file=sys.stderr)
        return CANNOT_TELL
    # Persisted. Now best-effort immediacy — never fatal to the durable result.
    # WRAPPED (GitHub #26): the send was outside the try, so a tmux failure after
    # a SUCCESSFUL persist exited 1 with a traceback. Every consumer reading the
    # exit code then concluded the message was lost, when it was durably stored
    # and would be read. "Best-effort" has to mean it in the code, not only in the
    # comment.
    if live:
        try:
            panes.send(agent.pane, msg)
            if _looks_stranded(panes, agent.pane):
                print(f"  -> {agent.name}    delivered to inbox as {item.id} ({backend}); "
                      f"live input is still stranded — the open pointer survives for `st inbox`.")
                return OK
        except Exception as e:                    # noqa: BLE001 — never fatal here
            print(f"  -> {agent.name}    delivered to inbox as {item.id} ({backend}); "
                  f"the live nudge FAILED ({type(e).__name__}: {str(e)[:80]}) — "
                  f"the message survives and they read it with `st inbox`.")
            return OK

        try:
            box.mark_read(agent.name, ids=[item.id])
            print(f"  -> {agent.name}    delivered to inbox as {item.id} ({backend}) "
                  f"+ live to {agent.pane}; pointer closed on live delivery")
        except Exception as e:                    # noqa: BLE001 — delivery already succeeded
            print(f"  -> {agent.name}    delivered to inbox as {item.id} ({backend}) "
                  f"+ live to {agent.pane}; pointer close FAILED "
                  f"({type(e).__name__}: {str(e)[:80]}) — it remains open for `st inbox`.")
    else:
        print(f"  -> {agent.name}    delivered to inbox as {item.id} ({backend}); "
              f"recipient not live — they read it with `st inbox`.")
    return OK


def _inbox_read(a, me: str) -> int:
    """The READ side of the inbox: list, count, or ack. Whose inbox = `me`.

    --count is the machine-readable one and prints ONE integer, nothing else. The
    plain list prints a human table and MARKS NOTHING; --read is the separate,
    explicit ack. An inbox that emptied itself because a status bar looked at it
    would be worse than no inbox: the recipient would never learn what was said.
    """
    try:
        # SAME DEFAULT AS THE DURABLE WRITE. The write side defaults to beads
        # (dearing, qdal.2); if the read side defaulted to files, a bare
        # `st inbox -d` would deliver to beads and a bare `st inbox` would show
        # an empty files inbox — the sender is told it persisted, the recipient
        # is told they have nothing, and BOTH are reading a real answer from the
        # wrong store. That is the send-on-one/read-on-another split this
        # module's own docstring exists to forbid. An inbox you cannot read is
        # not an inbox, so the two defaults move together or not at all.
        box = _inbox(a, default="beads")
        unread = box.unread(me)
    except Exception as e:
        # Could-not-look is never "you have no mail" (the whole reason exit 2
        # exists here). --count stays silent on stdout so a parser sees nothing.
        print(f"  could not tell: inbox unreadable: {e}", file=sys.stderr)
        return CANNOT_TELL

    if getattr(a, "count", False):
        print(len(unread))
        return OK

    read_ids = getattr(a, "read_id", [])
    if getattr(a, "read", False) or read_ids:
        if read_ids:
            unread_ids = {m.id for m in unread}
            missing = sorted(set(read_ids) - unread_ids)
            if missing:
                print("  refused: message ID(s) are not unread for "
                      f"{me}: {', '.join(missing)}. Nothing marked read.",
                      file=sys.stderr)
                return REFUSED
        marked = box.mark_read(me, ids=read_ids or None)
        # PRINT WHAT IT CONSUMED (GitHub #14). --read is the ACK, and it is the
        # only thing that consumes a message. A count is not the message: the
        # bodies are gone from the unread set the instant this returns, so a
        # surface that prints "marked 3" and discards them has destroyed the only
        # copy the reader had a right to see.
        for m in marked:
            frm = f" from {m.frm}" if getattr(m, "frm", None) else ""
            print(f"\n  {m.id}{frm}")
            for line in (m.body or "").splitlines() or [""]:
                print(f"    {line}")
        print(f"\n  marked {len(marked)} message(s) read for {me}.")
        return OK

    print()
    if not unread:
        print(f"  {me}: no unread messages.")
    else:
        for m in unread:
            src = f" from {m.frm}" if m.frm else ""
            print(f"  {m.id}{src}  {m.body}")
        print(f"\n  {len(unread)} unread. `st inbox --read` to ack all, or "
              "`st inbox --read-id <id>` to ack selectively.")
    print()
    return OK


def _cmd_task(a) -> int:
    """task creates a work item and PRINTS ITS ID, because the id is the product.

    Step 1 of the three steps (create -> send -> fetch). The id is the whole
    reason step 2 has anything to say.
    """
    title = " ".join(a.title)
    if a.dry_run:
        print(f"  would: create {title!r}" + (f" assignee={a.assignee}" if a.assignee else ""))
        print("\n  0 writes.")
        return OK
    try:
        item = _tracker(a).create(title, assignee=a.assignee)
    except Exception as e:
        print(f"  could not tell: tracker create failed: {e}", file=sys.stderr)
        return CANNOT_TELL
    print(f"  {item.id}    {item.title}")
    return OK


def _lead_status(registry, panes):
    """The reachability predicate anchor reports on — THE ROUTER'S OWN.

    stop_event._lead_is_up is what route_stop is actually asked at emit time, so
    binding anchor to it is what makes section 3 a report of the mechanism rather
    than a second opinion about it (aegis-j1dzp). It also drags in the meaning
    that matters: `up` = WILL DRAIN, not `a pane answers to that name`. Under the
    old pane-exists probe anchor printed "up. Your stop events go to them." about
    a lead the router treats as unreachable — the live-but-deaf case that lost
    seven workers' events (aegis-0v97).

    Takes the ALREADY-BUILT registry and panes rather than an args namespace: it
    must be the same registry anchor resolves identity from (two reads of a
    backend can disagree), and under `--registry quipu` building a second one
    means a second graph client on the most-used surface in the harness.

    Imported inside the call because stop_event pulls in the runtime, tmux and
    tracker layers, and that import cost would otherwise be paid by every
    session start. Returns None if the import fails, and anchor falls back to
    pane-exists — degraded, but never a claim we could not measure.
    """
    try:
        from .stop_event import _lead_is_up
    except Exception:
        return None
    return _lead_is_up(registry, panes)


def _cmd_anchor(a) -> int:
    """anchor is a PURE READ. Note what is NOT here: no _wire(), because the
    Dispatcher exists to write. anchor resolves its own reads and nothing else.

    --short / --events / --harness are the machine-readable modes (the status
    bar). They take the SAME agent resolution and the SAME backends as the human
    render — a status bar reading a different plate than the anchor would be worse
    than no status bar — and they print the value ALONE. Errors still go to stderr
    with the usual exit codes: an empty stdout means "nothing to show", and a
    caller that cannot distinguish that from "I could not look" has the exit code.
    """
    me = _me(a)
    if not me:
        print("  refused: no agent. `st anchor <you>` or set $SHANTY_AGENT.",
              file=sys.stderr)
        return REFUSED
    if getattr(a, "events", False):
        return _anchor_events(a, me)
    if getattr(a, "harness", False):
        return _anchor_harness(a, me)
    registry, panes = _registry(a), _panes(a)
    try:
        p = do_anchor(me, registry, panes, plate=_plate(a),
                      lead_status=_lead_status(registry, panes))
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except Unreachable as e:
        # NOT success, NOT failure. "I could not look" must never say "fine".
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    # PUBLISH the resolved plate for non-Python consumers (aegis-qdjof). This
    # sits in the CLI and NOT in anchor(), whose docstring makes "reads only" a
    # contract — the publish is bookkeeping this command does around a read, not
    # a thing the resolver learned to do. Fail-silent by construction, so it
    # cannot change what anchor prints or returns.
    #
    # Publishing on the UNREACHABLE path is deliberately skipped (we are above
    # the return): a backend we could not reach tells us nothing about the
    # plate, and writing "unknown" would erase a plate that is probably still
    # correct. Missing-or-stale already reads as UNKNOWN downstream, so silence
    # here costs nothing and a wrong write would cost a record.
    plate_publish.publish(Path(a.root), me, p.item)
    if getattr(a, "short", False):
        # The id, or nothing. An empty plate prints an empty line's worth of
        # NOTHING — not "nothing.", not a dash: the consumer renders the segment
        # empty, which is what an empty plate looks like.
        if p.item:
            print(p.item.id)
        return OK
    print()
    print(p.render())
    print()
    return OK


def _read_note(a) -> str | None:
    """--note / --note-file -> the note text, or None. Raises OSError on a bad file.

    --note-file exists because a note is prose, and prose typed into a shell as
    `--note "..."` gets `$(...)` and backticks EXPANDED before st ever sees it —
    the aegis-0214 footgun, where the message either runs or is silently deleted
    while the tool reports success. A file (or stdin) is inert.
    """
    if getattr(a, "note_file", None) is not None:
        if str(a.note_file) == "-":
            return sys.stdin.read()
        return a.note_file.read_text()
    return getattr(a, "note", None)

def _anchor_harness(a, me: str) -> int:
    """`st anchor --harness` — which agent program is this card's?

    Answers "claude" for a card with no harness field, because that IS the answer
    (harness.name_for) — not blank. A status-bar segment that went empty for every
    existing card would read as "no harness", which is a different and false claim.
    """
    try:
        card = _registry(a).get(me)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    print(harness_mod.name_for(card, root=a.root))
    return OK


def _anchor_events(a, me: str) -> int:
    """`st anchor --events` — how many stop events am I holding, undelivered?

    THIS MUST NOT DRAIN. drain() answers the same question by CONSUMING (it marks
    each event delivered so the destination can idle — the BLOCK-ONCE rail at the
    top of events.py), so a status bar polling drain() every few seconds would
    deliver the tier's events to a status bar and the administrator would never be
    told it had them. Counting is events.pending(): a read that marks nothing.
    """
    print(len(FilesEvents(Path(a.root) / "events").pending(me)))
    return OK


def _cmd_repool(a) -> int:
    """Hand an item back to the pool — the WHOLE hand-back (aegis-ap4gm #1).

    Exit codes match `st go`: 0 done (or already pooled), 1 refused with nothing
    written, 2 the write could not be confirmed — read the bead before retrying.
    """
    d = _wire(a)
    try:
        r = d.repool(a.item, dry_run=a.dry_run)
    except (RepoolRefused, LookupError) as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except TrackerWriteLost as e:
        # The write was attempted and the read-back still disagrees. Do NOT
        # blind-retry: read the bead first — the row may hold half the update.
        print(f"  ⚠ COULD NOT CONFIRM: {e}\n  Read the bead (`br show {a.item}`)"
              f" before retrying — the row may be half-updated.", file=sys.stderr)
        return CANNOT_TELL
    if r.noop:
        print(f"  {a.item} is already open and unassigned — nothing to write.")
        return OK
    frm = f"{r.was_status}/{r.holder or 'UNASSIGNED'}"
    if a.dry_run:
        print(f"  would repool {a.item}: {frm} -> open/unassigned. 1 tracker "
              f"read, 0 writes.")
        return OK
    extra = f" ({r.track_attempts} attempts)" if r.track_attempts > 1 else ""
    print(f"  ✓ {a.item} repooled: {frm} -> open/unassigned, verified by "
          f"read-back{extra}. It is back on `br ready` and feedable.")
    return OK


def _cmd_defer(a) -> int:
    """Park work only when its blocker KIND and reason travel with the state."""
    try:
        if a.reason_file is not None:
            reason = (sys.stdin.read() if str(a.reason_file) == "-"
                      else a.reason_file.read_text())
        else:
            reason = a.reason or ""
    except OSError as e:
        print(f"  refused: could not read --reason-file: {e}", file=sys.stderr)
        return REFUSED
    d = _wire(a)
    try:
        r = d.defer(a.item, a.kind, reason, dry_run=a.dry_run)
    except (DeferRefused, LookupError) as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except TrackerWriteLost as e:
        print(f"  ⚠ COULD NOT CONFIRM: {e}\n  Read the bead (`br show {a.item}`) "
              "before retrying — the status and label may already have landed.",
              file=sys.stderr)
        return CANNOT_TELL
    if r.noop:
        print(f"  {a.item} is already deferred as {r.label} — nothing to write.")
        return OK
    if a.dry_run:
        print(f"  would defer {a.item}: {r.was_status} -> deferred, label "
              f"{r.label}, reason recorded. 2 tracker reads, 0 writes.")
        return OK
    extra = f" ({r.track_attempts} attempts)" if r.track_attempts > 1 else ""
    print(f"  ✓ {a.item} deferred as {r.label}; reason and exactly one blocker "
          f"kind verified by read-back{extra}.")
    return OK


def _verification_registry(a):
    """A quipu client used ONLY to check that a named node exists.

    Deliberately separate from `_registry(a)`, which answers "who is the crew?"
    and may be files- or toml-backed by choice. Node verification is a question
    only the graph can answer, so it asks the graph regardless of which identity
    backend the caller selected — and returns None rather than raising, because
    a dispatch must never fail on the construction of a checker.
    """
    try:
        return QuipuRegistry(root=getattr(a, "root", None))
    except Exception:  # noqa: BLE001 — any failure here means "cannot check"
        return None


def _graph_context(a):
    """Require (per mode), then best-effort verify. Returns (ctx, refusal|None).

    In the default ADVISE mode a missing context warns and is RECORDED as
    missing rather than refused, so the ledger measures the habit instead of
    blocking the fleet's existing callers on the day this ships. A node that the
    graph positively does not hold is refused in BOTH modes: that is a wrong
    claim, not an absent one, and letting it through is how a dispatch comes to
    cite something nobody can look up.
    """
    try:
        ctx = graph_adoption.require(getattr(a, "quipu_node", []),
                                     getattr(a, "no_graph_context", ""))
    except graph_adoption.GraphContextMissing as e:
        if graph_adoption.mode(getattr(a, "root", None)) == graph_adoption.REQUIRE:
            return None, str(e)
        print(f"  ⚠ no graph context — recorded as missing. Name one with "
              f"--quipu-node, or say why with --no-graph-context '<reason>'.",
              file=sys.stderr)
        return graph_adoption.unstated(), None
    try:
        ctx = graph_adoption.verify(ctx, _verification_registry(a))
    except graph_adoption.GraphNodeUnknown as e:
        return None, str(e)
    return ctx, None


def _cmd_go(a) -> int:
    d = _wire(a)
    try:
        note = _read_note(a)
    except OSError as e:
        # A note that cannot be read must NOT degrade to a note-less dispatch:
        # the caveat is the reason the caller used the flag, and sending the work
        # without it is the exact failure aegis-8013 is about.
        print(f"  refused: could not read --note-file: {e}", file=sys.stderr)
        return REFUSED
    # GRAPH CONTEXT IS REQUIRED (aegis-rcyd.1). Checked BEFORE triage and before
    # anything is typed into a pane: a refusal here costs nothing, while a
    # dispatch that reached an agent citing a node nobody can look up cannot be
    # taken back. A graph that cannot be reached does NOT refuse — see
    # graph_adoption for why absence and silence are different answers.
    gctx, refusal = _graph_context(a)
    if refusal:
        print(f"  refused: {refusal}", file=sys.stderr)
        return REFUSED
    if a.dry_run:
        try:
            decision = d.triage(a.item, a.agent, note)
            p = d.go(a.item, a.agent, dry_run=True, note=note, reassign=a.reassign,
                     quipu_nodes=getattr(a, "quipu_node", []))
        except Closed as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        except Blocked as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        except HasOpenBlocker as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        except AlreadyAssigned as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        except GovernorRefused as e:
            # The gate applies to --dry-run TOO. A preview that showed a dispatch
            # the real command would refuse is a preview of the wrong command.
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        except LookupError as e:
            print(f"  refused: {e}", file=sys.stderr)
            return REFUSED
        print(p.render()); print("\n  triage: " + decision.render())
        print("  " + gctx.render())
        graph_adoption.record(a.root, "go", a.agent, a.item, gctx, dry_run=True)
        if a.worktree:
            # Dry-run creates NOTHING (the pure-dry-run rule), so name what a real
            # run would provision without touching disk.
            print(f"  would provision worktree: "
                  f"{worktree_for(_resolve_repo(a.worktree), a.agent)}")
        print("  0 writes. 1 tracker call, 1 send-keys.")
        return OK
    # KEEP CURRENT, MECHANIZED (aegis-4zld). Assignment is a SAFE pull moment —
    # the agent is between items by definition of being dispatched to — so bring
    # its workspace current (ff-only, kit-preserving) BEFORE the item lands and
    # work starts on a stale tree. A refused pull (local dirt, the aegis-43ph
    # condition) does NOT block the dispatch, but it rides INTO the dispatch
    # note so the agent starts knowing its tree may be stale — visible to both
    # ends, silent to neither. Deliberately NOT inside Dispatcher.go: the
    # dispatcher's asserted budget is one read/one write/one send, and a git
    # pull is the launcher's business, not the dispatch protocol's.
    if warn := _keep_current(a, a.agent):
        print(f"  ⚠ {warn}", file=sys.stderr)
        tag = f"[st keep-current: {warn}]"
        note = f"{note} — {tag}" if note else tag
    # ISOLATED WORKTREE for shared-repo work (aegis-h2rr), composed with the
    # keep-current above: the workspace clone is pulled, and if this item touches a
    # SHARED project repo, the agent gets its OWN worktree off it so its index/HEAD
    # cannot be clobbered by another agent (aegis-repg). PROVISION FAILURE REFUSES:
    # dispatching shared-repo work into the shared checkout with no isolation is the
    # exact bug this removes, so a worktree we cannot make is a hard stop, not a
    # degrade. A STALE worktree (rebase refused) is not — it rides into the note
    # like keep-current's does, visible to both ends.
    if a.worktree:
        try:
            wt_path = ensure_worktree(_resolve_repo(a.worktree), a.agent)
        except WorkspaceError as e:
            print(f"  refused: worktree — {e}", file=sys.stderr)
            return REFUSED
        print(f"  worktree: {wt_path}")
        wtag = f"[st worktree: work in {wt_path}"
        if wt_warn := _refresh_worktree(wt_path):
            print(f"  ⚠ {wt_warn}", file=sys.stderr)
            wtag += f" — {wt_warn}"
        else:
            # SAY THE SHA, not just "current" (aegis-ib65p acceptance). "current"
            # is unfalsifiable a day later; a sha lets the agent — or whoever
            # reads the bead afterwards — check what it actually started from.
            ref, _ = upstream_ref(wt_path)
            wtag += f" — current with {ref} @{_short_head(wt_path)}"
        # OPEN PRs RIDE THE DISPATCH TOO (decision 4). This is the half that
        # catches the expensive case: the duplication that prompted this bead was
        # an unmerged DRAFT, so no amount of branch-tip currency would have shown
        # it. A tree can be perfectly current and still be missing the work.
        prs, pr_err = _open_prs_for(wt_path)
        if pr_err:
            # NEVER SILENCE (decision 4, explicit). "We could not look" must not
            # render as "there are none" — that reads as an all-clear the fleet
            # did not earn.
            wtag += f" — open PRs UNKNOWN: {pr_err}"
        elif prs:
            wtag += (f" — {len(prs)} OPEN PR(s) on this repo, check before you "
                     f"build: {'; '.join(prs[:5])}")
        else:
            wtag += " — no open PRs on this repo"
        wtag += "]"
        note = f"{note} — {wtag}" if note else wtag
    try:
        p = d.go(a.item, a.agent, note=note, reassign=a.reassign,
                 quipu_nodes=getattr(a, "quipu_node", []))
    except Closed as e:
        # Closed is terminal (aegis-vuh33). Nothing written, nothing sent — serving
        # a closed bead reverts it to in_progress and re-does finished work. Reopen
        # deliberately if it must be worked again.
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except Blocked as e:
        # BLOCKED is a DECISION (internal-ref). Nothing written, nothing sent —
        # serving a blocked bead overwrites that status with in_progress and puts
        # an unadvanceable item on a plate, which is what cycles agents. Clear the
        # block deliberately if it is resolved.
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except HasOpenBlocker as e:
        # The item's own `blocks` deps are unmet, read this pass. Nothing written,
        # nothing sent. This comment used to say "`bd ready` already excludes an
        # item with an unmet blocker; the dispatch path now agrees with it" —
        # asserted, never checked, and measured false at least once (aegis-eqhf6).
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except AlreadyAssigned as e:
        # Refuse rather than steal. Nothing written, nothing sent — two agents on
        # one item is duplicated effort no tool ever flags (aegis-uvw5 / 7yeb).
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except GovernorRefused as e:
        # The usage governor's tier (aegis-hdqej). Nothing written, nothing sent.
        # The item is untouched and stays dispatchable — this is a NOT NOW, not a
        # rejection, and the message says on what reading and at what threshold.
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    except PaneNotAgent as e:
        # The pane is a SHELL, not a runtime — its agent has exited (aegis-ikj4t).
        # Nothing was typed and nothing was written, so the item stays
        # dispatchable. Refusing is the whole point: typing a dispatch into bash
        # would EXECUTE it, and the sender would be told the work was assigned.
        print(f"  refused: {e}", file=sys.stderr)
        print(f"  remedy: st new {a.agent}   (then re-run this dispatch)",
              file=sys.stderr)
        return REFUSED
    except TriageRefused as e:
        # #1: pane not ready (in-flight/wedged/high-context). No write, no send.
        print(f"  refused: pane not ready — {e.decision.render()}", file=sys.stderr)
        # #5: a RESTART verdict used to dead-end here — shantytown could diagnose
        # a wedged session and then say nothing about acting on it, because
        # new/stop did not exist. They do now, so the diagnosis names the remedy.
        # We do NOT relaunch automatically: killing an agent as a side effect of a
        # dispatch is exactly the kind of thing that must stay an explicit act.
        if e.decision.action is Action.RESTART:
            print(f"  remedy: st stop {a.agent} && st new {a.agent}   "
                  f"(launcher-relaunch, never handoff — a handoff drops --settings "
                  f"and produces a hookless agent)", file=sys.stderr)
        return REFUSED
    except DispatchedButUntracked as e:
        # Exit 2: the send is a fact, the record is not. Never 0 — a caller that
        # reads 0 here books the dispatch as complete.
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    except SendUnverified as e:
        # #2: we sent, but reading the pane back did NOT confirm it landed. Its
        # docstring pins this to exit 2, and go() ran verify BEFORE the tracker
        # write, so NOTHING was recorded — a human re-dispatches rather than the
        # tracker claiming an assignment that may never have arrived. This must be
        # a clean could-not-tell, NOT an uncaught traceback (found by the
        # full-cycle validation against a real pane).
        print(f"  could not tell: {e} — recorded nothing; re-dispatch.",
              file=sys.stderr)
        return CANNOT_TELL
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    # PUBLISH THE PLATE AT ASSIGNMENT (aegis-qdjof, completing it). This is the
    # moment `(agent -> work item)` becomes authoritative: the payload is on the
    # pane, verify() read it back, and the tracker write landed and was read
    # back. Before this, `plate_publish.publish` had exactly ONE caller —
    # `st anchor` — so a freshly dispatched agent's plate still named its
    # PREVIOUS item, and every yupana guard decision, every spool record and
    # every briefing keyed on it was attributed to the wrong work until the
    # agent happened to anchor. An agent that never anchored never updated.
    #
    # Deliberately AFTER the try/except, on the success path only: a refused or
    # unverified dispatch wrote nothing and sent nothing, and publishing there
    # would put an item on a plate the agent was never given.
    #
    # Fail-silent by construction, so it cannot change what `st go` prints or
    # returns — the same contract the anchor-side publish holds, and for the
    # same reason.
    plate_publish.publish_id(Path(a.root), a.agent, p.item_id)
    # THE ADOPTION ROW, on the success path only (aegis-rcyd.1). A dispatch that
    # was refused or could not be verified did not happen, and counting it would
    # put the denominator out of step with the fleet's actual work. Fail-silent
    # by construction: a measurement must never be able to break the thing it
    # measures.
    graph_adoption.record(a.root, "go", a.agent, a.item, gctx, session=p.pane)
    print(f"  {p.item_id} -> {p.agent}          in progress")
    print(f"  sent to pane {p.pane}")
    print(f"  {gctx.render()}")
    if p.track_attempts > 1:
        # THE LINE THAT MAKES AN INTERMITTENT FAULT COUNTABLE (aegis-8xc5w).
        # go() now reads its tracker write back and re-writes on a verified loss,
        # so this dispatch is CORRECT — the record and the pane agree. Saying
        # nothing would be defensible and would be the mistake: a fault that is
        # silently absorbed is a fault that never gets root-caused, and this one
        # already spent days misattributed to the store because the only thing
        # that ever saw it reported success. Print it where the operator is
        # already looking, on stderr so nothing parsing the outcome line changes.
        print(f"  ⚠ the tracker write did NOT land on the first attempt — it took "
              f"{p.track_attempts} write+read-back rounds. The dispatch is "
              f"recorded (this line means the read-back agreed in the end). Note "
              f"the id and the store: this is the intermittent lost write, caught "
              f"in the act.", file=sys.stderr)
    if p.orphaned_in_progress:
        # ON THE REAL RUN TOO, not only in --dry-run — same argument as
        # unreadable_deps below (aegis-ap4gm). This is the state where the
        # assignee guard is BYPASSED rather than weak: it keys on assignee, and
        # an orphan's is empty, so `st go` accepted one silently tonight.
        print(f"  ⚠ {p.item_id} was in_progress with NO assignee — {p.agent} is "
              f"RESUMING work that was started and handed back, not starting it. "
              f"The assignee guard cannot see this (it keys on a field that is "
              f"empty). If that is not what you meant, the item's status wants "
              f"resetting to `open`.", file=sys.stderr)
    if p.unreadable_deps:
        # ON THE REAL RUN TOO, not only in --dry-run (aegis-kt7jr). A warning
        # that fires only in the preview is a warning for the careful path, and
        # this one exists precisely because nothing else on any path reports it.
        # stderr: it is not the dispatch result, and a caller parsing stdout for
        # the outcome must not have to learn a new line.
        # The bead citation stays in THIS comment and out of the printed string
        # (aegis-kt7jr): shantytown is a public repo and a reader outside this
        # homelab cannot resolve an aegis- id. Caught by the ratchet, not by me.
        print(f"  ⚠ {p.unreadable_deps} dependency row(s) on {p.item_id} are "
              f"UNREADABLE — counted by the tracker, resolvable in no store from "
              f"here. If one is a `blocks` edge, this item was NOT ready and the "
              f"blocker check could not see it.", file=sys.stderr)
    if p.note:
        # Echo the note AS SENT. If flattening changed it, the sender finds out
        # here rather than from a confused worker.
        print(f"  note: {p.note}")
    return OK


def _unassigned_open(a):
    """(count, top_priority_count, note) for OPEN beads in nobody's haul.

    `(None, None, why)` means COULD NOT TELL — never `(0, ...)`. A roster that
    prints "0 unassigned" because bd was unreachable is the could-not-tell-
    rendered-as-fine bug this repo names in every other reader, in the one number
    a coordinator would use to decide there is nothing to route.

    WHY THIS BELONGS ON THE ROSTER (aegis-jqcs3). Hauls feed from READY beads
    ASSIGNED to a worker, so an unassigned bead is queued nowhere: it does not
    self-feed, no stop event advances to it, and it surfaces only if somebody
    runs `bd ready` and picks it by hand. Measured 2026-08-05: 114 of 635 open
    beads had no assignee — a third of the board in nobody's queue, including
    three P1s — while `st crew` reported free/busy and the fleet twice read as
    "nothing dispatchable".

    IT CANNOT EXCLUDE `decision-needed`, AND SAYS SO RATHER THAN PRETENDING.
    A decision-gated bead must not be handed to a worker as implementer work
    (aegis-2og7d), so unassigned is its CORRECT state and it is not unrouted work.
    Filtering it out is impossible here: `WorkItem` carries no labels and the
    beads adapter never parses them, because the Tracker protocol is three
    functions and deliberately does not grow (aegis-gqr8). Measured on the live
    store: 113 unassigned, of which 4 are decision-needed — a 3.5% overcount,
    named in the output so the reader can discount it knowingly instead of
    discovering it and discounting the whole number.

    (The first version of this function DID filter on `it.labels`. It was tested
    against a fixture that had been given a `labels` attribute by the test itself,
    so five green tests proved the filter worked on data that does not exist. The
    live cross-check against `bd` is what caught it — 113 here against 109 there.)
    """
    try:
        trk = _tracker(a, "beads")
        rows = _tracker_items(trk)
    except Exception as e:            # noqa: BLE001 — unreachable/misconfigured store
        return None, None, str(e)[:90]

    n = top = 0
    for it in rows:
        if (getattr(it, "status", "") or "").lower() != "open":
            continue
        if (getattr(it, "assignee", None) or "").strip():
            continue
        n += 1
        pr = getattr(it, "priority", None)
        if pr is not None and pr <= 1:
            top += 1
    return n, top, ""


def _cmd_crew(a) -> int:
    """crew — who exists, what state, what role, WHAT SETTINGS, and WHO IS FREE.

    The settings column exists because `up` was the only health this ever
    reported, and `up` is exactly what a deaf agent looks like: two agents
    sat here reading `up` while their stop hooks resolved against the wrong
    store and every stop event they emitted was discarded. The column answers the
    question `up` cannot — is this agent running the settings we currently
    believe are deployed? — and answers it in three values, because `unknown` is a
    real state and rounding it to `current` would recreate the bug.

    The WORK column answers the only question a dispatcher
    actually has: who can take the next item? `up` is a LAUNCH fact, not a WORK
    fact — an agent three hours into a refactor and an agent sitting at an empty
    prompt both print `up`. The verdict is triage's, unchanged and already
    load-bearing (dispatch.py refuses sends into busy panes); `st crew` simply
    never asked it. Measured cost of not asking: a 5-worker dispatch round fed on
    a handoff's word, with no way to verify it short of `st log` per agent and
    eyeballing the scrape (sattler, 2026-07-19).

    It also answers the roster's OTHER blind spot without inventing anything: the
    work verdict is derived from the PANE, so it is available for the agents that
    have no launch stamp — over half the roster — where the settings column can
    only honestly say `?`. We do NOT backfill a stamp to make that column look
    answered: a stamp records WHICH BYTES an agent launched with, and one we did
    not observe would be a fabricated measurement, which is worse than a blank.
    """
    # --governor answers FIRST, before the registry and the panes are touched.
    # Capacity is a property of the BUDGET, not the roster: a registry that cannot
    # be read must not blank the number that decides whether to dispatch at all.
    # (It is on `crew` rather than a new command because `--count` set the
    # precedent for a status-bar reader here, and the command count is pinned by
    # test_command_count — a new verb is a deliberate widening, this is not.)
    if getattr(a, "governor", False):
        return _crew_governor(a)
    if rules := getattr(a, "check_alert_keepers", None):
        return _check_alert_keepers(a, rules)
    # A self-cycle request exists before tend stops the pane and persists while
    # it relaunches.  It therefore protects the acknowledgement window that
    # would otherwise be delivered to a pane that cannot answer.
    from . import cycle as cycle_mod
    _pending = cycle_mod.Requests(a.root).pending()
    # aegis-7xptd5: a REFUSED request is not a cycle in flight. Both are records
    # in the same file, and printing `cycling` for each is how six stalled agents
    # rendered as six cycles in progress for over an hour.
    cycle_blocked = {}
    for _who, _rec in _pending.items():
        _path, _why = cycle_mod.refusal_summary(_rec)
        if _why:
            cycle_blocked[_who] = (_path, _why)
    cycling = set(_pending) - set(cycle_blocked)
    panes = _panes(a)
    try:
        agents = _registry(a).all().exact()
    except Exception as e:
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    runtime = _runtime(a, panes)
    # --count answers BEFORE the empty-roster line: an empty roster is `0/0`, not
    # a sentence telling a status bar to run `st new`.
    if getattr(a, "count", False):
        return _crew_count(agents, panes, runtime, untracked_root=a.root)
    if not agents:
        print("  no agents. `st new <agent>`.")
        return OK
    launches = _launches(a)
    stops = _stops(a)
    runtime = _runtime(a, panes)
    free, busy, queued, shelled = [], [], [], []
    work_unknown = []
    deliberate = []
    cycling_agents = []
    blocked_cycles = []
    tree_stale = []
    verdicts = []
    waiting = []
    saturated = []
    authdead = []
    manual = []
    bad_cards = []
    role_drift = []
    codex_blocked = []
    # One reader, built once: live_wiring off each pane's cmdline. A Panes
    # adapter with no cmdline genuinely cannot answer, and that is a cannot-tell
    # (live_verdict returns UNVERIFIED), never a pass.
    _cmdline = getattr(panes, "cmdline", None)
    _live = ((lambda pane: live_wiring(pane, _cmdline)) if _cmdline
             else (lambda pane: None))
    print()
    for ag, state, work, posture in _crew_states(
            agents, panes, runtime, cycling=cycling, untracked_root=a.root,
            cycle_blocked=cycle_blocked):
        if state == "cycling":
            cycling_agents.append(ag.name)
        if state == "cycle-blocked":
            path, _why = cycle_blocked.get(ag.name, ("", ""))
            blocked_cycles.append((ag.name, path))
        if work.endswith("sh"):
            shelled.append(f"{ag.name}({work.rsplit('+', 1)[1][:-2]})")
        if work.startswith(triage_mod.IDLE):
            free.append(ag.name)
        elif work.startswith(triage_mod.BUSY):
            busy.append(ag.name)
        elif work.startswith(triage_mod.QUEUED):
            queued.append(ag.name)
        elif work.startswith(triage_mod.WAITING):
            waiting.append(ag.name)
        elif work.startswith(triage_mod.SATURATED):
            # A `context_k=NNN` rides in the work cell (see _crew_states), so the
            # coordinator sees HOW over-limit, not just that it is.
            saturated.append(ag.name)
        elif work.startswith(triage_mod.AUTH_DEAD):
            authdead.append(ag.name)
        elif work.startswith(triage_mod.UNSURE):
            work_unknown.append(ag.name)
        # Only a LIVE agent can be running stale settings. A down agent has no
        # loaded settings to be stale, and will read the current file when it
        # next starts, so reporting on it would be noise that hides the real hits.
        # Shared with `role set` (aegis-qio0): the verdict rule and the bucket
        # rule each exist ONCE, so the column here and the warning there cannot
        # disagree about the same agent.
        verdict = _settings_verdict(launches, ag.name, state == "up")
        verdicts.append((ag.name, verdict))
        tree_cell, tree_detail = _tree_staleness_cell(
            a, ag, sweep=getattr(a, 'trees', False))
        if tree_detail:
            tree_stale.append((ag.name, tree_detail))
        # A DOWN pane with a stop record is down BY DECISION (#29). Collected here
        # and reported below rather than folded into the state column: `down` is
        # what the pane says and stays what the pane says — this is why.
        if state == "down" and (rec := stops.get(ag.name)) is not None:
            deliberate.append((ag.name, rec))
        if state != "up":
            from . import codex_daemon
            found = codex_daemon.inspect(ag.name)
            if found.blocked:
                codex_blocked.append((ag.name, found.reason()))
        # OBSERVED posture, and separately what the CARD lacks.
        # A live agent in manual mode is the running defect; a card with gaps is
        # the one waiting to be re-armed. Both were invisible; they need
        # different sentences because they need different fixes.
        if posture == launchable.MANUAL:
            manual.append(ag.name)
        if gaps := launchable.launch_gaps(ag):
            bad_cards.append((ag.name, gaps))
        # CARD-vs-PROCESS ROLE DRIFT (internal-ref). Settings are read ONCE at
        # launch; the card is read CONTINUOUSLY. So promoting an agent to lead by
        # card edit routes its reports' stop events to a process that never got
        # the `drain` direction — the tier is configured and inert, and the only
        # place anyone looks (the cards) shows it as correct.
        #
        # This is checked HERE, not only at launch, because the launch check
        # cannot see a card edited afterwards — which is the entire defect. Same
        # class as the settings-staleness column beside it, and it reuses
        # roles.live_verdict so the checker and this cannot disagree.
        #
        # LIVE AGENTS ONLY, and only when the graph requires something: a down
        # agent has no process to mismatch, and an isolated agent (no lead above,
        # no reports below) needs no directions at all — reporting on either
        # would be a false alarm on every leaf, which is how a column stops being
        # read.
        if state == "up" and roles_mod.required_stop_directions(ag, agents):
            lv, lv_why = roles_mod.live_verdict(ag, agents, _live)
            if lv == roles_mod.BROKEN:
                role_drift.append((ag.name, ag.role, lv_why))
        print(f"  {ag.name:<11} {ag.role:<14} {state:<13} {verdict:<8} "
              f"{tree_cell:<9} {work:<16} {posture:<7} {ag.pane or '—'}")
    stale, unknown = _reach_buckets(verdicts)
    # THE SWEEP, AS A LINE (aegis-ib65p decision 6). Learning that 12 of 12
    # worktrees were behind took a hand-rolled loop across three directories,
    # which means in practice nobody ever knew. The column answers "is anyone
    # stale"; this answers "which tree, and by how much" — the part an operator
    # would otherwise go and script again.
    if tree_stale:
        print()
        print(f"  {len(tree_stale)} agent(s) on a STALE or DIVERGED tree:")
        for name, detail in tree_stale:
            print(f"    · {name:<11} {detail}")
        print("    -N = commits you do NOT have (duplication risk: someone may "
              "have built it already).")
        print("    +N = local commits nobody else has (loss risk: push them).")
        print("    NOT pulled for you — rebase your own clean tree, never one "
              "with work in it.")
    print()
    # Down on purpose is not a roster hole to plug. Said before the free/busy
    # lines because an operator reading "3 down" needs to know which of those they
    # did themselves — the whole of GitHub #29 is a mechanism that could not tell.
    if deliberate:
        who = ", ".join(
            f"{n}{f' — {r.reason}' if r.reason else ''}" for n, r in deliberate)
        print(f"  {len(deliberate)} stopped ON PURPOSE (`st stop`, not faults): "
              f"{who}")
        print(f"    `st new <agent>` brings one back. Still respawned by "
              f"`st tend` — use `st tend --retire` to make it stay down.")
    if codex_blocked:
        print(f"  ⚠ {len(codex_blocked)} codex-daemon-wedged launch blocker(s):")
        for name, why in codex_blocked:
            print(f"    · {name}: {why}")
        print("    `st new <agent>` repairs only that card's daemon and stale lock.")
    if blocked_cycles:
        print(f"  ⚠ {len(blocked_cycles)} REQUESTED cycle(s) REFUSED and still "
              f"pending — NOT in flight:")
        for name, path in blocked_cycles:
            where = path or f"(run `st cycle {name}` to see it)"
            print(f"    · {name:<11} blocked on {where}")
        print("    Each of these agents keeps working on a context already judged "
              "full. Commit + `st push` that tree, and the next `st tend` serves "
              "the cycle.")
    if cycling_agents:
        print(f"  {len(cycling_agents)} planned context cycle(s): "
              f"{', '.join(cycling_agents)}")
        print("    Temporarily unavailable for dispatch and alert acknowledgements; "
              "the escalation poller holds the window until relaunch completes.")
    # The dispatcher's answer, said out loud. A column still makes the operator
    # scan 14 rows; the question is "who can take this", so print the list.
    if free:
        print(f"  {len(free)} free: {', '.join(free)}")
    elif busy and not work_unknown:
        print("  0 free — every live agent is mid-flight. Dispatching now "
              "interrupts work.")
    if busy:
        print(f"  {len(busy)} busy: {', '.join(busy)}")
    if work_unknown:
        print(f"  ⚠ {len(work_unknown)} UNKNOWN work state: "
              f"{', '.join(work_unknown)}")
        print("    Not counted as free or busy — pane content did not prove either. "
              "Inspect with `st log <agent>` before dispatching.")
    # WHO CAN TAKE THIS is only half the dispatcher's question; the other half is
    # WHAT IS NOT QUEUED ANYWHERE. See _unassigned_open (aegis-jqcs3).
    n_un, n_p1, why = _unassigned_open(a)
    if n_un is None:
        print(f"  ? unassigned-open: could not ask the tracker ({why}) — this is "
              f"NOT zero")
    elif n_un:
        p1 = f", {n_p1} of them P1 or above" if n_p1 else ""
        print(f"  {n_un} open bead(s) in NOBODY'S haul{p1} — unassigned, so they "
              f"self-feed to no one and no stop event advances to them. "
              f"`br list --status open` and route by domain.")
        print(f"    (includes any `decision-needed` beads, which are correctly "
              f"unassigned — st cannot read labels, see _unassigned_open.)")
    # THROTTLED-IDLE IS NOT IDLE (aegis-diasw). `3 free` means "three agents can
    # take work"; under an engaged priority floor it may mean "three agents that
    # nothing is allowed to reach". Those are opposite instructions to a
    # coordinator, and the roster reported them identically — which is half of
    # what made Rule Zero and the governor contradict each other in production.
    # Evaluated ONLY when somebody is free: when the fleet is saturated the
    # answer changes nothing, and `st crew` must not grow a metric read on a path
    # where it cannot matter.
    if free and (note := _throttled_idle_note(a)):
        print(note)
    # Unconditional, unlike the throttle note above: this one matters MOST when
    # the free list is empty, because an agent alive under another orchestrator is
    # precisely one that never appears on it (aegis-k9068).
    if (note := _alive_elsewhere_note(agents, panes)):
        print(note)
    # Not free, not busy, and the one state an operator will otherwise "fix" by
    # hand (aegis-x6xh). Say what it means and what NOT to do about it: the
    # incident that produced this line was an administrator reading a pane,
    # inferring a stall, and typing into a healthy agent's buffer.
    if queued:
        print(f"  {len(queued)} with UNSUBMITTED text in the input box: "
              f"{', '.join(queued)}")
        print(f"    Not idle and not working. Either a real stalled dispatch "
              f"(text sent, never submitted) or a")
        print(f"    human mid-sentence. A send-keys here APPENDS — do not "
              f"dispatch, and do not press Enter at")
        print(f"    someone else's pane to 'un-stall' it. Look with "
              f"`st log <agent>` and ask its owner.")
        print(f"    To resubmit once confirmed: a bare Enter does NOT submit "
              f"(measured) — use C-u,")
        print(f"    re-send the text with `send-keys -l`, pause ~1s, then Enter.")
    # The whole point of the verdict (aegis-qxc2). A column still makes the reader
    # scan 18 rows, and these agents are in NEITHER the free list nor the busy one
    # — so before this block a coordinator's summary said "5 free, 9 busy" of 18
    # agents and three stalled workers fell silently down the gap between the two
    # numbers. That gap is the original bug one layer up: invisible to the person
    # whose whole job is knowing who needs what.
    if waiting:
        print(f"  ⚠ {len(waiting)} agent(s) BLOCKED ON A QUESTION in their pane: "
              f"{', '.join(waiting)}")
        print(f"    Stopped until a person answers — not busy, not free, and it "
              f"will not time out. An ANSWERED")
        print(f"    picker still blocks until it is submitted; two agents sat on "
              f"those for over an hour.")
        # NAME THE COMMAND (aegis-w30p2). `st log <agent>` dumped the pane and
        # left the reader to eyeball a picker — which is how an option 2 that had
        # changed meaning between two prompts got answered twice in one evening.
        print(f"    Read it:   st ask {waiting[0]}")
        print(f"    Answer it: st answer {waiting[0]} <N>")
        print(f"    ...or tell them to put the decision on the bead with a "
              f"recommendation and carry")
        print(f"    on — a question in a pane reaches nobody and dies with the "
              f"session.")
    # Say the consequence, not just the count (aegis-q73g). The reader who needs
    # this line is the administrator about to book the previous item as done.
    if shelled:
        print(f"  ⚠ {len(shelled)} agent(s) still own live background shells: "
              f"{', '.join(shelled)}")
        print("    A turn that ended is not a task that finished. Whether that "
              "should block a dispatch is")
        print("    unruled — but a build, a test run or a `gh run watch` is "
              "unfinished work, and the next")
        print("    item's output will land on top of it.")
    # The bead this state was built for (aegis-h562). A saturated agent reads as
    # `idle` on every prior version of this command, so it lands on the free list
    # and gets work piled on — three agents sat past the threshold (687k/562k/524k)
    # for fifteen hours exactly that way. It is the fail-SILENT case this whole
    # file exists to convert: the number was on the pane the entire time. 400k is
    # a CYCLE threshold, not the ~1M limit — the depth shows as raw k tokens in the
    # work cell, never as a "% of limit" (that framing was a lie, Stiwi's rule).
    if saturated:
        print(f"  ⚠ {len(saturated)} agent(s) PAST THE 400k CYCLE THRESHOLD — NOT "
              f"free, a dispatch wall: {', '.join(saturated)}")
        print(f"    They read as idle but cannot hold new work: they drop earlier "
              f"context, re-derive settled")
        print(f"    decisions, and miss constraints stated long ago. `st go` "
              f"REFUSES them (the depth is in the")
        print(f"    work cell). Remedy: the agent {handoff_text.coordinator_tag()}, "
              f"THEN takes the task — do NOT")
        print(f"    auto-cycle, it loses whatever was not saved, and do NOT tell it "
              f"to /clear (that drops bypass). The")
        print(f"    saturated agent is the LEAST able to notice it must cycle, so "
              f"this is the coordinator's to drive.")
    # The bead this state was built for (aegis-arma). An operator re-login rotates
    # the shared credential, and EVERY live agent's session goes login-expired at
    # once — still rendering a ready UI over an empty box, i.e. `idle` to every
    # earlier version of this command. All 9 crew sat that way: fed, prompted, and
    # dispatched into, with every send dying against the banner. Not idle: DEAD.
    if authdead:
        print(f"  ⚠ {len(authdead)} agent(s) AUTH-DEAD — login expired, every API "
              f"call fails: {', '.join(authdead)}")
        print(f"    They render an idle-looking pane but nothing can run. /login "
              f"in the pane is an interactive")
        print(f"    browser OAuth flow — it cannot be driven for them. Recovery: "
              f"the OPERATOR re-logs in on their")
        print(f"    own session first (refreshing the shared credential), then "
              f"`st tend --reauth` relaunches every")
        print(f"    auth-dead agent in one command. Their frozen context is lost "
              f"either way — it was already")
        print(f"    unreachable the moment auth died.")
    # The incident this state was built for. An agent launched without
    # `dangerous` needs a human keystroke for EVERY bash call, so it reads `up`,
    # `current` and `busy` while being unable to advance a single command on its
    # own. Three agents sat that way through one evening: six coordinator
    # picker-answers across five agents, two blocked at once, one agent dead
    # twice. The mode line was at the bottom of the pane the whole time and no
    # surface read it — this is that read, promoted to the roster.
    if manual:
        print(f"  ⚠ {len(manual)} agent(s) in MANUAL MODE — a human must approve "
              f"EVERY bash call: {', '.join(manual)}")
        print(f"    Not free and not reliably busy: an unattended agent that "
              f"needs a keystroke per command cannot")
        print(f"    make progress by construction, and each approval only "
              f"reveals the next one. Fix is `dangerous`")
        print(f"    on the CARD plus a RELAUNCH (`st stop <agent> && st new "
              f"<agent>`) — the mode is read at launch, so")
        print(f"    editing the card alone changes nothing. Verify by this "
              f"column flipping, never by the card.")
    # aegis-np4x1. EVERY column above is a question asked of a pane we already
    # knew the name of, so a fleet running under a RETIRED naming scheme is
    # invisible here by construction — and was. Six aegis-crew-* agents ran
    # beside this roster for hours while it reported 19 agents and zero faults.
    # Not merely silent: WRONG. goldblum, maldoon and sentinel each printed
    # `down` while their twin was live under the old name, and three agents had
    # two sessions on one workspace — the shared-index clobber hazard, arrived at
    # by a route no amount of care inside the roster loop could have seen.
    from . import tier as tier_mod
    live_sessions = panes.sessions()
    if live_sessions is not None:
        stray = tier_mod.strays(live_sessions,
                                [ag.pane for ag in agents if ag.pane],
                                [ag.name for ag in agents])
        dupes = [(s, who) for s, who in stray if who]
        others = [s for s, who in stray if not who]
        if dupes:
            print(f"  ⚠ {len(dupes)} session(s) running a ROSTER AGENT under a "
                  f"name no card claims:")
            for s, who in dupes:
                card = next((g for g in agents if g.name == who), None)
                says = "—"
                if card is not None and card.pane:
                    says = "up" if panes.exists(card.pane) else "DOWN"
                print(f"      {s}  → {who} (whose card says {says})")
            print(f"    Two agents on one workspace share a git index, and a "
                  f"sibling's commit can swallow yours")
            print(f"    while git reports success to both. This is what an "
                  f"autostart unit left enabled through a")
            print(f"    rename looks like the morning after a reboot. Find the "
                  f"launcher BEFORE killing anything —")
            print(f"    a duplicate you kill without disarming its supervisor is "
                  f"back at the next boot.")
        if others:
            print(f"  ⚠ {len(others)} session(s) on this socket that no card "
                  f"claims: {', '.join(others)}")
            print(f"    Not necessarily crew — but st cannot tend what it cannot "
                  f"name, so they are listed, not judged.")
    if free or busy or queued or waiting or saturated or authdead or shelled or manual:
        print()
    # Say the consequence, not just the state. The operator who needs this line is
    # the one who just rewrote a settings file and has no reason to suspect it did
    # not go anywhere.
    if stale:
        print(f"  ⚠ {len(stale)} agent(s) are running settings OLDER than the file "
              f"on disk: {', '.join(stale)}")
        print(f"    Their hooks are whatever the file said AT LAUNCH. Rewriting a "
              f"settings file is not deploying it — only a relaunch")
        print(f"    (`st stop <agent> && st new <agent>`) re-reads it.")
    if unknown:
        print(f"  ? {len(unknown)} agent(s) have no launch stamp, so this cannot "
              f"be answered for them: {', '.join(unknown)}")
        print(f"    Launched before stamping existed, or by something other than "
              f"`st new`. UNKNOWN, not fine.")
    # The CARD half of the same defect, and the one that is dormant rather than
    # burning: these agents are down, so nothing is stalling right now — but the
    # card is what `st tend` re-arms, and re-arming one of these manufactures the
    # incident again. It sat unseen behind `retired=true` for exactly this reason:
    # a retired card is not launched, so its defect never shows up as a symptom.
    # BEFORE bad_cards on purpose: bad_cards is DORMANT (those agents are down,
    # nothing is stalling now). This one is BURNING — the tier is inert while
    # everything looks configured.
    if role_drift:
        print(f"  ⚠ {len(role_drift)} agent(s) are RUNNING SETTINGS THAT DO NOT "
              f"MATCH THEIR ROLE: {', '.join(n for n, _, _ in role_drift)}")
        for name, role, why in role_drift:
            print(f"      {name} (card says {role}): {why}")
        print(f"    Settings are read at LAUNCH; the card is read continuously. "
              f"A role changed after launch does")
        print(f"    NOT reach the running process, so the tier is configured "
              f"and INERT — and the cards, which are")
        print(f"    the only place anyone looks, show it as correct. Reports "
              f"routed to a lead in this state rise")
        print(f"    to the administrator as `lead-unreachable` while the lead "
              f"is visibly up.")
        print(f"    Fix: `st stop <agent> && st new <agent>`. NOT offered "
              f"automatically — a relaunch destroys")
        print(f"    in-flight context, so it stays a human's call.")
    if bad_cards:
        print(f"  ⚠ {len(bad_cards)} card(s) would launch an agent that "
              f"CANNOT WORK: {', '.join(n for n, _ in bad_cards)}")
        for name, gaps in bad_cards:
            print(f"      {name}: {', '.join(g.short for g in gaps)}")
        print(f"    Harmless while they stay down — the card is what `st tend` "
              f"re-arms, so this is a trap laid for")
        print(f"    whoever un-retires them next. `st tend --unretire` says the "
              f"full reason, and REFUSES on a workspace")
        print(f"    fault (manual mode it only warns about — that one can be "
              f"deliberate).")
    if stale or unknown or bad_cards or role_drift:
        print()
    return OK


def _check_alert_keepers(a, rules: list[Path]) -> int:
    """Fail-loud ownership check for alert rules, without treating scale-down as a fault.

    A keeper is a roster *ownership* declaration, not a promise that its pane is
    running at this instant: intentional stops are expected to self-heal through
    ``st tend``.  The durable bad states are instead (1) no label, (2) a label
    that names no card, and (3) a card which cannot make unattended progress when
    re-armed.  Reading ``panes`` here would turn normal right-sizing into a
    permanent false red and invite a silence.

    Templates contain Jinja and are therefore not necessarily YAML until
    Ansible renders them.  This intentionally reads only the Prometheus rule
    shape that survives rendering: an ``alert:`` stanza and its ``keeper:``
    label.  It rejects missing labels rather than pretending a YAML parse of a
    template proved ownership.
    """
    try:
        agents = {agent.name: agent for agent in _registry(a).all().exact()}
    except Exception as e:
        print(f"could not tell: roster unreadable: {e}", file=sys.stderr)
        return CANNOT_TELL

    findings = []
    unowned: list[str] = []
    seen = 0
    for rule_path in rules:
        try:
            lines = rule_path.read_text().splitlines()
        except OSError as e:
            print(f"could not tell: cannot read {rule_path}: {e}", file=sys.stderr)
            return CANNOT_TELL

        active = None

        def _flush(alert):
            """Record one parsed stanza's findings, and count a declared absence.

            The count is kept HONEST on purpose (kelly's recommendation): an
            explicitly unowned alert is reported as such rather than quietly
            making the owned total one smaller, which would read as if the
            fleet had one fewer alert instead of one deliberately unowned.
            """
            findings.extend(_keeper_findings(agents, rule_path, alert))
            if alert.get("keeper") == _KEEPER_NONE and alert.get("reason"):
                unowned.append(alert["name"] or "unnamed alert")

        for lineno, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                # A comment inside a stanza may carry the declared-absence
                # sentinel. It MUST be a comment: see _keeper_findings for why a
                # real `keeper` label cannot express this.
                if active is not None and active.get("keeper") is None:
                    body = stripped.lstrip("#").strip()
                    if body.startswith("keeper:"):
                        rest = body.removeprefix("keeper:").strip()
                        if rest.split(" ", 1)[0].strip().strip("\"'") == _KEEPER_NONE:
                            active["keeper"] = _KEEPER_NONE
                            active["reason"] = rest[len(_KEEPER_NONE):].strip(" -—:#")
                continue
            indent = len(line) - len(stripped)
            if stripped.startswith("- alert:") or stripped.startswith("alert:"):
                if active is not None:
                    _flush(active)
                name = (stripped.removeprefix("- ").removeprefix("alert:")
                        .strip().strip("\"'"))
                active = {"name": name, "line": lineno, "indent": indent,
                          "keeper": None}
                seen += 1
                continue
            if active is not None and indent <= active["indent"]:
                _flush(active)
                active = None
            if active is not None and stripped.startswith("keeper:"):
                value = stripped.removeprefix("keeper:")
                # Split the trailing `# …` off. For an ordinary keeper it is a
                # normal YAML comment that was previously being parsed INTO the
                # name (and so failing as "not on the roster"); for the `none`
                # sentinel it is the required justification.
                value, _, reason = value.partition("#")
                active["keeper"] = value.strip().strip("\"'")
                active["reason"] = reason.strip()
                # Remember this came from a LABEL: the sentinel is only valid as
                # a comment, and the two must be distinguishable.
                active["labelled_none"] = active["keeper"] == _KEEPER_NONE
        if active is not None:
            _flush(active)

    if not seen:
        print("could not tell: no alert stanzas found in supplied rule files", file=sys.stderr)
        return CANNOT_TELL
    if findings:
        for finding in findings:
            print(f"FAIL: {finding}")
        print("Alert-keeper check FAILED — every alert needs a roster keeper that "
              "can work unattended. Stopped but launchable keepers are permitted.")
        # THE STEER (aegis-jcr0g). Without this line the obvious repair for
        # "missing keeper label" is to add a keeper — and for a sink-only canary
        # that silently turns it into a real page to whoever is named, which is
        # the bug aegis-fyxsx removed. A diligent reader following the failure
        # text must land on the right fix, not the damaging one.
        print(f"If an alert is DELIBERATELY unowned, say so in the rule as a "
              f"COMMENT: `# keeper: {_KEEPER_NONE} — <why>`. A declared absence "
              f"is not a missing label. The reason is required, and it must be "
              f"a comment: a real keeper label is seated as chain[0] by "
              f"alert-comms-bridge.")
        return REFUSED
    if unowned:
        print(f"OK: {seen - len(unowned)} alert rule(s) have roster keepers that "
              f"can work unattended; {len(unowned)} explicitly unowned "
              f"({', '.join(sorted(unowned))})")
    else:
        print(f"OK: {seen} alert rule(s) have roster keepers that can work unattended")
    return OK


# The declared-absence sentinel (aegis-jcr0g, sattler's ruling 2026-08-30).
# A DECLARED absence is not a MISSING label — the same principle as the
# confidence labels, where unset must not read as extracted.
_KEEPER_NONE = "none"


def _keeper_findings(agents, rule_path: Path, alert) -> list[str]:
    """Return the durable ownership defects for one parsed alert stanza."""
    where = f"{rule_path}:{alert['line']} ({alert['name'] or 'unnamed alert'})"
    keeper = alert["keeper"]
    if keeper == _KEEPER_NONE:
        if not alert.get("labelled_none"):
            # WHY A REASON IS REQUIRED. Two correct guards collided here: this
            # check wants every alert owned, and aegis-fyxsx requires the ladder
            # canary to carry NO keeper for ever. The sentinel exists to express
            # that; a bare `none` would express nothing and would just be a
            # quieter way to skip the check — the unowned-alert hole this whole
            # function exists to close.
            if not alert.get("reason"):
                return [f"{where}: the declared-absence sentinel needs its "
                        f"reason (`# keeper: {_KEEPER_NONE} — <why this alert "
                        f"is deliberately unowned>`)"]
            return []
        # ⛔ A REAL LABEL IS REFUSED, and this is the whole point of the comment
        # form. alert-comms-bridge reads `labels["keeper"]` and, finding any
        # non-empty value, seats it as chain[0]: `["none", *chain]`. So a
        # `keeper: none` LABEL does not declare an absence — it routes tier zero
        # to a recipient that does not exist. Measured in
        # alert-comms-bridge.py::with_keeper, which truthy-tests the value.
        return [f"{where}: `keeper: {_KEEPER_NONE}` must be a COMMENT, not a "
                f"label — alert-comms-bridge seats any non-empty keeper as "
                f"chain[0], so as a label this routes tier zero to a "
                f"nonexistent recipient. Write `# keeper: {_KEEPER_NONE} — "
                f"<why>` instead."]
    if not keeper:
        return [f"{where}: missing keeper label"]
    agent = agents.get(keeper)
    if agent is None:
        return [f"{where}: keeper {keeper!r} is not on the roster"]
    if gaps := launchable.launch_gaps(agent):
        return [f"{where}: keeper {keeper!r} cannot work unattended "
                f"({', '.join(g.short for g in gaps)})"]
    return []


def _crew_states(agents, panes, runtime, cycling=(), untracked_root=None,
                 cycle_blocked=()):
    """(agent, pane state, work verdict, permission posture) per agent, by name.
    THE code path for the busy/idle judgment — the table renders it and `--count`
    counts it, so the number a status bar shows can never disagree with the roster
    a human just read. Reimplementing the verdict for the counter is how the two
    drift.

    WORK: only a live pane has one. A down agent is not idle-and-available, it is
    not there — printing `idle` for it would put it on the free list and send work
    into a session that does not exist. So its verdict is `—`: not looked at.

    POSTURE: read from the SAME capture as the work verdict, never
    from the card. The card is intent; the running process is truth, and they
    diverge the moment a card is edited without a relaunch. An agent in manual
    mode is neither busy nor free — it is a stall waiting to happen, and until
    this column existed the only way to see it was to capture the pane footer by
    hand, which is why three agents sat that way for a night.
    """
    cycling = set(cycling)
    cycle_blocked = set(cycle_blocked)
    for ag in sorted(agents, key=lambda x: x.name):
        if ag.name in cycle_blocked:
            # aegis-7xptd5: REFUSED, not in flight. Distinct from `cycling`
            # because the two need opposite responses — a cycle in flight is
            # waited out, a refused one needs somebody to commit a tree. The pane
            # is still live and still working, so unlike `cycling` this is not a
            # reason to withhold dispatch.
            state = "cycle-blocked"
        elif ag.name in cycling:
            # A durable request is stronger than a still-live pane during the
            # brief pre-stop interval: consumers must not mistake that pane for
            # an acknowledgement-capable destination.
            state = "cycling"
        elif ag.pane:
            state = "up" if panes.exists(ag.pane) else "down"
        else:
            state = "no pane"          # not "down" — we did not look
        if state == "up":
            # attrs=True: work_state needs dim to tell a placeholder suggestion
            # from queued-unsubmitted text. shows_ready_ui matches PLAIN
            # substrings and its markers arrive colour-split word by word, so it
            # gets the stripped view of the very same capture.
            screen = panes.capture(ag.pane, attrs=True)
            # One capture, two views of the same instant — a second capture-pane
            # would be a different moment. Both plain-substring readers get the
            # stripped view; only work_state's input-box check needs the attributes.
            plain = triage_mod.strip_attrs(screen)
            # awaiting: a BLOCKING picker (aegis-qxc2). Without it these panes
            # print `?`, which is honest and unactionable — 7 of 10 workers read
            # that way at once while every one of them sat on a question.
            # auth_dead: the login-expired banner (aegis-arma). Without it an
            # auth-dead pane prints `idle` — all 9 crew did, through a whole
            # expiry — and lands on the free list.
            ui_up = runtime.shows_ready_ui(plain)
            work = triage_mod.work_state(
                screen, ui_up,
                awaiting=asks_a_question(runtime, plain),
                auth_dead=auth_expired(runtime, plain))
            cmdline = None
            read_cmdline = getattr(panes, "cmdline", None)
            if callable(read_cmdline):
                try:
                    cmdline = read_cmdline(ag.pane)
                except Exception:
                    pass
            posture = launchable.observed_posture(plain, ui_up, cmdline=cmdline)
            # Background shells outlive the turn. An agent whose turn ended with a
            # build/test/`gh run watch` still live is NOT finished, and every
            # surface the administrator has was silent about it. Shown ON the work
            # verdict, because "idle" is exactly the word that would otherwise
            # mislead — `idle+1sh` is idle AND carrying live work.
            shells = triage_mod.running_shells(screen)
            if shells:
                work = f"{work}+{shells}sh"
            # The saturation ratio rides in the cell (aegis-h562), so the
            # coordinator sees HOW over-limit — 172% is a different decision from
            # 101%. Only when the verdict is saturated; a healthy pane's token
            # count is not this column's business.
            if work.startswith(triage_mod.SATURATED):
                tokens = triage_mod.context_tokens_k(plain)
                if tokens is not None:
                    work = f"{work}·{int(tokens)}k"
            # PANE-IDLE IS NOT WORK-IDLE (aegis-eh6ok). The PreToolUse
            # untracked-work detector already records a non-admin ACTING with an
            # empty hook. Consume that ledger instead of building a second
            # activity detector. Recent evidence turns an idle-looking pane into
            # honest UNKNOWN; unreadable evidence does the same. This is a
            # report, never a block, preserving the hook's fail-open contract.
            if (work == triage_mod.IDLE and untracked_root is not None
                    and ag.role != "administrator"):
                from . import untracked as untracked_mod
                activity, detail = untracked_mod.ledger_activity(
                    untracked_root, ag.name)
                if activity != untracked_mod.ACTIVITY_CLEAR:
                    work = f"{triage_mod.UNSURE} ({detail})"
        else:
            work = "—"
            # A down pane has no posture to read. `—` and not MANUAL: the card
            # may well say dangerous=False, but that is what it WILL launch with,
            # not what anything is running, and this column only ever reports what
            # was observed. What the card lacks is launch_gaps()' question.
            posture = "—"
        yield ag, state, work, posture


def _alive_elsewhere_note(agents, panes) -> str:
    """Agents whose pane is gone but whose NAME is on a live session anyway, or "".

    THE 2h GAP THIS CLOSES (aegis-k9068). A crew member launched by another
    orchestrator lands in that orchestrator's session namespace, so `st crew`
    looks for the pane it manages, does not find it, and renders the agent
    IDENTICALLY TO A DEAD ONE. The agent is alive, taking no dispatches, and the
    roster says `down` — which reads as "nothing is running" rather than "running
    where I cannot reach it". Tier-1 alert cover sat that way for two hours and no
    instrument said so.

    ORCHESTRATOR-AGNOSTIC ON PURPOSE. It does not look for any particular prefix:
    hardcoding one orchestrator's naming into st would make this fix true for
    exactly the case that already happened and false for the next one. The
    question asked is the general one — "is this agent's name on a session that is
    not the session I manage?"

    FAILS SILENT, like its sibling above. `sessions()` returns None when it cannot
    tell, and a roster that cannot enumerate sessions must say nothing rather than
    report every agent as missing — "I could not look" is not "they are gone".
    """
    try:
        live = panes.sessions()
        if not live:                      # None (cannot tell) or empty: say nothing
            return ""
        stray = []
        for ag in agents:
            if not ag.pane or panes.exists(ag.pane):
                continue
            other = [s for s in live if ag.name in s and s != ag.pane]
            if other:
                stray.append(f"{ag.name} (on {', '.join(sorted(other))})")
        if not stray:
            return ""
        return ("    ⚠ ALIVE ELSEWHERE, not down: " + "; ".join(sorted(stray)) +
                " — the pane st manages is gone but a session carrying the agent's "
                "name is running. It takes no dispatches from st and `st go` cannot "
                "reach it. Recover with `st new <agent>` after stopping the other "
                "session; do NOT read this as a crashed agent.")
    except Exception:      # noqa: BLE001 — the roster never fails on this
        return ""


def _throttled_idle_note(a) -> str:
    """The line that tells a free agent apart from a REACHABLE one, or "".

    FAILS TO SILENCE, always. No governor, an unreadable one, a signal we cannot
    see, no floor in force — every one of those returns "" and the roster reads
    exactly as it did before this existed. A capacity annotation that could break
    `st crew` would be a bad trade: the roster is the command an operator reaches
    for when things are already going wrong.
    """
    try:
        gov = _governor(a)
        if gov is None:
            return ""
        verdict = gov.evaluate(persist=False)
        if verdict.signal_lost or verdict.tier is None:
            return ""
        if verdict.drains:
            return ("    ⚠ THROTTLED-IDLE, not available: the usage governor is "
                    "draining the fleet — nothing dispatches at any priority.")
        if verdict.floor is None:
            return ""
        return (f"    ⚠ THROTTLED-IDLE, not fully available: the governor's "
                f"{verdict.tier.at}% tier is engaged — {verdict.effect()}. Work "
                f"below the floor cannot be dispatched to them; they are idle "
                f"because the throttle is holding, not because nobody fed "
                f"them.{_until(verdict)}")
    except Exception:      # noqa: BLE001 — the roster never fails on capacity
        return ""


def _until(verdict) -> str:
    """` They come back on their own when the five_hour budget resets in 1h35m.`

    The sentence that turns waiting into a decision (aegis-9mehy). Every other
    THROTTLED-IDLE line tells an operator that the fleet is down and nothing
    tells them for how long — which is precisely the state that gets somebody to
    start intervening by hand at 22:00 for a budget that refills at 22:40.
    """
    nxt = verdict.next_reset(time.time())
    if nxt is None:
        return ""
    window, left = nxt
    return (f" They come back on their own when the {window} budget resets "
            f"{gov_mod.fmt_when(left)}.")


def _effective_target(asked: int | None, cap: int | None) -> int | None:
    """The stricter of an operator's `--target` and the governor's cap.

    None from either side means "no opinion", so this is a min over the values
    actually declared and stays None when neither is. It NEVER raises a target:
    a cap can only shrink a fleet, and `--target 20` under `max_agents = 6`
    means six.
    """
    vals = [v for v in (asked, cap) if v is not None]
    return min(vals) if vals else None


def _target_source(asked: int | None, cap: int | None) -> str | None:
    """Which knob produced the effective target, for tend's held message.

    Returns the governor phrasing only when the CAP is what actually binds — if
    the operator asked for something stricter, `--target` is the honest answer.
    Ties go to the governor: a cap the operator happens to match is still the
    thing that would stop them raising it.
    """
    if cap is not None and (asked is None or cap <= asked):
        return "the governor's max_agents cap:"
    return None


def _crew_governor(a) -> int:
    """`st crew --governor` — the capacity verdict, machine-readable, one line.

    THE CONSUMER IS A STATUS BAR, and that shapes every choice here.

    FORMAT — the first token is a STATUS WORD, and the three cases are
    structurally different so a reader cannot mistake one for another:

        ok 45/50/5400 24/45/248400                  both budgets, no tier engaged
        ok 70/80/5400 24/45/248400 dispatch only P0 and above [five_hour >= 70%]
        ok 96/-/5400 24/45/248400 ...               above every five_hour tier
        lost                                        the signal could not be read
        off                                         no governor configured

    THE LABEL CARRIES EVERY ENGAGED RESTRICTION, `; `-separated (aegis-upo93) —
    a priority floor and a "who runs" trait tier are different KINDS and engage
    together, so a line that could name only one hid the one that stops agents:

        ok 3/50/2877 79/90/118076 dispatch only P0 and above [seven_day >= 70%]; only support crew runs [seven_day >= 80%]

    Each budget is `current/next-threshold/seconds-until-reset`. THE RESET IS
    HERE BECAUSE "THROTTLED" AND "THROTTLED UNTIL 22:40" ARE DIFFERENT SENTENCES
    to the operator reading the bar (aegis-9mehy): the first invites
    intervention, the second invites waiting, and the fleet had no way to say the
    second. Seconds rather than a wall-clock time because the consumer is a
    program — a duration needs no timezone, no date and no parsing, and the bar
    formats "resets in 1h35m" from it. `-` means nothing published one, which is
    a real answer; it is never rendered as 0, because a bar reading 0 would say
    "resets now" forever.

    The NEXT THRESHOLD is in the output
    because a consumer cannot colour honestly without it: the tiers are per-window
    and asymmetric (five_hour 50/70/80/95, seven_day 45/65/75/90), so a bare 44%
    is six points from engaging one budget and already engaging the other. A bar
    that coloured on the raw number would call those the same, which is precisely
    the "one number is the wrong number" mistake this flag prints two budgets to
    avoid. `-` means no higher tier exists for that window.

    `lost` and `off` carry NO NUMBERS AT ALL. That is deliberate: a bar that
    printed a stale percentage while blind would silently undo the governor's
    whole fail-safe, which is that blindness is LOUD (it alarms every pass on
    purpose). Making the blind case unparseable-as-a-reading is the same rule
    `shantytown.answer` applies to a collection — "could not look" must not be
    representable as an answer.

    BOTH WINDOWS, always. They exhaust independently and a five-hour budget
    refills in hours while the weekly does not refill for days, so one number is
    the wrong number half the time. The tier LABEL is last and may
    contain spaces: a reader takes the first three fields and treats the
    remainder as the label.

    A PURE READ (`persist=False`). A status bar polls every few seconds; if this
    extended a hysteresis hold, merely LOOKING at the bar would ratchet fleet
    policy. `st tend` remains the one writer of the engaged tier.
    """
    try:
        reg = _registry(a)
    except Exception:
        # No registry is a reason the ready count cannot be read, which
        # `_ready_count` already renders as "could not look" rather than zero.
        reg = None

    def _render(multi, *, running: int = 0, name: str = "base") -> str:
        """Render one provider with the same two-window contract in every fleet.

        The mixed-fleet branch used to call ``Verdict.render()``, whose human
        sentence carries only the policy's primary window.  That made a 4%
        five-hour reading say "wide open" while seven-day was 93% and the
        delegation reserve was engaged (aegis-ta96y).
        """
        try:
            readings = multi.reader.read_all()
            verdict = multi.evaluate(persist=False)
        except Exception:
            return "lost"
        if verdict.signal_lost:
            return "lost"

        clock = time.time()

        def _pct(window: str) -> str:
            r = readings.get(window)
            if r is None or not r.ok or r.pct is None:
                return "?/?/?"
            now = int(round(r.pct))
            higher = sorted(t.at for t in multi.policy.tiers_for(window)
                            if t.at > now)
            left = r.resets_in(clock)
            reset = "-" if left is None else str(max(0, int(left)))
            return f"{now}/{higher[0] if higher else '-'}/{reset}"

        burn = ""
        if verdict.burning:
            burn = " ".join(
                f"BURNDOWN[{b.window} capped {b.ceiling:.0f}% "
                f"+{b.headroom:.0f}pts {int(b.resets_in)}s]"
                for b in verdict.burning)
        pace = ""
        if verdict.pacing:
            pace = " ".join(
                f"PACE[{p.window} {p.pct:.0f}%used/{p.elapsed_pct:.0f}%elapsed "
                f"={p.ratio:.2f}x <={p.threshold:.2f}x]"
                for p in verdict.pacing)
        label = "; ".join(t.label() for t in verdict.restrictions)
        cap = ("" if verdict.max_agents is None
               else f"CAP[{verdict.max_agents} agents]")
        detail = " ".join(x for x in (cap, burn, pace, label) if x)
        usage = (f"ok {_pct(gov_mod.FIVE_HOUR)} "
                 f"{_pct(gov_mod.SEVEN_DAY)} {detail}").rstrip()
        advisory = creel_advisory_mod.controller_line(
            readings, running=running, cap=verdict.max_agents,
            probe=getattr(cfg, "env", {}).get(creel_advisory_mod.PROBE_ENV))
        # UTILIZATION ON ITS OWN LINE, EVERY PASS (aegis-967a9). Same argument
        # the fleet cap earns above: under-cap idleness is invisible exactly when
        # every other field reads "wide open", so a surface that shows it only
        # when something has gone wrong cannot show this at all. Tonight base ran
        # 0.90x seven-day pace with ZERO leads live under a cap of six and this
        # line said nothing. The primary line is left byte-identical so the
        # documented three-fields-then-label parse is unchanged.
        util = _utilization(name, readings=readings, policy=multi.policy,
                            verdict=verdict, live=running, now=clock,
                            advisory=advisory, root=a.root, reg=reg)
        return f"{usage} | {advisory}\n  {util.render()}"

    # Mixed fleets cannot be represented by the legacy one-line value without
    # lying by omission.  Keep that exact line for old configs; emit one named
    # line per provider once [governor.by_harness] exists.
    cfg, _err = config.load_or_default(Path(a.root))
    if cfg.governor.by_harness:
        _cfg, governors = _governors(a)
        try:
            cards = _registry(a).all().exact()
        except Exception:
            cards = []
        panes = _panes(a) if cards else None
        live_by_harness = _live_by_governor(cards, panes, cfg, governors, a.root)
        for name, multi in sorted(governors.items()):
            print(f"{name} {_render(multi, running=live_by_harness.get(name, 0), name=name)}")
        for harness in sorted({harness_mod.name_for(card, root=a.root) for card in cards}
                              - {"base"} - set(cfg.governor.by_harness)):
            if gov_mod.unconfigured(cfg.governor, harness):
                print(f"{harness} lost unconfigured — no usage governor")
        return OK

    gov = _governor(a)
    if gov is None:
        print("off")
        return OK
    # BURNDOWN IS NAMED IN THE LABEL (aegis-yegfx), and it has to be, because the
    # bar's honest-looking failure is silence: burndown removes this window's
    # non-drain tiers, so `governing` goes None and the line renders exactly like
    # a fleet that was never throttled. "Wide open because usage is low" and
    # "wide open because a guard deliberately stood down" are different sentences
    # to the operator reading the bar, and only one of them ends in an hour. This
    # is the same class of bug as aegis-yc864 — a display that disagreed with
    # enforcement — caught before shipping rather than after.
    # PACE IS NAMED FOR THE SAME REASON (aegis-7kwtu) — it withholds the same
    # non-drain tiers, so it has the same honest-looking failure: the bar goes
    # quiet and reads as "never throttled". BOTH NUMBERS GO IN THE LABEL, not the
    # ratio alone: an operator who sees `0.92x` cannot check it, and one who sees
    # `90%/98%` can tell at a glance that a normal end-of-week burn is being
    # correctly left alone rather than a guard having silently broken.
    # EVERY ENGAGED RESTRICTION, not the governing one alone (aegis-upo93).
    #
    # NOT engaged[-1] either (aegis-yc864). That was a POSITIONAL pick resting on
    # "cumulative, so the last one is the most restrictive" — true under one
    # budget, false once `engaged` spans two windows. `governing` derives from
    # the same computation `admits` enforces, so this line cannot disagree with
    # `st go` again — but `governing` answers only "WHICH FLOOR", and a trait
    # tier restricting WHO RUNS is engaged alongside it and was invisible here.
    # Measured: a P0 floor rendered while `excludes` was banning most of the
    # roster, and the coordinator moved to restore an agent on the strength of
    # this line. `restrictions` composes both kinds from the same properties the
    # enforcement paths use.
    #
    # The parse contract is unchanged: three fields then a free-text label. The
    # label may now contain `; `, which is the separator `Tier.label()` and
    # `Verdict.effect()` already use inside one.
    # THE FLEET CAP IS PRINTED SEPARATELY because the baseline is not a tier and
    # would otherwise be invisible here (aegis-tzpo1) — it engages at 0% usage,
    # when `restrictions` is empty and every other field says "wide open". A cap
    # that silently holds a fleet at 6 while the bar reads unrestricted is the
    # aegis-yc864 shape: a display disagreeing with enforcement.
    try:
        cards = _registry(a).all().exact()
        panes = _panes(a)
        running = sum(bool(card.pane and panes.exists(card.pane)) for card in cards)
    except Exception:
        running = 0
    print(_render(gov, running=running))
    return OK


def _live_by_governor(cards, panes, cfg, governors, root):
    """Live agents bucketed by the GOVERNOR THAT GOVERNS THEM, not by harness name.

    MEASURED 2026-08-29, and both surfaces were wrong in OPPOSITE directions
    while reading the same fleet in the same minute:

        st crew --governor   base live 0/6      <- bucketed by harness name
        st tend              base live 5/6      <- every card treated as base

    `harness.name_for` never returns "base" — it returns "claude" for a card that
    never said — so `live_by_harness.get("base")` was structurally always 0, and
    a fleet with five agents up reported an empty one. tend's mirror defect
    counted the codex agents into base as well as into codex.

    Neither number was load-bearing before: `running` only reached Creel's
    `--running`, where a wrong value skews an advisory quietly. aegis-967a9 puts
    it on the screen as `live N/cap` and lets it recommend growth, and growth on
    a miscount is the one direction this governor's fail-safe forbids. So both
    surfaces now resolve through `_governor_for`, which is the same function the
    dispatch gate uses — they cannot disagree without it also being wrong.

    An UNGOVERNED harness is counted by neither: it has no cap to be under, and
    `st crew --governor` already prints it its own "no usage governor" line.
    """
    live = {name: 0 for name in governors}
    for card in cards:
        if not (card.pane and panes is not None and panes.exists(card.pane)):
            continue
        harness, _governor, unconfigured = _governor_for(cfg, governors, card, root)
        if unconfigured is not None:
            continue
        name = harness if harness in governors else "base"
        live[name] = live.get(name, 0) + 1
    return live


def _agent_counts(a, agents, panes, runtime):
    """Agent counts for the Prometheus export, or None for COULD NOT LOOK.

    Stiwi's directive (aegis-ycqgyx) asks to "tell ... the number of Agents either
    codex or Claude through our Prometheus metrics", so the harness is the label
    that matters and it is resolved through `harness.name_for` — the same function
    `st anchor --harness` and the dispatch gate use, so the dashboard and the gate
    cannot disagree about whose harness a card is.

    THREE MAPPINGS, NOT ONE, because they answer different questions and folding
    them into a single `state` label produces a set that sums to nothing:

      state    the PANE state (up/down/no_pane/cycling/cycle_blocked). MECE, so
               `sum by (harness) (st_agents)` is the roster size.
      work     the busy/idle verdict, which ONLY a live pane has. Folding `busy`
               into the state label would double-count it against `up`, and would
               make an agent that is down indistinguishable from one nobody looked
               at — the distinction `_crew_states` exists to preserve.
      stopped  the deliberate-stop record. An `st stop`ped agent is genuinely
               `down`; that it was somebody's DECISION is a second fact about the
               same agent, not a third state (aegis-k9068 — tend used to explain
               every deliberate stop as a fault).

    The verdicts come from `_crew_states`, never a second computation, for the
    reason `_crew_count` gives about `--count`: reimplementing the judgment is how
    the number a dashboard shows drifts from the roster a human just read. One
    pass over it, so this costs the pane captures once.

    None, not empty, when the read fails. Empty publishes zero agents, and zero
    agents is a real and alarming fleet state that must never be manufactured by an
    unreadable registry.
    """
    try:
        rows = list(_crew_states(agents, panes, runtime))
    except Exception:
        return None
    try:
        stopped_now = set((_stops(a).all().exact() or {}))
    except Exception:
        stopped_now = set()
    state: dict[tuple[str, str], int] = {}
    work_c: dict[tuple[str, str], int] = {}
    stopped: dict[str, int] = {}
    for ag, pane_state, work, _posture in rows:
        try:
            harness = harness_mod.name_for(ag, root=a.root)
        except Exception:
            harness = "unknown"
        key = pane_state.replace(" ", "_").replace("-", "_")
        state[(harness, key)] = state.get((harness, key), 0) + 1
        if pane_state == "up":
            w = ("unknown" if work in ("?", "—", "") else
                 work.replace(" ", "_").replace("-", "_"))
            work_c[(harness, w)] = work_c.get((harness, w), 0) + 1
        if ag.name in stopped_now:
            stopped[harness] = stopped.get(harness, 0) + 1
    return {"state": state, "work": work_c, "stopped": stopped}


def _ready_count(root, reg):
    """How many beads are ready to work, or None for COULD NOT LOOK.

    None is not zero and must never render as zero.  An unreadable tracker may
    not authorise growth, and an empty queue and an unanswered question are
    different facts with different fixes — the rule `shantytown.answer` exists
    to enforce, applied to the one input that can turn an advisory into "+6".
    """
    try:
        from . import feed_check
        return len(feed_check.TrackerAdapter(root, reg).ready().exact())
    except Exception:
        return None


def _utilization(harness, *, readings, policy, verdict, live, now, advisory,
                 root, reg=None, blocked=0):
    """Occupancy for one harness, paying for the tracker query only if it matters.

    `assess` is pure and is called twice rather than handed a callable: the first
    pass decides whether a ready-work count could change the answer, and only
    then is one read.  A status bar polling `st crew --governor` every few
    seconds therefore spawns no tracker read while the fleet is at cap or over
    its pace bound, which is most of the time.

    Creel's delta is READ OFF Creel's own published line, never recomputed.
    """
    kw = dict(readings=readings, policy=policy, cap=verdict.max_agents,
              live=live, now=now, blocked=blocked,
              creel_delta=creel_advisory_mod.recommended_delta(advisory))
    seen = util_mod.assess(harness, ready=None, **kw)
    if seen.needs_ready:
        seen = util_mod.assess(harness, ready=_ready_count(root, reg), **kw)
    return seen


def _crew_count(agents, panes, runtime, untracked_root=None) -> int:
    """`st crew --count` — print `busy/total`, nothing else.

    TOTAL IS NOT THE ROSTER SIZE. It is the number of agents we can actually
    answer busy-or-idle for; an agent whose verdict is unknown (down, no pane,
    a pane with no runtime UI, a wedge) is in NEITHER number. Counting the
    unknowns into the denominator would render `3/9` when four of the nine were
    never asked — a made-up capacity figure that reads exactly like a measured
    one, which is the failure this repo keeps naming (`up` for a deaf agent,
    CLEAR for a check that could not reach its target).
    """
    busy = idle = 0
    for _ag, _state, work, _posture in _crew_states(
            agents, panes, runtime, untracked_root=untracked_root):
        if work == triage_mod.BUSY:
            busy += 1
        elif work == triage_mod.IDLE:
            idle += 1
    print(f"{busy}/{busy + idle}")
    return OK


def _cmd_roles(a) -> int:
    if not a.check:
        try:
            agents = _registry(a).all().exact()
        except Exception as e:
            print(f"  could not tell: {e}", file=sys.stderr)
            return CANNOT_TELL
        print()
        for ag in sorted(agents, key=lambda x: x.name):
            print(f"  {ag.name:<11} {ag.role:<14} "
                  f"reports_to: {ag.reports_to or '—'}")
        print()
        return OK

    # #6.4: hand check the hook READER, so `hooks: ok` reports the settings file
    # `role set` actually emitted instead of naming a column.
    # aegis-0v97: and hand it the LIVE reader too, so the check measures the
    # running process, not only the artifact its role would have emitted. The
    # artifact was green for a lead whose live process had no stop hooks at all.
    panes = _panes(a)
    # aegis-610jv: and the GUARD reader, so `roles --check` can see the host
    # policy guard it was blind to. Read off disk through the card's own harness,
    # because a codex role's artifact is a config.toml in a different place — the
    # unguarded cards this leg exists to find are precisely the codex ones.
    rep = roles_mod.check(_registry(a),
                          emitted=lambda card: emitted_stop_directions(
                              a.root, card.role,
                              harness_mod.name_for(card, root=a.root)),
                          live=lambda pane: live_wiring(pane, panes.cmdline),
                          catalog=_catalog(a),
                          guard=lambda card: emitted_bash_guard(
                              a.root, card.role,
                              harness_mod.name_for(card, root=a.root)),
                          # WITH THE STORE ROOT. Without it the lookup sees only
                          # the ambient environment, never the store's shantytown.toml,
                          # and every unguarded card downgrades from BROKEN to a
                          # silent unverified — measured on the live store.
                          # `or ""` distinguishes NOT CONFIGURED (ordinary) from
                          # COULD NOT TELL (None) — see roles.check.
                          guard_configured=bash_guard_command(a.root) or "",
                          pre_edit=lambda card: emitted_pre_edit_guard(
                              a.root, card.role,
                              harness_mod.name_for(card, root=a.root)),
                          # Claude Code is the measured edit-hook surface. Codex
                          # has no equivalent matcher today, so it stays visibly
                          # unverified instead of being pronounced healthy.
                          pre_edit_expected=lambda card: (
                              pre_edit_guard_command()
                              if harness_mod.name_for(card, root=a.root) == "claude"
                              else ""))
    print()
    print(rep.render())
    print()
    return {roles_mod.OK: OK,
            roles_mod.BROKEN: REFUSED,
            roles_mod.CANNOT_TELL: CANNOT_TELL}[rep.verdict]


def _declarers(catalog, band: str) -> list[str]:
    """Which declared roles carry this survival band. Sorted, possibly empty.

    ASKED OF THE CATALOG, never matched by name. `[roles.drains-last] survival =
    "last"` is the shape a deployment actually writes, so a role's NAME says
    nothing about its band — the live config declares `normal` and `support` as
    same-named roles and would make a name-matching bug invisible. This is the
    same refusal test_roles_band's stub is built around.
    """
    out = []
    for role in catalog.known():
        try:
            if getattr(catalog.of(role), traits_mod.SURVIVAL, None) == band:
                out.append(role)
        except Exception:      # noqa: BLE001 — UnknownRole / AmbiguousTrait / anything
            continue
    return sorted(out)


def _cmd_band(a) -> int:
    """band <agent> <first|normal|support|last> — DECLARE a card's survival band.

    aegis-ftmfn. The band decides whether an agent is still running after a usage
    throttle, and until now no verb wrote one. `roles set` writes the TREE
    POSITION, so `st roles set billy normal` is refused as a depth violation
    (correctly — `normal` is not a place in the tree), and the only remaining way
    was to hand-edit the card's `roles` array. Twenty cards were banded that way
    and three were missed. Nothing detected it, because a missing band and a band
    decided-`normal` resolve identically at the governor.

    IT WRITES A ROLE, NOT A FIELD, and that is not indirection for its own sake:
    `traits` composes survival from the ROLE STACK, so a `survival` key on the
    card would be a second source for one axis — the two would disagree the first
    time anybody used `roles set`, and the governor reads only the composed one.
    So this resolves the band to the role the DEPLOYMENT declares for it and puts
    that role on the stack.

    Three things it refuses to do quietly:
      · guess, when several roles declare the band (--via names one)
      · leave a second band-carrying role on the stack, where precedence would
        silently resolve a band the operator did not ask for
      · trust its own arithmetic — it RECOMPOSES the resulting stack through the
        same function `roles --check` prints from, and refuses if that does not
        come back as the band requested
    """
    catalog = _catalog(a)
    band = a.band
    if band not in traits_mod.SURVIVAL_BANDS:
        print(f"  refused: unknown survival band {band!r}. The four, in order: "
              f"{' < '.join(traits_mod.SURVIVAL_BANDS)} — `first` is shed FIRST "
              f"and `last` is shed LAST.", file=sys.stderr)
        return REFUSED

    declares = _declarers(catalog, band)
    if a.via is not None:
        if a.via not in declares:
            print(f"  refused: role {a.via!r} does not declare survival "
                  f"{band!r}. Roles that do: {', '.join(declares) or '(none)'}",
                  file=sys.stderr)
            return REFUSED
        declares = [a.via]
    if not declares:
        # A REFUSAL, NOT AN INVENTION. st could mint a role carrying the band,
        # and that would put a role in the catalog that the deployment's own
        # config file does not mention — the closed-enum problem this whole
        # model exists to kill, re-created by the convenience verb.
        print(f"  refused: no declared role carries survival {band!r}. Declare "
              f"one in shantytown.toml:\n\n      [roles.{band}]\n      "
              f"survival = \"{band}\"\n", file=sys.stderr)
        return REFUSED
    if len(declares) > 1:
        print(f"  refused: {len(declares)} roles declare survival {band!r}: "
              f"{', '.join(declares)}. Which one this card should carry is a "
              f"deployment decision, not a guess — name it with "
              f"--via <role>.", file=sys.stderr)
        return REFUSED
    carrier = declares[0]

    files = FilesRegistry(a.root / "crew")
    try:
        card = files.get(a.agent)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED

    before_band = roles_mod.band_of(catalog, card)
    stack = list(card.effective_roles())

    # DROP ANY OTHER BAND-CARRIER FIRST. Two banded roles on one stack is not an
    # error — `survival` is a SINGLE axis and precedence resolves it — which is
    # precisely the danger: asking for `normal` on a card already carrying
    # `drains-last` would compose back to `last`, silently, and the command would
    # report success having done the opposite of what it was told.
    dropped = []
    for r in list(stack):
        try:
            other = getattr(catalog.of(r), traits_mod.SURVIVAL, None)
        except Exception:      # noqa: BLE001 — an unresolvable role is not a band-carrier we can drop
            continue
        if other is not None and r != carrier:
            stack.remove(r)
            dropped.append((r, other))
    if carrier not in stack:
        stack.append(carrier)

    after = replace(card, roles=tuple(stack))
    after_band = roles_mod.band_of(catalog, after)
    if after_band != band:
        # VERIFY BY MECHANISM, NOT BY THE WRITE HAVING SUCCEEDED. Everything above
        # is reasoning about what the catalog will do; this asks it. A stacked
        # conflict that nothing ranks composes to `?` here rather than a band, and
        # `?` means the governor FAILS OPEN — the agent runs through every tier.
        # Reporting "band set to first" over that would be the exact inversion.
        print(f"  refused: writing {carrier!r} onto {a.agent}'s stack "
              f"{stack} does NOT resolve to {band!r} — it resolves to "
              f"{after_band!r}. Nothing written.", file=sys.stderr)
        return REFUSED

    print(f"  {a.agent}: band {before_band} -> {after_band}")
    print(f"  roles   {list(card.effective_roles())} -> {stack}")
    for r, b in dropped:
        print(f"  dropped {r!r} (declared survival {b!r}) — one band per card")
    if not card.roles:
        # The migration, said out loud. An empty stack means NOBODY SAID, and
        # writing one is a decision about more than the band: from here the card
        # carries an explicit set, and `roles set` will no longer be the only
        # thing describing it.
        print(f"  note    {a.agent} carried no role stack (it read as its tree "
              f"position, {card.role!r}); it now carries one explicitly.")
    # `normal (UNSET)` and `normal` are the SAME ANSWER at the governor, and the
    # comparison has to know that or the note never fires for the three cards
    # that motivated this verb — which are exactly the unset ones.
    if (before_band == after_band
            or (before_band == roles_mod.UNSET_BAND and after_band == traits_mod.DEFAULT_BAND)):
        # SAY THAT THE BEHAVIOUR DID NOT CHANGE, or this reads as a no-op and the
        # next reader deletes it. `normal (UNSET)` -> `normal` changes nothing at
        # the governor; what it changes is that the band is now a DECISION on the
        # record instead of an absence, which is the only difference a reviewer
        # could not previously see.
        print(f"  note    the governor already resolved {a.agent} to this band. "
              f"Nothing about a throttle changes — what changes is that it is "
              f"now DECLARED rather than unset.")

    if a.dry_run:
        print("\n  --dry-run: nothing written.")
        return OK
    files.set(after)
    print(f"  wrote   {a.root / 'crew' / (a.agent + '.json')}")
    return OK


def _would_break(files, graph_agents, catalog):
    """The cards this sync would NEWLY break. `[(name, measured reason)]`.

    Builds the crew that WOULD EXIST and asks `roles.faults` — the same function
    `--check` is built on — about it. Two details make the hypothetical honest:

      · a card the graph does not mention is CARRIED THROUGH UNCHANGED, because
        that is what sync does with it. Leaving those out would ask the question
        about a smaller crew than the one that results, and a lead is only
        resolvable against the whole set: drop the untouched cards and every
        remaining `reports_to` pointing at one turns into a false `lead is not in
        the registry`.
      · for a card that IS in the graph we replace role and reports_to ONLY,
        mirroring FilesRegistry.set, which preserves every other field. Taking the
        graph's Agent wholesale would silently blank the stacked role set and make
        `unattached by role` — the one legitimate reason to have no lead — read as
        an orphan.

    Best-effort by construction, never fatal: an unreadable registry means we
    could not ask, and returning "nothing would break" from here is a false
    all-clear. It cannot happen quietly — every caller reached this line through
    `files.get` already — but if it does, the caller's existing refusals stand.
    """
    try:
        before = files.all().exact()
    except Exception:      # noqa: BLE001 — no registry to read; see docstring
        return []
    current = {c.name: c for c in before}
    after = dict(current)
    for ag in graph_agents:
        cur = current.get(ag.name)
        after[ag.name] = (replace(cur, role=ag.role, reports_to=ag.reports_to)
                          if cur is not None else ag)
    return roles_mod.newly_broken(before, list(after.values()), catalog)


def _cmd_project(a) -> int:
    """Materialize the crew cards FROM the graph. quipu is the authority;
    the cards are a generated projection — writes go to the graph, reads may come
    from the card, NEVER the reverse. Regenerating is idempotent; hand-edits are
    overwritten on the next project, which is the point.

    Refuses (2) if the graph is unreachable — a projection you could not source
    is not an empty projection. It projects the graph AS-IS, orphans included, so
    `roles --check` still surfaces them rather than project hiding a bad graph.

    IT ALSO SHOWS ITS WORK AND REFUSES TO RESTRUCTURE A LIVE CREW SILENTLY
    (aegis-0v97). "Hand-edits are overwritten, which is the point" is true of a
    clean graph. It is catastrophic against a dirty one, and ours is dirty:
    measured 2026-07-20, the graph declares `a-backup-host` (a HOST — dolt/garage backups
    live on it) and `mayor` (which this fleet has stated does not exist and never
    will) as crew workers, plus two agents with no card and no session. Projecting
    that would have demoted the live administrator to an orphan worker, cut nine
    running agents loose, and materialized cards for a host and a ghost — with no
    preview and no confirmation, because this function used to be a bare
    `for ag in agents: files.set(ag)`.

    So: always print the diff; write nothing on --dry-run; and REFUSE (1) when the
    projection would change the role or supervisor of an agent that is LIVE RIGHT
    NOW, unless --force. Being the declared authority is not the same as being
    right, and a projection that cannot be previewed is a footgun regardless of
    which side of the divergence is correct.
    """
    # aegis-t4eve: the source is chosen here, not hardcoded, and it is PRINTED.
    # Ontology-first with file-fallback by default; an explicit --from is never
    # silently substituted (asking for quipu and getting a stale file without
    # being told is how the file becomes the authority).
    from . import hierarchy as hier_mod
    try:
        spec = getattr(a, "from_source", None)
        # Inject cli's QuipuRegistry rather than letting hierarchy bind its own:
        # the graph source stays patchable at ONE name (tests/test_project_guard
        # monkeypatches `cli.QuipuRegistry`), and the seam stays injectable.
        source, src_info = hier_mod.resolve(
            spec, file_default=hier_mod.default_file(a.root),
            quipu_factory=QuipuRegistry)
        agents = source.all().exact()
    except ValueError as e:                      # a mistyped --from is usage, not outage
        print(f"  {e}", file=sys.stderr)
        return REFUSED
    except Exception as e:
        print(f"  could not project: {e}", file=sys.stderr)
        return CANNOT_TELL
    print(f"  {src_info.render()}")

    # ZERO agents from a REACHABLE graph is almost never "no crew" — it is a
    # wrong namespace (SHANTY_ONTO_NS unset -> the library's example default,
    # which holds none of any real fleet's facts) answering "nobody exists"
    # with a straight face. This used to fall through to "already projected:
    # 0 cards match the graph. Nothing to do." — a false pass ellie documented
    # and aegis-wxrm asked to close. Could-not-tell, not success.
    if not agents:
        ns_hint = ("" if src_info.kind != "quipu" else
                   " — wrong namespace? (SHANTY_ONTO_NS is "
                   f"{'set' if os.environ.get('SHANTY_ONTO_NS') else 'UNSET — using the library example default'})")
        print(f"  could not project: {src_info.detail} answered but returned "
              f"ZERO CrewMembers{ns_hint}",
              file=sys.stderr)
        return CANNOT_TELL

    files = FilesRegistry(a.root / "crew")
    panes = _panes(a)
    dry = getattr(a, "dry_run", False)
    force = getattr(a, "force", False)

    def live(name: str) -> bool:
        """Is this agent RUNNING? Liveness comes from the card's pane, because the
        graph has no idea what is running — which is the whole reason it must not
        be allowed to restructure the crew unsupervised."""
        try:
            card = files.get(name)
        except LookupError:
            return False
        return bool(card.pane) and panes.exists(card.pane)

    changes, harm = [], []
    for ag in sorted(agents, key=lambda x: x.name):
        try:
            cur = files.get(ag.name)
            before = (cur.role, cur.reports_to)
        except LookupError:
            cur, before = None, None
        after = (ag.role, ag.reports_to)
        if before == after:
            continue
        is_live = live(ag.name)
        changes.append((ag.name, before, after, is_live, cur is None))
        if is_live:
            harm.append(ag.name)

    # The subtle one, and the reason a per-agent diff is not enough: an agent that
    # is NOT in the graph is left untouched, so it keeps pointing at a supervisor
    # this projection may just have demoted. Nobody's own row shows that.
    demoted = {n for n, b, af, _l, _new in changes
               if b and b[0] in ("administrator", "lead") and af[0] not in ("administrator", "lead")}
    dangling = []
    if demoted:
        graph_names = {ag.name for ag in agents}
        for p in sorted((a.root / "crew").glob("*.json")):
            nm = p.stem
            if nm in graph_names:
                continue
            try:
                card = files.get(nm)
            except LookupError:
                continue
            if card.reports_to in demoted:
                dangling.append((nm, card.reports_to, live(nm)))

    if not changes:
        print(f"\n  already projected: {len(agents)} cards match the graph. Nothing to do.")
        # CONSISTENCY IS NOT CORRECTNESS, AND THIS LINE READ AS BOTH (aegis-uymsl).
        #
        # Sibling of the zero-agents false pass fixed above, and the same defect
        # one step along: there, "0 cards match. Nothing to do." reported a wrong
        # namespace as success; here, "20 cards match. Nothing to do." reports a
        # ROUND TRIP as validation.
        #
        # When the source is QUIPU, the graph's crew facts are what THIS COMMAND
        # projects FROM the cards. So a clean match proves the sync worked — it
        # cannot detect a roster that was wrong when it was written, because the
        # thing it is checked against was written from it. Measured on this fleet:
        # `st roles sync --dry-run` reported 20/20 clean and a SPARQL count agreed
        # at 20, while the operator's actual decision existed in no machine-readable
        # form at all. Two instruments agreeing is not two instruments being right.
        #
        # A FILE source is different and is deliberately not warned about: it is an
        # independent referent someone wrote down, so a match against it is a real
        # check. That is the whole reason to configure one, and this message is
        # where an operator finds that out.
        if src_info.kind == "quipu":
            print("  ⚠ CONSISTENCY, not correctness: the graph's crew facts are "
                  "projected FROM these cards, so this compares the copy to the "
                  "copy. It proves the sync worked; it CANNOT tell you the roster "
                  "is the one that was decided. Configure an independent referent "
                  "— `st roles sync --from file:<path>` — and this becomes a real "
                  "check.", file=sys.stderr)
        print()
        return OK

    print(f"\n  {len(changes)} card(s) would change:\n")
    for name, before, after, is_live, is_new in changes:
        mark = "LIVE " if is_live else "     "
        if is_new:
            print(f"  {mark}+ {name:<10} NEW CARD -> {after[0]}, reports_to {after[1] or '—'}")
        else:
            print(f"  {mark}~ {name:<10} {before[0]} -> {after[0]}, "
                  f"reports_to {before[1] or '—'} -> {after[1] or '—'}")
    if dangling:
        print(f"\n  and {len(dangling)} card(s) NOT in the graph would be left pointing at a "
              f"demoted supervisor:")
        for nm, sup, is_live in dangling:
            print(f"  {'LIVE ' if is_live else '     '}! {nm:<10} still reports_to {sup}")

    # WOULD THIS SYNC MANUFACTURE AN ORPHAN? (aegis-ftmfn) Asked of the crew that
    # WOULD EXIST, using `roles --check`'s own definition — never a second one.
    #
    # This is not covered by any check above it, and the gap was measured. The
    # diff showed `grant  worker -> worker, reports_to sattler -> —` on its own
    # row: a change so small it reads as noise, and it is the one that leaves an
    # agent with nowhere to send its stop events. Nothing said so. The dangling
    # check next door is a different fault (a lead that got DEMOTED, not one that
    # went away), and it only looks at cards absent from the graph. So the single
    # outcome `--check` exists to catch was the one `sync` could not see.
    #
    # PRINTED EVEN ON --dry-run, and that is most of the value: a dry-run is what
    # an operator runs to decide, and this finding is exactly the thing they
    # cannot derive from the rows.
    broke = _would_break(files, agents, _catalog(a))
    if broke:
        print(f"\n  and {len(broke)} card(s) would be NEWLY BROKEN — "
              f"not broken now, broken after:")
        for nm, why in broke:
            print(f"  {'LIVE ' if live(nm) else '     '}! {nm:<10} {why}")

    if dry:
        print("\n  --dry-run: nothing written.\n")
        return OK

    # BOTH refusals are reported, never one hiding behind the other — the same
    # rule `roles._fold` states for two legs of one row. An operator who is told
    # only about the live restructure adds --force, and the orphan lands silently
    # on the retry.
    refused = False
    if harm and not force:
        refused = True
        print(f"\n  REFUSED: {len(harm)} LIVE agent(s) would be restructured: "
              f"{', '.join(sorted(harm))}.", file=sys.stderr)
        print("  They are running right now. Projecting would change their role or "
              "supervisor underneath them.", file=sys.stderr)
        print("  Reconcile the graph first, or re-run with --force if you mean it.\n",
              file=sys.stderr)
    if broke and not getattr(a, "allow_breakage", False):
        refused = True
        # DELIBERATELY NOT --force. The two flags consent to different things:
        # --force says "yes, restructure agents that are running", which is a
        # statement about TIMING. This says "yes, leave a card with nowhere to
        # send its stop events", which is a statement about the RESULT, and it is
        # true whether or not anything is running. Folding it into --force would
        # mean the operator who cleared the first refusal was never asked the
        # second — which is exactly the measured near-miss: the live-restructure
        # guard was the only thing standing between `sync --force` and an orphan
        # it never mentioned.
        print(f"\n  REFUSED: {len(broke)} card(s) would be NEWLY BROKEN: "
              f"{', '.join(nm for nm, _ in broke)}.", file=sys.stderr)
        print("  `roles --check` calls this broken because such a card has nowhere "
              "to send its stop events.", file=sys.stderr)
        print("  --force does NOT cover this. Fix the source, or re-run with "
              "--allow-breakage if you mean it.\n", file=sys.stderr)
    if refused:
        return REFUSED

    for ag in sorted(agents, key=lambda x: x.name):
        files.set(ag)
    print(f"\n  projected {len(agents)} cards from the graph -> {a.root / 'crew'}\n")
    return OK


def _resolve_repo(repo: str) -> Path:
    """A shared repo, as a path OR a bare name under $GT_ROOT (~/gt).

    A bare name normally means ``$GT_ROOT/<name>``. When that path exists but is
    a Gas Town rig rather than a checkout, and ``$GT_ROOT/<name>-src`` carries
    its own ``.git`` marker, the source checkout wins. This is the Hank layout:
    ``~/gt/hank`` owns ``.beads`` and a bare ``.repo.git`` cache, while
    ``~/gt/hank-src`` is the editable scbrown/hank checkout (aegis-gbuap).

    The own ``.git`` marker is load-bearing. ``~/gt`` is itself a repository,
    so asking Git whether ``~/gt/hank`` is in a worktree returns ``~/gt`` and
    silently blesses the wrong scope. Parent-repository inheritance must never
    qualify a child as the requested checkout.

    A BARE NAME IS ALWAYS $GT_ROOT/<name>, NEVER CWD-RELATIVE (aegis-k3i8t).
    This used to carry an `or p.exists()` clause, evaluated against the CWD, and
    it fired BEFORE the $GT_ROOT branch — so a bare name silently resolved to
    `./<name>` whenever the cwd happened to contain a directory of that name.

    That is not a rare coincidence, it is the normal case: a Python repo holds a
    package directory named after the repo, so `./shantytown` exists inside every
    shantytown checkout AND every shantytown worktree. `st push shantytown <me>`
    — the documented form — therefore failed SPECIFICALLY in the tree you are
    standing in when you push, which is the only place anyone runs it. Same for
    quipu, hank, bobbin. Measured 2026-08-04: refused with "no worktree at
    shantytown-wt/franklin" while that worktree existed; the absolute-path form
    worked.

    The refusal was the benign symptom. `st go <item> <agent> --worktree <repo>`
    does not refuse — it calls ensure_worktree() on whatever comes back, so a
    coordinator dispatching from inside a worktree CREATED a nested one and
    handed the agent a wrong path, silently.

    A relative path is still honoured, because a relative path has a separator
    (`./quipu`, `../quipu`) — which is exactly what distinguishes "a path I mean
    literally" from "a repo name". Bare names have one meaning, in every cwd.
    """
    p = Path(repo).expanduser()
    if p.is_absolute() or "/" in repo:
        return p
    root = Path(os.environ.get("GT_ROOT", Path.home() / "gt"))
    direct = root / repo
    source = root / f"{repo}-src"
    if not (direct / ".git").exists() and (source / ".git").exists():
        return source
    return direct


def _resolve_push_worktree(repo: str, agent: str) -> Path:
    """Resolve an EXISTING agent worktree for ``st push``.

    Bare repo names may live in either of the two established source roots on
    this host: ``$GT_ROOT`` (normally ``~/gt``) or ``$WORKSPACE_ROOT``
    (normally ``~/workspace``).  Push must follow the worktree that already
    exists; resolving the shared checkout under ``~/gt`` first made a real
    workspace worktree invisible and prescribed creating a misleading duplicate.

    Explicit paths retain their literal meaning.  If the same bare name and
    agent exist under both roots, refuse rather than guessing which repository
    the operator intended to publish.
    """
    p = Path(repo).expanduser()
    if p.is_absolute() or "/" in repo:
        return worktree_for(p, agent)

    gt_root = Path(os.environ.get("GT_ROOT", Path.home() / "gt"))
    workspace_root = Path(os.environ.get(
        "WORKSPACE_ROOT", Path.home() / "workspace"))
    resolved = _resolve_repo(repo)

    # A bare name can be a compatibility alias for a differently named source
    # checkout (currently hank -> hank-src).  Resolve that alias BEFORE looking
    # for an existing worktree: otherwise a stale hank-wt left by the archived
    # repository captures `st push hank`, even while `st worktree hank` correctly
    # provisions from the active hank-src checkout.
    gt_worktree = worktree_for(resolved, agent)
    candidates = [gt_worktree, workspace_root / f"{repo}-wt" / agent]
    matches = [path for path in dict.fromkeys(candidates) if path.is_dir()]
    if len(matches) > 1:
        listed = ", ".join(str(path) for path in matches)
        raise WorkspaceError(
            f"ambiguous worktree for {repo}/{agent}; found {listed}. "
            "Pass the shared checkout or worktree path explicitly."
        )
    if matches:
        return matches[0]
    return gt_worktree


def _push_invocation_branch(dest: Path) -> tuple[Path, str] | None:
    """Return the caller's worktree + branch when it belongs to ``dest``'s repo.

    A linked worktree has its own git-dir but shares one git *common* directory.
    Comparing the common directory is therefore the discriminating check: a
    caller elsewhere in the filesystem must not affect the established
    ``st push <repo> <agent>`` behavior, while a caller inside another worktree
    of this exact repository is expressing a branch choice we must not ignore.
    """
    import subprocess

    cwd = Path.cwd()

    def common(path: Path) -> Path | None:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute",
             "--git-common-dir"], capture_output=True, text=True, timeout=5,
        )
        return Path(r.stdout.strip()).resolve() if r.returncode == 0 else None

    cwd_common, dest_common = common(cwd), common(dest)
    if dest_common is None or cwd_common != dest_common:
        return None
    branch = subprocess.run(
        ["git", "-C", str(cwd), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True, text=True, timeout=5,
    )
    return cwd, branch.stdout.strip() if branch.returncode == 0 else "(detached HEAD)"


def _cycle_anchor_bead(a, agent: str) -> str:
    """The bead a --checkpoint-file lands on when none was named.

    The agent's active plate item: what it is mid-task on is, by construction,
    where a reader will look for its checkpoint. Best-effort — a checkpoint with
    nowhere obvious to go must not block the cycle, it just degrades to the reason
    line, and the caller says so out loud.
    """
    try:
        plate = _tracker_plate(_tracker(a), agent)
    except Exception:
        return ""
    if plate is None:
        return ""
    iid = getattr(plate, "id", None) or (plate.get("id") if isinstance(plate, dict) else None)
    return str(iid) if iid else ""


def _cmd_cycle(a) -> int:
    """cycle <agent> — clear context WITHOUT destroying the runtime (aegis-3laza).

    `/clear` is the wrong primitive and this fleet paid for it five times in one
    session. It cannot be invoked BY the agent that needs it; it drops the session
    out of bypass into MANUAL, so the remedy needs its own remedy; and the depth
    signal meant to trigger it read `ok` for an agent that was self-reporting
    saturation.

    The sequence that works — found by hand five separate times before it was a
    verb — is stop-with-a-reason then relaunch, because `st new` RESTORES what
    `/clear` destroys: bypass, the MCP kit, skills, journaling, and a verification
    that the stop hooks are live on the new process.

    ORDER: assess -> stop -> launch -> re-dispatch. The guard runs first and to
    completion, because everything after it is irreversible from here.
    """
    from . import cycle as cycle_mod

    agent_name = a.agent or os.environ.get("SHANTY_AGENT", "")
    if not agent_name:
        print("  refused: no agent. `st cycle <agent>`, or `--self` with "
              "$SHANTY_AGENT set.", file=sys.stderr)
        return REFUSED

    # --self is a REQUEST, and it cannot be anything else. The stop would kill the
    # session running this very command, so a self-cycle that tried to complete
    # in-process would die halfway through — after the stop, before the relaunch,
    # which is the one outcome worse than not cycling at all.
    if getattr(a, "self_", False):
        # --checkpoint-file: the file IS the checkpoint (aegis-x6yoq). Read it
        # FIRST, so a bad path refuses before anything is recorded, and so the
        # reason is derived rather than hand-composed under context pressure.
        ckpt_file = getattr(a, "checkpoint_file", "").strip()
        ckpt_body = ""
        if ckpt_file:
            try:
                ckpt_body = Path(ckpt_file).read_text().strip()
            except OSError as e:
                print(f"  refused: --checkpoint-file {ckpt_file!r} could not be read ({e}). "
                      f"Nothing was recorded; your context is untouched.", file=sys.stderr)
                return REFUSED
            if not ckpt_body:
                print(f"  refused: --checkpoint-file {ckpt_file!r} is empty. The checkpoint is "
                      f"the one thing a cycle destroys — write it first.", file=sys.stderr)
                return REFUSED
            if not a.reason.strip():
                # First non-blank line, shorn of markdown heading marks. The whole
                # file goes on the bead; the reason is only the pointer to it.
                first = next((ln.strip().lstrip("#").strip()
                              for ln in ckpt_body.splitlines() if ln.strip()), "")
                a.reason = first[:300]
        if not a.reason.strip():
            print("  refused: --self needs your checkpoint. You are the only one "
                  "who can write it, and it is the only thing the cycle destroys. "
                  "`st cycle --self --checkpoint-file <notes>` (or -r '<what you "
                  "are mid-task on, decisions already made, the exact next step>')",
                  file=sys.stderr)
            return REFUSED
        if a.dry_run:
            print(f"  would: request a cycle for {agent_name}")
            return OK
        checkpoint_bead = getattr(a, "checkpoint_bead", "").strip()
        try:
            role = _registry(a).get(agent_name).role
        except Exception as e:
            print(f"  refused: could not read {agent_name}'s role for checkpoint policy ({e})",
                  file=sys.stderr)
            return REFUSED
        if cycle_mod.requires_checkpoint_bead(role, checkpoint_bead):
            print("  refused: administrator cycles require --checkpoint-bead <id>. "
                  "Create or use the durable handoff bead, then retry.", file=sys.stderr)
            return REFUSED
        if checkpoint_bead:
            try:
                _tracker(a).get(checkpoint_bead)
            except Exception as e:
                print(f"  refused: checkpoint bead {checkpoint_bead!r} could not be read ({e})",
                      file=sys.stderr)
                return REFUSED
        # Post the checkpoint file to the bead BEFORE recording the request, so a
        # failed comment cannot leave a request pointing at a checkpoint that was
        # never written down. Best-effort by design: if the tracker is unreachable
        # the cycle must still be requestable — an agent at 600k that cannot hand
        # off because Dolt is flapping is strictly worse off than one whose notes
        # live only in the request record.
        posted_to = ""
        if ckpt_body:
            target = checkpoint_bead or _cycle_anchor_bead(a, agent_name)
            if target:
                try:
                    # Beads-specific by construction: appending a checkpoint
                    # comment is not part of the three-verb tracker contract
                    # (test_swap pins it), so this reaches the beads helper
                    # directly and other backends degrade below rather than
                    # pretend. See beads.append_comment.
                    if _backend(a, "files") not in ("beads", "br"):
                        raise RuntimeError(
                            f"backend {_backend(a, 'files')!r} cannot append a "
                            f"comment; checkpoint kept as the reason line")
                    trk = _tracker(a)
                    from .br import append_comment as br_append_comment
                    br_append_comment(trk, target, ckpt_body)
                    posted_to = target
                except Exception as e:
                    print(f"  ⚠ checkpoint file NOT posted to {target} ({e}). "
                          f"Cycle still requested; the reason line carries the gist, "
                          f"but re-post the file when the tracker is reachable.",
                          file=sys.stderr)
            else:
                print("  ⚠ no checkpoint bead and no active anchor to post to — "
                      "the file's first line is recorded as the reason only.",
                      file=sys.stderr)

        # GRAPH CONTEXT ON THE RESUME DISPATCH (aegis-rcyd.1). A cycle request is
        # where the NEXT session's first minute is decided, so it carries the same
        # requirement `st go` does: an exact existing node, or a stated reason.
        # Checked before the request is written — a refusal here loses nothing,
        # because the checkpoint has already been posted to the bead above and the
        # agent stays up either way.
        gctx, refusal = _graph_context(a)
        if refusal:
            print(f"  refused: {refusal}", file=sys.stderr)
            print(f"  the checkpoint above is already posted; re-run this with "
                  f"graph context and nothing is lost.", file=sys.stderr)
            return REFUSED
        quipu_nodes = list(gctx.nodes)
        cycle_mod.Requests(a.root).request(agent_name, a.reason.strip(),
                                           checkpoint_bead or posted_to,
                                           quipu_nodes)
        graph_adoption.record(a.root, "cycle", agent_name,
                              checkpoint_bead or posted_to or "-", gctx)
        print(f"  {agent_name}: cycle REQUESTED — checkpoint recorded.")
        if posted_to:
            print(f"  checkpoint file posted to {posted_to}.")
        if quipu_nodes:
            print(f"  graph context recorded: {', '.join(quipu_nodes)} "
                  f"— named in your resume dispatch.")
        elif gctx.exemption:
            print(f"  no graph context: {gctx.exemption} — recorded.")
        print(f"  `st tend` performs it. You stay up until it does, so keep "
              f"working; nothing is lost if it never fires.")
        print(f"  {handoff_text.refusal_note()}")
        return OK

    try:
        card = _registry(a).get(agent_name)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED

    # THE GUARD. fetch=True is load-bearing and is the trap the bead names: a tree
    # judged against a STALE remote-tracking ref reports commits as stranded when
    # they are already on origin, and a cycle verb that refuses on a phantom gets
    # routed around and then trusted by nobody. tree_staleness(fetch=True) also
    # prunes, which closes the opposite and worse error — a deleted upstream ref
    # laundering an orphaned commit into "safe".
    trees = _agent_trees(a, card, sweep=True)
    verdict = cycle_mod.assess(
        agent_name, trees, a.reason,
        staleness=lambda t: tree_staleness(t, fetch=True),
        allow_loss=a.allow_loss)
    if not verdict.ok:
        print(f"  refused: {verdict.render()}", file=sys.stderr)
        # aegis-7xptd5: STAMP THE REFUSAL ON THE PENDING REQUEST. The refusal is
        # printed here and nowhere else, so without this the stall exists only in
        # a journal nobody reads — `st crew` saw a request record and could not
        # tell "in flight" from "refused an hour ago". A no-op when nothing is
        # pending, so an operator's ad-hoc cycle of an agent that never asked
        # cannot mint a request.
        cycle_mod.Requests(a.root).mark_refused(
            agent_name, verdict.reason, verdict.risks)
        return REFUSED
    if verdict.risks:
        # --allow-loss was used. SAY WHAT IS BEING SPENT — an override that prints
        # nothing turns a decision into a habit.
        print("  ⚠ proceeding over work that will be stranded (--allow-loss):",
              file=sys.stderr)
        for r in verdict.risks:
            print(f"      {r.render()}", file=sys.stderr)

    if a.dry_run:
        print(f"  would: stop {agent_name} (reason: {verdict.checkpoint})")
        print(f"  would: relaunch {agent_name} — bypass, MCP kit, skills, hooks")
        print(f"  would: re-dispatch its plate item back to it")
        return OK

    # STOP, through the real command so its ownership guards apply unchanged: st
    # only reaps what st launched, and an unstamped session belongs to another
    # orchestrator. A cycle must not become a second way to kill a foreign pane.
    stop_args = argparse.Namespace(**vars(a))
    stop_args.agent = agent_name
    stop_args.reason = f"{cycle_mod.CYCLE_REASON}: {verdict.checkpoint}"
    if (rc := _cmd_stop(stop_args)) != OK:
        print(f"  refused: {agent_name} was not stopped — NOT relaunching. "
              f"A cycle that launches over a session it could not stop is how "
              f"you get two of the same agent.", file=sys.stderr)
        return rc

    # RELAUNCH through the one shared launcher, so the cycle cannot acquire a
    # cheaper version of the pre-flight that `new`/`start`/`attach` all pay.
    panes = _panes(a)
    rc = _launch(a, card, panes, _runtime(a, panes))
    if rc != OK:
        print(f"  could not tell: {agent_name} was stopped but the relaunch did "
              f"not verify (exit {rc}). Its checkpoint is on the stop record. "
              f"`st new {agent_name}` to retry — do NOT assume it is up.",
              file=sys.stderr)
        return CANNOT_TELL

    cycle_mod.Requests(a.root).clear(agent_name)   # only now: the cycle happened
    print(f"  {agent_name}: CYCLED — context cleared, runtime intact.")
    _redispatch_after_cycle(a, agent_name, getattr(a, "checkpoint_bead", ""))
    return OK


def _redispatch_after_cycle(a, agent_name: str, checkpoint_bead: str = "") -> None:
    """Put the agent's plate item back on its hook after a relaunch.

    Today the coordinator hand-re-dispatches after every cycle, which is most of
    what made cycling expensive: five cycles in one session, five manual
    re-dispatches, each one a turn.

    BEST-EFFORT AND LOUD, never fatal. The cycle itself has already succeeded by
    the time this runs — the agent is up with its runtime intact — so a failure
    here must report and stop, not unwind a good relaunch. A fresh agent with no
    plate item runs `st anchor` and finds its own work; that is a slower path, not
    a broken one.
    """
    try:
        tracker = _tracker(a)
        item = _tracker_plate(tracker, agent_name)
    except Exception as e:  # noqa: BLE001 — a tracker fault must not fail the cycle
        print(f"  note: could not read {agent_name}'s plate to re-dispatch "
              f"({e}). It will pick its work up from `st anchor`.",
              file=sys.stderr)
        return
    if item is None:
        print(f"  note: {agent_name} had no plate item to re-dispatch.")
        return
    try:
        d = _wire(a)
        # reassign=True: the item is ALREADY assigned to this agent — that is the
        # whole point — so the assignee guard would otherwise refuse the very
        # re-dispatch the cycle exists to automate.
        checkpoint_note = (f" checkpoint: {checkpoint_bead}." if checkpoint_bead
                           else " checkpoint is on the stop record.")
        d.go(item.id, agent_name, reassign=True,
             note="resumed after a context cycle — your" + checkpoint_note)
        print(f"  re-dispatched {item.id} -> {agent_name}")
    except Exception as e:  # noqa: BLE001
        print(f"  note: {agent_name} is UP but {item.id} was not re-dispatched "
              f"({e}). Send it by hand: `st go {item.id} {agent_name} "
              f"--reassign`.", file=sys.stderr)


def _cmd_worktree(a) -> int:
    """worktree <repo> [<agent>] [--gc] — st PROVISIONS the isolated worktree so
    the agent never runs `git worktree add` by hand (aegis-h2rr).

    A shared project checkout (~/gt/shantytown, quipu, hank-src, goldblum) is
    multi-writer: index and HEAD belong to the working copy, so two agents
    committing there corrupt each other silently (aegis-repg/iaef). Each agent
    gets its own worktree off the shared repo instead — <repo>-wt/<agent> on
    branch wt/<agent>. Provision prints the path (the cwd to work in). --gc removes
    it IFF unchanged; it NEVER discards uncommitted or unpushed work.
    """
    agent = a.agent or os.environ.get("SHANTY_AGENT")
    if not agent:
        print("  refused: no agent. `st worktree <repo> <agent>` or set "
              "$SHANTY_AGENT.", file=sys.stderr)
        return REFUSED
    repo = _resolve_repo(a.repo)
    try:
        if a.gc:
            removed = cleanup_worktree(repo, agent)
            dest = worktree_for(repo, agent)
            print(f"  {'removed' if removed else 'kept (holds work, or absent)'}: {dest}")
            return OK
        path = ensure_worktree(repo, agent)
        # ENFORCE, HAVING JUST ASSISTED (aegis-xig5m). Provisioning a worktree is
        # the moment st KNOWS this shared checkout is in play — so it is the
        # moment to install the guard, and it costs nothing. Doing it here is why
        # there is no new command to remember, which is what left coverage at 4
        # of 10 repos when it was a script somebody had to run.
        #
        # NEVER FATAL TO THE PROVISION. The worktree is the thing the caller
        # asked for and it already exists by this line; failing the command over
        # the seatbelt would deny the isolation to punish the absence of the
        # guard. Loud, then carry on.
        try:
            changed, note = guard_mod.install(repo)
            if changed:
                print(f"  {note}")
        except guard_mod.GuardError as e:
            print(f"  ⚠ guard NOT installed — {e}", file=sys.stderr)
        # BRING IT CURRENT BEFORE HANDING IT BACK (aegis-ib65p decision 1).
        # Provisioning was idempotent-and-inert: an EXISTING worktree was returned
        # untouched, so the second and every later call handed back whatever the
        # tree was months ago. Measured: 12 of 12 shantytown worktrees behind, one
        # of them by 155 commits, on the repo the fleet changes hourly — and the
        # coordinator rebuilt from scratch a fix that already existed.
        #
        # This is the right moment because it is the moment st KNOWS the shared
        # repo is in play — the same argument that put the guard install here, and
        # the reason there is no new command to remember.
        #
        # NEVER FATAL, NEVER FORCED. ff/rebase-only on a CLEAN tree; dirty or
        # conflicted is reported and left exactly as it was. A supervisor that
        # discarded an agent's uncommitted work to make a staleness number reach
        # zero would be a worse bug than the staleness (aegis-repg/iaef).
        if warn := _refresh_worktree(path):
            print(f"  ⚠ {warn}", file=sys.stderr)
        else:
            ref, _ = upstream_ref(path)
            print(f"  current with {ref}")
        print(path)
        return OK
    except WorkspaceError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED


def _cmd_push(a) -> int:
    """push <repo> [<agent>] — push this agent's branch to EVERY remote.

    THE BUG THIS REMOVES (aegis-96few, ruled B by arnold 2026-08-04). shantytown
    has two live remotes, neither a mirror, and each agent's `wt/<name>` branch is
    configured to push to ONE of them — measured 11 agents to forge, 5 to origin.
    So `git push origin wt/$me:main`, the one documented recipe, lands somewhere
    different depending on WHOSE tree runs it, and any two agents on opposite
    sides re-fork the repo the moment both push. Nobody is doing anything wrong:
    every agent is correct from inside their own tree. That is why this is a
    command and not a paragraph asking people to remember the second remote.

    It forked twice in one day this way, and three of the commits left dark by the
    first fork were fixes to the STALENESS DETECTOR — the mechanism whose whole job
    is to tell an agent its tree is behind (aegis-lvc4b).

    NEVER FORCES. A rejection here means the other remote moved and someone's work
    is on it; converging never needs a force, so a force could only destroy work.
    REFUSES, AND NAMES WHICH REMOTE REFUSED — with two live peers, "push rejected"
    cannot be acted on, because "my branch is behind" and "the other remote moved"
    have different next steps.

    FAILS CLOSED ON BRANCH AMBIGUITY (aegis-72png). If the command is invoked
    from another worktree of this repository, that checked-out branch is an
    intentional choice. Refuse when it differs from ``wt/<agent>`` rather than
    silently publishing the canonical branch and deferred work parked on it.
    """
    agent = a.agent or os.environ.get("SHANTY_AGENT")
    if not agent:
        print("  refused: no agent. `st push <repo> <agent>` or set "
              "$SHANTY_AGENT.", file=sys.stderr)
        return REFUSED
    try:
        dest = _resolve_push_worktree(a.repo, agent)
    except WorkspaceError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if not Path(dest).is_dir():
        print(f"  refused: no worktree at {dest} — `st worktree {a.repo} {agent}` "
              f"first.", file=sys.stderr)
        return REFUSED

    branch = f"wt/{agent}"
    invocation = _push_invocation_branch(Path(dest))
    if invocation is not None and invocation[1] != branch:
        caller_path, caller_branch = invocation
        print(
            f"  refused before push: checked-out branch '{caller_branch}' at "
            f"{caller_path} differs from canonical '{branch}' at {dest}. "
            "No remote was contacted. Run st push from the canonical worktree, "
            "or use an explicit git push after reviewing the exact ref.",
            file=sys.stderr,
        )
        return REFUSED
    outcomes = push_every_remote(dest, branch, a.branch)
    if not outcomes:
        print(f"  refused: {dest} has no remotes configured — nothing to push to.",
              file=sys.stderr)
        return REFUSED

    for o in outcomes:
        if o.ok:
            print(f"  {o.remote}: {'already current' if o.up_to_date else 'pushed'}"
                  f" {branch} -> {a.branch}")
        else:
            print(f"  ⚠ {o.reason}", file=sys.stderr)

    refused = [o for o in outcomes if not o.ok]
    if refused:
        landed = [o.remote for o in outcomes if o.ok]
        if landed:
            # SAY THIS OUT LOUD. A partial push is the state most likely to be
            # misread as "the push failed" and retried blindly, and the remotes
            # are now diverged BY THIS COMMAND until the refusal is resolved.
            print(f"  ⚠ PARTIAL: {', '.join(landed)} took it, "
                  f"{', '.join(o.remote for o in refused)} did not. The remotes "
                  f"are diverged until you resolve the refusal above. Nothing is "
                  f"unwound — un-pushing is a rewrite.", file=sys.stderr)
        return REFUSED
    return OK


def _cmd_subscribe(a) -> int:
    """subscribe — watch quipu entity events and route assigned workflows.

    The events adapter integrations.md sketched (`subscribe(kinds)`), finally built
    first-class on Quipu's cursored transaction log. A WATERMARKED POLL: on new
    transactions it asks quipu which governed workflows the graph assigns
    (`aegis:assignsWorkflow`) and routes each NEW one to the administrator — who
    acts (a bead + a nudge). `--once` polls a single batch (exit 0 reachable / 2
    could-not-tell); default loops every `--interval` seconds. State (watermark +
    handled set) persists under `<root>/events`, so a restart resumes rather than
    re-routing what it already handled.
    """
    import time

    from . import quipu_events as qe
    from . import shuttle_runs as sr
    from . import tier

    events = qe.QuipuEvents(server=a.server)
    registry = _registry(a)
    tracker = _tracker(a)
    panes = Tmux()
    try:
        admin = tier._find_administrator(registry)
    except Exception:                              # registry unreachable — route without a target
        admin = None
    state_path = a.root / "events" / "quipu-subscription.json"
    state = qe.SubscriptionState.load(state_path)
    # The shuttle half: workflow runs in quipu's windowed operational graphs
    # (scbrown/shuttle). Same watermarked-poll honesty, its own state file,
    # and its own CANNOT TELL — a store that predates graph kinds reports
    # loudly instead of reading as an empty workload, without failing the
    # quipu-events half beside it.
    runs_source = sr.ShuttleRuns(server=a.server)
    runs_path = a.root / "events" / "shuttle-runs.json"
    runs_state = sr.RunsState.load(runs_path)

    def route(w) -> None:
        # A governed workflow is assigned -> create the work and hand it to the
        # coordinator. Autonomous is fine (owner directive): shantytown orchestrates.
        title = f"workflow {w.iri}"
        if w.label:
            title += f" — {w.label}"
        if w.target:
            title += f" (targets {w.target})"
        try:
            item = tracker.create(title, assignee=admin)
        except Exception as e:
            print(f"  could not create a bead for {w.iri}: {e}", file=sys.stderr)
            return
        mailed = ""
        if admin:
            try:
                card = registry.get(admin)
                if card.pane and panes.exists(card.pane):
                    # Signed as the ROUTER, not as a person (aegis-5vxmz). No
                    # human composed this line — it is quipu's governed-workflow
                    # event turned into an assignment — and "governed workflow
                    # assigned: <id>" arriving unsigned in a coordinator's pane
                    # reads as the operator handing out work.
                    panes.send(card.pane, attribute(
                        f"governed workflow assigned: {item.id} — {title}",
                        attribution_mod.ST_EVENTS))
                    mailed = f", mailed {admin}"
            except LookupError:
                pass
        print(f"  routed {w.iri} -> {item.id}{mailed}")

    def route_run(r, is_new_run: bool) -> None:
        # First sight of a run opens a bead; each later state change nudges
        # the administrator's pane, attributed to the router (aegis-5vxmz) —
        # nobody composed these lines, the graph did.
        short = r.iri.rsplit(":", 1)[-1]
        wf = r.definition.rsplit(":", 1)[-1]
        if is_new_run:
            try:
                item = tracker.create(
                    f"shuttle run {short} ({wf}) — state {r.state}", assignee=admin)
                print(f"  shuttle run {short} -> {item.id}")
            except Exception as e:
                print(f"  could not create a bead for {r.iri}: {e}", file=sys.stderr)
                return
        if admin:
            try:
                card = registry.get(admin)
                if card.pane and panes.exists(card.pane):
                    panes.send(card.pane, attribute(
                        f"shuttle run {short} ({wf}) is now '{r.state}'",
                        attribution_mod.ST_EVENTS))
            except LookupError:
                pass

    def one() -> int:
        report = qe.poll_and_route(events, state, route)
        state.save(state_path)
        print(report.render())
        runs_report = sr.poll_runs(runs_source, runs_state, route_run)
        runs_state.save(runs_path)
        print(runs_report.render())
        return OK if report.reachable else CANNOT_TELL

    if a.once:
        return one()
    try:
        while True:                                # a long-running subscriber
            one()
            time.sleep(max(1.0, a.interval))
    except KeyboardInterrupt:
        return OK


def _not_yet(cmd: str) -> int:
    """A guard, not a stub. EVERY command in the surface is now wired
    (new/stop/log were the last three). Nothing routes here anymore; it exists so
    that a subcommand ADDED to the parser without a handler refuses loudly instead
    of silently doing nothing — the honest failure, not a plausible exit 0. If you
    see this, you added a parser entry and forgot to wire it in main().
    """
    print(f"  refused: `st {cmd}` is in the parser but has no handler wired in "
          f"main(). It is not a stub and will not pretend to work.", file=sys.stderr)
    return REFUSED


# --- tend: the only command that RESTARTS things ----------------------------

def _refresh_clone(path) -> str | None:
    """ff-only pull at a SAFE moment: the agent is down, between items, or being
    relaunched — nothing holds the checkout mid-thought. Returns an error
    string (loud) or None. NEVER raises — a failure here must not stop a
    respawn or a dispatch, because trading an outage for a stale checkout is
    the worse deal. And NEVER anything but --ff-only: force/reset against a
    crew clone is the aegis-repg/iaef data-loss class.

    .MCP.JSON SURVIVES THE PULL (aegis-4zld). The provisioned kit carries a
    live bearer token and is uncommitted BY DESIGN — so history can delete or
    replace a tracked template out from under it, and a refused pull can leave
    it half-restored. The kit is copied aside before the pull and put back if
    the pull removed or changed it: an agent must never come out of a
    keep-current pull with its tools stripped (the five-agents-worked-a-night-
    without-tools class, via a new door)."""
    import subprocess
    try:
        mcp = Path(path) / ".mcp.json"
        saved = mcp.read_bytes() if mcp.is_file() else None
        r = subprocess.run(["git", "-C", str(path), "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=60)
        err = None if r.returncode == 0 else (r.stderr or r.stdout).strip()
        if saved is not None and (not mcp.is_file() or mcp.read_bytes() != saved):
            mcp.write_bytes(saved)
        return err
    except Exception as e:                       # not a repo, git absent, timeout
        return str(e)


def _keep_current(a, agent_name: str) -> str | None:
    """Bring `agent_name`'s workspace clone current (ff-only) — the crew 'Keep
    Current' rule as MECHANISM instead of memory (aegis-4zld; Stiwi's ask).

    Returns None when the workspace is current (or the card names none —
    nothing to pull is not a failure), else a one-line WARNING the caller must
    surface: a stale-but-working agent beats a failed dispatch, but staleness
    must be VISIBLE, never silent (the deploy-lag disease, aegis-daoh/ttlr, is
    exactly invisible staleness). Never raises, never blocks."""
    try:
        card = _registry(a).get(agent_name)
    except Exception:
        return None                    # the dispatch path will name the real error
    if not card.workspace:
        return None
    err = _refresh_clone(card.workspace)
    if err is None:
        return None
    first = err.splitlines()[0] if err else "unknown"
    return (f"workspace could not be brought current (ff-only pull refused: "
            f"{first}) — dispatching anyway on the EXISTING tree; it may be "
            f"stale. Clean or reconcile {card.workspace} to restore keep-current.")


def _agent_trees(a, ag, sweep: bool = False) -> list:
    """The trees this agent edits. Its workspace clone always; its worktree off
    every shared repo st has provisioned from only when `sweep` is set.

    THE SWEEP IS OPT-IN, and the reason is cost on the hottest command there is.
    `st crew` is what a coordinator runs constantly. Discovering repos and
    measuring a worktree per agent per repo is ~3 git subprocesses each — on
    this fleet, fifteen agents across twelve containers, which is well over a
    hundred processes for one status read. A status command that got noticeably
    slower would be run less, and a staleness signal nobody looks at is the
    condition this bead is trying to end, reached by a different road.

    So the DEFAULT column costs one tree per agent (its own workspace clone) and
    `st crew --trees` buys the full picture. That is also what makes the default
    hermetic: discovery reads `GT_ROOT`/`~/gt` off the real filesystem, so an
    unconditional sweep made unit tests depend on which worktrees happened to
    exist on the developer's machine — measured, it silently changed three
    existing tests' output.

    When it does sweep, worktrees are DISCOVERED directly from the containers st
    created itself. Never map container -> repo -> inferred container: those two
    mappings are not inverses on the live fleet, and that round-trip silently
    dropped twelve trees (aegis-gmsza). A hardcoded repo list is the failure mode
    here, measured: the deployment's own installer defaulted to ONE repo when
    twelve were live.
    """
    out = []
    if ag.workspace:
        p = Path(ag.workspace).expanduser()
        if p.is_dir():
            out.append(p)
    if not sweep:
        return out
    out.extend(agent_worktrees(ag.name))
    return out


def _tree_label(p) -> str:
    """Name a tree by its REPO, not by its directory name.

    A worktree lives at `<repo>-wt/<agent>`, so its basename is the AGENT — which
    means a per-agent sweep rendered by basename says "arnold -5; arnold -3;
    arnold -1/+15" and names the same thing three times while identifying none of
    them. Measured on the live fleet: the detail lines told an operator they were
    stale in four places and not one of which repo, which is most of the value.
    """
    p = Path(p)
    if p.parent.name.endswith("-wt"):
        return p.parent.name[: -len("-wt")]
    return "workspace"


def _tree_staleness_cell(a, ag, sweep: bool = False) -> "tuple[str, str | None]":
    """(column cell, detail line or None) for `st crew`.

    The cell is deliberately TINY — `-3/+1`, `ok`, `?` — because it sits in a row
    an operator scans, and the detail goes on its own line below. `?` is its own
    value and never rounds to `ok`: a tree whose upstream could not be resolved
    is not a healthy tree, and this whole bead exists because invisible
    staleness was read as fine.
    """
    trees = _agent_trees(a, ag, sweep=sweep)
    if not trees:
        return "—", None
    behind = unpushed = 0
    unknown = False
    parts = []
    for t in trees:
        try:
            # The SWEEP fetches+prunes: its whole purpose is an authoritative
            # at-risk number, and an unpruned read can under-report a genuinely
            # orphaned commit as safe. The cheap default column stays fetchless.
            s = tree_staleness(t, fetch=sweep)
        except Exception:
            unknown = True
            continue
        if s.error:
            unknown = True
            parts.append(f"{_tree_label(t)}: {s.render()}")
            continue
        behind += s.behind
        unpushed += s.unpushed
        if not s.current():
            bits = []
            if s.behind:
                bits.append(f"-{s.behind}")
            if s.unpushed:
                bits.append(f"+{s.unpushed}")
            parts.append(f"{_tree_label(t)} {'/'.join(bits)} vs {s.ref}")
    if behind or unpushed:
        cell = f"-{behind}/+{unpushed}"
    elif unknown:
        cell = "?"
    else:
        cell = "ok"
    return cell, ("; ".join(parts) if parts else None)


def _short_head(dest) -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(dest), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=15)
        return (r.stdout or "").strip() or "?"
    except Exception:
        return "?"


def _open_prs_for(dest) -> "tuple[list[str] | None, str | None]":
    """Open PRs on the forge behind this tree's upstream. (prs, error)."""
    import subprocess
    try:
        ref, _ = upstream_ref(dest)
        if not ref:
            return None, "no upstream ref resolved for this tree"
        remote = ref.split("/", 1)[0]
        r = subprocess.run(["git", "-C", str(dest), "remote", "get-url", remote],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None, f"no url for remote {remote!r}"
        return forgejo_mod.open_pulls((r.stdout or "").strip())
    except Exception as e:
        return None, str(e)


def _refresh_worktree(dest, base: str | None = None) -> str | None:
    """Bring a project WORKTREE current — by REBASE onto its upstream, not ff-pull.

    The keep-current sibling _refresh_clone ff-pulls a clone on `main`. A worktree
    is on `wt/<agent>`, so an ff-only pull either no-ops (wrong branch) or fails —
    the caveat flagged on aegis-h2rr. The right move is the crew-worktree pattern:
    rebase wt/<agent> onto the upstream. But ONLY when the tree is clean: rebasing
    over uncommitted work is the force/reset data-loss (aegis-repg) this whole line
    removes. A dirty tree, or a rebase that conflicts, is LEFT AS-IS and reported —
    a stale-but-intact worktree beats a mangled one.
    Never raises; returns a one-line warning or None (current).

    `base` IS NO LONGER `origin/main` (aegis-ib65p). It is RESOLVED from the
    repo's own config by workspace.upstream_ref, because on the very repo this
    was written for, `origin` is a public MIRROR and `main` tracks `forge` — and
    which of the two led INVERTED inside three hours. Rebasing onto a literal
    `origin/main` there would have moved twelve worktrees BACKWARD off the
    authority while reporting success, which is this bead's own failure produced
    by its remedy. Passing `base` explicitly still works and still overrides;
    None means "ask the repo".

    It also now reports UNPUSHED work, not just missing work. Both were live in
    one evening and they are different risks: behind = duplication, unpushed =
    LOSS. A tree that is merely ahead used to read as perfectly fine.
    """
    import subprocess
    try:
        if base is None:
            base, note = upstream_ref(dest)
            if base is None:
                return note                      # cannot tell — say so, act not
        else:
            note = None
        # --prune: see tree_staleness. Without it, refs for branches deleted
        # upstream survive and launder orphaned commits into "on a remote".
        subprocess.run(["git", "-C", str(dest), "fetch", "--all", "--prune",
                        "--quiet"], capture_output=True, text=True, timeout=60)
        stale = tree_staleness(dest)
        extra = f" {note}" if note else ""
        # STRANDED WORK IS REPORTED WHETHER OR NOT THE REBASE SUCCEEDS. It is not
        # an error and must not block, but nobody else can see it, and a reset
        # anywhere near it destroys it silently.
        if stale.unpushed:
            extra += (f" {stale.unpushed} local commit(s) are NOT on {base} — "
                      f"push them; they exist only in this tree.")
        dirty = subprocess.run(["git", "-C", str(dest), "status", "--porcelain"],
                               capture_output=True, text=True)
        if dirty.returncode != 0 or dirty.stdout.strip():
            return ("worktree has local changes — not rebased onto "
                    f"{base}; working on it as-is (may be behind).{extra}")
        r = subprocess.run(["git", "-C", str(dest), "rebase", base],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            # Leave the worktree usable: abort the half-applied rebase.
            subprocess.run(["git", "-C", str(dest), "rebase", "--abort"],
                           capture_output=True, text=True)
            first = (r.stderr or r.stdout).splitlines()
            return (f"worktree rebase onto {base} refused "
                    f"({first[0] if first else 'conflict'}) — on the existing "
                    f"tree.{extra}")
        return extra.strip() or None
    except Exception as e:                       # not a repo, git absent, timeout
        return str(e)


def _systemctl_user_masked(unit: str) -> bool:
    """Is the unit symlinked to /dev/null? Then nothing can start it, ever.

    Deliberately a SEPARATE question from _systemctl_user_active: a masked
    oneshot with RemainAfterExit=yes reports active forever after its last
    pre-mask run, so is-active alone reads a tombstone as a live competitor.
    """
    import subprocess
    for scope in (["--user"], []):
        try:
            r = subprocess.run(["systemctl", *scope, "is-enabled", unit],
                               capture_output=True, text=True, timeout=10)
            if r.stdout.strip() == "masked":
                return True
        except Exception:
            pass
    return False                                 # cannot tell -> do not claim it


def _systemctl_user_active(unit: str) -> bool:
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return True
        r = subprocess.run(["systemctl", "is-active", "--quiet", unit],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False                             # cannot tell -> do not claim one


# ARMED is the WORD, never the exit code. `systemctl is-enabled` exits 0 for
# "static" and "indirect" too, and exits NON-zero while printing "masked" — so an
# exit-code reading would both invent supervisors that cannot start and, worse,
# have called our own deliberately-masked gastown-crew.service disarmed on the one
# hand and armed on the other depending on which way you squinted. Ask for the
# state, then compare it.
_ARMED_STATES = {"enabled", "enabled-runtime"}


def _systemctl_user_enabled(unit: str) -> bool:
    """Will this unit start on its own at the next boot?

    Deliberately NOT the same question as _systemctl_user_active. A boot-time
    oneshot is inactive for the entire life of a running host and still starts a
    competing fleet the moment it reboots (aegis-np4x1).
    """
    import subprocess
    for scope in (["--user"], []):
        try:
            r = subprocess.run(["systemctl", *scope, "is-enabled", unit],
                               capture_output=True, text=True, timeout=10)
        except Exception:
            return False                         # cannot tell -> do not claim one
        if r.stdout.strip() in _ARMED_STATES:
            return True
    return False


def _window_launch_gate(a) -> int | None:
    """Refuse every relaunch seam while a maintenance lease exists."""
    try:
        lease = window_mod.active(Path(a.root))
    except window_mod.WindowUnreadable as exc:
        print(f"  could not tell: {exc}; relaunch held fail-closed", file=sys.stderr)
        return CANNOT_TELL
    if lease is None:
        return None
    print(f"  refused: maintenance window {lease['id']!r} is {lease['state']} — "
          "relaunch held until `st window release` or `abort`", file=sys.stderr)
    return REFUSED


def _window_systemctl(action: str, unit: str) -> None:
    import subprocess
    r = subprocess.run(["systemctl", "--user", action, unit],
                       capture_output=True, text=True, timeout=30)
    if r.returncode:
        detail = (r.stderr or r.stdout).strip().splitlines()
        raise window_mod.WindowUnreadable(
            f"systemctl --user {action} {unit} failed: "
            f"{detail[0] if detail else f'exit {r.returncode}'}")


def _cmd_window(a) -> int:
    """One journalled maintenance transaction; every consequence is read back."""
    from . import deployed_sha
    from . import input_box

    root = Path(a.root)
    panes = _panes(a)
    action = a.window_action
    try:
        if action == "plan":
            agents = _registry(a).all().exact()
            roster = []
            for card in agents:
                live = bool(card.pane and panes.exists(card.pane))
                verdict, detail = "DOWN", ""
                if live:
                    try:
                        screen = panes.capture(card.pane)
                        awaiting = asks_a_question(_runtime(a, panes), screen)
                        rep = input_box.show(panes, card.pane, awaiting=awaiting)
                        verdict, detail = rep.verdict, rep.detail
                    except Exception as exc:  # unknown is evidence, never empty
                        verdict, detail = "UNKNOWN", f"{type(exc).__name__}: {exc}"
                roster.append({"agent": card.name, "pane": card.pane or "",
                               "live": live, "input": verdict, "input_detail": detail})
            anchors = []
            for row in _tracker_rows(_tracker(a)):
                status = str(row.get("status", "")).lower()
                if status == "in_progress":
                    anchors.append({k: row.get(k) for k in ("id", "title", "assignee")})
            timer = {"unit": sup_mod.TIMER,
                     "active": _systemctl_user_active(sup_mod.TIMER),
                     "enabled": _systemctl_user_enabled(sup_mod.TIMER)}
            manifest = window_mod.plan(
                root, a.id, roster=roster, anchors=anchors,
                deployed_sha=deployed_sha(), target_version=a.target_version,
                timer=timer, actor=_me(a) or "", persist=not a.dry_run)
            print(json.dumps(manifest, indent=2, sort_keys=True))
            if a.dry_run:
                print("  --dry-run: window ID not acquired")
            return OK

        if action == "drain":
            current = window_mod.WindowStore(root).require(a.id)
            if a.dry_run:
                print(f"  would drain {a.id}: pause {current['timer']['unit']} and "
                      f"notify {sum(bool(r.get('live')) for r in current['roster'])} "
                      "recorded live agent(s); lease already prevents relaunch")
                return OK
            def pause():
                if current.get("timer", {}).get("active"):
                    _window_systemctl("stop", sup_mod.TIMER)
                    if _systemctl_user_active(sup_mod.TIMER):
                        raise window_mod.WindowUnreadable("tend timer still active after stop")
            manifest = window_mod.drain(root, a.id, pause_timer=pause)
            inbox = _inbox(a, default="beads")
            for row in manifest["roster"]:
                if row.get("live"):
                    inbox.deliver(
                        row["agent"],
                        f"MAINTENANCE WINDOW {a.id}: checkpoint, clear typed input, "
                        f"report, then `st stop {row['agent']} --reason 'window {a.id}'`. "
                        "Relaunch is leased until release/abort.",
                        frm=_me(a) or "st window")
            print(f"  draining {a.id}: relaunch lease active; "
                  f"{sum(bool(r.get('live')) for r in manifest['roster'])} live agent(s) notified")
            return OK

        if action == "clear":
            def observe(manifest):
                blockers = []
                for row in manifest["roster"]:
                    pane = row.get("pane")
                    if pane and panes.exists(pane):
                        try:
                            screen = panes.capture(pane)
                            rep = input_box.show(
                                panes, pane,
                                awaiting=asks_a_question(_runtime(a, panes), screen))
                            suffix = f" input={rep.verdict}"
                        except Exception as exc:
                            suffix = f" input=UNKNOWN({type(exc).__name__})"
                        blockers.append(f"{row['agent']} pane={pane}{suffix}")
                for unit in (sup_mod.TIMER, sup_mod.SERVICE):
                    if _systemctl_user_active(unit):
                        blockers.append(f"writer unit={unit} active")
                return blockers
            window_mod.clear(root, a.id, observe=observe, persist=not a.dry_run)
            prefix = "would be " if a.dry_run else ""
            print(f"  {prefix}CLEAR {a.id}: no recorded pane or supervisor writer remains live")
            return OK

        if a.dry_run:
            current = window_mod.WindowStore(root).require(a.id)
            if action == "release" and current["state"] != "clear":
                raise window_mod.WindowRefused(
                    "release requires a successful CLEAR; use abort to roll back earlier")
            names = [r["agent"] for r in current["roster"] if r.get("live")]
            print(f"  would {action} {a.id}: restore "
                  f"{', '.join(names) if names else 'no agents'}; tend timer "
                  f"{'active' if current.get('timer', {}).get('active') else 'inactive'}")
            return OK

        def start_agent(name):
            card = _registry(a).get(name)
            rc = _launch(a, card, panes, _runtime(a, panes), dry_run=False,
                         window_restore=True)
            if rc != OK:
                raise window_mod.WindowUnreadable(f"restore launch {name} returned {rc}")
        current = window_mod.restore(
            root, a.id, start_agent=start_agent,
            is_live=lambda name: bool((card := _registry(a).get(name)).pane
                                      and panes.exists(card.pane)),
            timer_active=lambda: _systemctl_user_active(sup_mod.TIMER),
            resume_timer=lambda: _window_systemctl("start", sup_mod.TIMER),
            require_clear=(action == "release"))
        print(f"  {action} {a.id}: restored "
              f"{sum(bool(r.get('live')) for r in current['roster'])} agent(s) "
              "and the recorded tend-timer state by read-back")
        return OK
    except window_mod.WindowRefused as exc:
        print(f"  refused: {exc}", file=sys.stderr)
        return REFUSED
    except (window_mod.WindowUnreadable, OSError, LookupError) as exc:
        print(f"  could not tell: {exc}", file=sys.stderr)
        return CANNOT_TELL


def _run_cmd(argv) -> None:
    import subprocess
    subprocess.run(argv, capture_output=True, text=True, timeout=60)


def _run_rc(argv) -> int:
    """Same, but the RETURN CODE is the answer. `systemd-run` refuses (non-zero)
    for reasons a caller must be able to act on — a unit name already taken, no
    user manager at all — and a runner that swallowed that would report a wake
    as armed when nothing was scheduled."""
    import subprocess
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=60).returncode
    except Exception:      # noqa: BLE001 — no systemd is a refusal, not a crash
        return 1


def _attach_argv(pane: str, socket, read_only: bool, has_shanty: bool):
    """Build (argv, env-overlay) for the attach — the pure, testable core.

    THROUGH SHANTY when it is on PATH (the themed bar + segments Stiwi wants the
    attach to be), falling back to bare tmux only when it is absent — the same
    self-hiding discipline the segments already use. Either way the operator never
    types the socket or the `shanty-`/`aegis-crew-` pane prefix: st resolved both.

    The socket is passed to shanty via SHANTY_TMUX_SOCKET (shanty honours it as of
    the companion change), so shanty views the FLEET's real sessions on their
    existing socket — no agent is migrated onto shanty's own server (aegis-f5z4's
    ruling holds; shanty is the VIEW). tmux takes it as `-L`.
    """
    if has_shanty:
        env = {"SHANTY_TMUX_SOCKET": socket} if socket else {}
        argv = ["shanty", "attach"]
        if read_only:
            argv.append("-r")
        argv.append(pane)
        return argv, env
    argv = ["tmux"]
    if socket:
        argv += ["-L", socket]
    argv += ["attach-session", "-t", pane]
    if read_only:
        argv.append("-r")
    return argv, {}


def _exec_attach(argv, env_overlay) -> int:
    """Hand the terminal to the attach. os.execvpe REPLACES this process, so on
    success it never returns; only a failed exec (the binary vanished between the
    PATH check and here) falls through to a could-not-tell."""
    import os as _os
    try:
        _os.execvpe(argv[0], argv, {**_os.environ, **env_overlay})
    except OSError as e:
        print(f"  could not exec {argv[0]}: {e}", file=sys.stderr)
        return CANNOT_TELL
    return OK  # unreachable on success; keeps the type checker happy


def _dashboard_snapshot(a, reg, panes, runtime, now):
    """One dashboard snapshot for `a.admin`'s tier. Pure-ish: reads the registry,
    the REUSED crew-state verdicts, the plate, and the event ledger — composes
    them in dashboard.gather. Separated from the render loop so a test drives it
    without a clock or a foreground loop."""
    from . import dashboard as dash_mod
    from .tier import _find_administrator
    agents = reg.all().exact()
    admin = a.admin or _find_administrator(reg)
    if not admin:
        return None, "no administrator in the registry to show a tier for"
    if admin not in {x.name for x in agents}:
        return None, f"no such agent: {admin}"
    # The dashboard renders the tier, not the posture column — hand it the shape
    # it has always taken rather than widening its contract for a field it does
    # not draw. One capture still, because it is the same generator.
    crew_states = [(ag, state, work)
                   for ag, state, work, _ in _crew_states(agents, panes, runtime)]
    plate = _plate(a)
    last = FilesEvents(Path(a.root) / "events").latest_by_sender()
    return dash_mod.gather(admin, agents, crew_states, plate, last, now), None


def _cmd_dashboard(a) -> int:
    """dashboard [admin] — a live, read-only view of one admin's tier.

    The always-on sibling of `st crew`: scoped to an administrator and its crew,
    it REUSES the same busy/idle/waiting/saturated verdicts (never a second
    opinion) and refreshes on an interval so an operator keeps it in a pane while
    talking to that admin. `--once` renders a single snapshot (for scripting);
    the default loops until interrupted.
    """
    from . import dashboard as dash_mod

    reg = _registry(a)
    panes = _panes(a)
    runtime = _runtime(a, panes)

    def one() -> tuple[int, "Dashboard | None"]:
        try:
            data, err = _dashboard_snapshot(a, reg, panes, runtime, time.time())
        except Exception as e:
            print(f"  could not tell: {e}", file=sys.stderr)
            return CANNOT_TELL, None
        if data is None:
            print(f"  refused: {err}", file=sys.stderr)
            return REFUSED, None
        return OK, data

    if a.once:
        rc, data = one()
        if data is not None:
            print(dash_mod.render(data, time.time()))
        return rc

    # The self-refreshing panel. Clear + redraw each interval; Ctrl-C exits clean.
    print("  st dashboard — refreshing every "
          f"{a.interval}s. Ctrl-C to stop.", file=sys.stderr)
    try:
        while True:
            rc, data = one()
            if data is None:
                return rc                     # a refusal/could-not-tell is terminal
            # \x1b[2J\x1b[H: clear screen + home, so the pane shows ONE live frame
            # rather than an ever-growing scrollback of snapshots.
            print("\x1b[2J\x1b[H", end="")
            print(dash_mod.render(data, time.time()))
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return OK


def _cmd_input(a) -> int:
    """input <agent> [--show|--clear|--dismiss] — the input box as a surface.

    THE COORDINATOR'S QUESTION, answered by the tier instead of by eye.
    `capture-pane -p` strips the one attribute that separates Claude Code's
    ghost-text suggestion from text somebody typed and never submitted, so a
    human reading a pane has been guessing — and on 2026-08-01 guessed wrong and
    ran the stranded-input SOP on a suggestion (aegis-c6hli).

    EVERY VERDICT PRINTS ITS EVIDENCE. The SOP was run on a confident reading of
    a capture that had thrown the deciding bit away; nothing in that output could
    have contradicted it. The raw prompt line is shown so a second pair of eyes
    can check the verdict rather than inherit it.

    NOTHING HERE SUBMITS. Keys go through Panes.control, whose allowlist has no
    Enter and no Tab — Tab as deliberately as Enter, because Tab ACCEPTS the
    suggestion and would inject it into the agent's turn.
    """
    from . import input_box
    reg = _registry(a)
    panes = _panes(a)
    try:
        card = reg.get(a.agent)
    except Exception as e:
        print(f"  no such agent {a.agent!r}: {e}", file=sys.stderr)
        return REFUSED
    if not card.pane or not panes.exists(card.pane):
        print(f"  {a.agent} has no live pane — nothing to read.", file=sys.stderr)
        return REFUSED

    # The picker check is the RUNTIME's answer, passed in exactly as work_state
    # takes `awaiting` — this module knows no runtime's chrome. Without it an
    # agent blocked on a permission prompt reads TYPED, because the picker marks
    # its selected option with the same glyph the input box uses.
    from .runtime import asks_a_question
    runtime = _runtime(a, panes)
    plain = triage_mod.strip_attrs(panes.capture(card.pane, attrs=True))
    awaiting = bool(asks_a_question(runtime, plain))

    if a.clear:
        rep = input_box.clear(panes, card.pane, awaiting=awaiting)
    elif a.dismiss:
        rep = input_box.dismiss(panes, card.pane)
    else:
        rep = input_box.show(panes, card.pane, awaiting=awaiting)

    print(f"  {a.agent}: {rep.verdict}")
    if rep.text:
        print(f"    text     : {rep.text}")
    if rep.evidence:
        # repr() so the escapes are VISIBLE — the bytes are the evidence, and a
        # terminal rendering them would hide the very attribute in question.
        print(f"    evidence : {rep.evidence!r}")
    if rep.detail:
        print(f"    note     : {rep.detail}")
    if rep.verdict == input_box.GHOST and not (a.clear or a.dismiss):
        print("    (a suggestion, not stranded input — the buffer is empty. "
              "Nothing is stalled; do NOT run the stranded-input SOP on this.)")

    if not rep.changed:
        return CANNOT_TELL
    if rep.verdict == input_box.UNKNOWN:
        return CANNOT_TELL
    # GHOST is also an empty input buffer: it is only the runtime's dimmed
    # suggestion painted over that buffer.  Codex does this immediately after
    # C-u, so requiring a visually EMPTY frame turns a successful clear into a
    # refusal (aegis-7v54g).
    if a.clear and rep.verdict not in (input_box.EMPTY, input_box.GHOST):
        return REFUSED
    return OK


def _resolve_pane(a):
    """(panes, card, screen, awaiting) for a live agent pane, or None on refusal.

    Shared by ask/answer/input so the three cannot drift on the one thing they
    must agree about: whether a PICKER is up. `awaiting` is the RUNTIME's verdict,
    passed in exactly as work_state and input_box take it.
    """
    from .runtime import asks_a_question
    reg, panes = _registry(a), _panes(a)
    try:
        card = reg.get(a.agent)
    except Exception as e:
        print(f"  no such agent {a.agent!r}: {e}", file=sys.stderr)
        return None
    if not card.pane or not panes.exists(card.pane):
        print(f"  {a.agent} has no live pane — nothing to read.", file=sys.stderr)
        return None
    screen = panes.capture(card.pane, attrs=True)
    awaiting = bool(asks_a_question(_runtime(a, panes),
                                    triage_mod.strip_attrs(screen)))
    return panes, card, screen, awaiting


def _print_question(agent: str, q) -> None:
    """The block verbatim, then the options NUMBERED — the whole point (w30p2).

    Verbatim because paraphrasing an approval prompt is how an operator approves
    something other than what they read. The options carry their own numbers from
    the screen rather than being re-numbered here, so `st answer <agent> N` and
    what the agent will act on are the same N by construction.
    """
    print(f"  {agent} is blocked on a question:\n")
    for ln in q.context:
        print(f"    │{ln}")
    print()
    for o in q.options:
        mark = "❯" if o.selected else " "
        print(f"    {mark} {o.n}. {o.text}")
        if o.detail:
            print(f"         {o.detail}")
    if q.footer:
        print(f"\n    ({q.footer})")
    print(f"\n  Answer it: st answer {agent} <N>")


def _cmd_ask(a) -> int:
    """ask <agent> — print the question an agent is blocked on. READ-ONLY.

    THE COMMAND THAT REPLACES `capture-pane -p -t <pane> | tail -12`. The
    coordinator ran that six times in one evening across five agents, hand-typing
    a socket name and a pane name every time — and three of those panes were
    `aegis-crew-*` while most of the fleet is `shanty-*`, so the naming era had to
    be recalled per agent, at the moment of deciding whether to approve someone
    else's shell command. The card already knows the pane (aegis-w30p2).

    Reading the options MACHINE-WISE is the safety half. Option 2 is not a
    stable thing: measured across three live pickers on one night it was "Yes,
    and don't ask again for: curl …", "No, exit", and "Spaces". A coordinator
    who has learned what 2 means has learned something false.
    """
    got = _resolve_pane(a)
    if got is None:
        return REFUSED
    panes, card, screen, awaiting = got
    if not awaiting:
        print(f"  {card.name}: no blocking picker is up — nothing is being "
              f"asked. (`st input {card.name} --show` for what is in its box.)")
        return OK

    from .runtime import reads_a_question
    q = reads_a_question(_runtime(a, panes), screen)
    if q is None:
        # NOT "no question" — the runtime says one IS up. Saying otherwise would
        # tell a coordinator a blocked agent is fine, which is the whole class of
        # bug this area keeps closing.
        print(f"  {card.name}: a picker IS up, but its options could not be "
              f"read off this frame — so this is a could-not-tell, not an "
              f"all-clear. `st attach {card.name}` and read it.", file=sys.stderr)
        return CANNOT_TELL
    _print_question(card.name, q)
    return OK


def _cmd_answer(a) -> int:
    """answer <agent> N — select option N on an agent's blocking picker.

    THE ONE COMMAND HERE THAT ACTS ON ANOTHER AGENT'S SESSION, so it echoes what
    it selected and refuses in every direction it can: not a picker, unreadable
    picker, N out of range, N past what a keystroke can address. It also records
    who answered what, before it acts — an approval granted into someone else's
    pane must not rely on anybody remembering they granted it (internal-ref spent
    a cross-session argument on exactly that question).

    THERE IS NO --yes AND THERE WILL NOT BE. A permission prompt is a DECISION;
    the value of it being a decision is that a person made it. One keystroke is
    the goal, zero is a different and worse thing (aegis-apz9).
    """
    from . import picker
    got = _resolve_pane(a)
    if got is None:
        return REFUSED
    panes, card, _, awaiting = got
    res = picker.answer(panes, card.pane, a.n, awaiting=awaiting, agent=card.name)
    if not res.ok:
        print(f"  {card.name}: {res.detail}", file=sys.stderr)
        return REFUSED
    # ECHO WHAT WAS SELECTED, not the number that was typed. The number is what
    # the operator already knew; the TEXT is the thing they may have got wrong.
    print(f"  {card.name}: selected {res.option.n}. {res.option.text}")
    if not res.changed:
        print(f"    note     : {res.detail}", file=sys.stderr)
        return CANNOT_TELL
    return OK


def _cmd_attach(a, *, execer=_exec_attach, which=None) -> int:
    """attach [agent] [-r] — attach to a crew member, STARTING them if down.

    st already knows the socket (declared_socket) and the pane (the registry), so
    the operator never types `tmux -L gt-ae5f35 attach -t shanty-weaver`. Refuses
    cleanly on an unknown agent — never a raw tmux error — the same discipline as
    go/stop.

    IT LAUNCHES A DOWN AGENT (owner-directed). "weaver is down, run `st crew`" is a
    true sentence that answers a question nobody asked: the operator typing
    `st attach weaver` has already decided they want to be in weaver's pane, and
    making them run a second command to get there is the whole cold-start friction.
    The launch goes through the same `_launch` seam as `st new`, so an agent
    attached-into-existence is provisioned, workspace-checked and hook-verified
    exactly like one `st new` made.

    `--no-start` is for the caller that must promise it creates nothing — a script
    attaching to whatever is already running. That need is real, so it stays
    reachable; it is the FLAG rather than the default because the frequent, human
    case should be the default and the careful, scripted case should be the one
    that says so.
    """
    import shutil
    which = which or shutil.which
    reg = _registry(a)
    panes = _panes(a)
    socket = declared_socket(getattr(a, "root", None) or ".")

    name = a.agent
    if not name:
        # DEFAULT TO THE ADMINISTRATOR (aegis-83w2), not $SHANTY_AGENT. The
        # tier-root coordinator is who the operator most often wants to look at,
        # and st knows it from the registry — so `st attach` with no arg opens the
        # coordinator, and an explicit <agent> still targets that agent. Falling
        # back to $SHANTY_AGENT would open the OPERATOR's own pane, which they are
        # already in; the useful default is the one they'd otherwise type.
        from .tier import _find_administrator
        try:
            name = _find_administrator(reg)
        except Exception as e:
            print(f"  could not tell: {e}", file=sys.stderr)
            return CANNOT_TELL
        if not name:
            # No administrator in the registry — LIST, don't error.
            try:
                agents = reg.all().exact()
            except Exception as e:
                print(f"  could not tell: {e}", file=sys.stderr)
                return CANNOT_TELL
            print("  no administrator to default to — attach to which? name one:")
            for ag in sorted(agents, key=lambda x: x.name):
                print(f"    {ag.name}")
            return OK

    try:
        card = reg.get(name)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if not card.pane:
        # NO PANE ON THE CARD IS STILL A REFUSAL, even though a down agent is not.
        # `_launch` would invent an `st-<name>` session, but a session that is not
        # on the card is invisible to `st crew`, `st stop` and `st tend` — so
        # attaching into one would create an agent only this command can find.
        # The fix is the card, and it is one command.
        print(f"  refused: {name} has NO pane on its card, so there is no session "
              f"to attach to or start. Add a `pane` to "
              f"{Path(a.root) / 'crew' / f'{name}.json'} (e.g. "
              f"\"shanty-{name}\") — the card projection does not assign one.",
              file=sys.stderr)
        return REFUSED

    if not panes.exists(card.pane):
        where = f"socket {socket!r}" if socket else "the default tmux server"
        if a.no_start:
            print(f"  refused: {name} is down — no live pane {card.pane} on "
                  f"{where}, and --no-start says do not launch it. `st crew` to "
                  f"see who is up.", file=sys.stderr)
            return REFUSED
        print(f"  {name} is down (no {card.pane} on {where}) — starting it, then "
              f"attaching.")
        runtime = _runtime(a, panes)
        rc = _launch(a, card, panes, runtime, dry_run=False)
        if rc == REFUSED:
            # The launcher already said WHY. Do not attach into a session that
            # either does not exist or came up broken enough to refuse.
            print(f"  refused: could not start {name} — not attaching.",
                  file=sys.stderr)
            return REFUSED
        if rc == CANNOT_TELL:
            # LAUNCHED BUT UNVERIFIED -> ATTACH ANYWAY, LOUDLY. The session
            # exists; what could not be established is that the runtime came up
            # (or that its hooks are wired). The single most useful next action is
            # to put the human's eyes on that pane — which is what they asked for.
            # Exiting instead would hide the evidence behind a second command.
            print(f"  ⚠ {name} was launched but NOT verified live (see above) — "
                  f"attaching so you can see the pane itself.", file=sys.stderr)
        elif not panes.exists(card.pane):
            print(f"  could not tell: started {name} but {card.pane} still does "
                  f"not exist — nothing to attach to.", file=sys.stderr)
            return CANNOT_TELL

    argv, env = _attach_argv(card.pane, socket, a.read_only,
                             which("shanty") is not None)
    return execer(argv, env)


def _dream_sweep(a, cfg, agents, panes, *, force=False, dry_run=False):
    """Create at most one due dream bead; return (plan, item-id, reason).

    This is a best-effort tend layer.  Every capacity input is a measured,
    persist=False provider verdict; signal loss removes a provider from
    consideration rather than becoming fictional headroom.
    """
    from . import feed_check

    policy = cfg.dream
    state = dream_mod.State(a.root)
    tracker = _tracker(a)
    ready, active = feed_check.queue_state(a.root, _registry(a), tracker)
    free = feed_check.free_feedable_workers(_registry(a), panes, _runtime(a, panes),
                                            root=a.root)
    free = [name for name in free if name not in feed_check.hauls(ready, active)]
    cards = {card.name: card for card in agents}
    candidates = []
    cfg_now, governors = _governors(a)
    verdicts = {name: governor.evaluate(persist=False)
                for name, governor in governors.items()}
    for name, card in cards.items():
        # A periodic DREAM may queue behind foreground work, but only on a live
        # worker subscription. Leads/admins are coordination capacity, and a
        # missing pane cannot consume the queued artifact.
        if card.role != "worker" or not card.pane or not panes.exists(card.pane):
            continue
        harness, governor, unconfigured = _governor_for(
            cfg_now, governors, card, a.root)
        verdict = (unconfigured if unconfigured is not None else
                   verdicts.get(harness, verdicts.get("base")))
        if verdict is None or verdict.signal_lost:
            continue
        values = [pct for pct in verdict.by_window.values() if pct is not None]
        pct = max(values) if values else verdict.pct
        if pct is None or governor is None:
            continue
        headroom = 100.0 - float(pct)
        # The foreground delegation reserve is protected even when dream's own
        # threshold is looser.  Spare means above BOTH lines, never either.
        if headroom < governor.policy.delegation_reserve_pct:
            continue
        candidates.append({"agent": name, "harness": harness,
                           "headroom": headroom,
                           "idle": name in free})
    cycle, reason = dream_mod.plan(policy, state.read(), ready, candidates,
                                   force=force)
    if cycle is None:
        return None, "", reason
    if dry_run:
        return cycle, "", "dry-run"
    item = tracker.create(cycle.title, assignee=cycle.agent, priority=4,
                          labels=cycle.labels,
                          description=cycle.description)
    # Observed create first, state second. A tracker exception leaves the prior
    # due time intact, so the next tend pass retries rather than losing a cycle.
    state.record(cycle, item.id)
    card = cards.get(cycle.agent)
    if (cycle.agent in free and card is not None and card.pane
            and panes.exists(card.pane)):
        panes.send(card.pane, attribute(
            f"Work is on your hook: {item.id} — {cycle.title} — scheduled DREAM "
            f"cycle; read the bead and execute one bounded pass.", "st dream"))
    return cycle, item.id, "created"


def _cmd_dream(a) -> int:
    cfg = config.load(Path(a.root))
    state = dream_mod.State(a.root).read()
    if not a.run:
        due = state.get("last_at", 0) + cfg.dream.interval_minutes * 60
        print(f"  dream {'on' if cfg.dream.enabled else 'off'} · interval "
              f"{cfg.dream.interval_minutes}m · minimum headroom "
              f"{cfg.dream.min_headroom_pct}%")
        print(f"  last {state.get('last_item', 'never')} · next due "
              f"{time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(due)) if state else 'now'}")
        print(f"  rotation: {', '.join(cfg.dream.domains)}")
        return OK
    panes = _panes(a)
    agents = _registry(a).all().exact()
    cycle, item_id, reason = _dream_sweep(a, cfg, agents, panes, force=True,
                                          dry_run=a.dry_run)
    if cycle is None:
        print(f"  dream stayed asleep: {reason}")
        return OK
    verb = "would create" if a.dry_run else "created"
    suffix = "" if a.dry_run else f" ({item_id})"
    print(f"  {verb}: {cycle.title}{suffix} -> {cycle.agent} on {cycle.harness} "
          f"({cycle.headroom:.0f}% headroom)")
    return OK


def _cmd_tend(a) -> int:
    """tend — one supervision pass, or manage the timer that runs them.

    Exit codes carry the finding, not just the run: 0 = looked, nothing wrong
    (respawning something is not "wrong" — it is the job); 1 = REFUSED (an
    install collision, an unknown agent); 2 = the pass found a FAULT it could not
    fix (a retired agent alive, an agent that cannot report, a launch it refused).
    A supervisor that always exits 0 is a supervisor nobody can alert on.
    """
    if a.retire or a.unretire:
        return _tend_retire(a)
    if a.install:
        # NOT the bare name — see supervisor.resolve_st_bin. install() refuses
        # a relative path, so an unresolvable st is a loud failure here rather
        # than a silent 203/EXEC on every timer fire (aegis-408qs).
        st_bin = sup_mod.resolve_st_bin() or "st"
        changed, msg = sup_mod.install(st_bin, Path(a.root), interval=a.interval,
                                       run=None if a.dry_run else _run_cmd,
                                       is_active=_systemctl_user_active,
                                       is_masked=_systemctl_user_masked,
                                       is_enabled=_systemctl_user_enabled,
                                       dry_run=a.dry_run)
        print(f"  {msg}")
        return OK if changed or "already installed" in msg or a.dry_run else REFUSED
    if a.uninstall:
        changed, msg = sup_mod.uninstall(run=None if a.dry_run else _run_cmd)
        print(f"  {msg}")
        return OK if changed or "not installed" in msg else REFUSED
    if a.status:
        return _tend_status(a)

    # THE SOCKET GUARD, and it prevents a fleet-destroying interaction between two
    # of this repo's own features. `tend --install` runs from a systemd --user
    # timer, which has NO $TMUX at all — so on a host whose fleet lives on a named
    # socket, an undeclared socket makes every agent look DOWN to the supervisor,
    # and a supervisor that sees the whole fleet dead RESPAWNS THE WHOLE FLEET,
    # onto a different server, duplicating every agent. Measured before the
    # declaration existed: `crew --count` from a caller with no $TMUX said 0/0
    # while eighteen agents were up. A wrong socket is the one condition under
    # which this command must do NOTHING.
    from . import doctor as _doc
    sock_v, sock_why = _socket_check(a)
    if sock_v == _doc.SOCKET_WRONG:
        print(f"  refused: {sock_why}", file=sys.stderr)
        print("  Supervision does NOTHING on a wrong socket: every agent would "
              "look dead and be respawned onto the wrong server.", file=sys.stderr)
        return REFUSED

    # --reauth AFTER the socket guard, deliberately: on a wrong socket every
    # auth-dead agent would be invisible (or worse, a foreign fleet's panes would
    # be judged), and this branch KILLS sessions.
    if getattr(a, "reauth", False):
        return _tend_reauth(a)

    # --loop <secs>: run passes on an interval, so blocked-worker delivery is
    # PROMPT ON ITS OWN (aegis-w0kk) rather than "on the coordinator's next stop".
    # This is the heartbeat the bead's option 2 names; without a running st tend
    # timer (its systemd install refuses while gastown-crew-watchdog holds the
    # crew), a foreground/backgrounded `st tend --loop 30` is the runnable one.
    loop = getattr(a, "loop", None)
    if not loop:
        return _tend_once(a)
    import time
    print(f"  tend heartbeat: a pass every {loop}s. Blocked workers are pushed to "
          f"their coordinator within one interval. Ctrl-C to stop.",
          file=sys.stderr)
    # THE LOOP'S OWN STALENESS (aegis-arma follow-up, measured). A long-running
    # loop is a MEMORY image of the code at start: the live one ran for two days
    # while the editable install moved under it — every fix (auth-dead gating,
    # the idle-fleet push's bd cwd) landed on disk and reached nothing, the
    # aegis-ttlr class one level up (disk current, PROCESS stale). So the loop
    # watches its own package fingerprint and RE-EXECS itself when the code
    # changes — same argv, fresh import, loud line in between. A supervisor
    # that must be manually restarted to pick up its own fixes is a supervisor
    # that runs old code exactly when it matters.
    fp = _code_fingerprint()
    while True:
        # A CRASHED PASS IS NOT A DEAD SUPERVISOR (aegis-ey7n). The live loop
        # died to one uncaught OSError (ENOSPC in a ledger write) and nothing
        # restarted it — a supervisor with no supervisor. One bad pass logs its
        # traceback and the next interval tries again; a persistent fault
        # repeats loudly every interval, which is exactly what an operator can
        # see and a dead process is exactly what they cannot. KeyboardInterrupt
        # and SystemExit still pass through — Ctrl-C must keep killing it.
        try:
            _tend_once(a, quiet=True)
        except Exception:  # noqa: BLE001 — survive anything a pass can raise
            import traceback
            print(f"  ⚠ st tend: this pass CRASHED — supervision continues; "
                  f"next pass in {loop}s. Traceback:", file=sys.stderr)
            traceback.print_exc()
        now = _code_fingerprint()
        if fp is not None and now is not None and now != fp:
            print("  st tend: the installed code CHANGED under this loop — "
                  "re-exec to run what is on disk.", file=sys.stderr)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        time.sleep(loop)


def _code_fingerprint(pkg=None) -> str | None:
    """A cheap identity for the package code THIS process would import: name,
    mtime and size of every module file. Editable install: these are the
    checkout's files, so a `git pull` changes it. Non-editable: they are the
    venv copy's, so a reinstall changes it. None = could not look — and the
    caller treats that as 'never re-exec', because a supervisor that exec-loops
    on a stat error is worse than one that runs old code."""
    try:
        pkg = Path(pkg) if pkg else Path(__file__).resolve().parent
        parts = [f"{f.name}:{f.stat().st_mtime_ns}:{f.stat().st_size}"
                 for f in sorted(pkg.glob("*.py"))]
        return "|".join(parts) or None
    except OSError:
        return None


def _tend_once(a, quiet: bool = False) -> int:
    if (rc := _window_launch_gate(a)) is not None:
        return rc
    # Best-effort sweeps shed by the pass budget (aegis-qwadc). Declared HERE
    # rather than beside the deadline so a pass that returns before the sweeps
    # run — a dry run, an early refusal — still has something for the summary
    # to read. It was scoped to the sweep block first and two tests caught it.
    deferred: list[str] = []
    panes = _panes(a)
    try:
        reg = _registry(a)
        agents = reg.all().exact()
    except Exception as e:
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    runtime = _runtime(a, panes)

    # THE USAGE GOVERNOR (aegis-hdqej). The tend pass is the evaluation point —
    # it is the pass that already decides who lives — so this is the one caller
    # that PERSISTS the engaged tier (hysteresis has to survive a process that
    # exists for five seconds every five minutes). A dry run evaluates and prints
    # but writes nothing, like everything else on a dry run.
    cfg, governors = _governors(a)
    verdicts = {name: gov.evaluate(persist=not a.dry_run)
                for name, gov in governors.items()}
    setpoint_advisories = {}
    utilization_advisories = {}
    # THE PROMETHEUS EXPORT (aegis-ycqgyx, Stiwi directive 2026-09-01). Collected
    # in the loop below, where the decision and every input that produced it are
    # in hand, and pushed once at the end of it. Before this, tonight's decisions
    # — a cap hold, a -1 setpoint, a 1.02x pace hold, an unrated window — existed
    # ONLY as stderr in a journal: invisible to Grafana, to alerting, and to any
    # question about last week.
    gov_metric_lanes = []
    util_clock = time.time()
    live_by_gov = _live_by_governor(agents, panes, cfg, governors, a.root)
    from . import codex_daemon
    blocked_by_gov = {name: 0 for name in governors}
    for card in agents:
        found = codex_daemon.inspect(card.name)
        if not found.blocked:
            continue
        harness, _governor, unconfigured = _governor_for(
            cfg, governors, card, a.root)
        if unconfigured is None:
            name = harness if harness in governors else "base"
            blocked_by_gov[name] = blocked_by_gov.get(name, 0) + 1
    for name, gov in sorted(governors.items()):
        try:
            readings = gov.reader.read_all()
        except Exception:
            readings = {}
        running = live_by_gov.get(name, 0)
        line = creel_advisory_mod.controller_line(
            readings, running=running, cap=verdicts[name].max_agents,
            probe=cfg.env.get(creel_advisory_mod.PROBE_ENV))
        setpoint_advisories[name] = line
        # Unavailability is pushed once through the deduped alerter below.  A
        # permanent warning on every tend heartbeat trains the admin to ignore
        # this channel and therefore un-builds the advisory when it returns.
        if not line.startswith("advisory unavailable:"):
            print(f"  governor setpoint [{name}]: {line}", file=sys.stderr)
        # UTILIZATION, on the same pass and the same evidence (aegis-967a9). The
        # setpoint advisory answers whether the BUDGET wants a different fleet
        # size; this answers whether the fleet we are already allowed is being
        # used. A blind governor is skipped entirely: recommending growth while
        # the usage signal is lost is the one direction the fail-safe forbids.
        seen = None
        if not verdicts[name].signal_lost:
            seen = _utilization(name, readings=readings, policy=gov.policy,
                                verdict=verdicts[name], live=running,
                                now=util_clock, advisory=line, root=a.root,
                                reg=reg, blocked=blocked_by_gov.get(name, 0))
            # PUSH ON CHANGE ONLY, unlike the setpoint line beside it, and the
            # difference is deliberate (sattler, measured 2026-08-29). gennaro's
            # 1641346 keeps a nonzero SETPOINT delta actionable every pass so an
            # unactuated budget recommendation cannot go silent — right for a
            # rare trajectory event. Occupancy is not an event: "under cap with
            # work ready" stays true for hours, so the same rule re-paged +3
            # twice in twenty minutes while the standing answer was known and
            # deliberate. A channel that repeats itself is one the admin learns
            # to ignore (aegis-3w0br), which un-builds the advisory.
            # A NEWLY nonzero recommendation still pushes: the key changes.
            utilization_advisories[name] = creel_advisory_mod.Advice(
                line=seen.render(), key=seen.key(), actionable=False)
            print(f"  governor utilization [{name}]: {seen.render()}",
                  file=sys.stderr)
        # A BLIND LANE IS STILL EXPORTED, with `seen` None and
        # st_governor_signal_lost=1. Skipping it would make a governor that
        # cannot see the number look exactly like a governor that was never
        # configured — the failure this whole module exists to make impossible,
        # and the one the fail-safe cares most about.
        gov_metric_lanes.append({
            "lane": name, "verdict": verdicts[name], "utilization": seen,
            "readings": readings, "live": running,
            "blocked": blocked_by_gov.get(name, 0),
            "setpoint_delta": creel_advisory_mod.recommended_delta(line)})
    # PUBLISH, before the sweeps and therefore before the pass budget can shed
    # anything (aegis-qwadc). Deliberate: the sweeps are best-effort notification,
    # while this is the RECORD of the decision this pass just made, and a record
    # that is dropped under load is missing exactly when it is most interesting.
    # It costs one bounded HTTP PUT to a LAN gateway, it cannot raise, and it is
    # a no-op with ST_GOVERNOR_PUSHGATEWAY unset.
    #
    # A DRY RUN PUBLISHES NOTHING. It evaluates and prints, like everything else
    # on a dry run — and its counters must not move, or `st tend --dry-run` would
    # silently inflate st_governor_decisions_total for a decision nobody applied.
    if not a.dry_run and gov_metric_lanes:
        gov_metrics_mod.publish(
            Path(a.root), gov_metric_lanes,
            agents=_agent_counts(a, agents, panes, runtime),
            # The deployment's [env] table, NOT os.environ — st does not export
            # it into its own process, so reading only the ambient environment
            # would leave a correctly configured deployment silently unexported.
            # Same reason the creel probe above is read off cfg.env.
            env=cfg.env,
            log=lambda msg: print(f"  ⚠ {msg}", file=sys.stderr))
    # Preserve the byte-for-byte single-governor path.  A mixed fleet has no
    # meaningful global verdict: every decision below resolves from the card.
    verdict = verdicts.get("base") if not cfg.governor.by_harness else None
    card_verdicts = {}

    def _card_verdict(card):
        harness, governor, unconfigured = _governor_for(cfg, governors, card, a.root)
        if harness not in card_verdicts:
            card_verdicts[harness] = (unconfigured if unconfigured is not None
                                      else verdicts.get(harness, verdicts.get("base")))
        return card_verdicts[harness]

    for _v in list(verdicts.values()) + [
            _card_verdict(card) for card in agents
            if gov_mod.unconfigured(cfg.governor,
                                    harness_mod.name_for(card, root=a.root))]:
        if _v is not None and _v.alarm:
            # EVERY PASS, LOUDLY. A governor that goes quiet when it cannot see
            # the number is indistinguishable from one with nothing to report.
            print(f"  ⚠ {_v.alarm}", file=sys.stderr)
    # THE ON RAMP FIRED (aegis-9mehy). A window left a tier, so agents held down
    # by it become eligible in the very same pass — `_withheld` is re-evaluated
    # from this verdict, and retirement is checked before it, so no retiree is
    # resurrected by a relax. Printed because it is the single most consequential
    # thing a pass can do and it used to happen in total silence: the throttle
    # had a loud OFF ramp and a mute ON ramp.
    if verdict is not None:
        for _relaxed in verdict.relaxed:
            print(f"  {_relaxed.render(os.environ.get(sup_mod.WAKE_ENV, ''), time.time())}",
                  file=sys.stderr)
        # BURNDOWN (aegis-yegfx). Printed on EVERY pass it is armed, not just the
        # pass it arms — this is the one mechanism that makes the fleet spend
        # more, and a relaxation nobody can see is indistinguishable from a
        # governor that stopped working. It is deliberately NOT on `alarm`:
        # nothing is wrong, and routing it there would train an operator to
        # ignore the field that means something IS.
        for _burning in verdict.burning:
            print(f"  {_burning.render()}", file=sys.stderr)
        # Same surface, same reasoning, same deliberate omission from `alarm`
        # (aegis-7kwtu): a fleet running because its burn is ON PACE is a fleet
        # working as designed, and the operator needs to be able to SEE that
        # without being told something is wrong.
        for _pacing in verdict.pacing:
            print(f"  {_pacing.render()}", file=sys.stderr)

    def _respawn(card, session):
        runtime.start(card, session)
        # It is UP again, so the deliberate-stop record is history (#29). tend
        # respawning an `st stop`ped agent is correct and unchanged — a stop is not
        # a retirement — but the record must not outlive the stop it describes, or
        # this agent's NEXT crash reads as somebody's decision.
        _stops(a).forget(card.name)

    tender = tend_mod.Tender(
        panes, runtime, _launches(a),
        spawn=None if a.dry_run else _respawn,
        refresh=None if a.dry_run else _refresh_clone,
        # Decision 7: worktrees too, at the same safe moment. Dry run
        # touches nothing, exactly like the clone refresh above.
        refresh_trees=(None if a.dry_run
                       else lambda card: _refresh_agent_worktrees(a, card)),
        gaps=lambda card: prov_mod.missing_kit(card, Path(a.root)),
        # Backoff + give-up (GitHub #12): a crash-looping agent must cost one
        # launch per interval, not one per pass, and must eventually be retired
        # rather than thrashed forever.
        crashes=sup_mod.CrashLog(Path(a.root)),
        retire=lambda name: _retire_card(a, name),
        log=lambda msg: print(f"  {msg}", file=sys.stderr),
        # THE EFFECTIVE TARGET: the STRICTER of what the operator asked for and
        # what the governor caps (aegis-tzpo1). `--target` is a request; the cap
        # is a budget constraint, and a request cannot exceed a constraint. Either
        # may be None (no cap), so this is a min over what is actually declared —
        # and with neither declared it stays None, which is the whole-roster
        # behaviour every existing deployment has today.
        target=_effective_target(getattr(a, "target", None),
                                 None if verdict is None else verdict.max_agents),
        # Name the SOURCE of that number in tend's held message, so an operator
        # is not sent hunting for a `--target` flag they never passed.
        target_src=_target_source(getattr(a, "target", None),
                                  None if verdict is None else verdict.max_agents),
        governed=(None if not governors
                  else lambda card: (_card_verdict(card).excludes(card, _catalog(a))
                                     if _card_verdict(card) is not None else "")),
        # The same record `st crew` reads to print "stopped ON PURPOSE", so the
        # two commands cannot disagree about whose decision put an agent down
        # (aegis-k9068). Without it tend explained every deliberate stop with the
        # foreign-orchestrator wording and counted it as a FAULT.
        stops=_stops(a),
    )
    rep = tender.pass_over(agents, dry_run=a.dry_run)
    # DELIVER blocked workers to their coordinator (aegis-w0kk). Not on a dry run
    # — a dry run pushes nothing, same as it launches nothing. Deduped, so a
    # heartbeat does not re-spam a still-blocked worker every interval.
    if not a.dry_run:
        _log = lambda msg: print(f"  {msg}", file=sys.stderr)

        # EACH SWEEP FAILS ALONE, LOUDLY — THE SUPERVISOR SURVIVES (aegis-ey7n).
        # These sweeps write ledgers, and a write can fail for reasons that have
        # nothing to do with supervision: the live loop DIED at 22:37:37 on
        # ENOSPC inside Notifier._save — an uncaught OSError killed the whole
        # supervisor, nothing restarted it, and the fleet ran unsupervised for
        # half an hour on the exact night a full disk was also tearing event
        # files (the ev-172 dam). A notification layer must never take the
        # respawn layer down with it: the pass itself (respawn, PassLog) is the
        # job; the pushes are best-effort on top.
        # ...AND A SWEEP MUST NOT TAKE THE NEXT PASS DOWN EITHER (aegis-qwadc).
        #
        # The paragraph above is about a sweep that CRASHES. The same argument
        # applies to one that is merely SLOW, and that half was missing. Measured
        # 2026-08-30: two passes took 27 and 36 minutes against a normal 25-40s,
        # both in `br create`/`br update` on a contended store, one `br create`
        # spanning 18+s. Both completed successfully.
        #
        # A long pass is not a slow pass, it is NO SUPERVISION. `st-tend.timer` is
        # `OnUnitActiveSec` over a `Type=oneshot` service whose systemd default
        # `TimeoutStartSec` is infinity, so while the pass runs the timer shows no
        # next elapse at all — and since the crew watchdog is masked, tend is the
        # SOLE respawn path. Thirty-six minutes of a best-effort notification sweep
        # is thirty-six minutes in which nothing can respawn a dead agent.
        #
        # SO THE BUDGET SKIPS THE NEXT SWEEP; IT NEVER INTERRUPTS THE ONE RUNNING.
        # That distinction is the whole design. A wall-clock timeout would land
        # SIGTERM in the middle of a `br create`, and a half-applied bead write is
        # worse than a late cycle — the same reasoning as the indeterminate-commit
        # rule. Declining to START the next phase costs nothing and cannot corrupt
        # anything: every sweep here is idempotent and re-runs next pass.
        #
        # RESPAWN IS NOT AFFECTED, and that is not luck. `pass_over` runs well
        # above this helper, so it has already happened before the first sweep is
        # considered — the budget can only ever shed the best-effort layer the
        # comment above calls "on top".
        sweep_deadline = time.monotonic() + _TEND_SWEEP_BUDGET_S

        def _sweep(label, fn):
            if time.monotonic() > sweep_deadline:
                # Collected, not printed per sweep: a pass that blows the budget
                # skips most of what follows, and one line each would bury the
                # summary in its own noise.
                deferred.append(label)
                return []
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 — any sweep error is survivable
                print(f"  ⚠ tend: the {label} sweep CRASHED ({e!r}) — "
                      f"supervision continues without it this pass", file=sys.stderr)
                return []

        woke = _sweep("blocked-worker", lambda: notify_mod.Notifier(
            Path(a.root), _registry(a), panes, log=_log).sweep(agents, runtime))
        if woke:
            print(f"  ⚠ pushed {len(woke)} blocked worker(s) to their "
                  f"coordinator: {', '.join(woke)}", file=sys.stderr)
        # DRIVE THE CYCLE (aegis-bik9): a saturated IDLE agent is prompted to
        # checkpoint-then-/clear on its own pane, so it self-heals instead of
        # sitting idle-and-refused until a human raw-tmuxes it. Deduped once per
        # saturation episode; the instruction checkpoints BEFORE clearing.
        cycled = _sweep("saturation-cycle", lambda: notify_mod.CycleDriver(
            Path(a.root), _registry(a), panes, refresh=_refresh_clone,
            log=_log).sweep(agents, runtime))
        if cycled:
            print(f"  ⚠ prompted {len(cycled)} saturated agent(s) to cycle: "
                  f"{', '.join(cycled)}", file=sys.stderr)
        # HONOUR SELF-REQUESTED CYCLES (aegis-3laza). An agent cannot cycle itself
        # in-process — the stop kills the session running the stop — so `st cycle
        # --self` can only record a request, and this is what honours it. It is the
        # half that removes three of the five measured failures: the agent that
        # KNOWS it is degrading no longer has to wait for a coordinator to notice.
        #
        # Deliberately AFTER the saturation sweep, so an agent that just asked for
        # a cycle is not also prompted for one in the same pass.
        for who, request in _sweep(
                "cycle-requests",
                lambda: sorted(cycle_mod.Requests(a.root).pending().items())) or []:
            checkpoint = request.get("checkpoint", "")
            checkpoint_bead = request.get("checkpoint_bead", "")
            # GRAPH CONTEXT ON A MECHANICALLY SERVED CYCLE (aegis-5pchx).
            # This namespace is built from `tend`'s, whose parser never declared
            # --quipu-node/--no-graph-context, so `_graph_context` would see
            # nothing and — under SHANTY_GRAPH_CONTEXT=require — REFUSE. That is
            # not a coverage gap, it is Rule Zero self-feeding breaking: tend
            # deliberately leaves a refused request pending, so it would refuse
            # on every pass, forever, for every agent.
            #
            # The gate was already applied when the AGENT asked for the cycle,
            # and `Requests.request()` stored the nodes it was given. So carry
            # those forward rather than exempting the path — that is also what
            # the bead means by injecting the node into live resume context.
            # Only when the stored set is empty (a record predating the field,
            # or a request that stated a reason instead) does this fall back to
            # an explicit machine exemption: re-asking here would put the
            # question to a sweep loop, which cannot answer it.
            req_nodes = list(request.get("quipu_nodes") or [])
            rc_c = _sweep(f"cycle:{who}", lambda w=who, c=checkpoint, b=checkpoint_bead,
                          n=req_nodes: _cmd_cycle(
                argparse.Namespace(**{**vars(a), "cmd": "cycle", "agent": w,
                                      "reason": c, "self_": False,
                                      "checkpoint_bead": b,
                                      "quipu_node": n,
                                      "no_graph_context": "" if n else
                                      "mechanical: tend serving a cycle the agent "
                                      "already requested and gated",
                                      "allow_loss": False, "dry_run": False})))
            # The request is cleared by _cmd_cycle ONLY on a completed cycle, so a
            # refusal (dirty tree, no checkpoint) leaves it pending and the agent
            # is retried next pass rather than silently dropped. That is the right
            # direction: a request that evaporates on the first refusal is worse
            # than no request, because the agent stops asking.
            if rc_c != OK:
                print(f"  ⚠ tend: {who} REQUESTED a cycle and it did not complete "
                      f"(exit {rc_c}) — the request stays pending. Usually a dirty "
                      f"or unpushed tree; run `st cycle {who} -r '...'` to see it.",
                      file=sys.stderr)
        # TELL THE AGENTS THEMSELVES (aegis-7xptd5). The loop above records each
        # refusal on the request (via _cmd_cycle) and reports it to stderr, where
        # only a coordinator reading the journal would find it. The agent — the
        # one party who can actually clear a dirty tree — was told nothing, and
        # was told at request time to expect nothing. One line per refusal reason,
        # re-armed when the blockage changes.
        #
        # Re-read pending AFTER the loop so it reflects this pass's refusals and
        # this pass's completions, not the state we started from.
        blocked_told = _sweep("cycle-blocked-notify", lambda:
            notify_mod.CycleBlockedNotifier(
                Path(a.root), _registry(a), panes).sweep(
                    cycle_mod.Requests(a.root).pending()))
        if blocked_told:
            print(f"  ⚠ told {len(blocked_told)} agent(s) their requested cycle is "
                  f"blocked: {', '.join(blocked_told)}", file=sys.stderr)
        # ALERT THE IDLE FLEET (aegis-nk0e): the SOFT half of Rule Zero. If free
        # feedable workers and dispatchable beads coexist, push the coordinator —
        # a coordinator forgetting to dispatch is the same invisible failure w0kk
        # fixed for blocked workers. Deduped per idle episode, fail-open, and it
        # reuses the SAME free/dispatchable computation as hfta's hard gate.
        idle = _sweep("idle-fleet", lambda: notify_mod.IdleFleetAlerter(
            Path(a.root), _registry(a), panes, runtime, log=_log).sweep(agents))
        if idle:
            print(f"  ⚠ alerted the coordinator — {len(idle)} newly-idle feedable "
                  f"worker(s) with work ready: {', '.join(idle)}", file=sys.stderr)
        advised = _sweep("governor-setpoint", lambda: creel_advisory_mod.Alerter(
            Path(a.root), _registry(a), panes).sweep(setpoint_advisories))
        if advised:
            print(f"  ⚠ pushed changed governor setpoint advisory to the "
                  f"coordinator: {', '.join(advised)}", file=sys.stderr)
        # A SEPARATE LEDGER, deliberately. The two advisories change on different
        # events — the budget one when the trajectory error moves, this one when
        # occupancy does — so sharing a ledger would let either suppress the
        # other's push. Same recommendation-keyed dedup either way (1641346): a
        # standing "fill toward cap" keeps asking until it is acted on, a hold is
        # read once and goes quiet.
        utilized = _sweep("governor-utilization", lambda: creel_advisory_mod.Alerter(
            Path(a.root), _registry(a), panes,
            filename="governor_utilization.json",
            label="governor utilization").sweep(utilization_advisories))
        if utilized:
            print(f"  ⚠ pushed changed governor utilization advisory to the "
                  f"coordinator: {', '.join(utilized)}", file=sys.stderr)
        # SLEEP/DREAM (aegis-2o5n2): only after the normal idle-work sweep has
        # had first claim. The planner independently requires zero normal ready
        # work, so ordering and predicate both encode "lowest priority".
        dreamed = _sweep("dream", lambda: _dream_sweep(
            a, cfg, agents, panes, force=False, dry_run=False))
        if dreamed and dreamed[0] is not None:
            cycle, item_id, _reason = dreamed
            print(f"  ☾ DREAM queued {item_id} for {cycle.agent} on "
                  f"{cycle.harness}: {cycle.mode}/{cycle.domain} "
                  f"({cycle.headroom:.0f}% headroom)", file=sys.stderr)
        # BLOCKED ON A HUMAN (internal-ref): the beads NOTHING else looks at.
        # The plate-reader fix took blocked beads off plates — correct, and it
        # also removed the last thing that touched them at all. They are off
        # `bd ready`, off the Rule Zero sweep and off every capacity report, so
        # a bead blocked on a person is operationally identical to abandoned
        # while its status makes it look handled. Seventeen days on a P1
        # security bead is the specimen. This is the only thing that re-asks.
        stale_blocked = _sweep("blocked-stale", lambda: notify_mod.BlockedStaleAlerter(
            Path(a.root), _registry(a), panes, log=_log).sweep())
        if stale_blocked:
            print(f"  ⚠ re-surfaced {len(stale_blocked)} bead(s) blocked "
                  f"long enough to be forgotten: {', '.join(stale_blocked)}",
                  file=sys.stderr)
        # A DIFFERENT condition and a DIFFERENT action from age: these beads do
        # not need their blocker chased; every issue blocker is already closed
        # and the stale status itself is what hides them (aegis-mwc5j).
        misstatused = _sweep("blocked-misstatus", lambda: notify_mod.BlockedMisstatusAlerter(
            Path(a.root), _registry(a), panes, log=_log).sweep())
        if misstatused:
            print(f"  ⚠ found {len(misstatused)} MIS-STATUSED blocked bead(s) "
                  f"whose dependencies are ALL CLOSED: {', '.join(misstatused)}",
                  file=sys.stderr)
        # STALLED (aegis-e01l): the PROGRESS-over-time twin of the point-in-time
        # push above — an agent parked idle HOLDING an in_progress item with no
        # pane/item/shell change across the whole threshold window. The weaver
        # case: hours parked on a bead whose blocker had already resolved.
        stalled = _sweep("stalled", lambda: notify_mod.StalledAlerter(
            Path(a.root), _registry(a), panes, runtime, log=_log).sweep(agents))
        # aegis-es1tt: the stalled sweep now REMEDIATES — a self-heal nudge to the
        # agent first, coordinator escalation only if that goes unanswered. (_sweep
        # returns [] on crash, a dict on success.)
        _n = stalled.get("nudged", []) if isinstance(stalled, dict) else []
        _e = stalled.get("escalated", []) if isinstance(stalled, dict) else []
        if _n:
            print(f"  ⚠ self-heal nudged {len(_n)} agent(s) holding a neglected "
                  f"anchor (close-or-release): {', '.join(_n)}", file=sys.stderr)
        if _e:
            print(f"  ⚠ escalated {len(_e)} still-NEGLECTED anchor(s) to the "
                  f"coordinator (self-heal nudge unanswered): {', '.join(_e)}",
                  file=sys.stderr)
        # THE DRAIN (aegis-hdqej). Ask every live agent the tier excludes to
        # commit, push, report what went up, and stop itself. Deduped per drain
        # EPISODE, so a five-minute heartbeat does not re-broadcast every pass —
        # and re-broadcast in full if the tier relaxes and re-engages, because the
        # agents told the first time are gone by then.
        # Drains are per provider.  A Claude drain must not tell a Codex agent
        # to stop, and vice versa; the single-governor path remains one call.
        if cfg.governor.by_harness:
            drained = []
            for _h, _v in card_verdicts.items():
                _cards = [card for card in agents
                          if harness_mod.name_for(card, root=a.root) == _h]
                _gov = governors.get(_h, governors.get("base"))
                drained.extend(_sweep(f"drain:{_h}",
                                      lambda v=_v, cs=_cards, h=_h, g=_gov: _drain_sweep(
                                          a, v, cs, panes, governor_name=h,
                                          episode=0.0 if g is None else g.episode())))
        else:
            drained = _sweep("drain", lambda: _drain_sweep(a, verdict, agents, panes))
        if drained:
            print(gov_mod.render_drain(drained), file=sys.stderr)
        # ARM THE WAKE (aegis-9mehy). Re-armed on EVERY pass, because the
        # published reset timestamp moves — a wake computed an hour ago is a
        # wake for the previous window. Inside `_sweep` like every other
        # best-effort layer: a scheduler that could crash the supervisor would
        # be the same bad trade as a notifier that could (aegis-ey7n), and here
        # the worst case of doing nothing is merely a five-minute delay.
        for line in _sweep("governor-wake",
                           lambda: _governor_wake_sweep(a, verdict)):
            print(f"  {line}", file=sys.stderr)
    if not quiet:
        print()
        if verdict is not None:
            # Printed on EVERY pass a governor is configured for, including the
            # wide-open one. "No tier engaged" is a finding — an operator who
            # cannot see the governor working cannot tell it from a governor that
            # is silently off, which is the whole class of bug this repo keeps
            # paying for.
            print(verdict.render(time.time()))
        print(rep.render())
        print()
    # SAY WHAT THE BUDGET SHED (aegis-qwadc). A pass that quietly skipped half
    # its sweeps and reported a clean render would be the same silent-degradation
    # failure the crash handler above exists to avoid — worse, because a crash at
    # least prints. Named, not counted: which sweeps were skipped is the whole
    # diagnostic, and "3 sweeps deferred" tells an operator nothing about whether
    # a blocked worker went unpushed.
    if deferred:
        print(f"  ⚠ tend: the pass exceeded its {_TEND_SWEEP_BUDGET_S:.0f}s sweep "
              f"budget, so {len(deferred)} best-effort sweep(s) were DEFERRED to "
              f"the next pass: {', '.join(deferred)}. Respawn is unaffected — it "
              f"runs before any of these. A recurring deferral means the store or "
              f"a notifier is slow, not that supervision is failing.",
              file=sys.stderr)
    # The health signal, written even on a dry run — "a pass ran" is the fact
    # somebody needs when the supervisor itself has stopped. Recorded AFTER the
    # pass so it can never claim work that did not happen.
    if not a.dry_run:
        sup_mod.PassLog(Path(a.root)).record(rep)
    return OK if rep.healthy() else CANNOT_TELL


def _drain_sweep(a, verdict, agents, panes, *, governor_name="base", episode=None):
    """Broadcast the drain and report it. Returns the report rows (possibly []).

    DURABLE DELIVERY, non-negotiable: the message must survive the recipient's
    session dying, because at 95% the recipient dying is the intended outcome.
    `st inbox -d`'s own default backend is used, which on a beads deployment puts
    the instruction in the store where `st inbox` will find it even if the pane is
    gone before the agent reads it.

    ONLY LIVE AGENTS ARE TOLD. A down agent has no session to push from, and a
    durable "stop yourself" waiting for it would fire the moment it next comes
    up — a self-perpetuating shutdown nobody asked for, arriving long after the
    tier relaxed.
    """
    if verdict is None or not verdict.tier:
        # Not draining (or no governor). The sweep still runs so the LEDGER gets
        # cleared when a tier relaxes — otherwise the next episode would be
        # deduped against a stale one and half the fleet would never be told.
        gov_mod.DrainLedger(Path(a.root)).clear()
        return []
    inbox = _inbox(a, default="beads")
    me = _me(a) or "st tend"
    # Each provider has its own drain episode and ledger.  Reusing the legacy
    # ledger would let a relaxing sibling clear another provider's outstanding
    # drain, precisely when its workers still need to report their pushed WIP.
    drain_root = Path(a.root) if governor_name == "base" else (
        Path(a.root) / "governor-harness" / governor_name)
    drainer = gov_mod.Drainer(
        drain_root,
        deliver=lambda who, body: inbox.deliver(who, body, frm=me),
        stops=_stops(a),
        log=lambda msg: print(f"  {msg}", file=sys.stderr))
    rows = drainer.sweep(agents, verdict,
                         _governor_episode(a) if episode is None else episode,
                         live=lambda ag: bool(ag.pane) and panes.exists(ag.pane),
                         catalog=_catalog(a))
    return [replace(row, governor=governor_name) for row in rows]


def _governor_wake_sweep(a, verdict) -> list[str]:
    """Arm/re-arm/disarm the per-window reset wakes (aegis-9mehy).

    NO GOVERNOR, NO WAKES — and that has to include DISARMING, which is why an
    absent verdict still constructs the waker. A governor turned off (or a
    signal lost) while a wake was armed would otherwise leave a timer that fires
    a tend pass on behalf of a policy nobody is running any more.

    `wake_plan` is where every decision lives and it is pure; this function only
    hands it to systemd. Nothing here reads a tier, so no code path exists by
    which a clock could re-engage the crew on its own.
    """
    # THE ABSOLUTE PATH, never the bare name (aegis-408qs, one commit before
    # this one). systemd --user does not search ~/.local/bin, and a unit that
    # cannot exec fails 203/EXEC on every fire while the TIMER keeps reporting
    # itself healthy — 687 silent failures over two days, last time. systemd-run
    # happens to resolve the caller's PATH today, which would make this work
    # from a shell and fail from the st-tend unit's minimal environment: exactly
    # the kind of difference that ships.
    waker = sup_mod.GovernorWake(sup_mod.resolve_st_bin() or "st", Path(a.root),
                                 run=_run_rc,
                                 is_active=_systemctl_user_active,
                                 log=lambda m: print(f"  {m}", file=sys.stderr))
    plan = {} if verdict is None else gov_mod.wake_plan(verdict, time.time())
    return waker.sync(plan)


def _governor_episode(a) -> float:
    """When the CURRENT tier engaged — the key a drain broadcast dedupes on."""
    return gov_mod.FilesGovernorState(Path(a.root)).get().since


def _retire_card(a, name: str) -> None:
    """Mark an agent RETIRED on its card — the same durable mechanism
    `st tend --retire` uses, so a crash-loop retirement is visible to, and
    reversible by, exactly the commands an operator already knows.

    It names ITSELF as the actor, not $SHANTY_AGENT. This runs inside a tend
    pass, so the ambient identity is whoever happens to own the supervisor
    process — which is true and useless: the question a reader has is "what
    decided this", and the answer is the crash-loop rule, not a person. It also
    keeps the one automatic retirement path from being mistaken for a
    deliberate one, which is the distinction `retired` exists to carry.
    """
    try:
        reg = _registry(a)
        card = reg.get(name)
        reg.set(replace(card, retired=True, retired_by="st tend (crash-loop)",
                        retired_at=_now_iso()))
    except Exception as e:                        # noqa: BLE001 — never fatal
        print(f"  ⚠ could not retire {name}: {e}", file=sys.stderr)


def _tend_reauth(a) -> int:
    """tend --reauth — relaunch every AUTH-DEAD agent, in one command (aegis-arma).

    THE INCIDENT THIS REPLACES: an operator re-login rotated the shared
    credential and every live agent's session went login-expired at once — nine
    agents, each needing a by-hand `st stop` + `st new`, while every roster
    surface said `idle`. This is that recovery as ONE command, run at the moment
    only the operator can know: AFTER they have re-logged in. /login inside a
    pane is an interactive browser OAuth flow — it cannot be driven for the
    agent, so relaunch (which re-reads the refreshed credential) is the whole
    remedy.

    Deliberately a flag on tend and not an auto-heal on the default pass: a pass
    cannot know whether the operator has re-logged in yet, and relaunching
    against a still-stale credential kill-loops the fleet — each agent comes up,
    dies on its first call, and is killed again next pass, burning its frozen
    context for nothing. Same shape as the --cycle-stale rule in tend.__doc__:
    the supervisor REPORTS, the explicit flag ACTS.

    The verdict is the SAME work_state `st crew` renders (via _crew_states) —
    never a second opinion — and the respawn goes through the SAME Tender as a
    normal pass, so retirement, workspace-ensure, clone-refresh and the
    appeared-while-we-looked race guard all hold. What this adds around them:
    the kill (tend never kills), the ownership guard on it (a name match is not
    permission to kill — same rule as `st stop`), and a liveness verify after.

    HONEST BOUNDARY: the verify proves the process CAME UP, not that it is
    authed — the banner only appears on the first failed API call, so a launch
    against a still-stale credential looks identical here. If the operator did
    not re-log in first, the next `st crew` will say so.
    """
    panes = _panes(a)
    try:
        agents = _registry(a).all().exact()
    except Exception as e:
        print(f"  could not tell: {e}", file=sys.stderr)
        return CANNOT_TELL
    runtime = _runtime(a, panes)
    dead = [ag for ag, state, work, _ in _crew_states(agents, panes, runtime)
            if state == "up" and work.startswith(triage_mod.AUTH_DEAD)]
    if not dead:
        print("  no auth-dead agents — nothing to relaunch.")
        return OK

    relaunch, refused = [], 0
    for card in dead:
        if tend_mod.is_retired(card):
            # Retired-and-alive is already tend's RESURRECTED alarm; reauth must
            # not use auth-death as a side door to relaunching what was
            # deliberately stopped.
            print(f"  skip {card.name}: RETIRED — auth-dead, and deliberately "
                  f"stopped; not relaunching.")
            continue
        if not panes.owns(card.pane):
            print(f"  refused: {card.name} ({card.pane}) was not launched by st — "
                  f"refusing to kill a session st does not own. A name match is "
                  f"not permission to kill. Its own launcher recovers it, or: "
                  f"kill it by hand, then `st new {card.name}` brings it back "
                  f"st-owned.", file=sys.stderr)
            refused += 1
            continue
        relaunch.append(card)
    if a.dry_run:
        for card in relaunch:
            print(f"  would: kill {card.pane} and relaunch {card.name}")
        return REFUSED if refused else OK
    if not relaunch:
        return REFUSED if refused else OK

    print(f"  relaunching {len(relaunch)} auth-dead agent(s): "
          f"{', '.join(c.name for c in relaunch)}")
    stuck = 0
    for card in relaunch:
        _capture_history_before_kill(a, card.name, "reauth")
        panes.kill_session(card.pane)
        if panes.exists(card.pane):
            print(f"  could not tell: killed {card.pane} but it is still there — "
                  f"not relaunching {card.name} over a live session.",
                  file=sys.stderr)
            stuck += 1
            continue
        # The stamp described the DEAD launch; forget it so the respawn's stamp
        # (below) is the one `st crew` judges staleness against.
        _launches(a).forget(card.name)
    relaunch = [c for c in relaunch if not panes.exists(c.pane)]

    # THE SAME respawn path as a normal tend pass — one launcher, not a second.
    # The spawn also stamps what it launched with (same record `st new` writes),
    # so the relaunched agent's settings verdict is measured, not `unknown`.
    def _spawn(card, session):
        runtime.start(card, session)
        # Best-effort, same contract as `st new`: an unstamped agent reports
        # `unknown`, which is the state it is in — never fail the launch.
        _launched_now(a, card.name, runtime.settings_path(card))
    tender = tend_mod.Tender(
        panes, runtime, _launches(a),
        spawn=_spawn,
        refresh=_refresh_clone,
        gaps=lambda card: prov_mod.missing_kit(card, Path(a.root)),
        # Backoff + give-up (GitHub #12): a crash-looping agent must cost one
        # launch per interval, not one per pass, and must eventually be retired
        # rather than thrashed forever.
        crashes=sup_mod.CrashLog(Path(a.root)),
        retire=lambda name: _retire_card(a, name),
        log=lambda msg: print(f"  {msg}", file=sys.stderr),
    )
    rep = tender.pass_over(relaunch, dry_run=False)
    print()
    print(rep.render())

    unverified = []
    for card in relaunch:
        if not _observe_live(runtime, panes, card.pane, card):
            unverified.append(card.name)
    if unverified:
        print(f"  could not tell: {len(unverified)} relaunched agent(s) not "
              f"observed live within the timeout: {', '.join(unverified)} — "
              f"check `st log <agent>`.", file=sys.stderr)
    else:
        print(f"  {len(relaunch)} agent(s) relaunched and observed live.")
    print("  live is not authed: the login banner only shows on the first API "
          "call. If the operator has not re-logged in, they will die again on "
          "first use — `st crew` will name them auth-dead.")
    if refused or stuck:
        return REFUSED
    if unverified or not rep.healthy():
        return CANNOT_TELL
    return OK


def _actor() -> str:
    """WHO is running this — best-effort, and honestly labelled about which
    kind of answer it is.

    A crew session carries $SHANTY_AGENT (the launcher sets it), so that name
    is the good answer. A human at a shell does not, and their unix login is a
    DIFFERENT kind of identity — the two namespaces are not guaranteed
    disjoint, so `braino` alone would be ambiguous between the crew member and
    the account. The `unix:` prefix costs five characters and removes the
    ambiguity permanently. `unknown` when neither is readable: this is an audit
    line, so it must be able to say it does not know rather than guess.
    """
    who = os.environ.get("SHANTY_AGENT")
    if who:
        return who
    try:
        import getpass
        return f"unix:{getpass.getuser()}"
    except Exception:
        return "unknown"


def _now_iso() -> str:
    """UTC ISO-8601, seconds. Sortable, unambiguous, no local-tz guessing."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _tend_retire(a) -> int:
    """Retirement is a WRITE to the card, because it has to survive everything
    that could undo it: the supervisor restarting, the host rebooting, this
    process dying. That is the whole lesson of the watchdog that reverted a
    considered shutdown in under a minute.

    UN-retirement is the ARMING half, and it is the one that needed a
    pre-flight (internal-ref). --retire only ever REMOVES a card from the
    supervisor's reach, so it cannot make anything launch and is never gated.
    --unretire hands a card back to a supervisor that will start it unattended,
    which makes it the last moment a human is present to notice the card cannot
    actually be started where it claims to live. It had no check at all: it
    re-armed ian, whose card carried a gt-era pane and NO workspace, and
    nothing said a word. tend then launched it — into the supervisor's cwd,
    not into ian's tree — and the fleet had an agent that read defunct in `st
    crew` and live to the supervisor, which nobody connected for two deaths.
    """
    name = a.retire or a.unretire
    reg = _registry(a)
    try:
        card = reg.get(name)
    except LookupError as e:
        print(f"  refused: {e}", file=sys.stderr)
        return REFUSED
    if not hasattr(reg, "set"):
        print("  refused: this registry is read-only; retirement must be "
              "durable and it cannot be written here.", file=sys.stderr)
        return REFUSED
    want = bool(a.retire)

    # RETIREMENT REFERENCES ARE FOREIGN KEYS, EVEN THOUGH THE CARD FORMAT DOES
    # NOT SAY SO (aegis-z58d3). A card can disappear from supervision while its
    # name remains on work and in the reporting tree. Say those references at
    # the transition where a person can still act on them. The two surfaces
    # have deliberately different consequences: assigned work is reported (the
    # operator may be retiring first and routing second), while a reports_to
    # edge is refused because it makes the tier structurally false immediately.
    if want:
        try:
            tracker = _tracker(a)
            rows = _tracker_rows(tracker)
            assigned = [
                row for row in rows
                if (row.get("assignee") or "").split("/")[-1] == name
                and row.get("status") in {"open", "in_progress", "hooked", "blocked"}
            ]
        except Exception as e:
            print(f"  could not tell: retirement reference scan failed: {e}\n"
                  f"  {name} was NOT retired; an unreadable work store must not "
                  f"look like zero references.", file=sys.stderr)
            return CANNOT_TELL

        work = [row for row in assigned if not is_message(row.get("title", ""))]
        messages = len(assigned) - len(work)
        if work:
            print(f"  outstanding work assigned to {name} ({len(work)}):")
            for row in sorted(work, key=lambda x: x.get("id", "")):
                print(f"    {row.get('id', '?')} [{row.get('status', '?')}] "
                      f"{row.get('title', '')}")
        if messages:
            print(f"  excluded {messages} delivered inbox message(s) from the "
                  f"work count (still assigned to {name}).")

        dependents = sorted(
            ag.name for ag in reg.all().exact()
            if ag.name != name and ag.reports_to == name and not ag.retired)
        if dependents:
            print(f"  refused: {name} is still reports_to for: "
                  f"{', '.join(dependents)}. Rewire the tier before retiring "
                  f"its parent.", file=sys.stderr)
            return REFUSED

        print("  st cannot see verbally routed threads or external alert "
              "owner chains; check conversation handoffs and automation alert "
              "routing before considering retirement complete.")

    # THE PRE-FLIGHT, BEFORE THE DRY-RUN BRANCH. A dry run that reported "would
    # mark retired=False" while the real command refuses would be lying about
    # the thing dry runs exist to answer.
    #
    # TWO FAULTS, ONE GATE — the permission fault joining the workspace one.
    # The workspace half
    # is workspace.unlaunchable(); the permission half is `dangerous`, and the
    # SAME THREE CARDS carried both. That is not a coincidence: it is what
    # `retired = true` conceals. A retired card is never launched, so nothing it
    # would get wrong at launch ever becomes a symptom — un-retirement is where a
    # dormant defect becomes a live one, so it is where every launch fault must
    # be asked at once, not one per incident.
    if not want:
        gaps = launchable.launch_gaps(card)
        blocking = [g for g in gaps if g.blocking]
        if blocking and not getattr(a, "force", False):
            why = "; ".join(g.why for g in blocking)
            print(f"  refused: {why}\n"
                  f"  Un-retiring re-arms {name} for UNATTENDED respawn, and a "
                  f"supervisor cannot notice this the way you can right now. "
                  f"Fix the card and run this again, or `--force` to arm it "
                  f"anyway (the fault above does not go away — you go away).",
                  file=sys.stderr)
            return REFUSED
        for gap in blocking:
            # FORCED IS NOT SILENT. The operator overrode a refusal; the reason
            # still gets said, and still gets said in full. A --force that
            # prints nothing trains everyone to pass it by default.
            print(f"  ⚠ FORCED past a launchability fault: {gap.why}")
        # NOT a refusal, and said anyway. Manual mode is a legitimate election
        # (see launch_gaps) — but choosing it BY ACCIDENT was invisible, and the
        # arming moment is the last one where a person is looking.
        for gap in gaps:
            if not gap.blocking:
                print(f"  ⚠ {gap.why}")

    if a.dry_run:
        print(f"  would mark {name} retired={want} (by {_actor()})")
        return OK
    # THE WRITE IS RIGHT ON BOTH PATHS; ONLY THE SENTENCE WAS WRONG (internal-ref
    # item 4, partially declined — see the bead).
    #
    # `retired_by`/`retired_at` are documented in protocols.py as "WHO last moved
    # `retired`, and WHEN" — a TRANSITION record, in either direction, and
    # test_un_retiring_records_the_actor_too pins that deliberately: the question
    # internal-ref could not answer was about an UN-retirement, and answering it
    # cost two agents and a stat(1) on the card mtime. Clearing these on
    # --unretire would revert that, so we do not.
    #
    # What WAS a defect is the report: after an un-retirement this printed
    # "recorded on the card: retired_by=X retired_at=T", which reads as "X
    # retired this" — the exact inverse of what happened, in a line whose whole
    # job is to say what was recorded. Same family as the rest of this bead: a
    # tool describing an operation it did not perform. So the write stays and
    # the wording is direction-specific.
    reg.set(replace(card, retired=want, retired_by=_actor(),
                    retired_at=_now_iso()))
    if want:
        print(f"  {name} is RETIRED. `st tend` will not respawn it, and will "
              f"ESCALATE if it finds it alive.")
        print(f"  recorded on the card: RETIRED by {_actor()} "
              f"at {_now_iso()}")
    else:
        prior = (f" (the retirement it replaces was recorded by "
                 f"{card.retired_by or 'unrecorded'} at "
                 f"{card.retired_at or 'unrecorded time'})"
                 if card.retired_by or card.retired_at else "")
        print(f"  {name} is tended again.{prior}")
        print(f"  recorded on the card: UN-RETIRED by {_actor()} "
              f"at {_now_iso()} — these fields record who last MOVED "
              f"`retired`, not a retirement.")
    return OK


def _tend_status(a) -> int:
    """Installed? Active? And WHEN did a pass last run?

    The age is the point. A supervisor that has stopped does not fail — it just
    stops making things better, and that is invisible from the inside. Printing
    "last pass: 4 days ago" is what makes its absence as loud as a failure.
    """
    d = sup_mod.unit_dir()
    svc, tmr = d / sup_mod.SERVICE, d / sup_mod.TIMER
    print()
    print(f"  units       {'installed' if tmr.exists() else 'NOT installed'}"
          f"{'' if not tmr.exists() else (' (ours)' if sup_mod.ours(tmr) else ' (NOT ours)')}")
    print(f"  timer       {'active' if _systemctl_user_active(sup_mod.TIMER) else 'inactive'}")
    # is_masked IS NOT OPTIONAL HERE (aegis-unbuw). It defaults to "nothing is
    # masked", and this call site omitted it while `--install` passed it — so
    # the masked-tombstone fix landed on the path that REFUSES and missed the
    # path an operator actually reads. `st tend --status` went on calling a
    # masked, RemainAfterExit=yes oneshot an active competitor, which is the
    # same false positive, on the more-read surface, telling a human to go
    # fight a unit that can never run again.
    other = sup_mod.foreign_supervisor(_systemctl_user_active,
                                       _systemctl_user_enabled,
                                       _systemctl_user_masked)
    if other:
        print(f"  ⚠ conflict  {other[0]} is ALSO supervising this crew "
              f"({other[1]})")
    log = sup_mod.PassLog(Path(a.root))
    age = log.age_seconds()
    if age is None:
        print("  last pass   NEVER (or unreadable) — this is not 'fine'")
    else:
        rec = log.last() or {}
        print(f"  last pass   {int(age)}s ago · acted on {len(rec.get('acted') or [])}"
              f" · {len(rec.get('faults') or [])} fault(s)")
    # THE GOVERNOR, on the status surface an operator already checks (aegis-hdqej).
    # A pure READ — `persist=False` — because `--status` must never move policy;
    # the reason this feature has a status line at all is that a governor nobody
    # can observe is one nobody can tell from a governor that is off.
    gov = _governor(a)
    if gov is not None:
        verdict = gov.evaluate(persist=False)
        print(verdict.render())
        if verdict.alarm:
            print(f"  ⚠ {verdict.alarm}")
        rows = gov_mod.Drainer(Path(a.root), deliver=None, stops=_stops(a)) \
            .report(_governor_episode(a))
        if rows:
            print(gov_mod.render_drain(rows))
    print()
    return OK if tmr.exists() and age is not None else CANNOT_TELL




if __name__ == "__main__":
    raise SystemExit(main())


def _refresh_agent_worktrees(a, card) -> list:
    """Bring this agent's PROJECT WORKTREES current; return warnings.

    The tend-side counterpart to `_keep_current` (aegis-ib65p decision 7). Only
    ever called from the respawn path, where the agent is provably down.
    """
    out = []
    try:
        for wt in agent_worktrees(card.name):
            if warn := _refresh_worktree(wt):
                out.append(f"{_tree_label(wt)}: {warn}")
    except Exception as e:
        out.append(f"could not sweep worktrees: {e}")
    return out
