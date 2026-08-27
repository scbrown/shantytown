"""roles --check detects drift in the emitted pre-edit policy hook."""
import json
from pathlib import Path

from shantytown import roles
from shantytown.files import FilesRegistry
from shantytown.runtime import (emitted_pre_edit_guard, pre_edit_guard_command,
                                settings_for_role)


def _crew(root: Path, *, codex: bool = False) -> Path:
    crew = root / "crew"
    crew.mkdir()
    extra = {"harness": "codex"} if codex else {}
    (crew / "sattler.json").write_text(json.dumps(
        {"role": "administrator", **extra}))
    return crew


def _emit(root: Path) -> Path:
    path = root / "settings" / "administrator.settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps(settings_for_role("administrator", root=root)))
    return path


def _check(root: Path, crew: Path):
    return roles.check(
        FilesRegistry(crew),
        pre_edit=lambda card: emitted_pre_edit_guard(root, card.role, "claude"),
        pre_edit_expected=lambda card: pre_edit_guard_command())


def test_exact_emitted_guard_is_measured_ok(tmp_path):
    crew = _crew(tmp_path)
    _emit(tmp_path)
    report = _check(tmp_path, crew)
    assert report.verdict == roles.OK
    assert report.rows[0].pre_edit == roles.OK
    assert "pre-edit: ok" in report.render()


def test_legacy_hank_command_is_broken(tmp_path):
    crew = _crew(tmp_path)
    path = _emit(tmp_path)
    path.write_text(path.read_text().replace(
        "yupana hook pre-edit", "hank hook pre-edit"))
    report = _check(tmp_path, crew)
    assert report.verdict == roles.BROKEN
    assert "pre-edit guard drift" in report.rows[0].note
    assert "hank hook pre-edit" in report.rows[0].note


def test_missing_guard_is_broken(tmp_path):
    crew = _crew(tmp_path)
    path = _emit(tmp_path)
    data = json.loads(path.read_text())
    data["hooks"]["PreToolUse"] = [
        group for group in data["hooks"]["PreToolUse"]
        if group["matcher"] != "Edit|Write|MultiEdit"]
    path.write_text(json.dumps(data))
    report = _check(tmp_path, crew)
    assert report.verdict == roles.BROKEN
    assert "<absent>" in report.rows[0].note


def test_unreadable_artifact_is_cannot_tell(tmp_path):
    crew = _crew(tmp_path)
    path = _emit(tmp_path)
    path.write_text("not json")
    assert _check(tmp_path, crew).verdict == roles.CANNOT_TELL


def test_unsupported_harness_remains_unverified(tmp_path):
    crew = _crew(tmp_path, codex=True)
    report = roles.check(
        FilesRegistry(crew), pre_edit=lambda card: "",
        pre_edit_expected=lambda card: "")
    assert report.verdict == roles.OK
    assert report.rows[0].pre_edit == roles.UNVERIFIED
    assert "pre-edit:" not in report.render()


def test_positive_control_omitting_leg_cannot_detect_legacy_command(tmp_path):
    crew = _crew(tmp_path)
    path = _emit(tmp_path)
    path.write_text(path.read_text().replace(
        "yupana hook pre-edit", "hank hook pre-edit"))
    assert roles.check(FilesRegistry(crew)).verdict == roles.OK
