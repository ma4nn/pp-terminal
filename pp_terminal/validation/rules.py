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
from typing import Annotated, Any, Literal, cast
import logging
import pandas as pd
from pydantic import Field

from pp_terminal.data.filters import filter_by_security, filter_by_type
from pp_terminal.domain.cost_basis import calculate_total_cost_basis
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import TransactionType
from pp_terminal.utils.config import Config, ConfigModel, UUIDStr
from pp_terminal.validation.base import ValidationRule
from pp_terminal.validation.vap_liquidity_rule import VapLiquidityRule
from pp_terminal.validation.paid_tax_validation_rule import PaidTaxValidationRule

log = logging.getLogger(__name__)


class _RuleConfig(ConfigModel):
    value: float | UUIDStr | None = None
    severity: Literal['warning', 'error'] = 'error'
    applies_to: Annotated[list[str], Field(min_length=1)] | None = None
    valid_months: list[Annotated[int, Field(ge=1, le=12)]] | None = None


class AccountRuleConfig(_RuleConfig):
    type: Literal['balance-limit', 'balance-limit-from-attribute', 'date-passed-from-attribute', 'vap-liquidity']


class SecurityRuleConfig(_RuleConfig):
    type: Literal['price-staleness', 'price-limit', 'price-limit-from-attribute', 'cost-basis-limit', 'cost-basis-limit-from-attribute', 'paid-tax-validation', 'negative-share-balance', 'unlinked-depot-transfer']
    tolerance: float = Field(0.0, ge=0)


class _AccountRulesConfig(ConfigModel):
    rules: list[AccountRuleConfig] = []


class _SecurityRulesConfig(ConfigModel):
    rules: list[SecurityRuleConfig] = []


class ValidateConfig(ConfigModel):
    accounts: _AccountRulesConfig = Field(default_factory=_AccountRulesConfig)
    securities: _SecurityRulesConfig = Field(default_factory=_SecurityRulesConfig)


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
    def provide_context(cls, portfolio: Portfolio, snapshot: PortfolioSnapshot, config: Config) -> dict[str, Any]:
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


class UnlinkedDepotTransferRule(ValidationRule):
    """Flags securities with a depot transfer (TRANSFER_OUT) that has no linked destination account.
    Portfolio Performance records the destination via a cross-entry link; a missing link indicates
    corrupt or stale-cache data, and the transferred shares' cost basis stays attributed to the
    source account instead of the destination."""

    @classmethod
    def provide_context(cls, portfolio: Portfolio, snapshot: PortfolioSnapshot, config: Config) -> dict[str, Any]:
        transfer_outs = portfolio.securities_account_transactions.pipe(filter_by_type, transaction_types=TransactionType.TRANSFER_OUT)
        target = transfer_outs.get('transferTargetAccount')  # optional in the schema, so absent on hand-built frames
        unlinked = transfer_outs.iloc[0:0] if target is None else transfer_outs[target.fillna('').astype(str).str.strip() == '']
        return {
            'unlinked_transfers': unlinked,
            'account_names': portfolio.securities_accounts['name'],
        }

    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:
        is_error, message = super().validate(entity, entity_id, context)
        if not self._should_apply():
            return is_error, message

        unlinked = context['unlinked_transfers']
        security_transfers = unlinked[unlinked.index.get_level_values('securityId') == entity_id]
        if security_transfers.empty:
            return False, None

        account_names = context['account_names']
        details = ', '.join(
            f'{shares:.2f} from "{account_names.get(account_id, account_id)}"'
            for (_date, account_id, _sec), shares in security_transfers['shares'].items()
        )
        message = f'has a depot transfer with no linked destination account ({details}); cost basis stays with the source account (corrupt or stale-cache data)'
        return self.is_error(), message


def create_built_in_securities_rules() -> list[ValidationRule]:
    """Data-integrity rules that run by default; a user-configured rule of the same type replaces the built-in one."""
    return [
        NegativeShareBalanceRule(rule_type='negative-share-balance', value=None, severity='warning', tolerance=0.001),
        UnlinkedDepotTransferRule(rule_type='unlinked-depot-transfer', value=None, severity='warning'),
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
    'unlinked-depot-transfer': UnlinkedDepotTransferRule,
}


def known_rule_types() -> set[str]:
    return set(_RULE_TYPES)


def create_rule(rule_config: AccountRuleConfig | SecurityRuleConfig) -> ValidationRule:
    rule_class = _RULE_TYPES[rule_config.type]

    return rule_class(  # type: ignore[abstract]
        rule_type=rule_config.type,
        value=rule_config.value,
        severity=rule_config.severity,
        applies_to=rule_config.applies_to,
        valid_months=rule_config.valid_months,
        tolerance=getattr(rule_config, 'tolerance', 0.0)
    )


def get_applicable_rules(entity_id: str, entity: pd.Series, rules: list[ValidationRule]) -> list[ValidationRule]:
    applicable_rules: list[ValidationRule] = []

    for rule in rules:
        if rule.matches_entity(entity, entity_id):
            applicable_rules.append(rule)

    return applicable_rules
