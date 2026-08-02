"""The governor's ON ramp — waking on a budget reset (aegis-9mehy).

THE GAP THIS SUITE COVERS was not a missing metric. `claude_usage_reset_
timestamp_seconds` had been published for both windows since aegis-fnd8g; the
governor simply never read it. So the throttle had a carefully-built OFF ramp and
no ON ramp at all: at the 70% tier with seven agents stood down, nothing brought
them back except a human noticing, and the five-hour budget refills at a specific
minute. Every minute after it is capacity bought and not used.

THE LOAD-BEARING TEST IS `test_a_reset_that_did_NOT_drop_the_reading_HOLDS`.
Everything else here is plumbing that can be re-derived by reading the code; that
one pins the decision the feature turns on. It is trivially easy to build this
feature as "wake at the reset, then re-engage the crew" — and that version spends
a whole refreshed budget in minutes the first time a window's semantics differ
from our belief, or a second account is still burning, or the probe is stale. The
timer only ever schedules a READING. The reading decides. An implementation that
passes every other test here and fails that one has built a timer, not a
governor.

Time is injected everywhere and no test sleeps: a suite that had to wait for a
real five-hour window would be a suite nobody runs, which is how the untested
direction becomes the shipped one.
"""
from __future__ import annotations

from shantytown import config, governor as gov, supervisor as sup, tend as tend_mod
from shantytown.protocols import Agent
from shantytown.tmux import NullPanes


# One five_hour tier per spoken threshold plus one seven_day tier, because the
# two budgets refresh on COMPLETELY DIFFERENT CLOCKS (measured on the bead: 95
# minutes vs 70 hours) and a wake armed for one must not be armed for the other.
TIERS = """
[governor]
source = "stub"
relax_margin = 5

[[governor.tier]]
at = 50
window = "five_hour"
min_priority = 1

[[governor.tier]]
at = 70
window = "five_hour"
min_priority = 0

[[governor.tier]]
at = 80
window = "five_hour"
traits = ["support"]

[[governor.tier]]
at = 65
window = "seven_day"
min_priority = 0

[roles.support]
attachment = "reports-to"
survival = "support"
lane = ["monitoring"]
"""

T0 = 1_785_600_000.0          # an arbitrary fixed epoch; nothing derives meaning
FIVE, SEVEN = gov.FIVE_HOUR, gov.SEVEN_DAY


class _Clock:
    """A clock a test MOVES. The whole feature is about what happens at a
    particular moment, so a real clock would make every assertion here a race."""

    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += seconds
        return self.t


def _policy(tmp_path, text: str = TIERS):
    (tmp_path / "shantytown.toml").write_text(text)
    return config.load(tmp_path).governor


def _catalog(tmp_path, text: str = TIERS):
    (tmp_path / "shantytown.toml").write_text(text)
    return config.load(tmp_path).catalog()


def _evaluate(tmp_path, clock, pct, resets, *, text=TIERS, persist=True):
    """ONE governor pass, at whatever the clock currently says.

    Constructed fresh every call on purpose: a tend pass is a process that lives
    for five seconds every five minutes, so state that survived only inside a
    Governor object would be state that never survives. Everything these tests
    assert about memory has to come off disk.
    """
    reader = gov.StubReader(pct=pct, at=clock(), now=clock, resets=resets)
    governor = gov.Governor(_policy(tmp_path, text), reader,
                            gov.FilesGovernorState(tmp_path), now=clock)
    return governor.evaluate(persist=persist)


# --- reading the timestamp ----------------------------------------------------

EXPO = """
claude_usage_utilization_pct{account="a",window="five_hour"} 79
claude_usage_utilization_pct{account="a",window="seven_day"} 31
claude_usage_reset_timestamp_seconds{account="a",window="five_hour"} 1785638401
claude_usage_reset_timestamp_seconds{account="a",window="seven_day"} 1785884401
claude_usage_probe_success{account="a"} 1
claude_usage_probe_timestamp_seconds{account="a"} 1785632700
"""


def test_the_reset_timestamp_is_read_per_window():
    """The producer's real exposition, verbatim from the bead's measurement. Both
    budgets carry their own reset and they are ~68 hours apart — reading one for
    both would put the fleet's re-engagement three days late."""
    readings = gov._readings_by_window(gov.parse_prom(EXPO), "textfile")
    assert readings[FIVE].reset_at == 1785638401
    assert readings[SEVEN].reset_at == 1785884401
    assert readings[FIVE].resets_in(1785638401 - 3600) == 3600


def test_reset_folds_MAX_across_accounts():
    """The governed percentage is the WORST account's (max), so the moment it can
    fall is the LAST account's reset. Taking the earliest would arm a wake that
    reads a still-high number and reports 'the reset did not land' — the right
    outcome reached by a wrong route, and one that costs a whole tend interval."""
    body = ("claude_usage_utilization_pct{account=\"a\",window=\"five_hour\"} 40\n"
            "claude_usage_utilization_pct{account=\"b\",window=\"five_hour\"} 88\n"
            "claude_usage_reset_timestamp_seconds{account=\"a\",window=\"five_hour\"} 100\n"
            "claude_usage_reset_timestamp_seconds{account=\"b\",window=\"five_hour\"} 900\n"
            "claude_usage_probe_success{account=\"a\"} 1\n")
    r = gov._readings_by_window(gov.parse_prom(body), "textfile")[FIVE]
    assert r.pct == 88 and r.reset_at == 900


def test_a_MISSING_reset_is_not_signal_lost():
    """THE DIRECTION THAT MATTERS. A missing ON-ramp input costs promptness —
    tend's five-minute pass still re-engages — and promptness must never be
    allowed to buy the power to blind the governor. If this ever fails, a
    producer that stopped publishing one gauge would idle the whole fleet."""
    r = gov.Reading(pct=42, at=T0, ok=True, source="stub", reset_at=None)
    assert r.lost(T0, 900) == ""
    assert r.resets_in(T0) is None


def test_a_reset_carried_by_a_STALE_reading_is_not_recorded(tmp_path):
    """A reset timestamp beside a percentage we refused to govern by is exactly
    as stale as the percentage. Arming a wake from it would schedule a look based
    on a number we already declined to trust."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 75},
                  {FIVE: clock() + 5400}, text=TIERS)
    assert v.resets[FIVE] == T0 + 5400
    # Same numbers, but the probe timestamp is two hours old.
    reader = gov.StubReader(pct={FIVE: 75}, at=clock() - 7200, now=clock,
                            resets={FIVE: clock() + 5400})
    stale = gov.Governor(_policy(tmp_path), reader,
                         gov.FilesGovernorState(tmp_path), now=clock).evaluate()
    assert stale.signal_lost and stale.resets == {}


# --- THE DECISION: the reading decides, never the clock -----------------------

def test_a_reset_that_did_NOT_drop_the_reading_HOLDS(tmp_path):
    """THE TEST THAT MATTERS (decision 1 of the bead).

    The clock passed the published reset and the fleet is STILL at 75%. Every
    reason that can happen — a second account still burning, window semantics
    other than we believe, a probe that has not re-read — is a reason the budget
    is not actually free. Re-engaging here spends a budget we do not have, in
    minutes, which is the precise failure the governor exists to prevent.

    An implementation that assumes the reset landed passes every other test in
    this file and fails this one.
    """
    clock = _Clock()
    first = _evaluate(tmp_path, clock, {FIVE: 75}, {FIVE: clock() + 5400})
    assert first.tier.at == 70

    clock.advance(5400 + gov.WAKE_SKEW_S)          # the wake fires
    after = _evaluate(tmp_path, clock, {FIVE: 75}, {FIVE: clock() + 18000})
    assert after.tier is not None and after.tier.at == 70, (
        "the tier was released because the CLOCK passed the reset — the timer's "
        "job is to make us look, not to assume")
    assert after.relaxed == ()
    assert after.excludes(Agent(name="tim", roles=("worker",)),
                          _catalog(tmp_path)) == "" or after.floor == 0


def test_a_reset_that_DID_drop_the_reading_releases_and_names_the_window(tmp_path):
    """The refreshed case. The tier goes because the READING fell below
    (threshold - relax_margin); `refreshed` is reported alongside, from an
    observed rollover — a reset we recorded that has passed and been replaced."""
    clock = _Clock()
    assert _evaluate(tmp_path, clock, {FIVE: 75},
                     {FIVE: clock() + 5400}).tier.at == 70

    clock.advance(5400 + gov.WAKE_SKEW_S)
    after = _evaluate(tmp_path, clock, {FIVE: 12}, {FIVE: clock() + 18000})
    assert after.tier is None, "the budget refreshed and the tier did not release"
    assert len(after.relaxed) == 1
    rx = after.relaxed[0]
    assert (rx.window, rx.was, rx.now_at) == (FIVE, 70, None)
    assert rx.refreshed, "a measured rollover was reported as an ordinary fall"
    assert "five_hour" in rx.render()


def test_a_reading_that_falls_WITHOUT_a_rollover_says_so(tmp_path):
    """Usage can also fall because the fleet stopped burning it. That is a
    different sentence, and an operator reading the log at 22:40 should not have
    to guess which one happened."""
    clock = _Clock()
    _evaluate(tmp_path, clock, {FIVE: 75}, {FIVE: clock() + 5400})
    clock.advance(300)                              # nowhere near the reset
    after = _evaluate(tmp_path, clock, {FIVE: 10}, {FIVE: T0 + 5400})
    assert after.tier is None and len(after.relaxed) == 1
    assert not after.relaxed[0].refreshed
    assert "no rollover observed" in after.relaxed[0].render()


def test_hysteresis_applies_ON_THE_WAY_UP_TOO(tmp_path):
    """Decision 5. A window that refreshes to 48% must not re-engage the whole
    fleet into an immediate re-throttle at 50%. Flapping the crew — every agent
    relaunched, every dispatch decision re-taken — is worse than staying down a
    few more minutes."""
    clock = _Clock()
    assert _evaluate(tmp_path, clock, {FIVE: 55}, {FIVE: clock() + 600}).tier.at == 50

    clock.advance(660)
    held = _evaluate(tmp_path, clock, {FIVE: 48}, {FIVE: clock() + 18000})
    assert held.tier is not None and held.tier.at == 50 and held.held, (
        "released at 48% with relax_margin=5 — the fleet comes up and is "
        "throttled again two points later")
    assert held.relaxed == ()

    clock.advance(300)
    out = _evaluate(tmp_path, clock, {FIVE: 44}, {FIVE: clock() + 17700})
    assert out.tier is None and [r.was for r in out.relaxed] == [50]


def test_each_window_refreshes_on_its_own_clock(tmp_path):
    """Decision 6. Both budgets constrain, independently: a five-hour refresh may
    release its tier while the seven-day tier still holds the union."""
    clock = _Clock()
    first = _evaluate(tmp_path, clock, {FIVE: 75, SEVEN: 70},
                      {FIVE: clock() + 5400, SEVEN: clock() + 250_000})
    assert {t.at for t in first.engaged} == {50, 70, 65}

    clock.advance(5460)
    after = _evaluate(tmp_path, clock, {FIVE: 8, SEVEN: 70},
                      {FIVE: clock() + 18000, SEVEN: T0 + 250_000})
    assert [r.window for r in after.relaxed] == [FIVE]
    assert {t.at for t in after.engaged} == {65}, (
        "the five-hour refresh released the WEEKLY budget's tier as well")
    assert after.floor == 0


# --- the wake plan ------------------------------------------------------------

def test_wake_is_armed_from_the_metric_plus_skew(tmp_path):
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 75}, {FIVE: clock() + 5400})
    plan = gov.wake_plan(v, clock())
    assert plan == {FIVE: 5400 + gov.WAKE_SKEW_S}, (
        "waking exactly AT the reset reads a percentage the reset has not "
        "reached yet, which looks like 'it did not land' and holds the tier")


def test_the_wake_is_RE_ARMED_as_the_timestamp_moves(tmp_path):
    """Decision 2. The published timestamp moves; a wake computed an hour ago is
    a wake for the previous window."""
    clock = _Clock()
    first = gov.wake_plan(_evaluate(tmp_path, clock, {FIVE: 75},
                                    {FIVE: clock() + 5400}), clock())
    clock.advance(300)
    second = gov.wake_plan(_evaluate(tmp_path, clock, {FIVE: 76},
                                     {FIVE: clock() + 5400}), clock())
    assert first[FIVE] == second[FIVE] == 5400 + gov.WAKE_SKEW_S
    assert first is not second


def test_only_windows_with_a_tier_engaged_are_armed(tmp_path):
    """A wide-open window has nothing to re-engage. Arming for it buys a tend
    pass for nothing, and a timer that fires forever with no consequence is how a
    mechanism stops being read as meaningful."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 75, SEVEN: 10},
                  {FIVE: clock() + 5400, SEVEN: clock() + 250_000})
    assert set(gov.wake_plan(v, clock())) == {FIVE}


def test_a_past_or_absurd_reset_arms_nothing(tmp_path):
    """THIS pass is the post-reset read, so a reset already gone needs no wake —
    arming for a moment that has passed fires immediately, forever. And a
    timestamp years out is a parse or clock error, not a schedule: trusting it
    would silently replace a five-minute fallback with a wake in 2087."""
    clock = _Clock()
    past = _evaluate(tmp_path, clock, {FIVE: 75}, {FIVE: clock() - 60})
    assert gov.wake_plan(past, clock()) == {}
    absurd = _evaluate(tmp_path, clock, {FIVE: 75}, {FIVE: clock() + 400 * 86400})
    assert gov.wake_plan(absurd, clock()) == {}


def test_signal_lost_arms_no_wake(tmp_path):
    """Waking to re-read a budget we could not read this pass adds no
    information, and SIGNAL LOST already alarms every pass on purpose."""
    clock = _Clock()
    reader = gov.StubReader(pct={FIVE: 75}, at=clock() - 7200, now=clock,
                            resets={FIVE: clock() + 600})
    v = gov.Governor(_policy(tmp_path), reader,
                     gov.FilesGovernorState(tmp_path), now=clock).evaluate()
    assert v.signal_lost and gov.wake_plan(v, clock()) == {}


# --- the timer itself ---------------------------------------------------------

class _Systemd:
    """A fake `systemctl`/`systemd-run` that remembers what is armed. CI has no
    user manager, and a feature only provable on a box with systemd is a feature
    that gets shipped unproven."""

    def __init__(self, fail: bool = False):
        self.calls: list[list[str]] = []
        self.units: set[str] = set()
        self.fail = fail

    def run(self, argv):
        self.calls.append(list(argv))
        if argv[0] == "systemd-run":
            if self.fail:
                return 1
            unit = next(x.split("=", 1)[1] for x in argv if x.startswith("--unit="))
            self.units.add(f"{unit}.timer")
            return 0
        if argv[:3] == ["systemctl", "--user", "stop"]:
            self.units.discard(argv[3])
            return 0
        return 0

    def is_active(self, unit):
        return unit in self.units

    def armings(self):
        return [c for c in self.calls if c[0] == "systemd-run"]


# An ABSOLUTE path, because that is the only kind GovernorWake will arm — see
# `test_a_bare_st_is_REFUSED_rather_than_armed_broken` for why.
ST_BIN = "/usr/local/bin/st"


def _waker(tmp_path, fake, clock):
    return sup.GovernorWake(ST_BIN, tmp_path, run=fake.run,
                            is_active=fake.is_active, now=clock)


def test_sync_arms_a_transient_oneshot_that_runs_a_plain_tend(tmp_path):
    """The unit carries NO policy. It runs `st tend`; the pass it wakes takes a
    fresh reading and that reading decides. Nothing in the scheduler knows what a
    tier is, which is what makes 're-engage on a prediction' unrepresentable
    rather than merely forbidden."""
    fake, clock = _Systemd(), _Clock()
    lines = _waker(tmp_path, fake, clock).sync({FIVE: 5460})
    argv = fake.armings()[0]
    assert argv[0] == "systemd-run" and "--user" in argv
    assert "--unit=st-governor-wake-five-hour" in argv
    assert "--on-active=5460" in argv
    assert f"--setenv={sup.WAKE_ENV}=five_hour" in argv
    assert argv[-4:] == [ST_BIN, "--root", str(tmp_path), "tend"], (
        "the wake runs something other than an ordinary tend pass")
    assert any("ARMED" in ln for ln in lines)


def test_an_unchanged_wake_is_not_re_armed_every_pass(tmp_path):
    """This runs every five minutes. Re-arming an unmoved target would write
    journal lines forever and teach an operator to skim the one pass that
    matters."""
    fake, clock = _Systemd(), _Clock()
    w = _waker(tmp_path, fake, clock)
    w.sync({FIVE: 5460})
    clock.advance(300)
    assert w.sync({FIVE: 5160}) == []            # same fire time, 300s later
    assert len(fake.armings()) == 1


def test_a_KILLED_timer_is_re_armed_on_the_next_pass(tmp_path):
    """Killing the timer degrades to tend's own interval — and then tend puts it
    back. Trusting our own record alone would leave a dead timer recorded as
    armed forever: the exact failure this bead is about, one layer down."""
    fake, clock = _Systemd(), _Clock()
    w = _waker(tmp_path, fake, clock)
    w.sync({FIVE: 5460})
    fake.units.clear()                            # somebody stopped it
    clock.advance(300)
    lines = w.sync({FIVE: 5160})
    assert len(fake.armings()) == 2 and any("ARMED" in ln for ln in lines)
    assert fake.is_active("st-governor-wake-five-hour.timer")


def test_a_moved_reset_re_arms(tmp_path):
    fake, clock = _Systemd(), _Clock()
    w = _waker(tmp_path, fake, clock)
    w.sync({FIVE: 5460})
    assert w.sync({FIVE: 9000}) != []
    assert "--on-active=9000" in fake.armings()[-1]


def test_an_empty_plan_DISARMS(tmp_path):
    """A governor turned off, or a tier released, must not leave a timer firing
    tend passes on behalf of a policy nobody is running."""
    fake, clock = _Systemd(), _Clock()
    w = _waker(tmp_path, fake, clock)
    w.sync({FIVE: 5460})
    lines = w.sync({})
    assert any("DISARMED" in ln for ln in lines)
    assert not fake.is_active("st-governor-wake-five-hour.timer")
    assert w.armed() == {}


def test_a_FAILED_arming_is_loud_and_is_not_recorded_as_armed(tmp_path):
    """No systemd, or a name systemd refuses. It must be retried next pass —
    recording it would make us skip the retry on the strength of a unit that does
    not exist — and it must never raise: the fallback is a five-minute delay, not
    a dead supervisor."""
    fake, clock = _Systemd(fail=True), _Clock()
    w = _waker(tmp_path, fake, clock)
    lines = w.sync({FIVE: 5460})
    assert any("could NOT arm" in ln and "late, not lost" in ln for ln in lines)
    assert w.armed() == {}
    assert len(w.sync({FIVE: 5460})) == 1, "a failed arming was not retried"


def test_a_bare_st_is_REFUSED_rather_than_armed_broken(tmp_path):
    """The lesson from the commit immediately before this one (aegis-408qs), in
    the one place it could recur. systemd --user does not search ~/.local/bin,
    so a unit with a bare name fails 203/EXEC on every fire while the TIMER goes
    on reporting itself healthy — 687 silent failures over two days, last time.

    `systemd-run` resolves the CALLER's PATH, so a bare name WOULD work when a
    human runs tend from a shell and fail from the st-tend unit's minimal
    environment. That is the difference that ships. Refusing is loud and costs
    one tend interval; arming it is silent and costs the feature.
    """
    fake, clock = _Systemd(), _Clock()
    w = sup.GovernorWake("st", tmp_path, run=fake.run,
                         is_active=fake.is_active, now=clock)
    lines = w.sync({FIVE: 5460})
    assert fake.armings() == [], "armed a unit systemd --user cannot exec"
    assert any("could NOT arm" in ln for ln in lines) and w.armed() == {}


def test_the_waker_never_raises_when_systemd_is_absent(tmp_path):
    def _boom(argv):
        raise FileNotFoundError("systemd-run")

    w = sup.GovernorWake(ST_BIN, tmp_path, run=_boom, now=_Clock())
    assert any("could NOT arm" in ln for ln in w.sync({FIVE: 60}))


# --- what re-engagement actually does to the fleet ----------------------------

SUPPORT = Agent(name="ellie", pane="p-ellie", roles=("worker", "support"))
ORDINARY = Agent(name="tim", pane="p-tim", roles=("worker",))
RETIRED = Agent(name="hammond", pane="p-hammond", roles=("worker",), retired=True)


class _Panes(NullPanes):
    """Nothing is live: every agent is DOWN and therefore eligible to be brought
    up, which is the only state in which a governor hold is observable at all —
    tend never kills, so an excluded LIVE agent is left alone."""

    def capture(self, pane, history=0, **kw):
        return "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle)"


class _Runtime:
    name = "fake"

    def __init__(self):
        self.started = []

    def shows_ready_ui(self, screen):
        return "shift+tab to cycle" in screen

    def start(self, card, pane):
        self.started.append(card.name)


def _tend(tmp_path, verdict, agents):
    rt = _Runtime()
    cat = _catalog(tmp_path)
    rep = tend_mod.Tender(
        _Panes(live=set()), rt, None, spawn=rt.start,
        ensure=lambda card: card.workspace, catalog=cat,
        governed=lambda card: verdict.excludes(card, cat)).pass_over(agents)
    return rt, rep


def test_the_relax_makes_HELD_agents_eligible_on_the_same_pass(tmp_path):
    """The whole payoff. Nothing is respawned by the governor — the tier simply
    stops excluding, and tend does what it already does (decision 4: agents shed
    by a tier are PAUSED, never killed, so re-engaging is a state change and not
    a respawn loop)."""
    clock = _Clock()
    throttled = _evaluate(tmp_path, clock, {FIVE: 85}, {FIVE: clock() + 5400})
    rt, rep = _tend(tmp_path, throttled, [SUPPORT, ORDINARY])
    assert rt.started == ["ellie"]
    assert [f.agent for f in rep.findings
            if f.verdict == tend_mod.GOVERNED] == ["tim"]

    clock.advance(5460)
    freed = _evaluate(tmp_path, clock, {FIVE: 12}, {FIVE: clock() + 18000})
    assert freed.relaxed and freed.tier is None
    rt2, _ = _tend(tmp_path, freed, [SUPPORT, ORDINARY])
    assert sorted(rt2.started) == ["ellie", "tim"], (
        "the budget refreshed and the held agent stayed down")


def test_re_engagement_NEVER_resurrects_a_retired_agent(tmp_path):
    """`retired` is a durable human decision and outranks the governor in BOTH
    directions. tend checks retirement before anything can decide to act, so this
    holds by ordering rather than by a check the ON ramp had to remember — and
    that is exactly why it is worth pinning: the guarantee lives in code this
    feature did not touch."""
    clock = _Clock()
    _evaluate(tmp_path, clock, {FIVE: 85}, {FIVE: clock() + 5400})
    clock.advance(5460)
    freed = _evaluate(tmp_path, clock, {FIVE: 5}, {FIVE: clock() + 18000})
    rt, rep = _tend(tmp_path, freed, [ORDINARY, RETIRED])
    assert "hammond" not in rt.started
    assert [f.verdict for f in rep.findings if f.agent == "hammond"] == \
        [tend_mod.RETIRED]


# --- saying which path got us here --------------------------------------------

def _relaxed(**kw):
    base = dict(window=FIVE, was=70, now_at=None, pct=12.0, refreshed=True,
                reset_at=T0 + 18000)
    return gov.Relaxed(**{**base, **kw})


def test_the_log_names_the_WAKE_when_the_timer_fired():
    line = _relaxed().render(woke_for=FIVE, now=T0)
    assert "five_hour reset wake timer" in line and "RELEASED" in line
    assert "next five_hour reset in 5h00m" in line


def test_the_log_names_TEND_when_no_timer_fired():
    """Decision 3. The timer is for promptness and tend's pass is the fallback,
    so a reader must be able to tell which one got us here. If the timer silently
    stopped working we would still re-engage — five minutes late, every time —
    and without this line nothing would ever say so."""
    line = _relaxed().render(woke_for="", now=T0)
    assert "the scheduled tend pass (no wake timer fired)" in line


def test_a_wake_for_a_DIFFERENT_window_is_not_claimed_as_this_one():
    line = _relaxed(window=SEVEN).render(woke_for=FIVE, now=T0)
    assert "woke for a different window" in line


def test_the_line_says_retired_agents_are_not_included():
    assert "`retired` ones do not" in _relaxed().render()


# --- the surfaces -------------------------------------------------------------

def test_the_tend_line_says_HOW_LONG_the_throttle_lasts(tmp_path):
    """Decision 7. 'we are throttled' and 'we are throttled for another 1h35m'
    are different sentences to the person reading them: the first invites
    intervention, the second invites waiting."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 75}, {FIVE: clock() + 5700})
    assert "five_hour resets in 1h35m" in v.render(clock())


def test_the_tend_line_is_SILENT_when_no_reset_is_published(tmp_path):
    """A placeholder printed every pass trains an operator to ignore the field on
    the pass where it matters."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 75}, {})
    assert "resets in" not in v.render(clock())


def test_the_soonest_reset_is_the_one_reported(tmp_path):
    """95 minutes beside 70 hours: the answer to 'how long am I throttled' is 95
    minutes. Reporting the weekly one would be true and useless."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 75, SEVEN: 70},
                  {FIVE: clock() + 5700, SEVEN: clock() + 252_000})
    assert v.next_reset(clock())[0] == FIVE
    assert "five_hour resets in 1h35m" in v.render(clock())


def test_an_UNENGAGED_window_is_not_the_one_you_are_waiting_on(tmp_path):
    """The aegis-cjjdx bug, as measured live: five_hour NOT engaged and refilling
    soon, seven_day ENGAGED and refilling much later.

    Reporting the soonest reset OVERALL named `five_hour` and "3h18m" while the
    thing actually holding the fleet was `seven_day`, 56.6h out — a 17x
    understatement in the most expensive direction, because "clears after lunch"
    is a decision to wait and "clears in 2.4 days" is a decision to re-prioritise.

    five_hour tiers start at 50 and seven_day at 65, so 11/70 engages exactly one.
    """
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 11, SEVEN: 70},
                  {FIVE: clock() + 5700, SEVEN: clock() + 252_000})
    # Precondition: exactly one window is engaged, and it is NOT the soonest.
    assert {t.window for t in v.engaged} == {SEVEN}
    assert v.next_reset(clock())[0] == SEVEN
    assert "seven_day resets in" in v.render(clock())
    assert "five_hour resets in" not in v.render(clock())


def test_no_engaged_window_has_a_readable_reset_reports_nothing(tmp_path):
    """Silence beats naming a window that is not holding anything. An unengaged
    window must never become "this is why you are throttled"."""
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 11, SEVEN: 11},
                  {FIVE: clock() + 5700, SEVEN: clock() + 252_000})
    assert v.next_reset(clock()) is None


def test_fmt_eta_reads_at_a_glance():
    assert gov.fmt_eta(5700) == "1h35m"
    assert gov.fmt_eta(720) == "12m"
    assert gov.fmt_eta(252_000) == "2d 22h"
    assert gov.fmt_eta(None) == "—"


def test_a_reset_in_the_PAST_is_not_rendered_as_zero():
    """`0s` reads as 'about to happen'. A published reset already behind us means
    the producer has not re-read yet, and that is a different fact."""
    assert gov.fmt_eta(-1) == "now"
    assert gov.fmt_eta(-600) == "now (overdue)"


def test_a_past_reset_does_not_render_as_RESETS_IN_NOW(tmp_path):
    """Found by the first live run, not by this suite: gluing "in " onto a
    formatter that can also return an instant printed `five_hour resets in now`.
    The past case is a different sentence, not a degenerate duration, and the
    caller must not have to know which one it is about to get."""
    assert gov.fmt_when(5700) == "in 1h35m"
    assert gov.fmt_when(-1) == "now"
    assert gov.fmt_when(-600) == "now (overdue)"
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 75}, {FIVE: clock() - 1})
    line = v.render(clock())
    assert "resets now" in line and "resets in now" not in line


def test_a_refusal_quotes_the_TIERS_OWN_window_not_the_default_one(tmp_path):
    """The aegis-cjjdx display bug — the one that MANUFACTURED a false bug report.

    `Verdict.pct` is the POLICY'S DEFAULT window (five_hour). `Verdict.tier` can
    belong to a different window. Printed together they read as one measurement:

        the usage governor's 45% tier is engaged (usage 11%)

    Both numbers were true and they described different budgets. Read as a pair
    they say the governor is throttling on a condition that has cleared — a latch
    bug — and two readers reached exactly that conclusion independently, one of
    whom filed it and dispatched the "fix". The proposed remedy was to release the
    tier early: to switch off a spend guard on a budget genuinely 57% consumed.

    So the refusal must name the tier's OWN window and that window's reading.
    five_hour 11% is under every five_hour tier (lowest is 50); seven_day 70% is
    over its 65% tier. Exactly one tier engages, and it is not the default
    window's — so a message quoting `pct` cannot help but be the wrong number.
    """
    clock = _Clock()
    v = _evaluate(tmp_path, clock, {FIVE: 11, SEVEN: 70},
                  {FIVE: clock() + 5700, SEVEN: clock() + 252_000})
    # Precondition: the engaged tier belongs to the NON-default window.
    assert {t.window for t in v.engaged} == {SEVEN}
    assert v.pct == 11                      # unchanged: still the default window

    refusal = v.admits(_ITEM_P2)
    assert "seven_day usage 70%" in refusal, refusal
    # The whole defect: the default window's number must not appear beside a
    # seven_day tier. 11 is five_hour's reading and says nothing about this tier.
    assert "usage 11%" not in refusal, refusal


class _ITEM_P2:
    """The smallest thing `admits` reads: an id and a priority."""
    id = "st-cjjdx"
    priority = 2
