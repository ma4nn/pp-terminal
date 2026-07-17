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

from typing import Any

import pandas as pd
import pytest

from pp_terminal.domain.allocation import build_category_map
from pp_terminal.exceptions import InputError


def _assignments(rows: list[list[Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=['taxonomyName', 'itemId', 'itemType', 'categoryName', 'weight'])


def test_single_category_mapping() -> None:
    df = _assignments([
        ['Asset Allocation', 'sec-1', 'security', 'Equity', 10000],
        ['Asset Allocation', 'sec-2', 'security', 'Bonds', 10000],
    ])
    mapping, multi = build_category_map(df, 'Asset Allocation')

    assert mapping == {'sec-1': 'Equity', 'sec-2': 'Bonds'}
    assert multi == []


def test_dominant_category_for_multi_assignment() -> None:
    df = _assignments([
        ['Regionen', 'sec-1', 'security', 'Developed', 7000],
        ['Regionen', 'sec-1', 'security', 'Emerging', 3000],
    ])
    mapping, multi = build_category_map(df, 'Regionen')

    assert mapping == {'sec-1': 'Developed'}  # highest weight wins
    assert multi == ['sec-1']


def test_ignores_account_assignments_and_other_taxonomies() -> None:
    df = _assignments([
        ['Asset Allocation', 'sec-1', 'security', 'Equity', 10000],
        ['Asset Allocation', 'acc-1', 'account', 'Cash', 10000],
        ['Regionen', 'sec-1', 'security', 'Developed', 10000],
    ])
    mapping, multi = build_category_map(df, 'Asset Allocation')

    assert mapping == {'sec-1': 'Equity'}
    assert multi == []


def test_unknown_taxonomy_lists_available() -> None:
    df = _assignments([['Asset Allocation', 'sec-1', 'security', 'Equity', 10000]])

    with pytest.raises(InputError, match="Unknown taxonomy 'Nope'. Available: Asset Allocation"):
        build_category_map(df, 'Nope')


def test_empty_assignments_raises() -> None:
    df = _assignments([]).astype({'weight': 'int64'})

    with pytest.raises(InputError, match="no taxonomies"):
        build_category_map(df, 'Asset Allocation')
