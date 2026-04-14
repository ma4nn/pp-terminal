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

from pp_terminal.commands.simulate_share_sell import prepare_share_sell_df
from pp_terminal.commands.view_accounts import prepare_accounts_df
from pp_terminal.commands.view_securities import prepare_securities_df
from pp_terminal.commands.view_taxonomies import prepare_taxonomies_df
from pp_terminal.data.filters import clean_for_display
from pp_terminal.data.tax import load_prepaid_tax_data
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import AccountType
from pp_terminal.data.pp_portfolio_builder import CachedPpPortfolioBuilder
from pp_terminal.exceptions import InputError
from pp_terminal.utils.cache import checksum
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.domain.vap import calculate_vap, get_base_rate_for_year, add_account_balances
from pp_terminal.data.xml_writer import PpXmlWriter
from pp_terminal.utils.config import Config, get_tax_rate, get_exempt_rate, get_exempt_rate_attribute, get_tax_files

log = logging.getLogger(__name__)
_MCP_NAME = "pp-mcp"
_MCP_INSTRUCTIONS = """Portfolio Performance analytics server for a single portfolio XML file.

Workflow:
1. Use query_securities or query_accounts to discover holdings, IDs, and balances.
2. Use the appropriate analysis or import tool (see tool selection guide below).

Tool selection guide:
- "Show my holdings" / "What securities do I have?" → query_securities
- "Show my accounts" / "What is my cash balance?" → query_accounts
- "What taxonomies exist?" / "Show asset allocation categories" → portfolio://taxonomies resource
- "What if I sell everything?" / "Total tax on my portfolio?" → simulate_sell_all
- "I need X EUR after tax" / "Sell to get X net" / "Minimize taxes for X amount" → simulate_sell_target_net
- "What if I sell N shares of X?" → simulate_sell_shares
- "Show FIFO lots for X" / "Purchase history for X" → query_fifo_lots
- "Calculate Vorabpauschale" / "VAP for year X" → simulate_vap
- "Add a new security/ETF" → import_security
- "Record a buy/purchase" → import_buy
- "Record a sale" → import_sell
- "Record a dividend" → import_dividend
- "Record a deposit/withdrawal" → import_deposit / import_withdrawal
- "Record a stock split" → import_stock_split
- "Transfer cash between accounts" → import_account_transfer
- "Record share delivery" → import_delivery_inbound / import_delivery_outbound
- "Record interest payment" → import_interest

The sell/FIFO tools accept a 'security' parameter that can be either an ISIN (e.g. 'IE00B4L5Y983')
or an internal securityId UUID. Prefer ISIN when available.

All dates are ISO format strings (e.g. '2025-06-15'). All monetary amounts are in the security's
native currency. Tax rates are in percent (e.g. 26.375 for German Abgeltungssteuer + Soli).

Import tools modify the Portfolio Performance XML file directly. A .bak backup is always created
before writing. After any import, the in-memory portfolio is automatically refreshed.
Amounts for import tools are in the currency's base unit (e.g. EUR, not cents).
"""

_SUMMARY_COLUMNS = ['securityName', 'currency', 'shares', 'salePrice', 'costBasis',
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
    builder = CachedPpPortfolioBuilder(config=config)
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
        df = prepare_securities_df(portfolio, config, by_date, active_only, in_stock_only)
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
        df = prepare_accounts_df(portfolio, config, by_date, parsed_type)
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
        tax_rate_pct = tax_rate if tax_rate is not None else get_tax_rate(config)

        snapshot_begin = PortfolioSnapshot(portfolio, datetime(effective_year, 1, 2))
        snapshot_end = PortfolioSnapshot(portfolio, datetime(effective_year, 12, 31))

        result = calculate_vap(
            snapshot_begin, snapshot_end,
            base_rate_pct, tax_rate_pct, exempt_rate_percent=get_exempt_rate(config), exempt_rate_attr_uuid=get_exempt_rate_attribute(config)
        )

        if result.empty:
            return []

        result = add_account_balances(result, portfolio, snapshot_end)
        return _clean_records(result)

    def _sell_defaults(date: str | None, tax_rate: float | None) -> tuple[datetime, float]:
        sell_date = datetime.fromisoformat(date) if date else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        effective_tax_rate = tax_rate if tax_rate is not None else get_tax_rate(config)
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

        Each row is one purchase lot (FIFO order) showing: securityName, purchase date, shares,
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
        tax_csv_data = load_prepaid_tax_data(get_tax_files(config), portfolio)
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
        Each row is one security showing: securityName, currency, shares, salePrice, costBasis,
        fees, grossProceeds, capitalGain, deemedIncome (Vorabpauschale), taxableGain, totalTax,
        netProceeds. For individual FIFO lot detail, use query_fifo_lots instead.
        To target a specific net amount (e.g. 'I need 1000 EUR after tax'), use simulate_sell_target_net instead.

        Args:
            date: Sale date as ISO string, e.g. '2025-06-15' (defaults to today)
            tax_rate: Personal tax rate in percent (defaults to config or 26.375%)
        """
        portfolio = _ensure_fresh_portfolio()
        sell_date, effective_tax_rate = _sell_defaults(date, tax_rate)
        tax_csv_data = load_prepaid_tax_data(get_tax_files(config), portfolio)

        lots = prepare_share_sell_df(
            portfolio, config, sell_date, effective_tax_rate,
            None, None, tax_csv_data=tax_csv_data
        )
        if lots.empty:
            return []

        sum_cols = ['shares', 'costBasis', 'fees', 'grossProceeds', 'capitalGain',
                    'deemedIncome', 'taxableGain', 'totalTax', 'netProceeds']
        agg = {col: 'sum' for col in sum_cols}
        agg['salePrice'] = 'first'

        summary = lots.groupby(['securityName', 'currency'], sort=False).agg(agg).reset_index()
        return _clean_records(summary[_SUMMARY_COLUMNS])

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
        tax_csv_data = load_prepaid_tax_data(get_tax_files(config), portfolio)
        security_id = _resolve_security(portfolio, security)

        result = prepare_share_sell_df(
            portfolio, config, sell_date, effective_tax_rate,
            security_id, account_id, shares=shares, price=price, tax_csv_data=tax_csv_data
        )
        return _clean_records(result) if not result.empty else []

    @mcp.tool()
    def simulate_sell_target_net(
        target_net: float,
        security: str | None = None,
        account_id: str | None = None,
        date: str | None = None,
        tax_rate: float | None = None,
    ) -> list[dict[str, Any]]:
        """Find the shares to sell to achieve a target net proceeds amount with minimal taxes.

        Use this to answer questions like 'I need 1000 EUR after tax, what should I sell?',
        'which securities to sell to get 5000 EUR with minimal taxes?', or
        'how to realize X EUR net from my portfolio?'.
        Selects lots across securities/accounts to minimize total tax while reaching the target.
        Uses latest known prices. Each row is one FIFO lot showing: securityName, purchase date,
        shares to sell, currency, purchasePrice, costBasis, fees, salePrice, grossProceeds,
        capitalGain, deemedIncome (Vorabpauschale), taxableGain, totalTax, netProceeds.

        Args:
            target_net: Target net proceeds to realize (required)
            security: ISIN (e.g. 'IE00B4L5Y983') or securityId UUID to restrict to (defaults to all)
            account_id: Restrict to a single securities account (defaults to all accounts)
            date: Sale date as ISO string, e.g. '2025-06-15' (defaults to today)
            tax_rate: Personal tax rate in percent (defaults to config or 26.375%)
        """
        portfolio = _ensure_fresh_portfolio()
        sell_date, effective_tax_rate = _sell_defaults(date, tax_rate)
        tax_csv_data = load_prepaid_tax_data(get_tax_files(config), portfolio)
        security_id = _resolve_security(portfolio, security) if security else None

        result = prepare_share_sell_df(
            portfolio, config, sell_date, effective_tax_rate,
            security_id, account_id, target_net=target_net, tax_csv_data=tax_csv_data
        )
        return _clean_records(result) if not result.empty else []

    # ─── Import tools (write to XML) ───

    def _writer() -> PpXmlWriter:
        return PpXmlWriter(file_path)

    def _invalidate_portfolio() -> None:
        state['checksum'] = ''

    @mcp.tool()
    def delete_transaction(
        transaction_uuid: str,
    ) -> dict[str, Any]:
        """Delete a transaction by its UUID.

        Handles cross-entry cleanup for transfer transactions (removes both sides).
        Use query_accounts or query_securities to find transaction UUIDs.

        Args:
            transaction_uuid: The UUID of the transaction to delete
        """
        result = _writer().delete_transaction(transaction_uuid)
        _invalidate_portfolio()
        return result

    @mcp.tool()
    def import_security(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        name: str,
        currency: str,
        isin: str | None = None,
        wkn: str | None = None,
        ticker: str | None = None,
        feed: str = 'MANUAL',
    ) -> dict[str, str]:
        """Add a new security definition to the portfolio.

        Args:
            name: Display name (e.g. 'Vanguard FTSE All-World')
            currency: Trading currency (e.g. 'EUR', 'USD')
            isin: ISIN identifier (e.g. 'IE00B4L5Y983')
            wkn: WKN identifier
            ticker: Ticker symbol (e.g. 'VWCE.DE')
            feed: Price feed source (defaults to 'MANUAL')
        """
        sec_uuid = _writer().add_security(name, currency, isin=isin, wkn=wkn, ticker=ticker, feed=feed)
        _invalidate_portfolio()
        return {'securityId': sec_uuid, 'name': name, 'status': 'created'}

    @mcp.tool()
    def import_buy(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        security: str,
        account_id: str,
        date: str,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record a BUY transaction (purchase shares via a cash account).

        Creates the cross-linked portfolio + account transaction pair.
        Use query_accounts(account_type='DEPOSIT') to find cash account IDs.
        The portfolio linked to this cash account is found automatically.

        Args:
            security: ISIN (e.g. 'IE00B4L5Y983') or securityId UUID
            account_id: Cash account UUID (from query_accounts)
            date: Transaction date as ISO string (e.g. '2025-01-15')
            shares: Number of shares purchased
            amount: Total transaction amount in base currency units (e.g. 1000.50 for EUR 1000.50)
            currency: Transaction currency (e.g. 'EUR')
            fees: Transaction fees in currency units (default 0)
            taxes: Transaction taxes in currency units (default 0)
            note: Optional transaction note
        """
        txn_uuid = _writer().add_buy(
            security, account_id, datetime.fromisoformat(date),
            shares, amount, currency, fees=fees, taxes=taxes, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'BUY', 'status': 'created'}

    @mcp.tool()
    def import_sell(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        security: str,
        account_id: str,
        date: str,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record a SELL transaction (sell shares, proceeds go to cash account).

        Args:
            security: ISIN or securityId UUID
            account_id: Cash account UUID
            date: Transaction date as ISO string (e.g. '2025-01-15')
            shares: Number of shares sold
            amount: Total proceeds in currency units (e.g. 1500.00)
            currency: Transaction currency (e.g. 'EUR')
            fees: Transaction fees (default 0)
            taxes: Transaction taxes (default 0)
            note: Optional transaction note
        """
        txn_uuid = _writer().add_sell(
            security, account_id, datetime.fromisoformat(date),
            shares, amount, currency, fees=fees, taxes=taxes, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'SELL', 'status': 'created'}

    @mcp.tool()
    def import_dividend(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        security: str,
        account_id: str,
        date: str,
        amount: float,
        currency: str,
        shares: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record a DIVIDEND payment received into a cash account.

        Args:
            security: ISIN or securityId UUID
            account_id: Cash account UUID where dividend was received
            date: Payment date as ISO string
            amount: Net dividend amount received in currency units
            currency: Payment currency (e.g. 'EUR')
            shares: Number of shares at ex-dividend date (informational)
            taxes: Withholding taxes in currency units (default 0)
            note: Optional note
        """
        txn_uuid = _writer().add_dividend(
            security, account_id, datetime.fromisoformat(date),
            amount, currency, shares=shares, taxes=taxes, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'DIVIDENDS', 'status': 'created'}

    @mcp.tool()
    def import_deposit(
        account_id: str,
        date: str,
        amount: float,
        currency: str,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record a cash DEPOSIT into an account.

        Args:
            account_id: Cash account UUID
            date: Deposit date as ISO string
            amount: Deposit amount in currency units
            currency: Currency (e.g. 'EUR')
            note: Optional note
        """
        txn_uuid = _writer().add_deposit(
            account_id, datetime.fromisoformat(date), amount, currency, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'DEPOSIT', 'status': 'created'}

    @mcp.tool()
    def import_withdrawal(
        account_id: str,
        date: str,
        amount: float,
        currency: str,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record a cash WITHDRAWAL (removal) from an account.

        Args:
            account_id: Cash account UUID
            date: Withdrawal date as ISO string
            amount: Withdrawal amount in currency units
            currency: Currency (e.g. 'EUR')
            note: Optional note
        """
        txn_uuid = _writer().add_withdrawal(
            account_id, datetime.fromisoformat(date), amount, currency, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'REMOVAL', 'status': 'created'}

    @mcp.tool()
    def import_stock_split(
        security: str,
        date: str,
        ratio: str,
    ) -> dict[str, str]:
        """Record a STOCK_SPLIT event on a security.

        Args:
            security: ISIN or securityId UUID
            date: Split date as ISO string
            ratio: Split ratio (e.g. '4:1' for a 4-for-1 split, '1:10' for a reverse split)
        """
        _writer().add_stock_split(security, datetime.fromisoformat(date), ratio)
        _invalidate_portfolio()
        return {'security': security, 'type': 'STOCK_SPLIT', 'ratio': ratio, 'status': 'created'}

    @mcp.tool()
    def import_delivery_inbound(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        security: str,
        portfolio_id: str,
        date: str,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record a DELIVERY_INBOUND — shares transferred into a portfolio without a cash leg.

        Use for share transfers from another broker, inheritance, gifts, etc.

        Args:
            security: ISIN or securityId UUID
            portfolio_id: Securities account (portfolio) UUID
            date: Delivery date as ISO string
            shares: Number of shares delivered
            amount: Market value at delivery in currency units
            currency: Currency (e.g. 'EUR')
            fees: Fees (default 0)
            taxes: Taxes (default 0)
            note: Optional note
        """
        txn_uuid = _writer().add_delivery_inbound(
            security, portfolio_id, datetime.fromisoformat(date),
            shares, amount, currency, fees=fees, taxes=taxes, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'DELIVERY_INBOUND', 'status': 'created'}

    @mcp.tool()
    def import_delivery_outbound(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        security: str,
        portfolio_id: str,
        date: str,
        shares: float,
        amount: float,
        currency: str,
        fees: float = 0.0,
        taxes: float = 0.0,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record a DELIVERY_OUTBOUND — shares removed from a portfolio without a cash leg.

        Use for share transfers to another broker, gifts out, etc.

        Args:
            security: ISIN or securityId UUID
            portfolio_id: Securities account (portfolio) UUID
            date: Delivery date as ISO string
            shares: Number of shares removed
            amount: Market value at removal in currency units
            currency: Currency (e.g. 'EUR')
            fees: Fees (default 0)
            taxes: Taxes (default 0)
            note: Optional note
        """
        txn_uuid = _writer().add_delivery_outbound(
            security, portfolio_id, datetime.fromisoformat(date),
            shares, amount, currency, fees=fees, taxes=taxes, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'DELIVERY_OUTBOUND', 'status': 'created'}

    @mcp.tool()
    def import_interest(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        account_id: str,
        date: str,
        amount: float,
        currency: str,
        taxes: float = 0.0,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record an INTEREST payment received on a cash account.

        Args:
            account_id: Cash account UUID
            date: Payment date as ISO string
            amount: Interest amount in currency units
            currency: Currency (e.g. 'EUR')
            taxes: Withholding taxes (default 0)
            note: Optional note
        """
        txn_uuid = _writer().add_interest(
            account_id, datetime.fromisoformat(date),
            amount, currency, taxes=taxes, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'INTEREST', 'status': 'created'}

    @mcp.tool()
    def import_fees(
        account_id: str,
        date: str,
        amount: float,
        currency: str,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record a FEES (fee payment) on a cash account.

        Args:
            account_id: Cash account UUID
            date: Fee date as ISO string
            amount: Fee amount in currency units
            currency: Currency (e.g. 'EUR')
            note: Optional note
        """
        txn_uuid = _writer().add_fees(
            account_id, datetime.fromisoformat(date),
            amount, currency, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'FEES', 'status': 'created'}

    @mcp.tool()
    def import_taxes(
        account_id: str,
        date: str,
        amount: float,
        currency: str,
        note: str | None = None,
    ) -> dict[str, str]:
        """Record a TAXES (tax payment) on a cash account.

        Args:
            account_id: Cash account UUID
            date: Tax payment date as ISO string
            amount: Tax amount in currency units
            currency: Currency (e.g. 'EUR')
            note: Optional note
        """
        txn_uuid = _writer().add_taxes(
            account_id, datetime.fromisoformat(date),
            amount, currency, note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'TAXES', 'status': 'created'}

    @mcp.tool()
    def import_account_transfer(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        from_account_id: str,
        to_account_id: str,
        date: str,
        amount: float,
        currency: str,
        to_amount: float | None = None,
        to_currency: str | None = None,
        note: str | None = None,
    ) -> dict[str, str]:
        """Transfer cash between two deposit accounts.

        For same-currency transfers, provide amount and currency.
        For cross-currency (FX) transfers, also provide to_amount and to_currency
        for the destination side.

        Args:
            from_account_id: Source cash account UUID
            to_account_id: Destination cash account UUID
            date: Transfer date as ISO string
            amount: Source amount in currency units
            currency: Source currency (e.g. 'CHF')
            to_amount: Destination amount (for cross-currency transfers)
            to_currency: Destination currency (for cross-currency transfers)
            note: Optional note
        """
        txn_uuid = _writer().add_account_transfer(
            from_account_id, to_account_id, datetime.fromisoformat(date),
            amount, currency, to_amount=to_amount, to_currency=to_currency,
            note=note,
        )
        _invalidate_portfolio()
        return {'transactionId': txn_uuid, 'type': 'ACCOUNT_TRANSFER', 'status': 'created'}

    return mcp


def start_mcp(ctx: typer.Context) -> None:
    """Start MCP server for AI client access to portfolio data."""
    file_path = cast(Path, ctx.obj.source_file)
    config = cast(Config, ctx.obj.config)

    mcp = create_mcp_server(file_path, config)
    mcp.run(transport='stdio')
