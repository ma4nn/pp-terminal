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
from pandera.typing import DataFrame

from pp_terminal.data.filters import filter_earlier_than, filter_by_type
from pp_terminal.utils.helper import enum_list_to_values
from .portfolio import Portfolio
from .schemas import TransactionType, TransactionSchema


_NEGATIVE_SECURITIES_ACCOUNT_TRANSACTION_TYPES = [
    TransactionType.SELL,
    TransactionType.DELIVERY_OUTBOUND,
    TransactionType.TRANSFER_OUT,
]

class PortfolioSnapshot:
    _per_date: datetime
    _portfolio: Portfolio

    def __init__(self, portfolio: Portfolio, per_date: datetime = datetime.now()):
        self._portfolio = portfolio
        self._per_date = per_date

    @property
    def date(self) -> datetime:
        return self._per_date

    @property
    def portfolio(self) -> Portfolio:
        return self._portfolio

    @property
    def prices(self) -> pd.DataFrame:
        return self._portfolio.prices.pipe(filter_earlier_than, target_date=self._per_date).sort_index(level='date', ascending=False)

    @property
    def latest_prices(self) -> pd.Series:
        prices = self.prices.groupby('securityId').head(1).reset_index('date')['price']
        prices.name = 'prices'

        return prices

    @property
    def securities_account_transactions(self) -> DataFrame[TransactionSchema]:
        transactions = self._portfolio.securities_account_transactions

        return TransactionSchema.validate(transactions.pipe(filter_earlier_than, target_date=self._per_date))

    @property
    def deposit_account_transactions(self) -> DataFrame[TransactionSchema]:
        transactions = self.portfolio.deposit_account_transactions

        return TransactionSchema.validate(transactions.pipe(filter_earlier_than, target_date=self._per_date))

    @property
    def shares(self) -> pd.Series:
        transactions = self.securities_account_transactions
        transactions['shares'] = transactions.apply(
            lambda row: -1 if row['type'] in enum_list_to_values(_NEGATIVE_SECURITIES_ACCOUNT_TRANSACTION_TYPES) else 1,
            axis=1
        ) * transactions['shares']

        shares = transactions.pipe(filter_by_type, transaction_types=[
            TransactionType.BUY,
            TransactionType.SELL,
            TransactionType.TRANSFER_IN,
            TransactionType.TRANSFER_OUT,
            TransactionType.DELIVERY_INBOUND,
            TransactionType.DELIVERY_OUTBOUND
        ]).groupby(['accountId', 'securityId', 'currency'])['shares'].sum()
        shares.name = 'shares'

        return shares[shares != 0]

    @property
    def values(self) -> pd.Series:
        shares = self.shares
        if shares is None or shares.empty or self.latest_prices.empty:
            return pd.Series([], name='balance', index=pd.MultiIndex.from_tuples([], names=['accountId', 'securityId', 'currency']), dtype='float64')

        values = self.latest_prices * shares
        values.name = 'balance'

        return values.groupby(['accountId', 'securityId', 'currency']).sum()

    @property
    def balances(self) -> pd.Series:
        transactions = self.deposit_account_transactions

        balances = transactions.groupby(['accountId', 'currency'])['amount'].sum()
        balances.name = 'balance'

        return balances
