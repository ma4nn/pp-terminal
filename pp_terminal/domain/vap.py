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
import logging

import pandas as pd
import numpy as np
from pandera.typing import DataFrame

from pp_terminal.data.filters import filter_by_type, drop_empty_values
from pp_terminal.data.tax import get_exempt_multiplier_per_security
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot, _NEGATIVE_SECURITIES_ACCOUNT_TRANSACTION_TYPES
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import TransactionType, Percent, Money, VapResultSchema

log = logging.getLogger(__name__)

# Basiszinssatz (base interest rate) by year for German tax calculations
# @link https://www.bundesbank.de/de/statistiken/geld-und-kapitalmaerkte/zinssaetze-und-renditen/basiszinssatz
_BASE_RATE_BY_YEAR: dict[int, Percent] = {
    2018: 0.87,
    2019: 0.52,
    2020: 0.07,
    2021: -0.45,
    2022: -0.05,
    2023: 2.55,
    2024: 2.29,
    2025: 2.53,
    2026: 3.2,
}


def get_base_rate_for_year(year: int, fallback: Percent = 3.2) -> Percent:
    return _BASE_RATE_BY_YEAR.get(year, fallback)


def _calculate_payouts(snapshot_end: PortfolioSnapshot) -> pd.Series:
    """Calculate dividend payouts for the tax year."""
    transactions = snapshot_end.securities_account_transactions
    transactions = transactions[transactions.index.get_level_values('date').year == snapshot_end.date.year] if not transactions.index.get_level_values('date').empty else transactions

    payouts = transactions.pipe(filter_by_type, transaction_types=TransactionType.DIVIDENDS).groupby(['accountId', 'securityId'])['amount'].sum()
    payouts.name = 'Payouts'

    return payouts


def _calculate_min_shares(snapshot_begin: PortfolioSnapshot, snapshot_end: PortfolioSnapshot) -> pd.Series | None:  # pylint: disable=too-many-locals
    """
    Calculate the minimum share position during the tax year for each security.
    This detects if a position went to zero (complete sell) and was then rebought.
    Returns minimum shares held at any point during the year.
    """
    transactions = snapshot_end.securities_account_transactions

    begin_shares = snapshot_begin.shares
    if begin_shares.empty:
        return None

    # Get only in-year transactions
    transactions_inyear = transactions[
        transactions.index.get_level_values('date').year == snapshot_end.date.year
    ] if not transactions.index.get_level_values('date').empty else pd.DataFrame()

    if transactions_inyear.empty:
        # No transactions during year, minimum = begin shares
        return begin_shares

    # Calculate cumulative changes during the year
    def sign_shares(row: pd.Series) -> float:
        return float(-row['shares'] if row['type'] in [t.value for t in _NEGATIVE_SECURITIES_ACCOUNT_TRANSACTION_TYPES] else row['shares'])

    # Group by account/security and calculate minimum cumulative position
    result_dict = {}

    for (account_id, security_id, currency), begin_count in begin_shares.items():
        # Get transactions for this specific account/security
        mask = (
            (transactions_inyear.index.get_level_values('accountId') == account_id) &
            (transactions_inyear.index.get_level_values('securityId') == security_id)
        )
        security_txns = transactions_inyear[mask].copy() if mask.any() else pd.DataFrame()

        if security_txns.empty:
            # No transactions, min = begin
            result_dict[(account_id, security_id, currency)] = begin_count
        else:
            # Calculate cumulative position starting from begin_count
            security_txns = security_txns.reset_index().sort_values('date')
            security_txns['signed_shares'] = security_txns.apply(sign_shares, axis=1)
            cumulative = begin_count + security_txns['signed_shares'].cumsum()
            min_position = min(begin_count, cumulative.min())
            result_dict[(account_id, security_id, currency)] = max(0, min_position)

    index = pd.MultiIndex.from_tuples(result_dict.keys(), names=['accountId', 'securityId', 'currency'])
    return pd.Series(list(result_dict.values()), index=index, name='MinShares')


def _calculate_prorata_shares_for_inyear_buys(snapshot_begin: PortfolioSnapshot, snapshot_end: PortfolioSnapshot) -> pd.Series | None:
    """Calculate pro-rata shares for securities bought during the tax year."""
    transactions = snapshot_end.securities_account_transactions

    transactions_inyear = transactions[transactions.index.get_level_values('date').year == snapshot_end.date.year] if not transactions.index.get_level_values('date').empty else None
    if transactions_inyear is None:
        return pd.Series([], name='Amount', index=pd.MultiIndex.from_tuples([], names=['accountId', 'securityId']), dtype='float64')

    # buys up to the begin snapshot date are already fully counted in the begin shares
    transactions_inyear = transactions_inyear[transactions_inyear.index.get_level_values('date') > snapshot_begin.date]

    transactions_inyear = transactions_inyear.pipe(filter_by_type, transaction_types=[TransactionType.BUY, TransactionType.DELIVERY_INBOUND])
    transactions_inyear['months_held'] = snapshot_end.date.month - transactions_inyear.index.get_level_values('date').month + 1
    transactions_inyear['shares'] = transactions_inyear['shares'] * transactions_inyear['months_held']/12

    return transactions_inyear.groupby(['accountId', 'securityId'])['shares'].sum().abs()


def _calculate_effective_shares(
        snapshot_period_begin: PortfolioSnapshot,
        snapshot_period_end: PortfolioSnapshot
) -> pd.Series:
    # respect in-year buys
    effective_begin_shares = _calculate_min_shares(snapshot_period_begin, snapshot_period_end)
    if effective_begin_shares is None or effective_begin_shares.empty:
        effective_begin_shares = snapshot_period_begin.shares.copy()

    pro_rata_shares = _calculate_prorata_shares_for_inyear_buys(snapshot_period_begin, snapshot_period_end)
    if pro_rata_shares is not None:
        effective_begin_shares = effective_begin_shares.add(pro_rata_shares, fill_value=0)

    return effective_begin_shares


# @see https://www.gesetze-im-internet.de/invstg_2018/__18.html
def calculate_vap(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
        snapshot_period_begin: PortfolioSnapshot,
        snapshot_period_end: PortfolioSnapshot,
        base_rate_percent: Percent,
        tax_rate_percent: Percent,
        exempt_rate_percent: Percent = 0.0,
        exempt_rate_attr_uuid: str | None = None
) -> DataFrame[VapResultSchema]:
    """
    Calculate detailed Vorabpauschale (VAP) for German tax purposes.

    Returns:
        DataFrame with VAP amounts per security and account.
        Returns empty DataFrame if no VAP can be calculated.

    Reference:
        https://www.gesetze-im-internet.de/invstg_2018/__18.html
    """
    base_yields_per_share = calculate_base_yield_per_share(snapshot_period_begin, snapshot_period_end, base_rate_percent)
    effective_begin_shares = _calculate_effective_shares(snapshot_period_begin, snapshot_period_end)
    base_yield = effective_begin_shares.mul(base_yields_per_share, level="securityId")
    base_yield = base_yield.groupby(['accountId', 'securityId']).sum()

    payouts = _calculate_payouts(snapshot_period_end)
    vap = base_yield.subtract(payouts, fill_value=0) if payouts is not None else base_yield
    vap = vap.clip(lower=0).fillna(0)  # replace negative values with zero

    vap = vap * tax_rate_percent / 100

    if not vap.empty:
        exempt_multiplier = get_exempt_multiplier_per_security(
            snapshot_period_end.portfolio,
            exempt_rate_percent,
            exempt_rate_attr_uuid
        )

        if not exempt_multiplier.empty:
            exempt_rate_df = exempt_multiplier.to_frame(0)
            vap = exempt_rate_df.mul(vap.to_frame(), level='securityId').iloc[:, 0]

    if not vap.empty:
        vap = vap.unstack(level='accountId')

    vap = vap.pipe(drop_empty_values)
    if vap.empty:
        return VapResultSchema.empty()

    vap = pd.merge(snapshot_period_end.portfolio.securities[['wkn', 'name', 'currency']], vap, left_index=True, right_index=True, how='right', validate='one_to_one').sort_values(by='name')

    securities_accounts = snapshot_period_end.portfolio.securities_accounts
    result = vap.rename(columns=securities_accounts['name'])

    return VapResultSchema.validate(result)


def apply_allowance_to_vap(vap_result: DataFrame[VapResultSchema], allowance: Money, tax_rate: Percent) -> DataFrame[VapResultSchema]:
    """Offset the summed Vorabpauschale tax with the annual Sparerpauschbetrag.

    The allowance is a portfolio-level deduction on the taxable income (after Teilfreistellung), so it lowers
    the total VAP tax by allowance*rate and is distributed proportionally across the per-account cells.
    """
    if allowance <= 0 or vap_result.empty:
        return vap_result

    account_columns = [col for col in vap_result.columns if col not in ['wkn', 'name', 'currency']]
    total_tax = float(vap_result[account_columns].sum().sum())
    if total_tax <= 0:
        return vap_result

    reduction = min(allowance * tax_rate / 100.0, total_tax)
    factor = (total_tax - reduction) / total_tax
    result = vap_result.copy()
    result[account_columns] = result[account_columns] * factor
    return VapResultSchema.validate(result)


def calculate_vap_by_security(
        portfolio: Portfolio,
        year: int,
        tax_rate_percent: Percent,
        default_exempt_rate_percent: Percent = 0.0,
        exempt_rate_attr_uuid: str | None = None
) -> dict[str, Money] | None:
    """
    Calculate total VAP per security, summed across all securities accounts.

    Returns:
        Dictionary mapping security IDs to total VAP amounts.
        Returns None if VAP cannot be calculated.
    """
    base_rate_percent = get_base_rate_for_year(year)
    snapshot_begin = PortfolioSnapshot(portfolio, datetime(year, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(year, 12, 31))

    vap_result = calculate_vap(
        snapshot_begin,
        snapshot_end,
        base_rate_percent,
        tax_rate_percent,
        default_exempt_rate_percent,
        exempt_rate_attr_uuid
    )

    if vap_result.empty:
        return None

    account_columns = [col for col in vap_result.columns if col not in ['wkn', 'name', 'currency']]
    if not account_columns:
        return None

    vap_result['total_vap'] = vap_result[account_columns].sum(axis=1)
    result: dict[str, Money] = {str(k): Money(v) for k, v in vap_result['total_vap'].to_dict().items()}
    return result


def calculate_base_yield_per_share(
        snapshot_period_begin: PortfolioSnapshot,
        snapshot_period_end: PortfolioSnapshot,
        base_rate_percent: Percent
) -> pd.Series:
    outcome = snapshot_period_end.latest_prices.subtract(snapshot_period_begin.latest_prices, fill_value=0).clip(lower=0).fillna(0)
    base_yield = snapshot_period_begin.latest_prices * 0.7 * max(base_rate_percent, 0) / 100

    return outcome.combine(base_yield, np.minimum)


def calculate_vap_by_account(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        portfolio: Portfolio,
        year: int,
        tax_rate_percent: Percent,
        default_exempt_rate_percent: Percent = 0.0,
        exempt_rate_attr_uuid: str | None = None
) -> dict[str, Money] | None:
    """
    Calculate total VAP liability per deposit account.

    Returns:
        Dictionary mapping deposit account IDs to total VAP liability.
        Returns None if VAP cannot be calculated (no securities, no prices, etc.).
    """
    base_rate_percent = get_base_rate_for_year(year)
    snapshot_begin = PortfolioSnapshot(portfolio, datetime(year, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(year, 12, 31))

    vap_result = calculate_vap(
        snapshot_begin,
        snapshot_end,
        base_rate_percent,
        tax_rate_percent,
        default_exempt_rate_percent,
        exempt_rate_attr_uuid
    )

    if vap_result.empty:
        return None

    account_columns = [col for col in vap_result.columns if col not in ['wkn', 'name', 'currency']]
    if not account_columns:
        return None

    vap_totals_by_securities_account = {}
    for col in account_columns:
        total = vap_result[col].sum()
        if pd.notna(total) and total > 0:
            vap_totals_by_securities_account[col] = total

    if not vap_totals_by_securities_account:
        return None

    # Map securities account names to deposit account IDs via referenceAccount
    securities_accounts = portfolio.securities_accounts
    if securities_accounts.empty or 'referenceAccount' not in securities_accounts.columns:
        log.debug('Cannot map VAP to deposit accounts: no referenceAccount field')
        return None

    name_to_deposit = securities_accounts.set_index('name')['referenceAccount'].to_dict()

    # Aggregate VAP by deposit account
    vap_by_deposit_account: dict[str, Money] = {}
    for securities_account_name, vap_amount in vap_totals_by_securities_account.items():
        deposit_account_id = name_to_deposit.get(securities_account_name)
        if deposit_account_id and pd.notna(deposit_account_id):
            deposit_account_id = str(deposit_account_id)
            vap_by_deposit_account[deposit_account_id] = Money(
                vap_by_deposit_account.get(deposit_account_id, 0) + vap_amount
            )

    return vap_by_deposit_account if vap_by_deposit_account else None


def add_account_balances(
        vap_result: DataFrame[VapResultSchema],
        portfolio: Portfolio,
        snapshot_end: PortfolioSnapshot
) -> DataFrame[VapResultSchema]:
    """
    Add reference account balance row to VAP result for display purposes.

    Returns:
        VAP result with balance row appended at the end.
    """
    securities_accounts = portfolio.securities_accounts
    if securities_accounts.empty or 'referenceAccount' not in securities_accounts:
        return vap_result

    balance_by_account = pd.merge(
        securities_accounts,
        snapshot_end.balances.groupby(['accountId']).sum(),
        left_on='referenceAccount',
        right_index=True,
        how='left',
        validate='many_to_one'
    ).set_index('name')['balance'].dropna().to_dict()

    balance_data = balance_by_account | {'name': 'Related Account Balance', 'currency': portfolio.base_currency}

    vap_result.loc[len(vap_result)] = balance_data
    return VapResultSchema.validate(vap_result)
