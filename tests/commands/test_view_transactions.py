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

import pytest

from pp_terminal.commands.view_transactions import prepare_transactions_df
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import TransactionType


def test_returns_all_transactions(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells)
    assert len(df) == 6  # 4 purchases + 2 sells


def test_filter_by_security(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells, security_id='sec-1')
    assert len(df) == 6
    assert (df['securityId'] == 'sec-1').all()


def test_filter_by_account(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells, account_id='acc-1')
    assert len(df) == 4  # 3 BUYs in acc-1 + 1 SELL in acc-1
    assert (df['accountId'] == 'acc-1').all()


def test_filter_by_date_range(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(
        portfolio_with_sells,
        from_date=datetime(2021, 1, 1),
        to_date=datetime(2022, 12, 31)
    )
    assert len(df) == 2  # 2021-03-10 DELIVERY_INBOUND + 2022-01-05 BUY
    assert all(datetime(2021, 1, 1) <= d <= datetime(2022, 12, 31) for d in df['date'])


def test_filter_by_type(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells, transaction_types=[TransactionType.BUY])
    assert len(df) == 3
    assert (df['type'] == TransactionType.BUY.name).all()


def test_filter_by_multiple_types(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells, transaction_types=[TransactionType.SELL, TransactionType.DELIVERY_OUTBOUND])
    assert len(df) == 2


def test_empty_result_for_unknown_security(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells, security_id='nonexistent')
    assert df.empty


def test_security_name_column_populated(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells)
    assert 'securityName' in df.columns
    assert (df['securityName'] == 'Test Security').all()


def test_result_columns(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells)
    expected_columns = ['date', 'securityName', 'securityId', 'accountId', 'type', 'amount', 'shares', 'currency', 'fees', 'taxes']
    assert list(df.columns) == expected_columns


def test_sorted_by_date(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells)
    assert list(df['date']) == sorted(df['date'])


def test_from_date_only(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells, from_date=datetime(2023, 1, 1))
    assert len(df) == 1  # only 2023-06-15 DELIVERY_OUTBOUND
    assert df.iloc[0]['date'] == datetime(2023, 6, 15)


def test_to_date_only(portfolio_with_sells: Portfolio) -> None:
    df = prepare_transactions_df(portfolio_with_sells, to_date=datetime(2020, 6, 20))
    assert len(df) == 2  # 2020-01-15 BUY + 2020-06-20 BUY


@pytest.mark.parametrize("security_id,account_id,expected_count", [
    ('sec-1', 'acc-1', 4),   # all acc-1 transactions
    ('sec-1', 'acc-2', 2),   # DELIVERY_INBOUND + DELIVERY_OUTBOUND in acc-2
])
def test_combined_filters(portfolio_with_sells: Portfolio, security_id: str, account_id: str, expected_count: int) -> None:
    df = prepare_transactions_df(portfolio_with_sells, security_id=security_id, account_id=account_id)
    assert len(df) == expected_count
