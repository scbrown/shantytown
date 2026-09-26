"""Non-mutating write-authorization probe for doctor (not storage durability)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .quipu import request_headers

TOKEN_HELP = (
    "Ask the Quipu administrator for an accepted credential; install it in "
    "~/.config/quipu/token (mode 0400), or set QUIPU_AUTH_TOKEN_FILE. "
    "QUIPU_AUTH_TOKEN overrides the file. See docs/cli.md#quipu-credentials."
)


@dataclass(frozen=True)
class WriteHealth:
    state: str
    detail: str
    code: int

    def render(self) -> str:
        return f"\n  QUIPU WRITE AUTH\n  {self.state}: {self.detail}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer to a different endpoint, even on the same host.
        return None


def classify(status: int, body: str, token_set: bool) -> WriteHealth:
    if status == 401:
        state = "bad-token" if token_set else "no-token"
        return WriteHealth(state, TOKEN_HELP, 1)
    try:
        data = json.loads(body)
    except ValueError:
        data = {}
    reason = data.get("reason") if isinstance(data, dict) else None
    if status == 403 and reason == "server_is_read_only":
        return WriteHealth("read-only", "Server refuses writes regardless of credential; use the intended writable server.", 1)
    # Require the server's specific parse error, not any 400/422 or HTML error.
    error = data.get("error", "") if isinstance(data, dict) else ""
    if status == 400 and isinstance(error, str) and error.startswith("invalid episode JSON:") and "missing field" in error:
        return WriteHealth("ok", "Write authorization accepted; empty episode rejected, nothing written. Storage commits are not tested.", 0)
    return WriteHealth("unknown", f"Unexpected HTTP {status}; write authorization unproven.", 2)


def check(server: str | None, *, timeout: float = 5) -> WriteHealth:
    if not server:
        return WriteHealth("unconfigured", "Set QUIPU_SERVER to check write authorization.", 2)
    try:
        parsed = urllib.parse.urlsplit(server)
    except ValueError:
        return WriteHealth("unconfigured", "QUIPU_SERVER is not a valid HTTP(S) base URL.", 2)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return WriteHealth("unconfigured", "QUIPU_SERVER must be an HTTP(S) base URL without credentials, query or fragment.", 2)
    try:
        headers = request_headers()
    except (OSError, UnicodeError, ValueError):
        return WriteHealth("no-token", "Cannot read the configured token file. " + TOKEN_HELP, 1)
    headers["X-Quipu-Client"] = "agent-adhoc"
    token_set = "Authorization" in headers
    request = urllib.request.Request(server.rstrip("/") + "/episode", data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return classify(response.status, response.read(8192).decode("utf-8", "replace"), token_set)
    except urllib.error.HTTPError as error:
        try:
            body = error.read(8192).decode("utf-8", "replace")
        except OSError:
            body = ""
        return classify(error.code, body, token_set)
    except (OSError, ValueError, urllib.error.URLError):
        # No exception/body echo: remote errors may contain credentials.
        return WriteHealth("unreachable", "No usable HTTP response; check QUIPU_SERVER and connectivity.", 2)
