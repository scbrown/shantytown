"""EVERY send site is CLASSIFIED, and a new one cannot appear unclassified.

aegis-5vxmz. Stiwi asked for the agent name on "all send keys". The obvious way
to guarantee "all" is to prefix at the transport, where every send necessarily
passes — and that is exactly what this repo must not do: two of the send sites
carry a shell command line and a prompt answer, not prose (see
shantytown/attribution.py for the full argument). So "all" is guaranteed the
other way: the exception list is made EXPLICIT, and this test is what stops it
from silently growing.

WHAT THIS TEST CAN AND CANNOT PROVE, stated plainly because a guard whose
strength is overestimated is worse than none:

  IT PROVES   the set of `*.send(...)` call sites in shantytown/ is EXACTLY the
              set enumerated below. A new one — or a moved/renamed one — fails
              until somebody writes down which kind it is. That is the property
              worth having: the next person to add a pane message cannot forget,
              because the test stops them and names the decision.
  IT PROVES   that every site marked `attributed-here` really does pass its text
              through attribute(). A classification that lies about the line it
              sits on is caught.
  IT DOES NOT prove `attributed-upstream` sites are truly attributed upstream —
              that is a data-flow claim across functions, and no static check
              here makes it. Those sites have their own behavioural tests
              (test_inbox.py's differential pair, test_attribution.py below), and
              the inventory entry names them so the link is followable.

Keyed by (file, enclosing function) rather than line number on purpose: line
numbers churn on every edit above them, and a guard that cries wolf gets
neutered. A function rename does fail this test — correctly, since the site has
moved and deserves a fresh look.
"""
from __future__ import annotations
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "shantytown"

ATTRIBUTED_HERE = "attributed-here"          # attribute() wraps the text argument
ATTRIBUTED_UPSTREAM = "attributed-upstream"  # the composer signed it already
BARE = "bare-by-design"                      # not prose; signing it would break it

INVENTORY: dict[tuple[str, str], tuple[str, str]] = {
    ("notify.py", "wake_recipient"): (
        ATTRIBUTED_HERE,
        "blocked-worker push into a coordinator's pane; sender is `st tend`."),
    ("notify.py", "push_to_own_pane"): (
        ATTRIBUTED_HERE,
        "cycle prompt + haul feed/handoff into an agent's own pane. The chokepoint "
        "for those message composers, so a NEW tend message is signed by default."),
    ("notify.py", "push_to_admin"): (
        ATTRIBUTED_HERE,
        "idle-fleet, blocked-stale and stalled escalations into the admin's pane."),
    ("notify.py", "_deliver_agent"): (
        ATTRIBUTED_HERE,
        "StalledAlerter's self-heal nudge; the one tend push that does not route "
        "through the three helpers above."),
    ("cli.py", "_observe_live"): (
        BARE,
        "runtime.trust_answer() — an ANSWER consumed by the folder-trust chooser "
        "during the post-launch live check, "
        "not a message. Signing it would answer a different question."),
    ("cli.py", "_cmd_inbox"): (
        ATTRIBUTED_UPSTREAM,
        "msg = attribute(msg, _me(a)) at the top of _cmd_inbox. Behaviour pinned by "
        "test_inbox.py::test_a_pane_message_names_its_sender and its control."),
    ("cli.py", "_inbox_durable"): (
        ATTRIBUTED_UPSTREAM,
        "the same `msg` _cmd_inbox attributed, handed in as a parameter."),
    ("cli.py", "_dream_sweep"): (
        ATTRIBUTED_HERE,
        "scheduled dream assignment into the selected agent's pane; sender is "
        "the st dream scheduler, not a person."),
    ("cli.py", "route"): (
        ATTRIBUTED_HERE,
        "governed-workflow assignment from the quipu event router; sender is the "
        "ROUTER (`st quipu-events`), because no person composed it."),
    ("cli.py", "route_run"): (
        ATTRIBUTED_HERE,
        "shuttle run state change from the windowed-graph poll; same router, "
        "same reason — the graph composed this line, not a person."),
    ("dispatch.py", "go"): (
        ATTRIBUTED_UPSTREAM,
        "p.text was signed in Dispatcher.plan(), so the prefix also reaches "
        "--dry-run and triage. test_attribution.py has the differential pair."),
    ("runtime.py", "start"): (
        BARE,
        "the LAUNCH COMMAND LINE. A prefix here is a shell syntax error and every "
        "launch on the host fails. Both ClaudeRuntime and StoplessRuntime."),
}


class _Sends(ast.NodeVisitor):
    """Collect (function, node) for every `<something ending in panes>.send(...)`."""

    def __init__(self) -> None:
        self.found: list[tuple[str, ast.Call]] = []
        self._fn: list[str] = []

    def _enter(self, node):
        self._fn.append(node.name)
        self.generic_visit(node)
        self._fn.pop()

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter

    def visit_Call(self, node: ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "send":
            # `panes.send`, `self.panes.send`, `self._panes.send` — the receiver's
            # last identifier is what names it. Matching on the NAME rather than a
            # type keeps this a text-level guard, which is the honest thing: it is
            # a checklist enforcer, not a type checker.
            recv = f.value
            name = (recv.id if isinstance(recv, ast.Name)
                    else recv.attr if isinstance(recv, ast.Attribute) else "")
            if name.lstrip("_").endswith("panes"):
                self.found.append((self._fn[-1] if self._fn else "<module>", node))
        self.generic_visit(node)


def _sites() -> dict[tuple[str, str], ast.Call]:
    out: dict[tuple[str, str], ast.Call] = {}
    for path in sorted(SRC.glob("*.py")):
        v = _Sends()
        v.visit(ast.parse(path.read_text()))
        for fn, node in v.found:
            out[(path.name, fn)] = node
    return out


def test_no_pane_send_site_is_unclassified():
    """The guard. A new send site fails here until someone says which kind it is."""
    found = set(_sites())
    listed = set(INVENTORY)
    new = found - listed
    gone = listed - found
    assert not new, (
        f"UNCLASSIFIED pane send site(s): {sorted(new)}. Every send-keys call types "
        f"at the same prompt the operator uses, so an unsigned one is "
        f"indistinguishable from Stiwi (aegis-5vxmz). Wrap the text in "
        f"attribution.attribute(text, sender) and add it here as "
        f"{ATTRIBUTED_HERE!r} — or, if it carries a command line or a prompt "
        f"answer rather than prose, add it as {BARE!r} with the reason.")
    assert not gone, (
        f"inventory names send site(s) that no longer exist: {sorted(gone)}. "
        f"Removed, or moved to a renamed function? Re-classify rather than "
        f"deleting the entry blind — a site that moved is a site to re-read.")


def test_every_attributed_here_site_really_calls_attribute():
    """The control for the classification itself. Without this, 'attributed-here'
    would be a comment, and this repo's own line is that a claim nobody enforces
    is a comment."""
    sites = _sites()
    for key, (kind, why) in INVENTORY.items():
        if kind != ATTRIBUTED_HERE or key not in sites:
            continue   # a missing site is the FIRST test's finding, not this one's
        node = sites[key]
        text = node.args[1] if len(node.args) > 1 else None
        assert (isinstance(text, ast.Call)
                and isinstance(text.func, ast.Name)
                and text.func.id == "attribute"), (
            f"{key} is classified {ATTRIBUTED_HERE!r} ({why}) but its text argument "
            f"is not an attribute(...) call. Either sign it or re-classify it.")


def test_the_bare_sites_are_the_two_we_argued_for():
    """A positive control on the EXCEPTION LIST — the part of this design that can
    rot quietly. The whole scheme is 'the transport stays dumb and the exceptions
    are explicit'; if the exception list grows, the scheme has stopped paying for
    itself and the transport-level prefix deserves re-arguing. Failing here is a
    prompt to have that argument, not merely to bump a number."""
    bare = {k for k, (kind, _) in INVENTORY.items() if kind == BARE}
    assert bare == {("cli.py", "_observe_live"), ("runtime.py", "start")}, sorted(bare)
