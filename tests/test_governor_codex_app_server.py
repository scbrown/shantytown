"""Ask Codex for the account's limits instead of scraping what a session wrote.

aegis-ftsqh / aegis-1jmy9. `codex app-server` answers `account/rateLimits/read`
with the WHOLE account's metered state. Verified live against codex-cli 0.147.0
on 2026-08-24; the shapes below are the ones the CLI's own
`generate-json-schema` emits (`GetAccountRateLimitsResponse`,
`RateLimitSnapshot`, `RateLimitWindow`), not shapes anyone remembered.

WHY THIS SOURCE EXISTS ALONGSIDE THE SCRAPE, WHICH IS NOT BROKEN. The scrape
reads server-provided numbers and its staleness handling is sound. But its
freshness is not something the governor can cause: a session file is only as new
as the last Codex response, so on a quiet fleet the reading ages out and the
window goes SIGNAL LOST — and the fleet is quietest right after the governor
throttles it. Measured the same day: `~/.codex` yielded a 197-hour-old reading
while the deployment root yielded a 48-minute-old one. A pull breaks that loop.

Every test here drives a FAKE app-server, so the suite makes no network call and
needs no codex on the box. The live call is recorded on the bead.
"""
from __future__ import annotations

import json
import sys

from shantytown import governor as gov


def _fake_server(tmp_path, response, *, name="fake", exit_first=False):
    """A script that speaks just enough of the protocol to answer id 2.

    It READS ITS STDIN LINE BY LINE and keeps running, because the real thing
    does too — and a fake that answered on EOF would hide the bug that
    `subprocess.run(input=...)` closes stdin and gets an empty answer from a
    perfectly healthy server.
    """
    payload = tmp_path / f"{name}.json"
    payload.write_text(json.dumps(response))
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import json, sys\n"
        f"if {exit_first!r}:\n"
        "    sys.exit(3)\n"
        f"reply = open({str(payload)!r}).read()\n"
        "for line in sys.stdin:\n"
        "    try:\n"
        "        msg = json.loads(line)\n"
        "    except ValueError:\n"
        "        continue\n"
        "    if msg.get('id') == 1:\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {}}), flush=True)\n"
        "    elif msg.get('id') == 2:\n"
        "        print(reply, flush=True)\n"
    )
    return [sys.executable, str(script)]


def _ok(response, tmp_path, **kw):
    return gov.CodexAppServerReader(command=_fake_server(tmp_path, response),
                                    timeout=20, **kw)


_LIVE = {
    "jsonrpc": "2.0", "id": 2,
    "result": {
        "rateLimits": {
            "limitId": "codex", "limitName": None,
            "primary": {"usedPercent": 12, "windowDurationMins": 10080,
                        "resetsAt": 1788137002},
            "secondary": None, "planType": "pro",
        },
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "primary": {"usedPercent": 12, "windowDurationMins": 10080,
                            "resetsAt": 1788137002},
                "secondary": None,
            },
            "codex_bengalfox": {
                "limitId": "codex_bengalfox", "limitName": "GPT-5.3-Codex-Spark",
                "primary": {"usedPercent": 0, "windowDurationMins": 300,
                            "resetsAt": 1787565328},
                "secondary": {"usedPercent": 0, "windowDurationMins": 10080,
                              "resetsAt": 1788152128},
            },
        },
    },
}


def test_it_reads_the_pinned_bucket_and_the_reading_is_AS_FRESH_AS_THE_CALL(tmp_path):
    """The whole reason for this source. The scrape can only report when someone
    else last wrote a file; this reports now."""
    import time
    before = time.time()
    reading = _ok(_LIVE, tmp_path, window="seven_day").read()
    assert reading.pct == 12.0
    assert reading.limit_id == "codex"
    assert reading.reset_at == 1788137002.0
    assert reading.at >= before, "the reading is not stamped with the call"
    assert time.time() - reading.at < 30


def test_the_buckets_are_kept_APART(tmp_path):
    """`codex` has no five-hour window and Spark does. A source that merged them
    would answer "five_hour: 0%" for codex — true of a limit nobody asked about."""
    codex = _ok(_LIVE, tmp_path, window="five_hour", limit_id="codex").read()
    assert codex.pct is None
    assert "five_hour" in codex.error and "seven_day" in codex.error, codex.error

    spark = _ok(_LIVE, tmp_path, window="five_hour",
                limit_id="codex_bengalfox").read()
    assert spark.pct == 0.0
    assert spark.limit_id == "codex_bengalfox"


def test_an_unknown_bucket_NAMES_the_ones_that_exist(tmp_path):
    err = _ok(_LIVE, tmp_path, limit_id="nope").read().error
    assert "nope" in err and "codex_bengalfox" in err, err


def test_the_default_bucket_is_PINNED_not_whatever_comes_first(tmp_path):
    """Dict order is not a governance decision."""
    reader = _ok(_LIVE, tmp_path, window="seven_day")
    assert reader.limit_id == "codex"
    assert reader.read().limit_id == "codex"


def test_a_missing_codex_is_a_clear_error_and_never_a_number(tmp_path):
    reading = gov.CodexAppServerReader(command=["definitely-not-a-binary-x9"]).read()
    assert reading.pct is None
    assert "not on PATH" in reading.error


def test_a_server_that_dies_is_reported_not_guessed(tmp_path):
    cmd = _fake_server(tmp_path, _LIVE, name="dead", exit_first=True)
    reading = gov.CodexAppServerReader(command=cmd, timeout=10).read()
    assert reading.pct is None
    assert reading.error, "a dead server produced neither a number nor a reason"


def test_a_jsonrpc_error_is_surfaced_verbatim(tmp_path):
    err_resp = {"jsonrpc": "2.0", "id": 2,
                "error": {"code": -32000, "message": "not signed in"}}
    reading = _ok(err_resp, tmp_path).read()
    assert reading.pct is None
    assert "not signed in" in reading.error, reading.error


def test_a_window_it_cannot_map_is_reported_not_dropped(tmp_path):
    resp = {"jsonrpc": "2.0", "id": 2, "result": {"rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 6, "windowDurationMins": 43200,
                    "resetsAt": 1790000000},
        "secondary": None}}}
    reader = _ok(resp, tmp_path, window="seven_day")
    err = reader.read().error
    assert "43200" in err, err


def test_the_compat_single_view_is_used_ONLY_for_its_own_limit(tmp_path):
    """`rateLimits` is the documented backward-compatible single-bucket view. It
    is a fallback, never a substitute for a DIFFERENT limit that was asked for."""
    resp = {"jsonrpc": "2.0", "id": 2, "result": {"rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 12, "windowDurationMins": 10080,
                    "resetsAt": 1788137002},
        "secondary": None}}}
    assert _ok(resp, tmp_path, window="seven_day").read().pct == 12.0
    other = _ok(resp, tmp_path, window="seven_day",
                limit_id="codex_bengalfox").read()
    assert other.pct is None, "another limit's numbers were served as Spark's"


def test_the_source_is_selectable_from_config(tmp_path):
    assert "codex_app_server" in gov.SOURCES
    policy = gov.Policy(source="codex_app_server", window="seven_day",
                        limit_id="codex_bengalfox")
    reader = gov.reader_for(policy, now=0.0)
    assert isinstance(reader, gov.CodexAppServerReader)
    assert reader.limit_id == "codex_bengalfox"


# --- the reader's error must survive read_all (aegis-tq8um5) ------------------
#
# THE INCIDENT. 2026-09-03 10:47-11:17 EDT, one 33.75-minute episode: OpenAI's
# `GET /backend-api/wham/usage` answered 404 (reproduced by hand on BOTH
# codex 0.147.0 and 0.153.0, so not a client-version fault). `_rpc` caught that
# and wrote it down exactly:
#
#     account/rateLimits/read failed: GET https://chatgpt.com/backend-api/wham/
#     usage failed: 404 Not Found
#
# and `read_all` THREW THE STRING AWAY, returning `{}`. `Governor.evaluate`
# then synthesised the only thing an empty dict can mean to it —
# `no usage series for window 'seven_day'` — which is the message for a MISSING
# PROMETHEUS SERIES. Two agents spent an hour reconstructing an upstream outage
# the producer had already diagnosed in one line, and one of them (correctly
# labelled, still wrong) blamed a local binary swap.
#
# The distinction is not cosmetic: "the provider 404'd" is not actionable on
# this host and "nothing is publishing a series" is.


def _erroring_server(tmp_path, message, *, name="erroring"):
    """A fake app-server that answers id 2 with a JSON-RPC ERROR, as the real
    one did throughout the outage."""
    return _fake_server(tmp_path, {
        "jsonrpc": "2.0", "id": 2,
        "error": {"code": -32603, "message": message},
    }, name=name)


UPSTREAM_404 = ("failed to fetch codex rate limits: "
                "GET https://chatgpt.com/backend-api/wham/usage failed: "
                "404 Not Found")


def test_read_all_carries_the_reader_error_rather_than_an_empty_dict(tmp_path):
    """`read()` has always reported this; `read_all()` dropped it, and
    `evaluate` prefers `read_all()` when the reader has one."""
    reader = gov.CodexAppServerReader(
        command=_erroring_server(tmp_path, UPSTREAM_404), timeout=20)

    assert "404 Not Found" in reader.read().error          # the path that worked
    readings, error, fault = reader.read_all_detailed()    # the path evaluate takes
    assert readings == {}
    assert "404 Not Found" in error
    # It ran, it spoke the protocol, it was told no — that is UPSTREAM, decided
    # structurally and NOT by looking for "404" in someone else's wording.
    assert fault == gov.UPSTREAM


def test_the_governor_reports_the_upstream_error_not_a_missing_series(tmp_path):
    """End to end, through the seam `evaluate` actually uses."""
    (tmp_path / "shantytown.toml").write_text(
        '[governor]\nsource = "stub"\nwindow = "seven_day"\n'
        '[[governor.tier]]\nat = 50\nwindow = "seven_day"\nmax_agents = 4\n')
    from shantytown import config
    policy = config.load(tmp_path).governor
    reader = gov.CodexAppServerReader(
        command=_erroring_server(tmp_path, UPSTREAM_404, name="e2e"),
        window=gov.SEVEN_DAY, timeout=20)

    v = gov.Governor(policy, reader, gov.FilesGovernorState(tmp_path),
                     now=lambda: 1_000_000.0).evaluate(persist=False)

    assert v.signal_lost
    assert "404 Not Found" in v.why, v.why
    # ...and it must NOT claim the series is missing, which sends the responder
    # to a Prometheus config for a fault that is off this host entirely.
    assert "no usage series" not in v.why, v.why


def test_the_fault_label_is_upstream_when_the_provider_answered_an_error(tmp_path):
    (tmp_path / "shantytown.toml").write_text(
        '[governor]\nsource = "stub"\nwindow = "seven_day"\n'
        '[[governor.tier]]\nat = 50\nwindow = "seven_day"\nmax_agents = 4\n')
    from shantytown import config
    reader = gov.CodexAppServerReader(
        command=_erroring_server(tmp_path, UPSTREAM_404, name="fault-up"),
        window=gov.SEVEN_DAY, timeout=20)

    v = gov.Governor(config.load(tmp_path).governor, reader,
                     gov.FilesGovernorState(tmp_path),
                     now=lambda: 1_000_000.0).evaluate(persist=False)

    assert v.signal_lost and v.fault == gov.UPSTREAM


def test_a_missing_codex_binary_is_a_LOCAL_fault_not_an_upstream_one(tmp_path):
    """The other arm, and the one that makes the label worth having: same
    alert, same lane, opposite remedy. Nobody on this host can fix a provider
    404; a codex that is not on PATH is nothing BUT this host."""
    (tmp_path / "shantytown.toml").write_text(
        '[governor]\nsource = "stub"\nwindow = "seven_day"\n'
        '[[governor.tier]]\nat = 50\nwindow = "seven_day"\nmax_agents = 4\n')
    from shantytown import config
    reader = gov.CodexAppServerReader(
        command=[str(tmp_path / "definitely-not-a-real-binary")],
        window=gov.SEVEN_DAY, timeout=20)

    v = gov.Governor(config.load(tmp_path).governor, reader,
                     gov.FilesGovernorState(tmp_path),
                     now=lambda: 1_000_000.0).evaluate(persist=False)

    assert v.signal_lost and v.fault == gov.LOCAL
    assert "not on PATH" in v.why, v.why


def test_a_stale_reading_reports_UNKNOWN_rather_than_guessing(tmp_path):
    """A staleness verdict is computed by `Reading.lost`, not by a reader that
    watched something fail, so nothing on this path knows whose fault it is.
    Reporting `local` here would be a guess wearing a label's authority."""
    (tmp_path / "shantytown.toml").write_text(
        '[governor]\nsource = "stub"\nstub_pct = 12\n'
        '[[governor.tier]]\nat = 50\nmax_agents = 4\n')
    from shantytown import config
    stale = gov.StubReader(pct=12, at=1_000_000.0 - 10_000, now=lambda: 1_000_000.0)

    v = gov.Governor(config.load(tmp_path).governor, stale,
                     gov.FilesGovernorState(tmp_path),
                     now=lambda: 1_000_000.0).evaluate(persist=False)

    assert v.signal_lost and "STALE" in v.why
    assert v.fault == gov.UNKNOWN_FAULT
