#!/usr/bin/env python3
"""Is it SAFE to retire the */30 transcript-capture timer? (aegis-xfmon3 step 3)

sattler's ruling sequences this: land the Stop hook, prove it by delivery on one
real stop, THEN remove the timer. This gate exists because that second step, read
literally, is not sufficient — and the gap fails silently and fleet-wide.

    A LIVE AGENT RUNNING SETTINGS EMITTED BEFORE THE HOOK HAS NO ARCHIVER AT ALL.

Settings reach a live agent only on RELAUNCH (st says so itself: "NOT DEPLOYED to
N live agent(s) — they are still running the settings they launched with"). So at
the moment the hook is proven on the first relaunched agent, every OTHER live
agent is still uncovered, and the */30 timer is the only thing capturing them.
Removing it on a one-agent proof would un-cover the fleet — quietly, because the
archive keeps growing from the agents that ARE covered, and codex transcripts
live on tmpfs where uncaptured means gone at the next reboot.

Two conditions, both required:

  1. NO live agent is on stale settings   — asked via st's OWN verdict rule
     (cli._settings_verdict / _reach_buckets), never a second copy of it: two
     surfaces that could disagree about who is stale is the exact ambiguity that
     rule was unified to prevent.
  2. EVERY live agent has a hook.log line — the hook has actually fired for them,
     not merely been emitted into a file they read.

Condition 2 is the one that cannot be skipped in favour of condition 1. Settings
being current proves the agent LOADED a config naming the hook; only the log
proves the harness RAN it. This repo already has the sharper version of that
distinction: an observed-live pane proves the process came up, never that hooks
registered.

    exit 0  SAFE     — both conditions hold, for every live agent
    exit 1  NOT SAFE — named agents are uncovered; the timer must stay
    exit 2  CANNOT TELL — do NOT read as safe
"""
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("SHANTY_ROOT",
                           Path.home() / "gt" / "shantytown" / ".shanty"))
LOG = Path(os.environ.get("ST_HISTORY_DIR", ROOT / "history")) / "hook.log"


def decide(live, stale, unknown, fired):
    """(exit code, lines) — the whole verdict rule, pure and testable.

    Separated from the I/O so the decision can be exercised without a tmux
    server, a registry or a live fleet. The interesting cases are the ones that
    must NOT read as safe: an empty fleet, an UNKNOWN launch verdict, and an
    agent whose settings are current but who has never actually fired the hook.
    """
    if not live:
        # Nobody live is not a licence to remove the timer: the next agent to
        # start is covered by settings, but everything captured up to now was
        # captured BY the timer, and an empty fleet is the least informative
        # moment to make a permanent change.
        return 2, ["CANNOT TELL: no live agents — nothing has exercised the hook"]
    never = sorted(n for n in live if n not in fired)
    out = [f"live agents: {len(live)}",
           f"on stale settings (no archiver at all): {', '.join(stale) or 'none'}",
           f"launch verdict UNKNOWN: {', '.join(unknown) or 'none'}",
           f"hook has never fired for: {', '.join(never) or 'none'}"]
    if stale or unknown or never:
        out += ["",
                "NOT SAFE to remove the */30 timer — it is the only capture "
                "those agents have.",
                "Relaunch them (`st stop <agent> && st new <agent>`), let each "
                "stop once, re-run this."]
        return 1, out
    out += ["",
            "SAFE: every live agent is on current settings AND has fired the hook."]
    return 0, out


def read_fired(log: Path) -> set:
    """Agents named in the hook run log. A missing log is an EMPTY set, never an
    error — before the first stop under the new settings that is the true and
    expected state."""
    fired = set()
    if log.exists():
        for line in log.read_text(errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1]:
                fired.add(parts[1])
    return fired


def main() -> int:
    try:
        from shantytown import cli
        from shantytown.launched import FilesLaunches
        from shantytown.tmux import Tmux, declared_socket
    except Exception as e:                                  # noqa: BLE001
        print(f"CANNOT TELL: shantytown not importable ({e})")
        return 2

    try:
        reg = cli._registry(_Args())
        agents = [a for a in reg.all().exact() if not a.retired]
        panes = Tmux(socket=declared_socket(ROOT))
        launches = FilesLaunches(ROOT / "launched")
        live = [a for a in agents if a.pane and panes.exists(a.pane)]
        stale, unknown = cli._reach_buckets(
            (a.name, cli._settings_verdict(launches, a.name, True))
            for a in sorted(live, key=lambda x: x.name))
    except Exception as e:                                  # noqa: BLE001
        print(f"CANNOT TELL: could not establish who is live ({e})")
        return 2

    rc, lines = decide([a.name for a in live], stale, unknown, read_fired(LOG))
    for line in lines:
        print(line)
    print(f"hook.log: {LOG}")
    return rc


class _Args:
    """The few attributes cli's helpers read off the argparse namespace."""
    root = ROOT
    registry = "files"
    backend = None
    repo = None


if __name__ == "__main__":
    sys.exit(main())
