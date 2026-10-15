from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

from shantytown import due_brief, session_anchor
from shantytown.answer import Answer
from shantytown.anchor import anchor
from shantytown.protocols import Agent, WorkItem

NOW = datetime(2026,10,14,12,tzinfo=timezone.utc)
PROOF = SimpleNamespace(quipu_url="https://graph.example",graph="urn:project:test")


def row(identifier="work-a",section="top",due=None,priority=1):
    return {"id":identifier,"title":"repair café 🌐 "*20,"priority":priority,"status":"open",
            "due":due,"assignee":"ellie","graph":PROOF.graph,"section":section}


def control(count=1,undated=0):
    return {"section":"control","all":count,"unique":count,"undated":undated,"missing":0}


def test_one_scoped_projection_positive_fleet_and_exclusions():
    calls=[]
    data=[{**row(due=(NOW-timedelta(days=1)).isoformat()),"due_at":(NOW-timedelta(days=1)).isoformat()},
          {**row("work-soon",priority=0),"due_at":(NOW+timedelta(days=2)).isoformat()},
          {**row("work-future"),"due_at":(NOW+timedelta(days=4)).isoformat()},
          {**row("pointer"),"title":"inbox: skip"},
          {**row("label-pointer"),"labels":["INBOX"]}]
    def read(owner):
        calls.append(owner)
        return Answer.complete_read(data,how="fixture bounded owner projection")
    tracker=SimpleNamespace(decision_rows=read)
    answer=due_brief.projected(tracker,"ellie",now=NOW)
    assert answer.complete and calls==["ellie"]
    assert [item["id"] for item in answer.exact()["coming"]]==["work-a","work-soon"]
    assert answer.exact()["top"][0]["id"]=="work-soon"
    assert answer.exact()["undated"]==0
    text=due_brief.render(answer,now=NOW,fleet=True)
    assert "OVERDUE" in text and len(text.encode())<=400
    assert "0 active items have no due date" in text
    due_brief.projected(tracker,None,now=NOW)
    assert calls==["ellie",None]


def test_shadow_cap_missing_and_query_failure_are_unknown():
    for rows in [[row(),row()], [{**row(),"id":"../bad"}], [{**row(),"priority":None}],
                 [{**row(),"due_at":"not-a-date"}]]:
        tracker=SimpleNamespace(decision_rows=lambda *a,rows=rows:Answer.complete_read(rows,how="fixture"))
        assert not due_brief.projected(tracker,"ellie",now=NOW).complete
    tracker=SimpleNamespace(decision_rows=lambda *a:Answer.capped([],how="fixture"))
    assert "UNKNOWN" in due_brief.render(due_brief.projected(tracker,"ellie",now=NOW))


def test_utf8_hard_cap_keeps_identifiers_and_does_not_mutate_packet():
    packet={"top":[],"coming":[],"undated":25}
    for i in range(3):
        item={"id":"item-"+str(i)+"x"*60,"title":"é🌐"*100,"priority":i,"status":"blocked",
              "due_at":(NOW+timedelta(days=i)).isoformat()}
        packet["top"].append(item);packet["coming"].append(item)
    before=json.dumps(packet)
    text=due_brief.render(Answer.complete_read(packet,how="fixture"),now=NOW,fleet=True)
    assert len(text.encode())<=400 and "display capped" in text
    assert "25 active items have no due date" in text
    assert json.dumps(packet)==before
    for item in packet["top"]:
        assert item["id"] in text or item["id"][:30] not in text


def test_cache_reuses_once_then_expiry_queries_no_stale_unknown(tmp_path,monkeypatch):
    calls=[]
    tracker=SimpleNamespace(_use_remote_projection=lambda:True,prove_store=lambda:PROOF)
    def read(*a,**kw):
        calls.append(1)
        return Answer.complete_read({"top":[],"coming":[],"undated":0},how="fixture")
    monkeypatch.setattr(due_brief,"projected",read)
    assert due_brief.load(tracker,tmp_path,"ellie",now=NOW).complete
    assert due_brief.load(tracker,tmp_path,"ellie",now=NOW+timedelta(seconds=29)).complete
    assert len(calls)==1
    monkeypatch.setattr(due_brief,"projected",lambda *a,**k:Answer.capped({},how="fixture"))
    assert not due_brief.load(tracker,tmp_path,"ellie",now=NOW+timedelta(seconds=31)).complete


def test_pure_anchor_replaces_overlap_preserves_custody_and_brief(monkeypatch):
    class Registry:
        def get(self,name): return Agent(name,role="worker",reports_to=None)
        def all(self): return Answer.complete_read([],how="fixture")
    item=WorkItem("work-held",title="active custody",status="in_progress",assignee="ellie")
    from shantytown.tmux import NullPanes
    result=anchor("ellie",Registry(),NullPanes(),plate=lambda _:item)
    result.decision_view="  Top of your plate:\n    P0 other 10-15\n  Coming due:\n    P0 other 10-15\n"
    text=result.render(now=NOW)
    assert "ON YOUR PLATE" not in text and "Current work: work-held" in text
    assert result.item is item
    monkeypatch.setattr(session_anchor,"_run_anchor",lambda *a:(0,text))
    brief=session_anchor.render("fixture","ellie")
    assert "Coming due" in brief and "then consult Top of your plate" in brief


def test_malformed_cache_never_becomes_complete_or_empty(tmp_path, monkeypatch):
    tracker = SimpleNamespace(_use_remote_projection=lambda: True, prove_store=lambda: PROOF)
    valid = {"top": [], "coming": [], "undated": 0}
    monkeypatch.setattr(due_brief, "projected", lambda *a, **k: Answer.complete_read(valid, how="fixture"))
    assert due_brief.load(tracker, tmp_path, None, now=NOW).complete
    path = next((tmp_path / "cache").glob("decision-*.json"))
    cached = json.loads(path.read_text())
    for malformed in ({}, {**valid, "undated": -1}, {**valid, "top": [None]},
                      {**valid, "coming": [{"id": "../escape"}]}):
        cached["packet"] = malformed
        path.write_text(json.dumps(cached))
        monkeypatch.setattr(due_brief, "projected", lambda *a, **k: Answer.capped({}, how="fixture"))
        answer = due_brief.load(tracker, tmp_path, None, now=NOW)
        assert not answer.complete
        assert "UNKNOWN" in due_brief.render(answer)


def test_worker_plate_reuses_projection_without_changing_custody_candidates(monkeypatch, tmp_path):
    from shantytown.sd import SdTracker
    from shantytown import seeds_plate
    tracker = SdTracker(quipu=PROOF.quipu_url, prefix="test")
    monkeypatch.setattr(tracker, "_use_remote_projection", lambda: True)
    monkeypatch.setattr(tracker, "prove_store", lambda: PROOF)
    calls = []
    rows = [{**row("held"), "status": "in_progress"},
            {**row("blocked", priority=0), "status": "blocked",
             "due_at": (NOW-timedelta(days=1)).isoformat()}]
    def read(proof, owner, **kwargs):
        calls.append((owner, kwargs["decision"]))
        return Answer.complete_read(rows, how="bounded fixture")
    monkeypatch.setattr(seeds_plate, "owner_rows", read)
    assert [item["id"] for item in tracker.plate_rows("ellie").exact()] == ["held"]
    result = due_brief.load(tracker, tmp_path, "ellie", now=NOW)
    assert result.complete and result.exact()["coming"][0]["id"] == "blocked"
    assert calls == [("ellie", True)]
    assert not (tmp_path / "cache").exists()


def test_spec_change_is_capped_advisory_until_matching_owner_ack():
    data = [{**row("changed-work"), "spec_version": 2, "spec_previous_version": 1,
             "spec_changed_at": (NOW-timedelta(hours=2)).isoformat(),
             "spec_ack_version": 1, "spec_ack_actor": "ellie"}]
    tracker = SimpleNamespace(decision_rows=lambda owner: Answer.complete_read(data, how="fixture"))
    answer = due_brief.projected(tracker, "ellie", now=NOW)
    assert answer.complete
    text = due_brief.render(answer, now=NOW, fleet=True)
    assert "SPEC CHANGED v1 -> v2 (unacked) 2h" in text and len(text.encode()) <= 400
    data[0]["spec_ack_version"] = 2
    assert "SPEC CHANGED" not in due_brief.render(due_brief.projected(tracker, "ellie", now=NOW))
    data[0]["spec_ack_actor"] = "former-owner"
    assert "SPEC CHANGED" in due_brief.render(due_brief.projected(tracker, "ellie", now=NOW))
    data[0]["spec_ack_version"] = 3
    assert not due_brief.projected(tracker, "ellie", now=NOW).complete
