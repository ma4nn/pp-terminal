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

from unittest.mock import Mock

import pandas as pd
import pytest
from typer import Context

from pp_terminal.commands.simulate_pmt import PmtConfig
from pp_terminal.commands.view_taxonomies import print_taxonomies, prepare_taxonomies_df
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import Taxonomy
from pp_terminal.output.strategy import RichOutputStrategy
from pp_terminal.utils.config import command_config, empty_config


@pytest.fixture(name='portfolio_with_taxonomies')
def provide_portfolio_with_taxonomies() -> Portfolio:
    taxonomies = {
        'tax-1': Taxonomy(uuid='tax-1', name='Asset Allocation'),
    }
    assignments = pd.DataFrame({
        'taxonomyName': ['Asset Allocation', 'Asset Allocation', 'Asset Allocation'],
        'itemId': ['sec-1', 'sec-2', 'acc-1'],
        'itemType': ['security', 'security', 'account'],
        'categoryName': ['Equities', 'Bonds', 'Cash'],
        'weight': [10000, 10000, 10000]
    })

    return Portfolio(taxonomies=taxonomies, taxonomy_assignments=assignments)


def test_prepare_taxonomies_df(portfolio_with_taxonomies: Portfolio) -> None:
    df = prepare_taxonomies_df(portfolio_with_taxonomies)

    assert len(df) == 3
    assert list(df.columns) == ['Taxonomy', 'Category', 'Securities', 'Accounts']

    bonds_row = df[df['Category'] == 'Bonds']
    assert bonds_row['Securities'].iloc[0] == 1
    assert bonds_row['Accounts'].iloc[0] == 0

    cash_row = df[df['Category'] == 'Cash']
    assert cash_row['Securities'].iloc[0] == 0
    assert cash_row['Accounts'].iloc[0] == 1


def test_prepare_taxonomies_df_empty() -> None:
    portfolio = Portfolio()
    df = prepare_taxonomies_df(portfolio)

    assert df.empty
    assert list(df.columns) == ['Taxonomy', 'Category', 'Securities', 'Accounts']


def _config_with_returns(taxonomy: str | None, returns: list[float | dict[str, float]]) -> object:
    config = empty_config()
    config.taxonomy = taxonomy
    command_config(config, PmtConfig).returns = returns
    return config


def test_prepare_taxonomies_df_adds_configured_returns(portfolio_with_taxonomies: Portfolio) -> None:
    df = prepare_taxonomies_df(portfolio_with_taxonomies, 'Asset Allocation', [{'Equities': 5.0, 'Bonds': 2.0}])

    assert 'Expected Return' in df.columns
    assert df[df['Category'] == 'Equities']['Expected Return'].iloc[0] == 5.0
    assert df[df['Category'] == 'Bonds']['Expected Return'].iloc[0] == 2.0
    assert pd.isna(df[df['Category'] == 'Cash']['Expected Return'].iloc[0])  # not configured -> blank


def test_prepare_taxonomies_df_returns_scoped_to_configured_taxonomy() -> None:
    assignments = pd.DataFrame({
        'taxonomyName': ['Asset Allocation', 'Regions'],
        'itemId': ['sec-1', 'sec-1'],
        'itemType': ['security', 'security'],
        'categoryName': ['Equities', 'Equities'],  # same category name in a different taxonomy
        'weight': [10000, 10000],
    })
    df = prepare_taxonomies_df(Portfolio(taxonomy_assignments=assignments), 'Asset Allocation', [{'Equities': 5.0}])

    assert df[df['Taxonomy'] == 'Asset Allocation']['Expected Return'].iloc[0] == 5.0
    assert pd.isna(df[df['Taxonomy'] == 'Regions']['Expected Return'].iloc[0])  # other taxonomy is untouched


def test_prepare_taxonomies_df_default_return_fills_unlisted_categories(portfolio_with_taxonomies: Portfolio) -> None:
    df = prepare_taxonomies_df(portfolio_with_taxonomies, 'Asset Allocation', [{'*': 3.0, 'Equities': 5.0}])

    assert df[df['Category'] == 'Equities']['Expected Return'].iloc[0] == 5.0  # explicit override
    assert df[df['Category'] == 'Bonds']['Expected Return'].iloc[0] == 3.0  # falls back to the '*' default
    assert df[df['Category'] == 'Cash']['Expected Return'].iloc[0] == 3.0
    assert '*' not in set(df['Category'])  # the reserved key is not shown as a category


def test_prepare_taxonomies_df_multiple_scenarios_one_column_each(portfolio_with_taxonomies: Portfolio) -> None:
    df = prepare_taxonomies_df(portfolio_with_taxonomies, 'Asset Allocation', [{'Equities': 5.0}, {'Equities': 6.0}])

    assert 'Expected Return' not in df.columns
    equities = df[df['Category'] == 'Equities']
    assert equities['Expected Return 1'].iloc[0] == 5.0
    assert equities['Expected Return 2'].iloc[0] == 6.0


def test_prepare_taxonomies_df_ignores_fixed_rate_scenarios(portfolio_with_taxonomies: Portfolio) -> None:
    df = prepare_taxonomies_df(portfolio_with_taxonomies, 'Asset Allocation', [])

    assert list(df.columns) == ['Taxonomy', 'Category', 'Securities', 'Accounts']


def test_print_taxonomies(portfolio_with_taxonomies: Portfolio, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = Context(Mock())
    ctx.obj = Mock()
    ctx.obj.portfolio = portfolio_with_taxonomies
    ctx.obj.output = RichOutputStrategy()
    ctx.obj.config = empty_config()

    print_taxonomies(ctx)

    captured = capsys.readouterr()
    assert 'Asset Allocation' in captured.out
    assert 'Equities' in captured.out
    assert 'Bonds' in captured.out
    assert 'Cash' in captured.out
    assert 'Return' not in captured.out  # no returns configured -> no extra column
    assert 'Hint' not in captured.out  # ...and no configuration hint
    # counts (incl. the Total footer of 2 securities) render as integers, not decimals
    assert '1.00' not in captured.out
    assert '2.00' not in captured.out


def test_print_taxonomies_renders_configured_returns(portfolio_with_taxonomies: Portfolio, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = Context(Mock())
    ctx.obj = Mock()
    ctx.obj.portfolio = portfolio_with_taxonomies
    ctx.obj.output = RichOutputStrategy()
    ctx.obj.config = _config_with_returns('Asset Allocation', [4.0, {'Equities': 5.0, 'Bonds': 1.9}])

    print_taxonomies(ctx)

    captured = capsys.readouterr()
    assert 'Return' in captured.out
    assert '5.00%' in captured.out
    assert '1.90%' in captured.out
    assert '6.90%' not in captured.out  # returns are not summed in the Total footer
    assert 'Hint' in captured.out and 'pmt' in captured.out  # hint points at the pmt configuration origin
