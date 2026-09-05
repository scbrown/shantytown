"""panemem — a MemoryMax around each agent pane (aegis-0j0n1n).

WHY, MEASURED. 2026-09-03 on the crew host: one agent's local `cargo test`
reached 31.8 GiB in ten minutes, tripped systemd-oomd on the whole
`user@1000.service` slice, and oomd killed BOTH the offender AND an unrelated
agent mid-acceptance. The victim had done nothing wrong and had no way to
defend itself: oomd picks by its own policy across the slice it watches, so the
agent that allocates and the agent that dies are unrelated by construction.

    hostmem   ADMISSION. "There is not enough RAM left for another agent."
              Refuses a launch. Cannot help once an agent is already running.
    panemem   CONTAINMENT. "This pane may not exceed N GiB."
              Bounds a pane that is ALREADY running, so a runaway hits its own
              ceiling instead of the host's.

They are complements, not alternatives, and neither substitutes for the other:
admission control cannot bound a build that starts an hour after the launch it
admitted, and a ceiling cannot stop you launching an eleventh agent onto a box
with room for three.

WHAT THIS ATTACHES TO, AND WHY IT IS ALREADY THE RIGHT UNIT. tmux's systemd
integration puts every pane in its own transient scope — `systemctl --user
list-units --type=scope` shows `tmux-spawn-<uuid>.scope`, described as "tmux
child pane <pid> launched by process <server-pid>". So the per-pane cgroup we
need already exists for every crew pane; measured 2026-09-04, every one of them
carried `MemoryHigh=infinity` and `MemoryMax=infinity`. Nothing had to be built
to create the boundary. It was there, and it was open.

THE NUMBERS, and why not the obvious ones. Measured peaks on this host: ordinary
agent panes run 10-13 GiB (claude/codex plus a Rust build), and the runaway that
caused the incident reached 31.8 GiB. So a ceiling at one rustc's ~12 GiB
(hostmem's BUILD_GIB) would throttle healthy agents continuously, and continuous
reclaim is the very thing that raises slice pressure — the fix would cause the
symptom. The defaults sit above normal peak and below runaway.

THE SWAP KNOB IS A HYPOTHESIS, AND IS LABELLED AS ONE. A second incident the
same evening showed a 16 GiB `MemoryMax` on the heavy command alone was not
enough: the capped scope's reclaim thrash as it approached the limit raised
`user@1000.service` pressure to 57.8% for >20s, and oomd killed an unrelated
pane anyway. The cap fired correctly INSIDE its scope and a bystander still
died. `MemorySwapMax=0` is the lever against that — with no swap to thrash into,
a scope at its ceiling is killed promptly instead of grinding — but the host
carries 6 GiB of resident swap use, so this trades a short hard failure for a
long soft one and the trade has only been proven at small scale here. Relax it
with SHANTY_PANE_SWAP_MAX if panes start dying that used to survive.

THE ONE THING THAT MUST NEVER HAPPEN, AND DID. 2026-09-04 17:07:57: this
module's first wiring bounded THE LAUNCHER'S OWN PANE and left the launched pane
at infinity — exactly inverted. The kernel memcg-OOMed the launcher one second
later and st respawned the agent. The cause is a race nobody would guess from
the API: `tmux new-session -d` RETURNS BEFORE tmux's systemd integration has
moved the new pane into a scope of its own, so for ~20ms (measured: 0.1ms wrong,
20.4ms settled) the pane's pid still reports the cgroup it was FORKED INTO. When
st launches a pane, that cgroup is the launcher's. A single read of /proc does
not fail here — it returns a valid, live, WRONG scope belonging to a running
agent, and `set-property` then succeeds on it and reports 0.

    A LAUNCHER THAT BOUNDS ITSELF IS WORSE THAN NO CEILING AT ALL. An unbounded
    pane is the status quo. A bounded launcher is a dead agent, and it is dead
    at the exact moment it was trying to make the fleet safer.

So scope resolution WAITS for the pane to leave the caller's cgroup and REFUSES
if it never does (`resolve_pane_scope`), and `bound_pane` independently refuses
any ceiling at or below the scope's current usage — a limit below current usage
is not a limit, it is an immediate kill, and that is what turned a 0.25 GiB test
value into a dead session. Two guards for one bug on purpose: it took only one
mistake to kill an agent, so neither is allowed to be the only one standing.

WHO THE MEMCG KILLER PICKS INSIDE A CAPPED PANE — NOT THE BUILD. `claude` runs
with oom_score_adj=200. At the 17:07:57 kill the kernel chose it (anon-rss 380
MB) over whatever was holding the other ~12 GiB in the same cgroup. So a per-pane
MemoryMax converts "runaway build" into "dead agent". That is still much better
than "dead BYSTANDER agent", which is what this module exists to prevent — but it
is why bounding the BUILD (aegis-0j0n1n item (d)) is not optional extra credit,
and why the ceiling is set above normal agent peak rather than tight.

WHAT THIS DOES NOT FIX, AND IT IS MEASURED, NOT ASSUMED. oomd watches
`user@1000.service`, not our scopes, and selects victims across everything under
it. Bounding each pane removes the common cause; it does NOT stop oomd punishing
the slice. Measured 2026-09-04 with `scripts/test-panemem.sh`: an 8 GiB runaway
inside a pane capped at High=2G/Max=3G was contained perfectly — usage never left
the ceiling, the bystander and all three live crew sessions survived — and it
still drove `user@1000.service` full-pressure avg10 to 61.5%, held above oomd's
50% limit for 26 SECONDS, past oomd's 20s duration. On that run oomd happened not
to reap anyone; the condition it fires on was met. Per-pane limits are NECESSARY
AND NOT SUFFICIENT. Item (a) — crew panes in their own slice with
`ManagedOOMMemoryPressure` scoped to it — is host configuration and is required,
not optional; it is tracked on aegis-0j0n1n and is not fixed here.

FAILURE IS LOUD, NOT FATAL. A pane that could not be bounded is worth having;
a pane that is silently unbounded while we believe otherwise is the state this
module exists to end, and it is the failure mode this repo keeps rediscovering.
So `apply()` never raises into a launch path, and always returns WHY.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

#: Kill, inside the pane's own scope. The 31.8 GiB runaway would have died here
#: instead of taking a bystander with it. Above the 10-13 GiB an ordinary agent
#: pane reaches (claude/codex plus a Rust build), below runaway.
DEFAULT_MAX_GIB = 20.0

#: Throttle — DELIBERATELY EQUAL TO THE CEILING, i.e. NO throttle band.
#:
#: This started at 16.0 against a 20.0 ceiling, on the reasoning that a runaway
#: should meet back-pressure before it meets the killer (aegis-0j0n1n item (c),
#: "so a build slows instead of thrashing"). MEASURED, that reasoning is exactly
#: backwards on this host. Controlled run 2026-09-04, MemoryMax fixed at 3 GiB,
#: an 8 GiB demand, `user@1000.service` full-pressure avg10 sampled every 500ms,
#: each arm repeated:
#:
#:     High=2G < Max=3G   peak 76.9 / 87.6   held >50% for 52s / 46s
#:     High=Max=3G        peak  4.3 /  7.5   held >50% for  0s /  0s
#:
#: oomd fires on >50% sustained for 20s, so the throttle band MEETS ITS FIRING
#: CONDITION and the plain ceiling does not come near it (baseline was 1.0%). The
#: band does not slow a runaway gracefully; it grinds the whole slice for the
#: best part of a minute while the pane it is "protecting" stays alive — which is
#: the precise mechanism that killed a bystander in the second incident on the
#: bead. Without the band the pane is memcg-OOM-killed in about a second and
#: nothing outside it notices.
#:
#: So the trade this module makes, stated plainly: ONE pane dies, in its own
#: scope, attributably, and st tend respawns it — instead of an arbitrary pane
#: dying to oomd's victim policy. Lower this knob only with a pressure
#: measurement in hand; `scripts/test-panemem.sh` arm 2b asserts the band is
#: still harmful, so it will tell you if this ever stops being true.
DEFAULT_HIGH_GIB = DEFAULT_MAX_GIB

#: See the module docstring: the anti-thrash lever, and the least certain of the
#: three. "0" means no swap for a pane; "infinity" restores the old behaviour.
DEFAULT_SWAP_MAX = "0"

#: How long to wait for tmux's systemd integration to move a new pane into its
#: OWN scope. This is not a tunable, it is a race: `tmux new-session -d` returns
#: BEFORE the pane has been moved, and until it is moved the pane's pid still
#: reports the cgroup it was FORKED INTO — the launcher's. MEASURED on the crew host
#: 2026-09-04, polling every 10ms: the pane reported the launcher's own scope at
#: 0.1ms and settled into its own at 20.4ms. 2s is ~100x that.
#: 2.0 was not enough under load — observed refusing a real launch during the
#: pressure experiment above, when systemd --user was itself contended. The
#: normal case costs 20ms; only the failing case waits, and the failing case
#: ends in a refusal, so waiting longer is strictly cheaper than refusing early.
SCOPE_SETTLE_TIMEOUT_S = 5.0
SCOPE_POLL_S = 0.01

_GIB = 1024 ** 3


@dataclass(frozen=True)
class Limits:
    high: int          # bytes
    max: int           # bytes
    swap_max: str      # a systemd value, not a number: "0" or "infinity"

    def properties(self) -> list[str]:
        return [
            f"MemoryHigh={self.high}",
            f"MemoryMax={self.max}",
            f"MemorySwapMax={self.swap_max}",
        ]


@dataclass(frozen=True)
class Applied:
    """The outcome of trying to bound one pane.

    `ok` False is not an error to raise — it is a fact to report. `reason`
    always says which step failed, because "limits were not applied" and "limits
    were applied and then removed" need different responses and a bare False
    cannot tell them apart.
    """
    ok: bool
    scope: str | None
    reason: str

    def __bool__(self) -> bool:      # so callers can `if not applied:`
        return self.ok


def _gib_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return v if v > 0 else default


def limits() -> Limits | None:
    """The configured ceiling, or None when bounding is switched off.

    SHANTY_PANE_MEMORY=off disables it outright — an escape hatch that must
    exist, because a wrong ceiling that cannot be turned off is worse than no
    ceiling: it would make every pane die and leave no way to launch the agent
    that would fix it.
    """
    if (os.environ.get("SHANTY_PANE_MEMORY") or "").strip().lower() in {"off", "0", "false"}:
        return None
    high = _gib_env("SHANTY_PANE_HIGH_GIB", DEFAULT_HIGH_GIB)
    mx = _gib_env("SHANTY_PANE_MAX_GIB", DEFAULT_MAX_GIB)
    # A MemoryHigh above MemoryMax is meaningless: the kill would always come
    # before the throttle, so the throttle could never fire. Clamp rather than
    # refuse — a misconfigured pair should still get a working ceiling.
    if high > mx:
        high = mx
    swap = (os.environ.get("SHANTY_PANE_SWAP_MAX") or DEFAULT_SWAP_MAX).strip()
    return Limits(high=int(high * _GIB), max=int(mx * _GIB), swap_max=swap)


def scope_of_pid(pid: int | str) -> str | None:
    """The systemd scope holding `pid`, read from /proc — or None.

    cgroup v2 writes a single `0::<path>` line; the unit is the last path
    segment. We accept ONLY a `.scope`, because that is the unit type a
    transient per-pane cgroup is, and setting properties on something else
    (a .service, or the slice itself) would apply a per-pane ceiling to a
    shared parent — a far worse outcome than doing nothing.
    """
    try:
        text = Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        _, _, path = line.partition("::")
        if not path:
            continue
        unit = path.rstrip("/").rsplit("/", 1)[-1]
        if unit.endswith(".scope"):
            return unit
    return None


def _strip_guid(address: str) -> str:
    """Every `guid=` parameter removed, respecting D-Bus address GRAMMAR.

    A D-Bus address is `transport:key=value,key=value`, and the variable may hold
    SEVERAL of them separated by `;` — alternatives to try in order. Splitting the
    whole string on `,` therefore does not merely miss a guid; it corrupts the
    value. Measured against the first implementation of this function:

        in   unix:path=/run/user/1000/bus,guid=A;tcp:host=h,port=1,guid=B
        out  unix:path=/run/user/1000/bus,port=1

    The `guid=A;tcp:host=h` fragment is one comma-separated piece, so dropping it
    deleted the tcp transport and left its `port=1` spliced onto the unix address
    — a malformed address that names a socket it cannot reach. The same flaw runs
    the other way too: `unix:guid=A,path=/x` keeps its guid, because that guid sits
    behind the `unix:` transport prefix and so does not start the piece.

    Neither shape occurs for a user session bus on this host, which is exactly why
    it is worth fixing now rather than when it does: the failure is silent, and the
    thing it silently disables is a containment guarantee.

    Alternatives that consist of nothing BUT a guid are dropped — they carry no way
    to connect. If that empties the whole address, the caller keeps the original.
    """
    alts: list[str] = []
    for one in address.split(";"):
        if not one:
            continue
        transport, sep, params = one.partition(":")
        if not sep:
            # Not `transport:params` at all; we do not understand it, so we do not
            # get to rewrite it.
            alts.append(one)
            continue
        kept = [p for p in params.split(",") if not p.startswith("guid=")]
        if kept:
            alts.append(f"{transport}:{','.join(kept)}")
    return ";".join(alts)


def launch_env() -> dict[str, str]:
    """os.environ with a STALE D-BUS GUID STRIPPED — the thing that silently
    switched this whole module off fleet-wide (aegis-ihl7ie, dearing).

    tmux does not create a pane scope by itself. It registers a `JobRemoved`
    signal match on the session bus, asks systemd for a transient scope, and
    only then moves the pane. If that match cannot be registered it gives up and
    leaves the pane in the launcher's cgroup — with NO error on a normal
    invocation, no journal line, and a perfectly healthy-looking pane. Every
    ceiling this module would have set simply does not exist.

    DBUS_SESSION_BUS_ADDRESS carries an optional `guid=` naming the daemon that
    was listening when the variable was set. When the session bus is replaced —
    on 2026-09-04 because systemd-oomd killed the graphical session — every
    process still holding the old string keeps a guid that no longer matches, and
    sd-bus REJECTS the connection at AUTH. That is inherited by everything those
    processes launch, so it outlives the restart indefinitely.

    Two things made it hard to see, both worth knowing:
      * `busctl --user status` still ANSWERS, which reads as a healthy bus. Every
        call that matters returns "Access denied".
      * `tmux set-environment -g` does not repair a tmux server that is already
        running; the server's own environment is what its panes inherit.

    The guid is an OPTIMISATION, not an identifier we need — the socket path is
    the address. Dropping it costs nothing and makes a launch survive a bus that
    has been replaced since this process started. Measured: with the guid the
    match is refused and the pane lands in the launcher's cgroup; with it
    stripped, the identical launch produces a real tmux-spawn-<uuid>.scope.

    This is a LAUNCHER fix and reaches only tmux servers st starts from now on.
    A server already running with a stale address keeps failing until it is
    restarted — which is why the host condition still needs fixing (aegis-ihl7ie)
    and this is hardening, not a substitute for it.
    """
    env = dict(os.environ)
    addr = env.get("DBUS_SESSION_BUS_ADDRESS")
    if addr and "guid=" in addr:
        stripped = _strip_guid(addr)
        # Empty means every alternative was nothing but a guid. Emptying the
        # variable would turn a broken address into a MISSING one, which is a
        # different failure rather than a fix, so leave it exactly as found.
        if stripped:
            env["DBUS_SESSION_BUS_ADDRESS"] = stripped
    return env


def own_scope() -> str | None:
    """The scope THIS process is running in — i.e. the LAUNCHER'S OWN PANE.

    st launches panes from inside other panes (`st go`, `st new`) and from
    `st tend`. So a scope misresolution here is never aimed at nothing: it is
    aimed at a live agent, usually the one doing the launching. That is not a
    hypothetical — it is what happened (aegis-0j0n1n, 2026-09-04 17:07:57): the
    launch path bounded its own pane, the kernel memcg-OOMed it, and st had to
    respawn the agent. Everything that resolves a scope compares against this.
    """
    return scope_of_pid(os.getpid())


def _cgroup_path_of_pid(pid: int | str) -> str | None:
    """The full cgroup v2 path for `pid` (e.g. /user.slice/.../x.scope), or None."""
    try:
        text = Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        _, _, path = line.partition("::")
        if path:
            return path.rstrip("/")
    return None


def _is_descendant(pid: int, ancestor: int, limit: int = 64) -> bool:
    """True if `pid` is `ancestor` or below it. A vanished pid reads True — it is
    gone, so it is not evidence of a foreign tenant."""
    seen = 0
    cur = pid
    while cur > 1 and seen < limit:
        if cur == ancestor:
            return True
        try:
            stat_line = Path(f"/proc/{cur}/stat").read_text()
        except OSError:
            return True                      # exited mid-check; not a tenant
        # comm can contain spaces AND parentheses, so ppid is the 2nd field
        # AFTER the final ") " — the same trap the tmux audit shim documents.
        try:
            cur = int(stat_line.rsplit(") ", 1)[1].split()[1])
        except (IndexError, ValueError):
            return False
        seen += 1
    return False


def scope_is_exclusive_to(pane_pid: int | str, cgroup_path: str) -> tuple[bool, str]:
    """Does this cgroup hold ONLY the pane we just launched? -> (ok, why).

    THE GUARD THAT WAS MISSING, and its absence bound a live agent's cgroup
    (aegis-ihl7ie, found by dearing). `resolve_pane_scope` refuses the CALLER's
    own cgroup, which is the lesson from the first incident — and "not mine" is
    not the property this needs. It is structurally blind to a THIRD PARTY's
    scope.

    Measured: with tmux's per-pane scope creation broken, `st new malcolm`
    resolved the new pane to `ptyxis-spawn-<uuid>.scope` — the long-running crew
    tmux server's own cgroup, inherited because no per-pane scope was made. That
    is not the launcher's scope, so the refusal passed, and panemem put
    MemoryHigh=MemoryMax=20G / MemorySwapMax=0 on a cgroup holding dearing's live
    agent, malcolm's, and 46 processes including the terminal. Nothing had fired
    (memory.events all zero at 4.6G) but the cap bought NOTHING — it isolates no
    pane — while risking a memcg OOM that prefers `claude` at oom_score_adj=200,
    i.e. an agent.

    So the question is not "whose cgroup is this" but "is this cgroup THIS PANE'S
    ALONE". Anything else is a shared scope and must never be bound: a ceiling on
    a cgroup you do not exclusively own is a limit somebody else pays for.
    """
    procs = Path(f"/sys/fs/cgroup{cgroup_path}/cgroup.procs")
    try:
        pids = [int(x) for x in procs.read_text().split()]
    except (OSError, ValueError) as e:
        return False, f"cannot read {procs} ({e}) — refusing to bound a scope we cannot inspect"
    pane = int(pane_pid)
    foreign = [q for q in pids if not _is_descendant(q, pane)]
    if foreign:
        return False, (f"scope holds {len(pids)} process(es), {len(foreign)} outside the "
                       f"pane's tree (e.g. pid {foreign[0]}) — it is SHARED, not this "
                       "pane's own; refusing to cap somebody else's processes")
    return True, "ok"


def resolve_pane_scope(
    pane_pid: int | str,
    timeout_s: float = SCOPE_SETTLE_TIMEOUT_S,
    poll_s: float = SCOPE_POLL_S,
    launcher: str | None = None,
) -> tuple[str | None, str]:
    """The scope of the pane at `pane_pid`, once it is REALLY its own — (scope, why).

    Two things are wrong with reading /proc once, and this function exists for
    both:

    THE RACE. `tmux new-session -d` returns before tmux's systemd integration
    has moved the new pane into a scope of its own, so for ~20ms the pane's pid
    reports the cgroup it was forked into. When st launches a pane, that cgroup
    is the LAUNCHER'S. A single read therefore does not merely fail — it
    confidently returns a valid, live, wrong scope belonging to a running agent.

    THE GUARD. Waiting is not sufficient on its own, because a timeout would
    then hand back that same wrong answer. So the launcher's own scope is not a
    thing we wait past, it is a thing we REFUSE: if the pane still reports it
    when the clock runs out, this returns None. Never bound the cgroup the
    caller is standing in — no ceiling at all is strictly better, because an
    unbounded pane is the status quo while a bounded launcher is a dead agent.

    `launcher` is injectable so the selftest can prove the refusal arm without
    having to lose the race on purpose.
    """
    mine = launcher if launcher is not None else own_scope()
    deadline = time.monotonic() + timeout_s
    seen: str | None = None
    last_why = ""
    while True:
        seen = scope_of_pid(pane_pid)
        if seen and seen != mine:
            path = _cgroup_path_of_pid(pane_pid)
            if not path:
                return None, f"pid {pane_pid} left no readable cgroup path"
            ok, last_why = scope_is_exclusive_to(pane_pid, path)
            if ok:
                return seen, "ok"
            # WAIT FOR EXCLUSIVITY, DO NOT GIVE UP ON THE FIRST LOOK.
            #
            # THE BUG THIS FIXES (aegis-0j0n1n): the loop waited for the pane to
            # leave the CALLER's cgroup and then checked exclusivity ONCE. When a
            # session is created on an ALREADY-RUNNING tmux server the pane is
            # forked by the SERVER, so its first cgroup is the server's — which is
            # not the caller's, so the wait ended immediately and the exclusivity
            # check ran against the server's shared scope, before tmux had moved
            # the pane into one of its own.
            #
            # That is why only the pane whose launch STARTED the server was ever
            # bound: there, and only there, the pane's first cgroup IS the
            # caller's, so the loop waited and tmux got its 20ms.
            #
            # REPRODUCED on the live crew server 2026-09-05T06:15:38Z, from the
            # log this module now writes: the probe pane landed in
            # `ptyxis-spawn-…scope` with 18 processes, 17 outside its tree, and
            # was refused — while a scratch server created by the same code bound
            # first AND second session. One measurement, both arms, and it took
            # writing the refusals to a file to see it.
            #
            # The refusal is unchanged when the scope never becomes exclusive:
            # capping a cgroup somebody else is in stays forbidden. What changes
            # is that "not yet" no longer reads as "never".
            pass
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_s)
    if seen and seen != mine and last_why:
        # It left the caller's scope but never became its own — the shared-scope
        # case, now reported after the full settle window rather than instantly.
        return None, last_why
    if seen is None:
        return None, (f"pid {pane_pid} is not in a .scope after {timeout_s:g}s "
                      "— tmux systemd integration may be unavailable")
    # The tracker id for this refusal stays in the module docstring, not in the
    # message: this string is printed, and a public reader cannot resolve it.
    return None, (f"pid {pane_pid} still reports the LAUNCHER's own scope {seen} "
                  f"after {timeout_s:g}s — refusing to bound the caller's own "
                  "pane")


def current_bytes(scope: str) -> int | None:
    """The scope's memory.current, or None when it cannot be read."""
    raw = read_back(scope).get("MemoryCurrent", "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def apply_to_scope(scope: str, lim: Limits) -> Applied:
    """Set the ceiling on one scope. --runtime because a transient scope has no
    persistent unit file to write to; the setting lives as long as the scope,
    which is exactly as long as the pane."""
    r = subprocess.run(
        ["systemctl", "--user", "set-property", "--runtime", scope, *lim.properties()],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return Applied(False, scope, f"set-property failed: {(r.stderr or r.stdout).strip()[:200]}")
    return Applied(True, scope, "ok")


def read_back(scope: str) -> dict[str, str]:
    """What the scope ACTUALLY carries now.

    The point of this function is that `set-property` returning 0 is not proof
    the kernel accepted the value — a cgroup can refuse a limit below current
    usage, and systemd will still report success at its own layer. Callers
    verify with this, not with the return code.
    """
    r = subprocess.run(
        ["systemctl", "--user", "show", scope,
         "-p", "MemoryHigh", "-p", "MemoryMax", "-p", "MemorySwapMax",
         "-p", "MemoryCurrent", "-p", "MemoryPeak"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        k, _, v = line.partition("=")
        if k:
            out[k] = v
    return out


def bound_pane(pane_pid: int | str, launcher: str | None = None) -> Applied:
    """Bound the pane whose shell is `pane_pid`. Never raises.

    Called from the launch path, so every failure mode here has to end in a
    returned `Applied` rather than an exception: a pane that could not be
    bounded is still a pane the fleet needs.

    TWO REFUSALS, and they are independent on purpose (aegis-0j0n1n). The first
    incident was one bug, but it needed only one mistake to become a dead agent,
    so neither guard is allowed to be the only thing standing there:

      1. NOT THE CALLER'S OWN SCOPE. `resolve_pane_scope` waits out the race and
         refuses the launcher's cgroup outright. This is the direct fix.

      2. NOT A LIMIT THAT IS AN IMMEDIATE KILL. A MemoryMax at or below what the
         scope is ALREADY using does not constrain a future runaway — it OOM-kills
         the cgroup on the spot. That is literally what happened: a 0.25 GiB
         ceiling landed on a scope holding ~1.2 GiB and the kernel killed it one
         second later. A freshly launched pane uses a few MB, so this can only
         fire when something else has already gone wrong — which is exactly when
         a second guard is worth having. MemoryHigh gets the same treatment one
         step down: a throttle below current usage is not back-pressure, it is
         the reclaim thrash that raised `user@1000.service` pressure to 57.8% and
         got a bystander killed by oomd in the first place.

    NOTE, and it is not fixed here because it cannot be: inside a capped pane the
    memcg OOM killer prefers THE AGENT. `claude` runs with oom_score_adj=200, so
    at the 17:07:57 kill the kernel chose it (anon-rss 380 MB) over whatever was
    actually holding the other ~12 GiB. A per-pane MemoryMax therefore converts
    "runaway build" into "dead agent" — better than "dead BYSTANDER agent", which
    is what this module is for, but it is why MemoryHigh is the load-bearing
    lever for a pane and why bounding the BUILD (route (d) on the bead) is not
    optional extra credit.
    """
    lim = limits()
    if lim is None:
        return Applied(False, None, "disabled by SHANTY_PANE_MEMORY")
    try:
        scope, why = resolve_pane_scope(pane_pid, launcher=launcher)
    except Exception as e:                                  # pragma: no cover
        return Applied(False, None, f"cannot read cgroup of pid {pane_pid}: {e}")
    if not scope:
        return Applied(False, None, why)
    cur = current_bytes(scope)
    if cur is not None:
        if cur >= lim.max:
            return Applied(False, scope,
                           f"refusing MemoryMax={lim.max} on {scope}: it already "
                           f"holds {cur} bytes, so that ceiling is an immediate "
                           "memcg OOM kill, not a limit")
        if cur >= lim.high:
            return Applied(False, scope,
                           f"refusing MemoryHigh={lim.high} on {scope}: it already "
                           f"holds {cur} bytes, so that throttle is reclaim thrash "
                           "from the first instant")
    try:
        return apply_to_scope(scope, lim)
    except Exception as e:                                  # pragma: no cover
        return Applied(False, scope, f"set-property raised: {e}")
