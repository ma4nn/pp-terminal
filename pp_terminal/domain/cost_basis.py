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

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

import pandas as pd
from pandera.typing import DataFrame

from pp_terminal.data.filters import filter_by_type
from pp_terminal.data.tax import calculate_prepaid_tax_per_lot
from pp_terminal.domain.schemas import TransactionType, Money, TransactionSchema, TaxPaidSchema, TaxLotSchema, TaxLotSellSchema, Percent
from pp_terminal.domain.sell_strategy import FixedSharesStrategy

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SellContext:
    """Parameters describing a simulated sale: the date, price per share, and the applicable tax rules."""
    sell_date: datetime
    sell_price: Money
    tax_rate: Percent
    exempt_rate: Percent = 0.0
    tax_csv_data: DataFrame[TaxPaidSchema] | None = None


def _filter_purchase_transactions(transactions: DataFrame[TransactionSchema]) -> DataFrame[TransactionSchema]:
    valid_purchases = transactions.pipe(filter_by_type, transaction_types=[TransactionType.BUY, TransactionType.DELIVERY_INBOUND]).sort_index(level='date')
    valid_purchases = valid_purchases[valid_purchases['shares'] > 0].copy()

    return TransactionSchema.validate(valid_purchases)


def _transfer_target_account(transfer_out_row: pd.Series) -> str | None:
    """Destination securities account of a depot transfer, from Portfolio Performance's cross-entry link."""
    target = transfer_out_row.get('transferTargetAccount')
    if target is None or pd.isna(target) or str(target).strip() == '':
        return None
    return str(target)


def _consume_lots_fifo(
        remaining_lots: list[dict[str, Any]],
        account_id: str,
        security_id: str,
        shares_to_match: float,
        dest_account_id: str | None,
) -> tuple[float, list[dict[str, Any]]]:
    transferred_lots: list[dict[str, Any]] = []
    for lot in remaining_lots:
        if shares_to_match <= 0:
            break
        if lot['accountId'] != account_id or lot['securityId'] != security_id:
            continue
        lot_shares = lot['shares']
        shares_from_lot = min(shares_to_match, lot_shares)
        new_shares = lot_shares - shares_from_lot
        shares_to_match -= shares_from_lot

        if dest_account_id and lot_shares > 0:
            ratio = shares_from_lot / lot_shares
            # costBasis is left un-prorated here (still the source lot's full amount); every consumer
            # recomputes it from purchasePrice * shares + fees before reading it (see _calculate_cost_basis)
            transferred_lots.append({**lot, 'accountId': dest_account_id, 'shares': shares_from_lot, 'fees': lot['fees'] * ratio})

        if lot_shares > 0:
            lot['fees'] = lot['fees'] * (new_shares / lot_shares)
        lot['shares'] = new_shares

    return shares_to_match, transferred_lots


def _get_remaining_lots_after_fifo_matching(transactions: DataFrame[TransactionSchema]) -> DataFrame[TaxLotSchema]:  # pylint: disable=too-many-locals
    """
    Match all sell transactions to purchase lots using FIFO and return remaining lots.

    Returns:
        DataFrame of remaining lots after sales are matched.
        Each lot's 'shares' field represents remaining quantity.
        Exhausted lots (shares = 0) are removed.

    Implementation Note:
        FIFO matching is inherently sequential (each sale consumes lots in order,
        affecting state for the next sale). While the function accepts/returns DataFrames
        for schema validation and interface consistency, internally we use list of dicts
        for ~10x faster mutation during the matching algorithm. DataFrame .loc[] access
        has significant overhead that doesn't add value for sequential state updates.

        Depot transfers (TRANSFER_OUT → TRANSFER_IN pairs, as recorded by Portfolio
        Performance's "Wertpapierübertrag" feature) are handled by moving FIFO lots from the
        source account to the destination account while preserving the original acquisition
        cost. The destination account is taken from the authoritative cross-entry link that
        Portfolio Performance stores for each transfer (TransactionSchema.transferTargetAccount).
    """
    lots = _filter_purchase_transactions(transactions)
    lots['purchasePrice'] = lots['amount'].abs() / lots['shares']  # save actual market price per share
    lots['fees'] = lots['fees'].fillna(0)
    lots = lots.rename(columns={'amount': 'costBasis'})

    if lots.empty:
        return TaxLotSchema.validate(lots)

    sell_transactions = transactions.pipe(filter_by_type, transaction_types=[TransactionType.SELL, TransactionType.DELIVERY_OUTBOUND])
    transfer_out_transactions = transactions.pipe(filter_by_type, transaction_types=[TransactionType.TRANSFER_OUT])

    outgoing_frames = [f for f in [sell_transactions, transfer_out_transactions] if not f.empty]
    if not outgoing_frames:
        return TaxLotSchema.validate(lots)
    all_outgoing = pd.concat(outgoing_frames).sort_index(level='date')

    remaining_lots = lots.reset_index().to_dict('records')

    for (txn_date, account_id, security_id), row in all_outgoing.iterrows():
        is_transfer_out = row['type'] == TransactionType.TRANSFER_OUT.name
        shares_to_sell = float(row['shares'])

        dest_account_id = _transfer_target_account(row) if is_transfer_out else None
        if is_transfer_out and dest_account_id is None:
            # no authoritative cross-entry link (corrupt or stale-cache data): keep the lots in the
            # source account so security-level cost basis stays correct — only account attribution is stale
            log.warning('TRANSFER_OUT of %.4f shares of %s on %s has no linked destination account — keeping lots in the source account', shares_to_sell, security_id, txn_date)
            continue

        unmatched, transferred_lots = _consume_lots_fifo(remaining_lots, account_id, security_id, shares_to_sell, dest_account_id)
        if transferred_lots:
            remaining_lots.extend(transferred_lots)
            # transferred lots keep their original purchase date, so restore acquisition-date
            # order for FIFO matching of subsequent sells in the destination account
            remaining_lots.sort(key=lambda lot: lot['date'])

        if unmatched > 0.0001:  # Allow small floating point errors
            log.warning('Sale/transfer of %.8f shares for security %s could not be fully matched to purchase lots', unmatched, security_id)

    remaining_lots = [lot for lot in remaining_lots if lot['shares'] > 0.0001]
    if not remaining_lots:
        return TaxLotSchema.empty()

    return TaxLotSchema.validate(pd.DataFrame(remaining_lots).set_index(['date', 'accountId', 'securityId']))


def _calculate_cost_basis(df: DataFrame[TaxLotSchema]) -> DataFrame[TaxLotSchema]:
    df = df.copy()
    df['costBasis'] = df['purchasePrice'] * df['shares'] + df['fees'].fillna(0)
    return TaxLotSchema.validate(df)


def _compute_sell_metrics(df: DataFrame[TaxLotSellSchema], tax_rate: Percent) -> DataFrame[TaxLotSellSchema]:
    df = _calculate_cost_basis(df)
    df['grossProceeds'] = df['shares'] * df['salePrice']
    df['capitalGain'] = df['grossProceeds'] - df['costBasis']

    adjusted_gain = (df['capitalGain'] - df['deemedIncome']).clip(lower=0)
    df['taxableGain'] = (adjusted_gain * (1 - df['exemptRate'] / 100)).clip(lower=0)
    df['totalTax'] = (df['taxableGain'] * (tax_rate / 100.0)).clip(lower=0)
    df['netProceeds'] = df['grossProceeds'] - df['totalTax']
    df['netProceedsPerShare'] = df['netProceeds'] / df['shares']
    return df


def enrich_fifo_lots(transactions: DataFrame[TransactionSchema], ctx: SellContext) -> DataFrame[TaxLotSellSchema]:
    """Compute all sell metrics for remaining FIFO lots assuming full lot sale."""
    df = _get_remaining_lots_after_fifo_matching(transactions)
    if df.empty:
        return TaxLotSellSchema.empty()

    df = TaxLotSchema.validate(df)
    df['salePrice'] = ctx.sell_price
    df['exemptRate'] = ctx.exempt_rate
    df['deemedIncome'] = calculate_prepaid_tax_per_lot(df, ctx.sell_date, ctx.tax_csv_data).values

    df['feePerShare'] = df['fees'].fillna(0) / df['shares']
    df['deemedIncomePerShare'] = df['deemedIncome'] / df['shares']

    df = _compute_sell_metrics(df, ctx.tax_rate)
    return df


def finalize_sell_lots(lots: DataFrame[TaxLotSellSchema], tax_rate: Percent) -> DataFrame[TaxLotSellSchema]:
    """Recalculate sell metrics after a strategy has adjusted shares."""
    df = lots.copy()
    df['fees'] = df['feePerShare'] * df['shares']
    df['deemedIncome'] = df['deemedIncomePerShare'] * df['shares']
    df = _compute_sell_metrics(df, tax_rate)
    return df


def calculate_fifo_sell(
        transactions: DataFrame[TransactionSchema],
        ctx: SellContext,
        shares_to_sell: float | None = None
) -> DataFrame[TaxLotSellSchema]:
    """Calculate FIFO lots for shares being sold, including prepaid tax calculations."""
    df = enrich_fifo_lots(transactions, ctx)
    if df.empty:
        return TaxLotSellSchema.empty()

    if shares_to_sell is not None:
        df = FixedSharesStrategy(shares_to_sell).select_lots(df)
        df = finalize_sell_lots(df, ctx.tax_rate)

    return TaxLotSellSchema.validate(df)


def calculate_total_cost_basis(transactions: DataFrame[TransactionSchema]) -> Money:
    """
    Calculate the cost basis of currently held shares for a security, i.e. what did I originally pay for the shares I currently hold?
    @link https://www.investopedia.com/terms/c/costbasis.asp
    """
    df = _get_remaining_lots_after_fifo_matching(transactions)
    df = _calculate_cost_basis(df)

    return Money(df['costBasis'].abs().sum())
