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

import pandas as pd

from pp_terminal.data.filters import filter_by_account_and_security, retired_row_labels
from pp_terminal.domain.portfolio import Portfolio


def test_retired_row_labels_returns_retired_indices() -> None:
    df = pd.DataFrame({'isRetired': [False, True, None, True]}, index=['a', 'b', 'c', 'd'])

    assert retired_row_labels(df) == {'b', 'd'}


def test_retired_row_labels_empty_without_column() -> None:
    df = pd.DataFrame({'name': ['x', 'y']}, index=['a', 'b'])

    assert retired_row_labels(df) == set()


def test_nonexistent_security(portfolio_with_purchases: Portfolio) -> None:
    transactions = portfolio_with_purchases.securities_account_transactions.pipe(
        filter_by_account_and_security, account_id='acc-1', security_id='nonexistent-security')

    assert transactions.empty

def test_nonexistent_account(portfolio_with_purchases: Portfolio) -> None:
    transactions = portfolio_with_purchases.securities_account_transactions.pipe(
        filter_by_account_and_security, account_id='nonexistent-account', security_id='sec-1')

    assert transactions.empty
