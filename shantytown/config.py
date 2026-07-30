"""config — shantytown's own TOML: WHO comes up, and when the admin may sleep.

    <root>/shantytown.toml    (optional; absent means the built-in defaults)

THE FILENAME IS `shantytown.toml`, NOT `shanty.toml`, for the same reason the
binary is `st` and not `shanty` (cli.py's docstring): `shanty` is Stiwi's tmux
command — a DIFFERENT program on the same operator's PATH and in the same
workspaces. `shanty.toml` in a repo root is a file that program has every right
to claim, and a config file two tools each believe they own is a config file that
gets edited for the wrong reader. Full name, no ambiguity.

WHY A SECOND CONFIG FILE, when deployment.py already reads env.json. They answer
different questions and one of them is not env-shaped:

  env.json          WHERE this deployment's plumbing lives — a tracker path, a
                    guard script, a server URL. Flat string values, every one of
                    which is also settable as an environment variable, because
                    that is what a launcher can carry into a pane.
  shantytown.toml   WHAT the operator wants running, and how much of it. A mode
                    is a NAMED SET of crew plus a policy — nested, list-valued,
                    and edited by a human deciding how many tokens tonight costs.
                    That does not flatten into KEY=value without inventing a
                    syntax (SHANTY_MODE_LITE_CREW="administrator,lead"), and a
                    config syntax nobody can read is a config nobody audits.

tomllib is stdlib on 3.11+, which this package already requires — so the TOML
costs no dependency, and `dependencies = []` in pyproject stays true.

ONE READER, same discipline deployment.py is built on: every surface that needs a
mode, a roster or the hibernate policy calls in here. The comment in deployment.py
about three copies of a six-line resolver is the whole reason this module is not
just `tomllib.load` at each call site.

TWO LOADERS, because the two callers have opposite failure needs, and rounding
them to one answer is a bug in whichever direction you round:

  load()            RAISES ConfigError on malformed TOML or an unknown key. For
                    `st start` — an operator ran a command that launches agents,
                    and silently starting the WRONG SET because a comma was
                    misplaced is worse than not starting. Refuse and say where.
  load_or_default() Never raises. For the STOP-HOOK path, which runs inside a
                    blocking hook in every agent's process: a config typo must
                    not wedge the fleet. It returns the defaults and hands back
                    the error string so the caller can be LOUD about running on
                    defaults — silence there is how a hibernate policy nobody
                    noticed was ignored becomes "the admin never sleeps".

The defaults are deliberately the CHEAP ones: mode `lite` (the administrator
alone) and hibernate `off`. A missing config file must never bring up nineteen
agents, and it must never put an agent to sleep that the operator did not ask to
sleep — the surprising direction of each knob is the one that costs money or
loses work, so the default is the other one.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .protocols import Agent

CONFIG_NAME = "shantytown.toml"

# A mode selects crew by ROLE or by NAME; `*` is every card. Roles are the tier's
# own words (tier.VALID_ROLES) so a mode never invents a vocabulary.
ALL = "*"
ROLE_SELECTORS = ("administrator", "lead", "worker")

# The built-in modes. `lite` is the token-conservation floor: the administrator
# and nobody else, because the admin is the one agent that can decide who else is
# needed. `heavy` is every card that is not retired.
BUILTIN_MODES: dict[str, list[str]] = {
    "lite": ["administrator"],
    "heavy": [ALL],
}
DEFAULT_MODE = "lite"

# hibernate — ONE boolean and one safety valve. It was four trigger values and a
# percentage; docs/stop-policy-spec.md 8.2 argues the collapse. Short version:
# once Rule Zero is rank 2 of one ordered decision, an idle THRESHOLD can never
# change an outcome — crew idle with work ready is already a block, and crew idle
# with nothing dispatchable is not a reason to wake.


class ConfigError(ValueError):
    """The config file exists and is wrong. Always names the file and the key —
    a config error that does not say WHERE is a config error you edit by guessing.
    """


@dataclass(frozen=True)
class Hibernate:
    """When may the administrator's stop hook decline to wake it up?

    THE THING BEING GATED, stated precisely, because "hibernate" invites a wrong
    picture: nothing is suspended and no process sleeps. The administrator's Stop
    hook currently DRAINS — it blocks at the end of every turn and re-injects
    whatever arrived, which is what keeps a coordinator awake (and billing) all
    night. Hibernating means that block is SKIPPED while the fleet has nothing
    for the coordinator to decide, so the admin's turn actually ends. It is woken
    the way it is already woken today: `st tend`'s pushes, an `st inbox`, a
    dispatch. Hibernate removes a self-wake; it never removes a wake.

    WHY THE UNDELIVERED EVENTS ARE SAFE. Declining to drain does NOT consume
    anything: stop_event's deferral discipline already distinguishes "not
    delivered" from "delivered" and only marks the latter. A skipped drain leaves
    every event exactly where it was, so the next wake sees the full backlog. If
    that were not already true, this feature would be a work-loss bug.
    """
    enabled: bool = False
    # NOT a schedule to wake ON — a BOUND on how long a pending batch may sit
    # unread while nothing pushes. 0 disables it, which is legitimate: `st tend`
    # pushes, and a push is a wake with a REASON, which beats a timer.
    #
    # There was an `idle_percent` here, and it is gone (spec 8.2): once Rule Zero
    # is rank 2 of one ordered decision, an idle THRESHOLD cannot change an
    # outcome. Crew idle with work ready is already a block; crew idle with
    # nothing dispatchable is not a reason to wake. No third state exists.
    max_quiet_minutes: int = 60


@dataclass(frozen=True)
class Fleet:
    """Fleet-wide state that Rule Zero must respect (GitHub #29, #23).

    stood_down — THE QUIET STATE Rule Zero did not have. The gate could only ever
    demand MORE dispatch: there was no state in which standing the fleet down was
    the right answer, so a deliberate quiet period was indistinguishable from
    neglect and the gate could not be satisfied by RESTRAINT. Measured: an operator
    out of usage credits stopped nine of eleven crew on instruction, and the hook
    then blocked every stop demanding they be re-dispatched.

    It is CONFIG, not a runtime flag, for the same reason retirement lives on a
    card: a deliberate shutdown must survive the session that decided it.

    max_load — the capacity ceiling. Rule Zero compared nothing against the host:
    it forced dispatch to 9 agents on an 8-core box at load 33. 0 disables the
    check; the default is expressed per-core, so it travels between hosts.
    """
    stood_down: bool = False
    max_load_per_core: float = 4.0


@dataclass(frozen=True)
class Config:
    """The whole file, resolved. `path` is None when no file was found — callers
    render that differently from a file that said the same thing by hand, because
    "you are on the defaults" and "your config chose this" need different fixes.
    """
    mode: str = DEFAULT_MODE
    modes: dict[str, list[str]] = field(default_factory=lambda: dict(BUILTIN_MODES))
    hibernate: Hibernate = field(default_factory=Hibernate)
    fleet: Fleet = field(default_factory=Fleet)
    # [crew.<name>] — identity declared IN THIS FILE (GitHub #11). Empty means the
    # file declares no crew, which is the normal case: cards or the graph own it.
    crew: dict[str, Agent] = field(default_factory=dict)
    path: Path | None = None

    def selectors(self, mode: str | None = None) -> list[str]:
        """The crew selectors for `mode` (default: the configured mode).

        Raises ConfigError naming the modes that DO exist — an unknown mode is
        the one config mistake an operator makes at the command line, where the
        fix is a list of valid words and nothing else.
        """
        name = mode or self.mode
        if name not in self.modes:
            known = ", ".join(sorted(self.modes)) or "(none)"
            where = f" in {self.path}" if self.path else " (no config file)"
            raise ConfigError(f"unknown mode {name!r}{where}; defined modes: {known}")
        return list(self.modes[name])


def config_path(root) -> Path:
    return Path(root) / CONFIG_NAME


def load(root) -> Config:
    """Read <root>/shantytown.toml. Absent -> the built-in defaults. Malformed or
    unrecognised -> ConfigError.

    UNKNOWN KEYS ARE REFUSED, not ignored. A silently-dropped key is the config
    failure mode this repo has already paid for elsewhere (a hook file that was
    written, deployed, and never read): the operator edits a file, sees no error,
    and believes a policy is in force. `hibernate_percent` instead of
    `idle_percent` must be a refusal at the top of `st start`, not a coordinator
    that mysteriously never sleeps.
    """
    path = config_path(root)
    try:
        raw = path.read_bytes()
    except OSError:
        return Config()                       # no file: the defaults, and say so via path=None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: not valid TOML — {e}") from e
    return _resolve(data, path)


def load_or_default(root) -> tuple[Config, str | None]:
    """(config, error). Never raises — the stop-hook path. The error string is
    returned rather than swallowed so the caller can say out loud that it is
    running on defaults; see the module docstring."""
    try:
        return load(root), None
    except ConfigError as e:
        return Config(), str(e)
    except Exception as e:                    # noqa: BLE001 — a hook must not die here
        return Config(), f"{config_path(root)}: could not read config ({e!r})"


# --- parsing ----------------------------------------------------------------

_TOP_KEYS = {"startup", "modes", "hibernate", "fleet", "crew"}
_STARTUP_KEYS = {"mode"}
_HIB_KEYS = {"enabled", "max_quiet_minutes"}


def _refuse_unknown(path: Path, where: str, got, allowed: set[str]) -> None:
    unknown = sorted(set(got) - allowed)
    if unknown:
        raise ConfigError(
            f"{path}: unknown key(s) in [{where}]: {', '.join(unknown)}; "
            f"expected one of: {', '.join(sorted(allowed))}")


def _table(path: Path, data: dict, key: str) -> dict:
    val = data.get(key, {})
    if not isinstance(val, dict):
        raise ConfigError(f"{path}: [{key}] must be a table, got {type(val).__name__}")
    return val


def _resolve(data: dict, path: Path) -> Config:
    if not isinstance(data, dict):            # tomllib cannot produce this; be explicit
        raise ConfigError(f"{path}: top level must be a table")
    _refuse_unknown(path, "top level", data, _TOP_KEYS)

    startup = _table(path, data, "startup")
    _refuse_unknown(path, "startup", startup, _STARTUP_KEYS)

    # Operator modes are MERGED OVER the built-ins, never a replacement: a config
    # that defines only `[modes.night]` still has lite and heavy, so `st start
    # --mode lite` cannot stop working because somebody added a third mode. A
    # config MAY redefine lite/heavy — that is a deliberate override of a default,
    # which is what a config file is for.
    modes = dict(BUILTIN_MODES)
    for name, spec in _table(path, data, "modes").items():
        modes[name] = _crew_list(path, name, spec)

    mode = startup.get("mode", DEFAULT_MODE)
    if not isinstance(mode, str):
        raise ConfigError(f"{path}: startup.mode must be a string, got {type(mode).__name__}")
    if mode not in modes:
        known = ", ".join(sorted(modes))
        raise ConfigError(f"{path}: startup.mode = {mode!r} is not a defined mode; "
                          f"defined: {known}")

    return Config(mode=mode, modes=modes,
                  hibernate=_hibernate(path, _table(path, data, "hibernate")),
                  fleet=_fleet(path, _table(path, data, "fleet")),
                  crew=_crew(path, _table(path, data, "crew")),
                  path=path)


def _crew_list(path: Path, mode: str, spec) -> list[str]:
    """`[modes.<name>] crew = [...]` -> the selector list.

    Selectors are VALIDATED here as shapes (`*`, a role, or a bare name), not
    against the live registry: a mode may legitimately name an agent whose card
    does not exist yet (a config is written before a crew is hired). Whether a
    named agent EXISTS is resolve_crew's question, and it answers it against the
    roster it was handed rather than guessing here.
    """
    if not isinstance(spec, dict):
        raise ConfigError(f"{path}: [modes.{mode}] must be a table with a `crew` "
                          f"list, got {type(spec).__name__}")
    _refuse_unknown(path, f"modes.{mode}", spec, {"crew"})
    crew = spec.get("crew")
    if crew is None:
        raise ConfigError(f"{path}: [modes.{mode}] has no `crew` list — a mode that "
                          f"selects nobody would start nothing and report success")
    if isinstance(crew, str) or not isinstance(crew, list):
        raise ConfigError(f"{path}: [modes.{mode}] crew must be a LIST of strings "
                          f"(e.g. crew = [\"administrator\", \"lead\"]), got "
                          f"{type(crew).__name__}")
    out: list[str] = []
    for item in crew:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{path}: [modes.{mode}] crew entries must be non-empty "
                              f"strings; got {item!r}")
        out.append(item.strip())
    if not out:
        raise ConfigError(f"{path}: [modes.{mode}] crew is EMPTY — a mode that selects "
                          f"nobody would start nothing and report success")
    return out


# The card fields a human may DECLARE. `pane` is included but optional — omitted,
# it is generated (tier.pane_for), which is the whole point of that change.
_CREW_KEYS = {"role", "reports_to", "pane", "workspace", "workspace_source",
              "model", "harness", "dangerous", "retired"}


def _crew(path: Path, tbl: dict) -> dict:
    """[crew.<name>] -> Agent, a HUMAN-AUTHORED registry (GitHub #11).

    A legitimate source of truth, not a cache: a deployment with no ontology and
    no interest in one declares its crew here and is done. That is why the keys
    are the card's own fields rather than a reduced subset — a source of truth you
    cannot express your whole fleet in is a projection wearing a different hat.

    Validated the same way everything else in this file is: an unknown key is
    REFUSED, because a silently-dropped `workspace` is an agent launched in the
    wrong directory with no error anywhere.
    """
    from .tier import VALID_ROLES, pane_for
    out: dict[str, Agent] = {}
    for name, spec in tbl.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"{path}: [crew.{name}] must be a table, got "
                              f"{type(spec).__name__}")
        _refuse_unknown(path, f"crew.{name}", spec, _CREW_KEYS)
        role = spec.get("role", "worker")
        if role not in VALID_ROLES:
            raise ConfigError(f"{path}: [crew.{name}] role = {role!r} is not one "
                              f"of {', '.join(VALID_ROLES)}")
        for flag in ("dangerous", "retired"):
            if flag in spec and not isinstance(spec[flag], bool):
                raise ConfigError(f"{path}: [crew.{name}] {flag} must be true or "
                                  f"false, got {spec[flag]!r}")
        out[name] = Agent(
            name=name, role=role, reports_to=spec.get("reports_to"),
            pane=spec.get("pane") or pane_for(name),
            workspace=spec.get("workspace"),
            workspace_source=spec.get("workspace_source"),
            model=spec.get("model"), harness=spec.get("harness"),
            dangerous=bool(spec.get("dangerous", False)),
            retired=bool(spec.get("retired", False)))
    return out


class TomlRegistry:
    """The Registry protocol over [crew.<name>] in shantytown.toml.

    THE THIRD IMPLEMENTATION, and the first one a human writes by hand. files is a
    projection (generated cards), quipu is a graph (needs a server); this is the
    deployment that wants neither — identity in the same file as the modes, in a
    format an operator can read and diff.

    READ-ONLY on purpose. `set` would mean rewriting a hand-authored, commented
    TOML from a dataclass, which loses every comment and reorders every table —
    so `roles set` against this registry refuses rather than quietly mangling the
    file it was pointed at. Edit the file; it is the source of truth.
    """

    def __init__(self, root):
        self._root = root

    def _crew(self) -> dict:
        return load(self._root).crew

    def get(self, name: str) -> Agent:
        crew = self._crew()
        if name not in crew:
            raise LookupError(
                f"no such agent: {name} (looked in [crew.{name}] of "
                f"{config_path(self._root)})")
        return crew[name]

    def all(self) -> list[Agent]:
        crew = self._crew()
        if not crew:
            # EMPTY IS NOT NOBODY. A config with no [crew] table declares no crew
            # at all, which almost certainly means this registry was selected by
            # mistake — and returning [] would render an empty roster at exit 0,
            # the blank-answer failure this repo keeps paying for.
            raise LookupError(
                f"{config_path(self._root)} declares no [crew.<name>] tables, so "
                f"the toml registry has nothing to read. Declare the crew there, "
                f"or use --registry files/quipu.")
        return sorted(crew.values(), key=lambda a: a.name)

    def set(self, agent: Agent) -> None:
        raise LookupError(
            f"the toml registry is READ-ONLY: {config_path(self._root)} is "
            f"hand-authored, and rewriting it from a dataclass would drop every "
            f"comment and reorder every table. Edit it directly — it is the "
            f"source of truth.")


_FLEET_KEYS = {"stood_down", "max_load_per_core"}


def _fleet(path: Path, tbl: dict) -> Fleet:
    _refuse_unknown(path, "fleet", tbl, _FLEET_KEYS)
    down = tbl.get("stood_down", Fleet.stood_down)
    if not isinstance(down, bool):
        raise ConfigError(f"{path}: fleet.stood_down must be true or false, "
                          f"got {down!r}")
    load = tbl.get("max_load_per_core", Fleet.max_load_per_core)
    if isinstance(load, bool) or not isinstance(load, (int, float)) or load < 0:
        raise ConfigError(f"{path}: fleet.max_load_per_core must be a "
                          f"non-negative number (0 disables the check), "
                          f"got {load!r}")
    return Fleet(stood_down=down, max_load_per_core=float(load))


def _hibernate(path: Path, tbl: dict) -> Hibernate:
    _refuse_unknown(path, "hibernate", tbl, _HIB_KEYS)
    enabled = tbl.get("enabled", Hibernate.enabled)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{path}: hibernate.enabled must be true or false, "
                          f"got {enabled!r}")
    mins = tbl.get("max_quiet_minutes", Hibernate.max_quiet_minutes)
    if not isinstance(mins, int) or isinstance(mins, bool) or mins < 0:
        raise ConfigError(f"{path}: hibernate.max_quiet_minutes must be a "
                          f"non-negative integer (0 disables the bound), "
                          f"got {mins!r}")
    return Hibernate(enabled=enabled, max_quiet_minutes=mins)


# --- selectors -> a roster --------------------------------------------------

@dataclass(frozen=True)
class Roster:
    """Who a mode resolves to, against a real registry.

    `skipped_retired` and `unknown` are carried rather than dropped because both
    are things the operator needs told: a mode that names a retired agent is not
    starting it (and must say so, or `st start` looks like it silently ignored a
    line of config), and a mode that names an agent with no card is a typo the
    command should refuse on rather than start a smaller fleet than asked for.
    """
    names: list[str] = field(default_factory=list)
    skipped_retired: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


# Start order: the coordinator FIRST, then leads, then workers. Not cosmetic — a
# worker that comes up while its lead is down has its stop events RISE to the
# administrator with `lead-unreachable` (tier.py Q3), so booting bottom-up
# manufactures exactly the escalation the tier exists to avoid. Alphabetical
# within a tier so a boot is reproducible.
_TIER_ORDER = {"administrator": 0, "lead": 1, "worker": 2}


def resolve_crew(selectors, agents: list[Agent]) -> Roster:
    """Selectors + the roster -> the ordered list of agents to bring up.

    A RETIRED CARD IS NEVER SELECTED, whatever the selector said — including `*`.
    Retirement is a durable, deliberate shutdown (tend.py: a watchdog that could
    not tell "died" from "was killed on purpose" reverted a considered shutdown of
    eight agents in about sixty seconds). `st start --mode heavy` is the exact
    command that would resurrect a retiree, so the exclusion lives here, in the
    resolver both the command and the config path share, and it is REPORTED.
    """
    by_name = {a.name: a for a in agents}
    retired = {a.name for a in agents if bool(getattr(a, "retired", False))}
    picked: list[str] = []
    skipped: list[str] = []
    unknown: list[str] = []

    def take(name: str) -> None:
        if name in retired:
            if name not in skipped:
                skipped.append(name)
            return
        if name not in picked:
            picked.append(name)

    for sel in selectors:
        if sel == ALL:
            for a in agents:
                take(a.name)
        elif sel in ROLE_SELECTORS:
            for a in agents:
                if a.role == sel:
                    take(a.name)
        elif sel in by_name:
            take(sel)
        else:
            unknown.append(sel)

    picked.sort(key=lambda n: (_TIER_ORDER.get(by_name[n].role, 9), n))
    return Roster(names=picked, skipped_retired=skipped, unknown=unknown)
