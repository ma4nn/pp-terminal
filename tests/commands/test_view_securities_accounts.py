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
from _pytest.fixtures import TopRequest
from pandas.testing import assert_frame_equal

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import AccountType, TransactionType
from pp_terminal.data.pp_portfolio_builder import PpPortfolioBuilder
from pp_terminal.commands.view_accounts import calculate_securities_accounts_sum


def test_kommer(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

    expected_df = pd.DataFrame([
        ['Kryptowährung', 72.07],
        ['Depot', 3038.80],
        ['Depot', 14031.37],
    ], columns=['name', 'balance'], index=pd.MultiIndex.from_tuples([
        ('57ede399-7ef8-4696-a874-1f425e25d1f5', 'EUR'),
        ('dc6fac85-6c6e-47f1-a968-2b5b84d90997', 'USD'),
        ('dc6fac85-6c6e-47f1-a968-2b5b84d90997', 'EUR'),
    ], names=['accountId', 'currency']))

    result = calculate_securities_accounts_sum(PortfolioSnapshot(portfolio, datetime(2024, 1, 1)))[['name', 'balance']]

    assert_frame_equal(expected_df, result.round(2), check_names=False)


def test_empty_file(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'empty.ids.xml')

    result = calculate_securities_accounts_sum(PortfolioSnapshot(portfolio))

    assert result is not None
    assert len(result) == 0
    # Check that minimum required columns are present
    assert 'name' in result.columns
    assert 'type' in result.columns
    assert 'balance' in result.columns


def _portfolio_with_empty_and_sold_out_accounts() -> Portfolio:
    accounts = pd.DataFrame([
        ['Untouched depot', AccountType.SECURITIES.value, None, False, None],
        ['Sold out depot', AccountType.SECURITIES.value, None, False, None],
        ['Retired depot', AccountType.SECURITIES.value, None, True, None],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'], index=['acc-1', 'acc-2', 'acc-3'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Test Security', 'XXX', 'ISIN123', False, 'EUR'],
    ], columns=['name', 'wkn', 'isin', 'isRetired', 'currency'], index=['sec-1'])
    securities.index.name = 'securityId'

    transactions = pd.DataFrame([
        [datetime(2020, 1, 15), 'acc-2', 'sec-1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2020, 6, 20), 'acc-2', 'sec-1', TransactionType.SELL.value, 1200.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(2020, 1, 15), 'acc-3', 'sec-1', TransactionType.BUY.value, -500.0, 5.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees']
    ).set_index(['date', 'accountId', 'securityId'])

    prices = pd.DataFrame([
        [100.0],
    ], columns=['price'], index=pd.MultiIndex.from_tuples([(pd.Timestamp('2020-01-15'), 'sec-1')], names=['date', 'securityId']))

    portfolio = Portfolio(accounts=accounts, transactions=transactions, securities=securities, prices=prices)
    portfolio.base_currency = 'EUR'

    return portfolio


def test_securities_account_without_transactions_is_listed_with_zero_value() -> None:
    result = calculate_securities_accounts_sum(PortfolioSnapshot(_portfolio_with_empty_and_sold_out_accounts()))

    assert result.loc[('acc-1', 'EUR'), 'balance'] == 0.0


def test_securities_account_with_closed_positions_only_is_listed_with_zero_value() -> None:
    result = calculate_securities_accounts_sum(PortfolioSnapshot(_portfolio_with_empty_and_sold_out_accounts()))

    assert result.loc[('acc-2', 'EUR'), 'balance'] == 0.0


def test_retired_securities_accounts_are_hidden_by_default() -> None:
    result = calculate_securities_accounts_sum(PortfolioSnapshot(_portfolio_with_empty_and_sold_out_accounts()))

    assert set(result.index.get_level_values('accountId')) == {'acc-1', 'acc-2'}


def test_retired_securities_accounts_are_listed_when_inactive_included() -> None:
    result = calculate_securities_accounts_sum(PortfolioSnapshot(_portfolio_with_empty_and_sold_out_accounts()), include_inactive=True)

    assert result.loc[('acc-3', 'EUR'), 'balance'] == 500.0
