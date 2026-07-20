"""The five machine-readable flags: `anchor --short`, `anchor --events`,
`anchor --harness`, `crew --count`, `inbox --count`. An external status bar parses
these, so the contract under test is the BYTES: one value on stdout, nothing else,
exit 0 even when the answer is nothing.

Two of these have a failure mode that would look fine in production and be very
expensive:

  * `--events` counting by DRAINING. drain() marks each event delivered
    (BLOCK-ONCE, events.py) — a bar polling it every few seconds would deliver the
    tier's stop events to a status bar and no destination would ever be told it
    had them. test_counting_events_does_not_consume_them is the whole reason this
    file exists; it counts twice and then drains, and the drain must still deliver.
  * `crew --count` counting agents it never asked. An unknown verdict rounded into
    the denominator prints a capacity number that was never measured, in the same
    font as one that was.
  * `inbox --count` marking messages read. Identical shape to the first one, one
    type over — see tests/test_inbox.py for the store-level proof.

No new subcommand appears here on purpose (tests/test_command_count.py pins 13).
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from shantytown import cli
from shantytown.events import FilesEvents
from shantytown.inbox import FilesInbox
from shantytown.tmux import NullPanes

from test_crew_work import BUSY_SCREEN, IDLE_SCREEN, SHELL_SCREEN, _Panes


def _run(argv, tmp_path, monkeypatch, panes=None):
    """Drive the REAL parser — the flags have to exist on the real surface, not
    just as attributes a test set by hand."""
    if panes is not None:
        monkeypatch.setattr(cli, "Tmux", lambda: panes)
    # --backend files is EXPLICIT and load-bearing (dearing's ruling, qdal.2):
    # the inbox now defaults to BEADS on both the write and read sides, so a
    # test that relies on --root alone would shell out to `bd` against whatever
    # store the CWD resolves to. --root does not scope the beads backend; only
    # --repo does. tests/conftest.py enforces this, loudly.
    return cli.main(["--root", str(tmp_path), "--backend", "files", *argv])


def _card(root: Path, name: str, **fields) -> None:
    crew = root / "crew"
    crew.mkdir(parents=True, exist_ok=True)
    (crew / f"{name}.json").write_text(json.dumps(fields))


# --- anchor --short ----------------------------------------------------------

def test_short_prints_only_the_id(tmp_path, monkeypatch, capsys):
    _card(tmp_path, "ellie", role="worker")
    from shantytown.files import FilesTracker
    FilesTracker(tmp_path / "items").update(
        "aegis-1o3g", title="Restore the den service",
        status="in_progress", assignee="ellie")

    assert _run(["anchor", "ellie", "--short"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    out = capsys.readouterr().out
    assert out == "aegis-1o3g\n", f"the status bar would render {out!r}"


def test_short_prints_nothing_on_an_empty_plate(tmp_path, monkeypatch, capsys):
    """Empty output means nothing to show. Not 'nothing.', not '—', not a banner
    — the segment renders empty, which is what an empty plate looks like."""
    _card(tmp_path, "ellie", role="worker")
    assert _run(["anchor", "ellie", "--short"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == ""


def test_short_resolves_the_agent_from_the_environment(tmp_path, monkeypatch, capsys):
    """Same resolution as `st anchor`: $SHANTY_AGENT when no positional. The bar
    runs with no argument."""
    _card(tmp_path, "ellie", role="worker")
    from shantytown.files import FilesTracker
    FilesTracker(tmp_path / "items").update("aegis-9h2", title="t",
                                            status="in_progress", assignee="ellie")
    monkeypatch.setenv("SHANTY_AGENT", "ellie")
    assert _run(["anchor", "--short"], tmp_path, monkeypatch, NullPanes()) == cli.OK
    assert capsys.readouterr().out == "aegis-9h2\n"


def test_short_keeps_stdout_clean_when_it_could_not_look(tmp_path, monkeypatch, capsys):
    """An unknown agent is a REFUSAL, not an empty plate. stdout stays empty so
    the bar renders nothing; the difference is carried by the exit code, which is
    the same rule the rest of this CLI runs on."""
    _card(tmp_path, "ellie", role="worker")
    assert _run(["anchor", "nobody", "--short"], tmp_path, monkeypatch,
                NullPanes()) == cli.REFUSED
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "refused" in cap.err


def test_the_full_render_is_unchanged_without_the_flag(tmp_path, monkeypatch, capsys):
    """The human surface must not have quietly become the machine one."""
    _card(tmp_path, "ellie", role="worker")
    assert _run(["anchor", "ellie"], tmp_path, monkeypatch, NullPanes()) == cli.OK
    assert "ON YOUR PLATE" in capsys.readouterr().out


# --- anchor --events ---------------------------------------------------------

def test_events_prints_zero_when_there_are_none(tmp_path, monkeypatch, capsys):
    """0, not blank: zero events is a MEASUREMENT and the bar renders `0`. (An
    events dir that does not exist yet is still zero, and is not created.)"""
    _card(tmp_path, "sattler", role="administrator")
    assert _run(["anchor", "sattler", "--events"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "0\n"
    assert not (tmp_path / "events").exists(), "counting created the store"


def test_events_counts_only_my_undelivered_events(tmp_path, monkeypatch, capsys):
    _card(tmp_path, "sattler", role="administrator")
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="sattler", frm="tim", reason=None, rose=False)
    ev.persist(to="sattler", frm="kelly", reason="lead-unreachable", rose=True)
    ev.persist(to="malcolm", frm="ellie", reason=None, rose=False)   # not mine

    assert _run(["anchor", "sattler", "--events"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "2\n"


def test_counting_events_does_not_consume_them(tmp_path, monkeypatch, capsys):
    """THE RAIL. Count twice — same number — and the Stop hook's drain must STILL
    deliver both events afterwards. A counter that drained would empty the tier's
    delivery guarantee into a status bar, and every symptom of that (an admin that
    is never told a worker stopped) looks exactly like nothing happening.
    """
    _card(tmp_path, "sattler", role="administrator")
    ev = FilesEvents(tmp_path / "events")
    ev.persist(to="sattler", frm="tim", reason=None, rose=False)
    ev.persist(to="sattler", frm="kelly", reason=None, rose=False)

    before = sorted(p.read_text() for p in (tmp_path / "events").glob("*.json"))

    assert _run(["anchor", "sattler", "--events"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "2\n"
    assert _run(["anchor", "sattler", "--events"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "2\n", "the count consumed what it counted"

    after = sorted(p.read_text() for p in (tmp_path / "events").glob("*.json"))
    assert before == after, "counting MUTATED the store"

    # And the real consumer still gets them.
    drained = FilesEvents(tmp_path / "events").drain("sattler")
    assert sorted(e.frm for e in drained) == ["kelly", "tim"]
    # ...once. Block-once still holds after all that counting.
    assert FilesEvents(tmp_path / "events").drain("sattler") == []
    # ...and the count now agrees with the delivery that actually happened.
    assert _run(["anchor", "sattler", "--events"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "0\n"


def test_short_and_events_are_mutually_exclusive(tmp_path, monkeypatch):
    """Two values on one line is not a contract anyone can parse."""
    _card(tmp_path, "ellie", role="worker")
    with pytest.raises(SystemExit):
        _run(["anchor", "ellie", "--short", "--events"], tmp_path, monkeypatch,
             NullPanes())


# --- crew --count -----------------------------------------------------------

def test_count_prints_busy_over_total(tmp_path, monkeypatch, capsys):
    for name in ("ellie", "ian", "malcolm"):
        _card(tmp_path, name, role="worker", pane=f"p-{name}")
    panes = _Panes({"p-ellie": IDLE_SCREEN, "p-ian": BUSY_SCREEN,
                    "p-malcolm": BUSY_SCREEN})
    assert _run(["crew", "--count"], tmp_path, monkeypatch, panes) == cli.OK
    assert capsys.readouterr().out == "2/3\n"


def test_count_excludes_an_agent_whose_state_is_unknown(tmp_path, monkeypatch, capsys):
    """The explicit denominator test. Four agents on the roster: one busy, one
    idle, one DOWN (pane on the card, not in tmux), one sitting in a bare shell
    with no runtime UI (UNSURE — not idle, per test_crew_work). Only the two we
    could judge are counted, in EITHER position: `1/2`, never `1/4` and never
    `3/4`."""
    for name in ("ellie", "ian", "malcolm", "tim"):
        _card(tmp_path, name, role="worker", pane=f"p-{name}")
    panes = _Panes({"p-ellie": IDLE_SCREEN, "p-ian": BUSY_SCREEN,
                    "p-tim": SHELL_SCREEN})          # p-malcolm is not live at all
    assert _run(["crew", "--count"], tmp_path, monkeypatch, panes) == cli.OK
    assert capsys.readouterr().out == "1/2\n"


def test_count_of_an_empty_roster_is_zero_over_zero(tmp_path, monkeypatch, capsys):
    (tmp_path / "crew").mkdir(parents=True)
    assert _run(["crew", "--count"], tmp_path, monkeypatch, NullPanes()) == cli.OK
    assert capsys.readouterr().out == "0/0\n", "the bar got prose instead of a ratio"


def test_count_agrees_with_the_table_it_replaces(tmp_path, monkeypatch, capsys):
    """One judgment, two renderings. If `--count` ever grows its own opinion of
    busy, this fails — which is the point of sharing _crew_states."""
    for name in ("ellie", "ian", "malcolm", "tim"):
        _card(tmp_path, name, role="worker", pane=f"p-{name}")
    screens = {"p-ellie": IDLE_SCREEN, "p-ian": BUSY_SCREEN, "p-tim": SHELL_SCREEN}

    assert _run(["crew"], tmp_path, monkeypatch, _Panes(screens)) == cli.OK
    table = capsys.readouterr().out
    assert "1 free: ellie" in table and "1 busy: ian" in table

    assert _run(["crew", "--count"], tmp_path, monkeypatch, _Panes(screens)) == cli.OK
    assert capsys.readouterr().out == "1/2\n"


# --- anchor --harness -------------------------------------------------------

def test_harness_prints_claude_for_a_card_that_never_said(tmp_path, monkeypatch, capsys):
    """Every card on this fleet. `claude` IS the answer for an unset field — an
    empty segment would read as "no harness", which is a different claim."""
    _card(tmp_path, "ellie", role="worker")
    assert _run(["anchor", "ellie", "--harness"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "claude\n"


def test_harness_prints_what_the_card_says(tmp_path, monkeypatch, capsys):
    _card(tmp_path, "ellie", role="worker", harness="claude")
    assert _run(["anchor", "ellie", "--harness"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "claude\n"


def test_harness_refuses_an_unknown_agent_without_printing(tmp_path, monkeypatch, capsys):
    _card(tmp_path, "ellie", role="worker")
    assert _run(["anchor", "nobody", "--harness"], tmp_path, monkeypatch,
                NullPanes()) == cli.REFUSED
    assert capsys.readouterr().out == ""


# --- inbox --count ----------------------------------------------------------

def test_inbox_count_is_zero_with_no_messages(tmp_path, monkeypatch, capsys):
    _card(tmp_path, "sattler", role="administrator")
    assert _run(["inbox", "--count", "sattler"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "0\n"
    assert not (tmp_path / "inbox").exists(), "counting created the store"


def test_inbox_count_counts_my_unread(tmp_path, monkeypatch, capsys):
    _card(tmp_path, "sattler", role="administrator")
    box = FilesInbox(tmp_path / "inbox")
    box.deliver("sattler", "one")
    box.deliver("sattler", "two")
    box.deliver("ellie", "not yours")
    assert _run(["inbox", "--count", "sattler"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "2\n"


def test_inbox_count_does_not_mark_anything_read(tmp_path, monkeypatch, capsys):
    """The same rail as --events, one type over: a status bar polling this must
    not be the thing that "delivers" the message. Count twice, then LIST, and the
    messages are still unread."""
    _card(tmp_path, "sattler", role="administrator")
    box = FilesInbox(tmp_path / "inbox")
    box.deliver("sattler", "one")
    box.deliver("sattler", "two")
    before = sorted(p.read_text() for p in (tmp_path / "inbox").glob("*.json"))

    for _ in range(2):
        assert _run(["inbox", "--count", "sattler"], tmp_path, monkeypatch,
                    NullPanes()) == cli.OK
        assert capsys.readouterr().out == "2\n"
    assert sorted(p.read_text() for p in (tmp_path / "inbox").glob("*.json")) == before

    # the human read side also marks nothing...
    assert _run(["inbox", "sattler"], tmp_path, monkeypatch, NullPanes()) == cli.OK
    assert "2 unread" in capsys.readouterr().out
    assert len(FilesInbox(tmp_path / "inbox").unread("sattler")) == 2

    # ...and --read is the explicit ack that does.
    assert _run(["inbox", "--read", "sattler"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert "marked 2" in capsys.readouterr().out
    assert _run(["inbox", "--count", "sattler"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "0\n"


def test_inbox_count_defaults_to_me(tmp_path, monkeypatch, capsys):
    """The bar runs it with no argument, exactly like `anchor --short`."""
    _card(tmp_path, "sattler", role="administrator")
    FilesInbox(tmp_path / "inbox").deliver("sattler", "one")
    monkeypatch.setenv("SHANTY_AGENT", "sattler")
    assert _run(["inbox", "--count"], tmp_path, monkeypatch, NullPanes()) == cli.OK
    assert capsys.readouterr().out == "1\n"


def test_a_durable_send_is_readable_by_the_recipient(tmp_path, monkeypatch, capsys):
    """The loop `st mail -d` never closed: it persisted something nothing read
    back. Send durably to a DOWN agent, then count their inbox."""
    _card(tmp_path, "ellie", role="worker", pane="p-gone")
    _card(tmp_path, "sattler", role="administrator")
    assert _run(["inbox", "-d", "ellie", "HANDOFF: the epic"], tmp_path,
                monkeypatch, _Panes({})) == cli.OK
    capsys.readouterr()
    assert _run(["inbox", "--count", "ellie"], tmp_path, monkeypatch,
                NullPanes()) == cli.OK
    assert capsys.readouterr().out == "1\n"
