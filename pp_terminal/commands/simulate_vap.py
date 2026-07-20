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
import logging
from typing import Any, cast

import pandas as pd
import typer
from typing_extensions import Annotated

from pp_terminal.utils.config import Config
from pp_terminal.utils.helper import get_last_year, footer
from pp_terminal.utils.options import tax_rate_callback, exempt_rate_callback, allowance_callback
from pp_terminal.output.strategy import OutputStrategy, Console
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import Percent, Money
from pp_terminal.domain.vap import calculate_vap, get_base_rate_for_year, add_account_balances, apply_allowance_to_vap
from pp_terminal.output.table_decorator import TableOptions, format_value, colorize

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)

begin = None  # pylint: disable=invalid-name


def set_begin(value: datetime | None) -> datetime | None:
    """
    Temporary store the non-empty year / datetime in a global state.
    This is necessary because typer.default_factory does not have context available to make one option dependent on the other.
    """
    global begin  # pylint: disable=global-statement

    if value is not None:
        begin = value

    return value


def _get_base_rate_percent_by_year() -> Percent | None:
    if begin is None:
        return None

    return get_base_rate_for_year(begin.year)


@app.command(name="vap")
def print_tax_table(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
        ctx: typer.Context,
        year: Annotated[datetime, typer.Option(formats=["%Y"], help="The year to calculate the preliminary tax for", prompt=True, callback=set_begin, default_factory=get_last_year)],
        base_rate: Annotated[Percent, typer.Option(help="The base rate (Basiszinssatz)", min=-100, max=100, prompt="Base Rate (%)", prompt_required=True, default_factory=_get_base_rate_percent_by_year)],
        tax_rate: Annotated[Percent, typer.Option(help="Your personal tax rate", min=0, max=100, callback=tax_rate_callback)] = None,  # type: ignore
        exempt_rate: Annotated[Percent, typer.Option(help="Default exemption rate (Teilfreistellung), can be overwritten for each security.", min=0, max=100, callback=exempt_rate_callback)] = None,  # type: ignore
        allowance: Annotated[Money, typer.Option("--allowance", help="Sparerpauschbetrag still available this year", min=0, callback=allowance_callback)] = None  # type: ignore
) -> None:
    """
    Show a detailed table with calculated German preliminary tax values ("Vorabpauschale"/VAP) for a specified year, per each security and account.
    """
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)
    config = cast(Config, ctx.obj.config)

    exempt_rate_uuid = config.tax.exemption_rate_attribute
    console.print(output.hint('You can define the exempt rate per each security individually by creating a custom security attribute of type "Percent Number" in Portfolio Performance and add it to pp-terminal configuration file.'))

    snapshot_begin = PortfolioSnapshot(portfolio, datetime(year.year, 1, 2))
    snapshot_end = PortfolioSnapshot(portfolio, datetime(year.year, 12, 31))

    result = calculate_vap(snapshot_begin, snapshot_end, base_rate, tax_rate, exempt_rate, exempt_rate_uuid)
    result = apply_allowance_to_vap(result, allowance, tax_rate)

    vap_totals = {}
    if not result.empty:
        account_columns = [col for col in result.columns if col not in ['wkn', 'name', 'currency']]
        vap_totals = result[account_columns].sum().to_dict()
        result = add_account_balances(result, portfolio, snapshot_end)

    def format_value_with_balance_check(value: Any, index: str, row: pd.Series) -> str:
        if 'name' in row.index and row['name'] == 'Related Account Balance' and isinstance(value, Money) and index in vap_totals:
            color = 'red' if value < vap_totals[index] else 'green'
            return colorize(format_value(value, index, row), color)
        return format_value(value, index, row)

    console.print(*output.result_table(
        result,
        TableOptions(
            title=f"Estimated Taxes on Vorabpauschale {year.year} (§18 InvStG)",
            show_index=False,
            footer_lines=1,
            value_formatter=format_value_with_balance_check
        )
    ))

    allowance_note = f'Applies your Sparerpauschbetrag of up to {allowance:.2f}. ' if allowance > 0 else ''
    console.print(output.warning(f'{allowance_note}This simulation assumes that all amounts are in EUR.'))
    console.print(output.text(footer()), style="dim")
