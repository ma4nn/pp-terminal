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

from datetime import datetime
from typing import Any, cast
import logging
import pandas as pd

from pp_terminal.data.filters import filter_by_security
from pp_terminal.domain.cost_basis import calculate_total_cost_basis
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.validation.base import ValidationRule
from pp_terminal.validation.vap_liquidity_rule import VapLiquidityRule
from pp_terminal.validation.paid_tax_validation_rule import PaidTaxValidationRule

log = logging.getLogger(__name__)


class BalanceLimitRule(ValidationRule):
    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:
        is_error, message = super().validate(entity, entity_id, context)
        if not self._should_apply():
            return is_error, message

        limit = self._get_value(entity)
        balance = context['balance']

        if balance > limit:
            message = f'balance {balance:.2f} exceeds limit {limit:.2f}'
            return self.is_error(), message
        return False, None


class DatePassedRule(ValidationRule):
    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:
        is_error, message = super().validate(entity, entity_id, context)
        if not self._should_apply():
            return is_error, message

        date_value = self._get_value(entity)

        if pd.isna(date_value):
            return False, None

        if not isinstance(date_value, datetime):
            try:
                date_value = pd.to_datetime(date_value)
            except (ValueError, TypeError):
                log.warning('"%s" has invalid date value: %s', entity["name"], date_value)
                return False, None

        attribute_name = 'date attribute'
        portfolio = cast(Portfolio, context.get('portfolio')) if context else None
        if portfolio is not None:
            attribute_name = portfolio.all_attributes.get(self._value, attribute_name)

        current_date = datetime.now()
        if date_value < current_date:
            message = f'{attribute_name} has passed {date_value.strftime("%Y-%m-%d")}'
            return self.is_error(), message
        return False, None


class PriceStalenessRule(ValidationRule):
    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:
        is_error, message = super().validate(entity, entity_id, context)
        if not self._should_apply():
            return is_error, message

        max_days = self._get_value(entity)
        latest_price_date = entity.get('date')

        if pd.isna(latest_price_date) or latest_price_date is None:
            message = 'no price data'
            return self.is_error(), message

        current_date = datetime.now()
        days_old = (current_date - latest_price_date).days

        if days_old > max_days:
            message = f'price is {days_old} days old (latest: {latest_price_date.strftime("%Y-%m-%d")})'
            return self.is_error(), message
        return False, None


class PriceLimitRule(ValidationRule):
    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:
        is_error, message = super().validate(entity, entity_id, context)
        if not self._should_apply():
            return is_error, message

        limit = self._get_value(entity)
        current_price = entity.get('price')

        if pd.isna(current_price):
            message = 'no price data'
            return self.is_error(), message

        if current_price >= limit:
            message = f'price {current_price:.2f} has reached limit {limit:.2f}'
            return self.is_error(), message
        return False, None


class CostBasisLimitRule(ValidationRule):
    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:
        is_error, message = super().validate(entity, entity_id, context)
        if not self._should_apply():
            return is_error, message

        limit = self._get_value(entity)
        portfolio = cast(Portfolio, context.get('portfolio'))

        if portfolio is None:
            raise RuntimeError('No portfolio in context for cost-basis-limit validation')

        current_cost = calculate_total_cost_basis(portfolio.securities_account_transactions.pipe(filter_by_security, security_id=entity_id))

        if current_cost > limit:
            currency = entity.get('currency', 'EUR')
            message = f'current cost basis {current_cost:.2f} {currency} exceeds limit {limit:.2f} {currency}'
            return self.is_error(), message

        return False, None


class NegativeShareBalanceRule(ValidationRule):
    """Flags securities with a negative share balance in any account, which indicates
    missing or inconsistent transactions (e.g. sells exceeding buys) since short positions
    are not supported by Portfolio Performance."""

    @classmethod
    def provide_context(cls, portfolio: Portfolio, snapshot: PortfolioSnapshot, config: dict[str, Any]) -> dict[str, Any]:
        # share counts are currency-independent, so net out forex/native transaction legs
        balances = snapshot.share_balances.groupby(['accountId', 'securityId']).sum()
        return {
            'negative_share_balances': balances[balances < 0],
            'account_names': portfolio.securities_accounts['name'],
        }

    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:
        is_error, message = super().validate(entity, entity_id, context)
        if not self._should_apply():
            return is_error, message

        balances = context['negative_share_balances']
        negative_balances = balances[
            (balances.index.get_level_values('securityId') == entity_id) & (balances < -self.tolerance)
        ]
        if negative_balances.empty:
            return False, None

        account_names = context['account_names']
        details = ', '.join(
            f'{share_count:.2f} in "{account_names.get(account_id, account_id)}"'
            for (account_id, _), share_count in negative_balances.items()
        )
        message = f'has negative share balance ({details}), transactions seem to be missing or inconsistent'
        return self.is_error(), message


def create_built_in_securities_rules() -> list[ValidationRule]:
    """Data-integrity rules that run by default; a user-configured rule of the same type replaces the built-in one."""
    return [
        NegativeShareBalanceRule(rule_type='negative-share-balance', value=None, severity='warning', tolerance=0.001),
    ]


_RULE_TYPES = {
    'balance-limit': BalanceLimitRule,
    'balance-limit-from-attribute': BalanceLimitRule,
    'date-passed-from-attribute': DatePassedRule,
    'price-staleness': PriceStalenessRule,
    'price-limit': PriceLimitRule,
    'price-limit-from-attribute': PriceLimitRule,
    'cost-basis-limit': CostBasisLimitRule,
    'cost-basis-limit-from-attribute': CostBasisLimitRule,
    'vap-liquidity': VapLiquidityRule,
    'paid-tax-validation': PaidTaxValidationRule,
    'negative-share-balance': NegativeShareBalanceRule,
}


def create_rule(rule_config: dict[str, Any]) -> ValidationRule:
    rule_type = rule_config['type']
    if rule_type not in _RULE_TYPES:
        raise ValueError(f'Unknown rule type: {rule_type}')

    rule_class = _RULE_TYPES[rule_type]

    return rule_class(  # type: ignore[abstract]
        rule_type=rule_type,
        value=rule_config.get('value', None),
        severity=rule_config.get('severity', 'error'),
        applies_to=rule_config.get('applies-to', None),
        valid_months=rule_config.get('valid-months', None),
        tolerance=rule_config.get('tolerance', 0.0)
    )


def get_applicable_rules(entity_id: str, entity: pd.Series, rules: list[ValidationRule]) -> list[ValidationRule]:
    applicable_rules: list[ValidationRule] = []

    for rule in rules:
        if rule.matches_entity(entity, entity_id):
            applicable_rules.append(rule)

    return applicable_rules
