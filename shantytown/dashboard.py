"""dashboard — a live, read-only view of ONE admin's tier (aegis-h4qe, Part A).

The always-on sibling of `st crew`: where crew is a one-shot roster of the whole
fleet, this is a self-refreshing panel scoped to an administrator and its
reporting crew — roster, current work, STATE, last activity, live tallies — that
an operator keeps in a second pane while talking to that admin.

REUSE, DO NOT RE-DERIVE. The busy/idle/waiting/saturated verdicts already exist
(triage.work_state, the same computation `st crew` and the notify loop use). This
module is handed those verdicts and the plate items; it composes, it does not form
a second opinion. Two surfaces that each decide "is this agent saturated" will one
day disagree, and the disagreement is the exact ambiguity this fleet keeps paying
for. So `gather()` takes the crew-state tuples and the plate reader as INPUTS.

HONEST ABOUT GAPS. The rich stats — files touched, skills used, tokens, avg
time-on-item, throughput — need the capture layer that is Part B (aegis-5lwl), not
yet built. This view shows what it CAN measure now (state, current item, last stop
event) and NAMES the rest as needing capture, rather than printing a fabricated or
stale number. A dashboard that lies is worse than one that admits a blank.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Row:
    """One agent's line in the dashboard."""
    name: str
    role: str
    pane_state: str            # up | down | no pane
    work: str                  # the REUSED verdict: busy/idle/waiting/saturated/…/—
    item: str | None           # the held work item id, if any
    item_status: str | None    # its status
    last_activity: float | None  # epoch secs of the agent's last stop event, or None
    title: str = ""


@dataclass
class Dashboard:
    admin: str
    rows: list[Row] = field(default_factory=list)
    at: float = 0.0            # when this snapshot was taken

    # --- live tallies, computed from the REUSED verdicts (no re-derivation) ---
    def _count(self, pred) -> int:
        return sum(1 for r in self.rows if pred(r))

    @property
    def up(self) -> int:
        return self._count(lambda r: r.pane_state == "up")

    @property
    def down(self) -> int:
        return self._count(lambda r: r.pane_state == "down")

    def in_state(self, verdict: str) -> list[str]:
        # startswith: a verdict can carry a suffix (`saturated·687k`, `idle+1sh`).
        return [r.name for r in self.rows if r.work.startswith(verdict)]


def tier_of(admin: str, agents):
    """The admin plus every agent whose reports_to chain reaches it.

    Transitive, so a multi-level tier (workers -> lead -> admin) is whole. A cycle
    in reports_to (a misconfiguration) is broken by the visited set rather than
    looping forever — the dashboard must render even over a broken graph, the same
    way roles --check surfaces a bad graph instead of hanging on it.
    """
    by_name = {a.name: a for a in agents}
    members = []
    for a in agents:
        if a.name == admin:
            members.append(a)
            continue
        cur, seen = a, set()
        while cur is not None and cur.name not in seen:
            seen.add(cur.name)
            if cur.reports_to == admin:
                members.append(a)
                break
            cur = by_name.get(cur.reports_to) if cur.reports_to else None
    return members


def gather(admin: str, agents, crew_states, plate_reader, last_activity, at):
    """Compose the dashboard for `admin`'s tier — PURE, so it is testable without
    tmux, a tracker, or a clock.

    crew_states: an iterable of (agent, pane_state, work_verdict) — the SAME tuples
      `st crew` renders, so the state here can never disagree with the roster.
    plate_reader: name -> WorkItem|None (the held item). Reused from `st anchor`.
    last_activity: {name: epoch_ts} of each agent's last stop event.
    """
    members = {a.name for a in tier_of(admin, agents)}
    by_name = {a.name: a for a in agents}
    rows = []
    for ag, pane_state, work in crew_states:
        if ag.name not in members:
            continue
        item = None
        status = None
        title = ""
        # Only ask the tracker about a live agent — a down pane holds nothing we
        # can act on, and a plate lookup per down agent is cost for no signal.
        if pane_state == "up":
            try:
                held = plate_reader(ag.name)
            except Exception:
                held = None
            if held is not None:
                item, status, title = held.id, held.status, held.title
        rows.append(Row(
            name=ag.name,
            role=by_name[ag.name].role if ag.name in by_name else ag.role,
            pane_state=pane_state,
            work=work,
            item=item,
            item_status=status,
            last_activity=last_activity.get(ag.name),
            title=title,
        ))
    rows.sort(key=lambda r: r.name)
    return Dashboard(admin=admin, rows=rows, at=at)


def _age(ts: float | None, now: float) -> str:
    """Human "3m ago", or "—" when unknown. Unknown is NOT "just now": an agent
    with no timestamped stop event has an UNKNOWN last-activity, and rendering that
    as recent would invent a liveness the store never recorded."""
    if not ts:
        return "—"
    secs = max(0, int(now - ts))
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def render(d: Dashboard, now: float, *, width: int | None = None,
           offset: int = 0) -> str:
    """The panel as a string. Kept pure so a test asserts the rendering, and the
    CLI just prints it on each refresh."""
    from . import triage as triage_mod

    lines = []
    lines.append(f"  TIER OF {d.admin}   ·   {d.up} up / {d.down} down   ·   "
                 f"{_age(d.at, now)}".rstrip())
    lines.append("")
    if width is None:
        lines.append(f"  {'AGENT':<12} {'ROLE':<14} {'STATE':<15} {'ITEM':<12} "
                     f"{'LAST':<9} ASSIGNED TITLE")
    else:
        lines.append("  AGENT         STATE / LAST          ASSIGNED WORK")
    for r in d.rows:
        state = r.work if r.pane_state == "up" else f"({r.pane_state})"
        if width is None:
            lines.append(f"  {r.name:<12} {r.role:<14} {state:<15} {r.item or '—':<12} "
                         f"{_age(r.last_activity, now):<9} {text_window(r.title, None)}")
        else:
            # One row per agent, even in a short pane; reserve useful space for
            # the title instead of putting it beyond fixed metadata columns.
            prefix = _row_prefix(r, state, now, width)
            text = assigned_text(r.item, r.title) if r.item else "—"
            room = max(1, width - sum(w for _, w in _cells(prefix)))
            lines.append(prefix + text_window(text, room, offset))
    lines.append("")

    # Live tallies from the REUSED verdicts — the coordinator's at-a-glance.
    waiting = d.in_state(triage_mod.WAITING)
    saturated = d.in_state(triage_mod.SATURATED)
    tally = f"  busy {len(d.in_state(triage_mod.BUSY))} · idle {len(d.in_state(triage_mod.IDLE))}"
    if waiting:
        tally += f" · ⚠ BLOCKED {len(waiting)}: {', '.join(waiting)}"
    if saturated:
        tally += f" · ⚠ SATURATED {len(saturated)}: {', '.join(saturated)}"
    lines.append(tally)

    # Honest about the gap: throughput/timing/files/skills/tokens need Part B.
    lines.append("  stats: throughput · time-on-item · files · skills · tokens — "
                 "need capture (st agent stats, Part B; not yet measured, not faked).")
    return "\n".join(lines)


def assigned_text(item: str, title: str) -> str:
    return f"{item} — {title or '(title unavailable)'}"


def _cells(text: str) -> list[tuple[str, int]]:
    """Printable units, keeping combining marks attached and wide glyphs whole.

    Titles come from a tracker, not a trusted terminal program. Control bytes
    must not turn a title into cursor movement or an extra roster row.
    """
    import unicodedata

    cells = []
    for ch in text:
        if unicodedata.category(ch).startswith("C") or ch.isspace():
            ch = " "
        if unicodedata.combining(ch):
            if cells:
                prev, width = cells[-1]
                cells[-1] = (prev + ch, width)
            continue
        cells.append((ch, 2 if unicodedata.east_asian_width(ch) in "WF" else 1))
    return cells


def text_window(text: str, width: int | None, offset: int = 0) -> str:
    """A horizontal title viewport: ‹ means hidden left, … hidden right.

    Clamp per title so End shows every suffix, even when row lengths differ.
    None is the untruncated, printable one-shot form used by crew --wide.
    """
    cells = _cells(text)
    if width is None or sum(w for _, w in cells) <= width:
        return "".join(ch for ch, _ in cells)
    if width <= 0:
        return ""
    start = min(max(0, offset), _last_start(cells, width))
    if width == 1:
        return "‹" if start else "…"
    left = "‹" if start else ""
    remaining = cells[start:]
    right = sum(w for _, w in remaining) > width - len(left)
    room = max(0, width - len(left) - int(right))
    out, used = [], 0
    for ch, w in remaining:
        if used + w > room:
            break
        out.append(ch)
        used += w
    return left + "".join(out) + ("…" if right else "")


def _last_start(cells, width: int) -> int:
    if sum(w for _, w in cells) <= width:
        return 0
    end, used = len(cells), 0
    while end and used + cells[end - 1][1] <= width - 1:
        end -= 1
        used += cells[end][1]
    return end


def _row_prefix(row: Row, state: str, now: float, width: int) -> str:
    name_width = min(12, max(1, width // 4))
    prefix = f" {text_window(row.name, name_width):<{name_width}} "
    if width >= 70:
        prefix += f"{text_window(state, 15):<15} {_age(row.last_activity, now):<9} "
    elif width >= 45:
        prefix += f"{text_window(state, 12):<12} "
    return prefix


def _scroll_end(data: Dashboard, width: int, now: float) -> int:
    ends = []
    for row in data.rows:
        if row.item:
            state = row.work if row.pane_state == "up" else f"({row.pane_state})"
            prefix = _row_prefix(row, state, now, width)
            room = max(1, width - sum(w for _, w in _cells(prefix)))
            ends.append(_last_start(_cells(assigned_text(row.item, row.title)), room))
    return max(ends, default=0)


def watch(snapshot, interval: float) -> int:
    """Curses owns/restores terminal modes; only the assigned lines scroll."""
    import curses

    return curses.wrapper(lambda screen: _watch(screen, snapshot, interval))


def _watch(screen, snapshot, interval: float) -> int:
    import curses
    import time

    screen.keypad(True)
    offset = 0
    data = None
    refresh_at = 0.0
    while True:
        now = time.monotonic()
        if data is None or now >= refresh_at:
            rc, data = snapshot()
            if data is None:
                return rc
            refresh_at = time.monotonic() + interval
        height, width = screen.getmaxyx()
        # The bottom-right cell may scroll a curses terminal. Leave it unused.
        columns = max(1, width - 1)
        lines = render(data, time.time(), width=columns, offset=offset).splitlines()
        screen.erase()
        for y, line in enumerate(lines[:max(0, height - 1)]):
            try:
                screen.addstr(y, 0, text_window(line, columns))
            except curses.error:
                pass  # a resize can race getmaxyx; redraw on the next key/tick
        hint = "Left/Right: scroll titles · Home/End · q/Ctrl-C: quit"
        try:
            screen.addstr(max(0, height - 1), 0, text_window(hint, columns))
        except curses.error:
            pass
        screen.refresh()
        screen.timeout(max(1, int((refresh_at - time.monotonic()) * 1000)))
        key = screen.getch()
        longest = _scroll_end(data, columns, time.time())
        offset = min(offset, longest)
        if key in (ord("q"), ord("Q"), 3):
            return 0
        if key == curses.KEY_LEFT:
            offset = max(0, offset - 8)
        elif key == curses.KEY_RIGHT:
            offset = min(longest, offset + 8)
        elif key == curses.KEY_HOME:
            offset = 0
        elif key == curses.KEY_END:
            offset = longest
