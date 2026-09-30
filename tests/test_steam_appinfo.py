"""Steam app-type classification for the gaming hold (aegis-syw2fv).

The fixture values are RECORDED from a real appinfo.vdf (v29) on 2026-09-30:
431730 Aseprite is "Application", 2767030 Marvel Rivals is "Game", 228980 Steamworks
Common Redistributables is "Tool", and — the reason the rule is a LIFT-list —
2708610 TRIBES 3: Rivals Playtest is "Beta" and 2676230 FiveM is "Application"
(sattler-rev-127). The file layout is re-encoded here so the test never reads
anybody's Steam install.
"""
import struct

import pytest

from shantytown import gaming, quiet_detectors, steam_appinfo

RECORDED = {
    431730: {"type": "Application", "name": "Aseprite"},
    2767030: {"type": "Game", "name": "Marvel Rivals"},
    228980: {"type": "Tool", "name": "Steamworks Common Redistributables"},
    2708610: {"type": "Beta", "name": "TRIBES 3: Rivals Playtest"},
    2676230: {"type": "Application", "name": "FiveM"},
}


def encode(apps, version=29):
    """Build an appinfo.vdf: {appid: common-dict} -> bytes, v28 or v29 layout."""
    magic = {28: 0x07564428, 29: 0x07564429}[version]
    strings: list[str] = []

    def key(name):
        if version == 28:
            return name.encode() + b"\0"
        if name not in strings:
            strings.append(name)
        return struct.pack("<I", strings.index(name))

    def node(mapping):
        out = b""
        for name, value in mapping.items():
            if isinstance(value, dict):
                out += b"\x00" + key(name) + node(value)
            elif isinstance(value, int):
                out += b"\x02" + key(name) + struct.pack("<i", value)
            else:
                out += b"\x01" + key(name) + str(value).encode() + b"\0"
        return out + b"\x08"

    body = b""
    for appid, common in apps.items():
        kv = node({"appinfo": {"appid": appid, "common": common}})
        record = b"\0" * 60 + kv  # info_state .. binary sha1: never read
        body += struct.pack("<II", appid, len(record)) + record
    body += struct.pack("<I", 0)
    if version == 28:
        return struct.pack("<II", magic, 1) + body
    table_at = 16 + len(body)
    table = struct.pack("<I", len(strings)) + b"".join(s.encode() + b"\0" for s in strings)
    return struct.pack("<IIq", magic, 1, table_at) + body + table


@pytest.fixture
def appinfo(tmp_path):
    path = tmp_path / "appinfo.vdf"
    path.write_bytes(encode(RECORDED))
    return path


@pytest.mark.parametrize("version", [28, 29])
def test_reads_recorded_types_in_both_formats(tmp_path, version):
    path = tmp_path / "appinfo.vdf"
    path.write_bytes(encode(RECORDED, version))
    assert steam_appinfo.app_types(["431730", "2767030", "228980"], path) == {
        "431730": "application", "2767030": "game", "228980": "tool"}


def test_anything_unreadable_is_unknown_never_a_lift(tmp_path, appinfo):
    assert steam_appinfo.app_types(["2767030"], tmp_path / "absent") == {"2767030": None}
    assert steam_appinfo.app_types(["5"], appinfo) == {"5": None}  # not in the cache
    garbage = tmp_path / "garbage.vdf"
    garbage.write_bytes(b"\x99" * 64)
    assert steam_appinfo.app_types(["431730"], garbage) == {"431730": None}
    truncated = tmp_path / "truncated.vdf"
    truncated.write_bytes(appinfo.read_bytes()[:90])
    assert steam_appinfo.app_types(["431730"], truncated) == {"431730": None}


def reaper(proc, pid, appid):
    path = proc / str(pid)
    path.mkdir(parents=True)
    (path / "cmdline").write_bytes(b"\0".join([b"reaper", b"SteamLaunch", f"AppId={appid}".encode()]) + b"\0")


def deployed_spec(appinfo, **extra):
    """The Steam detector as the deployed config declares it, minus the hand exclusion."""
    row = {"name": "gaming", "kind": "steam", "enabled": True, "game_executable": "reaper",
           "game_arguments": ["SteamLaunch", "AppId=(?P<appid>[0-9]+)"],
           "steam_appinfo": str(appinfo), **extra}
    return quiet_detectors.parse({"detector": [row]}).detectors[0]


def test_steam_application_does_not_hold_but_a_game_does(tmp_path, appinfo):
    """The syw2fv success test: Aseprite running -> no hold; Marvel Rivals -> hold."""
    (tmp_path / "gaming").mkdir()
    proc = tmp_path / "proc"
    reaper(proc, 10, 431730)
    status = gaming.probe(tmp_path, proc=proc, now=1000, spec=deployed_spec(appinfo))
    assert status.state == "clear" and not status.held and not status.throttled
    assert gaming.probe(tmp_path, proc=proc, now=1060,
                        spec=deployed_spec(appinfo)).state == "clear"
    reaper(proc, 11, 2767030)
    status = gaming.probe(tmp_path, proc=proc, now=1120, spec=deployed_spec(appinfo))
    assert status.held and status.appids == ("2767030",)


def test_the_ignored_app_is_recorded_so_a_clear_is_explainable(tmp_path, appinfo):
    import json
    (tmp_path / "gaming").mkdir()
    proc = tmp_path / "proc"
    reaper(proc, 10, 431730)
    gaming.probe(tmp_path, proc=proc, now=1000, spec=deployed_spec(appinfo))
    state = json.loads((tmp_path / "gaming/state.json").read_text())
    assert state["not_games"] == {"431730": "application"} and state["appids"] == []


def test_unknown_type_keeps_holding(tmp_path):
    """No appinfo at all is the behaviour before this change, for any unnamed AppId."""
    (tmp_path / "gaming").mkdir()
    proc = tmp_path / "proc"
    reaper(proc, 10, 2767030)
    spec = deployed_spec(tmp_path / "no-steam-here.vdf")
    assert gaming.probe(tmp_path, proc=proc, now=1000, spec=spec).held


@pytest.mark.parametrize("appid,held", [
    (2767030, True),    # Game
    (2708610, True),    # Beta: a playtest is a game (sattler-rev-127)
    (2676230, True),    # Application NOT named in lift_appids: FiveM is a game client
    (5, True),          # absent from the cache: unknown holds
    (431730, False),    # Application named in lift_appids: Aseprite
    (228980, False),    # Tool
])
def test_only_a_positive_answer_lifts(tmp_path, appinfo, appid, held):
    (tmp_path / "gaming").mkdir()
    proc = tmp_path / "proc"
    reaper(proc, 10, appid)
    assert gaming.probe(tmp_path, proc=proc, now=1000, spec=deployed_spec(appinfo)).held is held


def test_the_same_rule_applies_with_no_config_at_all(tmp_path, appinfo, monkeypatch):
    """spec=None (legacy path) must use the same defaults as the parsed config."""
    monkeypatch.setattr("shantytown.steam_appinfo.DEFAULT_PATH", str(appinfo))
    for appid, held in ((2708610, True), (431730, False)):
        root = tmp_path / str(appid)
        (root / "gaming").mkdir(parents=True)
        (root / "gaming/enabled").touch()
        reaper(root / "proc", 10, appid)
        assert gaming.probe(root, proc=root / "proc", now=1000).held is held


def test_lift_lists_are_configurable_and_replace_the_defaults(tmp_path, appinfo):
    for name, lift, appids, held in (("empty", [], [431730], True),
                                     ("extended", ["431730", "2676230"], [431730, 2676230], False)):
        root = tmp_path / name
        (root / "gaming").mkdir(parents=True)
        for pid, appid in enumerate(appids, 10):
            reaper(root / "proc", pid, appid)
        spec = deployed_spec(appinfo, lift_appids=lift)
        assert gaming.probe(root, proc=root / "proc", now=1000, spec=spec).held is held


def test_a_named_appid_lifts_even_when_its_type_is_unreadable(tmp_path):
    (tmp_path / "gaming").mkdir()
    proc = tmp_path / "proc"
    reaper(proc, 10, 431730)
    spec = deployed_spec(tmp_path / "no-steam-here.vdf")
    assert not gaming.probe(tmp_path, proc=proc, now=1000, spec=spec).held


def test_a_running_tool_does_not_mask_a_game_session_ending(tmp_path, appinfo):
    """Game ends while a tool keeps running: the hold must still lift."""
    (tmp_path / "gaming").mkdir()
    proc = tmp_path / "proc"
    reaper(proc, 10, 431730)
    reaper(proc, 11, 2767030)
    spec = deployed_spec(appinfo)
    assert gaming.probe(tmp_path, proc=proc, now=1000, spec=spec).held
    (proc / "11/cmdline").unlink()
    # One probe a minute, as the timer runs; past the 300 s launch grace.
    states = [gaming.probe(tmp_path, proc=proc, now=1000 + 60 * n, spec=spec).state
              for n in range(1, 10)]
    assert states[:4] == ["gaming"] * 4           # launch grace
    assert states[-1] == "clear"                  # the tool alone does not keep it


@pytest.mark.parametrize("bad", [
    {"lift_app_types": "tool"}, {"lift_app_types": [""]}, {"lift_appids": ["abc"]},
    {"lift_appids": [431730]}, {"lift_appids": "431730"},
    {"steam_appinfo": ""}, {"steam_appinfo": 3}])
def test_config_rejects_malformed_classification_keys(appinfo, bad):
    with pytest.raises(ValueError):
        deployed_spec(appinfo, **bad)
