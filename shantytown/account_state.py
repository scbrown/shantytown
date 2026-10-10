"""Non-secret identity of the launched account, separate from the next card."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from .files import write_json_atomic


def _path(root, name):
    return Path(root) / 'governor' / 'account-launches' / (name + '.json')


def record(root, card, cfg, panes=None):
    from . import accounts, harness
    selected = accounts.selected(card, cfg)
    if selected is None and not cfg.accounts:
        return
    session = card.pane or 'st-' + card.name
    born = None
    if panes is not None and hasattr(panes, 'session_created'):
        born = panes.session_created(session)
    value = dict(account=selected.name if selected else None,
                 harness=harness.name_for(card, root), model=harness.resolve_model(card, root),
                 session=session, born=born)
    path = _path(root, card.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, value)


def process_card(root, card, cfg, panes):
    """Charge a deferred card change to its running account until relaunch."""
    if not getattr(cfg, "accounts", {}) or not card.pane or not panes.exists(card.pane):
        return card
    try:
        value = json.loads(_path(root, card.name).read_text())
    except (OSError, ValueError):
        # No new-account launch stamp: legacy sessions still belong to their
        # declared default account. Inspect the actual harness before the card.
        from . import harness
        reader = getattr(panes, 'cmdline', None)
        try:
            actual = harness.running_name(reader(card.pane)) if callable(reader) else None
        except (OSError, RuntimeError):
            actual = None
        program = actual or card.harness or cfg.harness_by_role.get(card.role) or cfg.harness_default or 'claude' 
        default = next((a for a in cfg.accounts.values()
                        if a.default and a.harness == program), None)
        return replace(card, harness=program, account=default.name if default else '')
    if value.get('session') != card.pane:
        return replace(card, account='')
    if hasattr(panes, 'session_created') and value.get('born') is not None:
        if panes.session_created(card.pane) != value['born']:
            return replace(card, account='')
    name = value.get('account')
    if name and name not in cfg.accounts:
        return replace(card, account='')
    return replace(card, account=name or '', harness=value.get('harness') or card.harness,
                   model=value.get('model'))
