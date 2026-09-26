"""st work jobs — declared scheduled and event-driven work (docs/jobs.md).

Every test is named for the failure it prevents. The ones that must never go
quiet: a broken job file cannot take down the other jobs or the tend pass; a
source that could not be read never moves a cursor; and a job that keeps
failing gives up and TELLS SOMEONE rather than retrying forever or going silent.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from shantytown import cli, jobs


def _write(root: Path, name: str, text: str) -> Path:
    d = root / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.toml"
    p.write_text(text)
    return p


def _job(tmp_path, name, text):
    return jobs.parse(_write(tmp_path, name, text), __import__("tomllib").loads(text))


def _epoch(y, mo, d, h, mi) -> float:
    return datetime(y, mo, d, h, mi).timestamp()


class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


INBOX = """
[trigger]
cron = "43 8 * * *"
[action]
inbox = "hammond"
message = "run the repo-patrol skill"
"""


# --- the file: every refusal names the file AND the field ------------------

@pytest.mark.parametrize("text, field, words", [
    ('[trigger]\ncron = "61 8 * * *"\n[action]\ninbox = "h"\nmessage = "x"\n',
     "trigger.cron", "outside 0-59"),
    ('[trigger]\ncron = "43 8 * *"\n[action]\ninbox = "h"\nmessage = "x"\n',
     "trigger.cron", "4 field"),
    ('[trigger]\ncron = "0 8 * * *"\nevery = "1h"\n[action]\ninbox = "h"\nmessage = "x"\n',
     "trigger", "pick one"),
    ('[trigger]\nevery = "soon"\n[action]\ninbox = "h"\nmessage = "x"\n',
     "trigger.every", "not a duration"),
    ('[trigger]\non = "gh.pr.opened"\n[action]\ninbox = "h"\nmessage = "x"\n',
     "trigger.repo", "owner/name"),
    ('[trigger]\non = "gh.pr.opened"\nrepo = "a/b"\nmatch = { authr_not = "me" }\n'
     '[action]\ninbox = "h"\nmessage = "x"\n', "trigger.match.authr_not", "only"),
    ('[trigger]\non = "slack.message"\n[action]\ninbox = "h"\nmessage = "x"\n',
     "trigger.on", "not a source"),
    ('[trigger]\nmanual = true\n[action]\ndispatch = "h"\ntitle = "t"\n',
     "action.quipu_node", "no_graph_context"),
    ('[trigger]\nmanual = true\n[action]\ninbox = "h"\nexec = "true"\nmessage = "x"\n',
     "action", "exactly one"),
    ('[trigger]\ncron = "0 8 * * *"\n[action]\ninbox = "h"\nmessage = "{{event.title}}"\n',
     "action.message", "needs an `on` trigger"),
    ('[trigger]\nmanual = true\n[action]\ninbox = "h"\nmessage = "{{dat}}"\n',
     "action.message", "unknown placeholder"),
    ('tigger = 1\n[trigger]\nmanual = true\n[action]\ninbox = "h"\nmessage = "x"\n',
     "tigger", "unknown key"),
    ('name = "other"\n[trigger]\nmanual = true\n[action]\ninbox = "h"\nmessage = "x"\n',
     "name", "does not match"),
    ('[trigger]\nmanual = true\n[action]\ninbox = "h"\nmessage = "x"\n[retry]\nmax = -1\n',
     "retry.max", ">= 0"),
])
def test_a_bad_job_file_is_refused_naming_the_file_and_the_field(tmp_path, text, field, words):
    _write(tmp_path, "bad", text)
    loaded, errors = jobs.load(tmp_path)
    assert loaded == []
    assert len(errors) == 1
    msg = str(errors[0])
    assert msg.startswith(f"bad.toml: {field}: "), msg
    assert words in msg, msg


def test_one_broken_file_does_not_hide_the_others(tmp_path):
    _write(tmp_path, "good", INBOX)
    _write(tmp_path, "typo", "[trigger\ncron = ")
    loaded, errors = jobs.load(tmp_path)
    assert [j.name for j in loaded] == ["good"]
    assert str(errors[0]).startswith("typo.toml: toml: ")


def test_the_three_documented_examples_load(tmp_path):
    """docs/jobs.md is the reader's copy of the format; if its examples do not
    load, the reader copies a refusal."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "jobs.md").read_text()
    import re
    import tomllib
    blocks = [b for b in re.findall(r"```toml\n(.*?)```", doc, re.S)
              if b.startswith("# <root>/jobs/")]
    assert len(blocks) == 3
    for b in blocks:
        name = b.split("\n", 1)[0].rsplit("/", 1)[1].removesuffix(".toml")
        jobs.parse(Path(f"{name}.toml"), tomllib.loads(b))


# --- cron -------------------------------------------------------------------

def test_cron_weekday_range_and_hour_list():
    c = jobs.Cron.parse("57 9,13 * * 1-5")
    fri = datetime(2026, 9, 25, 9, 57)           # a Friday
    assert c.matches(fri)
    assert c.matches(fri.replace(hour=13))
    assert not c.matches(fri.replace(hour=11))
    assert not c.matches(datetime(2026, 9, 26, 9, 57)), "Saturday is outside 1-5"
    assert not c.matches(datetime(2026, 9, 27, 13, 57)), "Sunday is outside 1-5"


def test_cron_day_of_week_lists_names_and_seven_is_sunday():
    assert jobs.Cron.parse("0 8 * * sat,sun").matches(datetime(2026, 9, 27, 8, 0))
    assert jobs.Cron.parse("0 8 * * 7").matches(datetime(2026, 9, 27, 8, 0))
    assert jobs.Cron.parse("0 8 * * 0").matches(datetime(2026, 9, 27, 8, 0))
    assert not jobs.Cron.parse("0 8 * * 1,3,5").matches(datetime(2026, 9, 29, 8, 0))


def test_cron_restricted_day_of_month_and_week_is_an_OR():
    """Vixie cron: both restricted means EITHER. The intuitive AND would make
    `0 8 1 * mon` fire only on Mondays that are the 1st."""
    c = jobs.Cron.parse("0 8 1 * mon")
    assert c.matches(datetime(2026, 9, 28, 8, 0))    # a Monday, not the 1st
    assert c.matches(datetime(2026, 10, 1, 8, 0))    # the 1st, a Thursday
    assert not c.matches(datetime(2026, 9, 29, 8, 0))


def test_cron_steps():
    c = jobs.Cron.parse("*/15 8-10/2 * * *")
    assert c.minute == frozenset({0, 15, 30, 45})
    assert c.hour == frozenset({8, 10})


def test_cron_latest_and_next_slot():
    c = jobs.Cron.parse("43 8 * * *")
    now = _epoch(2026, 9, 25, 9, 0)
    assert c.latest(_epoch(2026, 9, 25, 0, 0), now) == _epoch(2026, 9, 25, 8, 43)
    assert c.latest(_epoch(2026, 9, 25, 8, 43), now) is None, "a slot is not re-found"
    assert c.next_after(now) == _epoch(2026, 9, 26, 8, 43)


# --- due-ness, with a fake clock ---------------------------------------------

def _runner(tmp_path, clock, **kw):
    kw.setdefault("sources", jobs.Sources(tmp_path))
    return jobs.Runner(tmp_path, now=clock, detach=False, **kw)


def _recording_inbox(calls, ok=True):
    def act(job, fire, rendered):
        calls.append((job.name, fire.get("reason"), rendered))
        return jobs.Outcome(ok, "sent" if ok else "pane is not there")
    return act


def test_every_fires_first_then_waits_its_interval(tmp_path):
    _write(tmp_path, "tick", '[trigger]\nevery = "6h"\n[action]\ninbox = "h"\nmessage = "go"\n')
    clock, calls = _Clock(_epoch(2026, 9, 25, 9, 0)), []
    r = _runner(tmp_path, clock, actions={"inbox": _recording_inbox(calls)})
    r.sweep()
    assert len(calls) == 1 and "first run" in calls[0][1]
    clock.t += 5 * 3600
    r.sweep()
    assert len(calls) == 1, "fired inside its interval"
    clock.t += 3600
    r.sweep()
    assert len(calls) == 2


def test_cron_fires_once_on_time_and_not_again(tmp_path):
    _write(tmp_path, "patrol", INBOX)
    clock, calls = _Clock(_epoch(2026, 9, 25, 8, 0)), []
    r = _runner(tmp_path, clock, actions={"inbox": _recording_inbox(calls)})
    r.sweep()
    assert calls == [], "fired before its slot"
    clock.t = _epoch(2026, 9, 25, 8, 45)
    r.sweep()
    clock.t += 300
    r.sweep()
    assert len(calls) == 1
    assert calls[0][2]["message"] == "run the repo-patrol skill"


def test_catch_up_runs_a_missed_slot_ONCE_however_many_were_missed(tmp_path):
    _write(tmp_path, "patrol", INBOX)
    clock, calls = _Clock(_epoch(2026, 9, 22, 7, 0)), []
    r = _runner(tmp_path, clock, actions={"inbox": _recording_inbox(calls)})
    r.sweep()                                        # first sight
    clock.t = _epoch(2026, 9, 25, 11, 0)             # asleep across four slots
    r.sweep()
    r.sweep()
    assert len(calls) == 1
    assert "caught up" in calls[0][1]


def test_catch_up_false_skips_a_late_slot_and_waits_for_the_next(tmp_path):
    _write(tmp_path, "patrol", "catch_up = false\n" + INBOX)
    clock, calls = _Clock(_epoch(2026, 9, 25, 7, 0)), []
    r = _runner(tmp_path, clock, actions={"inbox": _recording_inbox(calls)})
    r.sweep()
    clock.t = _epoch(2026, 9, 25, 11, 0)
    r.sweep()
    assert calls == []
    assert r.check(jobs.load(tmp_path)[0][0])[0] == "not due"
    clock.t = _epoch(2026, 9, 26, 8, 44)
    r.sweep()
    assert len(calls) == 1, "skipping one late slot must not skip the next on-time one"


def test_a_when_guard_defers_a_firing_it_never_cancels_it(tmp_path):
    flag = tmp_path / "ready"
    _write(tmp_path, "guarded",
           f'[trigger]\nevery = "1h"\nwhen = "test -f {flag}"\n'
           f'[action]\ninbox = "h"\nmessage = "go"\n')
    clock, calls = _Clock(1_000_000), []
    r = _runner(tmp_path, clock, actions={"inbox": _recording_inbox(calls)})
    r.sweep()
    assert calls == []
    flag.write_text("")
    clock.t += 300
    r.sweep()
    assert len(calls) == 1


def test_a_when_trigger_fires_on_the_edge_not_every_pass(tmp_path):
    flag = tmp_path / "ready"
    _write(tmp_path, "edge", f'[trigger]\nwhen = "test -f {flag}"\n'
                             f'[action]\ninbox = "h"\nmessage = "go"\n')
    clock, calls = _Clock(1_000_000), []
    r = _runner(tmp_path, clock, actions={"inbox": _recording_inbox(calls)})
    r.sweep()
    flag.write_text("")
    r.sweep()
    r.sweep()
    assert len(calls) == 1, "a condition that stays true is one event, not one per pass"
    flag.unlink()
    r.sweep()
    flag.write_text("")
    r.sweep()
    assert len(calls) == 2


def test_a_manual_job_never_fires_on_its_own(tmp_path):
    _write(tmp_path, "m", '[trigger]\nmanual = true\n[action]\ninbox = "h"\nmessage = "go"\n')
    calls = []
    r = _runner(tmp_path, _Clock(1_000_000), actions={"inbox": _recording_inbox(calls)})
    r.sweep()
    assert calls == []
    ok, _ = r.run_now(jobs.load(tmp_path)[0][0])
    assert ok and len(calls) == 1


def test_a_host_scoped_job_runs_only_on_its_host(tmp_path):
    _write(tmp_path, "mac-only", 'host = "mac"\n[trigger]\nevery = "1h"\n'
                                 '[action]\ninbox = "h"\nmessage = "go"\n')
    calls = []
    for host in ("vati", None):
        _runner(tmp_path, _Clock(1_000_000), host=host,
                actions={"inbox": _recording_inbox(calls)}).sweep()
    assert calls == [], "ran on a host it was not scoped to (or on an undeclared one)"
    _runner(tmp_path, _Clock(1_000_000), host="mac",
            actions={"inbox": _recording_inbox(calls)}).sweep()
    assert len(calls) == 1


# --- events: the cursor moves only on a real answer ---------------------------

PR_JOB = """
[trigger]
on = "gh.pr.opened"
repo = "scbrown/pixelsrc"
match = { author_not = "scbrown" }
[action]
dispatch = "hammond"
title = "triage #{{event.number}}: {{event.title}}"
body = "by {{event.author}} {{event.url}}"
no_graph_context = "outside PRs are not modelled"
"""


class _Gh:
    """A stubbed `gh` runner: the PR list it returns is whatever the test says."""

    def __init__(self):
        self.prs, self.fail, self.calls = [], False, []

    def __call__(self, argv, timeout, **kw):
        self.calls.append(argv)
        if self.fail:
            return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 502")
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps([
            {"number": n, "title": t, "author": {"login": who},
             "url": f"https://github.com/scbrown/pixelsrc/pull/{n}"}
            for n, t, who in self.prs]))


def test_gh_source_seeds_then_fires_only_for_new_outside_prs(tmp_path):
    _write(tmp_path, "pr-triage", PR_JOB)
    gh, calls = _Gh(), []
    gh.prs = [(40, "old one", "someone"), (41, "older", "scbrown")]
    r = _runner(tmp_path, _Clock(1_000_000), sources=jobs.Sources(tmp_path, run=gh),
                actions={"dispatch": _recording_inbox(calls)})
    r.sweep()
    assert calls == [], "a new job dispatched the backlog"
    assert jobs.read_state(tmp_path, "pr-triage")["cursor"] == 41

    gh.prs = [(43, "fix palette", "outsider"), (42, "bump", "scbrown")] + gh.prs
    r.sweep()
    assert len(calls) == 1
    rendered = calls[0][2]
    assert rendered["title"] == "triage #43: fix palette"
    assert rendered["body"] == "by outsider https://github.com/scbrown/pixelsrc/pull/43"
    assert jobs.read_state(tmp_path, "pr-triage")["cursor"] == 43
    assert gh.calls[-1][:3] == ["gh", "pr", "list"] and "scbrown/pixelsrc" in gh.calls[-1]

    r.sweep()
    assert len(calls) == 1, "the same PR fired twice"


def test_gh_down_does_not_move_the_cursor(tmp_path):
    _write(tmp_path, "pr-triage", PR_JOB)
    gh, calls = _Gh(), []
    gh.prs = [(40, "old", "x")]
    r = _runner(tmp_path, _Clock(1_000_000), sources=jobs.Sources(tmp_path, run=gh),
                actions={"dispatch": _recording_inbox(calls)})
    r.sweep()
    gh.fail = True
    lines = r.sweep()
    assert any("could not evaluate" in ln and "HTTP 502" in ln for ln in lines)
    assert jobs.read_state(tmp_path, "pr-triage")["cursor"] == 40
    gh.fail = False
    gh.prs = [(41, "arrived while gh was down", "outsider")] + gh.prs
    r.sweep()
    assert [c[2]["title"] for c in calls] == ["triage #41: arrived while gh was down"]


def test_bead_closed_fires_once_per_newly_closed_bead(tmp_path):
    _write(tmp_path, "on-close", '[trigger]\non = "bead.closed"\nmatch = { label = "release" }\n'
                                 '[action]\ninbox = "h"\nmessage = "{{event.id}} closed"\n')
    rows = [{"id": "st-1", "title": "a", "status": "closed", "labels": ["release"]},
            {"id": "st-2", "title": "b", "status": "open", "labels": ["release"]}]
    calls = []
    r = _runner(tmp_path, _Clock(1_000_000),
                sources=jobs.Sources(tmp_path, bead_rows=lambda: rows),
                actions={"inbox": _recording_inbox(calls)})
    r.sweep()
    assert calls == []
    rows[1]["status"] = "closed"
    r.sources = jobs.Sources(tmp_path, bead_rows=lambda: rows)   # a fresh pass
    r.sweep()
    assert [c[2]["message"] for c in calls] == ["st-2 closed"]


def test_quipu_seeding_pages_to_the_head_and_fires_nothing(tmp_path):
    _write(tmp_path, "graph", '[trigger]\non = "quipu.tx"\nmatch = { source = "infra*" }\n'
                              '[action]\ninbox = "h"\nmessage = "tx {{event.id}}"\n')

    class _Q:
        def __init__(self, head):
            self.head = head

        def transactions_since(self, since, limit=1000):
            ids = range(since + 1, min(self.head, since + limit) + 1)
            return [SimpleNamespace(id=i, actor="a", source="infra/x" if i % 2 else "other",
                                    timestamp="") for i in ids]
    q, calls = _Q(2500), []
    r = _runner(tmp_path, _Clock(1_000_000), sources=jobs.Sources(tmp_path, quipu=q),
                actions={"inbox": _recording_inbox(calls)})
    r.sweep()
    assert calls == [] and jobs.read_state(tmp_path, "graph")["cursor"] == 2500
    q.head = 2503
    r.sweep()
    assert [c[2]["message"] for c in calls] == ["tx 2501", "tx 2503"]


# --- retry, backoff, and the administrator ------------------------------------

def test_a_failing_job_backs_off_then_gives_up_and_tells_the_administrator(tmp_path):
    _write(tmp_path, "flaky", '[trigger]\nevery = "1d"\n[action]\ninbox = "hammond"\n'
                              'message = "go"\n[retry]\nmax = 2\nbackoff = "60s"\n')
    clock, calls, told = _Clock(1_000_000), [], []
    r = _runner(tmp_path, clock, actions={"inbox": _recording_inbox(calls, ok=False)},
                escalate=lambda job, text: told.append(text) or True)
    lines = r.sweep()
    assert len(calls) == 1 and any("retrying in 1m" in ln for ln in lines)
    clock.t += 30
    r.sweep()
    assert len(calls) == 1, "retried before its backoff"
    clock.t += 30
    lines = r.sweep()
    assert len(calls) == 2 and any("retrying in 2m" in ln for ln in lines), "no doubling"
    clock.t += 120
    lines = r.sweep()
    assert len(calls) == 3
    assert any("GAVE UP after 3" in ln and "administrator was told" in ln for ln in lines)
    assert len(told) == 1 and told[0].startswith("[job flaky] gave up after 3 attempt(s)")
    assert "pane is not there" in told[0]

    rows = jobs.read_ledger(tmp_path, "flaky")
    assert [(r_["attempt"], r_["ok"]) for r_ in rows] == [(1, False), (2, False), (3, False)]
    assert rows[-1]["final"] and rows[-1]["escalated"]
    # Given up means DONE: the next firing waits a whole interval, it does not
    # restart the retry loop on the very next pass.
    clock.t += 300
    r.sweep()
    assert len(calls) == 3


def test_a_give_up_that_could_not_reach_the_administrator_says_so(tmp_path):
    _write(tmp_path, "flaky", '[trigger]\nevery = "1d"\n[action]\ninbox = "h"\n'
                              'message = "go"\n[retry]\nmax = 0\n')
    r = _runner(tmp_path, _Clock(1_000_000), actions={"inbox": _recording_inbox([], ok=False)},
                escalate=lambda job, text: False)
    lines = r.sweep()
    assert any("COULD NOT tell the administrator" in ln for ln in lines)
    assert jobs.read_ledger(tmp_path, "flaky")[-1]["escalated"] is False


def test_a_success_after_a_failure_ends_the_episode(tmp_path):
    _write(tmp_path, "flaky", '[trigger]\nevery = "1d"\n[action]\ninbox = "h"\n'
                              'message = "go"\n[retry]\nbackoff = "60s"\n')
    clock, results = _Clock(1_000_000), [False, True]

    def act(job, fire, rendered):
        return jobs.Outcome(results.pop(0), "x")
    told = []
    r = _runner(tmp_path, clock, actions={"inbox": act},
                escalate=lambda j, t: told.append(t))
    r.sweep()
    clock.t += 60
    lines = r.sweep()
    assert any(ln.startswith("job flaky: ok") for ln in lines)
    st = jobs.read_state(tmp_path, "flaky")
    assert st["pending"] == [] and st["last_ok"] == clock.t and told == []


# --- isolation: nothing a job does reaches the rest of the pass ---------------

def test_a_crashing_job_does_not_stop_the_other_jobs(tmp_path, monkeypatch):
    _write(tmp_path, "a-boom", '[trigger]\nevery = "1h"\n[action]\ninbox = "h"\nmessage = "x"\n')
    _write(tmp_path, "b-fine", '[trigger]\nevery = "1h"\n[action]\ninbox = "h"\nmessage = "y"\n')
    _write(tmp_path, "c-broken", "[trigger]\nevery = \n")
    real = jobs.evaluate

    def evaluate(job, *a, **k):
        if job.name == "a-boom":
            raise ZeroDivisionError("a bug in one job")
        return real(job, *a, **k)
    monkeypatch.setattr(jobs, "evaluate", evaluate)
    monkeypatch.setattr(jobs.Runner, "tick", _tick_without_eval_guard)
    calls = []
    lines = _runner(tmp_path, _Clock(1_000_000),
                    actions={"inbox": _recording_inbox(calls)}).sweep()
    assert any("a-boom CRASHED" in ln for ln in lines)
    assert any("c-broken.toml" in ln for ln in lines)
    assert [c[0] for c in calls] == ["b-fine"]


def _tick_without_eval_guard(self, job):
    """tick() guards evaluate() itself; to prove the SWEEP-level isolation holds
    even for a crash that guard does not catch, re-raise from one level up."""
    st = jobs.read_state(self.root, job.name)
    st.setdefault("since", self.now())
    d = jobs.evaluate(job, st, self.now(), root=self.root, sources=self.sources)
    st.update(d.observe)
    st.setdefault("pending", []).extend(
        dict(f, attempts=0, retry_at=0.0) for f in d.fires)
    jobs.write_state(self.root, job.name, st)
    return self._drain(job, st)


def test_the_tend_pass_survives_a_crashing_jobs_sweep(tmp_path, monkeypatch, capsys):
    """The jobs sweep sits inside tend's `_sweep`, like every other best-effort
    layer (aegis-ey7n): a failure there is one loud line, never a dead pass."""
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "w.json").write_text(json.dumps({"role": "worker", "pane": "p-w"}))
    from tests.test_tend import IDLE, _Panes
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(screens={"p-w": IDLE}))

    def boom(a):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cli, "_jobs_sweep", boom)

    class _A:
        root = tmp_path; dry_run = False
        backend = "files"; repo = None; registry = "files"

    rc = cli._tend_once(_A())
    err = capsys.readouterr().err
    assert "the jobs sweep CRASHED" in err
    assert rc in (cli.OK, cli.CANNOT_TELL)


def test_the_tend_pass_runs_a_due_job(tmp_path, monkeypatch, capsys):
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "w.json").write_text(json.dumps({"role": "worker", "pane": "p-w"}))
    from tests.test_tend import IDLE, _Panes
    monkeypatch.setattr(cli, "Tmux", lambda *_a, **_k: _Panes(screens={"p-w": IDLE}))
    _write(tmp_path, "nudge", '[trigger]\nevery = "1h"\n[action]\ninbox = "w"\nmessage = "hi"\n')
    sent = []
    monkeypatch.setattr(cli, "_job_st", lambda a, argv: (sent.append(argv), (cli.OK, "sent"))[1])

    class _A:
        root = tmp_path; dry_run = False
        backend = "files"; repo = None; registry = "files"

    cli._tend_once(_A())
    assert sent == [["inbox", "w", "[job nudge] hi"]]
    assert "job nudge: ok" in capsys.readouterr().err


# --- the actions go through the real commands ------------------------------------

def test_the_inbox_action_is_the_st_inbox_handler(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_cmd_inbox", lambda sub: (seen.append(sub), cli.OK)[1])
    a = SimpleNamespace(root=tmp_path, root_how="test", backend="files", repo=None,
                        registry="files")
    job = _job(tmp_path, "patrol", INBOX.replace('message', 'durable = true\nmessage'))
    out = cli._job_inbox(a)(job, {}, {"to": "hammond", "message": "run it", "durable": True})
    assert out.ok
    sub = seen[0]
    assert (sub.cmd, sub.agent, sub.durable) == ("inbox", "hammond", True)
    assert sub.message == ["[job patrol] run it"]
    assert sub.root == tmp_path


def test_a_refused_inbox_is_a_failed_run_with_the_reason(tmp_path, monkeypatch):
    def refuse(sub):
        print("  refused: hammond has no pane in the registry", file=__import__("sys").stderr)
        return cli.REFUSED
    monkeypatch.setattr(cli, "_cmd_inbox", refuse)
    a = SimpleNamespace(root=tmp_path, root_how="t", backend="files", repo=None, registry="files")
    job = _job(tmp_path, "patrol", INBOX)
    out = cli._job_inbox(a)(job, {}, {"to": "hammond", "message": "x", "durable": False})
    assert not out.ok and "no pane" in out.detail


def test_dispatch_creates_the_item_ONCE_across_retries(tmp_path, monkeypatch):
    created, gone = [], []

    class _T:
        def create(self, title, **fields):
            created.append((title, fields))
            return SimpleNamespace(id=f"st-{len(created)}")
    monkeypatch.setattr(cli, "_tracker", lambda a, *k: _T())
    results = [cli.REFUSED, cli.OK]
    monkeypatch.setattr(cli, "_job_st",
                        lambda a, argv: (gone.append(argv), (results.pop(0), "busy"))[1])
    a = SimpleNamespace(root=tmp_path)
    job = _job(tmp_path, "pr-triage", PR_JOB)
    fire = {"reason": "gh.pr.opened #43"}
    r = {"to": "hammond", "title": "triage #43", "body": "b", "labels": "",
         "priority": None, "quipu_nodes": [], "no_graph_context": "outside PRs"}
    assert not cli._job_dispatch(a)(job, fire, r).ok
    assert cli._job_dispatch(a)(job, fire, r).ok
    assert len(created) == 1, "a retry created a second copy of the work"
    assert gone[0][:3] == ["go", "st-1", "hammond"] and gone[1][:3] == ["go", "st-1", "hammond"]
    assert ["--no-graph-context", "outside PRs"] == gone[0][3:5]


def test_exec_runs_detached_and_is_collected_on_a_later_pass(tmp_path):
    _write(tmp_path, "sh", '[trigger]\nevery = "1h"\n[action]\nexec = "echo {{job}} $ST_JOB"\n')
    spawned = []

    def spawn(argv, timeout_s, result, log, env, cwd):
        spawned.append(argv)
        Path(result).write_text(json.dumps(jobs.run_exec(argv, timeout_s, log_path=log,
                                                         env=env, cwd=cwd)))
        return 999_999_999                           # no such pid: "exited"
    clock = _Clock(1_000_000)
    r = jobs.Runner(tmp_path, now=clock, spawn=spawn)
    assert r.sweep() == ["job sh: started (every 1h (first run))"]
    assert spawned == [["/bin/sh", "-c", "echo sh $ST_JOB"]]
    lines = r.sweep()
    assert lines and lines[0].startswith("job sh: ok")
    assert (tmp_path / "jobs" / ".state" / "sh.log").read_text() == "sh sh\n"


def test_exec_timeout_kills_the_command_and_fails_the_run(tmp_path):
    res = jobs.run_exec(["/bin/sh", "-c", "sleep 5"], 0.3)
    assert not res["ok"] and "timed out" in res["error"]
    assert res["end"] - res["start"] < 3


def test_a_runner_that_died_without_a_result_is_a_failure_not_a_hang(tmp_path):
    _write(tmp_path, "sh", '[trigger]\nevery = "1h"\n[action]\nexec = "true"\n[retry]\nmax = 0\n')
    r = jobs.Runner(tmp_path, now=_Clock(1_000_000), spawn=lambda *a: 999_999_999,
                    escalate=lambda j, t: True)
    r.sweep()
    lines = r.sweep()
    assert any("without a result" in ln for ln in lines)


# --- the CLI ----------------------------------------------------------------------

def test_cli_list_shows_trigger_next_due_and_action(tmp_path, capsys):
    _write(tmp_path, "repo-patrol", INBOX)
    rc = cli.main(["--root", str(tmp_path), "work", "jobs", "list"])
    out = capsys.readouterr().out
    assert rc == cli.OK
    assert "repo-patrol" in out and "cron 43 8 * * *" in out
    assert "inbox hammond" in out and "never ran" in out


def test_cli_list_exits_1_and_names_a_broken_file(tmp_path, capsys):
    _write(tmp_path, "repo-patrol", INBOX)
    _write(tmp_path, "bad", '[trigger]\ncron = "61 8 * * *"\n[action]\ninbox = "h"\nmessage = "x"\n')
    rc = cli.main(["--root", str(tmp_path), "work", "jobs"])
    captured = capsys.readouterr()
    assert rc == cli.REFUSED
    assert "bad.toml: trigger.cron:" in captured.err
    assert "repo-patrol" in captured.out, "a broken file hid the good ones"


def test_cli_check_says_what_would_fire_and_writes_nothing(tmp_path, capsys):
    _write(tmp_path, "tick", '[trigger]\nevery = "1h"\n[action]\ninbox = "h"\nmessage = "x"\n')
    _write(tmp_path, "later", '[trigger]\ncron = "0 0 1 1 *"\n[action]\ninbox = "h"\nmessage = "x"\n')
    rc = cli.main(["--root", str(tmp_path), "work", "jobs", "check"])
    out = capsys.readouterr().out
    assert rc == cli.OK
    assert "tick" in out and "would fire" in out and "first run" in out
    assert "later" in out and "not due" in out
    assert not (tmp_path / "jobs" / ".state").exists(), "check wrote state"


def test_cli_run_dry_run_renders_and_history_reads_the_ledger(tmp_path, capsys):
    _write(tmp_path, "tick", '[trigger]\nmanual = true\n[action]\nexec = ["echo", "{{date}}"]\n')
    assert cli.main(["--root", str(tmp_path), "work", "jobs", "run", "tick", "-n"]) == cli.OK
    assert "would run" in capsys.readouterr().out
    assert cli.main(["--root", str(tmp_path), "work", "jobs", "run", "tick"]) == cli.OK
    assert cli.main(["--root", str(tmp_path), "work", "jobs", "history", "tick"]) == cli.OK
    out = capsys.readouterr().out
    assert "manual: st work jobs run" in out and " ok " in out
    assert cli.main(["--root", str(tmp_path), "work", "jobs", "run", "nope"]) == cli.REFUSED


def test_parse_duration_reads_the_systemd_spelling_the_flag_always_took():
    assert jobs.parse_duration("5min") == 300
    assert jobs.parse_duration("90s") == 90
    assert jobs.parse_duration(300) == 300
    with pytest.raises(ValueError):
        jobs.parse_duration("five minutes")
    with pytest.raises(ValueError):
        jobs.parse_duration(0)
