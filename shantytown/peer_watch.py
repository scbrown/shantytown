"""Mutual administrator liveness: each host's administrator watches the other's
(aegis-az0a40). `st fleet watch` runs ONE pass.

WHY THIS EXISTS. A two-host fleet has two administrators, and until this
nothing noticed when either could not be reached. Measured the night it was
filed: one administrator's two messages to the other came back with a false
"recipient not live" while the recipient was up, and nobody's alert fired —
the operator noticed first. The ruling: each administrator has an alert for
the other, and each TRIES TO FIX the other before anybody pages a human.

ONE PASS, cheapest probe first, each able to stop the pass:

  1. host   the peer host answers ssh at all          (else UNKNOWN)
  2. st     the peer's own `st crew --json --local`   (else UNKNOWN)
  3. pane   the peer administrator's pane exists      (else DOWN, repairable)
  4. runtime  its foreground process is not a shell   (else DOWN, not repairable)
  5. activity  age of its last stop event — REPORTED, never a verdict: an
     administrator idle at its prompt all night is contactable, and paging on
     quiet would page every night.

UNKNOWN IS NOT DOWN. A probe that could not run (the peer host is asleep, off
the network, or its st did not answer) proves nothing about the administrator.
It never alerts, never repairs, never escalates, and is exported as its own
gauge — never folded into up=0 or up=1. The Prometheus rule is the backstop
for a long unknown and for a watcher that itself died.

ON DOWN, in order: (i) tell the LOCAL administrator once per transition; (ii) ONE
repair attempt per cooldown — the peer host's own `st agent new <peer>`, over
the same ssh transport as every other cross-host st verb — verified by
probing again, never by the relaunch's exit code alone; (iii) record every
attempt in a JSON-lines log under the shanty root; (iv) escalate to a human
ONLY when the repair failed, or when the peer has been down longer than the
threshold (a down that cannot be repaired from here waits the threshold
first, so a transient does not page). One escalation per down episode.

Everything with a side effect is INJECTED (probe, alert, repair, escalate,
clock), so the whole decision table is testable without ssh, tmux or a pager.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

OK, DOWN, UNKNOWN = "ok", "down", "unknown"
EXIT = {OK: 0, DOWN: 1, UNKNOWN: 2}

SHELLS = frozenset({"bash", "sh", "zsh", "fish", "dash", "ksh", "csh", "tcsh",
                    "-bash", "-sh", "-zsh"})

DEFAULT_ESCALATE_AFTER = 15 * 60
DEFAULT_REPAIR_COOLDOWN = 30 * 60
DEFAULT_ALERT_EVERY = 60 * 60
DEFAULT_ESCALATE_RETRY = 30 * 60
ESCALATE_ENV = "SHANTY_ESCALATE_COMMAND"


@dataclass
class Observation:
    verdict: str                       # OK | DOWN | UNKNOWN
    reason: str
    peer: str | None = None            # the peer administrator's name, if resolved
    repairable: bool = False           # can `agent new` on the peer host fix it?
    repair_why: str = ""               # why not, when it cannot
    activity_age: float | None = None  # seconds since the peer's last stop event
    probes: list[tuple[str, str]] = field(default_factory=list)


# ------------------------------------------------------------------ probes --

def _ssh(peer, command: str, *, timeout: float, run=subprocess.run, input=None):
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "--",
                peer.ssh, command], input=input, capture_output=True, text=True,
               timeout=timeout)


def _remote_st(peer, *args: str) -> str:
    return ('PATH="$HOME/.local/bin:$PATH" '
            + shlex.join(["st", "--root", peer.root, "--registry", "files", *args]))


def probe(peer, peer_name: str | None, *, run=subprocess.run,
          read_peer=None, now: Callable[[], float] = time.time) -> Observation:
    """Probe the peer host's administrator, cheapest first."""
    probes: list[tuple[str, str]] = []
    # 1. host — the cheapest possible question, so an asleep host costs one
    #    connect timeout and says so, instead of a st timeout that could be
    #    either the host or st.
    try:
        r = _ssh(peer, "true", timeout=15, run=run)
    except (OSError, subprocess.TimeoutExpired) as e:
        probes.append(("host", f"unknown: {type(e).__name__}"))
        return Observation(UNKNOWN, f"host {peer.name} did not answer ssh "
                           f"({type(e).__name__})", peer=peer_name, probes=probes)
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        why = tail[-1][:160] if tail else f"exit {r.returncode}"
        probes.append(("host", f"unknown: {why}"))
        return Observation(UNKNOWN, f"host {peer.name} unreachable over ssh: {why}",
                           peer=peer_name, probes=probes)
    probes.append(("host", "ok"))
    # 2. st — the peer's OWN local census, the same read `st crew` merges.
    if read_peer is None:
        from .fleet import read_peer as read_peer
    result = read_peer(peer)
    if result.get("error"):
        probes.append(("st", f"unknown: {result['error'][:160]}"))
        return Observation(UNKNOWN, f"st on host {peer.name} did not answer: "
                           f"{result['error'][:160]}", peer=peer_name, probes=probes)
    rows = result.get("agents") or []
    if peer_name:
        row = next((r for r in rows if r.get("name") == peer_name), None)
    else:
        admins = [r for r in rows
                  if "administrator" in str(r.get("role", "")).split(",")
                  and not r.get("retired")]
        row = admins[0] if len(admins) == 1 else None
        if len(admins) > 1:
            probes.append(("st", f"unknown: {len(admins)} administrators"))
            return Observation(UNKNOWN, f"host {peer.name} lists {len(admins)} "
                               "administrators; pass --peer", probes=probes)
    if row is None:
        probes.append(("st", "unknown: no administrator card"))
        return Observation(UNKNOWN, f"host {peer.name} has no "
                           f"{peer_name or 'administrator'} card to watch",
                           peer=peer_name, probes=probes)
    name = row["name"]
    probes.append(("st", "ok"))
    last = row.get("last_active")
    age = (max(0.0, now() - float(last))
           if isinstance(last, (int, float)) and not isinstance(last, bool) else None)
    obs = lambda v, why, **kw: Observation(v, why, peer=name, activity_age=age,
                                           probes=probes, **kw)
    # 3. pane
    if row.get("state") == "cycling":
        probes.append(("pane", "unknown: cycle in flight"))
        return obs(UNKNOWN, f"{name} is cycling (a planned relaunch is in flight)")
    if row.get("cycle_request_stale"):
        # aegis-az0a40.1: the peer's st says a cycle request sits unconsumed. Its
        # state is already the pane's (up/down), so this is a note, not a verdict.
        req_age = row.get("cycle_request_age")
        probes.append(("cycle", "stale request, not in flight"
                       + (f" ({int(req_age // 60)}m old)"
                          if isinstance(req_age, (int, float)) else "")))
    if not row.get("live"):
        probes.append(("pane", f"down: {row.get('pane')} is not there"))
        return obs(DOWN, f"{name}'s pane {row.get('pane')} is not there on "
                   f"host {peer.name}", repairable=True)
    probes.append(("pane", "ok"))
    # 4. runtime — a pane whose agent exited is a login shell. `agent new`
    #    refuses an existing session, so this is DOWN and NOT repairable here.
    fg = row.get("foreground")
    if isinstance(fg, str) and fg in SHELLS:
        probes.append(("runtime", f"down: pane runs {fg}"))
        return obs(DOWN, f"{name}'s runtime has exited (its pane runs {fg})",
                   repair_why="pane present, runtime exited — `agent new` refuses "
                              "an existing session")
    work = row.get("work")
    if work in ("wedged", "auth-dead"):
        probes.append(("runtime", f"down: {work}"))
        return obs(DOWN, f"{name} is {work}",
                   repair_why=f"{work} needs a person or a stop first, not a relaunch")
    probes.append(("runtime", "ok" if fg else "not measured (peer st predates it)"))
    # 5. activity — reported, never a verdict (module docstring).
    probes.append(("activity", f"{int(age)}s" if age is not None else "not measured"))
    return obs(OK, f"{name} is up ({row.get('work', '?')})")


def repair(peer, peer_name: str, *, run=subprocess.run) -> tuple[bool, str]:
    """ONE relaunch via the peer host's own launcher. (ok, detail)."""
    try:
        r = _ssh(peer, _remote_st(peer, "agent", "new", peer_name), timeout=180, run=run)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"relaunch did not answer ({type(e).__name__})"
    lines = [ln.strip() for ln in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines()
             if ln.strip()]
    tail = lines[-1][:200] if lines else ""
    if r.returncode == 0:
        return True, f"`st agent new {peer_name}` on host {peer.name} exited 0"
    return False, f"`st agent new {peer_name}` exited {r.returncode}: {tail}"


def alert_via_inbox(root, watcher: str, text: str, *, run=subprocess.run) -> tuple[bool, str]:
    """Tell the LOCAL administrator through st's own durable inbox."""
    try:
        r = run([sys.executable, "-m", "shantytown.cli", "--root", str(root),
                 "inbox", "-d", watcher, text], capture_output=True, text=True,
                timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, type(e).__name__
    out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return r.returncode == 0, (out[-1].strip()[:200] if out else f"exit {r.returncode}")


def escalate_via_command(command: str | None, severity: str, desc: str,
                         *, run=subprocess.run) -> tuple[int | None, str]:
    """The deployment's human-escalation wrapper. (rc, detail); rc None = not
    configured. Its own exit code is the verdict: 0 paged, 1 fallback, 2 nobody."""
    if not command:
        return None, f"escalation NOT CONFIGURED (set [env] {ESCALATE_ENV})"
    try:
        r = run([*shlex.split(command), "-s", severity, desc],
                capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 2, f"escalation command failed to run ({type(e).__name__})"
    out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return r.returncode, (out[-1].strip()[:200] if out else f"exit {r.returncode}")


# ------------------------------------------------------------------- state --

class State:
    """Timestamps that make a persistent outage page ONCE. One JSON file per
    peer host; unreadable reads as empty (the cost is one extra alert)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def read(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def write(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
        os.replace(tmp, self.path)


def append_log(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError as e:
        print(f"  ⚠ peer watch log not written ({type(e).__name__}): {path}",
              file=sys.stderr)


# -------------------------------------------------------------------- pass --

@dataclass
class Outcome:
    verdict: str                     # final verdict, after any verified repair
    observed: str                    # what the first probe saw
    peer: str
    reason: str
    actions: list[str]
    down_seconds: float | None
    activity_age: float | None

    @property
    def exit_code(self) -> int:
        return EXIT[self.verdict]


UNRESOLVED = "unresolved"


def run_pass(*, watcher: str, peer_host: str, peer_name: str | None,
             probe_fn: Callable[[str | None], Observation],
             alert_fn: Callable[[str], tuple[bool, str]],
             repair_fn: Callable[[str], tuple[bool, str]] | None,
             escalate_fn: Callable[[str], tuple[int | None, str]],
             state: State, log_path: Path, now: Callable[[], float] = time.time,
             dry_run: bool = False, escalate_after: float = DEFAULT_ESCALATE_AFTER,
             repair_cooldown: float = DEFAULT_REPAIR_COOLDOWN,
             alert_every: float = DEFAULT_ALERT_EVERY,
             escalate_retry: float = DEFAULT_ESCALATE_RETRY,
             say: Callable[[str], None] = print) -> Outcome:
    """One watch pass: probe, then alert/repair/escalate by the rules above."""
    st = state.read()
    # "unresolved" is a DISPLAY label, never a name (aegis-emretk). It used to be
    # persisted as the peer after any pass that could not resolve one, and every
    # later pass then asked the census for a card literally named "unresolved",
    # reporting "no administrator card" forever: one transient failure (the Mac's
    # launchd LAN block) became permanent. Only a real name is remembered, and a
    # state file poisoned by the old code is read as "not yet resolved".
    stored = st.get("peer")
    if stored == UNRESOLVED:
        stored = None
    obs = probe_fn(peer_name or stored)
    t = now()
    resolved = obs.peer or peer_name or stored
    peer = resolved or UNRESOLVED
    actions: list[str] = []
    for name, result in obs.probes:
        say(f"  probe {name:<9} {result}")
    say(f"  {peer}@{peer_host}: {obs.verdict.upper()} — {obs.reason}")
    new = dict(st, peer=resolved, last_run=t, last_verdict=obs.verdict)
    verdict = obs.verdict
    down_since = st.get("down_since")

    def do(kind: str, what: str, fn):
        if dry_run:
            say(f"  would: {kind}: {what}")
            actions.append(f"would-{kind}")
            return None
        res = fn()
        actions.append(kind)
        return res

    recoveries = list(st.get("pending_recoveries") or [])

    def end_episode(text):
        # Observation and delivery are independent. A failed recovery send
        # must never keep the old outage alive and hide the NEXT outage.
        recoveries.append({"text": text, "observed_at": t, "last_attempt": None})
        for k in ("down_since", "last_alert", "last_alert_attempt",
                  "escalated_episode", "last_escalation"):
            new.pop(k, None)

    if obs.verdict == OK:
        if down_since is not None:
            mins = int((t - down_since) // 60)
            end_episode(f"[st fleet watch] {peer}@{peer_host} is back up after "
                        f"~{mins}m down (observed at {t:.0f}).")
    elif obs.verdict == UNKNOWN:
        say("  unknown is not down: no alert, no repair, no escalation.")
    else:
        if down_since is None:
            down_since = t
        new["down_since"] = down_since
        down_for = t - down_since
        # (i) alert once per outage. The old hourly reminder woke an agent
        # to judge an unchanged fact. alert_every now bounds failed-send retries.
        last_alert = st.get("last_alert")
        if ((last_alert is None or last_alert < down_since)
                and (st.get("last_alert_attempt") is None
                     or st["last_alert_attempt"] < down_since
                     or t - st["last_alert_attempt"] >= alert_every)):
            text = (f"[st fleet watch] peer administrator {peer}@{peer_host} is DOWN "
                    f"(~{int(down_for // 60)}m): {obs.reason}. Repairing if possible; "
                    f"see st fleet watch.")
            res = do("alert", text, lambda: alert_fn(text))
            if res is not None:
                new["last_alert_attempt"] = t
                if res[0]:
                    new["last_alert"] = t
                say(f"  alerted {watcher}: {'ok' if res[0] else 'FAILED: ' + res[1]}")
        else:
            say("  alert suppressed (unchanged outage or delivery retry pending)")
        # (ii) one repair per cooldown, verified by a second probe.
        repair_state = None
        if not obs.repairable:
            repair_state = "impossible"
            say(f"  repair: not possible from here — {obs.repair_why or obs.reason}")
        elif repair_fn is None:
            repair_state = "disabled"
            say("  repair: disabled (--no-repair)")
        elif st.get("last_repair") is not None and t - st["last_repair"] < repair_cooldown:
            repair_state = "cooldown"
            say(f"  repair: in cooldown (last attempt "
                f"{int((t - st['last_repair']) // 60)}m ago)")
        else:
            res = do("repair", f"st agent new {peer} on host {peer_host}",
                     lambda: repair_fn(peer))
            if res is not None:
                new["last_repair"] = t
                ok, detail = res
                say(f"  repair: {detail}")
                after = probe_fn(peer) if ok else None
                if after is not None and after.verdict == OK:
                    repair_state = "repaired"
                    verdict = OK
                    say(f"  repair VERIFIED: {after.reason}")
                    text = (f"[st fleet watch] peer administrator {peer}@{peer_host} was "
                            f"down ({obs.reason}) and was relaunched; verified up.")
                    end_episode(text)
                else:
                    repair_state = "failed"
                    if after is not None:
                        say(f"  repair NOT verified: {after.verdict} — {after.reason}")
        # (iv) escalate: only on a failed repair, or past the threshold.
        if verdict == DOWN:
            due = repair_state == "failed" or down_for >= escalate_after
            if st.get("escalated_episode") == down_since:
                say("  escalation: already paged for this outage")
            elif not due:
                say(f"  escalation: not yet (down {int(down_for)}s < "
                    f"{int(escalate_after)}s, repair {repair_state})")
            elif (st.get("last_escalation") is not None
                    and st["last_escalation"] >= down_since
                    and t - st["last_escalation"] < escalate_retry):
                say("  escalation: retry in cooldown (last attempt did not page)")
            else:
                desc = (f"administrator {peer} on host {peer_host} unreachable from "
                        f"{watcher} for ~{int(down_for // 60)}m: {obs.reason}; "
                        f"repair {repair_state}")
                res = do("escalate", desc, lambda: escalate_fn(desc))
                if res is not None:
                    rc, detail = res
                    new["last_escalation"] = t
                    if rc in (0, 1):
                        new["escalated_episode"] = down_since
                    say(f"  escalation: rc={rc} {detail}")
    # Retry the oldest recovery independently, even if a new outage began.
    # Keep its original observation time; delivery delay is not outage length.
    if recoveries:
        recovery = recoveries[0]
        attempted = recovery.get("last_attempt")
        if attempted is None or t - attempted >= alert_every:
            text = recovery["text"]
            res = do("alert", text, lambda: alert_fn(text))
            if res is not None:
                if res[0]:
                    recoveries.pop(0)
                else:
                    recoveries[0] = dict(recovery, last_attempt=t)
                say(f"  recovery delivery: {'ok' if res[0] else 'FAILED: ' + res[1]}")
    new["pending_recoveries"] = recoveries
    down_seconds = (0.0 if verdict == OK else
                    t - new["down_since"] if new.get("down_since") is not None else None)
    if not dry_run:
        new["last_verdict"] = verdict
        state.write(new)
        if obs.verdict != OK or actions or st.get("last_verdict") not in (None, OK):
            append_log(log_path, dict(ts=t, watcher=watcher, peer=peer,
                                      peer_host=peer_host, observed=obs.verdict,
                                      verdict=verdict, reason=obs.reason,
                                      actions=actions))
    return Outcome(verdict=verdict, observed=obs.verdict, peer=peer, reason=obs.reason,
                   actions=actions, down_seconds=down_seconds,
                   activity_age=obs.activity_age)


# ----------------------------------------------------------------- metrics --

def _label(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def metrics(watcher: str, outcome: Outcome, ran_at: float) -> str:
    w, p = _label(watcher), _label(outcome.peer)
    lab = f'watcher="{w}",peer="{p}"'
    text = ("# HELP admin_peer_up 1 = peer administrator reachable and up, 0 = down. "
            "ABSENT when the watch could not tell.\n# TYPE admin_peer_up gauge\n")
    if outcome.verdict != UNKNOWN:
        text += f"admin_peer_up{{{lab}}} {int(outcome.verdict == OK)}\n"
    text += ("# HELP admin_peer_unknown 1 = the watch could not probe the peer "
             "(host unreachable, st silent). Never folded into up/down.\n"
             "# TYPE admin_peer_unknown gauge\n"
             f"admin_peer_unknown{{{lab}}} {int(outcome.verdict == UNKNOWN)}\n")
    if outcome.down_seconds is not None:
        text += ("# TYPE admin_peer_down_seconds gauge\n"
                 f"admin_peer_down_seconds{{{lab}}} {outcome.down_seconds:.0f}\n")
    if outcome.activity_age is not None:
        text += ("# HELP admin_peer_last_activity_age_seconds Seconds since the peer "
                 "administrator's last stop event. Informational.\n"
                 "# TYPE admin_peer_last_activity_age_seconds gauge\n"
                 f"admin_peer_last_activity_age_seconds{{{lab}}} "
                 f"{outcome.activity_age:.0f}\n")
    text += ("# HELP admin_peer_watch_last_run_timestamp_seconds When this watcher "
             "last completed a pass.\n"
             "# TYPE admin_peer_watch_last_run_timestamp_seconds gauge\n"
             f'admin_peer_watch_last_run_timestamp_seconds{{watcher="{w}"}} {ran_at:.0f}\n')
    return text


def write_metrics(path: Path, text: str) -> None:
    """Atomic textfile write — node_exporter must never read half a file."""
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False,
                                     prefix=".admin-peer.", suffix=".tmp") as f:
        tmp = Path(f.name)
        f.write(text)
    try:
        tmp.chmod(0o644)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
