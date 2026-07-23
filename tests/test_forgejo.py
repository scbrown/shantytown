"""ForgejoTracker — the proof-of-generality adapter (internal-ref).

The harness must not know what a work item is: these tests drive the Tracker
protocol (create/get/update) against a fake transport and pin the honest
status mapping (Forgejo has only open/closed; in_progress is carried by the
assignment, never faked with labels) and the 404-vs-could-not-tell split.
"""
from __future__ import annotations
import urllib.error

import pytest

from shantytown.forgejo import ForgejoTracker


class _Fake(ForgejoTracker):
    def __init__(self, **kw):
        super().__init__(repo="stiwi/example", base_url="http://forge.invalid", **kw)
        self.calls = []
        self.responses = []

    def _request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.responses:
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return {"number": 7, "title": "t", "state": "open", "assignee": None}


def test_repo_must_be_owner_name():
    with pytest.raises(ValueError):
        ForgejoTracker(repo="justaname")


def test_create_posts_title_body_and_bare_assignee():
    t = _Fake()
    item = t.create("fix the thing", body="details",
                    assignee="beads_aegis/crew/weaver")
    m, path, body = t.calls[0]
    assert (m, path) == ("POST", "/issues")
    assert body == {"title": "fix the thing", "body": "details",
                    "assignees": ["weaver"]}
    assert item.id == "7" and item.status == "open"


def test_get_maps_issue_to_workitem():
    t = _Fake()
    t.responses = [{"number": 3, "title": "x", "state": "closed",
                    "assignee": {"login": "ada"}}]
    item = t.get("3")
    assert (item.id, item.status, item.assignee) == ("3", "closed", "ada")


def test_get_404_is_lookup_error_and_5xx_is_could_not_tell():
    t = _Fake()
    t.responses = [urllib.error.HTTPError("u", 404, "nf", {}, None)]
    with pytest.raises(LookupError, match="no issue"):
        t.get("99")
    t.responses = [urllib.error.HTTPError("u", 502, "bad", {}, None)]
    with pytest.raises(LookupError, match="could not tell"):
        t.get("99")


def test_update_in_progress_assigns_but_never_changes_state():
    """The honest lossy mapping: Forgejo cannot say in_progress; the
    assignment IS the signal, and the issue must stay open."""
    t = _Fake()
    t.update("7", status="in_progress", assignee="crew/weaver")
    m, path, body = t.calls[0]
    assert (m, path) == ("PATCH", "/issues/7")
    assert body == {"assignees": ["weaver"]}          # no "state" key at all


def test_update_closed_and_open_map_to_state():
    t = _Fake()
    t.update("7", status="closed")
    assert t.calls[0][2] == {"state": "closed"}
    t.update("7", status="open")
    assert t.calls[1][2] == {"state": "open"}


def test_update_with_nothing_to_say_makes_no_request():
    t = _Fake()
    t.update("7")
    assert t.calls == []
