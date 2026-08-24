"""tmux — the pane adapter. Bare tmux only.

Do not couple the harness to a multiplexer before the harness exists
(docs/adapters.md). shanty/herdr are adapters LATER.

SOCKETS: tmux has more than one server. `tmux -L <name>` is a separate server
with its own sessions, and a bare `tmux` cannot see them — it does not error, it
reports an empty list. So on a host whose agents live on a named socket, bare
tmux reports EVERY LIVE AGENT AS DOWN, confidently and with exit 0.

That is not hypothetical: standing shantytown up on its own host, `st crew`
printed `down` for all 8 crew while every one of them was running on a named
socket. A false negative about liveness is the worst answer this adapter can
give — `crew` says everyone is dead, and `go` would refuse to dispatch to a pane
that is right there.

So the socket is configurable, and it is the ONLY tmux coupling here:
    SHANTY_TMUX_SOCKET=my-socket        # or Tmux(socket="my-socket")
Unset = bare tmux = the default server. Nothing else about the multiplexer leaks
into the harness.
"""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
import sys
import time


# The ONLY keys Tmux.control will send (aegis-c6hli). Cursor moves, deletions
# and Escape — nothing here can commit a buffer or accept a suggestion.
#
# Enter/C-m and Tab/C-i are ABSENT BY CONSTRUCTION and must stay absent. Enter
# is the submit; Tab ACCEPTS the ghost-text suggestion, which would put text the
# agent never wrote into its turn — the aegis-apz9 injection, self-inflicted by
# the tool built to prevent it. Adding either to this set is not a tweak, it is
# the reintroduction of that hole; test_input_box.py asserts they are missing.
CONTROL_KEYS = frozenset({
    "C-u",     # kill to line start — the clear
    "C-k",     # kill to line end
    "C-a",     # cursor to start; also a pure no-op nudge to force a repaint
    "C-e",     # cursor to end
    "BSpace",  # single delete
    "Escape",  # dismiss the suggestion
})

# The ONLY keys Panes.option will send (aegis-w30p2): the digits that address an
# option on a blocking picker. A SEPARATE allowlist from CONTROL_KEYS on purpose
# — see Tmux.option. Enter and Tab are absent here for the same reason and with
# the same force, and they are not needed: a bare digit selects AND confirms.
OPTION_KEYS = frozenset("123456789")

# How much literal text goes into ONE `send-keys -l`, and the gap between
# chunks. A single large write is read by a TUI as a PASTE — measured on codex
# 0.146.1: 1000 chars in one write types normally, 1004 becomes a
# `[Pasted Content N chars]` placeholder that the trailing Enter does NOT commit,
# and 2000 chars sent as two writes of 1000 types normally. So the trigger is the
# SIZE OF ONE WRITE and the total message length is irrelevant.
#
# 512 is deliberately half the measured boundary. That boundary belongs to
# somebody else's terminal handling and can move; the price of being conservative
# is a few more subprocess calls on a long message, and the price of being wrong
# is a message that is never delivered while the sender is told it was.
_SEND_CHUNK = 512
_SEND_CHUNK_GAP_S = 0.05


def _journal_send(pane: str, text: str) -> None:
    """Append one line per pane send to the send journal: who put what into
    whose pane, when. The forensic record the fleet did not have when three
    fabricated recovery instructions arrived from "an unattached process" —
    the routine send channel was the one path that left nothing behind.

    SENDER is best-effort attribution, honestly labelled: SHANTY_AGENT when the
    sender is a crew session, else '-'; the pid is always recorded so even an
    unnamed sender is correlatable against the process table. Text is capped —
    this is a journal, not a transcript — and newlines are flattened so one
    send is always one greppable line.

    NEVER raises, never blocks a delivery: an audit failure prints a warning
    and the message still goes. The inverse (refusing delivery on a full disk)
    would turn the audit trail into a fleet-messaging outage.

    THE SKIP MUST BE LOUD, NOT SILENT (aegis-tdesp). When SHANTY_ROOT is unset
    there is no store to journal into — but returning quietly is the exact bug
    that a `*/3` cron feeder hit: it exported only PATH, so every dispatch it
    wrote into a pane delivered but never journaled, and an unjournaled send is
    indistinguishable from a raw tmux injection. That matters because the apz9
    forensic test is "text NOT in sends.log => it traversed no st channel =>
    injector": a silently-unjournaled st send both MANUFACTURES that signature
    and, worse, could MASK a real injection under the same noise. So a rootless
    send is announced with the same identifying fields a journal line carries —
    it cannot land in sends.log without a root, but it is no longer silent, and
    the breadcrumb is greppable off stderr. The durable fix is caller-side: any
    env/cron/systemd-driven st send must export SHANTY_ROOT (default
    `$SHANTY/.shanty`). We deliberately do NOT fall back to `cwd/.shanty` here —
    that guess resolves against whatever directory cron happens to run in and
    would fragment the journal to random locations (the nipg failure mode).
    """
    sender = os.environ.get("SHANTY_AGENT") or "-"
    body = text.replace("\n", "\\n")[:500]
    root = os.environ.get("SHANTY_ROOT")
    if not root:
        # No store elected — cannot journal, but MUST NOT be silent (see above).
        print(f"  ⚠ send UNJOURNALED (SHANTY_ROOT unset — export it for "
              f"cron/env-driven st sends): sender={sender} pid={os.getpid()} "
              f"pane={pane} text={body}", file=sys.stderr)
        return
    try:
        logdir = os.path.join(root, "logs")
        os.makedirs(logdir, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with open(os.path.join(logdir, "sends.log"), "a", encoding="utf-8") as f:
            f.write(f"{stamp} sender={sender} pid={os.getpid()} "
                    f"pane={pane} text={body}\n")
    except OSError as e:
        print(f"  ⚠ send journal write failed ({e}) — message still delivered",
              file=sys.stderr)


# The journal as a PUBLIC seam (aegis-w30p2). picker.answer records who answered
# what before it acts, and it needs the fleet's one forensic log rather than a
# second one: "text NOT in sends.log => it traversed no st channel => injector"
# is the apz9 test, and an audit trail that answers live somewhere else would
# both weaken that test and hide from the person grepping for it.
journal = _journal_send


# Provenance marker for the ownership guard. st new sets it in the
# session environment; st stop refuses to reap any session that does not carry
# it. It is a tmux SESSION variable, so it is bound to that session's lifetime:
# if the session dies and something else (a real gt crew launch) recreates a
# session with the same name, the new session does not carry the marker and st
# correctly refuses to kill it. A file-based marker could not make that
# distinction — it would go stale and name-match a session st never launched.
# The whole footgun is that the registry pane names can COLLIDE with sessions
# somebody else already started under the same name, so a name match must never
# be sufficient permission to kill.
_OWNED_ENV = "SHANTY_OWNED"

# The socket the FLEET lives on, declared by the store rather than inferred from
# whatever pane happens to be running st. RULED 2026-07-20 (the shanty cutover):
# agents STAY on the fleet's existing socket and shanty is a VIEWER; they do not
# migrate onto shanty's own socket. Three reasons, in order of weight:
#   1. The crew is MIXED — some agents were launched by the other launcher onto
#      the fleet socket, and st cannot move those. Migrating only the st agents
#      SPLITS the fleet across two sockets, and a split fleet makes `st crew`
#      structurally incapable of reporting everyone. That is the same false
#      negative as the bug we are fixing, made permanent.
#   2. Moving a session between sockets is not a move: it is a kill and a
#      relaunch, and it costs every agent its in-flight context.
#   3. This repo deliberately owns the pane layer least (design.md: "the part
#      most likely to be replaced"). Binding fleet identity to one multiplexer's
#      socket is coupling in the direction we avoid.
# So the socket is DECLARED, in the store, and read from there — never inferred
# from the ambient $TMUX, which is exactly what made every agent report DOWN from
# inside a shanty pane.
SOCKET_FILE = "tmux-socket"


def declared_socket(root) -> str | None:
    """The fleet's socket, per the store. None = the default server.

    Precedence: `[tmux] socket` in shantytown.toml, then the legacy
    `settings/tmux-socket` file (DEPRECATED, aegis-8calr), then
    $SHANTY_TMUX_SOCKET, then None. A DECLARATION wins over the env on purpose —
    an ambient env var is what an operator's shell happens to hold, and this must
    not change meaning depending on which pane you ran it from. That ambiguity IS
    the bug.
    """
    from pathlib import Path as _P
    from .config import load_or_default
    cfg, _err = load_or_default(root)          # never raises: this runs in hooks
    if cfg.tmux_socket:
        return cfg.tmux_socket
    try:
        v = (_P(root) / "settings" / SOCKET_FILE).read_text().strip()
        if v:
            _warn_socket_file_once(_P(root))
            return v
    except OSError:
        pass
    return os.environ.get("SHANTY_TMUX_SOCKET") or None


_SOCKET_WARNED: set[str] = set()


def _warn_socket_file_once(root) -> None:
    """Deprecation notice for settings/tmux-socket — stderr, once per process.

    Same two constraints as env.json's (deployment.py): stdout belongs to the hook
    protocol, and a line an operator sees on every command is a line they stop
    reading.
    """
    marker = str(root)
    if marker in _SOCKET_WARNED:
        return
    _SOCKET_WARNED.add(marker)
    print(f"  ⚠ {root / 'settings' / SOCKET_FILE} is DEPRECATED — it is still "
          f"read. Declare it as `socket` under [tmux] in "
          f"{root / 'shantytown.toml'} instead.", file=sys.stderr)


def _carries_settings_pointer(line: str) -> bool:
    """Does this reconstructed launch line point at ANY harness's settings?

    Asked of the harnesses rather than pattern-matched here, because "carries
    settings" is a claim about a program's own syntax — a flag for one, an export
    for another. The previous version tested for the literal string `--settings`,
    which is Claude Code's spelling, so every correctly-wired codex agent failed
    it (aegis-506x9). A reader that hardcodes one program's mechanism does not
    check the fleet, it checks the half of the fleet that runs that program.
    """
    from . import harness as harness_mod
    return any(h.settings_in_cmdline(line) is not None
               for h in harness_mod.all_harnesses())


class OwnershipError(RuntimeError):
    """st refused to reap a session it did not launch (no _OWNED_ENV marker)."""


class PaneNotAgent(RuntimeError):
    """st refused to type a message into a pane that is a SHELL, not an agent.

    THE HAZARD (aegis-ikj4t). When a runtime exits, its tmux pane falls back to
    the login shell. st keeps routing to the pane, so every inbound message is
    EXECUTED BY BASH. Observed live: another agent's escalation text and an ack
    recipe ran as shell commands. Nothing destructive ran by luck of the wording,
    not by design.

    This is the aegis-0214 hazard (message bodies execute) through a different
    door. 0214 is an AUTHOR quoting badly; here the sender is correct, the body
    is correct, and the RECIPIENT silently stopped being an agent — so no amount
    of care at the writing end prevents it.

    A REFUSAL, not a warning, and raised rather than returned: a caller that
    ignores a return value delivers into bash and reports success, which is the
    behaviour being fixed. Callers that message agents catch this and say the
    message was not delivered.
    """


# Foreground commands that mean "this pane is a shell, not a runtime".
#
# A POSITIVE LIST, never "anything that is not a known runtime": st does not own
# every program a pane may legitimately run, and refusing everything unrecognised
# would break messaging for a harness nobody has told this file about. Unknown
# stays DELIVERABLE — the check can only ever refuse what it positively
# identifies as a shell.
#
# MEASURED, and this is the false positive that would have made the guard
# unshippable: an agent RUNNING A SHELL COMMAND does not look like this. While
# executing a bash tool call, `shanty-franklin` reported `claude` — the runtime
# stays the pane's foreground process because the tool's subprocess never takes
# the terminal. Live crew panes read `claude` and `node`; a pane whose runtime
# had exited read `bash`.
SHELL_COMMANDS = frozenset({"bash", "sh", "zsh", "fish", "dash", "ksh", "csh",
                            "tcsh", "-bash", "-sh", "-zsh"})


class Tmux:
    def __init__(self, socket: str | None = None) -> None:
        # Explicit arg wins; else the env; else bare tmux (default server).
        self.socket = socket if socket is not None else os.environ.get("SHANTY_TMUX_SOCKET") or None

    def _cmd(self, *args: str) -> list[str]:
        # -L must precede the subcommand.
        return ["tmux", *(("-L", self.socket) if self.socket else ()), *args]

    def exists(self, pane: str) -> bool:
        # Match sessions as well as pane ids: our panes are addressed by session
        # name (`crew-ian`), and #{pane_id} only ever yields %N — so a
        # pane_id-only check reports "down" for every session-addressed agent.
        r = subprocess.run(
            self._cmd("list-panes", "-a", "-F", "#{pane_id} #{session_name}"),
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return False
        return any(pane in line.split() for line in r.stdout.splitlines())

    def cmdline(self, pane: str) -> str | None:
        """The launch command line of the AGENT PROCESS running in `pane`.

        Used to ask what a live agent is ACTUALLY wired with, rather than what
        its role's artifact says it should be (runtime.live_stop_directions).
        None if the pane is gone or nothing can be read.

        TWO TREE SHAPES, both live on this host, and a reader that handles only
        one silently mis-answers for the other:
            gt-launched   pane_pid IS the runtime  (`claude --settings ...`)
            st-launched   pane_pid is `-bash`, the runtime is a CHILD
        So look at the pane process AND its descendants.

        Among candidates carrying a SETTINGS POINTER, take the EARLIEST-STARTED.
        A transient `bash -c` from a tool call can mention --settings in an eval
        string; the real runtime has been there since the session began, so
        oldest-wins keeps a passing shell command from impersonating the
        agent's wiring.

        ⚠️ ARGV IS NOT THE LAUNCH LINE, and reading it as though it were is what
        made this check harness-blind (aegis-506x9). Claude Code's pointer is a
        FLAG, so it lands in argv. codex has no such flag — its pointer is an
        EXPORT, `CODEX_HOME=<dir> codex …`, which the shell consumes into the
        child's environment. Measured on the live store, 2026-08-06:

            argv        node …/codex --dangerously-bypass-hook-trust --model …
            environ     CODEX_HOME=…/settings/codex/lead
            that file   carries BOTH the send and the drain Stop hooks

        So `ps` alone showed no pointer and a fully-wired codex lead was reported
        as the hookless zombie — the alarm whose text is "the stops of its 7
        reports land in a store nothing reads". The launch line is therefore
        RECONSTRUCTED here: argv, with the harnesses' settings_env_var
        assignments read back out of /proc and prepended, which is exactly the
        string the launcher typed. Callers keep taking a plain string and the
        interpretation stays in the harnesses, where the syntax lives.

        The env is read from the CANDIDATE PROCESS, not from this one. st runs
        with its own environment and inheriting it here would let the checker's
        own CODEX_HOME answer a question about somebody else's agent.
        """
        r = subprocess.run(
            self._cmd("list-panes", "-a", "-F", "#{pane_pid} #{session_name}"),
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return None
        pids = [ln.split()[0] for ln in r.stdout.splitlines()
                if len(ln.split()) > 1 and ln.split()[1] == pane]
        if not pids:
            return None
        root = pids[0]
        # etimes (seconds elapsed) is monotone in age and trivially sortable —
        # unlike lstart, which is a fixed-width 24-char date field. pid is here
        # so the environment can be read back for the process it belongs to.
        ps = subprocess.run(
            ["ps", "-o", "etimes=,pid=,args=", "-p", root, "--ppid", root],
            capture_output=True, text=True,
        )
        if ps.returncode != 0:
            return None
        best, best_age, fallback = None, -1, None
        for ln in ps.stdout.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split(None, 2)
            if len(parts) < 3:
                continue
            age_s, pid, args = parts
            try:
                age = int(age_s)
            except ValueError:
                continue
            args = args.strip()
            if args == "-bash":
                continue
            line = self._launch_line(pid, args)
            if fallback is None:
                fallback = line
            if _carries_settings_pointer(line):
                if age > best_age:
                    best, best_age = line, age
        # No pointer anywhere is a REAL answer (the hookless zombie), so return
        # the process we found rather than None, which means "could not look".
        # That distinction is the whole contract of the callers.
        return best or fallback

    @staticmethod
    def _launch_line(pid: str, args: str) -> str:
        """argv with the settings-pointer exports folded back in.

        Only the harnesses' declared vars are recovered — not the whole
        environment. A process environment holds credentials (codex's home sits
        beside its auth.json), and this string is printed in operator-facing
        findings, so pulling everything would leak secrets into `st crew` output
        to fix a display bug.
        """
        from . import harness as harness_mod
        wanted = harness_mod.settings_env_vars()
        if not wanted:
            return args
        try:
            with open(f"/proc/{pid}/environ", "rb") as fh:
                raw = fh.read()
        except OSError:
            # Unreadable environ (process gone, or not ours) is not a finding —
            # fall back to argv and let the caller's own None/empty contract say
            # what it can and cannot tell.
            return args
        env = {}
        for item in raw.split(b"\0"):
            if not item:
                continue
            key, sep, value = item.decode("utf-8", "replace").partition("=")
            if sep and key in wanted:
                env[key] = value
        prefix = " ".join(f"{k}={env[k]}" for k in wanted if k in env)
        return f"{prefix} {args}" if prefix else args

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

    def foreground(self, pane: str) -> str | None:
        """The pane's foreground command, or None if it cannot be read.

        None is CANNOT TELL and never "it is a shell" — the guard below may only
        refuse on a POSITIVE identification, so a tmux we could not ask leaves
        messaging exactly as it was.
        """
        r = subprocess.run(
            self._cmd("list-panes", "-a", "-F",
                      "#{session_name} #{pane_current_command}"),
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        for ln in r.stdout.splitlines():
            parts = ln.split(None, 1)
            if len(parts) == 2 and parts[0] == pane:
                return parts[1].strip()
        return None

    def send(self, pane: str, text: str, *, allow_shell: bool = False) -> None:
        # REFUSE A SHELL (aegis-ikj4t). A dead agent's pane is a login shell, and
        # st keeps routing to it — so every inbound message is executed by bash.
        # The check is here, at the one place st types into a pane, because the
        # hazard belongs to typing and not to any one caller.
        #
        # `allow_shell` is for the LAUNCHER, which types `cd … && claude …` into
        # a fresh bash pane on purpose. That is the whole reason this is a
        # parameter rather than an unconditional guard: a blanket refusal here
        # would make st unable to start an agent at all. Opting in is explicit
        # and greppable, so the exception stays visible instead of becoming the
        # rule.
        if not allow_shell:
            fg = self.foreground(pane)
            if fg is not None and fg in SHELL_COMMANDS:
                raise PaneNotAgent(
                    f"pane {pane} is running {fg!r}, not an agent runtime — its "
                    f"agent has exited. NOT DELIVERED: typing here would execute "
                    f"the message as a shell command.")
        # -l sends the text literally; the separate Enter is the submit.
        # This is the entire dispatch mechanism. gt nudge's own help says so:
        # "Send directly via tmux send-keys."
        #
        # JOURNALED FIRST. During a live incident, three fabricated "recovered
        # — proceed" instructions were injected into recovery-staged agents'
        # panes by a sender no log could name: the routine send path was
        # ephemeral BY DESIGN, so the forensics dead-ended at "an unattached
        # process". Every keystroke st puts into a pane is an ACTION on the
        # fleet, and actions leave records. Logged before the send so an
        # interrupted delivery still leaves its attempt; never blocks or fails
        # a delivery (a broken audit trail must not take messaging down with
        # it) but says so on stderr rather than going quietly dark. Launch
        # strings are safe to journal by existing invariant: provisioning
        # forbids secrets in them (they live in 0600 files instead).
        # CHUNKED, and that is a correctness fix rather than a politeness
        # (aegis-wcjuz). A single large literal write is seen by a TUI as a
        # PASTE: codex absorbs it into a `[Pasted Content N chars]` placeholder
        # and the trailing Enter does NOT commit it, so the body sits in the
        # input box, unread, while st prints success. Measured on this host:
        #
        #     one write of 1000 chars   -> literal text, submits normally
        #     one write of 1004 chars   -> PASTE placeholder, stranded
        #     2000 chars as 2x1000      -> literal text. TOTAL SIZE IS IRRELEVANT
        #
        # The trigger is the size of a SINGLE WRITE, not the length of the
        # message, which is why chunking fixes it outright rather than merely
        # raising the ceiling. The chunk below is well under the measured
        # boundary because that boundary is somebody else's implementation
        # detail and may move; the cost of being conservative is a few extra
        # send-keys calls, and the cost of being wrong is a silently undelivered
        # message. The small gap between chunks keeps them separate writes.
        #
        # This lives in send() rather than in `st inbox` so EVERY caller gets it
        # — dispatch, escalations, tend prompts. The defect was found on inbox
        # and was never inbox's: it belongs to the one place st types into a pane.
        _journal_send(pane, text)
        # `or [""]` keeps the empty-body case sending exactly one `-l ""`, which
        # is what this always did — the split of an empty string is no chunks.
        chunks = [text[i:i + _SEND_CHUNK]
                  for i in range(0, len(text), _SEND_CHUNK)] or [""]
        for n, chunk in enumerate(chunks):
            subprocess.run(self._cmd("send-keys", "-t", pane, "-l", chunk),
                           check=True)
            if n + 1 < len(chunks):
                time.sleep(_SEND_CHUNK_GAP_S)
        subprocess.run(self._cmd("send-keys", "-t", pane, "Enter"), check=True)

    def control(self, pane: str, key: str) -> None:
        """Send ONE editing key from a fixed allowlist. Cannot submit. Ever.

        `st input --clear` has to reach a pane's input buffer, and send() is the
        wrong tool: it appends Enter, which is the submit. So this exists — and
        it is an ALLOWLIST rather than a general key sender on purpose.

        The keys below can only move the cursor, delete, or dismiss. None of
        them can commit the buffer or accept a suggestion, so no caller of this
        method — however wrong — can perform the injection this whole area is
        about. That is a property of the mechanism, not of the caller's care,
        which is the only kind of guarantee worth having here: the aegis-apz9
        incident was an injected line laundered into execution by a coordinator
        pressing Enter, and TAB would be the same act with an extra step (TAB
        ACCEPTS the suggestion, so a "cleanup" command that typed TAB would
        inject the suggestion into the agent's turn — self-inflicted, by the
        very tool built to prevent it).
        It also takes NO text argument, so it cannot type anything at all.

        Journaled exactly like send(), for the reason recorded there: every
        keystroke st puts into a pane is an ACTION on the fleet.
        """
        if key not in CONTROL_KEYS:
            # No citation in the MESSAGE: a raised string is a value the program
            # can emit, and this repo is public. The rationale lives in the
            # comment on CONTROL_KEYS, where it stays findable and leaks nothing.
            raise ValueError(
                f"{key!r} is not an allowed control key. Allowed: "
                f"{', '.join(sorted(CONTROL_KEYS))}. Enter and Tab are refused "
                f"BY CONSTRUCTION and must stay that way.")
        _journal_send(pane, f"<control:{key}>")
        subprocess.run(self._cmd("send-keys", "-t", pane, key), check=True)

    def option(self, pane: str, n: int) -> None:
        """Send ONE DIGIT, 1-9, to pick an option from a blocking picker.

        DELIBERATELY NOT PART OF control() (aegis-w30p2). control()'s allowlist
        means one thing — "keys that edit a line and cannot commit it" — and a
        digit is not that: at an input box a digit TYPES. Widening that set to
        carry this would make the c6hli invariant read "no Enter, no Tab, but
        also some characters that insert text", which is not an invariant anyone
        can check at a glance. Two verbs, two allowlists, each with one meaning.

        The safety here is not in this method, it is at the gate: picker.answer
        refuses unless the runtime says a picker is actually up, precisely
        because THIS call typed at an idle pane is a stray character in its
        input box.

        STILL NO ENTER, STILL NO TAB, and not by restraint — none is needed. A
        bare digit SELECTS AND CONFIRMS: measured 2026-08-01 on a live Claude
        Code pane, `1` at the folder-trust dialog and `3` at a Bash permission
        prompt, each acted on with no second keystroke.

        `-l` sends the digit LITERALLY. Without it tmux resolves the argument as
        a key NAME, and key-name space is not something to be standing in when
        the target is another agent's live session.
        """
        key = str(n)
        if key not in OPTION_KEYS:
            raise ValueError(
                f"{n!r} is not a selectable option key. Allowed: "
                f"{', '.join(sorted(OPTION_KEYS))}. One keystroke cannot address "
                f"a two-digit option.")
        _journal_send(pane, f"<option:{key}>")
        subprocess.run(self._cmd("send-keys", "-t", pane, "-l", key), check=True)

    def new_session(self, name: str, cwd: str | None = None) -> str:
        """Create a DETACHED, EMPTY session; return its address (the name).

        `cwd` is the session's START DIRECTORY (tmux -c). Without it a session
        inherits the LAUNCHER's cwd (GitHub #18), so every agent st started from
        the checkout began life rooted in the checkout rather than in its own
        workspace — and the launch string's own `cd` is the only thing that had
        ever hidden it. A shell opened later in that pane, or any tool reading
        the pane's cwd, saw the wrong project.

        RAISES if a session by that name already exists — never silently replace
        a live agent (arnold's #5 ruling: the clobber hazard, same family as
        RESTART-never-handoff). The caller checks exists() and decides.

        It makes an EMPTY shell only. It does NOT launch an agent — that is a
        runtime send(), outside this adapter, so a handoff (which drops
        --settings) cannot leak in through the pane layer.
        """
        if self.exists(name):
            raise RuntimeError(f"session {name!r} already exists — stop it first")
        argv = ["new-session", "-d", "-s", name]
        if cwd and Path(cwd).is_dir():
            # Only when it EXISTS: tmux fails the whole new-session on a missing
            # -c, and a launch refused because a directory is not there yet is a
            # worse outcome than a session in the wrong cwd (ensure_workspace
            # runs before this and reports the real problem).
            argv += ["-c", str(Path(cwd).expanduser().resolve())]
        subprocess.run(self._cmd(*argv), check=True)
        # Provenance marker: st launched this session, so st may stop
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

    def sessions(self) -> list[str] | None:
        """EVERY session on this socket — ours and not. None if we cannot ask.

        The rest of this adapter answers questions about panes we already know the
        name of, which is precisely why nothing here could see the aegis-np4x1
        collision: six agents ran for hours under a retired naming scheme on this
        very socket, and every name-addressed check we own looked straight past
        them. You cannot notice a session you never enumerate.

        None, not [], when tmux cannot be reached. An empty list is the claim
        "nothing else is running", and a detector that makes that claim because
        its probe failed is worse than no detector — it is the reassuring silence
        this function exists to end.
        """
        r = subprocess.run(
            self._cmd("list-sessions", "-F", "#{session_name}"),
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            # rc 1 with "no server running" is a genuine, answerable zero: there
            # is no server, so there are no sessions. Any other failure is a
            # question we did not get to ask.
            if "no server running" in (r.stderr or "").lower():
                return []
            return None
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]

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

    def session_created(self, name: str) -> float | None:
        """Tmux session birth as a Unix epoch; None when it cannot be read."""
        r = subprocess.run(
            self._cmd("display-message", "-t", name, "-p", "#{session_created}"),
            capture_output=True, text=True,
        )
        raw = r.stdout.strip()
        try:
            return float(raw) if r.returncode == 0 and raw else None
        except ValueError:
            return None

    def kill_session(self, name: str) -> None:
        """Destroy the session AND the process tree in its pane. IDEMPOTENT.

        kill-session alone is NOT enough for a real agent: killing the session
        SIGHUPs the pane's shell, but a child that ignores SIGHUP (measured: a
        real claude survived a session kill during teardown validation and had
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
                 live: set | None = None, owned: set | None = None,
                 cmdlines: dict | None = None,
                 created: dict[str, float] | None = None,
                 foreground_cmd: str = "claude") -> None:
        # What the pane's foreground process is, so a test can model the DEAD
        # pane the send guard exists for (aegis-ikj4t). Defaults to a runtime
        # rather than a shell: the overwhelming majority of tests model a live
        # agent, and a double that refused every send by default would make the
        # guard look correct by breaking everything that uses it.
        self.foreground_cmd = foreground_cmd
        self.sent = []
        # Control keys are recorded SEPARATELY from sent text: a test asserting
        # "no Enter, no Tab" must be able to see every key that reached the pane,
        # and folding them into `sent` would hide them among message bodies.
        self.controls = []
        # Picked options, kept apart from `controls` and `sent` for the same
        # reason those two are apart (aegis-w30p2): three different kinds of
        # keystroke reach a pane, and a test that folds them together cannot
        # assert what any one of them did.
        self.picked = []
        self.screen = screen
        # pane -> launch command line. Lets a test model the green-and-dead
        # shape: a pane that EXISTS while the process in it carries someone
        # else's wiring. Default None = "cannot read", which fails toward
        # RISING — the safe direction, and the one a null adapter must take.
        self._cmdlines = dict(cmdlines) if cmdlines is not None else None
        # Ownership provenance. new_session marks a session owned;
        # `owned=` seeds sessions as if st had launched them (for the owned-kill
        # path). A session that is `live` but NOT `owned` models the footgun: a
        # real crew session behind a colliding name that st must refuse to reap.
        self._owned: set = set(owned) if owned is not None else set()
        self._created = dict(created or {})
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

    def send(self, pane: str, text: str, *, allow_shell: bool = False) -> None:
        # SAME REFUSAL AS THE REAL ADAPTER, and duplicated for the same reason
        # control()'s allowlist is: a double that delivered where the shipped
        # path refuses would let a test prove messages reach a dead pane safely
        # while production refuses them — the double has to be as strict as the
        # thing it stands in for, or the assertion is theatre.
        if not allow_shell and self.foreground_cmd in SHELL_COMMANDS:
            raise PaneNotAgent(
                f"pane {pane} is running {self.foreground_cmd!r}, not an agent "
                f"runtime — NOT DELIVERED.")
        self.sent.append((pane, text))
        # A real pane shows what was just typed into it, so capture() must
        # reflect the send — otherwise this double models a pane that silently
        # eats every message, which is not a pane. Unless drops=True.
        if not self._drops:
            self.screen += ("\n" if self.screen else "") + text

    def control(self, pane: str, key: str) -> None:
        """Records the key; enforces the SAME allowlist as Tmux.control.

        The check is duplicated deliberately. A double that accepted keys the
        real adapter refuses would let a test prove `st input` never sends Enter
        while the shipped path happily could — the double has to be as strict as
        the thing it stands in for, or the assertion is theatre.
        """
        if key not in CONTROL_KEYS:
            raise ValueError(f"{key!r} is not an allowed control key")
        self.controls.append((pane, key))

    def option(self, pane: str, n: int) -> None:
        """Records the digit; enforces the SAME allowlist as Tmux.option.

        Duplicated for the reason control() records above: a double that accepted
        keys the real adapter refuses lets a test prove a refusal the shipped
        path does not make. Recorded in its OWN list — a test asking "what keys
        reached this pane" must be able to see a picked option as distinct from
        an editing key and from message text, or `no Enter, no Tab` is an
        assertion about a bucket nobody can enumerate.
        """
        key = str(n)
        if key not in OPTION_KEYS:
            raise ValueError(f"{n!r} is not a selectable option key")
        self.picked.append((pane, key))

    def cmdline(self, pane: str) -> str | None:
        """The launch line of the "process" in `pane`: the SEED if one was given,
        else what was last sent to it.

        THERE WERE TWO OF THESE (found 2026-07-20). Two crew members landed the
        same seam a day apart — one seeding a pane->cmdline dict, one deriving it
        from the last send — and Python kept the second, silently. So
        `NullPanes(cmdlines=...)` set an attribute nothing read, and every test
        that seeded a foreign launch line was measuring "could not read" while
        its name said it was measuring "alive but deaf". Both are honest verdicts,
        which is exactly why nobody noticed: the suite stayed green. One method
        now, and the seed wins because a test that states a launch line means it.

        The second impl of Tmux.cmdline, and faithful for the reason that matters
        to aegis-8p0j: a real pane's process cmdline IS the string the launcher
        typed into it. Modelling it as the last send keeps the launch-time hook
        check honest in tests — a double that always returned a well-formed
        cmdline would make `st new`'s verification unfalsifiable, which is the
        one thing this check must not be.

        None when unseeded AND nothing was ever sent: an empty pane has no
        process, so there is nothing to read. That is a cannot-tell, and the
        caller must not render it as a pass — never a fabricated launch line,
        which would let a test prove a lead drains when nothing was measured.
        """
        if self._cmdlines is not None and pane in self._cmdlines:
            return self._cmdlines[pane]
        for p, text in reversed(self.sent):
            if p == pane:
                return text
        return None

    def new_session(self, name: str, cwd: str | None = None) -> str:
        """RAISES if the name is live; else creates an empty session. Requires
        session-lifecycle mode (live set) — new_session on the ambient default
        would always raise, since everything ambiently exists."""
        if self._live is None:
            self._live = set()      # first session call opts into lifecycle mode
        if name in self._live:
            raise RuntimeError(f"session {name!r} already exists — stop it first")
        self._live.add(name)
        self._owned.add(name)       # st launched it -> st owns it
        self._created[name] = time.time()
        return name

    def owns(self, name: str) -> bool:
        return name in self._owned

    def session_created(self, name: str) -> float | None:
        return self._created.get(name)

    def sessions(self) -> list[str] | None:
        """The seeded `live` set, or None in ambient mode.

        Ambient mode says "every pane exists", which is a useful lie for the
        name-addressed calls and a useless one here — an enumerator cannot return
        the set of all possible names. None is the honest answer, and it is the
        same None the real adapter returns when it cannot ask, so callers get
        exercised on the cannot-tell branch rather than only the happy one.
        """
        return None if self._live is None else sorted(self._live)

    def kill_session(self, name: str) -> None:
        """Idempotent: discard removes if present, no-op if absent."""
        if self._live is None:
            self._live = set()
        self._live.discard(name)
        self._owned.discard(name)
        self._created.pop(name, None)
