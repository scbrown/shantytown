"""Public-data CI check for crew-owned PR primary bindings (aegis-62g).

# arming: ci -- example workflow only; repositories must adopt it explicitly.
External contributor forks are outside crew policy. Unknown ownership refuses.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .pr_binding import primary
from .pr_preflight import Refused


def check(event, owner):
    if not isinstance(owner,str) or not owner:
        raise Refused('explicit crew repository owner required')
    try:
        repository=event['repository']['full_name']
        base=event['pull_request']['base']['repo']['full_name']
        head=event['pull_request']['head']['repo']['full_name']
        body=event['pull_request']['body']
    except (KeyError,TypeError) as error:
        raise Refused('PR ownership/event payload unreadable') from error
    for name in (repository,base,head):
        if not isinstance(name,str) or len(name.split('/'))!=2 or not all(name.split('/')):
            raise Refused('PR repository identity unreadable')
    if base!=repository or repository.split('/')[0]!=owner:
        raise Refused('event does not match the configured crew repository')
    if head.split('/')[0]!=owner:
        return {'scope':'external-contributor','required':False}
    return {'scope':'crew','required':True,'bead':primary(body)}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Check one primary bead in a crew-owned PR')
    parser.add_argument('--event-file',type=Path,required=True)
    parser.add_argument('--owner',required=True)
    args=parser.parse_args(argv)
    try:
        if args.event_file.stat().st_size>4_000_000:
            raise Refused('event payload exceeds its bound')
        result=check(json.loads(args.event_file.read_text()),args.owner)
        print(json.dumps(result,sort_keys=True));return 0
    except (Refused,ValueError,OSError):
        print('Primary bead check refused: missing, ambiguous or unreadable binding/ownership')
        return 2

if __name__=='__main__':raise SystemExit(main())
