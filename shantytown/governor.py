"""governor — spin the crew down as CLAUDE USAGE climbs (aegis-hdqej).

Stiwi, 2026-08-01: *"i want st to be claude code usage-aware. i want to be able to
spin down the crew at certain configured intervals. like 50% spin down and only
work p1+ issues. 70% p0 issues, 80% only support crew and then 95% stop and have
all crew ensure work is pushed"*

    usage >= 50%   dispatch only P1 and above
    usage >= 70%   dispatch only P0
    usage >= 80%   only SUPPORT CREW runs at all
    usage >= 95%   FULL STOP — every agent ensures its work is PUSHED, then stops

THE THRESHOLDS AND THE TIER ACTIONS ARE DEPLOYMENT POLICY, NOT ST OPINION. Every
number above is `[[governor.tier]]` in shantytown.toml and nothing here hardcodes
one — "certain configured intervals" is the deliverable, so a deployment that
wants 40/60/85 edits a config file and does not touch this package. What st owns
is the MECHANISM: read a number, decide a tier, and make three existing surfaces
honour it.

NO NEW MACHINERY, AND NO NEW DAEMON. The governor is a policy INPUT to decisions
st already makes:

  st go      already triage-gated, and already REFUSES rather than typing into a
             pane it should not. A priority floor is one more refusal, and it
             names the tier and the reading that caused it.
  st tend    already runs every ~5 min and already decides who comes up
             (`--target N` fills the tier from the root down). The governor
             withholds a respawn the same way the target cap does.
  st stop    already RECORDS INTENT (stopped.py), so a governor-drained agent is
             distinguishable from a crashed one and tend will not fight the
             governor by respawning what the governor just asked to stand down.

IT NEVER KILLS. tend's whole design argument is that a supervisor which could
also kill is a scheduler with a supervisor's permissions. So "spin down" is
DRAIN — a protocol that ASKS, and whose last step the agent performs itself:

  1. broadcast drain to every live excluded agent, DURABLY, so a dying session
     still got it;
  2. each agent commits WIP on its own branch/worktree, pushes, and reports what
     it pushed;
  3. it stops itself cleanly, so the stop event carries the intent;
  4. tend does not respawn it while the tier holds;
  5. an agent that CANNOT push escalates rather than dying silently.

THE 95% TIER IS THE ONE THAT MUST NOT BE SLOPPY, and the reason is measured:
~/gt/shantytown sat with 10 commits that existed on ONE BOX ONLY for three days
(pushed 2026-08-01, 94f1146..51a9f18). A fleet stopped at 95% with unpushed work
has thrown away the tokens that produced it. So the drain REPORTS by agent —
who drained, what SHAs went up, who failed — and UNREPORTED IS NOT DRAINED. The
report reads the agents' own stop records; it never infers a push from the fact
that a message was sent.

FAIL-SAFE, STATED ON PURPOSE. A governor reading a FROZEN number is worse than no
governor: it holds the fleet wide open at a stale 12% forever, and reads green
the entire time. So a stale probe is SIGNAL LOST, never "the last value is still
current" — and the two ways to be wrong are not symmetric:

    on_signal_lost = "warn"    (default) the fleet runs UNGOVERNED and this
                               ALARMS on every pass. An idled fleet caused by a
                               broken probe is its own outage; the loud version
                               of "I cannot see the tank" is the honest one.
    on_signal_lost = "freeze"  no NEW dispatch, for a deployment that would
                               rather stall than overspend. Still no drain.

AND IT NEVER FAILS INTO DRAIN. No probe bug, no unreachable Prometheus, no
unreadable role catalog may stop the whole crew. Every could-not-tell inside this
module resolves toward KEEP RUNNING, loudly. Draining is only ever the
consequence of a reading we actually got.

THE SIGNAL IS LIVE, and the names below were taken from the running Prometheus
rather than from the bead that specified them — which is the only reason they are
right. The spec said `claude_usage_pct{window="5h"}`; what shipped is
`claude_usage_utilization_pct{window="five_hour"}` behind the recording rule
`claude:usage_utilization_pct:max`, sourced from an authenticated
`GET /api/oauth/usage` (server truth, not an estimate from local transcripts).
Coding against the spec would have produced a governor that queries nothing, and
an expression with no data never fires and never reads as broken — the exact
failure the producer's own bead was filed about.

The reader is still an injected seam with a `stub` source, and that stays: it is
what let this be built and tested against 45/55/75/85/97 before the producer
landed, and it is what lets a deployment rehearse a tier without waiting to
actually be at 95%.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# The series this governor consumes, named once, and VERIFIED AGAINST THE LIVE
# PROMETHEUS rather than against the bead that specified them (they differed: the
# spec said `claude_usage_pct{window="5h"}` and what shipped is
# `claude_usage_utilization_pct{window="five_hour"}`, reusing a name this fleet had
# already chosen). A governor that queries a series nobody produces is the exact
# failure the producer's own bead was filed about — an expression with no data
# never fires and never reads as broken.
#
# READ THE RECORDING RULE, NEVER THE BARE PER-ACCOUNT METRIC. The producer is
# multi-account by design; `claude:usage_utilization_pct:max` is `max by (window)`
# across accounts, so adding an account cannot silently change what the governor
# sees. Reading the per-account series directly would govern by whichever account
# happened to be last in the response.
USAGE_METRIC = "claude:usage_utilization_pct:max"
# The per-account source. Named because the TEXTFILE reader has no choice: a
# recording rule is computed by Prometheus and is not in the file, so that reader
# takes the max across accounts ITSELF — the same aggregation, so both readers
# answer the same question rather than one of them quietly governing by one
# account.
ACCOUNT_USAGE_METRIC = "claude_usage_utilization_pct"
PROBE_OK_METRIC = "claude_usage_probe_success"
PROBE_TS_METRIC = "claude_usage_probe_timestamp_seconds"
# How old the PUBLISHED percentage is. Distinct from the probe timestamp and worth
# reading separately: when the probe fails the producer RETAINS the last good
# percentages and flags them, so the number stays fresh-looking while this climbs.
CACHE_AGE_METRIC = "claude_usage_cache_age_seconds"

# The producer's window labels. `five_hour`/`seven_day`, not `5h`/`7d`.
FIVE_HOUR, SEVEN_DAY = "five_hour", "seven_day"

SOURCES = ("prometheus", "textfile", "stub")
WARN, FREEZE = "warn", "freeze"
ON_SIGNAL_LOST = (WARN, FREEZE)

DRAIN = "drain"
ACTIONS = (DRAIN,)

# How old a probe reading may be before it is SIGNAL LOST rather than a number.
# 15 minutes is three tend passes: long enough that one missed scrape does not
# blind the governor, short enough that a dead probe cannot hold a spending fleet
# open for an hour. Overridable — it is a property of the producer's cadence, and
# the producer is a different bead.
DEFAULT_MAX_AGE_S = 900

# Leaving a tier requires falling this far BELOW its threshold. Without it a
# reading oscillating around 70 flips the whole fleet between "P0 only" and "P1+"
# every pass, and each flip is a dispatch decision an agent already acted on.
DEFAULT_RELAX_MARGIN = 5


class GovernorError(ValueError):
    """The [governor] table exists and is wrong. config.ConfigError wraps it —
    the operator must see the file and the key, never a bare value error."""


# --- the policy (all of it from shantytown.toml) ------------------------------

@dataclass(frozen=True)
class Tier:
    """One configured interval. Every field is a DIFFERENT kind of restriction and
    a tier may carry more than one — they compose, they do not exclude.

        at            engage while usage >= this percent
        window        WHICH budget `at` is read against (aegis-59hao)
        min_priority  a DISPATCH floor (see admits below — read the sign)
        traits        only agents whose role set carries one of these RUN at all
        action        "drain" — the full stop, with the push protocol

    THERE ARE TWO BUDGETS AND THEY EXHAUST INDEPENDENTLY (aegis-59hao). The
    producer publishes both windows and the governor used to read exactly one, so
    it was blind to whichever budget was actually further gone — and blind in the
    expensive direction, because a five-hour window refills in HOURS while the
    weekly does not refill for DAYS. Governing only the cheap-to-recover budget is
    backwards. `window` lives on the TIER rather than in a second config table so
    that a seven-day rule is just another tier row: nothing new to learn, and a
    config that never mentions a window keeps its exact current meaning.
    """
    at: int
    min_priority: int | None = None
    traits: tuple[str, ...] = ()
    action: str | None = None
    window: str = FIVE_HOUR

    @property
    def drains(self) -> bool:
        return self.action == DRAIN

    def label(self) -> str:
        """What this tier does, in one clause, for a refusal message. A refusal
        that names only a number teaches nothing — and with two budgets, one that
        names only a NUMBER is worse than useless: "usage 72%" sends an operator
        to look at the wrong budget. Every label names its window.
        """
        bits = []
        if self.min_priority is not None:
            bits.append(f"dispatch only P{self.min_priority} and above")
        if self.traits:
            bits.append(f"only {'/'.join(self.traits)} crew runs")
        if self.drains:
            bits.append("FULL STOP — every agent pushes its work, then stops")
        what = "; ".join(bits) or "no restriction declared"
        return f"{what} [{self.window} >= {self.at}%]"


@dataclass(frozen=True)
class Policy:
    """The whole [governor] table, resolved.

    THERE IS NO `enabled` KEY, deliberately. Declaring a tier IS the enabling act:
    a separate boolean creates exactly one new failure mode — an operator writes
    four tiers, sees nothing happen, and has to discover that a fifth line was
    required. `tiers == ()` is off, and it is the default, so a deployment that
    has never heard of this feature is unaffected.
    """
    source: str = "stub"
    window: str = FIVE_HOUR
    on_signal_lost: str = WARN
    relax_margin: int = DEFAULT_RELAX_MARGIN
    max_age_seconds: int = DEFAULT_MAX_AGE_S
    # source-specific coordinates. url for prometheus, path for a node_exporter
    # textfile, stub_pct for tests and for a dry deployment before fnd8g lands.
    url: str | None = None
    path: str | None = None
    # Basic auth for a Prometheus that requires it. The PASSWORD IS A FILE PATH,
    # never a value: shantytown.toml is tracked, diffable and hand-edited, so an
    # inline secret is a secret in git — and this is the one table an operator is
    # most likely to paste into a bead when asking why the governor is blind.
    username: str | None = None
    password_file: str | None = None
    stub_pct: float | None = None
    tiers: tuple[Tier, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.tiers)

    def tier_at(self, level: int) -> Tier | None:
        for t in self.tiers:
            if t.at == level:
                return t
        return None

    def candidate(self, pct: float) -> Tier | None:
        """The highest tier this reading alone engages. Tiers are stored sorted."""
        found = None
        for t in self.tiers:
            if pct >= t.at:
                found = t
        return found

    def engaged(self, top: Tier | None) -> tuple[Tier, ...]:
        """EVERY tier at or below `top` — because the tiers are CUMULATIVE.

        THIS IS THE BUG THAT SHIPPED FOR ABOUT AN HOUR, and it was invisible to a
        test suite that exercised each tier alone. Reading only the highest
        tier's own fields, the spoken configuration does this:

            75%  ->  70% tier, min_priority = 0   ->  P2 refused    (right)
            97%  ->  95% tier, action = "drain"   ->  P2 ADMITTED   (absurd)

        The 95% tier declares no priority floor, so the floor VANISHED at exactly
        the usage where it matters most: the fleet is being asked to stop and push
        its work, and a coordinator could still hand out P2 chores. Restriction
        must be monotone in usage — it can only ever tighten as the number climbs.

        Found end-to-end, by driving the real CLI at 45/55/75/85/97 and reading
        what it did, which is the only reason it is not still there.
        """
        if top is None:
            return ()
        return tuple(t for t in self.tiers if t.at <= top.at)


# --- the reading --------------------------------------------------------------

@dataclass(frozen=True)
class Reading:
    """One observation of the usage signal.

    `pct is None` and `ok is False` are BOTH could-not-look, and they are kept
    apart because they need different fixes: no value means the series is absent
    (fnd8g has not landed, or the query is wrong), while ok=False means the
    producer ran and told us its own probe FAILED. Collapsing them would send an
    operator to read a Prometheus config when the answer is in a probe log.
    """
    pct: float | None = None
    at: float | None = None          # the probe's own timestamp, epoch seconds
    ok: bool = True                  # claude_usage_probe_success
    error: str = ""                  # why we could not look at all
    source: str = ""
    # How old the PUBLISHED percentage is, straight from the producer. A SECOND,
    # independent staleness signal and not a duplicate of `at`: when the probe
    # fails the producer keeps serving the last good percentages, so a value can
    # be minutes old while everything else about the exposition looks current.
    # None = not published; the timestamp check below still applies.
    cache_age: float | None = None

    def lost(self, now: float, max_age: float) -> str:
        """"" if this reading is usable, else WHY it is not.

        A STALE READING IS NOT A READING. This is the whole fail-safe: the last
        value of a dead probe is not the current value, and treating it as one
        holds a spending fleet wide open at a number from last Tuesday.
        """
        if self.error:
            return self.error
        if not self.ok:
            # FLYING BLIND, not low usage. The producer RETAINS the last good
            # percentages when a probe fails and flags them here, so the number
            # still present alongside this flag is history, not a measurement —
            # and it is the direction that spends: a retained 11% holds the fleet
            # wide open for as long as the probe stays down.
            return (f"{PROBE_OK_METRIC} is 0 — a probe FAILED, so the published "
                    f"percentage is the last good one, not a measurement. This "
                    f"is flying blind, never low usage")
        if self.pct is None:
            # (The producer is a separate, still-open item; cited in the module
            # docstring's comment, never in an emittable string — this repo is
            # public and a string literal is a value the program can print.)
            return (f"no {USAGE_METRIC} series — nothing is publishing a "
                    f"utilization number to govern by")
        if self.at is None:
            return (f"{USAGE_METRIC} carries no {PROBE_TS_METRIC}, so its age "
                    f"cannot be checked — an unaged reading is indistinguishable "
                    f"from a frozen one")
        # THE OLDER OF THE TWO AGES WINS. The producer keeps serving the last good
        # percentages when a probe fails, so `cache_age` can be minutes while the
        # probe timestamp still looks recent. Taking the max is "be more
        # conservative, not less" — the number being governed by is only as fresh
        # as the stalest thing that produced it.
        age = now - self.at
        if self.cache_age is not None:
            age = max(age, self.cache_age)
        if age > max_age:
            return (f"the probe last succeeded {int(age)}s ago (limit "
                    f"{int(max_age)}s) — STALE, and a stale number reads green "
                    f"forever")
        # A clock skew big enough to put the probe in the future is not a
        # reading either: it would defeat every staleness check that follows.
        if age < -max_age:
            return (f"the probe timestamp is {int(-age)}s in the FUTURE — clock "
                    f"skew, so its age cannot be trusted")
        return ""


# --- the readers --------------------------------------------------------------

_SAMPLE = re.compile(r"^\s*([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+([^\s#]+)")
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')


def parse_prom(body: str) -> list[tuple[str, dict, float]]:
    """Prometheus text exposition -> [(name, labels, value)].

    Small on purpose: this reads a node_exporter textfile that WE produce
    (aegis-fnd8g), not arbitrary third-party exposition. It skips comments and
    unparseable values rather than raising, because a malformed line in a file
    that also contains the value we want must not blind the governor — but it
    never INVENTS a sample, so a file with nothing usable yields nothing and the
    caller reports could-not-look.
    """
    out: list[tuple[str, dict, float]] = []
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _SAMPLE.match(line)
        if not m:
            continue
        name, raw_labels, raw_value = m.group(1), m.group(2) or "", m.group(3)
        try:
            value = float(raw_value)
        except ValueError:
            continue
        labels = {k: v for k, v in _LABEL.findall(raw_labels)}
        out.append((name, labels, value))
    return out


def _from_samples(samples, window: str, source: str) -> Reading:
    """The ONE place samples become a Reading, shared by every reader.

    Shared deliberately: the textfile and the Prometheus paths must not be able
    to disagree about which window governs or about what a missing probe flag
    means. Two implementations of "is this signal lost" is two policies.

    EVERY MULTI-SERIES FOLD GOES THE CONSERVATIVE WAY, because the producer is
    multi-account and each of these arrives once per account:

        pct    MAX     the recording rule's own aggregation. An account at 88%
                       and one at 12% is a fleet at 88 — governing by the average
                       or by whichever came last spends the exhausted account's
                       budget it does not have.
        ok     MIN     ANY blind account blinds the governor. A probe that
                       succeeded for one account tells us nothing about another.
        age    MAX     the OLDEST reading wins. Freshness is only as good as the
                       stalest input to the number being governed by.
    """
    pct = at = cache_age = None
    rule_pct = None
    ok = True
    saw_ok = False
    for name, labels, value in samples:
        if name == USAGE_METRIC and labels.get("window") == window:
            # The recording rule is ALREADY the max across accounts. If Prometheus
            # somehow returned several, max again — it cannot be wrong.
            rule_pct = value if rule_pct is None else max(rule_pct, value)
        elif name == ACCOUNT_USAGE_METRIC and labels.get("window") == window:
            pct = value if pct is None else max(pct, value)
        elif name == PROBE_OK_METRIC:
            ok, saw_ok = (ok and bool(value)), True
        elif name == PROBE_TS_METRIC:
            at = value if at is None else min(at, value)
        elif name == CACHE_AGE_METRIC:
            cache_age = value if cache_age is None else max(cache_age, value)
    # The rule wins where it exists (Prometheus); the per-account max is the
    # textfile's equivalent, computed here rather than left to whichever account
    # sorted last.
    if rule_pct is not None:
        pct = rule_pct
    if not saw_ok and pct is not None:
        # The producer published a value and no success flag. NOT read as ok:
        # the flag is how staleness is meant to be visible, and inferring "it
        # must have worked" from the presence of a number is exactly the
        # assumption the flag exists to remove.
        return Reading(pct=pct, at=at, ok=True, source=source,
                       cache_age=cache_age,
                       error=f"no {PROBE_OK_METRIC} series — the value cannot be "
                             f"vouched for, so it is not read as a measurement")
    return Reading(pct=pct, at=at, ok=ok, source=source, cache_age=cache_age)


class StubReader:
    """A fixed reading. The seam that makes the whole governor testable NOW —
    aegis-fnd8g has not produced the real series yet, and a governor that could
    only be exercised against a metric that does not exist would ship untested and
    be discovered wrong at 95%."""

    def __init__(self, pct: float | None = None, at: float | None = None,
                 ok: bool = True, error: str = "", now=time.time):
        self._pct, self._at, self._ok, self._error = pct, at, ok, error
        self._now = now

    def read(self) -> Reading:
        at = self._at if self._at is not None else self._now()
        return Reading(pct=self._pct, at=at, ok=self._ok, error=self._error,
                       source="stub")


class TextfileReader:
    """The node_exporter textfile aegis-fnd8g writes, read DIRECTLY.

    Worth having alongside the Prometheus reader because it removes a whole
    hop: on the box that runs both the probe and the crew, going through
    Prometheus adds a scrape interval of lag and a service that can be down, to
    read a file sitting on local disk. It is also the honest fallback when
    Prometheus is the thing that is broken.
    """

    def __init__(self, path, window: str = FIVE_HOUR):
        self.path = Path(path)
        self.window = window

    def read(self) -> Reading:
        try:
            body = self.path.read_text()
        except OSError as e:
            return Reading(source="textfile",
                           error=f"cannot read {self.path}: {e}")
        return _from_samples(parse_prom(body), self.window, "textfile")


class PrometheusReader:
    """`/api/v1/query` against a Prometheus. One request, not three: the whole
    `claude_usage_*` family comes back in a single instant-vector query, so a
    tend pass costs one round trip and the three values are consistent with each
    other rather than sampled a second apart."""

    # BOTH families: the recording rule (`claude:usage_utilization_pct:max`)
    # carries a COLON, so a `claude_usage_.*` pattern silently misses the one
    # series the governor is supposed to read and falls back to per-account.
    QUERY = '{__name__=~"claude_usage_.*|claude:usage_.*"}'

    def __init__(self, url: str, window: str = FIVE_HOUR, timeout: float = 5.0,
                 fetch=None, username: str | None = None,
                 password: str | None = None):
        self.url = (url or "").rstrip("/")
        self.window = window
        self.timeout = timeout
        self.username, self.password = username, password
        # One tiny transport seam so tests inject a fake without patching
        # urllib — the same shape forgejo.py uses.
        self._fetch = fetch or self._http

    def _http(self, url: str) -> str:
        headers = {"Accept": "application/json"}
        if self.username:
            # BASIC AUTH, because a Prometheus that requires it and a reader that
            # cannot send it produce a governor that is permanently signal-lost —
            # and on the `warn` default that is a fleet running ungoverned with an
            # alarm nobody can fix from the config file. The password comes from
            # `password_file`, never inline: shantytown.toml is a tracked,
            # diffable, hand-edited file and a secret in it is a secret in git.
            import base64
            token = base64.b64encode(
                f"{self.username}:{self.password or ''}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read().decode()

    def read(self) -> Reading:
        from urllib.parse import quote
        url = f"{self.url}/api/v1/query?query={quote(self.QUERY)}"
        try:
            body = self._fetch(url)
        except urllib.error.HTTPError as e:
            # NAME AN AUTH FAILURE AS ONE. "unreachable" is what the generic
            # branch below says, and for a 401 it is false in the expensive
            # direction: the server is up and answering, it is refusing US, and
            # an operator told "unreachable" goes looking for a Prometheus
            # outage instead of a missing `username`/`password_file`.
            if e.code in (401, 403):
                return Reading(source="prometheus",
                               error=f"{self.url} answered {e.code} "
                                     f"{'UNAUTHORIZED' if e.code == 401 else 'FORBIDDEN'}"
                                     f" — it is up and refusing this query. Set "
                                     f"`username` and `password_file` under "
                                     f"[governor]; this is a credential problem, "
                                     f"not an outage")
            return Reading(source="prometheus",
                           error=f"{self.url} answered HTTP {e.code}")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            return Reading(source="prometheus",
                           error=f"{self.url} unreachable: {e}")
        try:
            data = json.loads(body)
            if data.get("status") != "success":
                return Reading(source="prometheus",
                               error=f"{self.url} answered status="
                                     f"{data.get('status')!r}")
            result = data["data"]["result"]
        except (ValueError, KeyError, TypeError) as e:
            return Reading(source="prometheus",
                           error=f"{self.url} answered something this reader "
                                 f"cannot parse ({type(e).__name__})")
        samples = []
        for row in result:
            metric = row.get("metric") or {}
            name = metric.get("__name__", "")
            try:
                value = float(row["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            samples.append((name, metric, value))
        return _from_samples(samples, self.window, "prometheus")


def reader_for(policy: Policy, *, now=time.time):
    """Policy -> a reader. RAISES for a source it cannot wire, rather than
    silently degrading to the stub: a governor that quietly reads a stub instead
    of Prometheus reports 'no restriction' forever, which is the exact
    holds-the-fleet-open failure this module's fail-safe section is about."""
    if policy.source == "stub":
        return StubReader(pct=policy.stub_pct, now=now)
    if policy.source == "textfile":
        if not policy.path:
            raise GovernorError("source = \"textfile\" needs `path` — the "
                                "node_exporter textfile to read")
        return TextfileReader(policy.path, policy.window)
    if policy.source == "prometheus":
        if not policy.url:
            raise GovernorError("source = \"prometheus\" needs `url` — e.g. "
                                "url = \"http://prometheus.example\"")
        password = None
        if policy.password_file:
            try:
                password = Path(policy.password_file).expanduser().read_text().strip()
            except OSError as e:
                # REFUSE, never fall through unauthenticated. An unreadable
                # password file that degraded to an anonymous request would get a
                # 401, report SIGNAL LOST, and send the operator hunting a
                # Prometheus outage that is really a file permission.
                raise GovernorError(
                    f"password_file {policy.password_file!r} cannot be read "
                    f"({e}) — refusing to query unauthenticated, which would "
                    f"look like an outage rather than a config error") from e
        return PrometheusReader(policy.url, policy.window,
                                username=policy.username, password=password)
    raise GovernorError(f"unknown source {policy.source!r}; "
                        f"expected one of: {', '.join(SOURCES)}")


# --- hysteresis state ---------------------------------------------------------

@dataclass
class Engaged:
    """Which tier is currently HELD, and since when.

    `since` is the drain EPISODE key as well as a timestamp: a drain broadcast is
    deduped against it, so relaxing below a drain tier and climbing back into it
    starts a NEW episode and every agent is told again. Carrying one episode
    across a relax would silence the second drain, which is the failure that
    matters — the first drain's agents are gone and the fleet that came back has
    never been asked.
    """
    at: int | None = None
    since: float = 0.0


class FilesGovernorState:
    """The engaged tier, on disk beside launched/ and stopped/.

    ON DISK because hysteresis that lives in a process is hysteresis that resets
    every tend pass — and a tend pass is a fresh process every five minutes. The
    whole point of relax_margin is to remember what we decided last time.
    """

    def __init__(self, root):
        self.path = Path(root) / "governor" / "state.json"

    def get(self) -> Engaged:
        try:
            d = json.loads(self.path.read_text())
            at = d.get("at")
            return Engaged(at=None if at is None else int(at),
                           since=float(d.get("since") or 0.0))
        except (OSError, ValueError, TypeError):
            # Unreadable state is NO hold, never an invented one. Fail-open: the
            # governor still engages whatever the live reading says, it just does
            # not remember a higher tier it can no longer prove.
            return Engaged()

    def set(self, engaged: Engaged) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            from .files import write_json_atomic
            write_json_atomic(self.path, {"at": engaged.at,
                                          "since": engaged.since})
        except OSError:
            pass          # best-effort, same rule as the launch stamp


# --- the verdict --------------------------------------------------------------

# Why an agent is excluded / an item refused — the strings a caller renders.
ADMITTED = ""


@dataclass(frozen=True)
class Verdict:
    """What the governor decided this pass, and on what evidence.

    Every field a caller needs to explain itself is here, because a refusal that
    cannot state the reading behind it is indistinguishable from a bug — the same
    argument dispatch.TriageRefused makes for carrying the whole Decision.
    """
    reading: Reading
    pct: float | None = None
    tier: Tier | None = None
    held: bool = False               # the tier is held by hysteresis, not by pct
    signal_lost: bool = False
    frozen: bool = False             # signal lost AND on_signal_lost = "freeze"
    why: str = ""
    alarm: str = ""                  # non-empty -> say it LOUDLY, EVERY pass
    # EVERY engaged tier, not just the top one. Restriction is CUMULATIVE and
    # must be monotone in usage — see Policy.engaged for the bug that proves it.
    engaged: tuple[Tier, ...] = ()

    @property
    def drains(self) -> bool:
        """Draining is only ever the consequence of a reading we GOT. A signal we
        could not read can never reach this — see the fail-safe note in the module
        docstring: never fail INTO drain."""
        if self.signal_lost:
            return False
        return any(t.drains for t in self.engaged)

    @property
    def floor(self) -> int | None:
        """The effective priority floor: the STRICTEST any engaged tier declares.

        Strictest is the LOWEST number (0 is the highest priority), so this is a
        min, not a max. A higher tier that declares no floor inherits the one
        below it rather than erasing it.
        """
        floors = [t.min_priority for t in self.engaged if t.min_priority is not None]
        return min(floors) if floors else None

    @property
    def trait_tiers(self) -> tuple[Tier, ...]:
        """Every engaged tier that restricts WHO RUNS. An agent must satisfy them
        all — two tiers each naming a band is two conditions, not a choice."""
        return tuple(t for t in self.engaged if t.traits)

    def admits(self, item) -> str:
        """"" if this item may be dispatched, else WHY not.

        READ THE SIGN OF `min_priority`. It is bd's numbering, where 0 is the
        HIGHEST priority — so `min_priority = 1` admits P0 and P1 and refuses P2
        and below. The key is named for what an operator means ("only P1 and
        above") and the comparison is therefore `<=`. This is a foot-gun and it
        is documented at every place it appears rather than renamed, because the
        name is the one Stiwi's spec used.

        AN ITEM WITH NO PRIORITY IS REFUSED while a floor is in force, and that
        direction is deliberate. The governor's entire job above 50% is to spend
        the remaining budget only on work whose importance is stated; admitting
        an item whose importance nobody stated defeats it silently. The refusal
        is per-item and operator-fixable in one command, which is a different and
        much cheaper thing than a fleet-wide stall.
        """
        if self.frozen:
            return (f"the usage governor is FROZEN: {self.why}. "
                    f"on_signal_lost = \"freeze\", so no NEW work is dispatched "
                    f"until the signal comes back. Nothing has been stopped and "
                    f"nothing is lost — in-flight work continues.")
        if self.drains:
            # A DRAIN TIER DISPATCHES NOTHING. Every live agent has been asked to
            # push its work and stop; handing one a new item contradicts the
            # instruction it is already acting on, and the item would be picked up
            # by a session that is about to end. Refused regardless of priority —
            # "full stop" that still admits a P0 is not a full stop.
            return (f"{self._tier_says()}: FULL STOP — the fleet is draining, so "
                    f"NOTHING is dispatched. The item is untouched and stays "
                    f"ready; dispatch it when usage falls.")
        floor = self.floor
        if self.tier is None or floor is None:
            return ADMITTED
        priority = getattr(item, "priority", None)
        if priority is None:
            return (f"{self._tier_says()} and {getattr(item, 'id', 'this item')} "
                    f"carries NO priority, so it cannot be shown to clear the "
                    f"floor. Set one and re-dispatch "
                    f"(`bd update {getattr(item, 'id', '<id>')} --priority=<0-4>`).")
        if priority > floor:
            return (f"{self._tier_says()} and {getattr(item, 'id', 'this item')} "
                    f"is P{priority}. Dispatch it when usage falls, or raise its "
                    f"priority if it really is that important.")
        return ADMITTED

    def excludes(self, agent, catalog=None) -> str:
        """"" if this agent may RUN under the current tier, else WHY not.

        FAIL-OPEN ON EVERY COULD-NOT-TELL. An unknown role, a catalog that cannot
        resolve a stacked conflict, no catalog at all — each of those means we do
        not know whether this agent is support crew, and the safe direction is to
        LET IT RUN. Spinning down an agent because we could not read its role
        would let a config typo stop the crew, which the fail-safe rules out by
        name.
        """
        if self.tier is None or self.signal_lost:
            return ADMITTED
        if self.drains:
            return (f"{self._tier_says()}: FULL STOP. Push your work, report what "
                    f"went up, then stop cleanly.")
        # EVERY trait tier, not just the top one — two engaged tiers each naming
        # a band are two conditions to satisfy, not a choice between them.
        for tier in self.trait_tiers:
            carried, known = carries_any(agent, tier.traits, catalog)
            if not known:
                continue                  # could not tell -> this tier lets it run
            if not carried:
                return (f"{self._tier_says()}, and {getattr(agent, 'name', '?')} "
                        f"does not carry {'/'.join(tier.traits)}.")
        return ADMITTED

    def effect(self) -> str:
        """What is ACTUALLY in force — composed across every engaged tier, not
        read off the top one. The status line uses this because a line that
        reported only the top tier's own fields would have hidden the cumulative
        bug (`95% tier · FULL STOP` while a P2 dispatched happily underneath)."""
        if self.drains:
            return "FULL STOP — every agent pushes its work, then stops; nothing dispatches"
        bits = []
        if self.floor is not None:
            bits.append(f"dispatch only P{self.floor} and above")
        for tier in self.trait_tiers:
            bits.append(f"only {'/'.join(tier.traits)} crew runs")
        return "; ".join(bits) or "no restriction declared"

    def _tier_says(self) -> str:
        t = self.tier
        assert t is not None
        seen = ("held from a previous pass (hysteresis)" if self.held
                else f"usage {self.pct:.0f}%" if self.pct is not None else "usage")
        return f"the usage governor's {t.at}% tier is engaged ({seen})"

    def render(self) -> str:
        """One line for `st tend` / `st tend --status`."""
        if not self.tier and not self.signal_lost:
            pct = "—" if self.pct is None else f"{self.pct:.0f}%"
            return f"  governor    usage {pct} · no tier engaged · wide open"
        if self.signal_lost:
            state = "FROZEN — no new dispatch" if self.frozen else "running UNGOVERNED"
            return f"  governor    SIGNAL LOST · {state} · {self.why}"
        pct = "—" if self.pct is None else f"{self.pct:.0f}%"
        held = " (held)" if self.held else ""
        return (f"  governor    usage {pct} · {self.tier.at}% tier{held} · "
                f"{self.effect()}")


def carries_any(agent, traits, catalog=None) -> tuple[bool, bool]:
    """(carries, could_tell). Does this agent's ROLE SET carry any of `traits`?

    RESOLVED THROUGH THE CATALOG, never by matching a role NAME (ruled on the
    crew-traits design bead, 2026-08-01). A name match would let `roles = [...,
    "support"]` work with nothing declaring what `support` IS — a second,
    ungoverned vocabulary beside the role stack, with no axis, no cardinality and
    no precedence. `support` is a VALUE on the survival axis, and a deployment
    that wants a support tier declares it:

        [roles.oncall]
        survival = "support"
        lane = ["monitoring"]

    TWO MATCH RULES, because the two new axes mean different things:

      survival (SINGLE, RANKED)  a THRESHOLD, not an equality. `traits =
                                 ["support"]` means "support AND ABOVE" — an
                                 agent banded `last` obviously survives a tier
                                 that spares `support`, and equality matching
                                 would spin down a coordinator banded `last`
                                 while keeping the watchers, which is upside
                                 down.
      everything else            exact membership. A lane is a set you are in or
                                 are not; there is no ordering to threshold on.

    `could_tell` is False when nothing could be resolved — an undeclared role, an
    unranked stacked conflict, no catalog at all. The caller must then fail OPEN
    (Verdict.excludes): a policy we cannot evaluate never spins the crew down.
    """
    from .traits import AXES, MULTI, SURVIVAL
    wanted = {t.lower() for t in traits}
    roles = tuple(getattr(agent, "effective_roles", lambda: ())() or ())
    if not roles:
        role = getattr(agent, "role", None)
        roles = (role,) if role else ()
    if not roles or catalog is None:
        return False, False
    try:
        composed = catalog.of(list(roles))
    except Exception:                     # noqa: BLE001 — UnknownRole/Ambiguous/anything
        return False, False               # could not tell -> the agent runs

    # THE THRESHOLD, on the one axis that declares an order.
    mine = getattr(composed, SURVIVAL, None)
    if mine is not None:
        my_rank = catalog.precedence.get((SURVIVAL, mine))
        for t in traits:
            want_rank = catalog.precedence.get((SURVIVAL, t.lower()))
            if want_rank is None or my_rank is None:
                continue
            if my_rank >= want_rank:
                return True, True

    # Exact membership everywhere else (and on survival too, so an UNRANKED
    # survival value still matches itself rather than being unusable).
    for axis, kind in AXES.items():
        value = getattr(composed, axis, None)
        if kind == MULTI:
            for v in value or ():
                if str(v).lower() in wanted:
                    return True, True
        elif value is not None and str(value).lower() in wanted:
            return True, True
    return False, True


# --- the governor -------------------------------------------------------------

class Governor:
    """Read the number, decide the tier. Nothing else — the ACTING lives in the
    surfaces that already act (`st go` refuses, `st tend` withholds, the Drainer
    asks). Every dependency is injected so a test drives 45/55/75/85/97 through
    the whole decision with no Prometheus, no clock and no filesystem.
    """

    def __init__(self, policy: Policy, reader, state=None, *, now=time.time):
        self.policy = policy
        self.reader = reader
        self.state = state
        self._now = now

    def evaluate(self, *, persist: bool = True) -> Verdict:
        """One decision, with hysteresis.

        `persist=False` is the READ path (`st go`). It honours a hold that is
        already recorded but never extends one — a dispatch command must not
        ratchet fleet policy as a side effect of being run. `st tend` is the
        evaluation point (it is the pass that already decides who lives), so it
        is the writer.
        """
        pol = self.policy
        now = self._now()
        if not pol.active:
            return Verdict(reading=Reading(source="none"),
                           why="no [[governor.tier]] declared — the governor is off")

        try:
            reading = self.reader.read()
        except Exception as e:            # noqa: BLE001 — a reader must never fatal a pass
            reading = Reading(source=pol.source,
                              error=f"the {pol.source} reader raised "
                                    f"{type(e).__name__}: {str(e)[:120]}")

        lost = reading.lost(now, pol.max_age_seconds)
        if lost:
            frozen = pol.on_signal_lost == FREEZE
            # DO NOT clear the engaged tier here. Losing sight of the number is
            # not evidence that usage fell, and dropping the hold would silently
            # re-open dispatch at the exact moment we can least justify it. The
            # hold stays on disk; it simply is not APPLIED while blind, because
            # applying a remembered tier to an unmeasured present is the frozen-
            # number failure wearing the other hat.
            return Verdict(
                reading=reading, pct=reading.pct, signal_lost=True, frozen=frozen,
                why=lost,
                alarm=(f"USAGE SIGNAL LOST: {lost}. "
                       + ("on_signal_lost = \"freeze\": no new work is being "
                          "dispatched. " if frozen else
                          "on_signal_lost = \"warn\": THE FLEET IS RUNNING "
                          "UNGOVERNED and will not spin down at any threshold. ")
                       + "Fix the usage probe — this alarms every pass on "
                         "purpose; a governor that goes quiet when it cannot "
                         "see is indistinguishable from one with nothing to "
                         "report."))

        pct = float(reading.pct)          # lost() proved it is not None
        candidate = pol.candidate(pct)
        engaged = self.state.get() if self.state is not None else Engaged()

        # THE HOLD. A tier is left only BELOW (threshold - relax_margin), so a
        # reading oscillating around a threshold does not flip the fleet's policy
        # every pass. It can only ever hold a tier HIGHER than the live reading
        # justifies — never lower — so hysteresis can never be the reason the
        # governor spends more than it should.
        held = False
        chosen = candidate
        if engaged.at is not None and pct >= engaged.at - pol.relax_margin:
            kept = pol.tier_at(engaged.at)
            if kept is not None and (chosen is None or kept.at > chosen.at):
                chosen, held = kept, True

        since = engaged.since
        if chosen is None:
            since = 0.0
        elif engaged.at != chosen.at or not since:
            # A NEW tier, or the first pass that engaged this one: start a new
            # episode. The episode key is what the drain broadcast dedupes on.
            since = now

        if persist and self.state is not None:
            self.state.set(Engaged(at=None if chosen is None else chosen.at,
                                   since=since))

        why = (f"usage {pct:.0f}%"
               + (f" (holding the {chosen.at}% tier; it is left below "
                  f"{chosen.at - pol.relax_margin}%)" if held else ""))
        return Verdict(reading=reading, pct=pct, tier=chosen, held=held, why=why,
                       engaged=pol.engaged(chosen))

    def episode(self) -> float:
        """The current tier's episode key — what a drain dedupes against."""
        return self.state.get().since if self.state is not None else 0.0


# --- the drain protocol -------------------------------------------------------

@dataclass(frozen=True)
class Drained:
    """One agent's outcome in a drain. `pushed` is what the AGENT reported, never
    what we assumed: a message we sent is not a push, and this whole record
    exists because a fleet stopped with unpushed work has thrown away the tokens
    that produced it."""
    agent: str
    told_at: float
    state: str                    # drained | pending | failed
    pushed: str = ""              # what the agent said went up
    why: str = ""


DRAINED, PENDING, FAILED = "drained", "pending", "failed"

# The marker an agent puts in `st stop --reason` so the report can read its
# outcome. A protocol needs one word both ends agree on, and free text does not
# make one — the drain message below tells the agent exactly what to write.
DRAIN_OK = "drained:"
DRAIN_FAIL = "drain-failed:"


class DrainLedger:
    """Who was told, in which episode. One file per agent, beside untracked/.

    DEDUPED BY EPISODE, not by a boolean: a fleet that relaxes below the drain
    tier and climbs back into it must be told AGAIN, because the agents that were
    told the first time are gone. A boolean would silence exactly that.
    """

    def __init__(self, root):
        self.root = Path(root) / "governor" / "drain"

    def _path(self, agent: str) -> Path:
        return self.root / f"{agent}.json"

    def told(self, agent: str) -> tuple[float, float] | None:
        """(episode, told_at) or None."""
        try:
            d = json.loads(self._path(agent).read_text())
            return float(d["episode"]), float(d["at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def tell(self, agent: str, episode: float, at: float, tier: int) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            from .files import write_json_atomic
            write_json_atomic(self._path(agent), {"episode": episode, "at": at,
                                                  "tier": tier})
        except OSError:
            pass

    def agents(self) -> list[str]:
        try:
            return sorted(p.stem for p in self.root.glob("*.json"))
        except OSError:
            return []

    def clear(self) -> None:
        """The tier relaxed. Forget the episode — the next one starts clean."""
        for p in self.root.glob("*.json") if self.root.is_dir() else []:
            try:
                p.unlink()
            except OSError:
                pass


# The instruction an agent receives. It MUST fit an inbox message (the durable
# channel caps a body at 493 chars — inbox.py maps it to a tracker item titled
# `inbox: <body>` under bd's 500-char title limit), so it is a pointer, not a
# document: the exact commands, in order, and nothing else. Tested for length.
def drain_message(agent: str, tier: int, pct: float | None) -> str:
    seen = f"{pct:.0f}%" if pct is not None else "signal high"
    return (
        f"DRAIN ({tier}% usage tier, at {seen}): stop taking new work. "
        f"1) commit WIP in your OWN worktree/branch; "
        f"2) git push (rejected? rebase on origin/main, retry, NEVER force); "
        f"3) st stop {agent} --reason '{DRAIN_OK} <repo>@<sha> ...' — that reason "
        f"IS the report, and unreported counts as NOT drained. "
        f"Cannot push? st stop {agent} --reason '{DRAIN_FAIL} <why>' and escalate "
        f"— never die silently on unpushed work."
    )


class Drainer:
    """Broadcast the drain, then REPORT it from the agents' own stop records.

    Every dependency injected for the usual reason: this must be provable without
    a tmux, a bd, or a five-minute wait. `deliver` is the durable send (it must
    survive the recipient's session dying, which is the entire point at 95%);
    `stops` is the FilesStops the agents write when they stop themselves.
    """

    def __init__(self, root, deliver, stops, *, log=None, now=time.time):
        self.ledger = DrainLedger(root)
        self._deliver = deliver              # (agent_name, body) -> str (msg id)
        self._stops = stops
        self._log = log or (lambda msg: None)
        self._now = now

    def sweep(self, agents, verdict: Verdict, episode: float,
              *, live=None, catalog=None) -> list[Drained]:
        """Tell everyone the tier excludes, once per episode, then report.

        `live(agent) -> bool` decides who is worth telling. A DOWN agent is not
        told: it has no session to push from, and a durable message it will read
        on some future relaunch would instruct it to stop the moment it comes
        back — a self-perpetuating shutdown nobody asked for.
        """
        if not verdict.tier or verdict.signal_lost:
            self.ledger.clear()
            return []
        excluded = [a for a in agents if verdict.excludes(a, catalog)]
        if not excluded:
            return []
        # DRAIN FROM THE TAIL — lowest survival rank first, so the last agents
        # standing are the ones that can still coordinate a drain. Same key the
        # bring-up order reads (traits.survival_key), reversed, because bring-up
        # takes from the head and spin-down takes from the tail: one comparator,
        # so the two directions cannot disagree about who matters. Getting this
        # backwards asks the administrator to stop first and leaves nobody to
        # supervise the shutdown.
        from .traits import survival_key
        for a in sorted(excluded, key=survival_key(catalog), reverse=True):
            if live is not None and not live(a):
                continue
            prior = self.ledger.told(a.name)
            if prior is not None and prior[0] == episode:
                continue                     # already told, this episode
            body = drain_message(a.name, verdict.tier.at, verdict.pct)
            try:
                self._deliver(a.name, body)
            except Exception as e:           # noqa: BLE001 — one failure is not the sweep
                # LOUD, and NOT recorded as told: an undelivered drain must be
                # retried next pass, and recording it would make the report say
                # "pending" forever about a message that never left.
                self._log(f"⚠ drain: could NOT reach {a.name} "
                          f"({type(e).__name__}: {str(e)[:80]}) — retrying next pass")
                continue
            self.ledger.tell(a.name, episode, self._now(), verdict.tier.at)
            self._log(f"DRAIN {a.name}: told to push and stop "
                      f"({verdict.tier.at}% tier)")
        return self.report(episode)

    def report(self, episode: float) -> list[Drained]:
        """Who drained, what went up, who failed. Read from the STOP RECORDS.

        UNREPORTED IS NOT DRAINED. A told agent with no stop record is `pending`,
        never a silent success — the failure this tier exists to prevent is work
        that only ever existed on one box, and a report that assumed compliance
        would hide exactly that.
        """
        out: list[Drained] = []
        for name in self.ledger.agents():
            told = self.ledger.told(name)
            if told is None or told[0] != episode:
                continue
            stop = self._stops.get(name) if self._stops is not None else None
            if stop is None or stop.at < told[1]:
                # No stop, or a stop from BEFORE we asked — the older record
                # describes a different decision and must not be read as
                # compliance with this one.
                out.append(Drained(name, told[1], PENDING,
                                   why="told, and has not reported a stop yet"))
                continue
            reason = (stop.reason or "").strip()
            if reason.startswith(DRAIN_FAIL):
                out.append(Drained(name, told[1], FAILED,
                                   why=reason[len(DRAIN_FAIL):].strip()
                                   or "reported a failure with no reason"))
            elif reason.startswith(DRAIN_OK):
                out.append(Drained(name, told[1], DRAINED,
                                   pushed=reason[len(DRAIN_OK):].strip()))
            else:
                # It stopped, but did not say what it pushed. That is NOT drained:
                # the report's whole content is the SHAs, and a stop with no
                # report is indistinguishable from an agent that quit.
                out.append(Drained(name, told[1], PENDING,
                                   why=f"stopped without a {DRAIN_OK} report "
                                       f"(reason: {reason or 'none'}) — a stop is "
                                       f"not a push"))
        return out


def render_drain(rows: list[Drained]) -> str:
    if not rows:
        return ""
    by = {DRAINED: [], PENDING: [], FAILED: []}
    for r in rows:
        by.setdefault(r.state, []).append(r)
    lines = ["", "  DRAIN REPORT"]
    for r in sorted(rows, key=lambda x: (x.state, x.agent)):
        detail = r.pushed or r.why
        lines.append(f"    {r.state:<8} {r.agent:<12} {detail}")
    lines.append(f"    {len(by[DRAINED])} drained · {len(by[PENDING])} pending · "
                 f"{len(by[FAILED])} FAILED "
                 f"({'all reported' if not by[PENDING] and not by[FAILED] else 'UNREPORTED IS NOT DRAINED'})")
    return "\n".join(lines)


# --- parsing (called by config.py, which owns the file and the error text) ----

_GOV_KEYS = {"source", "window", "on_signal_lost", "relax_margin",
             "max_age_seconds", "url", "path", "username", "password_file",
             "stub_pct", "tier"}
_TIER_KEYS = {"at", "min_priority", "traits", "action"}


def parse(tbl: dict) -> Policy:
    """[governor] -> Policy. Raises GovernorError; config.py adds the file name.

    UNKNOWN KEYS ARE REFUSED, like every other table in shantytown.toml except
    [env]. A dropped `relax_margin` would leave an operator believing hysteresis
    is configured while the fleet flips tiers every pass, with nothing anywhere
    saying so.
    """
    if not tbl:
        return Policy()
    unknown = sorted(set(tbl) - _GOV_KEYS)
    if unknown:
        raise GovernorError(f"unknown key(s) in [governor]: {', '.join(unknown)}; "
                            f"expected one of: {', '.join(sorted(_GOV_KEYS))}")

    source = tbl.get("source", Policy.source)
    if source not in SOURCES:
        raise GovernorError(f"[governor] source = {source!r} is not one of: "
                            f"{', '.join(SOURCES)}")
    on_lost = tbl.get("on_signal_lost", Policy.on_signal_lost)
    if on_lost not in ON_SIGNAL_LOST:
        raise GovernorError(
            f"[governor] on_signal_lost = {on_lost!r} is not one of: "
            f"{', '.join(ON_SIGNAL_LOST)}. \"warn\" runs the fleet ungoverned and "
            f"alarms; \"freeze\" dispatches nothing new. Neither one drains — a "
            f"probe bug must never be able to stop the crew.")
    window = tbl.get("window", Policy.window)
    if not isinstance(window, str) or not window.strip():
        raise GovernorError(f"[governor] window must be a non-empty string "
                            f"(which {USAGE_METRIC} window governs), got {window!r}")

    margin = _int(tbl, "relax_margin", Policy.relax_margin, minimum=0)
    max_age = _int(tbl, "max_age_seconds", Policy.max_age_seconds, minimum=1)

    for key in ("url", "path", "username", "password_file"):
        v = tbl.get(key)
        if v is not None and not isinstance(v, str):
            raise GovernorError(f"[governor] {key} must be a string, got "
                                f"{type(v).__name__}")
    stub = tbl.get("stub_pct")
    if stub is not None:
        if isinstance(stub, bool) or not isinstance(stub, (int, float)):
            raise GovernorError(f"[governor] stub_pct must be a number 0-100, got "
                                f"{stub!r}")
        stub = float(stub)

    raw_tiers = tbl.get("tier", [])
    if isinstance(raw_tiers, dict):
        raw_tiers = [raw_tiers]
    if not isinstance(raw_tiers, list):
        raise GovernorError("[[governor.tier]] must be an array of tables")
    tiers = tuple(sorted((_tier(t) for t in raw_tiers), key=lambda t: t.at))
    seen = set()
    for t in tiers:
        if t.at in seen:
            raise GovernorError(
                f"[[governor.tier]] declares `at = {t.at}` twice. Two tiers at one "
                f"threshold cannot both engage, and which one wins would be a "
                f"silent choice st made for you.")
        seen.add(t.at)

    return Policy(source=source, window=window.strip(), on_signal_lost=on_lost,
                  relax_margin=margin, max_age_seconds=max_age,
                  url=tbl.get("url"), path=tbl.get("path"),
                  username=tbl.get("username"),
                  password_file=tbl.get("password_file"), stub_pct=stub,
                  tiers=tiers)


def _int(tbl: dict, key: str, default: int, *, minimum: int) -> int:
    v = tbl.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise GovernorError(f"[governor] {key} must be an integer >= {minimum}, "
                            f"got {v!r}")
    return v


def _tier(spec) -> Tier:
    if not isinstance(spec, dict):
        raise GovernorError(f"[[governor.tier]] must be a table, got "
                            f"{type(spec).__name__}")
    unknown = sorted(set(spec) - _TIER_KEYS)
    if unknown:
        raise GovernorError(
            f"unknown key(s) in [[governor.tier]]: {', '.join(unknown)}; expected "
            f"one of: {', '.join(sorted(_TIER_KEYS))}")
    at = spec.get("at")
    if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at <= 100:
        raise GovernorError(f"[[governor.tier]] `at` must be an integer percent "
                            f"0-100, got {at!r}")
    prio = spec.get("min_priority")
    if prio is not None and (isinstance(prio, bool) or not isinstance(prio, int)
                             or prio < 0):
        raise GovernorError(
            f"[[governor.tier]] at = {at}: min_priority must be a non-negative "
            f"integer in your tracker's numbering, where 0 is the HIGHEST "
            f"priority — `min_priority = 1` admits P0 and P1. Got {prio!r}")
    traits = spec.get("traits", [])
    if isinstance(traits, str):
        traits = [traits]
    if not isinstance(traits, list) or not all(isinstance(t, str) and t.strip()
                                               for t in traits):
        raise GovernorError(f"[[governor.tier]] at = {at}: traits must be a list "
                            f"of non-empty strings, got {traits!r}")
    action = spec.get("action")
    if action is not None and action not in ACTIONS:
        raise GovernorError(f"[[governor.tier]] at = {at}: action = {action!r} is "
                            f"not one of: {', '.join(ACTIONS)}")
    if prio is None and not traits and action is None:
        raise GovernorError(
            f"[[governor.tier]] at = {at} restricts NOTHING — no min_priority, no "
            f"traits, no action. A tier that engages and changes no behaviour "
            f"would read as a policy in force while the fleet spends exactly as "
            f"it did before.")
    return Tier(at=at, min_priority=prio,
                traits=tuple(t.strip() for t in traits), action=action)
