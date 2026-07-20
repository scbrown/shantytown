"""Launch stamps — can we tell which settings a live agent is ACTUALLY on?

aegis-nipg. `--settings` is read once, at launch; every later rewrite reaches
nobody already running. `st crew` reported `up` for two agents whose stop hooks
were resolving against the wrong store, and `up` is what a deaf agent looks like.

The tests that matter here are the ones about UNKNOWN. A detector for "you were
given a false clean bill of health" that itself invents a clean bill of health
is worse than none: it launders the same silence through something that looks
like a check.
"""
from __future__ import annotations

import json

from shantytown.launched import FilesLaunches, CURRENT, STALE, UNKNOWN, digest


def _settings(tmp_path, body: str):
    p = tmp_path / "worker.settings.json"
    p.write_text(body)
    return p


# --- the three verdicts -----------------------------------------------------

def test_unchanged_settings_read_CURRENT(tmp_path):
    s = _settings(tmp_path, '{"hooks": {"Stop": "old"}}')
    lx = FilesLaunches(tmp_path / "launched")
    lx.record("kelly", s)
    assert lx.verdict("kelly") == CURRENT


def test_a_REWRITTEN_settings_file_makes_the_live_agent_STALE(tmp_path):
    """The whole bug, in four lines. The agent launched on the old bytes and can
    never see the new ones; only a relaunch re-reads --settings."""
    s = _settings(tmp_path, '{"hooks": {"Stop": "unrooted"}}')
    lx = FilesLaunches(tmp_path / "launched")
    lx.record("kelly", s)
    s.write_text('{"hooks": {"Stop": "rooted --root /srv/.shanty"}}')   # the fix
    assert lx.verdict("kelly") == STALE, "a settings rewrite must be VISIBLE"


def test_an_unstamped_agent_is_UNKNOWN_and_never_current(tmp_path):
    """An agent launched before stamping existed, or by something other than
    `st new`, has no stamp. The honest answer is that we cannot tell.

    This is the load-bearing test. The failure this whole module exists to catch
    is a false clean bill of health; if absence of a stamp read as CURRENT, the
    detector would hand out exactly that, and would do it most confidently for
    the oldest agents — the ones most likely to actually be stale.
    """
    lx = FilesLaunches(tmp_path / "launched")
    v = lx.verdict("nobody")
    assert v == UNKNOWN
    assert v != CURRENT


def test_an_UNREADABLE_stamp_is_UNKNOWN_not_current(tmp_path):
    """Corrupt/truncated stamp -> we could not tell. Same rule: only a stamp we
    actually read and matched earns CURRENT."""
    lx = FilesLaunches(tmp_path / "launched")
    (tmp_path / "launched").mkdir(parents=True)
    (tmp_path / "launched" / "kelly.json").write_text("{ this is not json")
    assert lx.verdict("kelly") == UNKNOWN


def test_a_VANISHED_settings_file_is_UNKNOWN_not_stale(tmp_path):
    """If the file it launched on is gone we cannot compare, so we cannot say it
    changed. Absent evidence is not evidence — that is the same distinction
    UNKNOWN exists to hold."""
    s = _settings(tmp_path, "{}")
    lx = FilesLaunches(tmp_path / "launched")
    lx.record("kelly", s)
    s.unlink()
    assert lx.verdict("kelly") == UNKNOWN


# --- the properties that keep it from crying wolf ---------------------------

def test_an_IDEMPOTENT_rewrite_of_identical_bytes_stays_CURRENT(tmp_path):
    """_emit_role_settings rewrites these files on every `st project` / `role
    set`, so mtime churns constantly while the bytes are identical. Hashing
    CONTENT is what keeps the whole fleet from reading STALE after a no-op
    re-emit — a detector that cries wolf is one that gets ignored on the night it
    is right."""
    body = '{"hooks": {"Stop": "same"}}'
    s = _settings(tmp_path, body)
    lx = FilesLaunches(tmp_path / "launched")
    lx.record("kelly", s)
    s.write_text(body)                       # byte-identical re-emit
    assert lx.verdict("kelly") == CURRENT


def test_verdict_is_probed_LIVE_every_time_not_cached(tmp_path):
    """The mistake this bead's author made by hand: reading one past success as a
    standing property. A stamp that verified CURRENT a moment ago must go STALE
    the instant the file changes — no memoized answer."""
    s = _settings(tmp_path, "{}")
    lx = FilesLaunches(tmp_path / "launched")
    lx.record("kelly", s)
    assert lx.verdict("kelly") == CURRENT    # asked once...
    s.write_text('{"changed": true}')
    assert lx.verdict("kelly") == STALE      # ...and the answer MOVED


def test_stop_forgets_the_stamp_so_a_dead_agent_is_never_current(tmp_path):
    """Stamps describe LIVE launches. One left behind after a stop would report
    on a process that no longer exists."""
    s = _settings(tmp_path, "{}")
    lx = FilesLaunches(tmp_path / "launched")
    lx.record("kelly", s)
    lx.forget("kelly")
    assert lx.verdict("kelly") == UNKNOWN
    lx.forget("kelly")                       # idempotent — no raise on absent


def test_record_survives_an_unhashable_settings_file(tmp_path):
    """Best-effort by design: a stamp that cannot be written must not fail a
    launch. Losing a stamp costs a detection; refusing to launch costs the
    agent."""
    lx = FilesLaunches(tmp_path / "launched")
    assert lx.record("kelly", tmp_path / "does-not-exist.json") is None
    assert lx.verdict("kelly") == UNKNOWN


def test_relaunch_on_the_new_file_clears_STALE(tmp_path):
    """The remediation actually remediates — the detector must go quiet once the
    agent has been restarted onto the current bytes, or nobody will trust it."""
    s = _settings(tmp_path, "{}")
    lx = FilesLaunches(tmp_path / "launched")
    lx.record("kelly", s)
    s.write_text('{"hooks": "new"}')
    assert lx.verdict("kelly") == STALE
    lx.record("kelly", s)                    # st stop && st new
    assert lx.verdict("kelly") == CURRENT


def test_stamps_are_per_agent(tmp_path):
    """Relaunching one agent must not silence the warning about another — the
    incident had two deaf agents and fixing one told us nothing about the other."""
    s = _settings(tmp_path, "{}")
    lx = FilesLaunches(tmp_path / "launched")
    lx.record("kelly", s)
    lx.record("gennaro", s)
    s.write_text('{"hooks": "new"}')
    lx.record("kelly", s)                    # only kelly relaunched
    assert lx.verdict("kelly") == CURRENT
    assert lx.verdict("gennaro") == STALE


def test_digest_is_content_not_path(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text("{}")
    b.write_text("{}")
    assert digest(a) == digest(b)
    assert digest(tmp_path / "nope.json") is None


def test_the_stamp_on_disk_is_readable_json(tmp_path):
    """A store a human can inspect during an incident without our code."""
    s = _settings(tmp_path, "{}")
    lx = FilesLaunches(tmp_path / "launched")
    lx.record("kelly", s)
    d = json.loads((tmp_path / "launched" / "kelly.json").read_text())
    assert d["settings"] == str(s)
    assert len(d["sha256"]) == 64
