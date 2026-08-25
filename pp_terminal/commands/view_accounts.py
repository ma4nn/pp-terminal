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

import logging
from datetime import datetime
from typing import cast

import pandas as pd
import typer

from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.output.column_utils import normalize_columns
from pp_terminal.data.filters import clean_for_display, unstack_column_by_currency, pivot_taxonomy_columns, retired_row_labels
from pp_terminal.utils.helper import footer
from pp_terminal.output.strategy import OutputStrategy, Console
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import AccountType
from pp_terminal.output.table_decorator import TableOptions
from pp_terminal.commands.message_column import messages_renderer
from pp_terminal.validation.engine import validate_accounts
from pp_terminal.utils.config import Config, ConfigModel, command_config

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)


class ViewAccountsConfig(ConfigModel):
    fields: list[str] | None = None


def _prepare_df_for_display(
    df: pd.DataFrame,
    selected_columns_preunstack: list[str],
    snapshot: PortfolioSnapshot,
    unstack_balance: bool
) -> pd.DataFrame:
    """Prepare DataFrame for display with optional balance unstacking."""
    if unstack_balance:
        cols_before_unstack = set(df.columns)
        df = df.pipe(unstack_column_by_currency, column='balance', base_currency=snapshot.portfolio.base_currency)
        currency_cols = [col for col in df.columns if col not in cols_before_unstack]
    else:
        currency_cols = []

    df = df.reset_index()

    selected_fields = []
    for col in selected_columns_preunstack:
        if col == 'balance' and currency_cols:
            selected_fields.extend(currency_cols)
        elif col in df.columns:
            selected_fields.append(col)

    df = df[selected_fields]

    if 'accountId' in df.columns:
        df = df.set_index('accountId')

    return df


def calculate_deposit_accounts_sum(snapshot: PortfolioSnapshot) -> pd.DataFrame:
    balances = (pd.merge(snapshot.portfolio.deposit_accounts, snapshot.balances, left_index=True, right_on='accountId', how="right", validate='one_to_many')
            .sort_values(by='balance'))

    balances = balances[balances['balance'] >= 0.01]
    # Drop columns that are not useful for display
    cols_to_drop = [col for col in balances.columns if col in ['referenceAccount', 'isRetired']]
    if cols_to_drop:
        balances = balances.drop(columns=cols_to_drop)
    return balances


def calculate_securities_accounts_sum(snapshot: PortfolioSnapshot) -> pd.DataFrame:
    values = (pd.merge(snapshot.portfolio.securities_accounts, snapshot.values.groupby(['accountId', 'currency']).sum(), left_index=True, right_on='accountId', how="right", validate='one_to_many')
            .sort_values(by='balance'))

    values = values[values['balance'] >= 0.01]
    # Drop columns that are not useful for display
    cols_to_drop = [col for col in values.columns if col in ['referenceAccount', 'isRetired']]
    if cols_to_drop:
        values = values.drop(columns=cols_to_drop)
    return values


def prepare_accounts_df(
    portfolio: Portfolio,
    config: Config,
    output: OutputStrategy,
    by: datetime,
    account_type: AccountType | None = None
) -> pd.DataFrame:
    snapshot = PortfolioSnapshot(portfolio, by)

    df1 = None
    if account_type == AccountType.DEPOSIT or account_type is None:
        df1 = calculate_deposit_accounts_sum(snapshot)

    df2 = None
    if account_type == AccountType.SECURITIES or account_type is None:
        df2 = calculate_securities_accounts_sum(snapshot)

    df = pd.concat([df1, df2])

    validation_results = validate_accounts(portfolio, snapshot, config)
    account_ids = df.index.get_level_values('accountId')
    df['messages'] = account_ids.map(messages_renderer(validation_results, output))

    # currency exists both as index level (from balances) and column (from accounts); drop the column
    if 'currency' in df.columns:
        df = df.drop(columns=['currency'])

    df = df.pipe(pivot_taxonomy_columns, portfolio.taxonomy_assignments, 'accountId', 'account')
    return df.pipe(clean_for_display, portfolio.account_attributes)


@app.command(name="accounts")
def print_accounts(  # pylint: disable=too-many-locals
    ctx: typer.Context,
    type: AccountType | None = None,  # pylint: disable=redefined-builtin
    by: datetime = datetime.now(),
    fields: str | None = None
) -> None:
    """
    Show a detailed table with the current balance per deposit account.
    """

    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)
    config = cast(Config, ctx.obj.config)

    requested_by_user = fields is not None
    if fields is None:
        config_fields = command_config(config, ViewAccountsConfig).fields
        requested_by_user = bool(config_fields)
        fields = ','.join(config_fields) if config_fields else 'AccountId,Name,Type,Balance,Messages'

    df = prepare_accounts_df(portfolio, config, output, by, type)
    snapshot = PortfolioSnapshot(portfolio, by)

    uuid_to_name = {uuid: attr.column for uuid, attr in portfolio.account_attributes.items()}
    requested_columns = [uuid_to_name.get(col.strip(), col.strip()) for col in fields.split(',')]

    # Available columns before unstacking - need to account for accountId which will be from the index
    available_before_unstack = list(set(df.columns) - {'balance'}) + ['accountId']
    if 'balance' in df.columns:
        available_before_unstack.append('balance')

    selected_columns_preunstack = normalize_columns(requested_columns, available_before_unstack, portfolio.account_attributes)

    df = _prepare_df_for_display(
        df, selected_columns_preunstack, snapshot,
        unstack_balance='balance' in selected_columns_preunstack and 'balance' in df.columns
    )

    retired_ids = retired_row_labels(pd.concat([portfolio.deposit_accounts, portfolio.securities_accounts]))

    console.print(*output.result_table(
        df, TableOptions(
            title="Balances on Accounts",
            caption=f"{len(df)} entries per {by.strftime("%Y-%m-%d")}",
            keep_columns=tuple(df.columns) if requested_by_user else (),
            show_index=True,
            dimmed_rows=retired_ids
        )
    ))
    console.print(output.text(footer()), style="dim")
