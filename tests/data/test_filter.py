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

import pandas as pd
import pytest

from pp_terminal.data.filters import (
    clean_for_display,
    drop_empty_values,
    filter_by_account,
    filter_by_account_and_security,
    filter_by_security,
    filter_by_type,
    filter_earlier_than,
    filter_later_than,
    filter_not_retired,
    retired_row_labels,
    unstack_column_by_currency,
)
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import Attribute, TransactionType


def test_retired_row_labels_returns_retired_indices() -> None:
    df = pd.DataFrame({'isRetired': [False, True, None, True]}, index=['a', 'b', 'c', 'd'])

    assert retired_row_labels(df) == {'b', 'd'}


def test_retired_row_labels_empty_without_column() -> None:
    df = pd.DataFrame({'name': ['x', 'y']}, index=['a', 'b'])

    assert retired_row_labels(df) == set()


class TestIndexFilters:
    def test_filter_by_security_returns_matching_rows(self, portfolio_with_purchases: Portfolio) -> None:
        result = filter_by_security(portfolio_with_purchases.securities_account_transactions, 'sec-1')

        assert len(result) == 4
        assert set(result.index.get_level_values('securityId')) == {'sec-1'}

    def test_filter_by_security_nonexistent_is_empty(self, portfolio_with_purchases: Portfolio) -> None:
        result = filter_by_security(portfolio_with_purchases.securities_account_transactions, 'nope')

        assert result.empty

    def test_filter_by_account_returns_only_that_account(self, portfolio_with_purchases: Portfolio) -> None:
        result = filter_by_account(portfolio_with_purchases.securities_account_transactions, 'acc-1')

        assert len(result) == 3
        assert set(result.index.get_level_values('accountId')) == {'acc-1'}

    def test_filter_by_account_and_security_returns_intersection(self, portfolio_with_purchases: Portfolio) -> None:
        result = filter_by_account_and_security(
            portfolio_with_purchases.securities_account_transactions, account_id='acc-1', security_id='sec-1')

        assert len(result) == 3
        assert set(result.index.get_level_values('accountId')) == {'acc-1'}
        assert set(result.index.get_level_values('securityId')) == {'sec-1'}


class TestDateFilters:
    @pytest.fixture
    def dated(self) -> pd.DataFrame:
        dates = [datetime(2020, 1, 1), datetime(2020, 6, 1), datetime(2021, 1, 1)]
        return pd.DataFrame({'value': [1, 2, 3]}, index=pd.Index(dates, name='date'))

    def test_filter_earlier_than_includes_boundary(self, dated: pd.DataFrame) -> None:
        result = filter_earlier_than(dated, datetime(2020, 6, 1))

        assert list(result['value']) == [1, 2]

    def test_filter_later_than_includes_boundary(self, dated: pd.DataFrame) -> None:
        result = filter_later_than(dated, datetime(2020, 6, 1))

        assert list(result['value']) == [2, 3]


class TestFilterByType:
    @pytest.fixture
    def transactions(self) -> pd.DataFrame:
        return pd.DataFrame({
            'type': [TransactionType.BUY.name, TransactionType.SELL.name, TransactionType.DIVIDENDS.name],
            'amount': [-100.0, 200.0, 5.0],
        })

    def test_single_type_is_normalized_to_list(self, transactions: pd.DataFrame) -> None:
        result = filter_by_type(transactions, TransactionType.BUY)

        assert list(result['type']) == [TransactionType.BUY.name]

    def test_list_of_types_matches_any(self, transactions: pd.DataFrame) -> None:
        result = filter_by_type(transactions, [TransactionType.BUY, TransactionType.SELL])

        assert set(result['type']) == {TransactionType.BUY.name, TransactionType.SELL.name}

    def test_no_match_returns_empty(self, transactions: pd.DataFrame) -> None:
        result = filter_by_type(transactions, TransactionType.TRANSFER_IN)

        assert result.empty


class TestFilterNotRetired:
    def test_drops_retired_rows(self) -> None:
        df = pd.DataFrame({'name': ['a', 'b', 'c'], 'isRetired': [False, True, False]})

        result = filter_not_retired(df)

        assert list(result['name']) == ['a', 'c']

    def test_without_column_returns_unchanged(self) -> None:
        df = pd.DataFrame({'name': ['a', 'b']})

        pd.testing.assert_frame_equal(filter_not_retired(df), df)


class TestDropEmptyValues:
    def test_empty_input_returned_as_is(self) -> None:
        assert drop_empty_values(pd.DataFrame()).empty

    def test_dataframe_drops_all_zero_row_and_column(self) -> None:
        df = pd.DataFrame({'a': [1.0, 0.0], 'b': [0.0, 0.0]}, index=['x', 'y'])

        result = drop_empty_values(df)

        assert list(result.index) == ['x']
        assert list(result.columns) == ['a']
        assert result.loc['x', 'a'] == pytest.approx(1.0)

    def test_numeric_series_drops_nan_and_zero(self) -> None:
        series = pd.Series([1.0, 0.0, None, 2.0], index=['a', 'b', 'c', 'd'])

        result = drop_empty_values(series)

        assert list(result.index) == ['a', 'd']
        assert list(result) == pytest.approx([1.0, 2.0])

    def test_non_numeric_series_drops_only_nan(self) -> None:
        series = pd.Series(['x', None, 'y', ''], index=['a', 'b', 'c', 'd'])

        result = drop_empty_values(series)

        assert list(result.index) == ['a', 'c', 'd']
        assert list(result) == ['x', 'y', '']


class TestCleanForDisplay:
    def test_drops_underscore_columns_and_renames_attributes(self) -> None:
        df = pd.DataFrame({'name': ['A'], '_internal': [1], 'uuid-123': [42]})
        attributes = {'uuid-123': Attribute(uuid='uuid-123', name='Exempt Rate', converter='x')}

        result = clean_for_display(df, attributes)

        assert list(result.columns) == ['name', 'Exempt Rate']
        assert list(result['Exempt Rate']) == [42]

    def test_no_attributes_only_drops_underscore_columns(self) -> None:
        df = pd.DataFrame({'name': ['A'], '_hidden': [1]})

        result = clean_for_display(df, {})

        assert list(result.columns) == ['name']


class TestUnstackColumnByCurrency:
    @pytest.fixture
    def balances(self) -> pd.DataFrame:
        index = pd.MultiIndex.from_tuples(
            [('acc-1', 'EUR'), ('acc-1', 'USD'), ('acc-2', 'EUR')],
            names=['accountId', 'currency'])
        return pd.DataFrame({'name': ['A', 'A', 'B'], 'balance': [300.0, 50.0, 100.0]}, index=index)

    def test_creates_one_column_per_currency(self, balances: pd.DataFrame) -> None:
        result = unstack_column_by_currency(balances, column='balance', base_currency='EUR')

        assert 'EUR' in result.columns
        assert 'USD' in result.columns
        assert 'balance' not in result.columns
        assert result.index.name == 'accountId'
        assert 'currency' not in (result.index.names or [])

    def test_collapses_account_to_single_row_sorted_by_base_currency(self, balances: pd.DataFrame) -> None:
        result = unstack_column_by_currency(balances, column='balance', base_currency='EUR')

        assert list(result.index) == ['acc-2', 'acc-1']
        assert result.loc['acc-1', 'EUR'] == pytest.approx(300.0)
        assert result.loc['acc-1', 'USD'] == pytest.approx(50.0)
        assert pd.isna(result.loc['acc-2', 'USD'])
