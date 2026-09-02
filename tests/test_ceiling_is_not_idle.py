"""An agent at its session ceiling must not render as `idle`.

aegis-9cobou, the fourth crew-table-vs-pane disagreement of 2026-09-02 and the
same family as aegis-4j4ypk.

MEASURED: malcolm (codex) hit the enforced four-item session ceiling and said so
in its pane, twice. Its last output, recovered from the transcript archive:

    "I remain at the enforced four-item session ceiling. Please stop me
     deliberately with that reason; I cannot pick up aegis-j0yaxj.1 this
     session."

`st crew` rendered that pane `idle` — indistinguishable from an agent with
nothing to do — so wu nudged again without reading the pane and received the
same answer.

THE FEED ALREADY KNEW. `IdleFleetAlerter` asks `session_budget.gate` and refuses
to feed a ceilinged agent. The machine had the fact; the table a human reads did
not. Reading the same gate here is what makes the two surfaces agree.

WHY NOT THE DETECTOR THE BEAD PROPOSED. Its acceptance asks for a verdict when
"the pane's last assistant block ends in a question". The specimen above does not
end in a question — it is a declarative request — so that detector would have
missed the very case it was filed for, while carrying a false-positive risk whose
cost is an idle agent silently withheld from work. A computed gate either tripped
or it did not.
"""
from __future__ import annotations
from types import SimpleNamespace

import pytest

from shantytown import cli, triage


class _Panes:
    def __init__(self, screens):
        self._s = screens

    def exists(self, pane):
        return pane in self._s

    def capture(self, pane, attrs=False):
        return self._s[pane]

    def cmdline(self, pane):
        return None


class _Runtime:
    def shows_ready_ui(self, plain):
        return True


def _card(name="malcolm", pane="p-malcolm"):
    c = SimpleNamespace()
    c.name, c.pane, c.role, c.harness = name, pane, "worker", "codex"
    return c


# An EMPTY codex input box, with the attribute run tmux -e actually emits. The
# bare "› Ask Codex to do anything" placeholder reads UNKNOWN without attributes
# and would make every assertion here vacuous (aegis-x6xh) — a fixture that reads
# `?` cannot demonstrate anything about converting `idle`.
IDLE_PANE = "\x1b[38;5;246m\u203a\xa0\x1b[39m\n"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(cli, "asks_a_question", lambda rt, plain: False, raising=False)
    monkeypatch.setattr(cli, "auth_expired", lambda rt, plain: False, raising=False)


def _rows(tmp_path, ceiling, budget_root=True):
    from shantytown import session_budget as sb
    import shantytown.cli as cli_mod
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sb, "gate", lambda root, agent, now=None: (None, None, ceiling))
    try:
        return list(cli_mod._crew_states(
            [_card()], _Panes({"p-malcolm": IDLE_PANE}), _Runtime(),
            budget_root=(tmp_path if budget_root else None)))
    finally:
        monkey.undo()


class _Ceiling(SimpleNamespace):
    measure = "items"


def test_an_agent_at_its_ceiling_does_not_read_idle(tmp_path):
    """The regression."""
    _, _, work, _ = _rows(tmp_path, _Ceiling())[0]
    assert work != triage.IDLE
    assert "ceiling" in work, f"the verdict must name the state, got {work!r}"
    assert "items" in work, "and the MEASURE, so a reader can tell which limit"


def test_an_agent_under_its_ceiling_still_reads_idle(tmp_path):
    """THE CONTROL THAT MATTERS MOST HERE. A verdict that started reporting
    `ceiling` for everyone would pass the test above and quietly remove the whole
    fleet from every free-capacity reading a coordinator takes."""
    _, _, work, _ = _rows(tmp_path, None)[0]
    assert work == triage.IDLE


def test_a_broken_budget_read_FAILS_OPEN_to_idle(tmp_path, monkeypatch):
    """Unknown must never withhold an agent from work. Same direction as the
    deferral gate (aegis-vyc3aa) and for the same reason: the cost of a wrong
    `idle` is one wasted nudge; the cost of a wrong `ceiling` is an agent that
    silently stops being offered work, with no signal anywhere."""
    from shantytown import session_budget as sb

    def boom(*a, **k):
        raise RuntimeError("budget db unreadable")

    monkeypatch.setattr(sb, "gate", boom)
    rows = list(cli._crew_states([_card()], _Panes({"p-malcolm": IDLE_PANE}),
                                 _Runtime(), budget_root=tmp_path))
    assert rows[0][2] == triage.IDLE


def test_no_budget_root_is_the_previous_behaviour_exactly(tmp_path):
    """Every other _crew_states caller passes no budget_root, so they must be
    byte-for-byte unchanged."""
    _, _, work, _ = _rows(tmp_path, _Ceiling(), budget_root=False)[0]
    assert work == triage.IDLE


def test_a_BUSY_pane_is_never_converted(tmp_path):
    """Additive by construction: the refinement only ever converts an `idle`, so
    it cannot take an agent that is demonstrably working and call it stopped."""
    busy = "• Working (3m • esc to interrupt)\n" + IDLE_PANE
    from shantytown import session_budget as sb
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sb, "gate", lambda root, agent, now=None: (None, None, _Ceiling()))
    try:
        rows = list(cli._crew_states([_card()], _Panes({"p-malcolm": busy}),
                                     _Runtime(), budget_root=tmp_path))
    finally:
        monkey.undo()
    assert rows[0][2].startswith(triage.BUSY)
