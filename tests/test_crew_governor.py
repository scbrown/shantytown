"""`st crew --governor` — the capacity verdict a status bar can parse.

The load-bearing property is `test_blind_cases_carry_no_numbers`. A bar that
printed a stale percentage while the governor was blind would silently undo the
whole fail-safe — the governor alarms every pass when it cannot see, on purpose,
and the surface humans actually watch must not quietly disagree. Making the blind
cases unparseable AS a reading is the same rule shantytown.answer applies to a
collection.
"""
from __future__ import annotations

import types

import pytest

import shantytown.cli as cli
import shantytown.governor as gov_mod


def _run(monkeypatch, capsys, governor):
    monkeypatch.setattr(cli, "_governor", lambda a: governor)
    rc = cli._crew_governor(types.SimpleNamespace(root="/nonexistent"))
    return rc, capsys.readouterr().out.strip()


class _Reader:
    def __init__(self, readings):
        self._readings = readings

    def read_all(self):
        return self._readings

    def read(self):
        return self._readings.get(gov_mod.FIVE_HOUR)


class _Gov:
    def __init__(self, readings, verdict):
        self.reader = _Reader(readings)
        self._verdict = verdict

    def evaluate(self, *, persist=True):
        # The read path must never persist. A bar polling every few seconds would
        # otherwise ratchet fleet policy just by being looked at.
        assert persist is False, "the status-bar read must not extend a hold"
        return self._verdict


def _reading(pct, ok=True):
    return gov_mod.Reading(pct=pct, ok=ok, source="stub")


def _verdict(engaged=(), signal_lost=False):
    return gov_mod.Verdict(reading=_reading(0), engaged=tuple(engaged),
                           signal_lost=signal_lost)


def test_both_windows_no_tier(monkeypatch, capsys):
    g = _Gov({gov_mod.FIVE_HOUR: _reading(45), gov_mod.SEVEN_DAY: _reading(24)},
             _verdict())
    rc, out = _run(monkeypatch, capsys, g)
    assert rc == cli.OK
    assert out == "ok 45 24"


def test_engaged_tier_is_named(monkeypatch, capsys):
    """A number teaches nothing; the label says what is actually in force."""
    tier = gov_mod.Tier(at=70, window=gov_mod.FIVE_HOUR, min_priority=0)
    g = _Gov({gov_mod.FIVE_HOUR: _reading(70), gov_mod.SEVEN_DAY: _reading(24)},
             _verdict(engaged=[tier]))
    _, out = _run(monkeypatch, capsys, g)
    assert out.startswith("ok 70 24 ")
    assert "dispatch only P0 and above" in out


def test_top_engaged_tier_wins(monkeypatch, capsys):
    """Restriction is cumulative, so the LAST engaged tier is the tightest one
    and is the one an operator needs to read."""
    lo = gov_mod.Tier(at=50, window=gov_mod.FIVE_HOUR, min_priority=1)
    hi = gov_mod.Tier(at=70, window=gov_mod.FIVE_HOUR, min_priority=0)
    g = _Gov({gov_mod.FIVE_HOUR: _reading(72), gov_mod.SEVEN_DAY: _reading(30)},
             _verdict(engaged=[lo, hi]))
    _, out = _run(monkeypatch, capsys, g)
    assert "P0" in out and "P1" not in out


def test_signal_lost_is_the_bare_word(monkeypatch, capsys):
    g = _Gov({gov_mod.FIVE_HOUR: _reading(45)}, _verdict(signal_lost=True))
    _, out = _run(monkeypatch, capsys, g)
    assert out == "lost"


def test_reader_that_raises_is_lost_not_a_number(monkeypatch, capsys):
    class _Boom:
        def read_all(self): raise RuntimeError("prometheus 401")
    g = types.SimpleNamespace(reader=_Boom(),
                              evaluate=lambda **kw: _verdict())
    _, out = _run(monkeypatch, capsys, g)
    assert out == "lost"


def test_no_governor_configured_is_off(monkeypatch, capsys):
    _, out = _run(monkeypatch, capsys, None)
    assert out == "off"


def test_absent_window_is_a_question_mark_not_zero(monkeypatch, capsys):
    """A budget the producer does not publish is NOT zero. Rendering 0 would read
    as maximum headroom — the most expensive direction for this wrong answer."""
    g = _Gov({gov_mod.FIVE_HOUR: _reading(45)}, _verdict())   # no seven_day
    _, out = _run(monkeypatch, capsys, g)
    assert out == "ok 45 ?"


def test_not_ok_reading_is_a_question_mark(monkeypatch, capsys):
    g = _Gov({gov_mod.FIVE_HOUR: _reading(45),
              gov_mod.SEVEN_DAY: _reading(None, ok=False)}, _verdict())
    _, out = _run(monkeypatch, capsys, g)
    assert out == "ok 45 ?"


@pytest.mark.parametrize("case", ["lost", "off"])
def test_blind_cases_carry_no_numbers(monkeypatch, capsys, case):
    """THE POINT. A blind verdict must be structurally impossible to read as a
    percentage — no digits at all, so no consumer can scrape a stale-looking
    number out of it however carelessly it parses."""
    if case == "lost":
        g = _Gov({gov_mod.FIVE_HOUR: _reading(99)}, _verdict(signal_lost=True))
    else:
        g = None
    _, out = _run(monkeypatch, capsys, g)
    assert out == case
    assert not any(ch.isdigit() for ch in out)
