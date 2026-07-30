"""scaffold — what `st init` creates, and the questions it asks to decide.

A fresh clone could not reach a runnable state without hand-authoring JSON. The
store directory, the crew cards, the role settings files and the config each had a
different origin: a `mkdir`, a hierarchy file fed to `roles sync`, a side effect of
`roles set`, and a hand-written TOML. Four artifacts, three of them undocumented,
and a pane field that nothing assigned — so the first honest instruction to a new
user was "edit these files by hand".

`st init` asks instead. Everything it writes is something the operator was going to
have to write anyway, in the same format, in the same place — this creates no new
config surface and no second way to declare a crew.

WHAT IS PURE HERE AND WHAT IS NOT. This module decides (the question script, the
plan, the generated TOML) and cli does (writing cards through the registry, wiring
roles through tier.role_set, emitting settings through the same emitter `roles set`
uses). The split matters: a wizard that wrote its own cards would be a second card
writer, and the first thing it would drift on is exactly the field this whole
exercise was about.

IT REUSES `roles set` RATHER THAN WIRING THE TIER ITSELF. The stop-hook routing and
the settings emission are one generative operation in tier.py; a wizard that
duplicated them could produce a crew whose cards and hooks disagree, which is the
failure the generative design exists to prevent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config

# Agent names become FILENAMES (crew/<name>.json) and tmux SESSION names
# (st-<name>). Both constrain the character set, and a name that is legal in one
# and not the other produces a card that cannot be launched — so validate against
# the intersection, up front, rather than discovering it at launch. tmux treats `.`
# and `:` as address syntax, so neither is allowed here.
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
NAME_RULE = ("lowercase letters, digits, `-` and `_`, starting with a letter or "
             "digit (it becomes a filename and a tmux session name)")

DEFAULT_ADMIN = "admin"

# The directories a deployment needs. events/ and launched/ are created by their
# own stores on first write, but a store whose shape is visible is a store an
# operator can inspect before anything has run.
DIRS = ("crew", "settings", "events", "launched")


class ScaffoldError(ValueError):
    """A refusal: a bad name, a bad answer, or a store that already exists."""


@dataclass(frozen=True)
class Answers:
    """Everything `st init` needs to know. One object, so the interactive path and
    the flags path cannot diverge in what they produce."""
    admin: str = DEFAULT_ADMIN
    workers: tuple[str, ...] = ()
    workspaces: str | None = None      # parent dir; each agent gets <dir>/<name>
    mode: str = config.DEFAULT_MODE
    hibernate: bool = False
    max_quiet_minutes: int = 60

    def names(self) -> tuple[str, ...]:
        return (self.admin, *self.workers)


@dataclass
class Plan:
    """What init WILL create. Rendered before anything is written, so the operator
    approves a list of paths rather than a promise."""
    answers: Answers
    root: str
    dirs: list[str] = field(default_factory=list)
    cards: list[tuple[str, str, str]] = field(default_factory=list)  # name, role, pane
    settings_roles: list[str] = field(default_factory=list)
    config_text: str = ""
    config_path: str = ""

    def render(self) -> str:
        lines = [f"  store    {self.root}"]
        for d in self.dirs:
            lines.append(f"  dir      {d}/")
        for name, role, pane in self.cards:
            ws = (f"  workspace {self.answers.workspaces}/{name}"
                  if self.answers.workspaces else "")
            lines.append(f"  card     {name:<12} {role:<14} pane {pane}{ws}")
        for role in self.settings_roles:
            lines.append(f"  hooks    settings/{role}.settings.json")
        lines.append(f"  config   {self.config_path}")
        lines.append("")
        lines.append(f"  {len(self.cards)} agent(s) · startup mode "
                     f"{self.answers.mode!r} · hibernate "
                     f"{'on' if self.answers.hibernate else 'off'}")
        return "\n".join(lines)


def validate_name(name: str, what: str = "agent name") -> str:
    name = (name or "").strip()
    if not name:
        raise ScaffoldError(f"{what} cannot be empty")
    if not _NAME.match(name):
        raise ScaffoldError(f"{what} {name!r} is not usable: expected {NAME_RULE}")
    return name


def make_answers(*, admin, workers=(), workspaces=None, mode=config.DEFAULT_MODE,
                 hibernate=False, max_quiet_minutes=60) -> Answers:
    """Validate raw values into Answers. The ONE gate — the interactive path and
    the flags path both come through here, so a rule cannot apply to only one."""
    admin = validate_name(admin, "administrator name")
    seen, clean = {admin}, []
    for w in workers:
        w = validate_name(w, "worker name")
        if w in seen:
            raise ScaffoldError(f"{w!r} is named twice — each agent needs one card")
        seen.add(w)
        clean.append(w)
    if not isinstance(hibernate, bool):
        raise ScaffoldError(f"hibernate must be true or false, got {hibernate!r}")
    if not (isinstance(max_quiet_minutes, int) and max_quiet_minutes >= 0):
        raise ScaffoldError(f"max quiet minutes must be a non-negative integer "
                            f"(0 disables the bound), got {max_quiet_minutes!r}")
    # The mode must exist, or `st start` would refuse immediately after init told
    # the operator everything was ready. Only the built-ins can exist at init time.
    if mode not in config.BUILTIN_MODES:
        raise ScaffoldError(f"startup mode {mode!r} is not one of "
                            f"{', '.join(sorted(config.BUILTIN_MODES))}")
    return Answers(admin=admin, workers=tuple(clean),
                   workspaces=(workspaces or None), mode=mode,
                   hibernate=hibernate, max_quiet_minutes=max_quiet_minutes)


# --- the question script ----------------------------------------------------

def ask_all(ask, *, defaults: Answers | None = None, note=print) -> Answers:
    """Run the wizard over an injected `ask(prompt, default) -> str`.

    Injected rather than calling input() directly so the whole script is testable
    without a terminal, and so a caller can pre-answer from flags. Every question
    has a default that Enter accepts, and a rejected answer is RE-ASKED with the
    reason — a wizard that aborts the whole run on one typo makes the operator
    re-answer the questions they got right.

    `note` is a SEPARATE channel from `ask`, and that separation is not stylistic:
    a rejection message routed through `ask` would call input() again and eat the
    operator's next line as though it were the answer to a question nobody asked.
    Telling and asking are different acts and need different doors.
    """
    d = defaults or Answers()

    def asking(prompt, default, convert):
        for _ in range(5):
            raw = ask(prompt, str(default))
            try:
                return convert(raw if raw.strip() else str(default))
            except ScaffoldError as e:
                note(f"  ! {e}")
        raise ScaffoldError(f"too many invalid answers for {prompt!r}")

    admin = asking("Administrator name (the coordinator)", d.admin,
                   lambda v: validate_name(v, "administrator name"))

    def parse_workers(v):
        names = [w for w in re.split(r"[,\s]+", v.strip()) if w]
        for w in names:
            validate_name(w, "worker name")
        return tuple(names)

    workers = asking("Worker names, comma-separated (blank for none — the admin "
                     "can be alone)", ",".join(d.workers), parse_workers)

    workspaces = asking("Parent directory for agent workspaces, one dir per agent "
                        "(blank = launch in the current directory)",
                        d.workspaces or "", lambda v: v.strip() or "")

    mode = asking(f"Startup mode — {'/'.join(sorted(config.BUILTIN_MODES))} "
                  f"(lite starts the administrator ALONE)", d.mode,
                  lambda v: _one_of(v, sorted(config.BUILTIN_MODES), "mode"))

    hib = asking("Let the administrator go quiet when there is nothing to "
                 "dispatch? yes/no", "yes" if d.hibernate else "no",
                 lambda v: _a_bool(v, "hibernate"))

    mins = d.max_quiet_minutes
    if hib:
        mins = asking("  Read waiting reports after at most N minutes of quiet "
                      "(0 = only when something pushes)", mins,
                      lambda v: _an_int(v, 0, 10_080, "minutes"))

    return make_answers(admin=admin, workers=workers, workspaces=workspaces,
                        mode=mode, hibernate=hib, max_quiet_minutes=mins)


def _one_of(value: str, allowed: list[str], what: str) -> str:
    v = value.strip().lower()
    if v not in allowed:
        raise ScaffoldError(f"{what} {value.strip()!r} is not one of {', '.join(allowed)}")
    return v


def _a_bool(value: str, what: str) -> bool:
    v = value.strip().lower()
    if v in ("y", "yes", "true", "on", "1"):
        return True
    if v in ("n", "no", "false", "off", "0"):
        return False
    raise ScaffoldError(f"{what} must be yes or no, got {value.strip()!r}")


def _an_int(value: str, lo: int, hi: int, what: str) -> int:
    try:
        n = int(str(value).strip())
    except ValueError:
        raise ScaffoldError(f"{what} must be a whole number, got {value.strip()!r}") from None
    if not lo <= n <= hi:
        raise ScaffoldError(f"{what} must be between {lo} and {hi}, got {n}")
    return n


# --- the plan ---------------------------------------------------------------

def plan(root, answers: Answers) -> Plan:
    """What init would create. No writes, no filesystem reads."""
    from .tier import pane_for
    cards = [(answers.admin, "administrator", pane_for(answers.admin))]
    cards += [(w, "worker", pane_for(w)) for w in answers.workers]
    roles = ["administrator"] + (["worker"] if answers.workers else [])
    return Plan(answers=answers, root=str(root), dirs=list(DIRS), cards=cards,
                settings_roles=roles, config_text=config_text(answers),
                config_path=str(config.config_path(root)))


def config_text(answers: Answers) -> str:
    """The generated shantytown.toml.

    Deliberately writes the DEFAULTS explicitly rather than omitting them. An
    absent key and a key set to its default behave identically, but they read
    differently: a file that names `trigger`, `idle_percent` and `every_minutes`
    is a file an operator can edit by changing a value, without first learning
    which keys exist. The full annotated reference is the example file.
    """
    lines = [
        "# shantytown.toml — written by `st init`.",
        "# The fully annotated reference: docs/shantytown.toml.example",
        "#",
        "# THIS IS THE ONE FILE YOU HAND-EDIT. crew/ and settings/ are GENERATED",
        "# (`st role set`, `st project`, `st new` rewrite them); hierarchy files are",
        "# an import SOURCE for `st roles sync`; and the ~/.config pointer is a",
        "# locator, not config. Deployment plumbing that used to live in env.json",
        "# goes in [env] here.",
        "",
        "[startup]",
        "# lite = the administrator ALONE (it decides who else is needed).",
        "# heavy = every non-retired card.",
        f'mode = "{answers.mode}"',
        "",
        "[hibernate]",
        "# May the administrator's stop decline to wake it? It can only ever go",
        "# quiet when nothing is urgent AND there is nothing to dispatch.",
        f"enabled = {'true' if answers.hibernate else 'false'}",
        "# Read waiting reports after at most this much quiet. 0 = only when",
        "# something pushes (a tend alert, an inbox, a dispatch).",
        f"max_quiet_minutes = {answers.max_quiet_minutes}",
        "",
    ]
    return "\n".join(lines)
