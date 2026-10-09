"""Remote plates never need other owners' bodies or readiness rows."""
import json
import subprocess

import pytest

from shantytown import br
from shantytown.sd import SdTracker


def tracker(monkeypatch, *, fail_owner=None, fail_ready=False):
    t = SdTracker(quipu="https://example.test", prefix="test")
    calls = []
    rows = [dict(id="a", title="blocked", assignee="worker", status="open", priority=2),
            dict(id="z", title="ready", assignee="rig/worker", status="open", priority=2),
            dict(id="h", title="held", assignee="other", status="in_progress", priority=1)]

    def run(*args):
        assert args[0] in ("list", "ready")
        assert "--assignee" in args, "an unfiltered board read escaped"
        owner = args[args.index("--assignee") + 1]
        calls.append((args[0], owner))
        if owner == fail_owner or (fail_ready and args[0] == "ready"):
            return subprocess.CompletedProcess(args, 2, "", "unreadable")
        selected = [r for r in rows if r["assignee"] == owner]
        if args[0] == "ready":
            selected = [r for r in selected if r["id"] == "z"]
        return subprocess.CompletedProcess(args, 0, json.dumps({"issues": selected, "total": len(selected), "has_more": False}), "")

    monkeypatch.setattr(t, "_bd", run)
    return t, calls


def test_both_owner_spellings_preserve_readiness_rank_and_cache_per_agent(monkeypatch):
    t, calls = tracker(monkeypatch)
    monkeypatch.setattr(br, "name_the_blocker", lambda _, item: item)
    reader = br.plate_reader(t)
    assert reader("rig/worker").id == "z"
    assert reader("rig/worker").id == "z"
    assert calls == [("list", "rig/worker"), ("list", "worker"),
                     ("ready", "rig/worker"), ("ready", "worker")]
    assert reader("other").id == "h"
    assert len(calls) == 6
    assert br.plate(t, "rig/worker").id == "z"


def test_owner_read_failure_remains_loud_and_strict_reader_refuses(monkeypatch, capsys):
    t, _ = tracker(monkeypatch, fail_owner="worker")
    assert br.plate(t, "rig/worker").id == "z"
    assert "PLATE INCOMPLETE" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="owner worker"):
        br.plate_reader(t, require_complete=True)("rig/worker")


def test_failed_readiness_is_unknown_and_does_not_claim_no_ready_work(monkeypatch):
    t, _ = tracker(monkeypatch, fail_ready=True)
    assert not t.plate_ready_ids("rig/worker").complete
    from shantytown.answer import PartialAnswer
    with pytest.raises(PartialAnswer):
        t.plate_ready_ids("rig/worker").exact()
    assert br.plate(t, "rig/worker").id == "a"


def test_inbox_scopes_recipient_and_closed_receipts_and_refuses_partial(monkeypatch):
    from shantytown.inbox import TrackerInbox
    from shantytown.answer import PartialAnswer
    t = SdTracker(quipu="https://example.test", prefix="test")
    calls = []

    def run(*args):
        assert "--assignee" in args and "--title-contains" in args
        owner = args[args.index("--assignee") + 1]
        closed = "--all" in args
        calls.append((owner, closed))
        rows = [dict(id="receipt", title="inbox: marker payload", assignee=owner,
                     status="closed" if closed else "open", priority=2)]
        return subprocess.CompletedProcess(args, 0,
            json.dumps({"issues": rows, "total": 1, "has_more": False}), "")

    monkeypatch.setattr(t, "_bd", run)
    inbox = TrackerInbox(t, lambda: (_ for _ in ()).throw(AssertionError("whole board read")),
                         items_for=t.inbox_items)
    assert len(inbox.unread("worker")) == 1
    assert inbox.find_delivery("worker", "marker").read
    assert calls == [("worker", False), ("worker", False), ("worker", True), ("worker", True)]
    monkeypatch.setattr(t, "_bd", lambda *args: subprocess.CompletedProcess(args, 0,
        '{"issues":[],"total":10,"has_more":true}', ""))
    with pytest.raises(PartialAnswer):
        inbox.unread("worker")
    with pytest.raises(PartialAnswer):
        inbox.find_delivery("worker", "marker")

@pytest.mark.parametrize("stamp", ["2000-01-01", "2099-01-01", "malformed", ""])
def test_deferral_candidates_keep_both_representations_without_whole_open_read(monkeypatch, stamp):
    t = SdTracker(quipu="https://example.test", prefix="test")
    calls = []
    def run(*args):
        status = args[args.index("--status") + 1]
        calls.append(args)
        assert ("--defer-until-present" in args) == (status == "open")
        rows = [dict(id=status, status=status, defer_until=stamp if status == "open" else None)]
        return subprocess.CompletedProcess(args, 0, json.dumps(
            dict(issues=rows, total=1, has_more=False)), "")
    monkeypatch.setattr(t, "_bd", run)
    assert {r["id"] for r in t.deferred_rows().exact()} == {"deferred", "open"}
    assert len(calls) == 2

@pytest.mark.parametrize("payload", [
    dict(issues=[], total=2, has_more=True),
    dict(issues=[dict(id="open", status="open")], total=1, has_more=False),
])
def test_deferral_candidates_refuse_partial_or_unscoped_open_rows(monkeypatch, payload):
    from shantytown.answer import PartialAnswer
    t = SdTracker(quipu="https://example.test", prefix="test")
    monkeypatch.setattr(t, "_bd", lambda *args: subprocess.CompletedProcess(
        args, 0, json.dumps(payload), ""))
    with pytest.raises(PartialAnswer):
        t.deferred_rows().exact()


@pytest.mark.parametrize("payload", [
    {"issues": [{"id": "z"}], "total": 2, "has_more": True},
    {"issues": [{"id": "z"}], "total": 2, "has_more": False},
    {"issues": [{"id": "z"}, {}], "total": 2, "has_more": False},
    [{"id": "z"}, {}], [{"id": "z"}, {"id": 7}],
    [{"id": "z"}, {"id": " "}], [{"id": "z"}, None],
])
def test_invalid_readiness_is_unknown_and_preserves_plate_ranking(monkeypatch, payload):
    from shantytown.answer import PartialAnswer
    t, _ = tracker(monkeypatch)
    original = t._bd
    def read(*args):
        if args[0] == "ready":
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        return original(*args)
    monkeypatch.setattr(t, "_bd", read)
    answer = t.plate_ready_ids("rig/worker")
    assert not answer.complete
    with pytest.raises(PartialAnswer):
        answer.exact()
    assert br.plate(t, "rig/worker").id == "a", "unknown readiness changed the fallback rank"


@pytest.mark.parametrize("payload", [
    [{"id": "z"}],
    {"issues": [{"id": "z"}], "total": 1, "has_more": False},
    [], {"issues": [], "total": 0, "has_more": False},
])
def test_ready_bare_array_and_complete_envelope_controls(monkeypatch, payload):
    t = SdTracker(quipu="https://example.test", prefix="test")
    monkeypatch.setattr(t, "_bd", lambda *args: subprocess.CompletedProcess(
        args, 0, json.dumps(payload), ""))
    expected = {"z"} if payload else set()
    if isinstance(payload, dict) and not payload["issues"]:
        expected = set()
    assert t.plate_ready_ids("worker").exact() == expected


def test_scoped_inbox_keeps_legacy_mail_and_rejects_partial_prefix_reads(monkeypatch):
    from shantytown.inbox import TrackerInbox
    from shantytown.answer import PartialAnswer
    t = SdTracker(quipu="https://example.test", prefix="test")
    rows = [dict(id="modern", title="inbox: modern", assignee="worker", status="open"),
            dict(id="legacy", title="  mail: legacy", assignee="worker", status="open"),
            dict(id="work", title="work mentions mail: in text", assignee="worker", status="open")]
    calls = []
    def run(*args):
        assert args[args.index("--assignee") + 1] == "worker"
        prefix = args[args.index("--title-contains") + 1]
        calls.append(prefix)
        selected = [row for row in rows if prefix in row["title"]]
        return subprocess.CompletedProcess(args, 0,
            json.dumps({"issues": selected, "total": len(selected), "has_more": False}), "")
    monkeypatch.setattr(t, "_bd", run)
    assert {item.id for item in t.inbox_items("worker").exact()} == {"modern", "legacy"}
    inbox = TrackerInbox(t, lambda: (_ for _ in ()).throw(AssertionError("whole board read")),
                         items_for=t.inbox_items)
    assert {message.id for message in inbox.unread("worker")} == {"modern", "legacy"}
    assert set(calls) == {"inbox:", "mail:"}
    def partial(*args):
        result = run(*args)
        if args[args.index("--title-contains") + 1] == "mail:":
            payload = json.loads(result.stdout);payload["has_more"] = True
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        return result
    monkeypatch.setattr(t, "_bd", partial)
    with pytest.raises(PartialAnswer):
        inbox.unread("worker")
