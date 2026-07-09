"""
    Copyright (C) 2025-26 Dipl.-Ing. Christoph Massmann <chris@dev-investor.de>

    This file is part of pp-terminal.

    pp-terminal is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    pp-terminal is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with pp-terminal. If not, see <http://www.gnu.org/licenses/>.
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd

from pp_terminal.data.filters import filter_not_retired
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.utils.config import get_command_config
from .base import ValidationRule
from .rules import create_built_in_securities_rules, create_rule, get_applicable_rules


@dataclass
class ValidationResult:
    """Container for validation results of a single entity."""
    entity_id: str
    violations: list[tuple[ValidationRule, str]]  # (rule, message)

    @property
    def messages(self) -> str:
        """Returns icon-prefixed, semicolon-separated violation messages."""
        if not self.violations:
            return ''

        icon = self._get_icon()
        msgs = '; '.join(msg for _, msg in self.violations)
        return f'{icon} {msgs}' if icon else msgs

    def _get_icon(self) -> str:
        """Returns appropriate icon based on severity."""
        if not self.violations:
            return ''
        has_error = any(rule.is_error() for rule, _ in self.violations)
        return '❌' if has_error else '⚠️'

    @property
    def has_errors(self) -> bool:
        """Returns True if any violation is an error."""
        return any(rule.is_error() for rule, _ in self.violations)

    @classmethod
    def empty(cls, entity_id: str = '') -> 'ValidationResult':
        """Returns empty result with no violations."""
        return cls(entity_id=entity_id, violations=[])


def _validate_entity(
    entity_id: str,
    entity: pd.Series,
    rules: list[ValidationRule],
    context: dict[str, Any]
) -> ValidationResult:
    """Validates single entity against applicable rules."""
    violations = {}

    for rule in get_applicable_rules(entity_id, entity, rules):
        _, message = rule.validate(entity, entity_id, context)
        if message and str(rule) not in violations:  # record only first occurrence for each violation
            violations[str(rule)] = (rule, message)

    return ValidationResult(entity_id=entity_id, violations=list(violations.values()))


def validate_accounts(
    portfolio: Portfolio,
    snapshot: PortfolioSnapshot,
    config: dict[str, Any]
) -> dict[str, ValidationResult]:
    """Validates all deposit accounts. Returns dict mapping account_id -> ValidationResult."""
    rules = [create_rule(rule_config) for rule_config in get_command_config(config, 'validate.accounts.rules', [])]

    total_balances = snapshot.balances.groupby('accountId').sum()
    total_balances.name = 'TotalBalance'

    accounts_with_balances = pd.merge(
        portfolio.deposit_accounts,
        total_balances,
        left_index=True,
        right_index=True,
        how='right',
        validate='one_to_one'
    )

    accounts_with_balances = accounts_with_balances.pipe(filter_not_retired)

    results = {}
    for account_id, account in accounts_with_balances.iterrows():
        context = {
            'balance': account['TotalBalance'],
            'portfolio': portfolio,
            'snapshot': snapshot,
            'config': config,
        }
        result = _validate_entity(str(account_id), account, rules, context)
        results[str(account_id)] = result

    return results


def validate_securities(
    portfolio: Portfolio,
    config: dict[str, Any]
) -> dict[str, ValidationResult]:
    rules = create_built_in_securities_rules() + [create_rule(rule_config) for rule_config in get_command_config(config, 'validate.securities.rules', [])]

    latest_prices = portfolio.prices.groupby(['securityId']).tail(1)

    securities_with_prices = pd.merge(
        portfolio.securities,
        latest_prices.reset_index()[['securityId', 'date', 'price']],
        left_index=True,
        right_on='securityId',
        how='left',
        validate='one_to_one'
    ).set_index('securityId')

    securities_with_prices = securities_with_prices.pipe(filter_not_retired)

    base_context = {
        'portfolio': portfolio,
        'config': config,
    }

    for rule in rules:
        base_context.update(rule.__class__.provide_context(portfolio, config))

    results = {}
    for security_id, security in securities_with_prices.iterrows():
        result = _validate_entity(str(security_id), security, rules, base_context)
        results[str(security_id)] = result

    return results
