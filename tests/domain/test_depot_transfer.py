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
# pylint: disable=duplicate-code

import logging
from datetime import datetime

import pandas as pd
import pytest
from _pytest.logging import LogCaptureFixture

from pp_terminal.commands.simulate_share_sell import prepare_share_sell_df
from pp_terminal.domain.cost_basis import calculate_total_cost_basis, enrich_fifo_lots
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import AccountType, TransactionType, TaxPaidSchema

TAX_RATE = 26.375
SELL_DATE = datetime(2024, 12, 31)

# transferTargetAccount carries Portfolio Performance's authoritative cross-entry link: the destination
# securities account of a TRANSFER_OUT. It is None for every other transaction type.
_TRANSACTION_COLUMNS = ['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees', 'transferTargetAccount']


def build_portfolio(transaction_rows: list[list[object]]) -> Portfolio:
    """Two-depot portfolio around a single security for depot transfer scenarios."""
    accounts = pd.DataFrame([
        ['Depot 1', AccountType.SECURITIES.value, None, False, 'EUR'],
        ['Depot 2', AccountType.SECURITIES.value, None, False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'], index=['depot1', 'depot2'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Test ETF', 'IE00B4L5Y983', 'EUR'],
    ], columns=['name', 'wkn', 'currency'], index=['sec1'])
    securities.index.name = 'securityId'

    transactions = pd.DataFrame(transaction_rows, columns=_TRANSACTION_COLUMNS)
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    prices = pd.DataFrame([
        [SELL_DATE, 'sec1', 160.0],
    ], columns=['date', 'securityId', 'price'])
    prices = prices.set_index(['date', 'securityId'])

    portfolio = Portfolio(accounts, transactions, securities, prices)
    portfolio.base_currency = 'EUR'
    return portfolio


def remaining_lots(portfolio: Portfolio) -> pd.DataFrame:
    lots = enrich_fifo_lots(portfolio.securities_account_transactions, SELL_DATE, sell_price=160.0, tax_rate=TAX_RATE)
    return lots.reset_index().sort_values(['date', 'accountId'])


def test_full_transfer_preserves_cost_basis() -> None:
    """A depot transfer must move the lot to the destination account with unchanged acquisition data."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 10.0, None],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot2'],
        [datetime(2023, 1, 15), 'depot2', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    assert calculate_total_cost_basis(portfolio.securities_account_transactions) == pytest.approx(1010.0)  # 10 x 100 + 10 fees

    lots = remaining_lots(portfolio)
    assert len(lots) == 1
    assert lots.iloc[0]['accountId'] == 'depot2'
    assert lots.iloc[0]['date'] == datetime(2020, 1, 15)  # original acquisition date preserved
    assert lots.iloc[0]['shares'] == pytest.approx(10.0)
    assert lots.iloc[0]['purchasePrice'] == pytest.approx(100.0)
    assert lots.iloc[0]['fees'] == pytest.approx(10.0)


def test_transfer_pairs_via_cross_entry_regardless_of_leg_dates() -> None:
    """Pairing uses the authoritative cross-entry link, so transfer legs booked on different days still pair."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2022, 12, 30), 'depot1', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot2'],
        [datetime(2023, 1, 2), 'depot2', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    assert calculate_total_cost_basis(portfolio.securities_account_transactions) == pytest.approx(1000.0)

    lots = remaining_lots(portfolio)
    assert len(lots) == 1
    assert lots.iloc[0]['accountId'] == 'depot2'
    assert lots.iloc[0]['purchasePrice'] == pytest.approx(100.0)


def test_partial_transfer_splits_lot_and_prorates_fees() -> None:
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 10.0, None],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 4.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot2'],
        [datetime(2023, 1, 15), 'depot2', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 4.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    lots = remaining_lots(portfolio).set_index('accountId')
    assert len(lots) == 2
    assert lots.loc['depot1', 'shares'] == pytest.approx(6.0)
    assert lots.loc['depot1', 'fees'] == pytest.approx(6.0)
    assert lots.loc['depot2', 'shares'] == pytest.approx(4.0)
    assert lots.loc['depot2', 'fees'] == pytest.approx(4.0)
    assert lots.loc['depot1', 'purchasePrice'] == pytest.approx(100.0)
    assert lots.loc['depot2', 'purchasePrice'] == pytest.approx(100.0)

    assert calculate_total_cost_basis(portfolio.securities_account_transactions) == pytest.approx(1010.0)


def test_sell_after_transfer_consumes_transferred_lot_first() -> None:
    """FIFO in the destination depot must consume by original acquisition date, not by lot arrival order."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot2'],
        [datetime(2023, 1, 15), 'depot2', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 5, 1), 'depot2', 'sec1', TransactionType.BUY.value, -2000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2024, 1, 10), 'depot2', 'sec1', TransactionType.SELL.value, 2500.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    lots = remaining_lots(portfolio)
    assert len(lots) == 1
    assert lots.iloc[0]['date'] == datetime(2023, 5, 1)  # the transferred 2020 lot was consumed first
    assert lots.iloc[0]['purchasePrice'] == pytest.approx(200.0)
    assert lots.iloc[0]['shares'] == pytest.approx(10.0)


def test_sell_in_source_account_after_transfer() -> None:
    """After the oldest lot moved away, sells in the source account consume its remaining lots."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2021, 3, 1), 'depot1', 'sec1', TransactionType.BUY.value, -1400.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot2'],
        [datetime(2023, 1, 15), 'depot2', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2024, 1, 10), 'depot1', 'sec1', TransactionType.SELL.value, 800.0, 5.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    lots = remaining_lots(portfolio).set_index('accountId')
    assert len(lots) == 2
    assert lots.loc['depot2', 'purchasePrice'] == pytest.approx(100.0)  # transferred 2020 lot untouched
    assert lots.loc['depot2', 'shares'] == pytest.approx(10.0)
    assert lots.loc['depot1', 'purchasePrice'] == pytest.approx(140.0)
    assert lots.loc['depot1', 'shares'] == pytest.approx(5.0)


def test_same_day_transfers_between_distinct_pairs_do_not_collide() -> None:
    """Two same-day, equal-size transfers of the same security route to their own destinations (no key collision)."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2020, 6, 1), 'depot2', 'sec1', TransactionType.BUY.value, -1500.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot2'],
        [datetime(2023, 1, 15), 'depot2', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 1, 15), 'depot2', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot1'],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    lots = remaining_lots(portfolio)
    # the heuristic keyed transfers on (date, security, shares), so these two collided and both
    # resolved to the same destination; the cross-entry link routes each to its own depot instead.
    # (Which depot ends with which lot on a same-day swap depends on processing order — a separate concern.)
    assert set(lots['accountId']) == {'depot1', 'depot2'}  # both depots still hold a lot, neither emptied
    assert sorted(lots['purchasePrice']) == pytest.approx([100.0, 150.0])  # both acquisition costs preserved


@pytest.mark.parametrize('target', [None, ''])
def test_transfer_out_without_cross_entry_link_keeps_lots_in_source(target: str | None, caplog: LogCaptureFixture) -> None:
    """A TRANSFER_OUT with no linked destination (corrupt or stale-cache data) keeps the lots in the source account."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, target],
    ])

    with caplog.at_level(logging.WARNING):
        cost_basis = calculate_total_cost_basis(portfolio.securities_account_transactions)

    assert cost_basis == pytest.approx(1000.0)  # lot preserved, only account attribution is stale
    assert 'keeping lots in the source account' in caplog.text

    lots = remaining_lots(portfolio)
    assert len(lots) == 1
    assert lots.iloc[0]['accountId'] == 'depot1'
    assert lots.iloc[0]['shares'] == pytest.approx(10.0)


def test_share_sell_account_filter_excludes_other_accounts() -> None:
    """simulate share-sell --account-id must not offer lots residing in other accounts."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2021, 3, 1), 'depot2', 'sec1', TransactionType.BUY.value, -700.0, 5.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    result = prepare_share_sell_df(portfolio, {}, SELL_DATE, TAX_RATE, account_id='depot1')

    assert result['shares'].sum() == pytest.approx(10.0)
    assert result['purchasePrice'].iloc[0] == pytest.approx(100.0)


def test_share_sell_account_filter_includes_transferred_lots() -> None:
    """Lots transferred into the requested account belong to it, with cost basis intact."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot2', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 1, 15), 'depot2', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot1'],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    result = prepare_share_sell_df(portfolio, {}, SELL_DATE, TAX_RATE, account_id='depot1')

    assert result['shares'].sum() == pytest.approx(10.0)
    assert result['purchasePrice'].iloc[0] == pytest.approx(100.0)


# Regression tests for /code-review findings #1 and #3 (fixed here) and #4 (a post-refactor
# behaviour change pinned as a characterization test so any future change to it is deliberate).


def _deemed_income_data(year: int, security_id: str, per_share: float) -> pd.DataFrame:
    """A one-row prepaid-tax (Vorabpauschale) table, as load_prepaid_tax_data would produce."""
    return TaxPaidSchema.validate(pd.DataFrame(
        {'deemed_income': [per_share]},
        index=pd.MultiIndex.from_arrays([[year], [security_id]], names=['year', 'security_id']),
    ))


def test_transfer_merging_same_date_lot_does_not_double_count_deemed_income() -> None:
    """Finding #1: a transfer into an account already holding a same-date lot of the same security
    yields two lots sharing one (date, accountId, securityId) key; deemed income must be attributed
    per lot, not summed across the shared key and broadcast back onto both."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2020, 1, 15), 'depot2', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 1, 15), 'depot2', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, 'depot1'],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])
    tax_data = _deemed_income_data(2021, 'sec1', 1.0)

    lots = enrich_fifo_lots(portfolio.securities_account_transactions, SELL_DATE, sell_price=160.0, tax_rate=TAX_RATE, tax_csv_data=tax_data)

    # 20 held shares x 1.0/share deemed income for 2021 = 20.0; the bug reports 40.0.
    assert lots['deemedIncome'].sum() == pytest.approx(20.0)


def test_share_sell_offers_unlinked_transfer_lots_from_source_account() -> None:
    """Finding #3: an unlinked TRANSFER_OUT keeps the cost basis in the source account, so the shares
    must stay sellable from there even though that account shows no net share balance after the
    transfer. They are not offered from the destination, since no cost basis was relocated to it."""
    portfolio = build_portfolio([
        [datetime(2020, 1, 15), 'depot1', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 1, 15), 'depot1', 'sec1', TransactionType.TRANSFER_OUT.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2023, 1, 15), 'depot2', 'sec1', TransactionType.TRANSFER_IN.value, 0.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    from_source = prepare_share_sell_df(portfolio, {}, SELL_DATE, TAX_RATE, account_id='depot1')
    assert from_source['shares'].sum() == pytest.approx(10.0)
    assert from_source['purchasePrice'].iloc[0] == pytest.approx(100.0)

    assert prepare_share_sell_df(portfolio, {}, SELL_DATE, TAX_RATE, account_id='depot2').empty


def test_fixed_shares_selects_globally_oldest_lot_across_accounts() -> None:
    """Characterization (finding #4): after the refactor, --shares without --account-id selects lots in
    global acquisition-date order across accounts, not grouped by account as before. Pins the behaviour so
    a future change is deliberate rather than silent."""
    portfolio = build_portfolio([
        [datetime(2022, 6, 1), 'depot1', 'sec1', TransactionType.BUY.value, -1400.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
        [datetime(2020, 1, 15), 'depot2', 'sec1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0, None],
    ])

    result = prepare_share_sell_df(portfolio, {}, SELL_DATE, TAX_RATE, security_id='sec1', shares=5.0)

    assert result.iloc[0]['date'] == datetime(2020, 1, 15)          # depot2's older lot, not depot1's
    assert result.iloc[0]['purchasePrice'] == pytest.approx(100.0)
