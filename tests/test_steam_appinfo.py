"""Steam app-type classification for the gaming hold (aegis-syw2fv).

The fixture values are RECORDED from a real appinfo.vdf (v29) on 2026-09-30:
431730 Aseprite is "Application", 2767030 Marvel Rivals is "Game", 228980 Steamworks
Common Redistributables is "Tool". The file layout is re-encoded here so the test
never reads anybody's Steam install.
"""
import struct

import pytest

from shantytown import gaming, quiet_detectors, steam_appinfo

RECORDED = {
    431730: {"type": "Application", "name": "Aseprite"},
    2767030: {"type": "Game", "name": "Marvel Rivals"},
    228980: {"type": "Tool", "name": "Steamworks Common Redistributables"},
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
    """No appinfo at all is exactly the behaviour before this change."""
    (tmp_path / "gaming").mkdir()
    proc = tmp_path / "proc"
    reaper(proc, 10, 431730)
    spec = deployed_spec(tmp_path / "no-steam-here.vdf")
    assert gaming.probe(tmp_path, proc=proc, now=1000, spec=spec).held


def test_hold_types_are_configurable(tmp_path, appinfo):
    (tmp_path / "gaming").mkdir()
    proc = tmp_path / "proc"
    reaper(proc, 10, 431730)
    spec = deployed_spec(appinfo, hold_app_types=["Game", "Application"])
    assert gaming.probe(tmp_path, proc=proc, now=1000, spec=spec).held


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
    {"hold_app_types": []}, {"hold_app_types": "game"}, {"hold_app_types": [""]},
    {"steam_appinfo": ""}, {"steam_appinfo": 3}])
def test_config_rejects_malformed_classification_keys(appinfo, bad):
    with pytest.raises(ValueError):
        deployed_spec(appinfo, **bad)
