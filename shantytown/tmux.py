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


# Provenance marker for the ownership guard (aegis-ac5g). st new sets it in the
# session environment; st stop refuses to reap any session that does not carry
# it. It is a tmux SESSION variable, so it is bound to that session's lifetime:
# if the session dies and something else (a real gt crew launch) recreates a
# session with the same name, the new session does not carry the marker and st
# correctly refuses to kill it. A file-based marker could not make that
# distinction — it would go stale and name-match a session st never launched.
# The whole footgun is that the registry pane names COLLIDE with the live crew
# (ellie.json pane = "aegis-crew-ellie" == the real gt session on gt-ae5f35), so
# a name match must never be sufficient permission to kill.
_OWNED_ENV = "SHANTY_OWNED"


class OwnershipError(RuntimeError):
    """st refused to reap a session it did not launch (no _OWNED_ENV marker)."""


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

    def capture(self, pane: str, history: int = 0, attrs: bool = False) -> str:
        # -S -N extends the capture back N lines into scrollback. Default 0 keeps
        # the VISIBLE-only behaviour triage depends on (see the Panes protocol).
        # -e keeps the SGR sequences. Off by default because every plain-text
        # consumer (verify's substring match, the `st log` dump) would otherwise
        # have to strip them; on for triage, which needs dim to tell a
        # placeholder from queued input (aegis-x6xh).
        args = ["capture-pane", "-t", pane, "-p"]
        if attrs:
            args.append("-e")
        if history > 0:
            args += ["-S", f"-{int(history)}"]
        r = subprocess.run(self._cmd(*args), capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""

    def send(self, pane: str, text: str) -> None:
        # -l sends the text literally; the separate Enter is the submit.
        # This is the entire dispatch mechanism. gt nudge's own help says so:
        # "Send directly via tmux send-keys."
        subprocess.run(self._cmd("send-keys", "-t", pane, "-l", text), check=True)
        subprocess.run(self._cmd("send-keys", "-t", pane, "Enter"), check=True)

    def new_session(self, name: str) -> str:
        """Create a DETACHED, EMPTY session; return its address (the name).

        RAISES if a session by that name already exists — never silently replace
        a live agent (arnold's #5 ruling: the clobber hazard, same family as
        RESTART-never-handoff). The caller checks exists() and decides.

        It makes an EMPTY shell only. It does NOT launch an agent — that is a
        runtime send(), outside this adapter, so a handoff (which drops
        --settings) cannot leak in through the pane layer.
        """
        if self.exists(name):
            raise RuntimeError(f"session {name!r} already exists — stop it first")
        subprocess.run(self._cmd("new-session", "-d", "-s", name), check=True)
        # Provenance marker (aegis-ac5g): st launched this session, so st may stop
        # it. Set immediately; if it fails, tear the session down rather than
        # leave an un-owned session st created (which its own guard could never
        # reap — a leak). All-or-nothing: a killable session, or nothing.
        try:
            subprocess.run(
                self._cmd("set-environment", "-t", name, _OWNED_ENV, name), check=True)
        except Exception:
            subprocess.run(self._cmd("kill-session", "-t", name),
                           capture_output=True, text=True)
            raise
        return name

    def owns(self, name: str) -> bool:
        """True iff this session carries st's provenance marker — i.e. st launched
        it and it is still the same session. A missing session, or a live session
        st did not create (a real crew session behind a colliding name), is not
        owned. tmux prints `SHANTY_OWNED=<v>` (rc 0) when set and `unknown
        variable` (rc 1) for an unset var or a missing session."""
        r = subprocess.run(
            self._cmd("show-environment", "-t", name, _OWNED_ENV),
            capture_output=True, text=True,
        )
        return r.returncode == 0 and r.stdout.startswith(f"{_OWNED_ENV}=")

    def kill_session(self, name: str) -> None:
        """Destroy the session AND the process tree in its pane. IDEMPOTENT.

        kill-session alone is NOT enough for a real agent: killing the session
        SIGHUPs the pane's shell, but a child that ignores SIGHUP (measured: a
        real claude survived a session kill during aegis-84z1 validation and had
        to be SIGKILLed by hand) can ORPHAN and keep running — burning tokens,
        invisible to `exists()`. So: capture the pane's process group BEFORE the
        kill, kill the session, then TERM the group and escalate to KILL. Best-
        effort on the tree (no such pid == already gone == success); the caller
        (`st stop`) still VERIFIES via exists()."""
        if not self.exists(name):
            return
        pane_pid = self._pane_pid(name)
        subprocess.run(self._cmd("kill-session", "-t", name), check=True)
        if pane_pid:
            self._kill_tree(pane_pid)

    def _pane_pid(self, name: str) -> int | None:
        """The pid of the pane's shell — the head of the process tree we must
        ensure dies. The real agent is its child."""
        r = subprocess.run(
            self._cmd("display-message", "-t", name, "-p", "#{pane_pid}"),
            capture_output=True, text=True,
        )
        s = r.stdout.strip()
        return int(s) if r.returncode == 0 and s.isdigit() else None

    def _kill_tree(self, pane_pid: int) -> None:
        """TERM then (if needed) KILL the pane's process group, so a SIGHUP-
        ignoring child cannot outlive the session. Signals the GROUP (negative
        pid) because the agent is a child of the pane shell; ESRCH (already gone)
        is the success case, swallowed."""
        import os
        import signal
        import time
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pane_pid, sig)      # pane shell leads its own group
            except (ProcessLookupError, PermissionError):
                return                        # gone (or not ours) — done
            time.sleep(0.2)
            try:
                os.killpg(pane_pid, 0)        # still alive? probe with signal 0
            except ProcessLookupError:
                return                        # confirmed dead after this signal


class NullPanes:
    """Second implementation. Proves dispatch doesn't import tmux.

    Two modes for exists(), because two callers want opposite defaults:
      - dispatch/triage: `NullPanes()` — every pane exists (ambient _exists=True),
        so go()/triage() have a pane to work with without seeding one.
      - session lifecycle (#5): `NullPanes(live=set())` — nothing exists until
        new_session() creates it. This is arnold's "in-memory session set". A set
        (even empty) switches exists() to membership; None keeps the ambient
        default. Same object, so the swap leak-detector still sees one Panes.
    """

    _exists = True

    def __init__(self, screen: str = "", drops: bool = False,
                 live: set | None = None, owned: set | None = None) -> None:
        self.sent = []
        self.screen = screen
        # Ownership provenance (aegis-ac5g). new_session marks a session owned;
        # `owned=` seeds sessions as if st had launched them (for the owned-kill
        # path). A session that is `live` but NOT `owned` models the footgun: a
        # real crew session behind a colliding name that st must refuse to reap.
        self._owned: set = set(owned) if owned is not None else set()
        # drops=True models a send that does NOT land — send-keys "succeeds" but
        # the pane never shows the text. This is what #2's verify must catch, and
        # it is the ONLY way to prove verify can fail (a verifier never seen
        # failing is not evidence).
        self._drops = drops
        # None -> ambient mode (everything exists); a set -> session-lifecycle
        # mode (only named sessions exist). new/kill_session require a set.
        self._live = live

    def exists(self, pane: str) -> bool:
        if self._live is not None:
            return pane in self._live
        return self._exists

    def capture(self, pane: str, history: int = 0, attrs: bool = False) -> str:
        # The double has no scrollback/visible split — one screen answers both.
        # attrs is accepted and ignored: whatever the caller seeded IS the
        # screen, escapes and all. Seed a screen with \x1b[2m in it to model a
        # placeholder, with none to model a stripped capture (which triage must
        # answer UNKNOWN for, not idle).
        return self.screen

    def send(self, pane: str, text: str) -> None:
        self.sent.append((pane, text))
        # A real pane shows what was just typed into it, so capture() must
        # reflect the send — otherwise this double models a pane that silently
        # eats every message, which is not a pane. Unless drops=True.
        if not self._drops:
            self.screen += ("\n" if self.screen else "") + text

    def new_session(self, name: str) -> str:
        """RAISES if the name is live; else creates an empty session. Requires
        session-lifecycle mode (live set) — new_session on the ambient default
        would always raise, since everything ambiently exists."""
        if self._live is None:
            self._live = set()      # first session call opts into lifecycle mode
        if name in self._live:
            raise RuntimeError(f"session {name!r} already exists — stop it first")
        self._live.add(name)
        self._owned.add(name)       # st launched it -> st owns it (aegis-ac5g)
        return name

    def owns(self, name: str) -> bool:
        return name in self._owned

    def kill_session(self, name: str) -> None:
        """Idempotent: discard removes if present, no-op if absent."""
        if self._live is None:
            self._live = set()
        self._live.discard(name)
        self._owned.discard(name)
