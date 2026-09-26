"""jobs — declared scheduled and event-driven work, evaluated by the tend pass.

WHY THIS EXISTS. Every recurring job used to be one of two things: a sweep
hard-coded into tend, or a host cron/launchd/session cron outside st. The second
kind failed SILENTLY, in every way a cron can: a bare `st` that a unit could not
exec (PATH), a send with no SHANTY_ROOT that nothing journaled, a clone nobody
refreshed, a failure nobody retried. And the patrols an agent ran on a harness
session cron died with the session and expired after a week. A job declared
HERE is evaluated by a pass that already runs every five minutes, already has
the root, already logs, and already survives a broken sweep.

NO RESIDENT SCHEDULER, deliberately (docs/features.md). Nothing here waits,
sleeps or listens. A pass asks each job "are you due?", runs what is, records
what happened, and exits with the rest of tend. Every trigger is a PULL with a
cursor, so a pass that did not run is late, never lost:

    cron   "43 8 * * *"   host-local wall clock; up to one tend interval late
    every  "6h"           since the last firing that finished
    on     <source>       new events past this job's cursor
    when   "<shell>"      alone: fires when the check turns true. Beside another
                          trigger: a guard that DEFERS a firing, never cancels it
    manual                only `st work jobs run`

One TOML per job in <root>/jobs/*.toml, state in <root>/jobs/.state/<name>.json,
one ledger row per attempt in <root>/logs/jobs.jsonl. docs/jobs.md is the
reader's version of this docstring.

CODE OWNS THE MACHINERY, AGENTS DECIDE. The three actions are the three things
st already does — message an agent (`st inbox`), hand an agent work (`st task`
+ `st go`), run a command — and the first two go through the CLI's own handlers,
injected by cli.py, so a job cannot deliver more loosely than a person typing
the same command would.
"""
from __future__ import annotations

import fcntl
import fnmatch
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

JOBS_DIR = "jobs"
STATE_DIR = ".state"
LEDGER = "jobs.jsonl"

TRIGGER_KINDS = ("cron", "every", "on", "manual")
SOURCES = ("bead.created", "bead.closed", "gh.pr.opened", "gh.issue.opened",
           "quipu.tx")
# Which `match` keys each source understands. A key a source does not read is a
# REFUSAL at load, not a silently ignored filter: `authr_not = "scbrown"` that
# matched every PR would dispatch triage for the owner's own work, forever.
MATCH_KEYS = {
    "bead.created": {"title", "label", "assignee"},
    "bead.closed": {"title", "label", "assignee"},
    "gh.pr.opened": {"title", "author", "author_not"},
    "gh.issue.opened": {"title", "author", "author_not"},
    "quipu.tx": {"source", "actor"},
}
TOP_KEYS = {"name", "description", "enabled", "host", "timeout", "catch_up",
            "trigger", "action", "retry"}
TRIGGER_KEYS = {"cron", "every", "on", "manual", "when", "repo", "match"}
ACTION_KEYS = {"inbox", "message", "durable", "dispatch", "title", "body",
               "quipu_node", "no_graph_context", "labels", "priority", "exec"}

# A slot seen more than this late was MISSED, not merely late: tend runs every
# five minutes, so an on-time slot is always seen within one interval. Two
# intervals of slack keeps a slow pass from turning into a "missed" verdict.
LATE_GRACE_S = 600
# How far back a cron search looks. A host asleep for longer than this catches
# up the latest slot inside the window, which is the one catch_up promises.
CRON_LOOKBACK_S = 35 * 86400
WHEN_TIMEOUT_S = 30
GH_TIMEOUT_S = 30
GH_LIMIT = 100
QUIPU_PAGE = 1000
# Seeding a quipu cursor pages to the head; a graph with 150k transactions must
# not turn one tend pass into 150 requests, so seeding resumes across passes.
QUIPU_SEED_PAGES = 20
HISTORY_KEEP = 20

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


class JobError(ValueError):
    """A job file that cannot be used, named by FILE and FIELD. The reader of
    this is someone who typed a TOML file five minutes ago; "invalid job" would
    send them back to read the whole thing."""

    def __init__(self, path, field_name: str, msg: str):
        self.path, self.field = Path(path), field_name
        super().__init__(f"{Path(path).name}: {field_name}: {msg}")


class RenderError(ValueError):
    """A template named a field the event does not carry."""


# --- durations --------------------------------------------------------------

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days)?\s*$")
_UNIT_S = {None: 1, "s": 1, "sec": 1, "secs": 1, "m": 60, "min": 60, "mins": 60,
           "h": 3600, "hr": 3600, "hrs": 3600, "d": 86400, "day": 86400, "days": 86400}


def parse_duration(value) -> float:
    """Seconds, from 300 / "300" / "90s" / "5m" / "5min" / "6h" / "1d".

    `5min` is systemd's spelling and `st fleet tend --interval` has always taken
    it, so the launchd plist reads the same string rather than making the Mac
    operator learn a second one."""
    if isinstance(value, bool):
        raise ValueError(f"{value!r} is not a duration")
    if isinstance(value, (int, float)):
        if value <= 0:
            raise ValueError(f"{value!r} is not a positive duration")
        return float(value)
    m = _DURATION.match(str(value))
    if not m or float(m.group(1)) <= 0:
        raise ValueError(f"{value!r} is not a duration (e.g. 90s, 5m, 6h, 1d)")
    return float(m.group(1)) * _UNIT_S[m.group(2)]


# --- cron -------------------------------------------------------------------

_DOW_NAMES = {n: i for i, n in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
_MON_NAMES = {n: i + 1 for i, n in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}


@dataclass(frozen=True)
class Cron:
    """A five-field cron expression, matched in HOST-LOCAL time.

    Written here rather than taken from a library because the project declares
    zero runtime dependencies (AGENTS.md), and the standard five fields are small
    enough to own. Supported: `*`, `*/n`, `a`, `a-b`, `a-b/n`, lists of those,
    and three-letter day/month names. Not supported, and refused rather than
    guessed at: `@daily`-style macros, seconds, `L`/`W`/`#`.

    Day-of-month and day-of-week follow Vixie cron: when BOTH are restricted a
    day matches if EITHER does. That rule surprises people, and implementing the
    intuitive AND instead would surprise everyone who ever read a crontab.
    """
    expr: str
    minute: frozenset
    hour: frozenset
    dom: frozenset
    month: frozenset
    dow: frozenset
    dom_star: bool
    dow_star: bool

    @classmethod
    def parse(cls, expr: str) -> "Cron":
        parts = str(expr).split()
        if len(parts) != 5:
            raise ValueError(f"{expr!r} has {len(parts)} field(s); cron wants 5 "
                             f"(minute hour day-of-month month day-of-week)")
        spec = (("minute", 0, 59, {}), ("hour", 0, 23, {}), ("day-of-month", 1, 31, {}),
                ("month", 1, 12, _MON_NAMES), ("day-of-week", 0, 7, _DOW_NAMES))
        sets = [_cron_field(p, *s) for p, s in zip(parts, spec)]
        # 7 is Sunday too, as in every cron that accepts it.
        dow = frozenset(0 if d == 7 else d for d in sets[4])
        return cls(expr=" ".join(parts), minute=sets[0], hour=sets[1], dom=sets[2],
                   month=sets[3], dow=dow, dom_star=parts[2] == "*",
                   dow_star=parts[4] == "*")

    def day_matches(self, t: datetime) -> bool:
        if t.month not in self.month:
            return False
        dom_ok = t.day in self.dom
        dow_ok = (t.isoweekday() % 7) in self.dow
        if self.dom_star or self.dow_star:
            return dom_ok and dow_ok
        return dom_ok or dow_ok

    def matches(self, t: datetime) -> bool:
        return (t.minute in self.minute and t.hour in self.hour
                and self.day_matches(t))

    def latest(self, after: float, upto: float) -> float | None:
        """The latest slot in (after, upto], as an epoch, or None.

        Walks forward from `after` skipping whole days and hours that cannot
        match, so a laptop that slept for a week costs a few hundred steps and
        not ten thousand."""
        after = max(after, upto - CRON_LOOKBACK_S)
        t = datetime.fromtimestamp(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = datetime.fromtimestamp(upto)
        found = None
        while t <= end:
            if not self.day_matches(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if t.hour not in self.hour:
                t = (t + timedelta(hours=1)).replace(minute=0)
                continue
            if t.minute in self.minute:
                found = t
            t += timedelta(minutes=1)
        return found.timestamp() if found else None

    def next_after(self, now: float, horizon_days: int = 366) -> float | None:
        t = datetime.fromtimestamp(now).replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = t + timedelta(days=horizon_days)
        while t <= end:
            if not self.day_matches(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if t.hour not in self.hour:
                t = (t + timedelta(hours=1)).replace(minute=0)
                continue
            if t.minute in self.minute:
                return t.timestamp()
            t += timedelta(minutes=1)
        return None


def _cron_value(tok: str, names: dict, what: str) -> int:
    tok = tok.lower()
    if tok in names:
        return names[tok]
    if not tok.isdigit():
        raise ValueError(f"{tok!r} is not a number in the {what} field")
    return int(tok)


def _cron_field(text: str, what: str, lo: int, hi: int, names: dict) -> frozenset:
    out: set[int] = set()
    for part in text.split(","):
        if not part:
            raise ValueError(f"empty list item in the {what} field")
        rng, _, step_s = part.partition("/")
        step = 1
        if step_s:
            if not step_s.isdigit() or int(step_s) == 0:
                raise ValueError(f"{part!r}: step must be a positive number")
            step = int(step_s)
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a_s, b_s = rng.split("-", 1)
            a, b = _cron_value(a_s, names, what), _cron_value(b_s, names, what)
        else:
            a = _cron_value(rng, names, what)
            b = hi if step_s else a
        if not (lo <= a <= hi and lo <= b <= hi):
            raise ValueError(f"{part!r} is outside {lo}-{hi} in the {what} field")
        if a > b:
            raise ValueError(f"{part!r}: range runs backwards in the {what} field")
        out.update(range(a, b + 1, step))
    return frozenset(out)


# --- the job file -----------------------------------------------------------

@dataclass(frozen=True)
class Trigger:
    kind: str                         # cron | every | on | when | manual
    cron: Cron | None = None
    every_s: float | None = None
    source: str = ""
    repo: str = ""
    match: tuple = ()                 # ((key, (values...)), ...)
    when: str = ""                    # a guard beside another kind, or the trigger

    def describe(self) -> str:
        head = {"cron": lambda: f"cron {self.cron.expr}",
                "every": lambda: f"every {_human_s(self.every_s)}",
                "on": lambda: f"on {self.source}" + (f" {self.repo}" if self.repo else ""),
                "when": lambda: f"when {self.when!r}",
                "manual": lambda: "manual"}[self.kind]()
        if self.when and self.kind != "when":
            head += f" (when {self.when!r})"
        return head


@dataclass(frozen=True)
class Action:
    kind: str                         # inbox | dispatch | exec
    to: str = ""                      # agent, or role:<name> for dispatch
    message: str = ""
    durable: bool = False
    title: str = ""
    body: str = ""
    quipu_nodes: tuple = ()
    no_graph_context: str = ""
    labels: str = ""
    priority: int | None = None
    command: tuple = ()               # argv; a string becomes ("/bin/sh", "-c", s)

    def describe(self) -> str:
        if self.kind == "inbox":
            return f"inbox {self.to}" + (" -d" if self.durable else "")
        if self.kind == "dispatch":
            return f"dispatch -> {self.to}"
        return "exec " + (self.command[-1] if self.command[:2] == ("/bin/sh", "-c")
                          else shlex.join(self.command))


@dataclass(frozen=True)
class Job:
    name: str
    path: Path
    trigger: Trigger
    action: Action
    description: str = ""
    enabled: bool = True
    hosts: tuple = ()
    timeout_s: float = 600.0
    retry_max: int = 2
    backoff_s: float = 300.0
    catch_up: bool = True


def _str_list(path, fld: str, value) -> tuple:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise JobError(path, fld, "must be a string or a list of strings")


def _placeholders_ok(path, fld: str, text: str, events: bool) -> None:
    for name in _PLACEHOLDER.findall(text):
        if name in ("date", "time", "job"):
            continue
        if name.startswith("event.") and len(name) > len("event."):
            if not events:
                raise JobError(path, fld, f"{{{{{name}}}}} needs an `on` trigger — "
                               f"only an event has fields")
            continue
        raise JobError(path, fld, f"unknown placeholder {{{{{name}}}}} "
                       f"(use date, time, job or event.<field>)")


def parse(path, data: dict) -> Job:
    """One job from one parsed TOML table. Raises JobError naming the field."""
    path = Path(path)
    unknown = sorted(set(data) - TOP_KEYS)
    if unknown:
        raise JobError(path, unknown[0], f"unknown key (expected one of "
                       f"{', '.join(sorted(TOP_KEYS))})")
    stem = path.stem
    if not _NAME.match(stem):
        raise JobError(path, "name", f"file name {stem!r} must be lowercase "
                       f"letters, digits, '-' or '_'")
    name = data.get("name", stem)
    if name != stem:
        # One name per job. A job whose file and `name` disagree has two
        # identities, and its state file and its ledger rows pick different ones.
        raise JobError(path, "name", f"{name!r} does not match the file name {stem!r}")
    enabled = data.get("enabled", True)
    catch_up = data.get("catch_up", True)
    for fld, v in (("enabled", enabled), ("catch_up", catch_up)):
        if not isinstance(v, bool):
            raise JobError(path, fld, "must be true or false")
    hosts = _str_list(path, "host", data["host"]) if "host" in data else ()
    try:
        timeout_s = parse_duration(data.get("timeout", 600))
    except ValueError as e:
        raise JobError(path, "timeout", str(e)) from None
    retry = data.get("retry", {})
    if not isinstance(retry, dict) or set(retry) - {"max", "backoff"}:
        raise JobError(path, "retry", "must be a table with `max` and/or `backoff`")
    retry_max = retry.get("max", 2)
    if isinstance(retry_max, bool) or not isinstance(retry_max, int) or retry_max < 0:
        raise JobError(path, "retry.max", "must be a whole number >= 0")
    try:
        backoff_s = parse_duration(retry.get("backoff", "5m"))
    except ValueError as e:
        raise JobError(path, "retry.backoff", str(e)) from None
    trigger = _parse_trigger(path, data.get("trigger"))
    action = _parse_action(path, data.get("action"), events=trigger.kind == "on")
    desc = data.get("description", "")
    if not isinstance(desc, str):
        raise JobError(path, "description", "must be a string")
    return Job(name=name, path=path, trigger=trigger, action=action,
               description=desc, enabled=enabled, hosts=hosts,
               timeout_s=timeout_s, retry_max=retry_max, backoff_s=backoff_s,
               catch_up=catch_up)


def _parse_trigger(path, t) -> Trigger:
    if not isinstance(t, dict):
        raise JobError(path, "trigger", "missing — a job needs a [trigger] table")
    unknown = sorted(set(t) - TRIGGER_KEYS)
    if unknown:
        raise JobError(path, f"trigger.{unknown[0]}", "unknown key")
    kinds = [k for k in TRIGGER_KINDS if k in t]
    when = t.get("when", "")
    if not isinstance(when, str):
        raise JobError(path, "trigger.when", "must be a shell command string")
    if len(kinds) > 1:
        raise JobError(path, "trigger", f"has {' and '.join(kinds)}; pick one "
                       f"(`when` may sit beside any of them as a guard)")
    if not kinds and not when:
        raise JobError(path, "trigger", "needs one of cron, every, on, when or manual")
    kind = kinds[0] if kinds else "when"
    if kind == "manual":
        if t["manual"] is not True:
            raise JobError(path, "trigger.manual", "must be true")
        if when:
            raise JobError(path, "trigger.when", "a manual job has no trigger to guard")
    if kind != "on" and ("repo" in t or "match" in t):
        bad = "repo" if "repo" in t else "match"
        raise JobError(path, f"trigger.{bad}", "only an `on` trigger reads it")
    if kind == "cron":
        try:
            return Trigger("cron", cron=Cron.parse(t["cron"]), when=when)
        except ValueError as e:
            raise JobError(path, "trigger.cron", str(e)) from None
    if kind == "every":
        try:
            return Trigger("every", every_s=parse_duration(t["every"]), when=when)
        except ValueError as e:
            raise JobError(path, "trigger.every", str(e)) from None
    if kind == "on":
        source = t["on"]
        if source not in SOURCES:
            raise JobError(path, "trigger.on", f"{source!r} is not a source "
                           f"({', '.join(SOURCES)})")
        repo = t.get("repo", "")
        if source.startswith("gh."):
            if not isinstance(repo, str) or not re.match(r"^[\w.-]+/[\w.-]+$", repo):
                raise JobError(path, "trigger.repo", f"{source} needs repo = \"owner/name\"")
        elif repo:
            raise JobError(path, "trigger.repo", f"{source} does not read a repo")
        match = t.get("match", {})
        if not isinstance(match, dict):
            raise JobError(path, "trigger.match", "must be a table")
        bad = sorted(set(match) - MATCH_KEYS[source])
        if bad:
            raise JobError(path, f"trigger.match.{bad[0]}", f"{source} matches on "
                           f"{', '.join(sorted(MATCH_KEYS[source]))} only")
        pairs = tuple(sorted((k, _str_list(path, f"trigger.match.{k}", v))
                             for k, v in match.items()))
        return Trigger("on", source=source, repo=repo, match=pairs, when=when)
    return Trigger(kind, when=when)


def _parse_action(path, a, *, events: bool) -> Action:
    if not isinstance(a, dict):
        raise JobError(path, "action", "missing — a job needs an [action] table")
    unknown = sorted(set(a) - ACTION_KEYS)
    if unknown:
        raise JobError(path, f"action.{unknown[0]}", "unknown key")
    kinds = [k for k in ("inbox", "dispatch", "exec") if k in a]
    if len(kinds) != 1:
        raise JobError(path, "action", "needs exactly one of inbox, dispatch or exec"
                       + (f" (has {' and '.join(kinds)})" if kinds else ""))
    kind = kinds[0]
    for k, v in a.items():
        if k in ("durable",) and not isinstance(v, bool):
            raise JobError(path, f"action.{k}", "must be true or false")
    if kind == "inbox":
        to, msg = a["inbox"], a.get("message", "")
        if not isinstance(to, str) or not to:
            raise JobError(path, "action.inbox", "must name an agent")
        if not isinstance(msg, str) or not msg.strip():
            raise JobError(path, "action.message", "an inbox action needs a message")
        _placeholders_ok(path, "action.message", msg, events)
        return Action("inbox", to=to, message=msg, durable=a.get("durable", False))
    if kind == "dispatch":
        to, title, body = a["dispatch"], a.get("title", ""), a.get("body", "")
        if not isinstance(to, str) or not to or to == "role:":
            raise JobError(path, "action.dispatch", "must name an agent, or role:<name>")
        if not isinstance(title, str) or not title.strip():
            raise JobError(path, "action.title", "a dispatch needs a title for the work item")
        if not isinstance(body, str):
            raise JobError(path, "action.body", "must be a string")
        nodes = _str_list(path, "action.quipu_node", a["quipu_node"]) if "quipu_node" in a else ()
        reason = a.get("no_graph_context", "")
        if not isinstance(reason, str):
            raise JobError(path, "action.no_graph_context", "must be a string")
        # THE SAME RULE `st go` ENFORCES, checked at load instead of at 08:43.
        # A dispatch cites a graph node or says why it has none; a job file is
        # a dispatch written in advance, so it owes the same answer in advance.
        if not nodes and not reason.strip():
            raise JobError(path, "action.quipu_node", "a dispatch needs quipu_node, or "
                           "no_graph_context = \"<why there is none>\" (the same "
                           "rule as `st go`)")
        if nodes and reason:
            raise JobError(path, "action.no_graph_context", "give quipu_node OR "
                           "no_graph_context, not both")
        labels = a.get("labels", "")
        if isinstance(labels, list):
            labels = ",".join(_str_list(path, "action.labels", labels))
        if not isinstance(labels, str):
            raise JobError(path, "action.labels", "must be a string or a list of strings")
        prio = a.get("priority")
        if prio is not None and (isinstance(prio, bool) or not isinstance(prio, int)
                                 or not 0 <= prio <= 4):
            raise JobError(path, "action.priority", "must be 0-4")
        for fld, text in (("action.title", title), ("action.body", body)):
            _placeholders_ok(path, fld, text, events)
        return Action("dispatch", to=to, title=title, body=body, quipu_nodes=nodes,
                      no_graph_context=reason, labels=labels, priority=prio)
    cmd = a["exec"]
    if isinstance(cmd, str) and cmd.strip():
        command = ("/bin/sh", "-c", cmd)
    elif isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd):
        command = tuple(cmd)
    else:
        raise JobError(path, "action.exec", "must be a shell string or a non-empty argv list")
    for part in command:
        _placeholders_ok(path, "action.exec", part, events)
    return Action("exec", command=command)


def jobs_dir(root) -> Path:
    return Path(root) / JOBS_DIR


def load(root) -> tuple[list[Job], list[JobError]]:
    """Every job under <root>/jobs, and every file that could not be one.

    NEVER RAISES for a bad file. One typo must not take down the other jobs, or
    the tend pass they run in — the errors are returned, and the caller prints
    them where an operator already looks."""
    jobs, errors = [], []
    d = jobs_dir(root)
    if not d.is_dir():
        return jobs, errors
    for p in sorted(d.glob("*.toml")):
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as e:
            errors.append(JobError(p, "file", f"unreadable ({e})"))
            continue
        except tomllib.TOMLDecodeError as e:
            errors.append(JobError(p, "toml", str(e)))
            continue
        try:
            jobs.append(parse(p, data))
        except JobError as e:
            errors.append(e)
    return jobs, errors


def runs_here(job: Job, host: str | None) -> tuple[bool, str]:
    """Is this host one the job is scoped to? No `host` = every host that has the
    file. A job that names a host on a deployment that never said which host it
    is does NOT run: "probably us" is how one job fires twice on two machines."""
    if not job.hosts:
        return True, ""
    if not host:
        return False, (f"scoped to {', '.join(job.hosts)}, and this deployment "
                       f"declares no [host] name")
    if host not in job.hosts:
        return False, f"scoped to {', '.join(job.hosts)}, not {host}"
    return True, ""


# --- templates --------------------------------------------------------------

def render(text: str, *, job: str, event: dict | None, now: float) -> str:
    stamp = datetime.fromtimestamp(now)

    def sub(m):
        key = m.group(1)
        if key == "date":
            return stamp.strftime("%Y-%m-%d")
        if key == "time":
            return stamp.strftime("%H:%M")
        if key == "job":
            return job
        name = key[len("event."):]
        if event is None or name not in event:
            have = ", ".join(sorted(event or {})) or "none"
            raise RenderError(f"{{{{{key}}}}}: the event has no {name!r} (it has {have})")
        return str(event[name])
    return _PLACEHOLDER.sub(sub, text)


# --- state ------------------------------------------------------------------

def state_path(root, name: str) -> Path:
    return jobs_dir(root) / STATE_DIR / f"{name}.json"


def read_state(root, name: str) -> dict:
    try:
        data = json.loads(state_path(root, name).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(root, name: str, data: dict) -> None:
    from .files import write_json_atomic
    p = state_path(root, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(p, data)


def append_ledger(root, row: dict) -> None:
    """One row per ATTEMPT, beside feed-audit and graph-adoption in <root>/logs.
    Best-effort: a full disk must not turn a successful run into a failed one."""
    try:
        d = Path(root) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


def read_ledger(root, name: str | None = None, limit: int = 20) -> list[dict]:
    rows = []
    try:
        text = (Path(root) / "logs" / LEDGER).read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and (name is None or row.get("job") == name):
            rows.append(row)
    return rows[-limit:]


# --- event sources ----------------------------------------------------------

def _run(argv, timeout, **kw):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kw)


class Sources:
    """Where `on` triggers read from. Every reader RAISES when it could not look
    — the cursor only moves on a real answer, so "gh was down" is a late
    firing, never an empty one that skips past what happened meanwhile.

    One instance per pass: the bead list is read once however many jobs watch
    it, because `br list --all` against a contended store is the slow part of a
    pass (aegis-qwadc measured it at 18s)."""

    def __init__(self, root=None, *, run=_run, bead_rows=None, quipu=None):
        self._root = root
        self._run = run
        self._bead_rows = bead_rows
        self._quipu = quipu
        self._beads_cache = None

    def gh(self, source: str, repo: str) -> list[dict]:
        noun = "pr" if source == "gh.pr.opened" else "issue"
        argv = ["gh", noun, "list", "--repo", repo, "--state", "all",
                "--limit", str(GH_LIMIT), "--json", "number,title,author,url"]
        try:
            r = self._run(argv, GH_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"gh {noun} list {repo}: {type(e).__name__}: {e}") from e
        if r.returncode != 0:
            raise RuntimeError(f"gh {noun} list {repo} exited {r.returncode}: "
                               f"{(r.stderr or '').strip()[:160]}")
        try:
            rows = json.loads(r.stdout or "[]")
        except ValueError as e:
            raise RuntimeError(f"gh {noun} list {repo}: unreadable JSON ({e})") from e
        out = []
        for x in rows if isinstance(rows, list) else []:
            if not isinstance(x, dict) or not isinstance(x.get("number"), int):
                continue
            author = x.get("author") or {}
            out.append({"number": x["number"], "id": x["number"],
                        "title": x.get("title", ""), "url": x.get("url", ""),
                        "author": author.get("login", "") if isinstance(author, dict) else str(author),
                        "repo": repo, "kind": noun})
        return out

    def beads(self) -> list[dict]:
        if self._bead_rows is None:
            raise RuntimeError("no tracker wired for bead events")
        if self._beads_cache is None:
            rows = []
            for x in self._bead_rows():
                labels = x.get("labels") or []
                if isinstance(labels, str):
                    labels = [s for s in labels.split(",") if s]
                rows.append({"id": str(x.get("id", "")), "title": x.get("title", "") or "",
                             "status": x.get("status", "") or "",
                             "assignee": x.get("assignee", "") or "",
                             "labels": ",".join(labels)})
            self._beads_cache = [r for r in rows if r["id"]]
        return self._beads_cache

    def quipu_since(self, cursor: int, limit: int = QUIPU_PAGE) -> list[dict]:
        if self._quipu is None:
            from .quipu_events import QuipuEvents
            self._quipu = QuipuEvents(root=self._root)
        from .protocols import EventsUnavailable
        try:
            evs = self._quipu.transactions_since(cursor, limit=limit)
        except EventsUnavailable as e:
            raise RuntimeError(str(e)) from e
        return [{"id": e.id, "actor": e.actor or "", "source": e.source or "",
                 "timestamp": e.timestamp or ""} for e in evs]


def _matches(trigger: Trigger, event: dict) -> bool:
    for key, values in trigger.match:
        if key == "author_not":
            if event.get("author", "") in values:
                return False
            continue
        if key == "label":
            have = set(filter(None, str(event.get("labels", "")).split(",")))
            if not have.intersection(values):
                return False
            continue
        if not any(fnmatch.fnmatchcase(str(event.get(key, "")), v) for v in values):
            return False
    return True


# --- due-ness ---------------------------------------------------------------

@dataclass
class Decision:
    """What one evaluation found. `fires` are new firings to enqueue; `observe`
    is state the pass learned whether or not anything fired (a seeded cursor, a
    when-check that read false, a slot that was missed for good) — kept apart so
    `st work jobs check` can show both and persist neither."""
    fires: list = field(default_factory=list)
    why: str = ""
    observe: dict = field(default_factory=dict)


def _when_true(job: Job, root, run=_run) -> tuple[bool, str]:
    env = dict(os.environ, SHANTY_ROOT=str(root), ST_JOB=job.name)
    try:
        r = run(["/bin/sh", "-c", job.trigger.when], WHEN_TIMEOUT_S, env=env,
                cwd=str(root))
    except subprocess.TimeoutExpired:
        return False, f"when-check timed out after {WHEN_TIMEOUT_S}s"
    except OSError as e:
        return False, f"when-check could not run: {e}"
    return r.returncode == 0, f"when-check exited {r.returncode}"


def evaluate(job: Job, st: dict, now: float, *, root, sources: Sources,
             run=_run) -> Decision:
    """Is `job` due at `now`, given its state? Reads the world (events, the
    when-check); writes nothing. The caller decides whether to persist."""
    t = job.trigger
    if t.kind == "manual":
        return Decision(why="manual — fires only on `st work jobs run`")
    pending = st.get("pending") or []
    if t.kind in ("cron", "every", "when") and pending:
        # One firing of a time trigger at a time. A slot that arrives while the
        # last one is still retrying is the SAME job re-run, and queueing it
        # would turn one outage into a burst of runs when it clears.
        return Decision(why=f"a firing is still pending ({pending[0].get('reason', '')})")
    since = float(st.get("since") or now)

    if t.kind == "when":
        ok, detail = _when_true(job, root, run)
        was = bool(st.get("when_last"))
        if ok and not was:
            return Decision(fires=[{"reason": "when-check turned true"}],
                            observe={"when_last": True})
        return Decision(why=("when-check still true (fires on the next false->true)"
                             if ok else detail),
                        observe={"when_last": ok} if ok != was else {})

    fires: list[dict] = []
    observe: dict = {}
    why = ""
    if t.kind == "cron":
        last_slot = float(st.get("last_slot") or since)
        slot = t.cron.latest(last_slot, now)
        if slot is None:
            nxt = t.cron.next_after(now)
            return Decision(why="not due" + (f" (next {_stamp(nxt)})" if nxt else ""))
        late = now - slot
        if late > LATE_GRACE_S and not job.catch_up:
            return Decision(why=f"missed the {_stamp(slot)} slot by {_human_s(late)} "
                                f"and catch_up = false — skipped",
                            observe={"last_slot": slot})
        reason = f"cron {t.cron.expr} @ {_stamp(slot)}"
        if late > LATE_GRACE_S:
            reason += f" (caught up {_human_s(late)} late)"
        fires = [{"reason": reason, "commit": {"last_slot": slot}}]
    elif t.kind == "every":
        last = st.get("last_done")
        if last is not None and now < float(last) + t.every_s:
            return Decision(why=f"not due (next {_stamp(float(last) + t.every_s)})")
        fires = [{"reason": f"every {_human_s(t.every_s)}"
                            + (" (first run)" if last is None else "")}]
    else:
        fires, observe, why = _poll(job, st, sources)
        if not fires:
            return Decision(why=why, observe=observe)

    if t.when:
        ok, detail = _when_true(job, root, run)
        if not ok:
            # A GUARD DEFERS, IT NEVER CANCELS. Nothing is consumed — not the
            # slot, not the cursor — so the firing happens on the first pass the
            # guard allows (subject to catch_up for cron).
            return Decision(why=f"due, held by its guard: {detail}")
    # Commit what the firing consumes at ENQUEUE time: the queue is persisted in
    # the same write, so a crash after this cannot lose the firing, and a crash
    # before it cannot double it.
    for f in fires:
        observe.update(f.pop("commit", {}))
    return Decision(fires=fires, why=why, observe=observe)


def _poll(job: Job, st: dict, sources: Sources) -> tuple[list, dict, str]:
    t = job.trigger
    seeded = bool(st.get("seeded"))
    if t.source.startswith("gh."):
        rows = sources.gh(t.source, t.repo)
        top = max((r["number"] for r in rows), default=0)
        cursor = int(st.get("cursor") or 0)
        if not seeded:
            # FIRST SIGHT SEEDS, IT DOES NOT FIRE. A new job on a repo with 80
            # open PRs means "tell me about the next one", not "dispatch 80
            # triage items at once".
            return [], {"cursor": top, "seeded": True}, f"seeded at #{top}"
        new = sorted((r for r in rows if r["number"] > cursor), key=lambda r: r["number"])
        fires = [{"reason": f"{t.source} #{r['number']} by {r['author'] or '?'}", "event": r}
                 for r in new if _matches(t, r)]
        observe = {"cursor": max(cursor, top)} if top > cursor else {}
        skipped = len(new) - len(fires)
        return fires, observe, ("no new events" if not new else
                                f"{skipped} new event(s), none matched")
    if t.source.startswith("bead."):
        rows = sources.beads()
        want_closed = t.source == "bead.closed"
        ids = {r["id"] for r in rows if not want_closed or r["status"] == "closed"}
        seen = set(st.get("seen") or [])
        if not seeded:
            return [], {"seen": sorted(ids), "seeded": True}, f"seeded with {len(ids)} bead(s)"
        new = [r for r in rows if r["id"] in ids - seen]
        fires = [{"reason": f"{t.source} {r['id']}", "event": r}
                 for r in sorted(new, key=lambda r: r["id"]) if _matches(t, r)]
        # Remember only ids that still exist, so the set cannot grow without
        # bound on a store that archives.
        observe = {"seen": sorted(ids)} if ids != seen else {}
        return fires, observe, ("no new events" if not new else
                                f"{len(new)} new event(s), none matched")
    # quipu.tx
    cursor = int(st.get("cursor") or 0)
    if not seeded:
        for _ in range(QUIPU_SEED_PAGES):
            page = sources.quipu_since(cursor)
            if page:
                cursor = max(cursor, max(e["id"] for e in page))
            if len(page) < QUIPU_PAGE:
                return [], {"cursor": cursor, "seeded": True}, f"seeded at tx {cursor}"
        return [], {"cursor": cursor}, f"seeding: reached tx {cursor}, continuing next pass"
    page = sources.quipu_since(cursor)
    if not page:
        return [], {}, "no new events"
    top = max(e["id"] for e in page)
    fires = [{"reason": f"quipu.tx {e['id']} ({e['source'] or '?'})", "event": e}
             for e in sorted(page, key=lambda e: e["id"]) if _matches(t, e)]
    return fires, {"cursor": top}, f"{len(page)} new tx, none matched"


# --- running ----------------------------------------------------------------

@dataclass
class Outcome:
    ok: bool
    detail: str = ""
    running: bool = False        # an exec that was STARTED, not finished


def run_exec(argv, timeout_s: float, *, log_path=None, env=None, cwd=None) -> dict:
    """Run a command to completion under a timeout. The ONE implementation of
    exec: `st work jobs run` calls it in the foreground, and a tend pass calls it
    in a detached child (`python -m shantytown.jobs _exec`) so a twenty-minute
    job cannot hold a supervision pass for twenty minutes (aegis-qwadc)."""
    start = time.time()
    out = open(log_path, "a", encoding="utf-8") if log_path else subprocess.DEVNULL
    try:
        try:
            proc = subprocess.Popen(list(argv), stdout=out, stderr=subprocess.STDOUT,
                                    env=env, cwd=cwd, start_new_session=True)
        except OSError as e:
            return {"ok": False, "rc": None, "start": start, "end": time.time(),
                    "error": f"could not start: {e}"}
        try:
            rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            # The whole GROUP: a shell string's children must not outlive it.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
            return {"ok": False, "rc": None, "start": start, "end": time.time(),
                    "error": f"timed out after {_human_s(timeout_s)}"}
        return {"ok": rc == 0, "rc": rc, "start": start, "end": time.time(),
                "error": "" if rc == 0 else f"exited {rc}"}
    finally:
        if log_path:
            out.close()


def _spawn_detached(argv, timeout_s, result_path: Path, log_path: Path, env, cwd) -> int:
    child = [sys.executable, "-m", "shantytown.jobs", "_exec", str(result_path),
             str(timeout_s), str(log_path), "--", *argv]
    proc = subprocess.Popen(child, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env, cwd=cwd,
                            start_new_session=True)
    return proc.pid


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


class Runner:
    """Evaluate and run jobs for one root. Every outside effect is injected —
    the clock, the host, the two CLI-backed actions, the escalation, the event
    sources, the process spawner — so a test drives a whole firing, retry and
    give-up with no tmux, no tracker, no gh and no wall clock.

    actions: {"inbox": fn, "dispatch": fn}, each fn(job, fire, rendered) ->
    Outcome. `fire["memo"]` persists across retries, which is how a dispatch
    that created its item and then failed to deliver it does not create a second
    item on the retry.
    """

    def __init__(self, root, *, host=None, actions=None, escalate=None,
                 sources=None, now=time.time, run=_run, spawn=_spawn_detached,
                 detach=True, log=None):
        self.root = Path(root)
        self.host = host
        self.actions = actions or {}
        self.escalate = escalate
        self.sources = sources or Sources(root)
        self.now = now
        self._run = run
        self._spawn = spawn
        self.detach = detach
        self.log = log or (lambda msg: None)

    # --- the sweep ---------------------------------------------------------

    def sweep(self) -> list[str]:
        """One pass over every job. Returns the lines worth printing — a pass
        where nothing fired says nothing. Loud about BROKEN files every pass: a
        job that silently stopped existing is the failure this module replaces."""
        jobs, errors = load(self.root)
        lines = [f"⚠ job file refused — {e}" for e in errors]
        for job in jobs:
            try:
                lines.extend(self.tick(job))
            except Exception as e:  # noqa: BLE001 — one job never breaks the others
                lines.append(f"⚠ job {job.name} CRASHED ({e!r}) — the other jobs "
                             f"ran; this one retries next pass")
        return lines

    def tick(self, job: Job) -> list[str]:
        if not job.enabled:
            return []
        here, _why = runs_here(job, self.host)
        if not here:
            return []
        with _Lock(self.root, job.name) as held:
            if not held:
                return [f"job {job.name}: another pass holds its lock — skipped"]
            st = read_state(self.root, job.name)
            now = self.now()
            st.setdefault("since", now)
            lines: list[str] = []
            try:
                d = evaluate(job, st, now, root=self.root, sources=self.sources,
                             run=self._run)
            except Exception as e:  # noqa: BLE001 — a source that could not look
                st["last_poll_error"] = f"{type(e).__name__}: {e}"[:300]
                write_state(self.root, job.name, st)
                return [f"⚠ job {job.name}: could not evaluate ({e}) — nothing "
                        f"consumed, retried next pass"]
            st.pop("last_poll_error", None)
            st.update(d.observe)
            queue = st.setdefault("pending", [])
            for f in d.fires:
                f.update(attempts=0, retry_at=0.0, queued=now)
                queue.append(f)
            write_state(self.root, job.name, st)   # enqueue is durable first
            lines += self._drain(job, st)
            write_state(self.root, job.name, st)
            return lines

    def _drain(self, job: Job, st: dict) -> list[str]:
        lines = []
        queue = st.get("pending") or []
        while queue:
            fire = queue[0]
            now = self.now()
            if fire.get("running"):
                done = self._harvest(job, fire)
                if done is None:
                    break                        # still running: serialize per job
                lines += self._finish(job, st, fire, done)
                continue
            if fire.get("retry_at", 0) > now:
                break
            outcome = self._attempt(job, fire)
            if outcome.running:
                lines.append(f"job {job.name}: started ({fire['reason']})")
                break
            lines += self._finish(job, st, fire, outcome)
        return lines

    # --- one attempt ---------------------------------------------------------

    def _rendered(self, job: Job, fire: dict, now: float) -> dict:
        a, ev = job.action, fire.get("event")
        r = lambda s: render(s, job=job.name, event=ev, now=now)  # noqa: E731
        if a.kind == "inbox":
            return {"to": a.to, "message": r(a.message), "durable": a.durable}
        if a.kind == "dispatch":
            return {"to": a.to, "title": r(a.title), "body": r(a.body),
                    "quipu_nodes": list(a.quipu_nodes),
                    "no_graph_context": a.no_graph_context,
                    "labels": a.labels, "priority": a.priority}
        return {"argv": [r(p) for p in a.command]}

    def _exec_env(self, job: Job, fire: dict) -> dict:
        # SHANTY_ROOT ALWAYS, and it is not a nicety: a job-launched `st` with
        # no root journals nothing, which is the unjournaled-cron failure this
        # module exists to retire (aegis-0681f6).
        return dict(os.environ, SHANTY_ROOT=str(self.root), ST_JOB=job.name,
                    ST_JOB_EVENT=json.dumps(fire.get("event") or {}, sort_keys=True))

    def _attempt(self, job: Job, fire: dict) -> Outcome:
        now = self.now()
        fire["attempts"] = int(fire.get("attempts", 0)) + 1
        fire["started"] = now
        try:
            rendered = self._rendered(job, fire, now)
        except RenderError as e:
            return Outcome(False, str(e))
        if job.action.kind == "exec":
            if not self.detach:
                res = run_exec(rendered["argv"], job.timeout_s, log_path=self._log_path(job),
                               env=self._exec_env(job, fire), cwd=str(self.root))
                return Outcome(res["ok"], res["error"] or "exit 0")
            result = jobs_dir(self.root) / STATE_DIR / f"{job.name}.result.json"
            try:
                result.unlink()
            except OSError:
                pass
            try:
                pid = self._spawn(rendered["argv"], job.timeout_s, result,
                                  self._log_path(job), self._exec_env(job, fire),
                                  str(self.root))
            except OSError as e:
                return Outcome(False, f"could not start the runner: {e}")
            fire["running"] = {"pid": pid, "result": str(result), "at": now}
            return Outcome(False, running=True)
        fn = self.actions.get(job.action.kind)
        if fn is None:
            return Outcome(False, f"no {job.action.kind} action is wired here")
        try:
            return fn(job, fire, rendered)
        except Exception as e:  # noqa: BLE001 — an action error is a failed attempt
            return Outcome(False, f"{type(e).__name__}: {e}")

    def _log_path(self, job: Job) -> Path:
        p = jobs_dir(self.root) / STATE_DIR / f"{job.name}.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _harvest(self, job: Job, fire: dict) -> Outcome | None:
        info = fire["running"]
        try:
            res = json.loads(Path(info["result"]).read_text())
        except (OSError, ValueError):
            res = None
        if res is None:
            if _alive(info.get("pid")):
                return None
            # Re-read once: the child may have written and exited between the
            # two looks.
            try:
                res = json.loads(Path(info["result"]).read_text())
            except (OSError, ValueError):
                fire.pop("running", None)
                return Outcome(False, "the exec runner exited without a result "
                                      "(killed, or the host restarted)")
        fire.pop("running", None)
        return Outcome(bool(res.get("ok")), res.get("error") or "exit 0")

    def _finish(self, job: Job, st: dict, fire: dict, outcome: Outcome) -> list[str]:
        now = self.now()
        attempt = int(fire.get("attempts", 1))
        row = {"job": job.name, "host": self.host or "", "reason": fire.get("reason", ""),
               "event": (fire.get("event") or {}).get("id"), "attempt": attempt,
               "start": fire.get("started", now), "end": now, "ok": outcome.ok,
               "error": "" if outcome.ok else outcome.detail,
               "detail": outcome.detail if outcome.ok else ""}
        st["last_run"] = {k: row[k] for k in ("start", "end", "ok", "error", "reason", "attempt")}
        queue = st["pending"]
        if outcome.ok:
            queue.pop(0)
            st["last_ok"] = st["last_done"] = now
            row["final"] = True
            self._record(st, row)
            return [f"job {job.name}: ok ({fire.get('reason', '')}) — {outcome.detail}"]
        if attempt <= job.retry_max:
            wait = job.backoff_s * (2 ** (attempt - 1))
            fire["retry_at"] = now + wait
            fire["last_error"] = outcome.detail
            row["retry_at"] = fire["retry_at"]
            self._record(st, row)
            return [f"⚠ job {job.name}: attempt {attempt} of {job.retry_max + 1} "
                    f"FAILED ({outcome.detail}) — retrying in {_human_s(wait)}"]
        # GIVE UP, AND SAY SO TO SOMEONE WHO CAN ACT (aegis-ntgnpa). A job that
        # retries forever hides a broken job behind optimism; one that gives up
        # quietly is the silent cron this replaces. So: stop, and message the
        # administrator once.
        queue.pop(0)
        st["last_done"] = now
        text = (f"[job {job.name}] gave up after {attempt} attempt(s): "
                f"{outcome.detail[:160]} — st work jobs history {job.name}")
        told = False
        if self.escalate is not None:
            try:
                told = bool(self.escalate(job, text))
            except Exception:  # noqa: BLE001 — escalation is best-effort
                told = False
        row.update(final=True, escalated=told)
        self._record(st, row)
        return [f"⚠ job {job.name}: GAVE UP after {attempt} attempt(s) "
                f"({outcome.detail}) — "
                + ("the administrator was told" if told else
                   "COULD NOT tell the administrator; this line is the only notice")]

    def _record(self, st: dict, row: dict) -> None:
        hist = st.setdefault("history", [])
        hist.append(row)
        del hist[:-HISTORY_KEEP]
        append_ledger(self.root, row)

    # --- the CLI's verbs ---------------------------------------------------

    def check(self, job: Job) -> tuple[str, str]:
        """(verdict, why) without writing anything. Events are READ — that is
        what "would this fire now" needs — but no cursor moves."""
        if not job.enabled:
            return "disabled", "enabled = false"
        here, why = runs_here(job, self.host)
        if not here:
            return "elsewhere", why
        st = read_state(self.root, job.name)
        now = self.now()
        st.setdefault("since", now)
        pending = st.get("pending") or []
        if pending:
            f = pending[0]
            if f.get("running"):
                return "running", f"{f.get('reason', '')} (pid {f['running'].get('pid')})"
            if f.get("retry_at", 0) > now:
                return "retrying", (f"{f.get('reason', '')}: attempt "
                                    f"{int(f.get('attempts', 0)) + 1} at "
                                    f"{_stamp(f['retry_at'])}")
            return "would fire", f.get("reason", "")
        try:
            d = evaluate(job, st, now, root=self.root, sources=self.sources, run=self._run)
        except Exception as e:  # noqa: BLE001
            return "cannot tell", f"{type(e).__name__}: {e}"
        if d.fires:
            reasons = "; ".join(f["reason"] for f in d.fires[:3])
            more = f" (+{len(d.fires) - 3} more)" if len(d.fires) > 3 else ""
            return "would fire", reasons + more
        return "not due", d.why

    def run_now(self, job: Job, *, dry_run: bool = False) -> tuple[bool, str]:
        """Fire once, now, bypassing the trigger — in the FOREGROUND, because the
        operator is watching. Recorded like any run; never retried, because the
        person who typed it is the retry."""
        now = self.now()
        fire = {"reason": "manual: st work jobs run", "attempts": 0}
        if dry_run:
            try:
                return True, json.dumps(self._rendered(job, fire, now), sort_keys=True)
            except RenderError as e:
                return False, str(e)
        with _Lock(self.root, job.name) as held:
            if not held:
                return False, "another pass holds this job's lock"
            detach, self.detach = self.detach, False
            try:
                outcome = self._attempt(job, fire)
            finally:
                self.detach = detach
            st = read_state(self.root, job.name)
            st.setdefault("since", now)
            row = {"job": job.name, "host": self.host or "", "reason": fire["reason"],
                   "event": None, "attempt": 1, "start": fire.get("started", now),
                   "end": self.now(), "ok": outcome.ok, "final": True,
                   "error": "" if outcome.ok else outcome.detail,
                   "detail": outcome.detail if outcome.ok else ""}
            st["last_run"] = {k: row[k] for k in ("start", "end", "ok", "error", "reason", "attempt")}
            if outcome.ok:
                st["last_ok"] = st["last_done"] = row["end"]
            self._record(st, row)
            write_state(self.root, job.name, st)
        return outcome.ok, outcome.detail

    def next_due(self, job: Job) -> str:
        st = read_state(self.root, job.name)
        t = job.trigger
        if t.kind == "cron":
            n = t.cron.next_after(self.now())
            return _stamp(n) if n else "never"
        if t.kind == "every":
            last = st.get("last_done")
            return "now" if last is None else _stamp(float(last) + t.every_s)
        return {"on": "on event", "when": "on check", "manual": "manual"}[t.kind]


class _Lock:
    """One pass per job at a time. launchd, a systemd timer and an operator's
    `st fleet tend` can overlap, and two passes reading the same cursor would
    both fire the same event."""

    def __init__(self, root, name: str):
        self.path = jobs_dir(root) / STATE_DIR / f"{name}.lock"
        self.fh = None

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def __exit__(self, *exc) -> None:
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()


# --- small renderers ----------------------------------------------------------

def _stamp(epoch) -> str:
    if epoch is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(epoch)))


def _human_s(s) -> str:
    s = float(s)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n and s % n == 0:
            return f"{int(s // n)}{unit}"
    if s >= 3600:
        return f"{s / 3600:.1f}h"
    if s >= 60:
        return f"{s / 60:.0f}m"
    return f"{s:.0f}s"


# --- the detached exec child ---------------------------------------------------

def _exec_main(argv: list[str]) -> int:
    """python -m shantytown.jobs _exec <result> <timeout_s> <log> -- <argv...>"""
    if len(argv) < 5 or argv[0] != "_exec" or argv[4] != "--":
        print("usage: python -m shantytown.jobs _exec RESULT TIMEOUT LOG -- ARGV...",
              file=sys.stderr)
        return 2
    result, timeout_s, log_path, cmd = Path(argv[1]), float(argv[2]), argv[3], argv[5:]
    res = run_exec(cmd, timeout_s, log_path=log_path)
    from .files import write_json_atomic
    write_json_atomic(result, res)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_exec_main(sys.argv[1:]))
