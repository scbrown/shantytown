"""Read-only lookup of a Steam app's TYPE from Steam's own appinfo cache.

`reaper SteamLaunch AppId=<n>` runs for everything Steam launches, not only games:
Aseprite (AppId 431730, type "Application") held the whole fleet as "gaming" for
19 hours (aegis-syw2fv). Steam records each app's type in `appcache/appinfo.vdf`,
so the detector can ask Steam instead of keeping a hand-maintained exclusion list.

Only the answer "this app is known and is NOT a game" may lift anything. Every
failure — no file, an unknown format, an app absent from the cache, a parse error —
returns None, and the caller keeps holding. A reader bug can therefore only cost a
false hold, never a desynced game.
"""
from __future__ import annotations

import struct
from pathlib import Path

DEFAULT_PATH = "~/.steam/steam/appcache/appinfo.vdf"
#: Steam app types that are a player in front of a game. Lower-case, as compared.
HOLD_TYPES = ("game", "demo")

# magic -> bytes of per-app header after (appid, size) and before the KV body
_HEADER = {0x07564427: 40, 0x07564428: 60, 0x07564429: 60}
_V29 = 0x07564429


def app_types(appids, path: str | Path = DEFAULT_PATH) -> dict[str, str | None]:
    """{appid: lower-case type, or None when Steam's answer cannot be read}."""
    wanted = {str(a) for a in appids}
    found: dict[str, str | None] = {a: None for a in wanted}
    if not wanted:
        return found
    try:
        data = Path(path).expanduser().read_bytes()
        magic, _universe = struct.unpack_from("<II", data, 0)
        skip = _HEADER[magic]
        offset, strings = 8, None
        if magic == _V29:
            (table,) = struct.unpack_from("<q", data, 8)
            offset, strings = 16, _string_table(data, table)
        while offset + 8 <= len(data):
            appid, size = struct.unpack_from("<II", data, offset)
            if appid == 0:
                break
            if str(appid) in wanted:
                try:
                    kv, _ = _parse(data, offset + 8 + skip, strings)
                    common = kv.get("appinfo", kv).get("common", {})
                    kind = common.get("type")
                    found[str(appid)] = kind.lower() if isinstance(kind, str) and kind else None
                except (ValueError, IndexError, struct.error, AttributeError, UnicodeDecodeError):
                    pass  # One unreadable entry stays None; the others are still answered.
            offset += 8 + size
    except (OSError, KeyError, ValueError, IndexError, struct.error):
        pass
    return found


def _string_table(data: bytes, offset: int) -> list[str]:
    (count,) = struct.unpack_from("<I", data, offset)
    position, table = offset + 4, []
    for _ in range(count):
        end = data.index(b"\0", position)
        table.append(data[position:end].decode("utf-8", "replace"))
        position = end + 1
    return table


def _key(data: bytes, position: int, strings):
    if strings is not None:
        (index,) = struct.unpack_from("<I", data, position)
        return strings[index], position + 4
    end = data.index(b"\0", position)
    return data[position:end].decode("utf-8", "replace"), end + 1


def _parse(data: bytes, position: int, strings, depth: int = 0):
    if depth > 64:
        raise ValueError("appinfo nesting too deep")
    node = {}
    while True:
        kind = data[position]
        position += 1
        if kind == 0x08:
            return node, position
        key, position = _key(data, position, strings)
        if kind == 0x00:
            value, position = _parse(data, position, strings, depth + 1)
        elif kind == 0x01:
            end = data.index(b"\0", position)
            value, position = data[position:end].decode("utf-8", "replace"), end + 1
        elif kind in (0x02, 0x04, 0x06):
            value, position = struct.unpack_from("<i", data, position)[0], position + 4
        elif kind == 0x03:
            value, position = struct.unpack_from("<f", data, position)[0], position + 4
        elif kind in (0x07, 0x0A):
            value, position = struct.unpack_from("<Q", data, position)[0], position + 8
        else:
            raise ValueError(f"appinfo value type {kind}")
        node[key] = value
