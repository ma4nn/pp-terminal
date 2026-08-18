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

from datetime import datetime, date as DateType
import logging
import math
from pathlib import Path
from typing import cast
from typing_extensions import Annotated

import click
import pandas as pd
import typer
from pandera.typing import DataFrame
from pydantic import Field

from pp_terminal.data.filters import filter_by_account_and_security, retired_row_labels
from pp_terminal.data.tax import load_prepaid_tax_data
from pp_terminal.domain.allocation import build_category_map
from pp_terminal.domain.cost_basis import SellContext, enrich_fifo_lots
from pp_terminal.domain.portfolio import Portfolio, get_security_by_id
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.schemas import Money, Percent, TaxPaidSchema
from pp_terminal.exceptions import InputError
from pp_terminal.output.strategy import OutputStrategy, Console
from pp_terminal.output.table_decorator import TableOptions
from pp_terminal.utils.config import Config, ConfigModel, command_config
from pp_terminal.utils.helper import footer
from pp_terminal.utils.options import tax_rate_callback, allowance_callback

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)


class PmtConfig(ConfigModel):
    returns: list[Annotated[float, Field(ge=0, le=100)] | dict[str, Annotated[float, Field(ge=0, le=100)]]] | None = None
    end_date: DateType | None = None

_RESULT_COLUMNS = ['assumedReturn', 'grossPerYear', 'grossRate', 'netPerYear', 'netPerMonth', 'netRate', 'startCapital']

# reserved key in a per-category returns table that sets the rate for every category not listed explicitly
DEFAULT_RETURN_KEY = '*'


def split_return_scenario(scenario: dict[str, float]) -> tuple[float | None, dict[str, float]]:
    """Splits a per-category returns table into its default rate (the reserved '*' key, if any) and the explicit per-category overrides."""
    overrides = {category: rate for category, rate in scenario.items() if category != DEFAULT_RETURN_KEY}
    return scenario.get(DEFAULT_RETURN_KEY), overrides


def amortization_factor(rate: float, years: float) -> float:
    """Annuity-due factor: the capital fraction to withdraw at the start of each year so it hits zero after `years` years at return `rate`."""
    if years <= 1:  # less than one withdrawal period left: take out everything
        return 1.0
    if rate == 0:
        return 1 / years
    return rate / (1 - math.pow(1 + rate, -years)) / (1 + rate)


def _horizon_years(date: datetime, end_date: datetime) -> float:
    years = (end_date - date).days / 365.25
    if years <= 0:
        raise InputError("The end date must be after the snapshot date")
    return years


def _security_categories(portfolio: Portfolio, values: pd.Series, taxonomy: str) -> pd.Index:
    category_by_security, multi_category = build_category_map(portfolio.taxonomy_assignments, taxonomy)
    if multi_category:
        log.warning(
            "Securities span multiple categories in '%s'; using the dominant one: %s",
            taxonomy, _security_names(portfolio, multi_category)
        )

    categories = values.index.map(category_by_security)
    unmapped = [str(sid) for sid in values.index[categories.isna()]]
    if unmapped:
        log.warning(
            "Securities are not classified in '%s' and contribute a 0%% return: %s",
            taxonomy, _security_names(portfolio, unmapped)
        )

    return categories


def _security_names(portfolio: Portfolio, security_ids: list[str]) -> str:
    return ', '.join(sorted(get_security_by_id(portfolio, sid).name for sid in security_ids))


def _account_names(portfolio: Portfolio, account_ids: list[str]) -> str:
    names = portfolio.deposit_accounts['name']
    return ', '.join(sorted(str(names.get(account_id, account_id)) for account_id in account_ids))


def _account_categories(portfolio: Portfolio, balances: pd.Series, taxonomy: str) -> pd.Index:
    category_by_account, multi_category = build_category_map(portfolio.taxonomy_assignments, taxonomy, item_type='account')
    if multi_category:
        log.warning(
            "Accounts span multiple categories in '%s'; using the dominant one: %s",
            taxonomy, _account_names(portfolio, multi_category)
        )

    categories = balances.index.map(category_by_account)
    unmapped = [str(account_id) for account_id in balances.index[categories.isna()]]
    if unmapped:
        log.warning(
            "Deposit accounts are not classified in '%s' and contribute a 0%% return: %s",
            taxonomy, _account_names(portfolio, unmapped)
        )

    return categories


def _active_account_balances(snapshot: PortfolioSnapshot) -> pd.Series:
    """Deposit-account balances excluding retired accounts, which no longer back a withdrawal."""
    balances = snapshot.balances
    retired = retired_row_labels(snapshot.portfolio.deposit_accounts)
    if not retired:
        return balances
    return balances[~balances.index.get_level_values('accountId').isin(retired)]


def blended_return_from_allocation(portfolio: Portfolio, date: datetime, taxonomy: str, returns_by_category: dict[str, float]) -> Percent:
    """Weighted average of the configured per-category real returns over the taxonomy's current allocation,
    covering both securities and deposit accounts; categories without a configured rate fall back to the
    '*' default (if set), and items unclassified in the taxonomy weigh in at 0%."""
    default_return, category_overrides = split_return_scenario(returns_by_category)
    snapshot = PortfolioSnapshot(portfolio, date)
    security_values = snapshot.values.groupby('securityId').sum()
    account_values = _active_account_balances(snapshot).groupby('accountId').sum()

    total = security_values.sum() + account_values.sum()
    if total <= 0:
        raise InputError("The portfolio has no positive value to derive a return from")

    category_values = pd.concat([
        security_values.groupby(_security_categories(portfolio, security_values, taxonomy)).sum(),
        account_values.groupby(_account_categories(portfolio, account_values, taxonomy)).sum(),
    ]).groupby(level=0).sum()
    category_returns = pd.Series({str(category): category_overrides.get(str(category), default_return) for category in category_values.index}, dtype='float64')
    missing = sorted(category_returns[category_returns.isna()].index)
    if missing:
        log.warning("No return configured for taxonomy categories, assuming 0%%: %s", ', '.join(missing))

    return Percent(float((category_values * category_returns.fillna(0.0)).sum() / total))


def _resolve_return_scenarios(
        portfolio: Portfolio, taxonomy: str | None, date: datetime, scenarios: list[float | dict[str, float]]
) -> list[Percent]:
    """One assumed return per configured scenario: a fixed rate is taken as is, a per-category
    map is blended over the current allocation (which requires a configured taxonomy)."""
    resolved: list[Percent] = []
    for scenario in scenarios:
        if isinstance(scenario, dict):
            if not taxonomy:
                raise InputError("A per-category 'returns' entry requires the 'taxonomy' setting")
            resolved.append(blended_return_from_allocation(portfolio, date, taxonomy, scenario))
        else:
            resolved.append(Percent(scenario))
    return resolved


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
        assumed_returns: list[Percent],
        end_date: datetime,
        tax_csv_data: DataFrame[TaxPaidSchema] | None = None,
        allowance: Money | None = None,
) -> pd.DataFrame:
    if not assumed_returns:
        return pd.DataFrame()

    years = _horizon_years(date, end_date)
    effective_allowance = allowance if allowance is not None else config.tax.allowance

    snapshot = PortfolioSnapshot(portfolio, date)
    market_value, taxable_gain = _taxable_position(snapshot, tax_rate, Percent(config.tax.exemption_rate), tax_csv_data)
    cash = Money(_active_account_balances(snapshot).sum())

    if market_value == 0 and cash == 0:
        return pd.DataFrame()

    start_capital = market_value + cash
    if start_capital <= 0:
        raise InputError("Negative cash balances cancel out the securities value, nothing left to withdraw")

    gain_per_euro = taxable_gain / start_capital

    rows = []
    for assumed_return in assumed_returns:
        gross = start_capital * amortization_factor(assumed_return / 100, years)
        tax = max(gross * gain_per_euro - effective_allowance, 0) * tax_rate / 100
        net = gross - tax
        rows.append({
            'assumedReturn': Percent(assumed_return),
            'grossPerYear': Money(gross),
            'grossRate': Percent(gross / start_capital * 100),
            'netPerYear': Money(net),
            'netPerMonth': Money(net / 12),
            'netRate': Percent(net / start_capital * 100),
            'startCapital': Money(start_capital),  # constant per run, but the only way it reaches csv/json output
        })

    return pd.DataFrame(rows)[_RESULT_COLUMNS]


def _next_step_hint(gross: Money | None, cash: Money, gross_rate: Percent | None) -> str:
    if gross is None:
        hint = ('Pick a row and run [cyan]simulate share-sell --target-gross <grossPerYear> --summary[/cyan] '
                'to turn that row\'s gross withdrawal into a concrete sell plan per security (its real tax and net follow).')
        if cash > 0:
            hint += ' You may fund part of it from cash, lowering [cyan]--target-gross[/cyan] by that amount.'
        return hint

    if cash <= 0:
        return (f'Run [cyan]simulate share-sell --target-gross {gross:.2f} --summary[/cyan] '
                'to turn this year\'s gross withdrawal into a concrete sell plan per security (its real tax and net follow).')

    # proportional (plan-consistent) split: draw this year's withdrawal rate from cash too, the rest from securities
    cash_draw = min(cash * (gross_rate or Percent(0)) / 100, gross)
    from_securities = gross - cash_draw
    if from_securities <= 0:
        return (f'Fund the full {gross:.2f} from your cash balance of {cash:.2f} — '
                'no securities need to be sold this year.')

    return (
        f'Plan-consistent split: spend {cash_draw:.2f} from your cash balance of {cash:.2f}, then run '
        f'[cyan]simulate share-sell --target-gross {from_securities:.2f} --summary[/cyan] to raise the rest by selling securities. '
        'Drawing more from cash than this is your bad-year buffer — refill it in good years.'
    )


@app.command(name="pmt")
def simulate_pmt(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        ctx: typer.Context,
        assumed_returns: Annotated[list[Percent] | None, typer.Option("--return", min=0, max=100, help="Expected annual real return in percent; repeat the option to compare several rates (defaults to config)")] = None,
        end_date: Annotated[datetime | None, typer.Option("--end-date", formats=["%Y-%m-%d"], help="Date by which the capital should be depleted (defaults to config)")] = None,
        date: Annotated[datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Snapshot date (defaults to today)")] = None,
        tax_rate: Annotated[Percent, typer.Option(help="Your personal tax rate", min=0, max=100, callback=tax_rate_callback)] = None,  # type: ignore
        allowance: Annotated[Money, typer.Option("--allowance", help="Sparerpauschbetrag still available this year", min=0, callback=allowance_callback)] = None,  # type: ignore
        tax_csv: Annotated[Path | None, typer.Option(help="CSV file with paid tax per share data")] = None
) -> None:
    """
    Simulate an amortization withdrawal (annuity, "ARVA"): the net amount to spend this year so that
    the portfolio reaches zero by the given end date at the assumed real return. Recompute yearly.
    """
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)
    config = cast(Config, ctx.obj.config)
    pmt = command_config(config, PmtConfig)

    if date is None:
        date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if not assumed_returns:
        assumed_returns = _resolve_return_scenarios(portfolio, config.taxonomy, date, pmt.returns or [])
    if not assumed_returns:
        assumed_returns = [Percent(typer.prompt("Expected Annual Real Return (%)", type=click.FloatRange(0, 100)))]
    if end_date is None and pmt.end_date is not None:
        end_date = datetime.combine(pmt.end_date, datetime.min.time())
    if end_date is None:
        end_date = typer.prompt("End Date", type=click.DateTime(formats=["%Y-%m-%d"]))

    tax_files = [tax_csv] if tax_csv else config.tax.files
    tax_csv_data = load_prepaid_tax_data(tax_files, portfolio)

    result = prepare_pmt_result(portfolio, config, date, tax_rate, assumed_returns, end_date, tax_csv_data, allowance)

    if result.empty:
        console.print(output.empty_result())
        return

    rate_clause = (f'a constant real return of {assumed_returns[0]:.2f}% p.a.'
                   if len(assumed_returns) == 1 else 'the constant real return assumed per row.')
    console.print(output.introduction(
        f'Withdrawing the [bold]gross[/bold] amount at the start of each year runs the portfolio down to zero '
        f'by {end_date.strftime("%Y-%m-%d")} ({round(_horizon_years(date, end_date) * 12)} months left) at {rate_clause} '
        f'The [bold]net[/bold] amount is what is left to spend after an [bold]estimated[/bold] German tax on the drawn gain '
        f'(up to {allowance:.2f} Sparerpauschbetrag applied). All amounts are in today\'s purchasing power.\n'
        '[dim]Restrictions: the tax (and hence net) is approximate — it applies the portfolio\'s average embedded gain '
        'uniformly, whereas a real sale realizes specific lots (the least-taxed first), so a given year\'s actual tax is '
        'usually lower. Run [cyan]simulate share-sell[/cyan] for the exact per-security figure, matching this row\'s gross '
        'with [cyan]--target-gross[/cyan] (or its net with [cyan]--target-net[/cyan]). Cash is included at par; future '
        'Vorabpauschale and the nominal taxation of real gains are not modeled.[/dim]'
    ))
    console.print(*output.result_table(
        result,
        TableOptions(title=f"Amortization Withdrawal on {date.strftime('%Y-%m-%d')}", show_index=False, show_total=False)
    ))

    cash = Money(_active_account_balances(PortfolioSnapshot(portfolio, date)).sum())
    single = result.iloc[0] if len(result) == 1 else None
    gross = Money(single['grossPerYear']) if single is not None else None
    gross_rate = Percent(single['grossRate']) if single is not None else None
    console.print(output.hint(_next_step_hint(gross, cash, gross_rate)))
    console.print(output.hint(
        'Recompute yearly with the actual balance and the remaining horizon — '
        'lower realized returns shrink the next amount instead of causing ruin.'
    ))
    console.print(output.text(footer()), style="dim")
