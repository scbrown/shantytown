"""Untaken higher-priority work is advice, never an admission gate.

# arming: library; dispatch, crew, Stop haul and tend are the callers.
"""
from .inbox import drop_parked, is_anchor, is_decision, is_message, is_unfeedable


def priority(row):
    v = row.get('priority')
    try:
        return None if v is None or isinstance(v, bool) else int(str(v).removeprefix('P'))
    except (ValueError, TypeError):
        return None


def down_agents(reg, panes):
    out = set()
    for card in reg.all().exact():
        try:
            if not card.pane or not panes.exists(card.pane):
                out.add(card.name)
        except Exception:
            pass  # Unreadable liveness is not proof of a stopped owner.
    return out


def workable(row):
    labels = row.get("labels") or []
    title = row.get("title") or ""
    return not (is_message(title) or is_decision(labels) or is_anchor(labels)
                or is_unfeedable(title, labels) or row.get("blocked_by")
                or row.get("blocked_count")
                or any(l.startswith(("blocked:", "parked:")) for l in labels))


def advice(ready, selected, down=()):
    p = priority(selected)
    if p is None:
        return ''
    found = {}
    for r in drop_parked(ready):
        owner = (r.get('assignee') or '').split('/')[-1]
        q = priority(r)
        if (r.get('status', 'open') != 'open' or q is None or q >= p
                or r.get('id') == selected.get('id') or (owner and owner not in down)
                or not workable(r)):
            continue
        if r.get('id'):
            found[r['id']] = r
    rows = sorted(found.values(), key=lambda r: (priority(r), r['id']))
    if not rows:
        return ''
    names = ', '.join(f"{r['id']} (P{priority(r)})" for r in rows[:3])
    return (f"governor: {len(rows)} higher-priority ready bead(s) sit untaken "
            f"while {selected.get('id', '?')} is P{p}: {names}. "
            "Advisory only; record the reason when choosing lower-priority work.")


def fleet_advice(ready, active, reg, panes):
    cards = reg.all().exact()
    down = down_agents(reg, panes)
    live = set()
    for c in cards:
        try:
            if c.pane and panes.exists(c.pane):
                live.add(c.name)
        except Exception:
            pass
    running = [r for r in drop_parked(active)
               if (r.get('assignee') or '').split('/')[-1] in live
               and priority(r) is not None and workable(r)]
    # One finding per sweep: repeating the same waiters for every active agent
    # buries the actionable names in the roster. The lowest-priority live item
    # witnesses the widest set of neglected higher-priority work.
    running.sort(key=lambda r: (-priority(r), r.get('id', '')))
    for row in running:
        if note := advice(ready, row, down):
            return [note]
    return []
