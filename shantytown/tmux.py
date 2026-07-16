"""tmux — the pane adapter. Bare tmux only.

Do not couple the harness to a multiplexer before the harness exists
(docs/adapters.md). shanty/herdr are adapters LATER.
"""
from __future__ import annotations
import subprocess


class Tmux:
    def exists(self, pane: str) -> bool:
        r = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True,
        )
        return r.returncode == 0 and pane in r.stdout.split()

    def capture(self, pane: str) -> str:
        r = subprocess.run(["tmux", "capture-pane", "-t", pane, "-p"],
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""

    def send(self, pane: str, text: str) -> None:
        # -l sends the text literally; the separate Enter is the submit.
        # This is the entire dispatch mechanism. gt nudge's own help says so:
        # "Send directly via tmux send-keys."
        subprocess.run(["tmux", "send-keys", "-t", pane, "-l", text], check=True)
        subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True)


class NullPanes:
    """Second implementation. Proves dispatch doesn't import tmux."""

    _exists = True

    def __init__(self, screen: str = "") -> None:
        self.sent = []
        self.screen = screen

    def exists(self, pane: str) -> bool:
        return self._exists

    def capture(self, pane: str) -> str:
        return self.screen

    def send(self, pane: str, text: str) -> None:
        self.sent.append((pane, text))
