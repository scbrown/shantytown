"""Opt-in serialized trusted-helper execution, separate from queue eligibility.

No native auto-merge or forge merge API lives here. Adoption must designate one
registry and the existing writer, review holds/blackout/rollback configuration,
and provide a control-proven remote reader. Default-off, no schedule installed.
# arming: library -- independent reviewed activation and real path proof pending.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
from .pr_preflight import Refused,Indeterminate

SHA=re.compile(r'[0-9a-f]{40}|[0-9a-f]{64}')


def execute_one(registry,record_path,checkout,receipt,helper,remote_read,*,
                enabled=False,writer=None,designated_writer=None,holds_clear=None,
                blackout=None,rollback=None,read_gate=None,runner=subprocess.run):
    if (enabled is not True or not writer or writer!=designated_writer
            or holds_clear is not True or blackout is not False
            or not isinstance(rollback,str) or not rollback.strip()):
        raise Refused('activation, writer, holds, blackout or rollback clearance unproven')
    if not callable(read_gate):
        raise Refused('fresh verified physical gate reader required')
    def gate():
        try:
            current=read_gate()
        except Exception as error:
            raise Refused('physical merge gate read unavailable') from error
        if (not isinstance(current,dict) or current.get('verified') is not True
                or current.get('enabled') is not True or current.get('writer')!=writer
                or current.get('holds_clear') is not True or current.get('blackout') is not False
                or current.get('rollback')!=rollback):
            raise Refused('current physical merge gates refuse')
    gate()
    helper=Path(helper)
    neutral=Path.home()/'.local/share/aegis-exec-src/scripts/pr-risk.py'
    if (not helper.is_absolute() or not helper.is_file()
            or helper.resolve()!=neutral.resolve()):
        raise Refused('installed ownership-neutral trusted helper required')
    record=json.loads(Path(record_path).read_text())
    try:
        snap=record['snapshot'];repo=snap['repo'];number=snap['number'];head=snap['head']
        author=record['author'];digest=record['id']
        if (not isinstance(repo,str) or not re.fullmatch(r'[\w.-]+/[\w.-]+',repo)
                or type(number) is not int or number<1
                or not isinstance(head,str) or not SHA.fullmatch(head)
                or not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest)
                or not isinstance(author,str) or not author or not isinstance(receipt,str) or not receipt):
            raise Refused('unknown exact candidate/author/receipt')
    except (KeyError,TypeError) as error:raise Refused('classification record unreadable') from error
    argv=['python3',str(helper),'merge',str(record_path),'--checkout',str(checkout),
          '--lead-receipt',receipt]
    with registry.locked() as data:
        binding=data['bindings'].get(f'{repo}#{number}')
        if (not isinstance(binding,dict) or binding['author']!=author or binding['head']!=head
                or not binding.get('reviewer') or binding['reviewer']==author):
            raise Refused('known author-bound candidate and independent reviewer required')
        slot=data.setdefault('merge_slot',None)
        if slot is not None and (not isinstance(slot,dict) or slot.get('digest')!=digest
                                 or slot.get('receipt')!=receipt):
            raise Refused('another candidate or receipt owns the serialized slot')
        actual=remote_read(repo,number)
        if (not isinstance(actual,dict) or actual.get('verified') is not True
                or actual.get('head')!=head or type(actual.get('merged')) is not bool):
            raise Refused('remote candidate read is unproven')
        if actual['merged']:
            sha=actual.get('merge_sha')
            if not isinstance(sha,str) or not SHA.fullmatch(sha) or actual.get('commit_verified') is not True:
                raise Indeterminate('merge is reported but remote commit proof is incomplete')
            # A previously attempted merge can converge through reads only.
            data['merge_slot']=None
            data.setdefault('merge_receipts',{})[digest]={'sha':sha,'head':head,'writer':writer}
            registry.save(data)
            return {'outcome':'merged','sha':sha,'reconciled':True}
        if slot is not None:
            raise Indeterminate('prior merge intent unresolved; absence is not permission to retry')
        preflight=runner(argv,capture_output=True,text=True,timeout=120)
        if preflight.returncode:
            raise Refused('trusted helper preflight refused; no merge intent written')
        output=getattr(preflight,'stdout','')+'\n'+getattr(preflight,'stderr','')
        proof=re.search(r'^TOOL SOURCE .+ sha=([0-9a-f]{40}) origin/main=([0-9a-f]{40}) state=current$',
                        output,re.MULTILINE)
        if not proof or proof.group(1)!=proof.group(2):
            raise Refused('trusted helper currency is unproven; no merge intent written')
        # Re-read physical conditions after potentially slow CI/review preflight.
        gate()
        # Helper --execute repeats its CI/review/expected-head/writer gates itself.
        data['merge_slot']={'digest':digest,'receipt':receipt,'head':head,'writer':writer,
                            'rollback':rollback}
        registry.save(data)
        try:
            result=runner([*argv,'--execute'],capture_output=True,text=True,timeout=120)
            actual=remote_read(repo,number)
            sha=actual.get('merge_sha') if isinstance(actual,dict) else None
            if (not isinstance(actual,dict) or actual.get('verified') is not True
                    or actual.get('head')!=head or actual.get('merged') is not True
                    or actual.get('commit_verified') is not True
                    or not isinstance(sha,str) or not SHA.fullmatch(sha)):
                raise Indeterminate('merge intent pending remote proof')
            # Remote proof can establish completion even if helper response was lost.
            data.setdefault('merge_receipts',{})[digest]={'sha':sha,'head':head,'writer':writer}
            data['merge_slot']=None;registry.save(data)
            return {'outcome':'merged','sha':sha,'helper_exit':result.returncode}
        except Exception as error:
            raise Indeterminate('merge intent pending; reconcile before another execute') from error
