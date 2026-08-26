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
from typing import Annotated, cast

import pandas as pd
import typer
from pandera.typing import DataFrame

from pp_terminal.data.filters import filter_by_account, filter_by_type
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import CashFlowResultSchema, TransactionType
from pp_terminal.exceptions import InputError
from pp_terminal.output.strategy import Console, OutputStrategy
from pp_terminal.output.table_decorator import TableOptions
from pp_terminal.utils.helper import footer

app = typer.Typer()
console = Console()


def resolve_deposit_account(portfolio: Portfolio, account: str) -> str:
    accounts = portfolio.deposit_accounts
    if account in accounts.index:
        return account

    name_matches = accounts.index[accounts['name'] == account]
    if len(name_matches) == 1:
        return str(name_matches[0])
    if len(name_matches) > 1:
        raise InputError(f"Name '{account}' matches multiple deposit accounts: {list(name_matches)}")

    raise InputError(f"Deposit account '{account}' not found by ID or name. Available: {list(accounts['name'])}")


def prepare_cash_flows_df(
        portfolio: Portfolio,
        by: datetime,
        account_id: str | None = None,
        include_transfers: bool = False,
) -> DataFrame[CashFlowResultSchema]:
    """Aggregates external cash flows per currency, from the beginning of the file through `by`."""
    transactions = PortfolioSnapshot(portfolio, by).deposit_account_transactions
    if account_id is not None:
        transactions = transactions.pipe(filter_by_account, account_id=account_id)

    deposit_types = [TransactionType.DEPOSIT]
    withdrawal_types = [TransactionType.REMOVAL]
    if include_transfers:
        deposit_types.append(TransactionType.TRANSFER_IN)
        withdrawal_types.append(TransactionType.TRANSFER_OUT)

    deposits = transactions.pipe(filter_by_type, transaction_types=deposit_types)
    withdrawals = transactions.pipe(filter_by_type, transaction_types=withdrawal_types)

    deposits_by_currency = deposits.groupby('currency')['amount'].sum()
    # withdrawals are stored as negative amounts, report them as a positive figure
    withdrawals_by_currency = -withdrawals.groupby('currency')['amount'].sum()
    currencies = deposits_by_currency.index.union(withdrawals_by_currency.index)

    result = pd.DataFrame(index=currencies)
    result['totalDeposits'] = deposits_by_currency
    result['totalWithdrawals'] = withdrawals_by_currency
    result = result.fillna(0.0)
    result['netContributions'] = result['totalDeposits'] - result['totalWithdrawals']
    result['transactionCount'] = (pd.concat([deposits, withdrawals])
                                  .groupby('currency').size()
                                  .reindex(currencies, fill_value=0))

    return CashFlowResultSchema.validate(result.reset_index(names='currency'))


@app.command(name="cash-flows")
def print_cash_flows(
        ctx: typer.Context,
        account: Annotated[str | None, typer.Option(help="Restrict to one deposit account, given as its ID or name")] = None,
        by: datetime = datetime.now(),
        include_transfers: Annotated[bool, typer.Option(help="Include transfers between your own deposit accounts")] = False,
) -> None:
    """
    Show cumulative deposits, withdrawals and net contributions per currency.
    """

    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)

    account_id = resolve_deposit_account(portfolio, account) if account else None
    df = prepare_cash_flows_df(portfolio, by, account_id, include_transfers)

    console.print(*output.result_table(
        df, TableOptions(
            title="Cash Flows",
            caption=f"per {by.strftime("%Y-%m-%d")}, {'incl.' if include_transfers else 'excl.'} internal transfers",
            show_index=False,
            show_total=False  # summing across currencies would be meaningless
        )
    ))
    console.print(output.text(footer()), style="dim")
