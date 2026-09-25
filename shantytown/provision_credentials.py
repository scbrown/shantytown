"""Optional file-only agent credentials; shared defaults remain available."""

import os
from pathlib import Path
import re
import stat


def agent_overrides(root, agent: str) -> dict[str, str]:
    """Read a private, explicit override without exporting it to the harness.

    Absence preserves shared provisioning. A present but invalid file refuses
    before any kit is rewritten: silently falling back would misattribute work.
    Errors deliberately carry neither file contents nor credential values.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", agent):
        raise ValueError("invalid agent name for credential lookup")
    path = Path(root) / "provision" / "agents" / agent / "secrets.env"
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return {}
    except OSError:
        raise ValueError("agent credential file is unreadable") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                or info.st_size > 65536 or info.st_uid != os.getuid()):
            raise ValueError("agent credential file must be private and owned by this user")
        with os.fdopen(fd, encoding="utf-8") as stream:
            fd = None
            content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError("agent credential file is too large")
        result = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if (not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
                    or not value or key in result):
                raise ValueError("agent credential file has invalid or duplicate entries")
            result[key] = value
        return result
    except (OSError, UnicodeError):
        raise ValueError("agent credential file is unreadable") from None
    finally:
        if fd is not None:
            os.close(fd)
