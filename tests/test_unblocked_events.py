"""aegis-sfpfwf: tend consumes chaski's workitem-unblocked events.

Every arm re-checks br: the event is a claim about the graph, br is the truth."""
from shantytown.notify import BlockedMisstatusAlerter
from shantytown.unblocked_events import UnblockedEventConsumer, classify, firings_query


def _dep(bid, status):
    return {"id": bid, "dependency_type": "blocks", "status": status}


def _bead(status, *deps, labels=(), defer_until=None):
    return {"status": status, "dependencies": list(deps), "labels": list(labels),
            "defer_until": defer_until, "title": "t"}


def _firing(n, bead, at="2026-09-30T02:00:00Z"):
    return {"firing": f"f{n}", "bead": bead, "at": at}


class _World:
    def __init__(self, beads, firings, push_ok=True):
        self.beads, self.firings = beads, firings
        self.reopened, self.comments, self.msgs, self.since = [], [], [], []
        self.push_ok = push_ok

    def consumer(self, tmp_path):
        def reopen(b):
            self.reopened.append(b)
            self.beads[b]["status"] = "open"

        def push(reg, panes, msg):
            self.msgs.append(msg)
            return "sattler" if self.push_ok else None

        def firings(since):
            self.since.append(since)
            return [f for f in self.firings if f["at"] >= since]

        return UnblockedEventConsumer(
            tmp_path, reg=None, panes=None, push=push, firings=firings,
            show=lambda b: dict(self.beads[b]), reopen=reopen,
            comment=lambda b, body: self.comments.append((b, body)))


def test_classify_the_four_verdicts():
    assert classify(_bead("blocked", _dep("x", "closed")))[0] == "corrected"
    assert classify(_bead("deferred", _dep("x", "closed"),
                          defer_until="2026-10-03T14:00:00Z"))[0] == "held"
    assert classify(_bead("open", labels=["blocked:external"]))[0] == "held"
    assert classify(_bead("blocked", _dep("x", "open")))[0] == "mismatch"
    assert classify(_bead("open"))[0] == "noop"


def test_a_stale_blocked_status_is_CORRECTED_and_the_firing_cited(tmp_path):
    w = _World({"b1": _bead("blocked", _dep("x", "closed"), _dep("y", "closed"))},
               [_firing(1, "b1")])
    got = w.consumer(tmp_path).sweep()
    assert got == [("b1", "corrected")]
    assert w.reopened == ["b1"]
    assert w.comments and "f1" in w.comments[0][1]
    assert "blocked -> open" in w.msgs[0]


def test_a_deferral_or_external_label_is_HELD_never_lifted(tmp_path):
    """The DeferralAlerter rule survives: nothing here un-defers."""
    w = _World({"d": _bead("deferred", defer_until="2026-10-03T14:00:00Z"),
                "e": _bead("open", labels=["blocked:external"])},
               [_firing(1, "d"), _firing(2, "e")])
    got = w.consumer(tmp_path).sweep()
    assert got == [("d", "held"), ("e", "held")]
    assert w.reopened == [] and w.comments == []
    assert "deferred until" in w.msgs[0] and "blocked:external" in w.msgs[1]


def test_SABOTAGE_an_event_for_a_bead_br_still_blocks_changes_nothing(tmp_path):
    w = _World({"s": _bead("blocked", _dep("x", "closed"), _dep("y", "open"))},
               [_firing(1, "s")])
    got = w.consumer(tmp_path).sweep()
    assert got == [("s", "mismatch")]
    assert w.reopened == [] and "MISMATCH" in w.msgs[0] and "y" in w.msgs[0]


def test_a_second_pass_does_nothing(tmp_path):
    w = _World({"b1": _bead("blocked", _dep("x", "closed"))}, [_firing(1, "b1")])
    w.consumer(tmp_path).sweep()
    w.beads["b1"]["status"] = "blocked"      # even if br regressed, the firing is spent
    assert w.consumer(tmp_path).sweep() == []
    assert w.reopened == ["b1"]
    assert w.since[-1] == "2026-09-30T02:00:00Z"   # cursor advanced


def test_an_undelivered_report_is_retried_not_lost(tmp_path):
    w = _World({"d": _bead("deferred", defer_until="2026-10-03T14:00:00Z")},
               [_firing(1, "d")], push_ok=False)
    w.consumer(tmp_path).sweep()
    w.push_ok = True
    assert w.consumer(tmp_path).sweep() == [("d", "held")]


def test_an_unreadable_bead_stops_the_pass_without_skipping_it(tmp_path):
    w = _World({"ok": _bead("blocked", _dep("x", "closed"))},
               [_firing(1, "gone"), _firing(2, "ok", at="2026-09-30T03:00:00Z")])
    c = w.consumer(tmp_path)
    assert c.sweep() == []           # KeyError on "gone": stop, keep order
    assert w.reopened == []


def test_an_unreadable_quipu_fails_open(tmp_path):
    def boom(since):
        raise OSError("quipu down")
    c = UnblockedEventConsumer(tmp_path, None, None, firings=boom,
                               show=lambda b: {}, reopen=lambda b: None,
                               comment=lambda b, x: None, push=lambda *a: "x")
    assert c.sweep() == []


def test_CONTROL_a_misstatused_bead_with_no_firing_says_EVENT_MISSED(tmp_path):
    msgs = []
    rows = [{"id": "m", "status": "blocked", "title": "t"}]
    alerter = BlockedMisstatusAlerter(
        tmp_path, reg=None, panes=None, push=lambda r, p, m: msgs.append(m) or "x",
        bd_blocked=lambda: rows, bd_show=lambda b: _bead("blocked", _dep("x", "closed")),
        now=1.0, seen=lambda: {})
    assert alerter.sweep() == ["m"] and "EVENT MISSED" in msgs[0]


def test_a_bead_the_consumer_saw_is_not_called_missed(tmp_path):
    msgs = []
    rows = [{"id": "m", "status": "blocked", "title": "t"}]
    alerter = BlockedMisstatusAlerter(
        tmp_path, reg=None, panes=None, push=lambda r, p, m: msgs.append(m) or "x",
        bd_blocked=lambda: rows, bd_show=lambda b: _bead("blocked", _dep("x", "closed")),
        now=1.0, seen=lambda: {"m": "2026-09-30T02:00:00Z"})
    alerter.sweep()
    assert "EVENT MISSED" not in msgs[0]


def test_the_query_names_the_reaction_and_pages_from_the_cursor():
    q = firings_query("2026-09-30T02:00:00Z")
    assert "reaction-workitem-unblocked" in q and '>= "2026-09-30T02:00:00Z"' in q
    assert "FILTER" not in firings_query("")


def test_a_bead_awaiting_a_HUMAN_decision_is_held_not_reopened(tmp_path):
    """dearing-rev-129: decision-stiwi / needs-human / waiting-* and any blocked-*
    variant hold the bead; only blocked:bead is what the event resolves."""
    for label in ("decision-stiwi", "decision-needed", "needs-stiwi", "needs-human",
                  "waiting-on-vendor", "blocked-external", "parked:by-design"):
        verdict, reason = classify(_bead("blocked", _dep("x", "closed"), labels=[label]))
        assert (verdict, label in reason) == ("held", True), label
    assert classify(_bead("blocked", _dep("x", "closed"),
                          labels=["blocked:bead", "ontology"]))[0] == "corrected"
    w = _World({"h": _bead("blocked", _dep("x", "closed"), labels=["decision-stiwi"])},
               [_firing(1, "h")])
    assert w.consumer(tmp_path).sweep() == [("h", "held")] and w.reopened == []
