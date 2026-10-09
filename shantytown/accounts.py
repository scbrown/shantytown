"""Named subscription lanes. Configuration contains references, never credentials.

The legacy unnamed harness lanes remain unchanged until accounts are declared.
An account's default model is explicit: switching providers cannot carry a model
slug from the old provider, or guess one from a baked-in catalogue.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re

from . import governor as gov


class AccountError(ValueError):
    pass


@dataclass(frozen=True)
class Account:
    name: str
    harness: str
    model: str
    credential_ref: str
    governor: gov.Policy
    default: bool = False
    usage_account: str | None = None


def parse(table: dict) -> dict[str, Account]:
    """Strict, non-secret configuration. Each named account has its own meter/cap."""
    if not isinstance(table, dict):
        raise AccountError('accounts must be a table of named accounts')
    out = {}
    defaults = set()
    meters = set()
    allowed = {'harness', 'model', 'credential_ref', 'default', 'governor',
               'usage_account'}
    for name, raw in table.items():
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', name)
                or name in {'base', 'claude', 'codex', 'opencode'}):
            raise AccountError('account names must be distinct from legacy harness lanes')
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise AccountError(f'account {name}: unknown fields or invalid table')
        program = raw.get('harness')
        if program not in {'claude', 'codex'}:
            raise AccountError(f'account {name}: harness must be claude or codex')
        model = raw.get('model')
        if not isinstance(model, str) or not model.strip() or '\n' in model or '\r' in model:
            raise AccountError(f'account {name}: declare its default model')
        ref = raw.get('credential_ref')
        # File/env references let an operator's existing secret projection remain
        # authoritative. Infisical references are resolved only at actual launch.
        if (not isinstance(ref, str) or '\n' in ref or '\r' in ref
                or not re.fullmatch(r'(?:infisical:[A-Za-z_][A-Za-z0-9_-]*|'
                                    r'env:[A-Za-z_][A-Za-z0-9_]*|file:/[^\n]+)', ref)):
            raise AccountError(f'account {name}: credential_ref must be an Infisical, '
                               'environment or absolute-file reference, never a value')
        default = raw.get('default', False)
        if type(default) is not bool:
            raise AccountError(f'account {name}: default must be boolean')
        if default and program in defaults:
            raise AccountError(f'account {name}: duplicate default for {program}')
        if default:
            defaults.add(program)
        policy = gov.parse(raw.get('governor', {}))
        if not policy.active or policy.max_agents is None:
            raise AccountError(f'account {name}: declare independent governor tiers and max_agents')
        if policy.by_harness:
            raise AccountError(f'account {name}: a lane cannot contain sibling governors')
        usage = raw.get('usage_account')
        if usage is not None and (not isinstance(usage, str) or not usage.strip()):
            raise AccountError(f'account {name}: usage_account must be a non-empty label')
        if policy.source == 'codex_app_server':
            raise AccountError(f'account {name}: the unscoped app-server meter cannot '
                               'prove named-account usage; use scoped sessions or a meter')
        # Prometheus accounts must select a labelled account rather than borrowing
        # the max-across-accounts recording rule. Textfiles/session paths can be
        # separately projected by the operator; duplicate coordinates are refused.
        if policy.source == 'prometheus' and not usage:
            raise AccountError(f'account {name}: Prometheus needs usage_account')
        meter = (policy.source, policy.path, policy.url, policy.metric,
                 policy.account_metric, usage, policy.limit_id)
        if policy.source != 'stub' and meter in meters:
            raise AccountError(f'account {name}: shares another account\'s usage coordinates')
        meters.add(meter)
        out[name] = Account(name, program, model, ref, policy, default, usage)
    return out


def selected(card, cfg) -> Account | None:
    name = getattr(card, 'account', None)
    configured = getattr(cfg, 'accounts', {})
    if not configured and not name:
        return None
    if name:
        try:
            account = configured[name]
        except KeyError:
            raise AccountError('the card names an undeclared account') from None
        if card.harness and card.harness != account.harness:
            raise AccountError('the card account and harness disagree')
        return account
    program = (card.harness or cfg.harness_by_role.get(card.role)
               or cfg.harness_default or 'claude')
    return next((a for a in cfg.accounts.values()
                 if a.default and a.harness == program), None)


def lane(card, cfg) -> str:
    account = selected(card, cfg)
    return account.name if account else (
        card.harness or getattr(cfg, 'harness_by_role', {}).get(card.role) or getattr(cfg, 'harness_default', None) or 'claude')


def switch(card, target: Account, *, cfg, model=None, auto_failover=None):
    pin = cfg.harness_required_by_role.get(card.role)
    if pin and pin != target.harness:
        raise AccountError('the target account violates this role\'s harness pin')
    if card.chrome and target.harness != 'claude':
        raise AccountError('the target account cannot provide this card\'s browser capability')
    if model is not None and (not isinstance(model, str) or not model.strip()
                              or '\n' in model or '\r' in model):
        raise AccountError('target model must be a non-empty model name')
    # There is no forced pin/cap bypass in the automatic path.
    return replace(card, account=target.name, harness=target.harness,
                   model=model or target.model,
                   auto_failover=(card.auto_failover if auto_failover is None
                                  else auto_failover))


def constrained(verdict) -> bool:
    """Only a proven FULL STOP/P0-only lane, never a missing usage signal."""
    return bool(verdict and not verdict.signal_lost
                and (verdict.drains or verdict.floor == 0))


class ScopedPrometheusReader(gov.PrometheusReader):
    """Do not let another subscription's max or freshness stand in for ours."""
    def __init__(self, original, account):
        self.__dict__.update(original.__dict__)
        self.account = account

    def _samples(self):
        samples, error = super()._samples()
        if error is not None:
            return samples, error
        return [row for row in samples if row[1].get('account') == self.account], None


class ScopedTextfileReader(gov.TextfileReader):
    def __init__(self, original, account):
        self.__dict__.update(original.__dict__)
        self.account = account

    def read_all_detailed(self):
        try:
            samples = [row for row in gov.parse_prom(self.path.read_text())
                       if row[1].get('account') == self.account]
        except OSError:
            return {}, 'named account usage file could not be read', gov.LOCAL
        return gov._readings_by_window(samples, 'textfile', metric=self.metric,
                                       account_metric=self.account_metric), '', ''

    def read(self):
        return self.read_all().get(self.window, gov.Reading(
            source='textfile', error='named account has no usage observation'))


def reader_for(account):
    reader = gov.reader_for(account.governor)
    if account.usage_account and account.governor.source == 'prometheus':
        return ScopedPrometheusReader(reader, account.usage_account)
    if account.usage_account and account.governor.source == 'textfile':
        return ScopedTextfileReader(reader, account.usage_account)
    return reader


def receiver(card, cfg, verdicts, counts, *, catalog=None, preferred=None) -> Account | None:
    """Pick known headroom deterministically, across every configured account."""
    current = selected(card, cfg)
    if current is None or not constrained(verdicts.get(current.name)):
        return None
    candidates = []
    for name, target in cfg.accounts.items():
        if name == current.name:
            continue
        verdict = verdicts.get(name)
        if (verdict is None or verdict.signal_lost or verdict.frozen
                or verdict.drains or verdict.floor == 0 or verdict.pct is None
                or counts.get(name, 0) >= (verdict.max_agents if verdict.max_agents is not None
                                           else target.governor.max_agents)):
            continue
        try:
            updated = switch(card, target, cfg=cfg)
        except AccountError:
            continue
        if verdict.excludes(updated, catalog):
            continue
        candidates.append((0 if name == preferred else 1,
                           verdict.pct, counts.get(name, 0), name, target))
    return min(candidates, key=lambda row: row[:-1])[-1] if candidates else None
