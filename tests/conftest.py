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

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from _pytest.monkeypatch import MonkeyPatch
from pandera.typing import DataFrame

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import AccountType, TransactionType, TaxPaidSchema
from pp_terminal.utils import config as config_module


TAX_RATE = (0.25 + 0.055*0.25) * 100
EXEMPT_RATE_CONFIG = {
    "attributes": {
        "securities": {
            "exempt-rate": "2baac2d0-459b-4b41-a0ef-d7dad0866892"
        }
    }
}


@pytest.fixture(autouse=True)
def _reset_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config_module, '_loaded_config', {})
    # point XDG at an empty dir so a developer's real ~/.config/pp-terminal/config.toml
    # can never leak into tests that don't pass --config
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr('ppxml2db.dbhelper.db', sqlite3.connect(':memory:'))


@pytest.fixture(name='sample_accounts')
def provide_sample_accounts() -> pd.DataFrame:
    securities_accounts = pd.DataFrame([
        ['Testdepot', AccountType.SECURITIES.value, 'EUR'],
        ['Testkonto', AccountType.DEPOSIT.value, 'EUR'],
    ], columns=['name', 'type', 'currency'], index=['1', '2'])
    securities_accounts.index.name = 'accountId'

    return securities_accounts


@pytest.fixture(name='sample_transactions')
def provide_sample_transactions() -> pd.DataFrame:
    return (pd.DataFrame([
            [datetime(2018, 8, 15), TransactionType.BUY.value, 1000.0, 5.0, '1234567890', '1', AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
            [datetime(2018, 1, 30), TransactionType.TRANSFER_IN.value, 100000.0, 0, None, '2', AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'type', 'amount', 'shares', 'securityId', 'accountId', 'accountType', 'currency', 'taxes', 'fees'])
            .set_index(['date', 'accountId', 'securityId']))


@pytest.fixture(name='portfolio_with_purchases')
def provide_portfolio_with_purchases() -> Portfolio:
    """Portfolio with multiple purchases across two accounts."""
    accounts = pd.DataFrame([
        ['Account 1', AccountType.SECURITIES.value, None, False, 'EUR'],
        ['Account 2', AccountType.SECURITIES.value, None, False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
       index=['acc-1', 'acc-2'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Test Security', 'XXX', 'ISIN123', None, False, 'EUR'],
    ], columns=['name', 'wkn', 'isin', 'note', 'isRetired', 'currency'],
       index=['sec-1'])
    securities.index.name = 'securityId'

    # Purchases: BUY amounts are negative (cash outflow)
    transactions = pd.DataFrame([
        [datetime(2020, 1, 15), 'acc-1', 'sec-1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],  # 10 shares @ 100
        [datetime(2020, 6, 20), 'acc-1', 'sec-1', TransactionType.BUY.value, -1500.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],  # 10 shares @ 150
        [datetime(2021, 3, 10), 'acc-2', 'sec-1', TransactionType.DELIVERY_INBOUND.value, 0.0, 5.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],  # 5 shares @ 0 (gift)
        [datetime(2022, 1, 5), 'acc-1', 'sec-1', TransactionType.BUY.value, -2000.0, 20.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],  # 20 shares @ 100
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    return Portfolio(
        accounts=accounts,
        transactions=transactions,
        securities=securities,
        prices=None
    )


@pytest.fixture(name='portfolio_with_sells')
def provide_portfolio_with_sells(portfolio_with_purchases: Portfolio) -> Portfolio:
    """Portfolio with purchases and sales."""
    if not isinstance(portfolio_with_purchases.securities_account_transactions, pd.DataFrame):
        raise TypeError("transactions must be a DataFrame")

    transactions = portfolio_with_purchases.securities_account_transactions.copy()

    # Add sales: SELL amounts are positive (cash inflow)
    sales = pd.DataFrame([
        [datetime(2020, 12, 1), 'acc-1', 'sec-1', TransactionType.SELL.value, 1400.0, 7.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],  # Sell 7 shares
        [datetime(2023, 6, 15), 'acc-2', 'sec-1', TransactionType.DELIVERY_OUTBOUND.value, 0.0, 3.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],  # Transfer out 3 shares
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    sales = sales.set_index(['date', 'accountId', 'securityId'])

    transactions = pd.concat([transactions, sales])

    return Portfolio(
        accounts=portfolio_with_purchases.securities_accounts,
        transactions=transactions,
        securities=portfolio_with_purchases.securities,
        prices=None
    )


@pytest.fixture(name='tax_csv_data')
def provide_tax_csv_data() -> DataFrame[TaxPaidSchema]:
    """Tax CSV data with deemed income base per share."""
    data = DataFrame[TaxPaidSchema]([
        [0.189573],
        [0.227488],
        [0.265403],
    ], columns=['deemed_income'],
        index=pd.MultiIndex.from_arrays(
            [[2020, 2021, 2022], ['sec-1', 'sec-1', 'sec-1']],
            names=['year', 'security_id']
        ))

    return data
