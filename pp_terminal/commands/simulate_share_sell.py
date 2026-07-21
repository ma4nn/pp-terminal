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
from typing import cast, Any
from typing_extensions import Annotated

import pandas as pd
import typer
from pandera.typing import DataFrame
from pydantic import Field

from pp_terminal.data.filters import filter_by_account_and_security, filter_by_security, filter_by_account
from pp_terminal.domain.cost_basis import SellContext, enrich_fifo_lots, finalize_sell_lots
from pp_terminal.data.tax import load_prepaid_tax_data
from pp_terminal.domain.sell_strategy import SellStrategy, FixedSharesStrategy, MinTaxStrategy, AllocationPreservingStrategy
from pp_terminal.domain.allocation import build_category_map
from pp_terminal.exceptions import InputError
from pp_terminal.utils.config import Config, ConfigModel, command_config
from pp_terminal.utils.helper import footer, format_percent
from pp_terminal.utils.options import tax_rate_callback
from pp_terminal.output.strategy import OutputStrategy, Console
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.portfolio import Portfolio, get_security_by_id
from pp_terminal.domain.schemas import Percent, Money, TaxPaidSchema
from pp_terminal.output.table_decorator import TableOptions, format_value

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)


class ShareSellConfig(ConfigModel):
    min_amount: Annotated[Money, Field(gt=0)] | None = None


_RESULT_COLUMNS = ['securityName', 'isin', 'account', 'date', 'shares', 'currency', 'purchasePrice', 'costBasis',
                   'fees', 'salePrice', 'grossProceeds', 'capitalGain', 'deemedIncome',
                   'taxableGain', 'totalTax', 'netProceeds']

_PLAN_GROUP = ['securityName', 'isin', 'account', 'currency']
_PLAN_SUM_COLUMNS = ['shares', 'costBasis', 'fees', 'grossProceeds', 'capitalGain', 'deemedIncome',
                     'taxableGain', 'totalTax', 'netProceeds']
_PLAN_COLUMNS = ['securityName', 'isin', 'account', 'shares', 'currency', 'purchasePrice', 'costBasis',
                 'fees', 'salePrice', 'grossProceeds', 'capitalGain', 'deemedIncome',
                 'taxableGain', 'totalTax', 'netProceeds']

# lean plan for the CLI: identity + order + gain/tax outcome; the cost-basis/tax mechanics stay in the per-lot detail
_PLAN_DISPLAY_COLUMNS = ['securityName', 'isin', 'account', 'shares', 'currency', 'salePrice',
                         'grossProceeds', 'capitalGain', 'totalTax', 'netProceeds']


def summarize_sell_plan(lots: pd.DataFrame) -> pd.DataFrame:
    """Collapse the per-lot sell rows into one actionable order per security."""
    with_class = 'assetClass' in lots.columns
    with_share = 'classShare' in lots.columns
    group = ['assetClass', *_PLAN_GROUP] if with_class else _PLAN_GROUP
    columns = ['assetClass', *_PLAN_COLUMNS] if with_class else list(_PLAN_COLUMNS)
    aggregations: dict[str, str] = {column: 'sum' for column in _PLAN_SUM_COLUMNS}
    aggregations['salePrice'] = 'first'  # constant across a security's lots
    if with_share:
        aggregations['classShare'] = 'first'  # constant across an asset class
        columns = [*columns, 'classShare']
    # dropna=False keeps securities without an ISIN (e.g. crypto), otherwise their proceeds vanish from the total
    grouped = lots.assign(weightedCost=lots['purchasePrice'] * lots['shares']).groupby(group, sort=False, dropna=False)
    plan = grouped.agg({**aggregations, 'weightedCost': 'sum'})
    plan['purchasePrice'] = plan['weightedCost'] / plan['shares']
    return plan.reset_index()[columns]


def get_today() -> datetime:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _format_share_sell_value(value: Any, column_name: str, row: pd.Series) -> str:
    if column_name == 'classShare':
        if row.isin(['Total']).any():
            return format_percent(1.0)  # all asset classes together are the whole sale
        return format_percent(value) if isinstance(value, float) else ''
    return format_value(value, column_name, row)


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

    exempt_rate = Percent(config.tax.exemption_rate)
    all_enriched = []
    for (acc_id, sec_id, _currency), _shares_held in holdings.items():
        transactions = snapshot.securities_account_transactions.pipe(
            filter_by_account_and_security, security_id=sec_id, account_id=acc_id
        )
        sale_price = price if price else latest_prices.loc[sec_id]
        sell_ctx = SellContext(snapshot.date, sale_price, tax_rate, exempt_rate, tax_csv_data)
        enriched = enrich_fifo_lots(transactions, sell_ctx)
        if not enriched.empty:
            all_enriched.append(enriched)

    if not all_enriched:
        return pd.DataFrame()

    result = pd.concat(all_enriched)

    category_by_security = _resolve_categories(portfolio, taxonomy, holdings) if taxonomy else None
    strategy = _build_strategy(shares, target_net, category_by_security, min_amount)
    if strategy is not None:
        result = finalize_sell_lots(strategy.select_lots(result), tax_rate)
    if isinstance(strategy, AllocationPreservingStrategy) and strategy.excluded_groups:
        log.warning(
            "Asset classes whose entire sale would fall below the %.2f minimum order were left unsold: %s",
            min_amount, ', '.join(strategy.excluded_groups)
        )

    result = result.reset_index()
    security_info = portfolio.securities.reindex(columns=['name', 'isin'])
    result['securityName'] = result['securityId'].map(security_info['name'])
    result['isin'] = result['securityId'].map(security_info['isin'])
    result['account'] = result['accountId'].map(portfolio.securities_accounts['name']).fillna(result['accountId'])

    columns, sort_keys = _RESULT_COLUMNS, ['securityName', 'date']
    if taxonomy:
        result['assetClass'] = result['securityId'].map(category_by_security).fillna('(unclassified)')
        class_gross = result.groupby('assetClass')['grossProceeds'].transform('sum')
        result['classShare'] = class_gross / result['grossProceeds'].sum()
        columns, sort_keys = ['assetClass', *_RESULT_COLUMNS, 'classShare'], ['assetClass', *sort_keys]

    return result.sort_values(sort_keys)[columns]


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


def _sell_scope(portfolio: Portfolio, security_id: str | None, account_id: str | None) -> str:
    scope = ''
    if security_id:
        scope += f' of {get_security_by_id(portfolio, security_id).name}'
    if account_id:
        scope += f' in {portfolio.securities_accounts["name"].get(account_id, account_id)}'
    return scope


def _sell_introduction(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        scope: str,
        price: Money | None,
        shares: float | None,
        target_net: Money | None,
        taxonomy: str | None,
        min_amount: Money | None,
) -> str:
    """Explain which shares get sold and why, adapting to the active selection strategy."""
    price_clause = f'the given price of {price:.2f}' if price is not None else 'the latest price'
    if target_net is not None and taxonomy is not None:
        floor_clause = (f'; orders below {min_amount:.2f} are consolidated onto larger holdings or left unsold'
                        if min_amount is not None else '')
        selection = (
            f'Sells just enough{scope} to net [bold]{target_net:.2f}[/bold] while [bold]holding your '
            f'{taxonomy} allocation steady[/bold]: every asset class sheds the same fraction of its value, '
            f'drawing the least-taxed securities within each class first (FIFO within each){floor_clause}.'
        )
    elif target_net is not None:
        selection = (
            f'Sells just enough{scope} to net [bold]{target_net:.2f}[/bold], taking the '
            '[bold]least-taxed lots first[/bold] (lowest tax per euro of net proceeds) to keep the tax bill minimal.'
        )
    elif shares is not None:
        selection = (
            f'Sells [bold]{shares:g} shares[/bold]{scope} at {price_clause}, drawing the '
            '[bold]oldest lots first[/bold] (FIFO) — the order gains are realized in.'
        )
    else:
        selection = f'Sells [bold]every share you hold[/bold]{scope} at {price_clause}, realizing all FIFO lots in full.'

    return (
        f'{selection} Each lot is taxed on its gain over the [bold]FIFO cost basis[/bold] '
        '(Abgeltungssteuer + Soli), crediting any Vorabpauschale already paid; the Sparerpauschbetrag is '
        '[bold]not[/bold] applied, so gains are shown fully taxed. [bold]Net Proceeds[/bold] is what reaches '
        'your account after fees and tax.'
    )


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
        taxonomy: Annotated[str | None, typer.Option("--preserve-allocation", metavar="TAXONOMY", help="Preserve the asset allocation while reaching --target-net using this taxonomy's classes; defaults to config")] = None,
        min_amount: Annotated[Money | None, typer.Option("--min-amount", help="Minimum gross size per sell order; small holdings consolidate within their class or stay unsold (preserve-allocation only); defaults to config", min=0.01)] = None,
        summary: Annotated[bool, typer.Option("--summary", help="Aggregate the FIFO lots into one row per security (an actionable sell plan)")] = False,
        tax_csv: Annotated[Path | None, typer.Option(help="CSV file with paid tax per share data")] = None
) -> None:
    """
    Simulate selling shares: calculate fees, taxes (Abgeltungssteuer + Soli), and net proceeds.
    Uses FIFO cost basis and accounts for taxes already paid.
    """
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)
    config = cast(Config, ctx.obj.config)

    # fall back to the globally configured taxonomy so a target-net run preserves the allocation
    # without repeating --preserve-allocation; it has no effect (and no cost) without a target net
    if taxonomy is None and target_net is not None:
        taxonomy = config.taxonomy
    # the order floor only makes sense while preserving an allocation, so pull it from config only then
    if min_amount is None and taxonomy is not None:
        min_amount = command_config(config, ShareSellConfig).min_amount

    _validate_options(security_id, shares, price, target_net, taxonomy, min_amount)

    if date is None:
        date = get_today()

    tax_files = [tax_csv] if tax_csv else config.tax.files
    tax_csv_data = load_prepaid_tax_data(tax_files, portfolio)

    result = prepare_share_sell_df(
        portfolio, config, date, tax_rate,
        security_id, account_id, shares, price, target_net, taxonomy, min_amount, tax_csv_data
    )

    if result.empty:
        console.print(output.empty_result())
        return

    console.print(output.introduction(_sell_introduction(
        _sell_scope(portfolio, security_id, account_id), price, shares, target_net, taxonomy, min_amount
    )))

    if summary:
        result = summarize_sell_plan(result)
        lead = ['assetClass'] if 'assetClass' in result.columns else []
        trail = ['classShare'] if 'classShare' in result.columns else []
        result = result[lead + _PLAN_DISPLAY_COLUMNS + trail]

    title = "Share Sale Plan" if summary else "Share Sale Simulation"
    console.print(*output.result_table(
        result,
        TableOptions(title=f"{title} on {date.strftime('%Y-%m-%d')}", show_index=False, show_total=True,
                     value_formatter=_format_share_sell_value)
    ))

    console.print(output.warning('Multi-currency totals are not meaningful.'))
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
