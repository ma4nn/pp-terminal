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
from pathlib import Path
from typing import cast
from typing_extensions import Annotated

import pandas as pd
import typer
from pandera.typing import DataFrame

from pp_terminal.data.filters import filter_by_account_and_security
from pp_terminal.data.tax import load_prepaid_tax_data
from pp_terminal.domain.cost_basis import SellContext, enrich_fifo_lots
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import Money, Percent, TaxPaidSchema
from pp_terminal.exceptions import InputError
from pp_terminal.output.strategy import OutputStrategy, Console
from pp_terminal.output.table_decorator import TableOptions
from pp_terminal.utils.config import Config, get_allowance, get_exempt_rate, get_tax_files
from pp_terminal.utils.helper import footer
from pp_terminal.utils.options import tax_rate_callback, tax_csv_callback, allowance_callback

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)

_RESULT_COLUMNS = ['assumedReturn', 'grossPerYear', 'netPerYear', 'netPerMonth', 'netRate']


def amortization_factor(rate: float, years: int) -> float:
    """Annuity-due factor: the capital fraction to withdraw at the start of each year so it hits zero after `years` years at return `rate`."""
    if rate == 0:
        return 1 / years
    return rate / (1 - (1 + rate) ** -years) / (1 + rate)


def _taxable_position(
        snapshot: PortfolioSnapshot,
        tax_rate: Percent,
        exempt_rate: Percent,
        tax_csv_data: DataFrame[TaxPaidSchema] | None
) -> tuple[Money, Money]:
    """Market value of all held securities and their taxable gain if sold today at the current FIFO frontier."""
    holdings = snapshot.shares
    if holdings.empty:
        return Money(0), Money(0)

    latest_prices = snapshot.latest_prices
    missing_prices = [sid for sid in holdings.index.get_level_values('securityId').unique() if sid not in latest_prices.index]
    if missing_prices:
        raise InputError(f"No price data for: {', '.join(missing_prices)}")

    market_value, taxable_gain = 0.0, 0.0
    for (account_id, security_id, _currency), _shares in holdings.items():
        transactions = snapshot.securities_account_transactions.pipe(
            filter_by_account_and_security, security_id=security_id, account_id=account_id
        )
        lots = enrich_fifo_lots(transactions, SellContext(snapshot.date, latest_prices.loc[security_id], tax_rate, exempt_rate, tax_csv_data))
        market_value += lots['grossProceeds'].sum()
        taxable_gain += lots['taxableGain'].sum()

    return Money(market_value), Money(taxable_gain)


def prepare_pmt_result(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        portfolio: Portfolio,
        config: Config,
        date: datetime,
        tax_rate: Percent,
        assumed_return: Percent,
        years: int,
        tax_csv_data: DataFrame[TaxPaidSchema] | None = None,
        allowance: Money | None = None,
) -> pd.DataFrame:
    effective_allowance = allowance if allowance is not None else get_allowance(config)

    snapshot = PortfolioSnapshot(portfolio, date)
    market_value, taxable_gain = _taxable_position(snapshot, tax_rate, get_exempt_rate(config), tax_csv_data)
    cash = Money(snapshot.balances.sum())

    if market_value == 0 and cash == 0:
        return pd.DataFrame()

    start_capital = market_value + cash
    if start_capital <= 0:
        raise InputError("Negative cash balances cancel out the securities value, nothing left to withdraw")

    gross = start_capital * amortization_factor(assumed_return / 100, years)
    gain_per_euro = taxable_gain / start_capital
    tax = max(gross * gain_per_euro - effective_allowance, 0) * tax_rate / 100
    net = gross - tax

    return pd.DataFrame([{
        'assumedReturn': Percent(assumed_return),
        'grossPerYear': Money(gross),
        'netPerYear': Money(net),
        'netPerMonth': Money(net / 12),
        'netRate': Percent(net / start_capital * 100),
    }])[_RESULT_COLUMNS]


def _next_step_hint(net: Money, cash: Money) -> str:
    hint = (f'Run [cyan]simulate share-sell --target-net {net:.2f} --summary[/cyan] '
            'to turn this year\'s net amount into a concrete sell plan per security.')
    if cash > 0:
        hint += f' If you draw on your cash balance of {cash:.2f} instead, lower --target-net accordingly.'
    return hint


@app.command(name="pmt")
def simulate_pmt(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        ctx: typer.Context,
        assumed_return: Annotated[Percent, typer.Option("--return", min=0, max=100, prompt="Assumed Annual Real Return (%)", prompt_required=True, help="Assumed annual real return in percent")],
        years: Annotated[int, typer.Option(min=1, prompt="Horizon (Years)", prompt_required=True, help="Number of years the capital has to last")],
        date: Annotated[datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Snapshot date (defaults to today)")] = None,
        tax_rate: Annotated[Percent, typer.Option(help="Your personal tax rate", min=0, max=100, callback=tax_rate_callback)] = None,  # type: ignore
        allowance: Annotated[Money, typer.Option("--allowance", help="Sparerpauschbetrag still available this year; defaults to config (1000 EUR, use 2000 for joint assessment)", min=0, callback=allowance_callback)] = None,  # type: ignore
        tax_csv: Annotated[Path | None, typer.Option(help="CSV file with paid tax per share data", callback=tax_csv_callback)] = None
) -> None:
    """
    Simulate an amortization withdrawal (annuity, "VPW"): the net amount to spend this year so that
    the portfolio reaches zero after the given horizon at the assumed real return. Recompute yearly.
    """
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)
    config = cast(Config, ctx.obj.config)

    if date is None:
        date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    tax_files = [tax_csv] if tax_csv else get_tax_files(config)
    tax_csv_data = load_prepaid_tax_data(tax_files, portfolio)

    result = prepare_pmt_result(portfolio, config, date, tax_rate, assumed_return, years, tax_csv_data, allowance)

    if result.empty:
        console.print(output.empty_result())
        return

    console.print(output.introduction(
        f'Withdrawing the [bold]gross[/bold] amount at the start of each year runs the portfolio down to zero '
        f'after {years} years at a constant real return of {assumed_return:.2f}% p.a. '
        f'The [bold]net[/bold] amount is what is left to spend after German taxes on the drawn gain '
        f'(up to {allowance:.2f} Sparerpauschbetrag applied). All amounts are in today\'s purchasing power.\n'
        '[dim]Restrictions: cash is included at par; future Vorabpauschale and the nominal taxation of real gains are not modeled.[/dim]'
    ))
    console.print(*output.result_table(
        result,
        TableOptions(title=f"Amortization Withdrawal on {date.strftime('%Y-%m-%d')}", show_index=False, show_total=False)
    ))

    cash = Money(PortfolioSnapshot(portfolio, date).balances.sum())
    console.print(output.hint(_next_step_hint(Money(result.iloc[0]['netPerYear']), cash)))
    console.print(output.hint(
        'Recompute yearly with the actual balance and the remaining horizon — '
        'lower realized returns shrink the next amount instead of causing ruin.'
    ))
    console.print(output.text(footer()), style="dim")
