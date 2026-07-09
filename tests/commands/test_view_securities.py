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
from unittest.mock import Mock

import pandas as pd
import pytest
from typer import Context

from pp_terminal.commands.view_securities import print_securities
from pp_terminal.output.strategy import RichOutputStrategy
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import AccountType, TransactionType


@pytest.fixture(name='securities_portfolio')
def provide_securities_portfolio() -> Portfolio:
    """Portfolio with multiple securities and transactions."""

    accounts = pd.DataFrame([
        ['Depot1', AccountType.SECURITIES.value, 'account1', False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
    index=['depot1'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['MSCI World ETF', 'IE00B4L5Y983', 'EUR'],
        ['S&P 500 ETF', 'IE00B5BMR087', 'USD'],
        ['No Holdings Security', 'IE00000000000', 'EUR'],
        ['Put DAX 18000 Dec25', 'OPT001', 'EUR'],            # short (written)
        ['Closed Position', 'FLAT01', 'EUR'],                # flat / net zero
    ], columns=['name', 'wkn', 'currency'], index=['sec1', 'sec2', 'sec3', 'opt1', 'flat1'])
    securities.index.name = 'securityId'

    transactions = pd.DataFrame([
        [datetime(2022, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, 5000.0, 50.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2023, 6, 10), 'depot1', 'sec1', TransactionType.BUY.value, 7000.0, 30.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2024, 1, 5), 'depot1', 'sec1', TransactionType.SELL.value, 2000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2023, 3, 20), 'depot1', 'sec2', TransactionType.BUY.value, 9000.0, 25.5, AccountType.SECURITIES.value, 'USD', 0.0],
        # short: written put, sold-to-open 2 contracts, no inbound -> net -2
        [datetime(2024, 3, 1), 'depot1', 'opt1', TransactionType.SELL.value, 600.0, 2.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        # flat: buy 10, sell 10 -> net 0
        [datetime(2023, 2, 1), 'depot1', 'flat1', TransactionType.BUY.value, 1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2023, 9, 1), 'depot1', 'flat1', TransactionType.SELL.value, 1100.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    prices = pd.DataFrame([
        [datetime(2024, 12, 31), 'sec1', 100.0],
        [datetime(2024, 12, 31), 'sec2', 200.0],
        [datetime(2024, 12, 31), 'sec3', 50.0],
        [datetime(2024, 12, 31), 'opt1', 30.0],
        [datetime(2024, 12, 31), 'flat1', 50.0],
    ], columns=['date', 'securityId', 'price'])
    prices = prices.set_index(['date', 'securityId'])

    portfolio = Portfolio(accounts, transactions, securities, prices)
    portfolio.base_currency = 'EUR'
    return portfolio


@pytest.fixture(name='empty_securities_portfolio')
def provide_empty_securities_portfolio() -> Portfolio:
    """Portfolio with securities but no transactions."""

    accounts = pd.DataFrame([
        ['Depot1', AccountType.SECURITIES.value, 'account1', False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
    index=['depot1'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Test Security 1', 'WKN001', 'EUR'],
        ['Test Security 2', 'WKN002', 'USD'],
    ], columns=['name', 'wkn', 'currency'], index=['sec1', 'sec2'])
    securities.index.name = 'securityId'

    transactions = pd.DataFrame(
        columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees']
    ).set_index(['date', 'accountId', 'securityId'])

    prices = pd.DataFrame(
        columns=['date', 'securityId', 'price']
    ).set_index(['date', 'securityId'])

    portfolio = Portfolio(accounts, transactions, securities, prices)
    portfolio.base_currency = 'EUR'
    return portfolio


def test_list_securities_with_shares(securities_portfolio: Portfolio, capsys: pytest.CaptureFixture[str]) -> None:
    """Test listing securities shows correct share amounts."""
    ctx = Context(Mock())
    ctx.obj = Mock()
    ctx.obj.portfolio = securities_portfolio
    ctx.obj.output = RichOutputStrategy()
    ctx.obj.config = {}

    print_securities(ctx)

    captured = capsys.readouterr()
    output = captured.out

    assert 'MSCI World ETF' in output
    assert 'S&P 500 ETF' in output
    assert 'No Holdings' in output  # May wrap across lines
    assert '70.0' in output  # sec1: 50 + 30 - 10 = 70 shares
    assert '25.5' in output  # sec2: 25.5 shares
    assert 'IE00B4L5Y983' in output
    assert 'IE00B5BMR087' in output


def test_list_securities_without_transactions(empty_securities_portfolio: Portfolio, capsys: pytest.CaptureFixture[str]) -> None:
    """Test listing securities with no transactions omits Shares column."""
    ctx = Context(Mock())
    ctx.obj = Mock()
    ctx.obj.portfolio = empty_securities_portfolio
    ctx.obj.output = RichOutputStrategy()
    ctx.obj.config = {}

    print_securities(ctx)

    captured = capsys.readouterr()
    output = captured.out

    assert 'Test Security 1' in output
    assert 'Test Security 2' in output
    assert 'shares' not in output  # Column is dropped when all values are 0


def test_list_securities_share_calculation(securities_portfolio: Portfolio) -> None:
    """Test that shares are correctly aggregated across accounts."""
    snapshot = PortfolioSnapshot(securities_portfolio, datetime(2024, 12, 31))
    shares = snapshot.shares

    assert shares is not None
    shares_by_security = shares.groupby('securityId').sum()

    assert shares_by_security.loc['sec1'] == pytest.approx(70.0)  # 50 + 30 - 10
    assert shares_by_security.loc['sec2'] == pytest.approx(25.5)
    assert 'sec3' not in shares_by_security.index  # No transactions
    assert shares_by_security.loc['opt1'] == pytest.approx(-2.0)  # short kept with sign
    assert 'flat1' not in shares_by_security.index  # net zero stays excluded


def test_list_securities_in_stock_includes_shorts(securities_portfolio: Portfolio, capsys: pytest.CaptureFixture[str]) -> None:
    """`view securities --in-stock` must list shorts and still hide flat positions."""
    ctx = Context(Mock())
    ctx.obj = Mock()
    ctx.obj.portfolio = securities_portfolio
    ctx.obj.output = RichOutputStrategy()
    ctx.obj.config = {}

    print_securities(ctx, by=datetime(2024, 12, 31), in_stock=True)

    output = capsys.readouterr().out

    assert 'MSCI World ETF' in output       # long shown
    assert 'Put DAX 18000' in output        # short shown (was hidden before the fix)
    assert 'Closed Position' not in output  # flat / net zero filtered out


def test_list_securities_sorted_by_name(securities_portfolio: Portfolio, capsys: pytest.CaptureFixture[str]) -> None:
    """Test that securities are sorted alphabetically by name."""
    ctx = Context(Mock())
    ctx.obj = Mock()
    ctx.obj.portfolio = securities_portfolio
    ctx.obj.output = RichOutputStrategy()
    ctx.obj.config = {}

    print_securities(ctx)

    captured = capsys.readouterr()
    output = captured.out

    msci_pos = output.find('MSCI World ETF')
    sp500_pos = output.find('S&P 500 ETF')
    no_holdings_pos = output.find('No Holdings')  # May be wrapped

    assert msci_pos < no_holdings_pos < sp500_pos
