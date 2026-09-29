"""Registered hook bundles (aegis-68j0ys): render, preserve, check, and know nothing.

The failure this exists for: a tool's hand-installed hooks were silently BLOWN
AWAY when crew settings moved to st's generator, because `merge_one_level` lets
the emitted `hooks` key win and nothing declared them. So the load-bearing test
is regeneration: emit, emit again over the first file, and the bundle survives.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from shantytown import harness as harness_mod
from shantytown import hook_bundles as hb

EXAMPLE_CMD = "example-tool hook on-prompt || true"


def bundle(**over) -> dict:
    b = {"schema": hb.SCHEMA, "name": "example", "version": "1.2.3", "owner": "example-installer",
         "roles": ["*"],
         "hooks": [{"event": "UserPromptSubmit", "command": EXAMPLE_CMD, "timeout": 10},
                   {"event": "PostToolUse", "matcher": "Write|Edit",
                    "command": "example-tool hook after-edit || true"}]}
    b.update(over)
    return b


def register(root: Path, obj: dict) -> None:
    f = root / f"src-{obj['name']}.json"
    f.write_text(json.dumps(obj))
    hb.register(root, f)


def emit(root: Path, harness: str, role: str) -> Path:
    """Write a role settings file exactly as `roles set` does: harness.settings,
    rendered over whatever is on disk."""
    h = harness_mod.get(harness)
    path = root / "settings" / h.settings_name(role)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    path.write_text(h.render(h.settings(role, root=root), existing, root=root))
    return path


def commands(harness: str, path: Path, event: str) -> list[str]:
    text = path.read_text()
    data = tomllib.loads(text) if harness == "codex" else json.loads(text)
    return [h["command"] for g in data.get("hooks", {}).get(event, []) for h in g["hooks"]]


@pytest.fixture
def root(tmp_path) -> Path:
    r = tmp_path / ".shanty"
    (r / "settings").mkdir(parents=True)
    return r


# --- validation ------------------------------------------------------------------

def test_a_valid_bundle_validates():
    assert hb.validate(bundle()) == []


@pytest.mark.parametrize("bad, fragment", [
    ({"schema": "nope"}, "schema"),
    ({"name": "Bad Name"}, "name"),
    ({"roles": []}, "roles"),
    ({"hooks": []}, "hooks"),
    ({"hooks": [{"event": "UserPromptSubmit", "command": ""}]}, "command"),
    ({"hooks": [{"event": "UserPromptSubmit", "command": "x", "timeout": 0}]}, "timeout"),
    ({"hooks": [{"event": "UserPromptSubmit", "command": "x", "harnesses": ["vim"]}]}, "harnesses"),
])
def test_invalid_bundles_are_refused_with_the_reason(bad, fragment):
    errs = hb.validate(bundle(**bad))
    assert errs and any(fragment in e for e in errs), errs


def test_register_is_idempotent_and_reports_updates(root):
    f = root / "b.json"
    f.write_text(json.dumps(bundle()))
    assert hb.register(root, f) == ("example", "installed")
    assert hb.register(root, f) == ("example", "unchanged")
    f.write_text(json.dumps(bundle(version="2.0.0")))
    assert hb.register(root, f) == ("example", "updated")


def test_register_refuses_an_invalid_file_and_writes_nothing(root):
    f = root / "b.json"
    f.write_text(json.dumps(bundle(schema="nope")))
    with pytest.raises(ValueError, match="schema"):
        hb.register(root, f)
    assert not (root / hb.REGISTRY_DIR / "example.json").exists()


# --- rendering ---------------------------------------------------------------------

@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_bundle_hooks_render_after_sts_own_hooks(root, harness):
    before = commands(harness, emit(root, harness, "worker"), "UserPromptSubmit")
    assert before, "control: st emits its own UserPromptSubmit hook"
    register(root, bundle())
    after = commands(harness, emit(root, harness, "worker"), "UserPromptSubmit")
    assert after[: len(before)] == before, "st's own hooks keep their place"
    assert after[len(before):] == [EXAMPLE_CMD]


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_bundle_SURVIVES_regeneration(root, harness):
    """The "blown away" failure: emit twice, over the first file. Still there."""
    register(root, bundle())
    path = emit(root, harness, "worker")
    emit(root, harness, "worker")
    emit(root, harness, "worker")
    assert commands(harness, path, "UserPromptSubmit").count(EXAMPLE_CMD) == 1


def test_role_filter(root):
    register(root, bundle(roles=["lead"]))
    assert EXAMPLE_CMD in commands("claude", emit(root, "claude", "lead"), "UserPromptSubmit")
    assert EXAMPLE_CMD not in commands("claude", emit(root, "claude", "worker"), "UserPromptSubmit")


def test_harness_filter(root):
    b = bundle()
    b["hooks"][0]["harnesses"] = ["claude"]
    register(root, b)
    assert EXAMPLE_CMD in commands("claude", emit(root, "claude", "worker"), "UserPromptSubmit")
    assert EXAMPLE_CMD not in commands("codex", emit(root, "codex", "worker"), "UserPromptSubmit")


def test_an_event_codex_does_not_run_is_not_written_and_IS_reported(root):
    register(root, bundle(hooks=[{"event": "PostToolUseFailure", "command": "x-fail || true"}]))
    assert "x-fail || true" not in commands("codex", emit(root, "codex", "worker"), "PostToolUseFailure")
    assert "x-fail || true" in commands("claude", emit(root, "claude", "worker"), "PostToolUseFailure")
    res = hb.check(root)
    unsup = [i for i in res.items if i["configured"] == "unsupported"]
    assert [(i["harness"], i["event"]) for i in unsup] == [("codex", "PostToolUseFailure")]
    assert res.exit_code == 1, "unsupported is loud, never a silent drop"


def test_a_broken_dropin_does_not_stop_settings_being_written_and_check_names_it(root):
    register(root, bundle())
    (root / hb.REGISTRY_DIR / "broken.json").write_text("{not json")
    path = emit(root, "claude", "worker")
    assert EXAMPLE_CMD in commands("claude", path, "UserPromptSubmit"), "good bundles still render"
    res = hb.check(root)
    assert [e["file"] for e in res.registry_errors] == ["broken.json"]
    assert res.exit_code == 1


# --- check -------------------------------------------------------------------------

def test_check_ok_after_emit(root):
    register(root, bundle())
    emit(root, "claude", "worker")
    emit(root, "codex", "worker")
    res = hb.check(root)
    assert res.items and all(i["configured"] == "ok" for i in res.items)
    assert all(i["live"] == hb.NOT_CHECKED and i["firing"] == hb.NOT_CHECKED for i in res.items)
    assert res.exit_code == 0


def test_check_reports_a_hook_removed_by_hand_as_missing(root):
    register(root, bundle())
    path = emit(root, "claude", "worker")
    data = json.loads(path.read_text())
    data["hooks"]["UserPromptSubmit"] = [g for g in data["hooks"]["UserPromptSubmit"]
                                          if g["hooks"][0]["command"] != EXAMPLE_CMD]
    path.write_text(json.dumps(data))
    res = hb.check(root)
    missing = [i for i in res.items if i["configured"] == "missing"]
    assert [(i["event"], i["command"]) for i in missing] == [("UserPromptSubmit", EXAMPLE_CMD)]
    assert res.exit_code == 1


def test_check_registered_but_never_emitted_is_missing(root):
    emit(root, "claude", "worker")
    register(root, bundle())                 # registered AFTER the emit
    assert hb.check(root).exit_code == 1


def test_check_unreadable_file_is_cannot_tell(root):
    register(root, bundle())
    emit(root, "claude", "worker").write_text("{garbage")
    res = hb.check(root)
    assert {i["configured"] for i in res.items} == {"unreadable"}
    assert res.exit_code == 2


def test_check_empty_registry_is_ok_not_cannot_tell(root):
    emit(root, "claude", "worker")
    res = hb.check(root)
    assert res.items == [] and res.exit_code == 0


def test_check_json_schema_keys(root):
    register(root, bundle())
    emit(root, "claude", "worker")
    doc = hb.check(root).to_json(root=root, host="h")
    assert doc["schema"] == "st.hook-check/1"
    assert set(doc) >= {"host", "root", "checked_at", "exit", "summary", "registry_errors", "items"}
    assert set(doc["items"][0]) >= {"bundle", "version", "harness", "role", "event", "matcher",
                                    "command", "file", "configured", "live", "firing", "detail"}


# --- st knows nothing about what is in a bundle ------------------------------------

STACK_NAMES = re.compile(r"bobbin|quipu|yupana|desire.?path|camayoc|caboodle|\bdp\b", re.I)


def test_no_tool_names_in_the_bundle_code_path():
    """Stiwi 2026-09-29: st offers GENERIC hook support; the tools configure
    themselves. A tool name in this path would be st taking on WHAT."""
    src = Path(hb.__file__).read_text()
    assert not STACK_NAMES.findall(src), STACK_NAMES.findall(src)
    import inspect
    from shantytown import cli
    handler = inspect.getsource(cli._cmd_hooks)
    assert not STACK_NAMES.findall(handler), STACK_NAMES.findall(handler)


# --- codex notify: a single-slot harness key, one owner ---------------------------

NOTIFY = ["bash", "-c", "printf '%s' \"$1\" | example-tool ingest", "--"]


def test_codex_notify_renders_from_its_one_owner_and_survives_regeneration(root):
    register(root, bundle(name="notifier", hooks=[], codex_notify=NOTIFY))
    path = emit(root, "codex", "worker")
    emit(root, "codex", "worker")
    assert tomllib.loads(path.read_text())["notify"] == NOTIFY
    assert "notify" not in json.loads(emit(root, "claude", "worker").read_text()), "codex only"
    res = hb.check(root)
    assert [(i["event"], i["configured"]) for i in res.items] == [("notify", "ok")]
    assert res.exit_code == 0


def test_two_notify_claimants_render_NEITHER_and_check_says_why(root):
    register(root, bundle(name="one", hooks=[], codex_notify=NOTIFY))
    register(root, bundle(name="two", hooks=[], codex_notify=["other"]))
    path = emit(root, "codex", "worker")
    assert "notify" not in tomllib.loads(path.read_text())
    res = hb.check(root)
    assert {i["configured"] for i in res.items} == {"unsupported"}
    assert all("claimed by 2 bundles" in i["detail"] for i in res.items)
    assert res.exit_code == 1


def test_notify_changed_by_hand_is_missing(root):
    register(root, bundle(name="notifier", hooks=[], codex_notify=NOTIFY))
    path = emit(root, "codex", "worker")
    path.write_text(path.read_text().replace("example-tool ingest", "something else"))
    assert hb.check(root).exit_code == 1


# --- increment 2: per-agent files, cross-layer duplicates, a loud swallowed error ---

def emit_agent(root: Path, harness: str, agent: str, role: str) -> Path:
    h = harness_mod.get(harness)
    path = root / "settings" / h.agent_settings_name(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(h.render(h.settings(role, root=root), "", root=root))
    return path


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_a_per_agent_file_carries_bundles_and_is_checked_against_its_agents_role(root, harness):
    register(root, bundle(roles=["lead"]))
    path = emit_agent(root, harness, "alice", "lead")
    assert EXAMPLE_CMD in commands(harness, path, "UserPromptSubmit"), "same seam, same bundles"
    res = hb.check(root, agent_roles={"alice": "lead"})
    mine = [i for i in res.items if i["file"] == str(path)]
    assert mine and all(i["configured"] == "ok" for i in mine)
    assert res.exit_code == 0


def test_a_per_agent_file_losing_a_bundle_is_missing(root):
    register(root, bundle())
    path = emit_agent(root, "claude", "alice", "worker")
    path.write_text(path.read_text().replace(EXAMPLE_CMD, "something-else || true"))
    res = hb.check(root, agent_roles={"alice": "worker"})
    assert any(i["file"] == str(path) and i["configured"] == "missing" for i in res.items)
    assert res.exit_code == 1


def test_a_per_agent_file_with_no_known_role_is_cannot_tell_not_skipped(root):
    register(root, bundle())
    emit_agent(root, "claude", "ghost", "worker")
    res = hb.check(root, agent_roles={})
    assert [i["configured"] for i in res.items if "agent-ghost" in i["file"]] == ["unreadable"]
    assert res.exit_code == 2


def test_a_bundle_command_also_in_a_non_st_layer_is_a_DUPLICATE(root, tmp_path):
    """Claude Code merges every settings source: the same hook in the user's global
    file AND the st-emitted file fires twice per tool call."""
    register(root, bundle())
    emit(root, "claude", "worker")
    emit(root, "codex", "worker")
    glob = tmp_path / "global-settings.json"
    glob.write_text(json.dumps({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": EXAMPLE_CMD}]}]}}))
    res = hb.check(root, other_layers=[glob, tmp_path / "absent.json"])
    dup = [i for i in res.items if i["configured"] == "duplicate"]
    assert [(i["harness"], i["event"]) for i in dup] == [("claude", "UserPromptSubmit")]
    assert str(glob) in dup[0]["detail"]
    assert res.exit_code == 1
    assert res.summary()["duplicate"] == 1


def test_control_no_other_layer_no_duplicate(root, tmp_path):
    register(root, bundle())
    emit(root, "claude", "worker")
    unrelated = tmp_path / "g.json"
    unrelated.write_text(json.dumps({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "unrelated || true"}]}]}}))
    assert hb.check(root, other_layers=[unrelated]).exit_code == 0


def test_a_registry_that_cannot_load_writes_settings_without_bundles_AND_says_so(root, monkeypatch, capsys):
    register(root, bundle())
    def boom(_root):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(hb, "load", boom)
    out = hb.apply({"hooks": {"Stop": []}}, "worker", "claude", root)
    assert out == {"hooks": {"Stop": []}}
    assert "hook bundles NOT rendered" in capsys.readouterr().err


# --- increment 2: the LIVE layer -------------------------------------------------

import hashlib

from shantytown.launched import FilesLaunches


def live_items(res, event="UserPromptSubmit"):
    return [i for i in res.items if i["event"] == event and i["harness"] == "claude"]


def test_live_ok_when_the_running_process_launched_on_bytes_carrying_the_hook(root):
    register(root, bundle())
    path = emit(root, "claude", "worker")
    res = hb.check(root)
    hb.apply_live(res, [hb.Running("alice", str(path), launch_bytes=path.read_bytes())])
    assert {i["live"] for i in live_items(res)} == {"ok"}
    assert res.exit_code == 0


def test_live_STALE_when_it_launched_before_the_bundle_was_rendered(root):
    """The failure launched.py exists for, per hook: the file is right, the
    process is not, and nothing is wrong until someone relaunches."""
    before = emit(root, "claude", "worker").read_bytes()      # launched on this
    register(root, bundle())
    path = emit(root, "claude", "worker")                     # fixed afterwards
    res = hb.check(root)
    hb.apply_live(res, [hb.Running("alice", str(path), launch_bytes=before)])
    items = live_items(res)
    assert {i["live"] for i in items} == {"stale"}
    assert "alice" in items[0]["detail"]
    assert res.exit_code == 1


def test_live_uses_the_hash_when_no_snapshot_and_the_file_is_unchanged(root):
    register(root, bundle())
    path = emit(root, "claude", "worker")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    res = hb.check(root)
    hb.apply_live(res, [hb.Running("alice", str(path), launch_sha256=sha)])
    assert {i["live"] for i in live_items(res)} == {"ok"}


def test_live_unknown_when_no_snapshot_and_the_file_changed(root):
    register(root, bundle())
    path = emit(root, "claude", "worker")
    res = hb.check(root)
    hb.apply_live(res, [hb.Running("alice", str(path), launch_sha256="0" * 64)])
    assert {i["live"] for i in live_items(res)} == {"unknown"}
    assert res.exit_code == 2


def test_live_not_checked_when_nobody_runs_on_the_file(root):
    register(root, bundle())
    emit(root, "claude", "worker")
    lead = emit(root, "claude", "lead")
    res = hb.check(root)
    hb.apply_live(res, [hb.Running("bob", str(lead), launch_bytes=lead.read_bytes())])
    worker = [i for i in live_items(res) if i["role"] == "worker"]
    assert {i["live"] for i in worker} == {hb.NOT_CHECKED}


def test_launch_records_a_snapshot_that_matches_its_stamp_and_forget_clears_it(tmp_path):
    settings = tmp_path / "worker.settings.json"
    settings.write_text('{"hooks": {}}')
    store = FilesLaunches(tmp_path / "launched")
    store.record("alice", settings)
    assert store.snapshot("alice") == settings.read_bytes()
    settings.write_text('{"hooks": {"Stop": []}}')           # file changes after launch
    assert store.snapshot("alice") == b'{"hooks": {}}', "the launch bytes, not today's"
    store.forget("alice")
    assert store.snapshot("alice") is None


def test_a_snapshot_disagreeing_with_its_stamp_is_not_trusted(tmp_path):
    settings = tmp_path / "s.json"
    settings.write_text("{}")
    store = FilesLaunches(tmp_path / "launched")
    store.record("alice", settings)
    (tmp_path / "launched" / "alice.snapshot").write_bytes(b'{"torn": true}')
    assert store.snapshot("alice") is None
