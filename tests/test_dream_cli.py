from __future__ import annotations

import types

from shantytown import cli, dream, governor
from shantytown.answer import Answer
from shantytown.protocols import Agent, WorkItem


class _Registry:
    def __init__(self, cards): self.cards = cards
    def all(self): return Answer.complete_read(self.cards, how="dream test roster")


class _Panes:
    def __init__(self): self.sent = []
    def exists(self, pane): return True
    def send(self, pane, text): self.sent.append((pane, text))


class _Tracker:
    def __init__(self): self.fields = None
    def create(self, title, **fields):
        self.fields = (title, fields)
        return WorkItem(id="aegis-dream1", title=title)


def _wire(monkeypatch, tmp_path, pct):
    card = Agent(name="arnold", role="worker", pane="p1", harness="codex")
    reg, panes, tracker = _Registry([card]), _Panes(), _Tracker()
    policy = governor.Policy(source="stub", stub_pct=pct,
                             delegation_reserve_pct=10,
                             tiers=(governor.Tier(at=100, min_priority=0),))
    verdict = types.SimpleNamespace(
        signal_lost=False, by_window={"seven_day": pct}, pct=pct,
        admits=lambda _item: "")
    gov = types.SimpleNamespace(policy=policy,
                                evaluate=lambda persist=False: verdict)
    cfg = types.SimpleNamespace(
        dream=dream.Policy(enabled=True, min_headroom_pct=20),
        governor=types.SimpleNamespace(by_harness={"codex": policy}))
    monkeypatch.setattr(cli, "_registry", lambda a: reg)
    monkeypatch.setattr(cli, "_runtime", lambda a, p: object())
    monkeypatch.setattr(cli, "_tracker", lambda a: tracker)
    monkeypatch.setattr(cli, "_governors", lambda a: (cfg, {"codex": gov}))
    monkeypatch.setattr(cli, "_governor_for",
                        lambda cfg, gs, card, root: ("codex", gov, None))
    monkeypatch.setattr("shantytown.feed_check._bd_ready", lambda cwd=None: [])
    monkeypatch.setattr("shantytown.feed_check.bd_in_progress", lambda cwd=None: [])
    monkeypatch.setattr("shantytown.feed_check.bd_cwd", lambda reg: str(tmp_path))
    monkeypatch.setattr("shantytown.feed_check.free_feedable_workers",
                        lambda *args, **kwargs: ["arnold"])
    args = types.SimpleNamespace(root=str(tmp_path))
    return args, cfg, reg.cards, panes, tracker


def test_sweep_ignores_ready_work_held_by_provider_governor(monkeypatch, tmp_path):
    args, cfg, cards, panes, tracker = _wire(monkeypatch, tmp_path, pct=10)
    monkeypatch.setattr("shantytown.feed_check._bd_ready", lambda cwd=None: [
        {"id": "aegis-held", "title": "held", "priority": 2, "labels": []},
    ])
    _cfg, governors = cli._governors(args)
    governors["codex"].evaluate().admits = lambda _item: "priority floor holds P2"

    cycle, item_id, reason = cli._dream_sweep(args, cfg, cards, panes)

    assert cycle is not None and (item_id, reason) == ("aegis-dream1", "created")
    assert tracker.fields is not None


def test_sweep_creates_lowest_priority_review_artifact_and_wakes_agent(monkeypatch, tmp_path):
    args, cfg, cards, panes, tracker = _wire(monkeypatch, tmp_path, pct=10)
    cycle, item_id, reason = cli._dream_sweep(args, cfg, cards, panes)
    assert (item_id, reason) == ("aegis-dream1", "created")
    title, fields = tracker.fields
    assert title.startswith("DREAM consolidate")
    assert fields["priority"] == 4
    assert fields["assignee"] == "arnold"
    assert "dream-discrepancy" in fields["labels"]
    assert "do not mutate infrastructure" in fields["description"]
    assert panes.sent and "aegis-dream1" in panes.sent[0][1]
    assert dream.State(tmp_path).read()["last_item"] == "aegis-dream1"


def test_sweep_protects_delegation_reserve(monkeypatch, tmp_path):
    args, cfg, cards, panes, tracker = _wire(monkeypatch, tmp_path, pct=95)
    cycle, item_id, reason = cli._dream_sweep(args, cfg, cards, panes)
    assert cycle is None and item_id == ""
    assert "measured spare capacity" in reason
    assert tracker.fields is None and panes.sent == []
