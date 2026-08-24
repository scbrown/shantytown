"""A send is two facts, and st reported the first as the second — aegis-wcjuz.

    the keystrokes reached the pane        st checked this
    the message was SUBMITTED              st claimed this

On a TUI that absorbs a large write as a PASTE the first is true and the second
is false: the body sits in the input box as `[Pasted Content N chars]`, the
trailing Enter does not commit it, the agent never sees it, and the sender is
told it was delivered. Measured on a live pane — a 1024-char brief carrying a GO
ruling on a production deploy sat unread while the coordinator believed the agent
was working, and the fleet's only signal was silence, which is indistinguishable
from an agent thinking hard.

MEASURED on this host against codex 0.146.1, by writing to a real pane and
reading it back (no Enter, so nothing submitted and no model was called):

    one write of 1000 chars     -> literal text
    one write of 1004 chars     -> [Pasted Content ...]   STRANDED
    one write of 1023 chars     -> [Pasted Content ...]   STRANDED
    2000 chars as TWO 1000s     -> literal text

So the trigger is the size of a SINGLE WRITE and the total message length is
irrelevant — which is why chunking fixes it outright instead of raising a ceiling.

Two independent halves are pinned here, and the second matters even if the first
is someday wrong: DO NOT CREATE THE CONDITION (chunk), and DO NOT CLAIM A
DELIVERY YOU DID NOT OBSERVE (read the box back).
"""
from __future__ import annotations

from shantytown import harness as harness_mod
from shantytown import tmux as tmux_mod
from shantytown.runtime import input_stranded


class _Recorder:
    """Captures the argv of every send-keys the adapter runs."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))

        class _R:
            returncode = 0
            # Empty: `foreground()` finds no matching pane and answers None =
            # CANNOT TELL, so the dead-pane guard allows the send. These tests
            # are about chunking, and a fixture that silently tripped a
            # different guard would be testing that guard instead.
            stdout = ""
        return _R()

    def sendkeys(self):
        """Only the calls that actually type — `foreground()` queries tmux too."""
        return [c for c in self.calls if "send-keys" in c]

    def literals(self):
        """The text of each `send-keys -l` call, in order.

        The chunk is the LAST argument, not the one after `-l`: since
        aegis-5993y a `--` sits between them so tmux cannot parse a chunk
        beginning with `-` as a flag.
        """
        return [c[-1] for c in self.sendkeys() if "-l" in c]

    def keys(self):
        """Non-literal key sends (the submits)."""
        return [c[-1] for c in self.sendkeys() if "-l" not in c]


def _send(monkeypatch, text):
    rec = _Recorder()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    monkeypatch.setattr(tmux_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(tmux_mod, "_journal_send", lambda *_a: None)
    tmux_mod.Tmux().send("some-pane", text)
    return rec


def test_a_long_body_is_split_into_writes_no_TUI_will_read_as_a_paste(monkeypatch):
    """The fix. 512 is half the measured boundary on purpose: that boundary is
    somebody else's terminal handling and may move, and the cost of being
    conservative is a few subprocess calls while the cost of being wrong is a
    message that is never delivered while the sender is told it was."""
    rec = _send(monkeypatch, "z" * 5000)
    lits = rec.literals()
    assert len(lits) > 1, "a 5000-char body went out as one write"
    assert max(len(x) for x in lits) <= tmux_mod._SEND_CHUNK
    assert "".join(lits) == "z" * 5000, "chunking altered the body"
    assert tmux_mod._SEND_CHUNK <= 1000, (
        "the chunk must stay under the measured paste boundary (1004 stranded, "
        "1000 did not)")


def test_the_body_is_reassembled_EXACTLY_including_newlines(monkeypatch):
    """Dispatch bodies are multi-line and carry bracketed notes. A chunker that
    dropped or doubled a boundary character would corrupt messages rather than
    strand them — a quieter failure than the one being fixed."""
    body = "first line\n\nsecond line with [st keep-current: x]\n" + ("m" * 2000)
    rec = _send(monkeypatch, body)
    assert "".join(rec.literals()) == body


def test_a_short_body_still_goes_out_as_ONE_write(monkeypatch):
    """The overwhelming majority of sends are short. They must not pay for this."""
    rec = _send(monkeypatch, "ack - proceed")
    assert rec.literals() == ["ack - proceed"]


def test_an_empty_body_types_nothing_but_still_submits_once(monkeypatch):
    """Splitting an empty string yields NO chunks, and a loop that then sent no
    `-l` at all would leave the Enter to commit whatever the box already held —
    a message that types nothing must never submit somebody else's stranded
    input. Exactly one empty literal, exactly one Enter, as before."""
    rec = _send(monkeypatch, "")
    assert rec.literals() == [""]
    assert rec.keys() == ["Enter"]


def test_the_submit_is_still_ONE_Enter_and_it_is_LAST(monkeypatch):
    """The two-step literal-then-Enter contract is what the whole dispatch path
    rests on. Chunking must not turn one submit into several — each extra Enter
    would commit a partial body as its own turn."""
    rec = _send(monkeypatch, "y" * 3000)
    assert rec.keys() == ["Enter"]
    assert "-l" not in rec.sendkeys()[-1], "the last thing typed was not the submit"


# --- the second half: never claim a delivery you did not observe -------------

_PASTED = "[Pasted Content 1024 chars]"


def test_a_stranded_paste_is_detected_in_the_input_box():
    assert input_stranded(f"some earlier output\n\n› {_PASTED}filler\n") is True


def test_the_same_text_further_up_the_pane_is_NOT_a_stranded_send():
    """Tail-only, like every text predicate here. An agent that PRINTED this
    string — this repo's own source contains it — is not an agent holding it."""
    screen = f"› {_PASTED}\n" + "\n".join(f"line {i}" for i in range(30))
    assert input_stranded(screen) is False


def test_blank_padding_below_the_box_does_not_hide_it():
    """A TUI that draws its box high and pads below would otherwise push the box
    out of a fixed window — the padding case that already slipped through once."""
    assert input_stranded(f"› {_PASTED}\n" + "\n" * 25) is True


def test_an_ordinary_idle_pane_is_not_reported_stranded():
    """The direction that decides whether the verdict stays worth reading: a
    check that fires on healthy sends trains a sender to ignore it."""
    assert input_stranded("› Summarize recent commits\n\n  model · ~/ws\n") is False


def test_the_markers_are_DERIVED_from_the_registry():
    """Same shape as picker_markers/settings_env_vars: a program is covered by
    declaring its own measured chrome, not by editing the predicate. An empty
    tuple means NOBODY HAS MEASURED THIS PROGRAM — never "it always submits"."""
    assert "[Pasted Content" in harness_mod.stranded_markers()
    assert harness_mod.get("claude").stranded_markers == ()


# --- the dead pane must not become a shell that runs the fleet's mail ---------
#
# aegis-ikj4t. When a runtime exits, its pane falls back to the login shell and
# st keeps routing to it, so every inbound message is EXECUTED BY BASH. Observed
# live: another agent's escalation text and an ack recipe ran as shell commands.
# Nothing destructive ran by luck of the wording, not by design.

import pytest

from shantytown.tmux import NullPanes, PaneNotAgent, SHELL_COMMANDS


def test_a_message_to_a_DEAD_pane_is_REFUSED_not_executed():
    panes = NullPanes(foreground_cmd="bash")
    with pytest.raises(PaneNotAgent):
        panes.send("shanty-x", "gt escalate -s HIGH 'disk full'")
    assert panes.sent == [], "text reached a shell pane"


@pytest.mark.parametrize("shell", sorted(SHELL_COMMANDS))
def test_every_known_shell_is_refused(shell):
    with pytest.raises(PaneNotAgent):
        NullPanes(foreground_cmd=shell).send("shanty-x", "anything")


@pytest.mark.parametrize("runtime", ["claude", "node"])
def test_a_LIVE_agent_still_receives_normally(runtime):
    """The direction that decides whether this is shippable at all. Measured on
    the live fleet: healthy panes read `claude` and `node` — and, critically, an
    agent RUNNING A SHELL COMMAND still reads as its runtime, because the tool's
    subprocess never takes the terminal. Without that, this guard would refuse
    every agent the moment it ran a bash tool call."""
    panes = NullPanes(foreground_cmd=runtime)
    panes.send("shanty-x", "ack - proceed")
    assert panes.sent == [("shanty-x", "ack - proceed")]


def test_an_UNKNOWN_foreground_is_DELIVERED_not_refused():
    """A positive list, never "anything not a known runtime". st does not own
    every program a pane may legitimately run, and refusing the unrecognised
    would break messaging for a harness nobody has told this file about.
    Cannot-tell must not manufacture a refusal."""
    panes = NullPanes(foreground_cmd="some-new-agent-program")
    panes.send("shanty-x", "hello")
    assert panes.sent == [("shanty-x", "hello")]


def test_the_LAUNCHER_may_still_type_into_a_bash_pane():
    """The one caller for which a shell is the CORRECT target: starting an agent
    is typing `cd … && claude …` into a fresh bash pane. A blanket refusal here
    would leave st unable to launch anything — which is why the exemption is an
    explicit, greppable parameter rather than a special case inside the guard."""
    panes = NullPanes(foreground_cmd="bash")
    panes.send("shanty-x", "cd /w && claude --settings s.json", allow_shell=True)
    assert panes.sent == [("shanty-x", "cd /w && claude --settings s.json")]


# --- aegis-5993y: a chunk beginning with `-` must not be read as a flag -------


def test_every_literal_send_is_separated_by_a_double_dash(monkeypatch):
    """`-l` says the ARGUMENT is literal. It does not stop tmux's own option
    parser from reaching that argument first.

    MEASURED on tmux 3.6, against a real pane:

        send-keys -t <p> -l    "-node ingest has the data."
            -> "command send-keys: unknown flag -n", exit 1
        send-keys -t <p> -l -- "-node ingest has the data."
            -> exit 0

    `check=True` turns that exit 1 into a CalledProcessError partway through the
    chunk loop, so the recipient keeps every chunk up to the break and is told
    nothing, while the sender gets a traceback instead of a delivery line —
    partial delivery reported as neither success nor failure.
    """
    rec = _send(monkeypatch, "hello")
    for call in rec.sendkeys():
        if "-l" not in call:
            continue
        assert call[call.index("-l") + 1] == "--", (
            f"no `--` guard before the literal: {call!r}")


def test_a_body_that_CHUNKS_onto_a_leading_dash_still_sends(monkeypatch):
    """The reason this is a correctness bug and not a rare edge case: chunking
    splits at a fixed width, so whether a message survives depends on where the
    boundary happens to land. Here the body is built so the SECOND chunk starts
    with the hyphen that killed the live delivery."""
    filler = "z" * tmux_mod._SEND_CHUNK
    body = filler + "-node ingest has the data."
    rec = _send(monkeypatch, body)
    lits = rec.literals()
    assert len(lits) > 1
    assert lits[1].startswith("-"), "the fixture did not create the condition"
    assert "".join(lits) == body, "the body did not survive the boundary"
    for call in rec.sendkeys():
        if "-l" in call:
            assert "--" in call


def test_the_option_key_send_is_guarded_too(monkeypatch):
    """`control`/`option` type into another agent's live pane with `-l` as well.
    The allowlist holds no hyphen today; allowlists grow."""
    rec = _Recorder()
    monkeypatch.setattr(tmux_mod.subprocess, "run", rec)
    monkeypatch.setattr(tmux_mod, "_journal_send", lambda *_a: None)
    tmux_mod.Tmux().option("some-pane", 1)
    literal_calls = [c for c in rec.sendkeys() if "-l" in c]
    assert literal_calls, "option() sent no literal"
    for call in literal_calls:
        assert call[call.index("-l") + 1] == "--", call
        assert call[-1] == "1"
