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
from typing import Any

import pandas as pd
from pandera.typing import DataFrame

from pp_terminal.data.filters import filter_by_type
from pp_terminal.data.tax import calculate_prepaid_tax_per_lot
from pp_terminal.domain.schemas import TransactionType, Money, TransactionSchema, TaxPaidSchema, TaxLotSchema, TaxLotSellSchema, Percent
from pp_terminal.domain.sell_strategy import FixedSharesStrategy

log = logging.getLogger(__name__)

def _filter_purchase_transactions(transactions: DataFrame[TransactionSchema]) -> DataFrame[TransactionSchema]:
    valid_purchases = transactions.pipe(filter_by_type, transaction_types=[TransactionType.BUY, TransactionType.DELIVERY_INBOUND]).sort_index(level='date')
    valid_purchases = valid_purchases[valid_purchases['shares'] > 0].copy()

    return TransactionSchema.validate(valid_purchases)


def _build_transfer_in_lookup(transfer_in_transactions: DataFrame[TransactionSchema]) -> dict[tuple[object, ...], str]:
    lookup: dict[tuple[object, ...], str] = {}
    for (date, dest_account_id, security_id), row in transfer_in_transactions.iterrows():
        key = (date, security_id, round(float(row['shares']), 4))
        lookup[key] = dest_account_id
    return lookup


def _consume_lots_fifo(
        remaining_lots: list[dict[str, Any]],
        account_id: str,
        shares_to_match: float,
        dest_account_id: str | None,
) -> tuple[float, list[dict[str, Any]]]:
    transferred_lots: list[dict[str, Any]] = []
    for lot in remaining_lots:
        if shares_to_match <= 0:
            break
        if lot['accountId'] != account_id:
            continue
        lot_shares = lot['shares']
        shares_from_lot = min(shares_to_match, lot_shares)
        new_shares = lot_shares - shares_from_lot
        shares_to_match -= shares_from_lot

        if dest_account_id and lot_shares > 0:
            ratio = shares_from_lot / lot_shares
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
        cost.
    """
    lots = _filter_purchase_transactions(transactions)
    lots['purchasePrice'] = lots['amount'].abs() / lots['shares']  # save actual market price per share
    lots['fees'] = lots['fees'].fillna(0)
    lots = lots.rename(columns={'amount': 'costBasis'})

    if lots.empty:
        return TaxLotSchema.validate(lots)

    sell_transactions = transactions.pipe(filter_by_type, transaction_types=[TransactionType.SELL, TransactionType.DELIVERY_OUTBOUND])
    transfer_out_transactions = transactions.pipe(filter_by_type, transaction_types=[TransactionType.TRANSFER_OUT])
    transfer_in_transactions = transactions.pipe(filter_by_type, transaction_types=[TransactionType.TRANSFER_IN])

    transfer_in_lookup = _build_transfer_in_lookup(transfer_in_transactions) if not transfer_in_transactions.empty else {}

    outgoing_frames = [f for f in [sell_transactions, transfer_out_transactions] if not f.empty]
    if not outgoing_frames:
        return TaxLotSchema.validate(lots)
    all_outgoing = pd.concat(outgoing_frames).sort_index(level='date') if len(outgoing_frames) > 1 else outgoing_frames[0].sort_index(level='date')

    remaining_lots = lots.reset_index().to_dict('records')

    for (_date, account_id, _security_id), row in all_outgoing.iterrows():
        is_transfer_out = str(row['type']) == TransactionType.TRANSFER_OUT.name
        shares_to_sell = float(row['shares'])

        dest_account_id = transfer_in_lookup.get((_date, _security_id, round(shares_to_sell, 4))) if is_transfer_out else None
        if is_transfer_out and dest_account_id is None:
            log.warning('No matching TRANSFER_IN for TRANSFER_OUT of %.4f shares of %s on %s — lots will be dropped', shares_to_sell, _security_id, _date)

        unmatched, transferred_lots = _consume_lots_fifo(remaining_lots, account_id, shares_to_sell, dest_account_id)
        if is_transfer_out:
            remaining_lots.extend(transferred_lots)

        if unmatched > 0.0001:  # Allow small floating point errors
            log.warning('Sale/transfer of %.8f shares for security %s could not be fully matched to purchase lots', unmatched, _security_id)

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


def enrich_fifo_lots(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        transactions: DataFrame[TransactionSchema],
        sell_date: datetime,
        sell_price: Money,
        tax_rate: Percent,
        tax_csv_data: DataFrame[TaxPaidSchema] | None = None,
        exempt_rate: Percent = 0.0
) -> DataFrame[TaxLotSellSchema]:
    """Compute all sell metrics for remaining FIFO lots assuming full lot sale."""
    df = _get_remaining_lots_after_fifo_matching(transactions)
    if df.empty:
        return TaxLotSellSchema.empty()

    df = TaxLotSchema.validate(df)
    df['salePrice'] = sell_price
    df['exemptRate'] = exempt_rate
    df['deemedIncome'] = calculate_prepaid_tax_per_lot(df, sell_date, tax_csv_data).values

    df['feePerShare'] = df['fees'].fillna(0) / df['shares']
    df['deemedIncomePerShare'] = df['deemedIncome'] / df['shares']

    df = _compute_sell_metrics(df, tax_rate)
    return df


def finalize_sell_lots(lots: DataFrame[TaxLotSellSchema], tax_rate: Percent) -> DataFrame[TaxLotSellSchema]:
    """Recalculate sell metrics after a strategy has adjusted shares."""
    df = lots.copy()
    df['fees'] = df['feePerShare'] * df['shares']
    df['deemedIncome'] = df['deemedIncomePerShare'] * df['shares']
    df = _compute_sell_metrics(df, tax_rate)
    return df


def calculate_fifo_sell(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        transactions: DataFrame[TransactionSchema],
        sell_date: datetime,
        sell_price: Money,
        tax_rate: Percent,
        shares_to_sell: float | None = None,
        tax_csv_data: DataFrame[TaxPaidSchema] | None = None,
        exempt_rate: Percent = 0.0
) -> DataFrame[TaxLotSellSchema]:
    """Calculate FIFO lots for shares being sold, including prepaid tax calculations."""
    df = enrich_fifo_lots(transactions, sell_date, sell_price, tax_rate, tax_csv_data, exempt_rate)
    if df.empty:
        return TaxLotSellSchema.empty()

    if shares_to_sell is not None:
        df = FixedSharesStrategy(shares_to_sell).select_lots(df)
        df = finalize_sell_lots(df, tax_rate)

    return TaxLotSellSchema.validate(df)


def calculate_total_cost_basis(transactions: DataFrame[TransactionSchema]) -> Money:
    """
    Calculate the cost basis of currently held shares for a security, i.e. what did I originally pay for the shares I currently hold?
    @link https://www.investopedia.com/terms/c/costbasis.asp
    """
    df = _get_remaining_lots_after_fifo_matching(transactions)
    df = _calculate_cost_basis(df)

    return Money(df['costBasis'].abs().sum())
