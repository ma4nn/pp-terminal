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
from typing import Annotated, cast

import typer
import pandas as pd
from pp_terminal.data.filters import clean_for_display, filter_by_security, pivot_taxonomy_columns, retired_row_labels
from pp_terminal.domain.cost_basis import calculate_total_cost_basis
from pp_terminal.domain.vap import calculate_vap_by_security
from pp_terminal.output.column_utils import normalize_columns
from pp_terminal.utils.config import Config, ConfigModel, command_config
from pp_terminal.utils.helper import footer
from pp_terminal.output.strategy import OutputStrategy, Console
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.output.table_decorator import TableOptions, attribute_value_formatter, percent_attribute_columns
from pp_terminal.commands.message_column import messages_renderer
from pp_terminal.validation.engine import validate_securities

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)


class ViewSecuritiesConfig(ConfigModel):
    fields: list[str] | None = None


def prepare_securities_df(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    portfolio: Portfolio,
    config: Config,
    output: OutputStrategy,
    by: datetime,
    include_inactive: bool = False,
    in_stock: bool = False
) -> pd.DataFrame:
    securities = portfolio.securities
    snapshot = PortfolioSnapshot(portfolio, by)
    shares = snapshot.shares

    df = securities.reset_index()

    if not shares.empty:
        shares_by_security = shares.groupby('securityId').sum()
        df = df.merge(shares_by_security, left_on='securityId', right_index=True, how='left', validate='one_to_one')
        df['shares'] = df['shares'].fillna(0.0)
    else:
        df['shares'] = 0.0

    latest_prices = snapshot.latest_prices.rename('latestPrice')
    df = df.merge(latest_prices, left_on='securityId', right_index=True, how='left')

    if not include_inactive and 'isRetired' in df.columns:
        df = df[~df['isRetired']]

    if in_stock:
        df = df[df['shares'] > 0.001]

    validation_results = validate_securities(portfolio, snapshot, config)
    df['messages'] = df['securityId'].map(messages_renderer(validation_results, output))

    df['costBasis'] = df['securityId'].map(
        lambda sid: calculate_total_cost_basis(portfolio.securities_account_transactions.pipe(filter_by_security, security_id=sid))
    )

    df['marketValue'] = df['latestPrice'] * df['shares']
    df['unrealizedGains'] = df['marketValue'] - df['costBasis']

    vap_by_security = calculate_vap_by_security(
        portfolio,
        by.year,
        config.tax.rate,
        config.tax.exemption_rate,
        config.tax.exemption_rate_attribute
    )
    df['vap'] = df['securityId'].map(vap_by_security) if vap_by_security else None

    df = df.pipe(pivot_taxonomy_columns, portfolio.taxonomy_assignments, 'securityId', 'security')
    return df.pipe(clean_for_display, portfolio.security_attributes)


@app.command(name="securities")
def print_securities(  # pylint: disable=too-many-locals
    ctx: typer.Context,
    by: datetime = datetime.now(),
    inactive: Annotated[bool, typer.Option("--inactive", help="Include retired (inactive) securities")] = False,
    in_stock: bool = False,
    fields: str | None = None
) -> None:
    """Show a detailed table with all securities and their IDs."""

    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)
    config = cast(Config, ctx.obj.config)

    requested_by_user = fields is not None
    if fields is None:
        config_fields = command_config(config, ViewSecuritiesConfig).fields
        requested_by_user = bool(config_fields)
        fields = ','.join(config_fields) if config_fields else 'SecurityId,Name,Wkn,Currency,Shares,Messages'

    df = prepare_securities_df(portfolio, config, output, by, inactive, in_stock)
    retired_ids = retired_row_labels(df)

    uuid_to_name = {uuid: attr.column for uuid, attr in portfolio.security_attributes.items()}
    requested_columns = [uuid_to_name.get(col.strip(), col.strip()) for col in fields.split(',')]
    selected_columns = normalize_columns(requested_columns, list(df.columns))

    df = df[selected_columns]

    if 'isRetired' in df.columns and 'isRetired' not in fields:
        df = df.drop(columns=['isRetired'])

    df = df.sort_values(by='name') if 'name' in df.columns else df

    console.print(*output.result_table(
        df, TableOptions(
            title=f"{'All ' if inactive else 'Active '}Securities",
            caption=f"{len(df)} entries per {by.strftime("%Y-%m-%d")}",
            keep_columns=tuple(df.columns) if requested_by_user else (),
            show_index=False,
            value_formatter=attribute_value_formatter(portfolio.security_attributes),
            non_summable_columns=percent_attribute_columns(portfolio.security_attributes),
            dimmed_rows=retired_ids
        )
    ))
    console.print(output.text(footer()), style="dim")
