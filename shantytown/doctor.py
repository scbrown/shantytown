"""st doctor — what tools are here, what version, what's missing, what's stale.

The out-of-box feature (Stiwi's ask: "I want `st` to facilitate
installing these other tools"). DETECT is the product; install is a flag. Detect
is where the value measured out — "most users need to know the state far more
often than they need a fresh install" — so this module earns its slot on the
detect half, and the three lies a naive doctor tells are the whole reason it
exists:

  ABSENT vs UNKNOWN.  quipu-server --version ERRORS — it opens a store to answer
    (`error opening store .bobbin/quipu/quipu.db`), an upstream bug. A binary that
    is present but won't say its version is UNKNOWN ("I could not tell"), NOT
    missing. Collapsing them is the same class as a 429 read as "metric absent" —
    it makes a naive installer conclude quipu is broken/absent when it is neither.

  INSTALLED vs CURRENT.  bobbin 0.3.1 sits on the box while 0.6.0 is released. The
    out-of-box problem is not "not installed", it is "installed and nobody knows
    what's there". So STALE is a first-class state, and "could not check the latest
    release" is UNKNOWN, never silently CURRENT.

  DETECT touches nothing.  asking a binary who it is must not mkdir (prime's old
    bug) or write. Only --install mutates, and --dry-run shows the plan without
    running a step.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# The single word for a tool's row. Each exists to stop one specific lie (above).
ABSENT = "absent"       # binary not on PATH
UNPATHED = "unpathed"   # installed in the toolchain's install dir, but that dir is
                        # not on PATH — exit 1. Exists to stop the aegis-wmy7 lie:
                        # `go install` SUCCEEDS into GOBIN, PATH lookup can't see
                        # it, and "not installed" made a successful install
                        # indistinguishable from a failed one.
UNKNOWN = "unknown"     # present, but version or latest could not be determined — exit 2
STALE = "stale"         # present, version known, older than the latest release
CURRENT = "current"     # present, version known, == latest
PRESENT = "present"     # present, version known, latest not checked/unknown


@dataclass(frozen=True)
class ToolSpec:
    name: str
    binary: str
    version_argv: tuple[str, ...]
    version_re: str
    toolchain: str          # the toolchain needed to install ("unknown" if we don't know how)
    installs_via: str       # human description of the mechanism
    leverage: str           # the st feature this tool lights up
    release: str | None = None      # "github:owner/repo" | "forgejo:owner/repo" | None (no releases)
    version_broken: bool = False     # KNOWN upstream: --version fails; do NOT read that as absent
    # A PROBE WITH A SIDE EFFECT IS NOT A PROBE (GitHub #25). `quipu-server
    # --version` answers by STARTING A SERVER, which binds a port — on a host
    # where that port is taken the probe hangs or dies, and doctor then rendered
    # the failed probe's own address as if it were quipu's. Never run it: presence
    # comes from PATH, and the version is honestly UNKNOWN.
    version_unsafe: bool = False
    # The tool runs as a SERVICE, so a CLI lookup is the wrong question (GitHub
    # #27): reactor had been up for 9 days while doctor said "not installed" and
    # advised --install, which would have put a CLI beside the running service.
    service_unit: str | None = None
    capability_argv: tuple[str, ...] | None = None
    capability_marker: str | None = None
    capability_label: str | None = None


# Surveyed, not assumed. beads has NO releases → source build; quipu's
# --version is known-broken; reactor has no install mechanism yet (blocked upstream).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "beads", "bd", ("bd", "version"), r"(\d+\.\d+\.\d+)",
        toolchain="go",
        installs_via="go build from source (no release binary is published)",
        leverage="the beads tracker backend — st --backend beads",
        release=None,
    ),
    ToolSpec(
        "bobbin", "bobbin", ("bobbin", "--version"), r"(\d+\.\d+\.\d+)",
        toolchain="cargo",
        installs_via="release binary if published, else cargo build",
        leverage="st context — semantic code search over your repos",
        release="github:scbrown/bobbin",
    ),
    ToolSpec(
        "quipu", "quipu-server", ("quipu-server", "--version"), r"(\d+\.\d+\.\d+)",
        toolchain="cargo",
        installs_via="release binary if published, else cargo build",
        leverage="the registry (identity, required) + knowledge (episodes)",
        release="github:scbrown/quipu",
        version_broken=True,
        version_unsafe=True,
    ),
    ToolSpec(
        "reactor", "reactor", ("reactor", "--version"), r"(\d+\.\d+\.\d+)",
        toolchain="unknown",
        installs_via="no install mechanism yet — upstream has no release",
        leverage="bead-lifecycle events feeding the harness",
        release=None,
        service_unit="reactor",
    ),
    # dp records FAILED tool calls — the capabilities an agent reached for that did
    # not exist. OPTIONAL: st works whole without it; when present, st reads its
    # signal (see desirepath.py). Source-only like beads (no release published),
    # and its version is a commit hash, not semver — the regex captures the token
    # after "dp " and release=None means no latest comparison, so it reads PRESENT.
    ToolSpec(
        "desirepath", "dp", ("dp", "version"), r"dp\s+(\S+)",
        toolchain="go",
        installs_via="go install from source (no release binary is published)",
        leverage="the failed-tool-call signal — what the crew reached for that did not exist",
        release=None,
    ),
    ToolSpec(
        "codex", "codex", ("codex", "--version"), r"(\d+\.\d+\.\d+)",
        toolchain="unknown",
        installs_via="install or upgrade the OpenAI Codex CLI",
        leverage="Codex crew launch plus blocking Stop-hook delivery for leads",
        release=None,
        capability_argv=("codex", "--help"),
        capability_marker="--dangerously-bypass-hook-trust",
        capability_label="hooks floor: `codex --help` lists --dangerously-bypass-hook-trust",
    ),
)


@dataclass(frozen=True)
class Health:
    spec: ToolSpec
    present: bool
    version: str | None
    version_error: str | None    # set iff present but version could not be read
    latest: str | None
    latest_error: str | None     # set iff we tried to check latest and could not
    toolchain_ok: bool
    unpathed_at: str | None = None   # absolute path when installed off-PATH (wmy7)
    capability_ok: bool | None = None
    capability_error: str | None = None

    @property
    def state(self) -> str:
        if not self.present:
            return UNPATHED if self.unpathed_at else ABSENT
        if self.version_error is not None:
            return UNKNOWN
        if self.capability_error is not None:
            return UNKNOWN
        if self.capability_ok is False:
            return STALE
        if self.latest and self.version and _older(self.version, self.latest):
            return STALE
        if self.latest and self.version:
            return CURRENT
        return PRESENT

    @property
    def uncertain(self) -> bool:
        """True when doctor could not fully determine this tool's state — the
        'I could not tell' that must not be laundered into a clean bill."""
        return self.present and (self.version_error is not None
                                 or self.latest_error is not None
                                 or self.capability_error is not None)


# --- injectable probes: real by default, faked in tests ----------------------

def _which(name: str) -> str | None:
    return shutil.which(name)


def _service_active(unit: str, *, run=None) -> bool:
    """Is this systemd unit active? --user first, then system. Any failure is
    False: 'we could not ask' must never render as 'it is running'."""
    runner = run or _run
    for argv in ((("systemctl", "--user", "is-active", "--quiet", unit)),
                 (("systemctl", "is-active", "--quiet", unit))):
        try:
            rc, _out = runner(argv)
            if rc == 0:
                return True
        except Exception:      # noqa: BLE001 — no systemd, no permission, no answer
            return False
    return False


def _off_path_location(spec: ToolSpec, *, which, run) -> str | None:
    """Where the toolchain would have installed spec.binary OFF the PATH, if it
    is actually there — else None. Consulted only when PATH lookup fails: the
    observer widens to the writer's known destination instead of reporting
    nonexistence it cannot establish (aegis-wmy7; same declared-vs-deployed
    class as ss7x/nipg — the tool exists, the observer cannot see it).
    """
    # go ONLY, deliberately: cargo's ~/.cargo/bin is on PATH by rustup
    # convention (and is on PATH here), so widening to it would trade a real
    # false-negative fix for host-dependent detection nobody needs yet.
    if spec.toolchain != "go":
        return None
    gobin = ""
    if which("go") is not None:
        rc, out = run(("go", "env", "GOBIN"))
        if rc == 0:
            gobin = (out or "").strip()
    if not gobin:
        # go's documented default when GOBIN is unset: $GOPATH/bin, GOPATH
        # defaulting to ~/go. Checking the default even without a go on
        # PATH is deliberate — the binary outlives its toolchain.
        gobin = os.path.join(os.path.expanduser("~"), "go", "bin")
    p = os.path.join(gobin, spec.binary)
    if os.path.isfile(p) and os.access(p, os.X_OK):
        return p
    return None


def _run(argv: tuple[str, ...]) -> tuple[int, str]:
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, timeout=10)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def _fetch_latest(release: str | None, *, opener=urllib.request.urlopen) -> tuple[str | None, str | None]:
    """(version, error). Best-effort. 'could not check' is a real answer that maps
    to UNKNOWN — never to CURRENT. No release channel (source-build tools) is not
    an error: it returns (None, None)."""
    if not release:
        return None, None
    try:
        kind, slug = release.split(":", 1)
    except ValueError:
        return None, f"bad release spec {release!r}"
    # A self-hosted forge has no canonical hostname, so it is configurable and
    # the default is a local one. github is the only fixed endpoint here.
    forge = (os.environ.get("SHANTY_FORGEJO_URL") or "http://localhost:3000").rstrip("/")
    url = {
        "github": f"https://api.github.com/repos/{slug}/releases/latest",
        "forgejo": f"{forge}/api/v1/repos/{slug}/releases/latest",
    }.get(kind)
    if not url:
        return None, f"unknown release source {kind!r}"
    try:
        with opener(url, timeout=8) as resp:
            data = json.load(resp)
    except Exception as e:  # noqa: BLE001 — any failure here is "could not look", named
        return None, f"could not reach release source: {e}"
    tag = (data.get("tag_name") or data.get("name") or "") if isinstance(data, dict) else ""
    m = re.search(r"(\d+\.\d+\.\d+)", tag)
    return (m.group(1), None) if m else (None, f"no version found in latest tag {tag!r}")


def _older(a: str, b: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))
    try:
        return parts(a) < parts(b)
    except ValueError:
        return False


def detect(spec: ToolSpec, *, which=_which, run=_run, fetch=_fetch_latest,
           check_latest: bool = True, offpath=_off_path_location) -> Health:
    """Detect one tool. Reads PATH, runs --version, optionally checks the latest
    release. Mutates NOTHING."""
    present = which(spec.binary) is not None
    # A SERVICE IS PRESENT WHEN IT IS RUNNING, not when a CLI of the same name is
    # on PATH (GitHub #27). Asked the wrong way, a service that has been up for
    # days reads as "not installed" and doctor then advises an --install that
    # would put a binary beside it.
    if not present and spec.service_unit:
        present = _service_active(spec.service_unit, run=run)
    unpathed_at = None
    if not present:
        unpathed_at = offpath(spec, which=which, run=run)
    version = version_error = None
    if spec.version_unsafe:
        # Deliberately UNASKED. Not an error and not a version: doctor renders
        # this as could-not-tell, which is the truth.
        version_error = ("version not probed: this tool answers --version by "
                         "starting a server (binds a port) — a probe with a side "
                         "effect is not a probe")
    elif present or unpathed_at:
        # For an off-PATH binary, run it by its absolute path — its version is
        # as knowable as anyone's; only its PATH entry is missing.
        argv = spec.version_argv if present else (unpathed_at,) + spec.version_argv[1:]
        rc, out = run(argv)
        m = re.search(spec.version_re, out or "")
        if rc == 0 and m:
            version = m.group(1)
        else:
            # Present, but could not read a version. NAME it — do not fall through
            # to "absent". For quipu this is the known upstream --version bug.
            first = (out or "").strip().splitlines()[0] if (out or "").strip() else "(no output)"
            version_error = first[:160]

    latest = latest_error = None
    if present and check_latest:
        latest, latest_error = fetch(spec.release)

    if spec.toolchain == "unknown":
        toolchain_ok = False
    else:
        toolchain_ok = which(spec.toolchain) is not None

    capability_ok = capability_error = None
    if present and spec.capability_argv and spec.capability_marker:
        rc, out = run(spec.capability_argv)
        if rc != 0:
            first = (out or "").strip().splitlines()[0] if (out or "").strip() else "(no output)"
            capability_error = f"capability probe failed: {first[:160]}"
        else:
            capability_ok = spec.capability_marker in (out or "")

    return Health(spec, present, version, version_error, latest, latest_error,
                  toolchain_ok, unpathed_at, capability_ok, capability_error)


def detect_all(specs: tuple[ToolSpec, ...] = SPECS, **kw) -> list[Health]:
    return [detect(s, **kw) for s in specs]


def exit_code(healths: list[Health]) -> int:
    """0 = all present & accounted-for; 1 = something absent/stale (actionable);
    2 = something could not be determined ('I could not tell'). Uncertainty
    dominates: a report you cannot trust is worse than one that says 'fix this'."""
    if any(h.uncertain for h in healths):
        return 2
    if any(h.state in (ABSENT, UNPATHED, STALE) for h in healths):
        return 1
    return 0


# --- install planning: detect-before-install, refuse-loudly ------------------

@dataclass(frozen=True)
class InstallPlan:
    tool: str
    action: str          # "install" | "upgrade" | "skip" | "refuse"
    reason: str
    steps: tuple[str, ...]


def plan_install(health: Health) -> InstallPlan:
    s = health.spec
    # Only ABSENT and STALE are actionable. Do not churn a working install:
    if health.state == CURRENT:
        return InstallPlan(s.name, "skip", "already current", ())
    if health.state == PRESENT:
        return InstallPlan(s.name, "skip",
                           f"installed ({health.version}); latest not confirmed, leaving as-is", ())
    if health.state == UNKNOWN:
        # present but version unreadable — reinstalling blindly could clobber a
        # working binary over an upstream --version bug. Leave it, say why.
        return InstallPlan(s.name, "skip",
                           "present but version unknown — not reinstalling over a working binary", ())
    if health.state == UNPATHED:
        # The install already succeeded; the missing piece is the operator's
        # PATH, which no reinstall fixes. Skip WITH the one action that does.
        d = os.path.dirname(health.unpathed_at or "")
        return InstallPlan(s.name, "skip",
                           f"already installed at {health.unpathed_at} — add {d} to PATH; reinstalling would change nothing", ())
    # ABSENT or STALE from here.
    if s.release is None and s.toolchain == "unknown":
        return InstallPlan(s.name, "refuse", f"no known install mechanism — {s.installs_via}", ())
    if not health.toolchain_ok:
        # Refuse LOUDLY rather than half-install. A missing toolchain that fails
        # halfway is worse than a clean "install <toolchain> first".
        return InstallPlan(
            s.name, "refuse",
            f"toolchain '{s.toolchain}' is not on PATH — install it first, refusing to half-install",
            (),
        )
    steps = _install_steps(s, health)
    action = "upgrade" if health.state == STALE else "install"
    return InstallPlan(s.name, action, s.installs_via, steps)


def _install_steps(spec: ToolSpec, health: Health) -> tuple[str, ...]:
    """Best-known install commands. NEVER pip --break-system-packages (this host is
    PEP-668; st itself ships via pipx). Prefer a release binary; fall back to a
    source build only when there is no release."""
    if spec.name == "beads":
        return ("go install github.com/steveyegge/beads/cmd/bd@latest",)
    if spec.name == "desirepath":
        return ("go install github.com/scbrown/desire-path/cmd/dp@latest",)
    if spec.toolchain == "cargo":
        # Prefer a published release binary; cargo is the fallback when none.
        return (f"cargo install --git https://github.com/{spec.release.split(':',1)[1]}",)
    return ()


def run_install(plan: InstallPlan, *, run=_run, dry_run: bool = False) -> None:
    """Execute a plan. skip/refuse run nothing. --dry-run runs nothing (the steps
    were already shown by the report). Only this function ever mutates the box."""
    if plan.action in ("skip", "refuse") or dry_run:
        return
    for step in plan.steps:
        rc, out = run(tuple(step.split()))
        if rc != 0:
            raise RuntimeError(f"{plan.tool}: install step failed: {step}\n{out[:200]}")


# --- rendering ---------------------------------------------------------------

_GLYPH = {ABSENT: "✗", UNPATHED: "!", UNKNOWN: "?", STALE: "△", CURRENT: "✓", PRESENT: "•"}


def report(healths: list[Health], *, plans: list[InstallPlan] | None = None) -> str:
    lines = ["st doctor — tool inventory", ""]
    for h in healths:
        g = _GLYPH.get(h.state, "?")
        if h.state == ABSENT:
            detail = "not installed"
        elif h.state == UNPATHED:
            v = f"{h.version} " if h.version else ""
            detail = (f"{v}installed at {h.unpathed_at} — NOT on your PATH "
                      f"(add {os.path.dirname(h.unpathed_at)} to PATH)")
        elif h.state == UNKNOWN:
            reason = (h.version_error or h.capability_error or h.latest_error
                      or "could not determine state")
            if h.spec.version_broken and h.version_error:
                reason = f"cannot report version (known upstream bug: --version opens a store) [{reason}]"
            detail = f"present, but {reason}"
        elif h.state == STALE:
            if h.capability_ok is False:
                detail = f"{h.version} installed — required hooks capability MISSING (STALE)"
            else:
                detail = f"{h.version} installed — {h.latest} available (STALE)"
        elif h.state == CURRENT:
            detail = f"{h.version} installed (current)"
        else:  # PRESENT
            detail = f"{h.version} installed"
            if h.latest_error:
                detail += f"; latest unknown ({h.latest_error})"
        lines.append(f"  {g} {h.spec.name:8} {detail}")
        lines.append(f"      leverage: {h.spec.leverage}")
        if h.spec.capability_label:
            verdict = ("available" if h.capability_ok is True else
                       "MISSING" if h.capability_ok is False else
                       "could not verify")
            lines.append(f"      required: {h.spec.capability_label} — {verdict}")
        # When dp is present, show the signal it has actually captured — proof the
        # tool is not just installed but feeding st. Self-hiding: summary_line()
        # returns None if dp is absent or has no readable data, and we print
        # nothing (aegis-f9rv). st consumes dp's published `stats --json`, not its
        # SQLite directly.
        if h.spec.name == "desirepath" and h.present:
            from . import desirepath
            sig = desirepath.summary_line()
            if sig:
                lines.append(f"      signal: {sig}")
    lines.append("")
    if plans:
        lines.append("install plan:")
        for p in plans:
            lines.append(f"  {p.tool:8} {p.action.upper()} — {p.reason}")
            for step in p.steps:
                lines.append(f"      $ {step}")
    else:
        actionable = [h for h in healths if h.state in (ABSENT, STALE)]
        if actionable:
            lines.append("run `st doctor --install` to install/upgrade: "
                         + ", ".join(h.spec.name for h in actionable))
    return "\n".join(lines)


# --- the socket check: a dead-looking fleet must FAIL, not report -------------

SOCKET_OK, SOCKET_WRONG, SOCKET_UNKNOWN = "ok", "WRONG", "unknown"


def socket_health(registry_panes, seen_on_declared, seen_anywhere, declared):
    """Is `st` looking at the socket the fleet is actually on?

    THE FAILURE THIS REPLACES: bare tmux on a host whose agents live on a named
    socket reports EVERY AGENT DOWN — confidently, and with exit 0. From inside a
    status-bar wrapper's own pane that is guaranteed, because $TMUX then points at
    the wrapper's socket. `st crew` says the fleet is dead, `st go` refuses to
    dispatch to a pane that is right there, and nothing anywhere errors.

    So the rule, stated as a check: if the registry names panes and we can see
    NONE of them on the declared socket while some ARE visible elsewhere, that is
    a WRONG SOCKET — a configuration fault, reported as one. Seeing none anywhere
    is UNKNOWN (the fleet may genuinely be down, and this check must not claim a
    fault it cannot distinguish); seeing some is ok.
    """
    if not registry_panes:
        return SOCKET_UNKNOWN, "no panes in the registry — nothing to look for"
    if seen_on_declared:
        where = f"socket {declared!r}" if declared else "the default tmux server"
        return SOCKET_OK, (f"{seen_on_declared}/{registry_panes} registry panes "
                           f"visible on {where}")
    if seen_anywhere:
        return SOCKET_WRONG, (
            f"0/{registry_panes} registry panes are visible on "
            f"{'socket ' + repr(declared) if declared else 'the default tmux server'}, "
            f"but {seen_anywhere} of them exist on another socket. Every `st` "
            f"command here will report the fleet DEAD — silently, with exit 0. "
            f"Declare the right one as `socket` under [tmux] in "
            f"<root>/shantytown.toml.")
    return SOCKET_UNKNOWN, (
        f"0/{registry_panes} registry panes visible anywhere — the fleet may "
        f"really be down. NOT claiming a socket fault: this check cannot tell "
        f"those two apart, and guessing is how a dead fleet gets reported as a "
        f"config error and vice versa.")


# --- stray stashes in SHARED repos (aegis-pxzi4) ------------------------------

STASH_CLEAN, STASH_FOUND, STASH_UNKNOWN = "clean", "found", "unknown"


def stray_stashes(repos, *, run=_run) -> list[tuple[str, str, float, str]]:
    """(repo, ref, age_days, label) for every entry in a shared repo's stash.

    WHY DOCTOR ASKS THIS AT ALL. `refs/stash` is SHARED across every linked
    worktree — the isolation that makes a worktree safe covers the index, HEAD
    and the branch, and stops there. So one agent's `git stash list` shows
    another's entries as if they were its own, and `pop`/`drop` would take or
    destroy them. Measured 2026-08-04: I listed a sibling's stash from my own
    worktree, twelve minutes after they made it.

    There is NO stash hook, so this cannot be guarded the way commit/rebase/merge
    are. Discovery is the only lever, and nobody runs `git stash list` in a repo
    they did not stash in — which is precisely the case that matters.

    THE FINDING IS "LOOK AT THIS", NEVER "CLEAN THIS UP". A long-lived stash in a
    shared checkout is more likely to be the ONLY COPY of something than it is to
    be litter: the live fleet's two entries are a preserved orphan from a
    pre-ff-pull rescue and a deliberate pre-push set-aside. The entries a stranger
    finds oldest are exactly the ones someone chose to keep, so age reads as
    IMPORTANCE here, not as staleness — the opposite of every other age in this
    module.

    Repos are DISCOVERED by the caller, never configured; a hardcoded list is the
    failure this file's other checks already name.
    """
    out = []
    for repo in repos:
        code, txt = run(("git", "-C", str(repo), "log", "-g",
                         "--format=%gd\t%ct\t%gs", "refs/stash"))
        if code != 0 or not txt.strip():
            continue          # no stash ref at all is the overwhelmingly common case
        for line in txt.strip().splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            ref, ts, label = parts
            try:
                age = (time.time() - float(ts)) / 86400.0
            except ValueError:
                age = float("nan")
            out.append((Path(repo).name, ref, age, label))
    return out


def render_stashes(found: list[tuple[str, str, float, str]], n_repos: int) -> str:
    if not found:
        # Stated, not self-hidden. "Checked and clean" and "never checked" must
        # not render identically — the rule this whole module is built on.
        return f"  ✓ stashes  none in {n_repos} shared repo(s)"
    bits = "; ".join(f"{r} {age:.0f}d [{lbl[:52]}]" for r, _ref, age, lbl in found)
    return (f"  ⚠ stashes  {len(found)} in shared repo(s) — VISIBLE TO EVERY "
            f"worktree, so `git stash pop` here takes whoever's it is. INSPECT "
            f"before clearing; a long-lived one is likely the only copy. {bits}")
