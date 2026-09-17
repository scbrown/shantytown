"""The hook-side matchers accept BOTH spellings during the alias window.

task_order.boundary_task and cost's confirmed-defer hook key on a command an
AGENT typed (`st stats --begin-task X`, `st defer X ...`). The regrouping moved
those to `st agent stats` / `st work defer`, and an agent may type either while
the old spelling is aliased — a matcher that recognised only one would silently
lose task boundaries (or focus closes) for whichever half of the fleet learned
the other spelling. surface.ungroup is the one place that folds the two.
"""
from __future__ import annotations

from shantytown import cost, task_order
from shantytown.surface import GROUP_OF, SURFACE, ungroup


def test_ungroup_folds_a_grouped_spelling_and_leaves_everything_else_alone():
    assert ungroup(["st", "agent", "stats", "--begin-task", "p-1"]) == ["st", "stats", "--begin-task", "p-1"]
    assert ungroup(["st", "stats", "--begin-task", "p-1"]) == ["st", "stats", "--begin-task", "p-1"]
    assert ungroup(["st", "work", "defer", "p-1", "human"]) == ["st", "defer", "p-1", "human"]
    # a verb followed by an argument that happens to spell a leaf is NOT a group
    assert ungroup(["st", "inbox", "stats"]) == ["st", "inbox", "stats"]
    # a group followed by a non-leaf is left for argparse to refuse
    assert ungroup(["st", "agent", "bogus"]) == ["st", "agent", "bogus"]
    assert ungroup(["st"]) == ["st"]


def test_every_leaf_ungroups_under_its_own_group_only():
    for leaf, group in GROUP_OF.items():
        assert ungroup(["st", group, leaf]) == ["st", leaf]
        for other in SURFACE:
            if SURFACE[other] is not None and other != group:
                assert ungroup(["st", other, leaf]) == ["st", other, leaf]


def test_a_task_boundary_is_declared_under_either_spelling():
    for command in ("st agent stats --begin-task p-1", "st stats --begin-task p-1",
                    "/usr/local/bin/st agent stats --begin-task p-1"):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        assert task_order.boundary_task(payload) == "p-1", command
    # still only a STANDALONE command: a compound could act first
    assert task_order.boundary_task({"tool_name": "Bash", "tool_input": {
        "command": "st agent stats --begin-task p-1 && rm -rf x"}}) is None


def test_a_confirmed_defer_clips_the_focus_under_either_spelling(tmp_path):
    """The mirror of test_cost's clip test, driven with the OLD spelling: the
    capture store must record the defer under `st defer` exactly as it does
    under `st work defer`, or a focus stays open for whoever typed the alias."""
    import sqlite3
    conn = sqlite3.connect(tmp_path / "stats.sqlite")
    conn.executescript(task_order._SCHEMA)
    conn.execute("INSERT INTO task_contexts(ts,agent,session,task,paired_start) "
                 "VALUES (?,?,?,?,?)", (30, "worker", "session", "p-c", 0))
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "session",
               "tool_use_id": "defer1",
               "tool_input": {"command": "st defer p-c human --reason-file reason"},
               "tool_response": {"stdout": "p-c deferred as blocked:human", "exit_code": 0}}
    task_order.capture(conn, payload, 35, "worker")
    start = {**payload, "tool_use_id": "begin2",
             "tool_input": {"command": "st stats --begin-task p-c"}}
    task_order.capture(conn, {**start, "hook_event_name": "PreToolUse"}, 40, "worker")
    task_order.capture(conn, {**start, "tool_response": {"stdout": task_order.marker("p-c")}},
                       41, "worker")
    conn.commit(); conn.close()
    focus = cost.bindings(tmp_path, "project", {})
    assert focus[-1]["bead"] == "p-c" and focus[-1]["start"] == 41
