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

import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
import typer
from mcp.server.fastmcp import FastMCP

from pp_terminal.commands.simulate_share_sell import prepare_share_sell_df, summarize_sell_plan
from pp_terminal.commands.view_accounts import prepare_accounts_df
from pp_terminal.commands.view_securities import prepare_securities_df
from pp_terminal.commands.view_taxonomies import prepare_taxonomies_df
from pp_terminal.data.filters import clean_for_display
from pp_terminal.data.tax import load_prepaid_tax_data
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import AccountType
from pp_terminal.data.pp_portfolio_builder import CachedPpPortfolioBuilder
from pp_terminal.exceptions import InputError
from pp_terminal.output.strategy import JsonOutputStrategy
from pp_terminal.utils.cache import checksum
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.vap import calculate_vap, get_base_rate_for_year, add_account_balances
from pp_terminal.utils.config import Config

log = logging.getLogger(__name__)
_MCP_NAME = "pp-mcp"
_MCP_INSTRUCTIONS = """Portfolio Performance analytics server for a single portfolio XML file.

Workflow:
1. Use query_securities or query_accounts to discover holdings, IDs, and balances.
2. Use the appropriate analysis tool (see tool selection guide below).

Tool selection guide:
- "Show my holdings" / "What securities do I have?" → query_securities
- "Show my accounts" / "What is my cash balance?" → query_accounts
- "What taxonomies exist?" / "Show asset allocation categories" → portfolio://taxonomies resource
- "What if I sell everything?" / "Total tax on my portfolio?" → simulate_sell_all
- "I need X EUR after tax" / "Sell to get X net" / "Minimize taxes for X amount" → simulate_sell_target_net
- "What if I sell N shares of X?" → simulate_sell_shares
- "Show FIFO lots for X" / "Purchase history for X" → query_fifo_lots
- "Calculate Vorabpauschale" / "VAP for year X" → simulate_vap

The sell/FIFO tools accept a 'security' parameter that can be either an ISIN (e.g. 'IE00B4L5Y983')
or an internal securityId UUID. Prefer ISIN when available.

All dates are ISO format strings (e.g. '2025-06-15'). All monetary amounts are in the security's
native currency. Tax rates are in percent (e.g. 26.375 for German Abgeltungssteuer + Soli).
"""

_SUMMARY_COLUMNS = ['securityName', 'account', 'currency', 'shares', 'salePrice', 'costBasis',
                    'fees', 'grossProceeds', 'capitalGain', 'deemedIncome',
                    'taxableGain', 'totalTax', 'netProceeds']


def _is_empty(value: Any) -> bool:
    if value is None or value is pd.NaT or value == '':
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def _clean_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = df.to_dict(orient='records')
    return [
        {k: v for k, v in record.items() if not _is_empty(v)}
        for record in records
    ]

def _resolve_security(portfolio: Portfolio, security: str) -> str:
    if security in portfolio.securities.index:
        return security

    isin_matches = portfolio.securities.index[portfolio.securities['isin'] == security]
    if len(isin_matches) == 1:
        return str(isin_matches[0])
    if len(isin_matches) > 1:
        raise InputError(f"ISIN '{security}' matches multiple securities: {list(isin_matches)}")

    wkn_matches = portfolio.securities.index[portfolio.securities['wkn'] == security]
    if len(wkn_matches) == 1:
        return str(wkn_matches[0])
    if len(wkn_matches) > 1:
        raise InputError(f"WKN '{security}' matches multiple securities: {list(wkn_matches)}")

    raise InputError(f"Security '{security}' not found by ID or ISIN OR WKN")


def create_mcp_server(file_path: Path, config: Config) -> FastMCP:  # pylint: disable=too-many-locals,too-many-statements
    builder = CachedPpPortfolioBuilder()
    output = JsonOutputStrategy()  # plain (icon-free) validation messages for MCP records
    state: dict[str, Any] = {
        'portfolio': builder.construct(file_path),
        'checksum': checksum(file_path),
    }

    mcp = FastMCP(_MCP_NAME, instructions=_MCP_INSTRUCTIONS)

    def _ensure_fresh_portfolio() -> Portfolio:
        current_checksum = checksum(file_path)
        if current_checksum != state['checksum']:
            log.info("XML file changed, rebuilding portfolio")
            state['portfolio'] = builder.construct(file_path)
            state['checksum'] = current_checksum
        return cast(Portfolio, state['portfolio'])

    @mcp.resource("portfolio://accounts", mime_type="application/json")
    def accounts_resource() -> str:
        """All portfolio accounts (deposit and securities) with their metadata."""
        portfolio = _ensure_fresh_portfolio()
        df = (pd.concat([portfolio.deposit_accounts, portfolio.securities_accounts])
              .reset_index()
              .pipe(clean_for_display, portfolio.account_attributes))
        return json.dumps(_clean_records(df))

    @mcp.resource("portfolio://securities", mime_type="application/json")
    def securities_resource() -> str:
        """All portfolio securities with their metadata."""
        portfolio = _ensure_fresh_portfolio()
        df = (portfolio.securities.reset_index()
              .pipe(clean_for_display, portfolio.security_attributes))
        return json.dumps(_clean_records(df))

    @mcp.resource("portfolio://taxonomies", mime_type="application/json")
    def taxonomies_resource() -> str:
        """All taxonomies with their categories and assignment counts."""
        portfolio = _ensure_fresh_portfolio()
        df = prepare_taxonomies_df(portfolio)
        return json.dumps(_clean_records(df))

    @mcp.tool()
    def query_securities(
        date: str | None = None,
        active_only: bool = False,
        in_stock_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List all securities with current holdings and key metrics.

        Each row is one security showing: securityId, name, ISIN, WKN, currency, shares held,
        latestPrice (per share), costBasis, vap (Vorabpauschale), Messages (validation warnings),
        and taxonomy columns (e.g. Asset Allocation, Regions) when assigned.
        Use securityId or ISIN from results to identify securities in other tools.

        Args:
            date: Valuation date as ISO string (defaults to today)
            active_only: If true, exclude retired securities
            in_stock_only: If true, only show securities with shares > 0
        """
        portfolio = _ensure_fresh_portfolio()
        by_date = datetime.fromisoformat(date) if date else datetime.now()
        df = prepare_securities_df(portfolio, config, output, by_date, active_only, in_stock_only)
        return _clean_records(df)

    @mcp.tool()
    def query_accounts(
        date: str | None = None,
        account_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all accounts with balances and validation warnings.

        Each row is one account showing: accountId, name, type (account or portfolio),
        balance per currency, and Messages (validation warnings).

        Args:
            date: Valuation date as ISO string (defaults to today)
            account_type: Filter by type: 'DEPOSIT' (= cash) or 'SECURITIES' (= portfolio)
        """
        portfolio = _ensure_fresh_portfolio()
        by_date = datetime.fromisoformat(date) if date else datetime.now()
        parsed_type = AccountType[account_type] if account_type else None
        df = prepare_accounts_df(portfolio, config, output, by_date, parsed_type)
        return _clean_records(df.reset_index())

    @mcp.tool()
    def simulate_vap(
        year: int | None = None,
        tax_rate: float | None = None,
    ) -> list[dict[str, Any]]:
        """Calculate German preliminary lump-sum tax (Vorabpauschale/VAP) per security for a given year (section 18 InvStG).

        Each row is one security showing: securityId, name, WKN, currency, and one column per
        securities account with the VAP amount. A 'Related Account Balance' row shows whether
        the linked deposit account has sufficient funds to cover the tax.

        Args:
            year: Tax year (defaults to last year)
            tax_rate: Personal tax rate in percent (defaults to config or 26.375%)
        """
        portfolio = _ensure_fresh_portfolio()
        effective_year = year if year is not None else datetime.now().year - 1

        base_rate_pct = get_base_rate_for_year(effective_year)
        tax_rate_pct = tax_rate if tax_rate is not None else config.tax.rate

        snapshot_begin = PortfolioSnapshot(portfolio, datetime(effective_year, 1, 2))
        snapshot_end = PortfolioSnapshot(portfolio, datetime(effective_year, 12, 31))

        result = calculate_vap(
            snapshot_begin, snapshot_end,
            base_rate_pct, tax_rate_pct, exempt_rate_percent=config.tax.exemption_rate, exempt_rate_attr_uuid=config.tax.exemption_rate_attribute
        )

        if result.empty:
            return []

        result = add_account_balances(result, portfolio, snapshot_end)
        return _clean_records(result)

    def _sell_defaults(date: str | None, tax_rate: float | None) -> tuple[datetime, float]:
        sell_date = datetime.fromisoformat(date) if date else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        effective_tax_rate = tax_rate if tax_rate is not None else config.tax.rate
        return sell_date, effective_tax_rate

    @mcp.tool()
    def query_fifo_lots(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        security: str | None = None,
        account_id: str | None = None,
        date: str | None = None,
        tax_rate: float | None = None,
        price: float | None = None,
    ) -> list[dict[str, Any]]:
        """List all FIFO purchase lots with projected tax on hypothetical sale.

        Each row is one purchase lot (FIFO order) showing: securityName, isin, account, purchase date, shares,
        currency, purchasePrice, costBasis, fees, salePrice, grossProceeds, capitalGain,
        deemedIncome (Vorabpauschale), taxableGain, totalTax, netProceeds.

        Args:
            security: ISIN (e.g. 'IE00B4L5Y983') or securityId UUID to filter (defaults to all)
            account_id: Filter to a single securities account (defaults to all accounts)
            date: Valuation date as ISO string, e.g. '2025-06-15' (defaults to today)
            tax_rate: Personal tax rate in percent (defaults to config or 26.375%)
            price: Override sale price per share (only meaningful with security, defaults to latest price)
        """
        portfolio = _ensure_fresh_portfolio()
        sell_date, effective_tax_rate = _sell_defaults(date, tax_rate)
        tax_csv_data = load_prepaid_tax_data(config.tax.files, portfolio)
        security_id = _resolve_security(portfolio, security) if security else None

        result = prepare_share_sell_df(
            portfolio, config, sell_date, effective_tax_rate,
            security_id, account_id, price=price, tax_csv_data=tax_csv_data
        )
        return _clean_records(result) if not result.empty else []

    @mcp.tool()
    def simulate_sell_all(
        date: str | None = None,
        tax_rate: float | None = None,
    ) -> list[dict[str, Any]]:
        """Simulate selling all shares and return a per-security summary with totals.

        Use this to answer questions like 'what if I sell everything?' or 'how much tax on my portfolio?'.
        Each row is one security per account showing: securityName, account, currency, shares, salePrice,
        costBasis, fees, grossProceeds, capitalGain, deemedIncome (Vorabpauschale), taxableGain, totalTax,
        netProceeds. For individual FIFO lot detail, use query_fifo_lots instead.
        To target a specific net amount (e.g. 'I need 1000 EUR after tax'), use simulate_sell_target_net instead.

        Args:
            date: Sale date as ISO string, e.g. '2025-06-15' (defaults to today)
            tax_rate: Personal tax rate in percent (defaults to config or 26.375%)
        """
        portfolio = _ensure_fresh_portfolio()
        sell_date, effective_tax_rate = _sell_defaults(date, tax_rate)
        tax_csv_data = load_prepaid_tax_data(config.tax.files, portfolio)

        lots = prepare_share_sell_df(
            portfolio, config, sell_date, effective_tax_rate,
            None, None, tax_csv_data=tax_csv_data
        )
        if lots.empty:
            return []

        return _clean_records(summarize_sell_plan(lots)[_SUMMARY_COLUMNS])

    @mcp.tool()
    def simulate_sell_shares(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        security: str,
        shares: float | None = None,
        account_id: str | None = None,
        date: str | None = None,
        tax_rate: float | None = None,
        price: float | None = None,
    ) -> list[dict[str, Any]]:
        """Simulate selling shares of a specific security using FIFO order.

        Returns the FIFO purchase lots consumed by the sale. Each row is one lot showing:
        securityName, purchase date, shares sold from that lot, currency, purchasePrice,
        costBasis, fees, salePrice, grossProceeds, capitalGain, deemedIncome (Vorabpauschale),
        taxableGain, totalTax, netProceeds.
        To find the optimal shares to sell for a target net amount, use simulate_sell_target_net instead.

        Args:
            security: ISIN (e.g. 'IE00B4L5Y983') or securityId UUID (required)
            shares: Number of shares to sell
            account_id: Securities account ID (defaults to all accounts holding this security)
            date: Sale date as ISO string, e.g. '2025-06-15' (defaults to today)
            tax_rate: Personal tax rate in percent (defaults to config or 26.375%)
            price: Sale price per share (defaults to latest known price)
        """
        portfolio = _ensure_fresh_portfolio()
        sell_date, effective_tax_rate = _sell_defaults(date, tax_rate)
        tax_csv_data = load_prepaid_tax_data(config.tax.files, portfolio)
        security_id = _resolve_security(portfolio, security)

        result = prepare_share_sell_df(
            portfolio, config, sell_date, effective_tax_rate,
            security_id, account_id, shares=shares, price=price, tax_csv_data=tax_csv_data
        )
        return _clean_records(result) if not result.empty else []

    @mcp.tool()
    def simulate_sell_target_net(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        target_net: float,
        security: str | None = None,
        account_id: str | None = None,
        date: str | None = None,
        tax_rate: float | None = None,
        taxonomy: str | None = None,
        min_amount: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find the shares to sell to achieve a target net proceeds amount with minimal taxes.

        Use this to answer questions like 'I need 1000 EUR after tax, what should I sell?',
        'which securities to sell to get 5000 EUR with minimal taxes?', or
        'how to realize X EUR net from my portfolio?'.
        Selects lots across securities/accounts to minimize total tax while reaching the target.
        Pass a taxonomy to instead preserve the current asset allocation while reaching the target:
        each asset class of that Portfolio Performance taxonomy keeps its weight and the sale
        consolidates within a class onto the most tax-efficient securities (so redundant holdings of
        the same class need not all be sold). Answers 'get X EUR net without changing my allocation'.
        With a taxonomy, pass min_amount to skip asset classes whose gross sale would fall below it
        (avoids dust trades); the remaining classes still reach the full target.
        Uses latest known prices. Each row is one FIFO lot showing: securityName, isin, purchase date,
        shares to sell, currency, purchasePrice, costBasis, fees, salePrice, grossProceeds,
        capitalGain, deemedIncome (Vorabpauschale), taxableGain, totalTax, netProceeds.
        With a taxonomy each row also carries assetClass and classShare (the class's share of total gross proceeds).

        Args:
            target_net: Target net proceeds to realize (required)
            security: ISIN (e.g. 'IE00B4L5Y983') or securityId UUID to restrict to (defaults to all)
            account_id: Restrict to a single securities account (defaults to all accounts)
            date: Sale date as ISO string, e.g. '2025-06-15' (defaults to today)
            tax_rate: Personal tax rate in percent (defaults to config or 26.375%)
            taxonomy: Preserve the current asset allocation using this taxonomy's classes (defaults to pure tax minimization)
            min_amount: Minimum gross sale per asset class; smaller classes are skipped (requires taxonomy)
        """
        portfolio = _ensure_fresh_portfolio()
        sell_date, effective_tax_rate = _sell_defaults(date, tax_rate)
        tax_csv_data = load_prepaid_tax_data(config.tax.files, portfolio)
        security_id = _resolve_security(portfolio, security) if security else None

        result = prepare_share_sell_df(
            portfolio, config, sell_date, effective_tax_rate,
            security_id, account_id, target_net=target_net,
            taxonomy=taxonomy, min_amount=min_amount, tax_csv_data=tax_csv_data
        )
        return _clean_records(result) if not result.empty else []

    return mcp


def start_mcp(ctx: typer.Context) -> None:
    """Start MCP server for AI client access to portfolio data."""
    file_path = cast(Path, ctx.obj.source_file)
    config = cast(Config, ctx.obj.config)

    mcp = create_mcp_server(file_path, config)
    mcp.run(transport='stdio')
