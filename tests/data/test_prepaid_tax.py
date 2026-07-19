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
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from pandera.typing import DataFrame

from pp_terminal.data.tax import calculate_prepaid_tax_per_lot, load_prepaid_tax_data, load_prepaid_tax_data_from_csv
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import TaxLotSchema, TaxPaidSchema
from pp_terminal.exceptions import InputError


def test_single_year_full_year(tax_csv_data: pd.DataFrame) -> None:
    """Test tax credit for single lot held full year."""
    df = DataFrame[TaxLotSchema]([
        [100.0, 10000.0, None, None, None]
    ], columns=['shares', 'costBasis', 'purchasePrice', 'currency', 'fees'],
        index=pd.MultiIndex.from_arrays([[datetime(2020, 1, 1)], ['acc-1'], ['sec-1']], names=['date', 'accountId', 'securityId']))

    # Current date 2022-12-31: years held = 2020, 2021 (not 2022 because last_year = current_year - 1)
    current_date = datetime(2022, 12, 31)
    tax = float(calculate_prepaid_tax_per_lot(df, current_date, tax_csv_data).sum())

    # 2020: 100 shares * €0.189573 = €18.96 (full year)
    # 2021: 100 shares * €0.227488 = €22.75 (full year)
    # Total: €41.71
    assert tax == pytest.approx(41.7061, abs=0.01)

def test_purchase_year_month_proration(tax_csv_data: pd.DataFrame) -> None:
    """Test that purchase year is prorated by months held."""
    df = DataFrame[TaxLotSchema]([
        [100.0, 10000.0, None, None, None]
    ], columns=['shares', 'costBasis', 'purchasePrice', 'currency', 'fees'],
        index=pd.MultiIndex.from_arrays([[datetime(2020, 6, 15)], ['acc-1'], ['sec-1']], names=['date', 'accountId', 'securityId']))

    current_date = datetime(2022, 12, 31)
    credit = float(calculate_prepaid_tax_per_lot(df, current_date, tax_csv_data).sum())

    # 2020: 100 shares * €0.189573 * (13-6)/12 = 100 * 0.189573 * 7/12 = €11.06
    # 2021: 100 shares * €0.227488 * 1.0 = €22.75
    # Total: €33.81
    assert credit == pytest.approx(33.8073, abs=0.01)

def test_multiple_lots_different_accounts(tax_csv_data: pd.DataFrame) -> None:
    """Test tax credit across multiple lots in different accounts."""
    df = DataFrame[TaxLotSchema]([
        [50.0, 5000.0, None, None, None],
        [30.0, 3600.0, None, None, None],
    ], columns=['shares', 'costBasis', 'purchasePrice', 'currency', 'fees'],
        index=pd.MultiIndex.from_arrays(
            [[datetime(2020, 1, 1), datetime(2021, 1, 1)], ['acc-1', 'acc-2'], ['sec-1', 'sec-1']],
            names=['date', 'accountId', 'securityId']))

    current_date = datetime(2022, 12, 31)
    credit = float(calculate_prepaid_tax_per_lot(df, current_date, tax_csv_data).sum())

    # Lot 1 (acc-1):
    #   2020: 50 * €0.189573 = €9.48
    #   2021: 50 * €0.227488 = €11.37
    # Lot 2 (acc-2):
    #   2021: 30 * €0.227488 = €6.82
    # Total: €27.68
    assert credit == pytest.approx(27.6777, abs=0.01)

def test_purchased_in_current_year_no_credit(tax_csv_data: pd.DataFrame) -> None:
    """Test that lots purchased in current year have no tax credit."""
    df = DataFrame[TaxLotSchema]([
        [100.0, 10000.0, None, None, None]
    ], columns=['shares', 'costBasis', 'purchasePrice', 'currency', 'fees'],
        index=pd.MultiIndex.from_arrays([[datetime(2022, 6, 1)], ['acc-1'], ['sec-1']], names=['date', 'accountId', 'securityId']))

    credit = calculate_prepaid_tax_per_lot(df, datetime(2022, 12, 31), tax_csv_data)

    # Purchased in 2022, evaluated in 2022 -> last_year = 2021 < first_year = 2022
    assert credit.index.equals(df.index)
    assert (credit == 0.0).all()

def test_missing_tax_data_ignored(tax_csv_data: pd.DataFrame) -> None:
    """Test that missing tax data for year/account/security is ignored (returns 0)."""
    df = DataFrame[TaxLotSchema]([
        [100.0, 10000.0, None, None, None]
    ], columns=['shares', 'costBasis', 'purchasePrice', 'currency', 'fees'],
        index=pd.MultiIndex.from_arrays([[datetime(2019, 1, 1)], ['acc-1'], ['sec-1']], names=['date', 'accountId', 'securityId']))

    current_date = datetime(2022, 12, 31)
    credit = float(calculate_prepaid_tax_per_lot(df, current_date, tax_csv_data).sum())

    # 2019: No data in CSV -> €0.00
    # 2020: 100 * €0.189573 = €18.96
    # 2021: 100 * €0.227488 = €22.75
    # Total: €41.71 (2019 silently ignored)
    assert credit == pytest.approx(41.7061, abs=0.01)

def test_no_tax_csv_returns_zero() -> None:
    """Test that None tax CSV returns zero credit."""
    df = DataFrame[TaxLotSchema]([
        [100.0, 10000.0, None, None, None]
    ], columns=['shares', 'costBasis', 'purchasePrice', 'currency', 'fees'],
        index=pd.MultiIndex.from_arrays([[datetime(2020, 1, 1)], ['acc-1'], ['sec-1']], names=['date', 'accountId', 'securityId']))

    credit = calculate_prepaid_tax_per_lot(df, datetime(2022, 12, 31), None)

    assert credit.index.equals(df.index)
    assert (credit == 0.0).all()


def test_lot_without_any_tax_data_gets_zero_not_nan(tax_csv_data: pd.DataFrame) -> None:
    """Lots the CSV knows nothing about must come back as 0.0, not NaN, when other lots match."""
    df = DataFrame[TaxLotSchema]([
        [100.0, 10000.0, None, None, None],
        [10.0, 1000.0, None, None, None],
    ], columns=['shares', 'costBasis', 'purchasePrice', 'currency', 'fees'],
        index=pd.MultiIndex.from_arrays(
            [[datetime(2020, 1, 1), datetime(2020, 6, 1)], ['acc-1', 'acc-1'], ['sec-1', 'sec-other']],
            names=['date', 'accountId', 'securityId']))

    credit = calculate_prepaid_tax_per_lot(df, datetime(2022, 12, 31), tax_csv_data)

    assert credit[(datetime(2020, 1, 1), 'acc-1', 'sec-1')] == pytest.approx(41.7061, abs=0.01)
    assert credit[(datetime(2020, 6, 1), 'acc-1', 'sec-other')] == 0.0

def test_tax_data_for_other_securities_and_years_returns_zero_series() -> None:
    """Test that tax data matching neither security nor year yields a zero Series aligned to the lots index."""
    df = DataFrame[TaxLotSchema]([
        [100.0, 10000.0, None, None, None]
    ], columns=['shares', 'costBasis', 'purchasePrice', 'currency', 'fees'],
        index=pd.MultiIndex.from_arrays([[datetime(2020, 1, 1)], ['acc-1'], ['sec-1']], names=['date', 'accountId', 'securityId']))

    tax_data = DataFrame[TaxPaidSchema]([
        [0.5],
        [0.5],
    ], columns=['deemed_income'],
        index=pd.MultiIndex.from_arrays([[2020, 2019], ['sec-other', 'sec-1']], names=['year', 'security_id']))

    credit = calculate_prepaid_tax_per_lot(df, datetime(2022, 12, 31), tax_data)

    assert credit.index.equals(df.index)
    assert (credit == 0.0).all()

@pytest.mark.parametrize('csv_content', [
    pytest.param('isin;year\nISIN123;2020\n', id='fewer-than-3-columns'),
    pytest.param('', id='empty-file'),
    pytest.param('isin;year;deemed_income\nISIN123;notayear;0.1\n', id='non-numeric-year'),
    pytest.param(None, id='missing-file'),
])
def test_load_csv_invalid_input_raises_input_error(csv_content: str | None, portfolio_with_purchases: Portfolio, tmp_path: Path) -> None:
    """Test that invalid CSV input raises InputError."""
    csv_path = tmp_path / 'tax.csv'
    if csv_content is not None:
        csv_path.write_text(csv_content)

    with pytest.raises(InputError):
        load_prepaid_tax_data_from_csv(csv_path, portfolio_with_purchases)

def test_load_csv_empty_portfolio_raises_input_error(tmp_path: Path) -> None:
    """Test that a portfolio without securities raises InputError."""
    csv_path = tmp_path / 'tax.csv'
    csv_path.write_text('isin;year;deemed_income\nISIN123;2020;0.1\n')

    with pytest.raises(InputError):
        load_prepaid_tax_data_from_csv(csv_path, Portfolio())

def test_load_csv_without_matching_isin_raises_input_error(portfolio_with_purchases: Portfolio, tmp_path: Path) -> None:
    """A CSV with only unknown ISINs currently raises InputError: the intended
    empty-DataFrame branch in load_prepaid_tax_data_from_csv is unreachable
    because set_index fails on a frame without year/security_id columns."""
    csv_path = tmp_path / 'tax.csv'
    csv_path.write_text('isin;year;deemed_income\nUNKNOWN000;2020;0.5\n')

    with pytest.raises(InputError):
        load_prepaid_tax_data_from_csv(csv_path, portfolio_with_purchases)

def test_load_multiple_files_later_file_overrides_earlier(portfolio_with_purchases: Portfolio, tmp_path: Path) -> None:
    """Test that duplicate (year, security_id) entries from a later file win."""
    first = tmp_path / 'first.csv'
    first.write_text('isin;year;deemed_income\nISIN123;2020;0.10\nISIN123;2021;0.30\n')
    second = tmp_path / 'second.csv'
    second.write_text('isin;year;deemed_income\nISIN123;2020;0.20\n')

    result = load_prepaid_tax_data([first, second], portfolio_with_purchases)

    assert result is not None
    assert len(result) == 2
    assert result.loc[(2020, 'sec-1'), 'deemed_income'] == pytest.approx(0.20)
    assert result.loc[(2021, 'sec-1'), 'deemed_income'] == pytest.approx(0.30)

def test_load_multiple_files_skips_bad_file(portfolio_with_purchases: Portfolio, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Test that a bad file is skipped with an error log while good files are still loaded."""
    good = tmp_path / 'good.csv'
    good.write_text('isin;year;deemed_income\nISIN123;2020;0.10\n')
    bad = tmp_path / 'bad.csv'
    bad.write_text('isin;year\nISIN123;2020\n')

    with caplog.at_level(logging.ERROR):
        result = load_prepaid_tax_data([good, bad], portfolio_with_purchases)

    assert result is not None
    assert len(result) == 1
    assert result.loc[(2020, 'sec-1'), 'deemed_income'] == pytest.approx(0.10)
    assert 'bad.csv' in caplog.text

def test_load_only_bad_files_returns_none(portfolio_with_purchases: Portfolio, tmp_path: Path) -> None:
    """Test that None is returned if no file could be loaded."""
    bad = tmp_path / 'bad.csv'
    bad.write_text('isin;year\nISIN123;2020\n')
    missing = tmp_path / 'missing.csv'

    assert load_prepaid_tax_data([bad, missing], portfolio_with_purchases) is None
