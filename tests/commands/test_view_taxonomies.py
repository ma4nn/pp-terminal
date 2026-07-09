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

from pp_terminal.commands.view_taxonomies import print_taxonomies, prepare_taxonomies_df
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import Taxonomy
from pp_terminal.output.strategy import RichOutputStrategy


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


def test_print_taxonomies(portfolio_with_taxonomies: Portfolio, capsys: pytest.CaptureFixture[str]) -> None:
    ctx = Context(Mock())
    ctx.obj = Mock()
    ctx.obj.portfolio = portfolio_with_taxonomies
    ctx.obj.output = RichOutputStrategy()

    print_taxonomies(ctx)

    captured = capsys.readouterr()
    assert 'Asset Allocation' in captured.out
    assert 'Equities' in captured.out
    assert 'Bonds' in captured.out
    assert 'Cash' in captured.out
