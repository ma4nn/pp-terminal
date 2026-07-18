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

from pp_terminal.commands.simulate_share_sell import prepare_share_sell_df, _resolve_categories
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
        prepare_share_sell_df(Portfolio(), {}, datetime(2025, 1, 1), 26.375, taxonomy="Anything")


def test_min_amount_requires_preserve_allocation() -> None:
    """A minimum trade size only makes sense together with allocation preservation."""
    with pytest.raises(InputError, match="requires preserve-allocation"):
        prepare_share_sell_df(Portfolio(), {}, datetime(2025, 1, 1), 26.375, target_net=1000.0, min_amount=50.0)


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
