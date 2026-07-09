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
from pandera.typing import DataFrame

from pp_terminal.data.tax import load_prepaid_tax_data
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import TaxPaidSchema, Percent
from pp_terminal.domain.vap import calculate_base_yield_per_share, get_base_rate_for_year
from pp_terminal.utils.config import get_tax_files, get_exempt_rate_attribute
from pp_terminal.validation.base import ValidationRule

log = logging.getLogger(__name__)

_START_YEAR = 2018


def _get_diff_percent(val1: float, val2: float) -> Percent:
    if val2 != 0:
        return abs(val1 - val2) / abs(val2)

    return float('inf') if val1 != 0 else 0


class PaidTaxValidationRule(ValidationRule):
    """Validates calculated VAP base yield against paid tax data from CSV files."""

    @classmethod
    def provide_context(cls, portfolio: Portfolio, snapshot: PortfolioSnapshot, config: dict[str, Any]) -> dict[str, Any]:
        tax_files = get_tax_files(config)
        tax_csv_data: DataFrame[TaxPaidSchema] | None = load_prepaid_tax_data(tax_files, portfolio) if tax_files else None

        return {
            'base_yield_by_year': cls._calculate_base_yield_since(portfolio),
            'tax_csv_data': tax_csv_data,
            'exempt_rate_attr_uuid': get_exempt_rate_attribute(config),
        }

    @staticmethod
    def _calculate_base_yield_since(portfolio: Portfolio) -> dict[int, pd.Series]:
        base_yield_by_year: dict[int, pd.Series] = {}
        current_year = datetime.now().year

        for year in range(_START_YEAR, current_year + 1):
            snapshot_begin = PortfolioSnapshot(portfolio, datetime(year, 1, 2))
            snapshot_end = PortfolioSnapshot(portfolio, datetime(year, 12, 31))
            base_rate = get_base_rate_for_year(year)

            base_yield_by_security = calculate_base_yield_per_share(snapshot_begin, snapshot_end, base_rate)
            if not base_yield_by_security.empty:
                base_yield_by_year[year] = base_yield_by_security

        return base_yield_by_year

    def validate(self, entity: pd.Series, entity_id: str, context: dict[str, Any]) -> tuple[bool, str | None]:  # pylint: disable=too-many-locals,too-many-branches
        is_error, message = super().validate(entity, entity_id, context)
        if not self._should_apply():
            return is_error, message

        tax_csv_data: DataFrame[TaxPaidSchema] | None = context.get('tax_csv_data')
        base_yield_by_year: dict[int, dict[str, Any]] | None = context.get('base_yield_by_year')
        portfolio = cast(Portfolio, context.get('portfolio'))
        exempt_rate_attr_uuid: str | None = context.get('exempt_rate_attr_uuid')

        if exempt_rate_attr_uuid and exempt_rate_attr_uuid in portfolio.securities.columns:
            exempt_rate = portfolio.securities.loc[entity_id, exempt_rate_attr_uuid]
            if pd.notna(exempt_rate) and exempt_rate >= 1.0:
                log.debug('Paid tax validation skipped for security %s: 100%% exempt rate', entity_id)
                return False, None

        if tax_csv_data is None or tax_csv_data.empty:
            log.debug('Paid tax validation skipped for security %s: no tax CSV data', entity_id)
            return False, None

        if base_yield_by_year is None:
            log.debug('Paid tax validation skipped for security %s: no pre-calculated base yield data', entity_id)
            return False, None

        mismatches = []
        current_year = datetime.now().year

        for year in range(_START_YEAR, current_year):  # exclude current year
            calculated_value = base_yield_by_year.get(year, {}).get(entity_id, 0.0)
            snapshot_end = PortfolioSnapshot(portfolio, datetime(year, 12, 31))
            shares = snapshot_end.shares.groupby(level='securityId').sum().to_dict()

            # only respect if in portfolio
            if entity_id not in shares or shares[entity_id] <= 0:
                continue

            if (year, entity_id) in tax_csv_data.index:
                csv_value = float(tax_csv_data.loc[(year, entity_id), 'deemed_income'])
            else:
                csv_value = 0.0

            diff_percent = _get_diff_percent(calculated_value, csv_value)
            if diff_percent > self.tolerance:
                mismatches.append(f"{year} (calc: {calculated_value:.2f}, csv: {csv_value:.2f}, {'+' if diff_percent > 0 else ''}{diff_percent*100:.1f}%)")
            else:
                log.debug('yield values within tolerance for %d: %s vs. %s', year, calculated_value, csv_value)

        if mismatches:
            message = f"unexpected/missing paid tax values: {', '.join(mismatches)}"
            return self.is_error(), message

        return False, None
