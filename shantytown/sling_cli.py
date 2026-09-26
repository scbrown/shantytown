"""CLI adapters for an executive design handoff."""
from __future__ import annotations
import sys

from . import config, sling
from .answer import CouldNotLook, PartialAnswer
from .protocols import Agent


def target(a):
    from . import cli, fleet
    from .deployment import deployment_default, local_host
    cfg, error = config.load_or_default(a.root)
    if error:
        raise CouldNotLook(error)
    registry = cli._registry(a)
    if a.registry == 'files' and cfg.host_peers:
        if deployment_default(a.root, 'QUIPU_SERVER'):
            registry = cli.QuipuRegistry(root=a.root)
        else:
            cards = registry.all().exact()
            peers = fleet.collect(cfg.host_peers)
            if fleet.errors(peers):
                raise CouldNotLook('; '.join(fleet.errors(peers)))
            local = local_host(a.root)
            cards = [c for c in cards if c.host in (None, local)]
            for row in fleet.rows(peers):
                roles = tuple(row['role'].split(','))
                if 'executive' in roles and (not isinstance(row.get('tree_role'), str)
                                              or type(row.get('retired')) is not bool):
                    raise CouldNotLook('peer lacks executive role/retirement proof; update peer st')
                cards.append(Agent(name=row['name'], role=row.get('tree_role', roles[0]),
                                   reports_to=None, roles=roles, host=row['host'],
                                   retired=row.get('retired', False)))
            return sling.executive(cards)
    return sling.executive(registry.all().exact())


def command(a):
    from . import cli
    try:
        panes = cli._panes(a)
        sender, verified = cli._verified_sender(a, panes)
        if not verified or not sender:
            raise sling.Refused('sling requires a verified sender identity')
        recipient = target(a)
        if a.executive and a.executive != recipient.name:
            raise sling.Refused('executive changed during relay; nothing sent')
        cfg, error = config.load_or_default(a.root)
        if error:
            raise CouldNotLook(error)
        if cfg.host_peers and not recipient.host:
            raise sling.Refused('executive has no host placement; update the role registry')
        a.agent = recipient.name
        a.durable = True
        # The existing dispatch relay pins the receiving host, rejects --repo
        # reinterpretation, and sends note text on stdin rather than in a shell.
        note = cli._read_note(a) or ''
        a.me = sender
        remote = cli._go_on_host(a, note, recipient=recipient)
        if remote is not None:
            return remote
        tracker = cli._tracker(a, default='beads')
        plan = sling.prepare(sling.design(tracker, a.item), sender, recipient, note)
        print(f'executive: {recipient.name} (host={recipient.host or "local"})')
        print(plan['payload'])
        print('children: ' + (', '.join(plan['children']) or '(none)'))
        if note:
            print('handoff note: ' + note)
        if a.dry_run:
            return cli.OK
        box = cli._inbox(a, default='beads')
        receipt = sling.deliver(plan, tracker, box, a.root)
        print(f'durable handoff verified: {receipt.id}')
        # Leave unread for the stop hook; live submission is only a hint.
        try:
            local_card = cli._registry(a).get(recipient.name)
            if local_card.pane and panes.exists(local_card.pane):
                panes.send(local_card.pane, plan['payload'])
        except Exception as exc:
            print(f'live nudge unavailable ({type(exc).__name__}); durable receipt retained',
                  file=sys.stderr)
        return cli.OK
    except (sling.Refused, LookupError) as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return cli.REFUSED
    except (CouldNotLook, PartialAnswer, OSError, RuntimeError, ValueError) as exc:
        print(f'could not tell: {exc}; inspect handoff before retrying', file=sys.stderr)
        return cli.CANNOT_TELL
