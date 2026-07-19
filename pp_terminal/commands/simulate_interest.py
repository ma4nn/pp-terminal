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

import logging
from datetime import datetime
from typing import Annotated, Any, cast

import numpy as np
import pandas as pd
import typer
from pandera.typing import DataFrame
from rich.console import Console

from pp_terminal.data.filters import filter_later_than, filter_by_type
from pp_terminal.utils.helper import get_last_year, footer
from pp_terminal.output.strategy import OutputStrategy
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import Percent, TransactionType, Money, InterestResultSchema
from pp_terminal.output.table_decorator import TableOptions, format_value, colorize

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)


_DAYS_PER_YEAR = 360


def calculate_interest(snapshot_begin: PortfolioSnapshot, snapshot_end: PortfolioSnapshot, interest_rate: Percent) -> DataFrame[InterestResultSchema]:
    transactions = snapshot_end.deposit_account_transactions

    df = transactions.reset_index(level='date').sort_values(by=['accountId', 'currency', 'date'])
    df = df.groupby(['accountId', 'currency', 'date']).agg({'date': 'min', 'amount': 'sum'})

    df['days_diff'] = df.groupby(['accountId', 'currency'])['date'].diff().dt.days.fillna(0).astype(int)
    df['balance'] = df.groupby(['accountId', 'currency'])['amount'].cumsum()
    df['weighted_balance'] = df['balance'] * df['days_diff']
    df['interest_rate'] = np.power(1 + interest_rate / 100 / _DAYS_PER_YEAR, df['days_diff']) - 1
    df['interest'] = df['balance'] * df['interest_rate']

    df = df.pipe(filter_later_than, target_date=snapshot_begin.date)
    df = df.groupby(['accountId', 'currency']).agg({'interest': 'sum', 'weighted_balance': 'sum', 'days_diff': 'sum'})
    df['mean_balance'] = (df['weighted_balance'] / df['days_diff']).fillna(0)

    df = df.reset_index(level='currency')

    interest_transactions = (transactions.pipe(filter_by_type, transaction_types=[TransactionType.INTEREST, TransactionType.INTEREST_CHARGE])
                             .pipe(filter_later_than, target_date=snapshot_begin.date)
                             .reset_index(level='date'))
    interest_transactions['amount'] = interest_transactions['amount'] + interest_transactions['taxes']

    actual_interest_df = interest_transactions.groupby(['accountId', 'currency'])['amount'].sum().fillna(0).reset_index(level='currency')
    actual_interest_df = actual_interest_df.rename(columns={'amount': 'actual_interest'})

    # Merge actual interest into main dataframe
    df = df.merge(actual_interest_df, left_index=True, right_index=True, how='left', suffixes=('', '_drop'))
    # Handle potential duplicate currency columns from merge
    if 'currency_drop' in df.columns:
        df = df.drop(columns=['currency_drop'])

    # Merge with account names - deposit_accounts also has currency, so we'll get currency_x and currency_y
    interest_per_account = (pd.merge(snapshot_end.portfolio.deposit_accounts, df, left_index=True, right_index=True, how="right", validate='one_to_one', suffixes=('_account', ''))
                .sort_values(by='interest'))

    # Keep the currency from df (interest calculations), drop the one from accounts
    if 'currency_account' in interest_per_account.columns:
        interest_per_account = interest_per_account.drop(columns=['currency_account'])

    result = interest_per_account[interest_per_account['interest'] > 0][['name', 'currency', 'mean_balance', 'interest', 'actual_interest']]
    return InterestResultSchema.validate(result)


def _format_value_wrapper(value: Any, index: str, row: pd.Series) -> str:
    if index == 'Actual Interest' and isinstance(value, Money):
        color = 'red' if value < row['Simulated Interest'] else 'green'
        return colorize(format_value(value, index, row), color)

    return format_value(value, index, row)


@app.command(name="interest")
def simulate_interest_rate(
        ctx: typer.Context,
        interest_rate: Annotated[Percent, typer.Option(min=0, max=100, prompt="Interest Rate (%)", prompt_required=True)],
        year: Annotated[datetime, typer.Option(formats=["%Y"], help="The year to calculate the preliminary tax for", prompt=True, default_factory=get_last_year)],
) -> None:
    """
    Simulate a given interest rate for a deposit account.
    """

    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)

    snapshot_begin = PortfolioSnapshot(portfolio, datetime(year.year, 1, 1))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(year.year, 12, 31))

    df = calculate_interest(snapshot_begin, snapshot_end, interest_rate)
    df = df.rename(columns={'mean_balance': '⌀ Balance', 'interest': 'Simulated Interest', 'actual_interest': 'Actual Interest'})
    df.insert(3, 'Interest Rate', f"{interest_rate}%")

    console.print(*output.result_table(
        df, TableOptions(title='Simulated Interest on Accounts', caption=f"for {year.strftime("%Y")}, excl. taxes", show_index=False, show_total=False, value_formatter=_format_value_wrapper)
    ))
    console.print(output.text(footer()), style="dim")
