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
