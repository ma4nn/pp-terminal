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

from pp_terminal.data.filters import filter_by_account_and_security, filter_by_security, filter_by_account
from pp_terminal.domain.cost_basis import enrich_fifo_lots, finalize_sell_lots
from pp_terminal.data.tax import load_prepaid_tax_data
from pp_terminal.domain.sell_strategy import SellStrategy, FixedSharesStrategy, MinTaxStrategy, AllocationPreservingStrategy
from pp_terminal.domain.allocation import build_category_map
from pp_terminal.exceptions import InputError
from pp_terminal.utils.config import Config, get_exempt_rate, get_tax_files
from pp_terminal.utils.helper import footer
from pp_terminal.utils.options import tax_rate_callback, tax_csv_callback
from pp_terminal.output.strategy import OutputStrategy, Console
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.portfolio import Portfolio, get_security_by_id
from pp_terminal.domain.schemas import Percent, Money, TaxPaidSchema
from pp_terminal.output.table_decorator import TableOptions

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)

_RESULT_COLUMNS = ['securityName', 'isin', 'date', 'shares', 'currency', 'purchasePrice', 'costBasis',
                   'fees', 'salePrice', 'grossProceeds', 'capitalGain', 'deemedIncome',
                   'taxableGain', 'totalTax', 'netProceeds']


def get_today() -> datetime:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def prepare_share_sell_df(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        portfolio: Portfolio,
        config: Config,
        date: datetime,
        tax_rate: Percent,
        security_id: str | None = None,
        account_id: str | None = None,
        shares: float | None = None,
        price: Money | None = None,
        target_net: Money | None = None,
        taxonomy: str | None = None,
        min_amount: Money | None = None,
        tax_csv_data: DataFrame[TaxPaidSchema] | None = None,
) -> pd.DataFrame:
    if taxonomy is not None and target_net is None:
        raise InputError("preserve-allocation requires a target net proceeds amount")
    if min_amount is not None and taxonomy is None:
        raise InputError("a minimum amount requires preserve-allocation (a taxonomy)")

    snapshot = PortfolioSnapshot(portfolio, date)
    holdings = snapshot.shares

    if security_id:
        holdings = holdings.pipe(filter_by_security, security_id=security_id)
    if account_id:
        holdings = holdings.pipe(filter_by_account, account_id=account_id)

    if holdings.empty:
        return pd.DataFrame()

    security_ids = holdings.index.get_level_values('securityId').unique()
    latest_prices = snapshot.latest_prices

    missing_prices = [sid for sid in security_ids if sid not in latest_prices.index]
    if missing_prices:
        raise InputError(f"No price data for: {', '.join(missing_prices)}")

    all_enriched = []
    for (acc_id, sec_id, _currency), _shares_held in holdings.items():
        transactions = snapshot.securities_account_transactions.pipe(
            filter_by_account_and_security, security_id=sec_id, account_id=acc_id
        )
        sale_price = price if price else latest_prices.loc[sec_id]
        enriched = enrich_fifo_lots(
            transactions, snapshot.date, sale_price, tax_rate,
            tax_csv_data, exempt_rate=get_exempt_rate(config)
        )
        if not enriched.empty:
            all_enriched.append(enriched)

    if not all_enriched:
        return pd.DataFrame()

    result = pd.concat(all_enriched)

    category_by_security = _resolve_categories(portfolio, taxonomy, holdings) if taxonomy else None
    strategy = _build_strategy(shares, target_net, category_by_security, min_amount)
    if strategy:
        result = finalize_sell_lots(strategy.select_lots(result), tax_rate)
        if isinstance(strategy, AllocationPreservingStrategy) and strategy.excluded_groups:
            log.warning(
                "Asset classes below the %.2f minimum were left unsold to avoid dust trades: %s",
                min_amount, ', '.join(strategy.excluded_groups)
            )

    result = result.reset_index()
    security_info = portfolio.securities.reindex(columns=['name', 'isin'])
    result['securityName'] = result['securityId'].map(security_info['name'])
    result['isin'] = result['securityId'].map(security_info['isin'])
    result = result.sort_values(['securityName', 'date'])

    return result[_RESULT_COLUMNS]


def _resolve_categories(portfolio: Portfolio, taxonomy: str, holdings: pd.Series) -> dict[str, str]:
    category_by_security, multi_category = build_category_map(portfolio.taxonomy_assignments, taxonomy)
    held_ids = list(dict.fromkeys(holdings.index.get_level_values('securityId')))
    held = set(held_ids)

    held_multi = [sid for sid in multi_category if sid in held]
    if held_multi:
        log.warning(
            "Securities span multiple categories in '%s'; using the dominant one (allocation may drift): %s",
            taxonomy, _security_names(portfolio, held_multi)
        )

    unmapped = [sid for sid in held_ids if sid not in category_by_security]
    if unmapped:
        log.warning(
            "Securities are not classified in '%s' and are preserved individually rather than within their asset class: %s",
            taxonomy, _security_names(portfolio, unmapped)
        )

    return category_by_security


def _security_names(portfolio: Portfolio, security_ids: list[str]) -> str:
    return ', '.join(sorted(get_security_by_id(portfolio, sid).name for sid in security_ids))


@app.command(name="share-sell")
def simulate_share_sell(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        ctx: typer.Context,
        security_id: Annotated[str | None, typer.Argument(help="Security ID (defaults to all securities)")] = None,
        account_id: Annotated[str | None, typer.Option("--account-id", "-a", help="Securities account ID (defaults to all accounts)")] = None,
        date: Annotated[datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Sale date (defaults to today)")] = None,
        tax_rate: Annotated[Percent, typer.Option(help="Your personal tax rate", min=0, max=100, callback=tax_rate_callback)] = None,  # type: ignore
        shares: Annotated[float | None, typer.Option(help="Number of shares to sell (only with --security-id)", min=0.0001)] = None,
        price: Annotated[Money | None, typer.Option(help="Sale price per share (only with --security-id)", min=0.0001)] = None,
        target_net: Annotated[Money | None, typer.Option("--target-net", help="Target net proceeds to realize (minimizes taxes)", min=0.01)] = None,
        taxonomy: Annotated[str | None, typer.Option("--preserve-allocation", metavar="TAXONOMY", help="Preserve the current asset allocation while reaching --target-net, using this Portfolio Performance taxonomy's classes")] = None,
        min_amount: Annotated[Money | None, typer.Option("--min-amount", help="Skip any asset class whose gross sale would fall below this, to avoid dust trades (only with --preserve-allocation)", min=0.01)] = None,
        tax_csv: Annotated[Path | None, typer.Option(help="CSV file with paid tax per share data", callback=tax_csv_callback)] = None
) -> None:
    """
    Simulate selling shares: calculate fees, taxes (Abgeltungssteuer + Soli), and net proceeds.
    Uses FIFO cost basis and accounts for taxes already paid.
    """
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)
    config = cast(Config, ctx.obj.config)

    _validate_options(security_id, shares, price, target_net, taxonomy, min_amount)

    if date is None:
        date = get_today()

    tax_files = [tax_csv] if tax_csv else get_tax_files(config)
    tax_csv_data = load_prepaid_tax_data(tax_files, portfolio)

    result = prepare_share_sell_df(
        portfolio, config, date, tax_rate,
        security_id, account_id, shares, price, target_net, taxonomy, min_amount, tax_csv_data
    )

    if result.empty:
        console.print(output.empty_result())
        return

    console.print(*output.result_table(
        result,
        TableOptions(title=f"Share Sale Simulation on {date.strftime('%Y-%m-%d')}", show_index=False, show_total=True)
    ))

    console.print(output.warning('This simulation excludes Sparerpauschbetrag. Multi-currency totals not meaningful.'))
    console.print(output.text(footer()), style="dim")


def _validate_options(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        security_id: str | None,
        shares: float | None,
        price: Money | None,
        target_net: Money | None,
        taxonomy: str | None,
        min_amount: Money | None
) -> None:
    if shares is not None and target_net is not None:
        raise InputError("--shares and --target-net are mutually exclusive")
    if shares is not None and security_id is None:
        raise InputError("--shares requires --security-id")
    if price is not None and security_id is None:
        raise InputError("--price requires --security-id")
    if taxonomy is not None and target_net is None:
        raise InputError("--preserve-allocation requires --target-net")
    if min_amount is not None and taxonomy is None:
        raise InputError("--min-amount requires --preserve-allocation")


def _build_strategy(
        shares: float | None,
        target_net: Money | None,
        category_by_security: dict[str, str] | None,
        min_amount: Money | None
) -> SellStrategy | None:
    if shares is not None:
        return FixedSharesStrategy(shares)
    if target_net is not None:
        if category_by_security is not None:
            return AllocationPreservingStrategy(target_net, category_by_security, min_amount)
        return MinTaxStrategy(target_net)
    return None
