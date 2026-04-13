"""
    Copyright (C) 2025-26 Daniel Gehriger

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
from pathlib import Path
from typing import Optional, cast

import typer
from rich.console import Console
from typing_extensions import Annotated

from pp_terminal.data.xml_writer import PpXmlWriter

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)


def _get_writer(ctx: typer.Context) -> PpXmlWriter:
    source_file = cast(Path, ctx.obj.source_file)
    return PpXmlWriter(source_file)


@app.command(name="security")
def import_security(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Security display name")],
    currency: Annotated[str, typer.Argument(help="Trading currency (e.g. EUR, USD)")],
    isin: Annotated[Optional[str], typer.Option(help="ISIN identifier")] = None,
    wkn: Annotated[Optional[str], typer.Option(help="WKN identifier")] = None,
    ticker: Annotated[Optional[str], typer.Option(help="Ticker symbol")] = None,
    feed: Annotated[str, typer.Option(help="Price feed source")] = "MANUAL",
) -> None:
    """Add a new security definition."""
    writer = _get_writer(ctx)
    sec_uuid = writer.add_security(name, currency, isin=isin, wkn=wkn, ticker=ticker, feed=feed)
    console.print(f"[green]Created security[/green] '{name}' (uuid={sec_uuid})")


@app.command(name="buy")
def import_buy(
    ctx: typer.Context,
    security: Annotated[str, typer.Argument(help="ISIN or security UUID")],
    account_id: Annotated[str, typer.Argument(help="Cash account UUID")],
    date: Annotated[str, typer.Argument(help="Transaction date (ISO format)")],
    shares: Annotated[float, typer.Argument(help="Number of shares")],
    amount: Annotated[float, typer.Argument(help="Total amount in currency units")],
    currency: Annotated[str, typer.Argument(help="Currency (e.g. EUR)")],
    fees: Annotated[float, typer.Option(help="Transaction fees")] = 0.0,
    taxes: Annotated[float, typer.Option(help="Transaction taxes")] = 0.0,
    note: Annotated[Optional[str], typer.Option(help="Transaction note")] = None,
) -> None:
    """Record a BUY transaction."""
    writer = _get_writer(ctx)
    txn_uuid = writer.add_buy(
        security, account_id, datetime.fromisoformat(date),
        shares, amount, currency, fees=fees, taxes=taxes, note=note,
    )
    console.print(f"[green]Created BUY[/green] {shares} shares (uuid={txn_uuid})")


@app.command(name="sell")
def import_sell(
    ctx: typer.Context,
    security: Annotated[str, typer.Argument(help="ISIN or security UUID")],
    account_id: Annotated[str, typer.Argument(help="Cash account UUID")],
    date: Annotated[str, typer.Argument(help="Transaction date (ISO format)")],
    shares: Annotated[float, typer.Argument(help="Number of shares")],
    amount: Annotated[float, typer.Argument(help="Total proceeds in currency units")],
    currency: Annotated[str, typer.Argument(help="Currency (e.g. EUR)")],
    fees: Annotated[float, typer.Option(help="Transaction fees")] = 0.0,
    taxes: Annotated[float, typer.Option(help="Transaction taxes")] = 0.0,
    note: Annotated[Optional[str], typer.Option(help="Transaction note")] = None,
) -> None:
    """Record a SELL transaction."""
    writer = _get_writer(ctx)
    txn_uuid = writer.add_sell(
        security, account_id, datetime.fromisoformat(date),
        shares, amount, currency, fees=fees, taxes=taxes, note=note,
    )
    console.print(f"[green]Created SELL[/green] {shares} shares (uuid={txn_uuid})")


@app.command(name="dividend")
def import_dividend(
    ctx: typer.Context,
    security: Annotated[str, typer.Argument(help="ISIN or security UUID")],
    account_id: Annotated[str, typer.Argument(help="Cash account UUID")],
    date: Annotated[str, typer.Argument(help="Payment date (ISO format)")],
    amount: Annotated[float, typer.Argument(help="Net dividend amount")],
    currency: Annotated[str, typer.Argument(help="Currency (e.g. EUR)")],
    shares: Annotated[float, typer.Option(help="Shares at ex-dividend date")] = 0.0,
    taxes: Annotated[float, typer.Option(help="Withholding taxes")] = 0.0,
    note: Annotated[Optional[str], typer.Option(help="Transaction note")] = None,
) -> None:
    """Record a DIVIDEND payment."""
    writer = _get_writer(ctx)
    txn_uuid = writer.add_dividend(
        security, account_id, datetime.fromisoformat(date),
        amount, currency, shares=shares, taxes=taxes, note=note,
    )
    console.print(f"[green]Created DIVIDENDS[/green] {amount} {currency} (uuid={txn_uuid})")


@app.command(name="deposit")
def import_deposit(
    ctx: typer.Context,
    account_id: Annotated[str, typer.Argument(help="Cash account UUID")],
    date: Annotated[str, typer.Argument(help="Deposit date (ISO format)")],
    amount: Annotated[float, typer.Argument(help="Deposit amount")],
    currency: Annotated[str, typer.Argument(help="Currency (e.g. EUR)")],
    note: Annotated[Optional[str], typer.Option(help="Transaction note")] = None,
) -> None:
    """Record a cash DEPOSIT."""
    writer = _get_writer(ctx)
    txn_uuid = writer.add_deposit(
        account_id, datetime.fromisoformat(date), amount, currency, note=note,
    )
    console.print(f"[green]Created DEPOSIT[/green] {amount} {currency} (uuid={txn_uuid})")


@app.command(name="withdrawal")
def import_withdrawal(
    ctx: typer.Context,
    account_id: Annotated[str, typer.Argument(help="Cash account UUID")],
    date: Annotated[str, typer.Argument(help="Withdrawal date (ISO format)")],
    amount: Annotated[float, typer.Argument(help="Withdrawal amount")],
    currency: Annotated[str, typer.Argument(help="Currency (e.g. EUR)")],
    note: Annotated[Optional[str], typer.Option(help="Transaction note")] = None,
) -> None:
    """Record a cash WITHDRAWAL (removal)."""
    writer = _get_writer(ctx)
    txn_uuid = writer.add_withdrawal(
        account_id, datetime.fromisoformat(date), amount, currency, note=note,
    )
    console.print(f"[green]Created REMOVAL[/green] {amount} {currency} (uuid={txn_uuid})")


@app.command(name="stock-split")
def import_stock_split(
    ctx: typer.Context,
    security: Annotated[str, typer.Argument(help="ISIN or security UUID")],
    date: Annotated[str, typer.Argument(help="Split date (ISO format)")],
    ratio: Annotated[str, typer.Argument(help="Split ratio (e.g. '4:1')")],
) -> None:
    """Record a STOCK_SPLIT event on a security."""
    writer = _get_writer(ctx)
    writer.add_stock_split(security, datetime.fromisoformat(date), ratio)
    console.print(f"[green]Created STOCK_SPLIT[/green] {ratio} on {date}")


@app.command(name="delivery-in")
def import_delivery_inbound(
    ctx: typer.Context,
    security: Annotated[str, typer.Argument(help="ISIN or security UUID")],
    portfolio_id: Annotated[str, typer.Argument(help="Portfolio (securities account) UUID")],
    date: Annotated[str, typer.Argument(help="Delivery date (ISO format)")],
    shares: Annotated[float, typer.Argument(help="Number of shares")],
    amount: Annotated[float, typer.Argument(help="Market value at delivery")],
    currency: Annotated[str, typer.Argument(help="Currency (e.g. EUR)")],
    fees: Annotated[float, typer.Option(help="Fees")] = 0.0,
    taxes: Annotated[float, typer.Option(help="Taxes")] = 0.0,
    note: Annotated[Optional[str], typer.Option(help="Transaction note")] = None,
) -> None:
    """Record a DELIVERY_INBOUND (shares in without cash)."""
    writer = _get_writer(ctx)
    txn_uuid = writer.add_delivery_inbound(
        security, portfolio_id, datetime.fromisoformat(date),
        shares, amount, currency, fees=fees, taxes=taxes, note=note,
    )
    console.print(f"[green]Created DELIVERY_INBOUND[/green] {shares} shares (uuid={txn_uuid})")


@app.command(name="delivery-out")
def import_delivery_outbound(
    ctx: typer.Context,
    security: Annotated[str, typer.Argument(help="ISIN or security UUID")],
    portfolio_id: Annotated[str, typer.Argument(help="Portfolio (securities account) UUID")],
    date: Annotated[str, typer.Argument(help="Delivery date (ISO format)")],
    shares: Annotated[float, typer.Argument(help="Number of shares")],
    amount: Annotated[float, typer.Argument(help="Market value at delivery")],
    currency: Annotated[str, typer.Argument(help="Currency (e.g. EUR)")],
    fees: Annotated[float, typer.Option(help="Fees")] = 0.0,
    taxes: Annotated[float, typer.Option(help="Taxes")] = 0.0,
    note: Annotated[Optional[str], typer.Option(help="Transaction note")] = None,
) -> None:
    """Record a DELIVERY_OUTBOUND (shares out without cash)."""
    writer = _get_writer(ctx)
    txn_uuid = writer.add_delivery_outbound(
        security, portfolio_id, datetime.fromisoformat(date),
        shares, amount, currency, fees=fees, taxes=taxes, note=note,
    )
    console.print(f"[green]Created DELIVERY_OUTBOUND[/green] {shares} shares (uuid={txn_uuid})")


@app.command(name="interest")
def import_interest_cmd(
    ctx: typer.Context,
    account_id: Annotated[str, typer.Argument(help="Cash account UUID")],
    date: Annotated[str, typer.Argument(help="Payment date (ISO format)")],
    amount: Annotated[float, typer.Argument(help="Interest amount")],
    currency: Annotated[str, typer.Argument(help="Currency (e.g. EUR)")],
    taxes: Annotated[float, typer.Option(help="Withholding taxes")] = 0.0,
    note: Annotated[Optional[str], typer.Option(help="Transaction note")] = None,
) -> None:
    """Record an INTEREST payment."""
    writer = _get_writer(ctx)
    txn_uuid = writer.add_interest(
        account_id, datetime.fromisoformat(date),
        amount, currency, taxes=taxes, note=note,
    )
    console.print(f"[green]Created INTEREST[/green] {amount} {currency} (uuid={txn_uuid})")


@app.command(name="transfer")
def import_transfer(
    ctx: typer.Context,
    from_account_id: Annotated[str, typer.Argument(help="Source cash account UUID")],
    to_account_id: Annotated[str, typer.Argument(help="Destination cash account UUID")],
    date: Annotated[str, typer.Argument(help="Transfer date (ISO format)")],
    amount: Annotated[float, typer.Argument(help="Transfer amount")],
    currency: Annotated[str, typer.Argument(help="Currency (e.g. EUR)")],
    note: Annotated[Optional[str], typer.Option(help="Transaction note")] = None,
) -> None:
    """Transfer cash between two deposit accounts."""
    writer = _get_writer(ctx)
    txn_uuid = writer.add_account_transfer(
        from_account_id, to_account_id, datetime.fromisoformat(date),
        amount, currency, note=note,
    )
    console.print(f"[green]Created ACCOUNT_TRANSFER[/green] {amount} {currency} (uuid={txn_uuid})")
