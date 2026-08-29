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

import pandas as pd
import pytest
from _pytest.fixtures import TopRequest

from pp_terminal.data.filters import pivot_taxonomy_columns
from pp_terminal.data.pp_portfolio_builder import PpPortfolioBuilder


class TestParseTaxonomies:
    def test_parses_taxonomies_from_xml(self, request: TopRequest) -> None:
        portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

        assert len(portfolio.taxonomies) > 0
        assert all(t.name for t in portfolio.taxonomies.values())

    def test_taxonomy_assignments_have_expected_columns(self, request: TopRequest) -> None:
        portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'kommer.ids.xml')

        assert not portfolio.taxonomy_assignments.empty
        assert set(portfolio.taxonomy_assignments.columns) == {'taxonomyName', 'itemId', 'itemType', 'categoryName', 'weight'}

    def test_empty_portfolio_returns_empty_taxonomies(self, request: TopRequest) -> None:
        portfolio = PpPortfolioBuilder().construct(request.path.parent.parent / 'fixtures' / 'empty.ids.xml')

        assert len(portfolio.taxonomies) == 0
        assert portfolio.taxonomy_assignments.empty


class TestPivotTaxonomyColumns:
    @pytest.fixture
    def sample_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            'securityId': ['sec-1', 'sec-2'],
            'name': ['Fund A', 'Fund B']
        })

    def test_empty_assignments_returns_df_unchanged(self, sample_df: pd.DataFrame) -> None:
        result = pivot_taxonomy_columns(sample_df, pd.DataFrame(), 'securityId', 'security')
        pd.testing.assert_frame_equal(result, sample_df)

    def test_single_taxonomy_full_weight(self, sample_df: pd.DataFrame) -> None:
        assignments = pd.DataFrame({
            'itemId': ['sec-1'],
            'itemType': ['security'],
            'taxonomyName': ['Asset Allocation'],
            'categoryName': ['Equities'],
            'weight': [10000]
        })

        result = pivot_taxonomy_columns(sample_df, assignments, 'securityId', 'security')

        assert 'Asset Allocation' in result.columns
        assert result.loc[result['securityId'] == 'sec-1', 'Asset Allocation'].iloc[0] == 'Equities'
        assert pd.isna(result.loc[result['securityId'] == 'sec-2', 'Asset Allocation'].iloc[0])

    def test_split_weight_formatting(self, sample_df: pd.DataFrame) -> None:
        assignments = pd.DataFrame({
            'itemId': ['sec-1', 'sec-1'],
            'itemType': ['security', 'security'],
            'taxonomyName': ['Regions', 'Regions'],
            'categoryName': ['Europe', 'USA'],
            'weight': [7000, 3000]
        })

        result = pivot_taxonomy_columns(sample_df, assignments, 'securityId', 'security')

        assert 'Regions' in result.columns
        cell = result.loc[result['securityId'] == 'sec-1', 'Regions'].iloc[0]
        assert 'Europe (70%)' in cell
        assert 'USA (30%)' in cell

    def test_multiple_taxonomies(self, sample_df: pd.DataFrame) -> None:
        assignments = pd.DataFrame({
            'itemId': ['sec-1', 'sec-1'],
            'itemType': ['security', 'security'],
            'taxonomyName': ['Asset Allocation', 'Regions'],
            'categoryName': ['Equities', 'Global'],
            'weight': [10000, 10000]
        })

        result = pivot_taxonomy_columns(sample_df, assignments, 'securityId', 'security')

        assert 'Asset Allocation' in result.columns
        assert 'Regions' in result.columns

    def test_filters_by_item_type(self, sample_df: pd.DataFrame) -> None:
        assignments = pd.DataFrame({
            'itemId': ['sec-1', 'acc-1'],
            'itemType': ['security', 'account'],
            'taxonomyName': ['Asset Allocation', 'Asset Allocation'],
            'categoryName': ['Equities', 'Cash'],
            'weight': [10000, 10000]
        })

        result = pivot_taxonomy_columns(sample_df, assignments, 'securityId', 'security')

        assert 'Asset Allocation' in result.columns
        assert result.loc[result['securityId'] == 'sec-1', 'Asset Allocation'].iloc[0] == 'Equities'

    def test_id_column_in_index(self) -> None:
        df = pd.DataFrame({
            'name': ['Fund A', 'Fund B']
        }, index=pd.Index(['sec-1', 'sec-2'], name='securityId'))

        assignments = pd.DataFrame({
            'itemId': ['sec-1'],
            'itemType': ['security'],
            'taxonomyName': ['Asset Allocation'],
            'categoryName': ['Equities'],
            'weight': [10000]
        })

        result = pivot_taxonomy_columns(df, assignments, 'securityId', 'security')

        assert 'Asset Allocation' in result.columns
        assert result.loc['sec-1', 'Asset Allocation'] == 'Equities'

    def test_id_column_absent_returns_df_unchanged(self) -> None:
        df = pd.DataFrame({'foo': [1, 2]})
        assignments = pd.DataFrame({
            'itemId': ['sec-1'],
            'itemType': ['security'],
            'taxonomyName': ['Asset Allocation'],
            'categoryName': ['Equities'],
            'weight': [10000]
        })

        result = pivot_taxonomy_columns(df, assignments, 'securityId', 'security')

        pd.testing.assert_frame_equal(result, df)

    def test_no_assignments_for_item_type_returns_df_unchanged(self, sample_df: pd.DataFrame) -> None:
        assignments = pd.DataFrame({
            'itemId': ['acc-1'],
            'itemType': ['account'],
            'taxonomyName': ['Asset Allocation'],
            'categoryName': ['Cash'],
            'weight': [10000]
        })

        result = pivot_taxonomy_columns(sample_df, assignments, 'securityId', 'security')

        pd.testing.assert_frame_equal(result, sample_df)
