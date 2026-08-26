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
from pp_terminal.commands.view_accounts import calculate_deposit_accounts_sum


def test_calculate_sum(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

    expected_df = pd.DataFrame([
        ['Fremdwährungskonto USD', 324.0],
        ['Tagesgeld', 500.0],
        ['Wertpapierkonto', 593.87],
        ['Fremdwährungskonto GBP', 2000.0],
    ], columns=['name', 'balance'], index=pd.MultiIndex.from_tuples([
        ('789294db-0aa4-4673-9d91-ad083c9d6916', 'USD'),
        ('ea9414e0-1787-46c0-92b3-8e2370eb892e', 'EUR'),
        ('e068fb14-2554-427e-b2d0-30dcc6e15717', 'EUR'),
        ('db94317b-26ed-4a8b-bf6c-2f535a217138', 'GBP')
    ], names=['accountId', 'currency']))

    result = calculate_deposit_accounts_sum(PortfolioSnapshot(portfolio))[['name', 'balance']]

    assert result is not None
    assert_frame_equal(expected_df, result)


def test_empty_file(request: TopRequest) -> None:
    portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'empty.ids.xml')

    result = calculate_deposit_accounts_sum(PortfolioSnapshot(portfolio))

    assert result is not None
    assert len(result) == 0
    # Check that minimum required columns are present
    assert 'name' in result.columns
    assert 'type' in result.columns
    assert 'balance' in result.columns


def _portfolio_with_empty_and_zeroed_accounts() -> Portfolio:
    accounts = pd.DataFrame([
        ['Untouched', AccountType.DEPOSIT.value, None, False, 'EUR'],
        ['Emptied', AccountType.DEPOSIT.value, None, False, 'EUR'],
        ['Overdrawn', AccountType.DEPOSIT.value, None, False, 'EUR'],
        ['Retired with balance', AccountType.DEPOSIT.value, None, True, 'EUR'],
        ['Retired without transactions', AccountType.DEPOSIT.value, None, True, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'], index=['acc-1', 'acc-2', 'acc-3', 'acc-4', 'acc-5'])
    accounts.index.name = 'accountId'

    transactions = pd.DataFrame([
        [datetime(2020, 1, 1), 'acc-2', None, TransactionType.DEPOSIT.value, 100.0, 0.0, AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
        [datetime(2020, 2, 1), 'acc-2', None, TransactionType.REMOVAL.value, -100.0, 0.0, AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
        [datetime(2020, 2, 1), 'acc-3', None, TransactionType.REMOVAL.value, -50.0, 0.0, AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
        [datetime(2020, 3, 1), 'acc-4', None, TransactionType.DEPOSIT.value, 25.0, 0.0, AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees']
    ).set_index(['date', 'accountId', 'securityId'])

    portfolio = Portfolio(accounts=accounts, transactions=transactions)
    portfolio.base_currency = 'EUR'

    return portfolio


def test_account_without_transactions_is_listed_with_zero_balance() -> None:
    result = calculate_deposit_accounts_sum(PortfolioSnapshot(_portfolio_with_empty_and_zeroed_accounts()))

    assert result.loc[('acc-1', 'EUR'), 'balance'] == 0.0


def test_account_with_transactions_summing_up_to_zero_is_listed() -> None:
    result = calculate_deposit_accounts_sum(PortfolioSnapshot(_portfolio_with_empty_and_zeroed_accounts()))

    assert result.loc[('acc-2', 'EUR'), 'balance'] == 0.0


def test_overdrawn_account_is_listed_with_negative_balance() -> None:
    result = calculate_deposit_accounts_sum(PortfolioSnapshot(_portfolio_with_empty_and_zeroed_accounts()))

    assert result.loc[('acc-3', 'EUR'), 'balance'] == -50.0


def test_retired_accounts_are_hidden_by_default() -> None:
    result = calculate_deposit_accounts_sum(PortfolioSnapshot(_portfolio_with_empty_and_zeroed_accounts()))

    assert set(result.index.get_level_values('accountId')) == {'acc-1', 'acc-2', 'acc-3'}


def test_retired_accounts_are_listed_when_inactive_included() -> None:
    result = calculate_deposit_accounts_sum(PortfolioSnapshot(_portfolio_with_empty_and_zeroed_accounts()), include_inactive=True)

    assert result.loc[('acc-4', 'EUR'), 'balance'] == 25.0
    assert ('acc-5', 'EUR') in result.index
