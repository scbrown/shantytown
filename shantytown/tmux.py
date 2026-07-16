"""tmux — the pane adapter. Bare tmux only.

Do not couple the harness to a multiplexer before the harness exists
(docs/adapters.md). shanty/herdr are adapters LATER.

SOCKETS: tmux has more than one server. `tmux -L <name>` is a separate server
with its own sessions, and a bare `tmux` cannot see them — it does not error, it
reports an empty list. So on a host whose agents live on a named socket, bare
tmux reports EVERY LIVE AGENT AS DOWN, confidently and with exit 0.

That is not hypothetical: standing shantytown up on its own host, `shanty crew`
printed `down` for all 8 crew while every one of them was running on socket
`gt-ae5f35`. A false negative about liveness is the worst answer this adapter can
give — `crew` says everyone is dead, and `go` would refuse to dispatch to a pane
that is right there.

So the socket is configurable, and it is the ONLY tmux coupling here:
    SHANTY_TMUX_SOCKET=gt-ae5f35        # or Tmux(socket="gt-ae5f35")
Unset = bare tmux = the default server. Nothing else about the multiplexer leaks
into the harness.
"""
from __future__ import annotations
import os
import subprocess


class Tmux:
    def __init__(self, socket: str | None = None) -> None:
        # Explicit arg wins; else the env; else bare tmux (default server).
        self.socket = socket if socket is not None else os.environ.get("SHANTY_TMUX_SOCKET") or None

    def _cmd(self, *args: str) -> list[str]:
        # -L must precede the subcommand.
        return ["tmux", *(("-L", self.socket) if self.socket else ()), *args]

    def exists(self, pane: str) -> bool:
        # Match sessions as well as pane ids: our panes are addressed by session
        # name (`aegis-crew-ian`), and #{pane_id} only ever yields %N — so a
        # pane_id-only check reports "down" for every session-addressed agent.
        r = subprocess.run(
            self._cmd("list-panes", "-a", "-F", "#{pane_id} #{session_name}"),
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return False
        return any(pane in line.split() for line in r.stdout.splitlines())

    def capture(self, pane: str) -> str:
        r = subprocess.run(self._cmd("capture-pane", "-t", pane, "-p"),
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""

    def send(self, pane: str, text: str) -> None:
        # -l sends the text literally; the separate Enter is the submit.
        # This is the entire dispatch mechanism. gt nudge's own help says so:
        # "Send directly via tmux send-keys."
        subprocess.run(self._cmd("send-keys", "-t", pane, "-l", text), check=True)
        subprocess.run(self._cmd("send-keys", "-t", pane, "Enter"), check=True)


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
