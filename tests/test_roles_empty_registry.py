""""Nobody exists" is not a clean bill of health unless the registry vouches for it.

THE FALSE PASS. `Report.verdict` is "worst wins" over its rows, so an EMPTY report
is OK — there is no row to be worse than OK. `roles --check` therefore printed

    0 agents, every one reports somewhere.        exit 0

for a registry that had told it nothing. test_doctor.py's module docstring already
cites this exact string as the canonical example of "a doctor that only ever says
healthy is indistinguishable from a broken one", and one instance of it WAS closed:
`FilesRegistry.all` raises on an absent directory, so "I could not look" stopped
arriving as "nobody exists".

`--registry quipu` reached the other instance. Measured on the live graph: 12 real
CrewMembers, `all() -> []` under a wrong namespace, and a clean bill of health for
a crew the checker had never spoken to.

WHY THIS IS NOT "EMPTY MEANS CANNOT TELL". The two registries genuinely differ, and
flattening them would break a real answer:

  FilesRegistry   the directory IS the whole search space, and an absent directory
                  already raises. Reaching [] means the space was read and nothing
                  was in it — complete. A fresh install with no cards is an honest
                  zero, and stays OK.

  QuipuRegistry   a graph is never "absent", so there is no second signal; and WHICH
                  entities are `a:CrewMember` is selected by the ontology namespace,
                  which is deployment config the client cannot validate. A wrong
                  namespace is answered truthfully with zero rows. Empty therefore
                  conflates "nobody exists" with "I looked in the wrong place", and
                  nothing downstream can separate them.

So the verdict belongs to the REGISTRY (`Registry.empty_note`), and `roles.check`
stays registry-agnostic — the same "quipu has not leaked into the core" guarantee
that makes one check() run over both impls.

THE DEFAULT IS FAIL-SAFE, and the direction is the point: a registry that does not
answer is not treated as having answered "my empty is fine". Forgetting to vouch
costs a loud `cannot tell`; forgetting to warn costs a false clean bill of health.
"""

from __future__ import annotations

import json
from pathlib import Path

from shantytown import roles
from shantytown.files import FilesRegistry
from shantytown.quipu import QuipuRegistry

WRONG_NS = "http://wrong.example/ontology/"


def _quipu(rows, onto=WRONG_NS):
    """A REACHABLE quipu answering `rows`. Reachable is the whole point: every
    other guard in that client keys on the server being wrong, and none of them
    is firing here."""
    r = QuipuRegistry(server="http://test.invalid", onto=onto)
    r._query = lambda sparql: rows
    return r


# --- the bug -----------------------------------------------------------------

def test_zero_rows_from_a_REACHABLE_graph_is_cannot_tell_not_ok():
    report = roles.check(_quipu([]))
    assert report.verdict == roles.CANNOT_TELL


def test_the_verdict_does_not_render_as_a_pass():
    """The exact string the false pass printed must not appear. Asserting on the
    rendering and not only the verdict, because the verdict was never what anybody
    read — the line was."""
    out = roles.check(_quipu([])).render()
    assert "every one reports somewhere" not in out
    assert "NOT a clean result" in out


def test_the_note_names_the_NAMESPACE_and_the_server():
    """The only actionable fact. An operator cannot tell "nobody exists" from "wrong
    namespace" without seeing which namespace was queried — and nothing in the
    client can make that comparison for them."""
    out = roles.check(_quipu([])).render()
    assert WRONG_NS in out
    assert "http://test.invalid" in out
    assert "SHANTY_ONTO_NS" in out


def test_the_cli_exit_code_is_2_for_an_empty_graph(monkeypatch, tmp_path, capsys):
    """End to end: CANNOT_TELL has to reach the shell as exit 2, or the guard only
    changes text. This is the behaviour a cron entry or a supervisor keys on."""
    from shantytown import cli

    (tmp_path / "crew").mkdir()
    monkeypatch.setattr(cli, "_registry", lambda a: _quipu([]))
    monkeypatch.setattr(cli, "_panes", lambda a: type("P", (), {"cmdline": lambda *x: None})())
    rc = cli.main(["--root", str(tmp_path), "--registry", "quipu", "roles", "--check"])
    assert rc == cli.CANNOT_TELL
    assert "every one reports somewhere" not in capsys.readouterr().out


# --- positive controls: a guard that is always on is not a guard --------------

def test_a_NON_empty_graph_is_unaffected():
    report = roles.check(_quipu([
        {"s": WRONG_NS + "hammond"},
        {"s": WRONG_NS + "ian", "rt": WRONG_NS + "hammond"},
    ], onto=WRONG_NS))
    # hammond has a report and no lead -> administrator/root -> OK, and the empty
    # branch must not have fired at all.
    assert report.verdict == roles.OK
    out = report.render()
    assert "hammond" in out and "ian" in out
    assert "(registry)" not in out, "the empty branch fired on a non-empty graph"


def test_an_empty_FILES_registry_is_still_a_real_answer(tmp_path: Path):
    """THE BEHAVIOUR THAT MUST NOT REGRESS. FilesRegistry deliberately draws the
    empty-vs-absent line and earns it: the directory is the whole search space. A
    fresh install with no cards is an honest zero and stays exit 0. Flattening
    "empty" to "cannot tell" everywhere would have broken this."""
    crew = tmp_path / "crew"
    crew.mkdir()
    report = roles.check(FilesRegistry(crew))
    assert report.verdict == roles.OK
    assert "every one reports somewhere" in report.render()


def test_an_ABSENT_files_registry_is_still_cannot_tell(tmp_path: Path):
    """The instance that was already fixed, pinned so this change did not trade one
    for the other."""
    report = roles.check(FilesRegistry(tmp_path / "nope"))
    assert report.verdict == roles.CANNOT_TELL


def test_a_populated_files_registry_is_unaffected(tmp_path: Path):
    crew = tmp_path / "crew"
    crew.mkdir()
    (crew / "sattler.json").write_text(json.dumps({"role": "administrator"}))
    (crew / "tim.json").write_text(json.dumps({"role": "worker", "reports_to": "sattler"}))
    assert roles.check(FilesRegistry(crew)).verdict == roles.OK


# --- the contract itself -----------------------------------------------------

def test_the_two_registries_answer_the_question_DIFFERENTLY(tmp_path: Path):
    """The asymmetry is the whole design. If both impls returned the same thing this
    method would not need to exist, and the fix would have belonged in check()."""
    assert FilesRegistry(tmp_path).empty_note() is None
    assert QuipuRegistry(server="http://test.invalid", onto=WRONG_NS).empty_note() is not None


def test_a_registry_that_does_not_implement_it_is_NOT_trusted():
    """FAIL-SAFE DEFAULT, and the direction is deliberate. A third registry — or a
    test double — that never considered the question must not inherit "my empty is
    fine", because that is precisely the assumption that shipped the bug."""
    class Mute:
        def all(self):
            return []

        def get(self, name):
            raise LookupError(name)

    report = roles.check(Mute())
    assert report.verdict == roles.CANNOT_TELL
    assert "does not say" in report.render()


def test_a_registry_may_VOUCH_for_its_empty_answer():
    """The other half of the default: opting in works, so the guard is a contract
    and not a blanket ban on empty registries."""
    class Vouching:
        def all(self):
            return []

        def get(self, name):
            raise LookupError(name)

        def empty_note(self):
            return None

    assert roles.check(Vouching()).verdict == roles.OK


def test_an_unreachable_registry_still_outranks_the_empty_branch():
    """Order matters: `all()` raising is "I could not look" and must keep its own
    message. The empty branch must not swallow it into the generic namespace advice."""
    class Down:
        def all(self):
            raise OSError("boom")

        def get(self, name):
            raise LookupError(name)

    report = roles.check(Down())
    assert report.verdict == roles.CANNOT_TELL
    assert "boom" in report.render()
