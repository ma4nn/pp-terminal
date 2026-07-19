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
# pylint: disable=duplicate-code

from datetime import datetime

import pandas as pd
import pytest
from pp_terminal.data.filters import filter_by_security
from pp_terminal.domain.cost_basis import SellContext, calculate_fifo_sell, apply_allowance

from pp_terminal.exceptions import InputError
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import AccountType, TransactionType


@pytest.fixture(name='share_sell_portfolio')
def provide_share_sell_portfolio() -> Portfolio:
    """Portfolio with multiple purchases for FIFO testing."""

    accounts = pd.DataFrame([
        ['Depot1', AccountType.SECURITIES.value, 'account1', False, 'EUR'],
        ['Konto1', AccountType.DEPOSIT.value, None, False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
    index=['depot1', 'account1'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Test ETF', 'IE00B4L5Y983', 'EUR'],
    ], columns=['name', 'wkn', 'currency'], index=['sec1'])
    securities.index.name = 'securityId'

    transactions = pd.DataFrame([
        [datetime(2022, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, 5000.0, 50.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],  # €100/share
        [datetime(2023, 6, 10), 'depot1', 'sec1', TransactionType.BUY.value, 7000.0, 50.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],  # €140/share
        [datetime(2024, 3, 20), 'depot1', 'sec1', TransactionType.BUY.value, 9000.0, 60.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],  # €150/share
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    prices = pd.DataFrame([
        [datetime(2024, 12, 31), 'sec1', 160.0],
    ], columns=['date', 'securityId', 'price'])
    prices = prices.set_index(['date', 'securityId'])

    portfolio = Portfolio(accounts, transactions, securities, prices)
    portfolio.base_currency = 'EUR'
    return portfolio


def test_fifo_lots_single_purchase(share_sell_portfolio: Portfolio) -> None:
    """Test FIFO when selling shares from a single purchase."""
    snapshot = PortfolioSnapshot(share_sell_portfolio, datetime(2024, 12, 31))

    transactions = snapshot.securities_account_transactions.pipe(filter_by_security, security_id='sec1')
    lots = calculate_fifo_sell(transactions, SellContext(datetime(2024, 12, 31), 160.0, 26.375), shares_to_sell=30.0)

    assert len(lots) == 1
    assert lots.iloc[0]['shares'] == pytest.approx(30.0)
    assert lots.iloc[0]['purchasePrice'] == pytest.approx(100.0)
    assert lots.iloc[0]['costBasis'] == pytest.approx(3000.0)
    assert lots.iloc[0]['capitalGain'] == pytest.approx(1800.0)  # 30 * (160 - 100)
    assert lots.iloc[0]['deemedIncome'] == pytest.approx(0.0)  # No deemed income (no CSV provided)
    assert lots.iloc[0]['taxableGain'] == pytest.approx(1800.0)  # (capitalGain - deemedIncome) * (1 - exemption)
    assert lots.iloc[0]['totalTax'] == pytest.approx(474.75)  # taxableGain * 0.26375
    assert lots.iloc[0]['netProceeds'] == pytest.approx(4325.25)  # 4800 - 474.75


def test_fifo_lots_multiple_purchases(share_sell_portfolio: Portfolio) -> None:
    """Test FIFO when selling shares across multiple purchases."""
    # Sell 120 shares: 50 from first purchase, 50 from second, 20 from third
    snapshot = PortfolioSnapshot(share_sell_portfolio, datetime(2024, 12, 31))
    transactions = snapshot.securities_account_transactions.pipe(filter_by_security, security_id='sec1')
    lots = calculate_fifo_sell(transactions, SellContext(datetime(2024, 12, 31), 160.0, 26.375), shares_to_sell=120.0)

    assert len(lots) == 3

    # First lot: 50 shares @ €100
    assert lots.iloc[0]['shares'] == pytest.approx(50.0)
    assert lots.iloc[0]['purchasePrice'] == pytest.approx(100.0)
    assert lots.iloc[0]['costBasis'] == pytest.approx(5000.0)
    assert lots.iloc[0]['capitalGain'] == pytest.approx(3000.0)  # 50 * (160 - 100)
    assert lots.iloc[0]['deemedIncome'] == pytest.approx(0.0)
    assert lots.iloc[0]['totalTax'] == pytest.approx(791.25)  # 3000 * 0.26375

    # Second lot: 50 shares @ €140
    assert lots.iloc[1]['shares'] == pytest.approx(50.0)
    assert lots.iloc[1]['purchasePrice'] == pytest.approx(140.0)
    assert lots.iloc[1]['costBasis'] == pytest.approx(7000.0)
    assert lots.iloc[1]['capitalGain'] == pytest.approx(1000.0)  # 50 * (160 - 140)
    assert lots.iloc[1]['deemedIncome'] == pytest.approx(0.0)
    assert lots.iloc[1]['totalTax'] == pytest.approx(263.75)  # 1000 * 0.26375

    # Third lot: 20 shares @ €150
    assert lots.iloc[2]['shares'] == pytest.approx(20.0)
    assert lots.iloc[2]['purchasePrice'] == pytest.approx(150.0)
    assert lots.iloc[2]['costBasis'] == pytest.approx(3000.0)
    assert lots.iloc[2]['capitalGain'] == pytest.approx(200.0)  # 20 * (160 - 150)
    assert lots.iloc[2]['deemedIncome'] == pytest.approx(0.0)
    assert lots.iloc[2]['totalTax'] == pytest.approx(52.75)  # 200 * 0.26375


def test_fifo_lots_insufficient_shares(share_sell_portfolio: Portfolio) -> None:
    """Test error when trying to sell more shares than available."""
    snapshot = PortfolioSnapshot(share_sell_portfolio, datetime(2024, 12, 31))

    transactions = snapshot.securities_account_transactions.pipe(filter_by_security, security_id='sec1')

    with pytest.raises(InputError, match="Insufficient shares"):
        calculate_fifo_sell(transactions, SellContext(datetime(2024, 12, 31), 160.0, 26.375), shares_to_sell=200.0)


def _sell_120(share_sell_portfolio: Portfolio) -> pd.DataFrame:
    snapshot = PortfolioSnapshot(share_sell_portfolio, datetime(2024, 12, 31))
    transactions = snapshot.securities_account_transactions.pipe(filter_by_security, security_id='sec1')
    # 3 lots, taxable gains 3000 + 1000 + 200 = 4200 (exemption rate 0 here)
    return calculate_fifo_sell(transactions, SellContext(datetime(2024, 12, 31), 160.0, 26.375), shares_to_sell=120.0)


def test_apply_allowance_reduces_total_tax(share_sell_portfolio: Portfolio) -> None:
    """The Sparerpauschbetrag cuts the summed taxable gain by the allowance and the tax by allowance*rate."""
    lots = _sell_120(share_sell_portfolio)

    relieved = apply_allowance(lots, allowance=1000.0, tax_rate=26.375)

    assert relieved['taxableGain'].sum() == pytest.approx(4200.0 - 1000.0)           # 4200 -> 3200
    assert relieved['totalTax'].sum() == pytest.approx(lots['totalTax'].sum() - 263.75)  # 1000 * 0.26375 saved
    assert relieved['netProceeds'].sum() == pytest.approx(lots['netProceeds'].sum() + 263.75)
    assert relieved['grossProceeds'].sum() == pytest.approx(lots['grossProceeds'].sum())  # gross untouched


def test_apply_allowance_capped_at_total_taxable(share_sell_portfolio: Portfolio) -> None:
    """An allowance larger than the taxable gain zeroes the tax, never turns it negative."""
    lots = _sell_120(share_sell_portfolio)

    relieved = apply_allowance(lots, allowance=1_000_000.0, tax_rate=26.375)

    assert relieved['taxableGain'].sum() == pytest.approx(0.0)
    assert relieved['totalTax'].sum() == pytest.approx(0.0)
    assert relieved['netProceeds'].sum() == pytest.approx(lots['grossProceeds'].sum())  # no tax -> net == gross


def test_apply_allowance_zero_is_noop(share_sell_portfolio: Portfolio) -> None:
    lots = _sell_120(share_sell_portfolio)

    relieved = apply_allowance(lots, allowance=0.0, tax_rate=26.375)

    assert relieved['totalTax'].sum() == pytest.approx(lots['totalTax'].sum())
