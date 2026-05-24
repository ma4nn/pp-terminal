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
from typing import cast

import pandas as pd
import typer
from typing_extensions import Annotated

from pp_terminal.data.filters import (
    filter_by_account, filter_by_security, filter_by_type,
    filter_earlier_than, filter_later_than,
)
from pp_terminal.domain.portfolio import Portfolio, get_security_by_id
from pp_terminal.domain.schemas import TransactionType
from pp_terminal.exceptions import InputError
from pp_terminal.output.strategy import OutputStrategy, Console
from pp_terminal.output.table_decorator import TableOptions
from pp_terminal.utils.helper import footer

app = typer.Typer()
console = Console()

_RESULT_COLUMNS = ['date', 'securityName', 'securityId', 'accountId', 'type', 'amount', 'shares', 'currency', 'fees', 'taxes']


def prepare_transactions_df(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        portfolio: Portfolio,
        security_id: str | None = None,
        account_id: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        transaction_types: list[TransactionType] | None = None,
) -> pd.DataFrame:
    df = portfolio.securities_account_transactions

    if security_id:
        df = df.pipe(filter_by_security, security_id=security_id)
    if account_id:
        df = df.pipe(filter_by_account, account_id=account_id)
    if from_date:
        df = df.pipe(filter_later_than, target_date=from_date)
    if to_date:
        df = df.pipe(filter_earlier_than, target_date=to_date)
    if transaction_types:
        df = df.pipe(filter_by_type, transaction_types=transaction_types)

    if df.empty:
        return pd.DataFrame(columns=_RESULT_COLUMNS)

    df = df.reset_index().sort_values('date')
    df['securityName'] = df['securityId'].map(
        lambda sid: get_security_by_id(portfolio, sid).name if sid and sid in portfolio.securities.index else ''
    )

    return df[_RESULT_COLUMNS]


def _parse_types(types: list[str] | None) -> list[TransactionType] | None:
    if not types:
        return None
    result = []
    for t in types:
        try:
            result.append(TransactionType[t.upper()])
        except KeyError as e:
            valid = [tt.name for tt in TransactionType]
            raise InputError(f"Unknown transaction type '{t}'. Valid types: {', '.join(valid)}") from e
    return result


@app.command(name="transactions")
def view_transactions(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        ctx: typer.Context,
        security: Annotated[str | None, typer.Argument(help="Security ISIN or ID (defaults to all)")] = None,
        account_id: Annotated[str | None, typer.Option("--account-id", "-a", help="Account ID filter")] = None,
        from_date: Annotated[datetime | None, typer.Option("--from", formats=["%Y-%m-%d"], help="Start date (inclusive)")] = None,
        to_date: Annotated[datetime | None, typer.Option("--to", formats=["%Y-%m-%d"], help="End date (inclusive)")] = None,
        types: Annotated[list[str] | None, typer.Option("--type", help="Transaction type filter (e.g. BUY, SELL, DIVIDENDS)")] = None,
) -> None:
    """Show transactions with optional filters by security, account, date range, or type."""
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)

    security_id = None
    if security:
        isin_matches = portfolio.securities.index[portfolio.securities['isin'] == security]
        if len(isin_matches) == 1:
            security_id = str(isin_matches[0])
        elif security in portfolio.securities.index:
            security_id = security
        else:
            raise typer.BadParameter(f"Security '{security}' not found by ID or ISIN")

    transaction_types = _parse_types(types)

    df = prepare_transactions_df(portfolio, security_id, account_id, from_date, to_date, transaction_types)

    if df.empty:
        console.print(output.empty_result())
        return

    console.print(*output.result_table(
        df,
        TableOptions(title="Transactions", show_index=False, show_total=False)
    ))
    console.print(output.text(footer()), style="dim")
