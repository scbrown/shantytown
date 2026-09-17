"""The count is the thesis — so pin it (malcolm doc-defect #2).

cli.py's docstring once said "Ten commands" while the code had eleven (context
landed unannounced) and docs/cli.md said "nine" and "eight" — a three-way drift in
the one repo whose entire pitch is the exact command count. A number nobody enforces
is a comment. This test makes the docstring and the code prove each other: the
surface named in the docstring must equal the surface actually wired. Add a command
without updating the docstring (or vice versa) and this goes red.

SINCE 2026-09-17 THE SURFACE HAS SHAPE, not just size (Stiwi's ruling): six verbs
stay top-level and the other twenty-seven live under five groups. So this file
pins three things that have to move together:

  * the STRUCTURE — which names are verbs, which are groups, which leaf lives
    under which group — in cli.SURFACE, in the parser, and in the docstring;
  * the COUNT — thirty-three LEAVES (6 verbs + 27 grouped commands). That is the
    number: a group runs no handler and earns no slot. Every English copy of it
    (the docstring's first line, README, docs/cli.md — the latter two are pinned
    in test_docs_drift.py) is checked against the parser;
  * the ALIAS CONTRACT — every old top-level spelling still parses the same
    arguments into the same handler, prints exactly one line to stderr, and is
    hidden from `st --help`. Hidden matters: an alias that shows IS a top-level
    command to anyone reading the surface, which is how `role`/`project` once
    held the count at 19 (below).
"""
from __future__ import annotations
import argparse
import re

import pytest

import shantytown.cli as cli


# --- reading the parser --------------------------------------------------------

def _top(parser=None) -> argparse._SubParsersAction:
    parser = parser or cli.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("st has no subcommands?")


def _actual_surface() -> dict[str, frozenset[str] | None]:
    """{verb: None, group: frozenset(leaves)} — VISIBLE names only, read off the
    wired parser, not off cli.SURFACE (the point is to check that table)."""
    out: dict[str, frozenset[str] | None] = {}
    top = _top()
    for name in top.choices:                  # iteration sees only the visible
        sub = next((ac for ac in top.choices[name]._actions
                    if isinstance(ac, argparse._SubParsersAction)), None)
        is_group = name in cli.SURFACE and cli.SURFACE[name] is not None
        # `roles` and `window` carry sub-verbs of their own; only a GROUP's
        # subparsers count as leaves of the surface.
        out[name] = frozenset(sub.choices) if (sub is not None and is_group) else None
    return out


def _actual_leaves() -> set[str]:
    surface = _actual_surface()
    return {name for name, leaves in surface.items() if leaves is None} | {
        leaf for leaves in surface.values() if leaves for leaf in leaves}


def _hidden_aliases() -> set[str]:
    return set(_top().choices.hidden)


# --- reading the docstring -----------------------------------------------------

def _documented_surface() -> dict[str, frozenset[str] | None]:
    """Parse the surface lines at the top of the module docstring.

    Two line shapes, both separating commands with `·` and carrying NO prose:
      `task · go · inbox [--count]`            verbs (a line NOT starting with `·`)
      `agent → new · stop · cycle [--self]`    a group and its leaves
      `        · ask · answer`                 continues the line above
    Each token's first word is the command name (`hold gaming [...]` -> hold).
    The justification bullets ALSO start with `·` but each has an em-dash
    description (`· doctor — ...`); excluding em-dash lines keeps this from
    scraping a command name out of prose and masking a real drift.
    """
    doc = cli.__doc__ or ""
    out: dict[str, set[str] | None] = {}
    current: str | None = None                  # the group being continued
    for line in doc.splitlines():
        if "·" not in line or "—" in line:
            continue
        stripped = line.strip()
        if "→" in stripped:
            group, rest = stripped.split("→", 1)
            current = group.strip()
            out[current] = set()
        elif stripped.startswith("·"):
            rest = stripped                     # continuation of `current`
        else:
            current, rest = None, stripped      # a verbs line
        for token in rest.split("·"):
            token = token.strip()
            if not token:
                continue
            first = token.split()[0]
            if not re.fullmatch(r"[a-z]+", first):
                continue
            if current is None:
                out[first] = None
            else:
                out[current].add(first)         # type: ignore[union-attr]
    return {k: (frozenset(v) if v is not None else None) for k, v in out.items()}


# --- the structure: docstring == table == parser -------------------------------

def test_docstring_and_code_agree_on_the_surface():
    documented = _documented_surface()
    actual = _actual_surface()
    assert documented == actual, (
        f"command surface drifted — the docstring lists {documented} but the parser "
        f"wires {actual}. Update BOTH cli.SURFACE and the cli.py docstring; this is "
        f"deliberate friction: the count is the product."
    )


def test_the_table_is_the_parser():
    """cli.SURFACE is what build_parser() wires FROM, so it cannot drift from the
    parser by construction — this pins that the construction still holds (a leaf
    added with sub.add_parser instead of leaf() would show up here)."""
    table = {k: (frozenset(v) if v else None) for k, v in cli.SURFACE.items()}
    assert table == _actual_surface()


def test_six_verbs_five_groups_twenty_seven_leaves():
    """A bare number check too, so 'the docs claim N' is itself pinned.

    Grew to 13 with `project` — materialize the crew cards from the graph (the
    quipu-registry projection).

    Grew to 14 with `tend` — crew supervision, moved off the Gas Town watchdog
    and made native. This one was argued the other way first and lost on a
    specific ground worth keeping: it could have been a flag on `st crew`, and
    that is exactly the objection. `crew` is a READ, and `tend` is the only
    surface in this repo that can create a session and launch an agent. A
    consequence hidden behind a flag on a read command is a consequence someone
    triggers by running the safe-looking thing. The verb gets its own slot so the
    mutation shows up in shell history, in `--help`, and here.

    Grew to 15 with `attach`, 16 with `dashboard`, 17 with `subscribe`, 18 with
    `worktree`, 19 with `stats`, 20 with `start` and `input`, 21 with `init`;
    SHRANK to 19 when the `role`/`project` aliases were deleted; grew to 22 with
    `ask` + `answer`, 23 with `push`, and on to 31 with `harness` and 33 with
    `hold`/`window`/`cost`/`dream`. Each argument is in git history at the
    commit that grew the number; the one this file keeps applying is: a
    consequence hidden behind a flag on a read command is a consequence someone
    triggers by running the safe-looking thing, so a WRITE gets its own verb.

    REGROUPED at 33 (2026-09-17): the count did not move, the shape did. Six
    verbs stay top-level — task, go, inbox, crew, anchor, attach — and the other
    twenty-seven live under work / agent / fleet / repo / ops. The count is
    LEAVES: a group runs nothing, so it earns nothing. Thirty-eight names on the
    tree, thirty-three commands.

    Each command still earns its slot."""
    surface = _actual_surface()
    verbs = [n for n, leaves in surface.items() if leaves is None]
    groups = {n: leaves for n, leaves in surface.items() if leaves is not None}
    assert len(verbs) == 6, verbs
    assert len(groups) == 5, sorted(groups)
    assert sum(len(l) for l in groups.values()) == 27
    assert len(_actual_leaves()) == 33, (
        "the command count changed. If that's intended, update the number here, "
        "cli.SURFACE and the cli.py docstring together — and say why the surface "
        "grew in docs/cli.md."
    )


def test_the_grouping_is_the_one_ruled():
    """The specific assignment, verbatim from the ruling, so a leaf cannot quietly
    migrate between groups."""
    assert _actual_surface() == {
        "task": None, "go": None, "inbox": None, "crew": None, "anchor": None,
        "attach": None,
        "work": frozenset({"repool", "defer", "cost", "dream"}),
        "agent": frozenset({"new", "stop", "harness", "cycle", "input", "ask",
                            "answer", "log", "history", "stats"}),
        "fleet": frozenset({"start", "tend", "roles", "init", "hold", "window",
                            "dashboard"}),
        "repo": frozenset({"worktree", "push", "context"}),
        "ops": frozenset({"doctor", "subscribe", "help"}),
    }


def test_the_prose_numbers_in_the_docstring_match_the_parser():
    """THE ONE COPY OF THE COUNT NOTHING WAS CHECKING (found while adding `cycle`).

    This file pins the SET of commands (docstring list vs parser) and bare integers
    above. It never pinned the ENGLISH WORDS in the first line of cli.py's
    docstring — and that word had silently drifted to "Twenty-two" while the
    parser wired twenty-three. 'A number nobody enforces is a comment.' The list
    was enforced, so the drift hid in the sentence ABOVE the list — the line a
    reader actually quotes. Now every number in that line is checked: verbs,
    groups, grouped commands and the total."""
    words = {5: "five", 6: "six", 7: "seven", 20: "twenty", 21: "twenty-one",
             22: "twenty-two", 23: "twenty-three", 24: "twenty-four",
             25: "twenty-five", 26: "twenty-six", 27: "twenty-seven",
             28: "twenty-eight", 29: "twenty-nine", 30: "thirty",
             31: "thirty-one", 32: "thirty-two", 33: "thirty-three",
             34: "thirty-four", 35: "thirty-five", 36: "thirty-six"}
    surface = _actual_surface()
    n_verbs = sum(1 for l in surface.values() if l is None)
    n_groups = sum(1 for l in surface.values() if l is not None)
    n_grouped = sum(len(l) for l in surface.values() if l is not None)
    first = (cli.__doc__ or "").splitlines()[0].lower()
    for n, what in ((n_verbs, "verbs"), (n_groups, "groups"),
                    (n_grouped, "grouped commands"), (n_verbs + n_grouped, "in all")):
        assert re.search(rf"\b{words[n]}\b", first), (
            f"the parser wires {n} {what} but cli.py's docstring opens with "
            f"{first!r} — update the words, not just the list.")


# --- the deleted aliases stay deleted -----------------------------------------

def test_the_deleted_aliases_are_really_gone():
    """The negative control for the deletion of `role`/`project` (2026-07-24).

    Both old spellings must be UNKNOWN COMMANDS, not silently-accepted ones — and
    not hidden aliases either: the two-release alias window below is for the
    regrouping, and these two had theirs.
    """
    from shantytown.cli import main

    assert "role" not in _top().choices and "role" not in _hidden_aliases()
    assert "project" not in _top().choices and "project" not in _hidden_aliases()
    for gone in (["role", "set", "ellie", "worker"], ["project", "-n"]):
        with pytest.raises(SystemExit) as e:
            main(gone)
        assert e.value.code == 2, "argparse refuses an unknown command with 2"


def test_the_canonical_spellings_still_work():
    """...and the handlers behind them are UNTOUCHED. Only the alias parsers went;
    `roles set` / `roles sync` dispatch to the same functions they always did."""
    assert "roles" in _actual_surface()["fleet"]
    assert cli._cmd_role is not None and cli._cmd_project is not None


# --- the alias contract for the regrouping ------------------------------------

def test_every_old_spelling_is_a_hidden_alias_of_the_same_parser():
    """The 27 grouped commands each keep their old top-level spelling: the SAME
    parser object (so the arguments cannot drift between the two spellings),
    resolvable by name, and invisible to every rendering."""
    top = _top()
    assert _hidden_aliases() == set(cli.GROUP_OF) == {
        leaf for leaves in cli.SURFACE.values() if leaves for leaf in leaves}
    for leaf, group in cli.GROUP_OF.items():
        group_sub = next(ac for ac in top.choices[group]._actions
                         if isinstance(ac, argparse._SubParsersAction))
        assert top.choices[leaf] is group_sub.choices[leaf], leaf
        assert leaf not in list(top.choices), f"{leaf} leaks into iteration"
    assert not [a.dest for a in top._choices_actions if a.dest in cli.GROUP_OF]


def test_st_help_lists_exactly_the_six_verbs_and_five_groups(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    listed = re.findall(r"^    ([a-z]+) ", out, re.M)
    assert listed == list(cli.SURFACE), listed
    # The choice braces on the usage line and the header, too — not just the rows.
    braces = set(re.findall(r"\{([a-z,]+)\}", out))
    command_braces = {b for b in braces if b.split(",")[0] in cli.SURFACE}
    assert command_braces == {",".join(cli.SURFACE)}, braces


def test_a_group_help_lists_its_leaves_in_the_declared_order(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["agent", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert re.findall(r"^    ([a-z]+) ", out, re.M) == list(cli.SURFACE["agent"])


def test_both_spellings_parse_to_the_same_namespace(monkeypatch):
    monkeypatch.setenv(cli.QUIET_ALIASES_ENV, "1")
    cases = [
        (["cycle", "ada", "--self", "-r", "x"], ["agent", "cycle", "ada", "--self", "-r", "x"]),
        (["roles", "sync", "--dry-run"], ["fleet", "roles", "sync", "--dry-run"]),
        (["hold", "gaming", "--status"], ["fleet", "hold", "gaming", "--status"]),
        (["window", "plan", "w1"], ["fleet", "window", "plan", "w1"]),
        (["stats", "--files", "ada"], ["agent", "stats", "--files", "ada"]),
        (["defer", "i1", "human", "--reason", "r"], ["work", "defer", "i1", "human", "--reason", "r"]),
        (["help", "haul"], ["ops", "help", "haul"]),
        (["push", "repo", "--branch", "b"], ["repo", "push", "repo", "--branch", "b"]),
    ]
    for old, new in cases:
        a, b = vars(cli._parse_args(old)), vars(cli._parse_args(new))
        b.pop(f"{new[0]}_cmd")              # the group's own dest; the alias has none
        assert a == b, (old, new)
        assert a["cmd"] == old[0] and a["group"] == new[0]


def test_the_old_spelling_prints_exactly_one_line_to_stderr(monkeypatch, capsys):
    """The notice, verbatim, and NOTHING else on stderr — `--help` still exits 0
    and stdout is the leaf's help, so the alias changes only the spelling."""
    monkeypatch.delenv(cli.QUIET_ALIASES_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        cli.main(["cycle", "--help"])
    assert e.value.code == 0
    out, err = capsys.readouterr()
    assert err == "st cycle is now st agent cycle; the old spelling goes away in two releases.\n"
    assert out.startswith("usage: st agent cycle")

    with pytest.raises(SystemExit):
        cli.main(["--root", "/nowhere", "roles", "sync", "--help"])
    _, err = capsys.readouterr()
    assert err == "st roles is now st fleet roles; the old spelling goes away in two releases.\n"

    # The new spelling says nothing at all.
    with pytest.raises(SystemExit):
        cli.main(["agent", "cycle", "--help"])
    assert capsys.readouterr().err == ""


def test_the_notice_is_silenced_by_the_env(monkeypatch, capsys):
    monkeypatch.setenv(cli.QUIET_ALIASES_ENV, "1")
    with pytest.raises(SystemExit) as e:
        cli.main(["cycle", "--help"])
    assert e.value.code == 0
    assert capsys.readouterr().err == ""


def test_an_unknown_option_on_a_nested_leaf_is_a_usage_error(capsys):
    """On Python 3.14 `st fleet roles set lex worker --reports-to hammond` raised a
    TypeError out of parse_intermixed_args (a parser with subparsers cannot be
    intermixed-parsed) instead of a usage error. Now the intermixed re-parse
    walks down to the LEAF parser, so this is exit 2 with a usage line, under
    either spelling."""
    for argv in (["roles", "set", "lex", "worker", "--reports-to", "hammond"],
                 ["fleet", "roles", "set", "lex", "worker", "--reports-to", "hammond"]):
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 2
        err = capsys.readouterr().err
        assert "unrecognized arguments: --reports-to hammond" in err
        assert "usage: st fleet roles set" in err


def test_the_intermixed_path_still_reopens_a_variadic_positional():
    """The case _parse_args exists for: a flag BEFORE the trailing positional."""
    a = cli._parse_args(["inbox", "ian", "-n", "hi"])
    assert a.cmd == "inbox" and a.message == ["hi"] and a.dry_run


def test_the_machine_readable_flags_stay_top_level():
    """An external status bar shells out to these; they are the part of the
    surface that must not move at all."""
    for argv, flag in ((["anchor", "--short"], "short"), (["anchor", "--events"], "events"),
                       (["anchor", "--harness"], "harness"), (["crew", "--count"], "count"),
                       (["crew", "--governor"], "governor"), (["inbox", "--count"], "count")):
        a = cli._parse_args(argv)
        assert a.cmd == argv[0] and a.group is None and getattr(a, flag) is True
