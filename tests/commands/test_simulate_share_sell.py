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

import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pandas as pd
import pytest

from pp_terminal.utils.config import empty_config
from pp_terminal.commands.simulate_share_sell import (
    prepare_share_sell_df, _resolve_categories, summarize_sell_plan, _sell_introduction
)
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.exceptions import InputError


@pytest.fixture(name='warning_log')
def provide_warning_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Capture warnings straight from the command logger, independent of propagation/global logging config."""
    logger = logging.getLogger('pp_terminal.commands.simulate_share_sell')
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(previous_level)


def _portfolio(assignment_rows: list[list[Any]]) -> Portfolio:
    securities = pd.DataFrame(
        [['ETF A', 'W1', 'EUR'], ['ETF B', 'W2', 'EUR'], ['ETF C', 'W3', 'EUR'], ['ETF D', 'W4', 'EUR']],
        columns=['name', 'wkn', 'currency'],
        index=['sec-1', 'sec-2', 'sec-3', 'sec-4'],
    )
    securities.index.name = 'securityId'
    taxonomy_assignments = pd.DataFrame(
        assignment_rows, columns=['taxonomyName', 'itemId', 'itemType', 'categoryName', 'weight']
    )
    return Portfolio(securities=securities, taxonomy_assignments=taxonomy_assignments)


def _holdings(security_ids: list[str]) -> pd.Series:
    index = pd.MultiIndex.from_tuples(
        [('acc-1', sid, 'EUR') for sid in security_ids], names=['accountId', 'securityId', 'currency']
    )
    return pd.Series([10.0] * len(security_ids), index=index, name='shares')


def test_preserve_allocation_requires_target_net() -> None:
    """The shared entry point (used by CLI and MCP) rejects a taxonomy without a target net."""
    with pytest.raises(InputError, match="requires a target net"):
        prepare_share_sell_df(Portfolio(), empty_config(), datetime(2025, 1, 1), 26.375, taxonomy="Anything")


def test_min_amount_requires_preserve_allocation() -> None:
    """A minimum trade size only makes sense together with allocation preservation."""
    with pytest.raises(InputError, match="requires preserve-allocation"):
        prepare_share_sell_df(Portfolio(), empty_config(), datetime(2025, 1, 1), 26.375, target_net=1000.0, min_amount=50.0)


def test_warns_about_held_but_unclassified_securities(warning_log: pytest.LogCaptureFixture) -> None:
    portfolio = _portfolio([
        ['AA', 'sec-1', 'security', 'Equity', 10000],
        ['AA', 'sec-2', 'security', 'Equity', 10000],
    ])
    holdings = _holdings(['sec-1', 'sec-2', 'sec-3'])  # sec-3 is held but not in the taxonomy

    mapping = _resolve_categories(portfolio, 'AA', holdings)

    assert mapping == {'sec-1': 'Equity', 'sec-2': 'Equity'}
    assert 'not classified' in warning_log.text
    assert 'ETF C' in warning_log.text            # the unmapped holding is named
    assert 'ETF A' not in warning_log.text        # mapped holdings are not warned about
    assert 'ETF B' not in warning_log.text


def _sell_lots(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Minimal per-lot sell result as produced by prepare_share_sell_df (one row per FIFO lot)."""
    return pd.DataFrame(rows)


def test_summarize_sell_plan_aggregates_lots_per_security() -> None:
    lots = _sell_lots([
        {'securityName': 'ETF A', 'isin': 'W1', 'account': 'Depot', 'currency': 'EUR', 'shares': 4.0, 'purchasePrice': 100.0,
         'salePrice': 120.0, 'costBasis': 400.0, 'fees': 2.0, 'grossProceeds': 480.0, 'capitalGain': 80.0,
         'deemedIncome': 1.0, 'taxableGain': 79.0, 'totalTax': 20.0, 'netProceeds': 460.0},
        {'securityName': 'ETF A', 'isin': 'W1', 'account': 'Depot', 'currency': 'EUR', 'shares': 6.0, 'purchasePrice': 110.0,
         'salePrice': 120.0, 'costBasis': 660.0, 'fees': 3.0, 'grossProceeds': 720.0, 'capitalGain': 60.0,
         'deemedIncome': 2.0, 'taxableGain': 58.0, 'totalTax': 15.0, 'netProceeds': 705.0},
        {'securityName': 'ETF B', 'isin': 'W2', 'account': 'Depot', 'currency': 'EUR', 'shares': 5.0, 'purchasePrice': 50.0,
         'salePrice': 55.0, 'costBasis': 250.0, 'fees': 1.0, 'grossProceeds': 275.0, 'capitalGain': 25.0,
         'deemedIncome': 0.0, 'taxableGain': 25.0, 'totalTax': 6.0, 'netProceeds': 269.0},
    ])

    plan = summarize_sell_plan(lots)

    assert list(plan['securityName']) == ['ETF A', 'ETF B']
    etf_a = plan[plan['securityName'] == 'ETF A'].iloc[0]
    assert etf_a['shares'] == pytest.approx(10.0)                    # 4 + 6 lots summed
    assert etf_a['netProceeds'] == pytest.approx(1165.0)            # 460 + 705 summed
    assert etf_a['totalTax'] == pytest.approx(35.0)
    assert etf_a['salePrice'] == pytest.approx(120.0)              # constant across lots
    assert etf_a['purchasePrice'] == pytest.approx(106.0)          # (4*100 + 6*110) / 10 weighted


def test_summarize_sell_plan_splits_same_security_across_accounts() -> None:
    """The same security held in two depots yields two order tickets, not one merged row."""
    lots = _sell_lots([
        {'securityName': 'ETF A', 'isin': 'W1', 'account': 'Depot', 'currency': 'EUR', 'shares': 4.0, 'purchasePrice': 100.0,
         'salePrice': 120.0, 'costBasis': 400.0, 'fees': 2.0, 'grossProceeds': 480.0, 'capitalGain': 80.0,
         'deemedIncome': 1.0, 'taxableGain': 79.0, 'totalTax': 20.0, 'netProceeds': 460.0},
        {'securityName': 'ETF A', 'isin': 'W1', 'account': 'Zweitdepot', 'currency': 'EUR', 'shares': 6.0, 'purchasePrice': 110.0,
         'salePrice': 120.0, 'costBasis': 660.0, 'fees': 3.0, 'grossProceeds': 720.0, 'capitalGain': 60.0,
         'deemedIncome': 2.0, 'taxableGain': 58.0, 'totalTax': 15.0, 'netProceeds': 705.0},
    ])

    plan = summarize_sell_plan(lots)

    assert set(plan['account']) == {'Depot', 'Zweitdepot'}
    assert plan[plan['account'] == 'Depot'].iloc[0]['shares'] == pytest.approx(4.0)
    assert plan[plan['account'] == 'Zweitdepot'].iloc[0]['shares'] == pytest.approx(6.0)


def test_summarize_sell_plan_carries_asset_class_when_present() -> None:
    """With --preserve-allocation the lots carry an assetClass; it must lead the plan and cluster securities."""
    lots = _sell_lots([
        {'assetClass': 'Equity', 'securityName': 'ETF A', 'isin': 'W1', 'account': 'Depot', 'currency': 'EUR',
         'shares': 4.0, 'purchasePrice': 100.0, 'salePrice': 120.0, 'costBasis': 400.0, 'fees': 2.0,
         'grossProceeds': 480.0, 'capitalGain': 80.0, 'deemedIncome': 1.0, 'taxableGain': 79.0,
         'totalTax': 20.0, 'netProceeds': 460.0},
        {'assetClass': 'Equity', 'securityName': 'ETF B', 'isin': 'W2', 'account': 'Depot', 'currency': 'EUR',
         'shares': 5.0, 'purchasePrice': 50.0, 'salePrice': 55.0, 'costBasis': 250.0, 'fees': 1.0,
         'grossProceeds': 275.0, 'capitalGain': 25.0, 'deemedIncome': 0.0, 'taxableGain': 25.0,
         'totalTax': 6.0, 'netProceeds': 269.0},
    ])

    plan = summarize_sell_plan(lots)

    assert list(plan.columns)[0] == 'assetClass'
    assert list(plan['assetClass']) == ['Equity', 'Equity']
    assert list(plan['securityName']) == ['ETF A', 'ETF B']  # two securities kept distinct within the class


def test_summarize_sell_plan_carries_class_share_without_summing_it() -> None:
    """classShare is a per-class weight; the plan must keep it constant per class, not sum it across securities."""
    lots = _sell_lots([
        {'assetClass': 'Equity', 'classShare': 0.6, 'securityName': 'ETF A', 'isin': 'W1', 'account': 'Depot',
         'currency': 'EUR', 'shares': 4.0, 'purchasePrice': 100.0, 'salePrice': 120.0, 'costBasis': 400.0,
         'fees': 2.0, 'grossProceeds': 480.0, 'capitalGain': 80.0, 'deemedIncome': 1.0, 'taxableGain': 79.0,
         'totalTax': 20.0, 'netProceeds': 460.0},
        {'assetClass': 'Equity', 'classShare': 0.6, 'securityName': 'ETF B', 'isin': 'W2', 'account': 'Depot',
         'currency': 'EUR', 'shares': 5.0, 'purchasePrice': 50.0, 'salePrice': 55.0, 'costBasis': 250.0,
         'fees': 1.0, 'grossProceeds': 275.0, 'capitalGain': 25.0, 'deemedIncome': 0.0, 'taxableGain': 25.0,
         'totalTax': 6.0, 'netProceeds': 269.0},
        {'assetClass': 'Bonds', 'classShare': 0.4, 'securityName': 'ETF C', 'isin': 'W3', 'account': 'Depot',
         'currency': 'EUR', 'shares': 3.0, 'purchasePrice': 30.0, 'salePrice': 33.0, 'costBasis': 90.0,
         'fees': 0.5, 'grossProceeds': 99.0, 'capitalGain': 9.0, 'deemedIncome': 0.0, 'taxableGain': 9.0,
         'totalTax': 2.0, 'netProceeds': 97.0},
    ])

    plan = summarize_sell_plan(lots)

    assert list(plan.columns)[-1] == 'classShare'
    assert list(plan['classShare']) == pytest.approx([0.6, 0.6, 0.4])


def test_summarize_sell_plan_keeps_securities_without_isin() -> None:
    """A security without an ISIN (e.g. crypto) must not be dropped, or the plan total under-reports."""
    lots = _sell_lots([
        {'securityName': 'ETF A', 'isin': 'W1', 'account': 'Depot', 'currency': 'EUR', 'shares': 4.0, 'purchasePrice': 100.0,
         'salePrice': 120.0, 'costBasis': 400.0, 'fees': 2.0, 'grossProceeds': 480.0, 'capitalGain': 80.0,
         'deemedIncome': 1.0, 'taxableGain': 79.0, 'totalTax': 20.0, 'netProceeds': 460.0},
        {'securityName': 'Bitcoin', 'isin': None, 'account': 'Krypto', 'currency': 'EUR', 'shares': 0.5, 'purchasePrice': 20000.0,
         'salePrice': 30000.0, 'costBasis': 10000.0, 'fees': 0.0, 'grossProceeds': 15000.0, 'capitalGain': 5000.0,
         'deemedIncome': 0.0, 'taxableGain': 5000.0, 'totalTax': 1300.0, 'netProceeds': 13700.0},
    ])

    plan = summarize_sell_plan(lots)

    assert set(plan['securityName']) == {'ETF A', 'Bitcoin'}
    assert plan['netProceeds'].sum() == pytest.approx(lots['netProceeds'].sum())  # nothing silently dropped


def test_summarize_sell_plan_preserves_total_proceeds() -> None:
    lots = _sell_lots([
        {'securityName': 'ETF A', 'isin': 'W1', 'account': 'Depot', 'currency': 'EUR', 'shares': 4.0, 'purchasePrice': 100.0,
         'salePrice': 120.0, 'costBasis': 400.0, 'fees': 2.0, 'grossProceeds': 480.0, 'capitalGain': 80.0,
         'deemedIncome': 1.0, 'taxableGain': 79.0, 'totalTax': 20.0, 'netProceeds': 460.0},
        {'securityName': 'ETF B', 'isin': 'W2', 'account': 'Depot', 'currency': 'EUR', 'shares': 5.0, 'purchasePrice': 50.0,
         'salePrice': 55.0, 'costBasis': 250.0, 'fees': 1.0, 'grossProceeds': 275.0, 'capitalGain': 25.0,
         'deemedIncome': 0.0, 'taxableGain': 25.0, 'totalTax': 6.0, 'netProceeds': 269.0},
    ])

    plan = summarize_sell_plan(lots)

    assert plan['netProceeds'].sum() == pytest.approx(lots['netProceeds'].sum())
    assert plan['totalTax'].sum() == pytest.approx(lots['totalTax'].sum())


def test_introduction_full_liquidation_sells_every_share() -> None:
    intro = _sell_introduction('', None, None, None, None, None)
    assert 'every share you hold' in intro
    assert 'the latest price' in intro
    assert 'FIFO cost basis' in intro


def test_introduction_fixed_shares_uses_fifo_and_scope() -> None:
    intro = _sell_introduction(' of ETF A', 120.0, 10.0, None, None, None)
    assert '10 shares[/bold] of ETF A' in intro    # scope and a clean share count
    assert 'the given price of 120.00' in intro   # explicit --price is surfaced
    assert 'oldest lots first[/bold] (FIFO)' in intro


def test_introduction_target_net_explains_tax_minimization() -> None:
    intro = _sell_introduction('', None, None, 5000.0, None, None)
    assert 'net [bold]5000.00' in intro
    assert 'least-taxed lots first' in intro
    assert 'allocation steady' not in intro       # no taxonomy -> not the preserving variant


def test_introduction_preserve_allocation_explains_allocation_and_floor() -> None:
    intro = _sell_introduction('', None, None, 5000.0, 'Regions', 50.0)
    assert 'holding your Regions allocation steady' in intro
    assert 'every asset class sheds the same fraction' in intro
    assert 'orders below 50.00 are consolidated' in intro


def test_multi_category_warning_only_covers_held_securities(warning_log: pytest.LogCaptureFixture) -> None:
    portfolio = _portfolio([
        ['AA', 'sec-1', 'security', 'Equity', 6000],   # held, multi -> dominant Equity, warned
        ['AA', 'sec-1', 'security', 'Bonds', 4000],
        ['AA', 'sec-2', 'security', 'Equity', 10000],  # held, single
        ['AA', 'sec-4', 'security', 'Equity', 5000],   # NOT held, multi -> must not be warned about
        ['AA', 'sec-4', 'security', 'Bonds', 5000],
    ])
    holdings = _holdings(['sec-1', 'sec-2'])

    _resolve_categories(portfolio, 'AA', holdings)

    assert 'multiple categories' in warning_log.text
    assert 'ETF A' in warning_log.text            # held multi-category security is warned
    assert 'ETF D' not in warning_log.text        # unheld multi-category security is not
    assert 'not classified' not in warning_log.text  # every held security is classified here
