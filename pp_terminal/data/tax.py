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
import typer
from pandera.typing import DataFrame

from pp_terminal.exceptions import InputError
from pp_terminal.output.strategy import Console
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import TaxPaidSchema, Percent, TaxLotSchema

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)


def get_exempt_multiplier_per_security(
    portfolio: Portfolio,
    default_exempt_rate_percent: Percent,
    exempt_rate_attr_uuid: str | None = None
) -> pd.Series:
    """
    Calculate exemption multiplier (1 - exempt_rate) for each security.

    Returns:
        Series indexed by securityId with multiplier values (e.g., 0.70 for 30% exemption)
    """
    if portfolio.securities.empty:
        return pd.Series(dtype=float)

    if exempt_rate_attr_uuid and exempt_rate_attr_uuid in portfolio.securities.columns:
        exempt_multiplier = (
            1 - portfolio.securities[[exempt_rate_attr_uuid]]
            .astype(float)
            .fillna(default_exempt_rate_percent / 100)
        )
        exempt_multiplier.columns = ['multiplier']
        return exempt_multiplier['multiplier']

    default_multiplier = 1 - default_exempt_rate_percent / 100
    return pd.Series(
        default_multiplier,
        index=portfolio.securities.index,
        name='multiplier'
    )


def load_prepaid_tax_data_from_csv(
    csv_path: Path,
    portfolio: Portfolio
) -> DataFrame[TaxPaidSchema]:
    """
    Load prepaid deemed income base from CSV (e.g. Vorabpauschale).

    CSV format: isin;year;[any_column_name]
    The last column is used as deemed income per share.
    """
    log.debug('Loading prepaid deemed income data from "%s"..', csv_path)

    try:
        df = pd.read_csv(csv_path, sep=';', comment='#')

        if df.shape[1] < 3:
            raise InputError(f"CSV must have at least 3 columns (isin;year;deemed_income), got {df.shape[1]}")

        # Use last column as deemed income, regardless of its name
        value_column = df.columns[-1]
        df = df.rename(columns={
            df.columns[0]: 'isin',
            df.columns[1]: 'year',
            value_column: 'deemed_income'
        })

        if portfolio.securities.empty:
            raise InputError("Portfolio has no securities, cannot map ISIN to security_id")

        # Handle duplicate ISINs by keeping first occurrence
        securities_df = portfolio.securities.reset_index()[['securityId', 'isin']]
        securities_df = securities_df.drop_duplicates(subset=['isin'], keep='first')
        isin_to_id = securities_df.set_index('isin')['securityId']
        df['security_id'] = df['isin'].map(isin_to_id)
        df = df[df['security_id'].notna()]

        if df.empty:
            empty_df = pd.DataFrame(columns=['deemed_income']).set_index(['year', 'security_id'])
            return TaxPaidSchema.validate(empty_df)

        df = df.set_index(['year', 'security_id'])

        return TaxPaidSchema.validate(df[['deemed_income']])
    except FileNotFoundError as e:
        raise InputError(f"Prepaid tax data CSV file not found: {csv_path}") from e
    except Exception as e:
        raise InputError(f"Failed to read prepaid tax data CSV: {e}") from e


def load_prepaid_tax_data(
    csv_paths: list[Path],
    portfolio: Portfolio
) -> DataFrame[TaxPaidSchema] | None:
    """
    Load prepaid deemed income data from one or multiple CSV files.
    Later files override earlier files for duplicate (year, security_id) entries.
    """
    if not csv_paths:
        return None

    all_data = []
    for csv_path in csv_paths:
        try:
            df = load_prepaid_tax_data_from_csv(csv_path, portfolio)
            if not df.empty:
                all_data.append(df)
        except InputError as e:
            log.error("Skipping prepaid tax file %s: %s", csv_path, e)

    if not all_data:
        return None

    combined = pd.concat(all_data)

    # Last file wins: keep last occurrence of duplicate (year, security_id)
    combined = combined[~combined.index.duplicated(keep='last')]

    return TaxPaidSchema.validate(combined)


def calculate_prepaid_tax_per_lot(
    lots: DataFrame[TaxLotSchema],
    current_date: datetime,
    tax_csv_data: DataFrame[TaxPaidSchema] | None
) -> pd.Series:
    """
    Calculate prepaid deemed income base per lot (e.g. Vorabpauschale deemed income).

    Args:
        lots: Current FIFO lots DataFrame (after matching sales)
        current_date: Evaluation date (for year range calculation)
        tax_csv_data: CSV data with deemed income base per share, indexed by (year, security_id)

    Returns:
        Series of prepaid deemed income base amounts, indexed to match the input lots dataframe.
    """
    if lots.empty or tax_csv_data is None or tax_csv_data.empty:
        return pd.Series(0.0, index=lots.index)

    # Track each lot by position: depot transfers can leave two lots sharing one
    # (date, accountId, securityId) key, so that key is not a safe per-lot identity
    lots_with_years = lots.copy().reset_index()
    lots_with_years['lotId'] = range(len(lots_with_years))
    lots_with_years['first_year'] = lots_with_years['date'].dt.year
    lots_with_years['last_year'] = current_date.year - 1

    # Filter out current year purchases (no prepaid deemed income yet)
    lots_with_years = lots_with_years[lots_with_years['last_year'] >= lots_with_years['first_year']]

    if lots_with_years.empty:
        return pd.Series(0.0, index=lots.index)

    # Create year range for each lot
    lots_with_years['year_range'] = lots_with_years.apply(
        lambda row: list(range(row['first_year'], row['last_year'] + 1)),
        axis=1
    )

    # Explode into lot-year pairs
    lot_years = lots_with_years.explode('year_range').rename(columns={'year_range': 'year'})

    # Calculate month_factor (prorate for purchase year)
    lot_years['month_factor'] = 1.0
    is_first_year = lot_years['year'] == lot_years['first_year']
    lot_years.loc[is_first_year, 'month_factor'] = (
        (13 - lot_years.loc[is_first_year, 'date'].dt.month) / 12.0
    )

    tax_csv_data = tax_csv_data.reset_index().rename(columns={'security_id': 'securityId'})
    lot_years = lot_years.merge(
        tax_csv_data,
        on=['year', 'securityId'],
        how='inner'
    )

    if lot_years.empty:
        return pd.Series(0.0, index=lots.index)

    lot_years['deemed_income_total'] = (
        lot_years['shares'] * lot_years['deemed_income'] * lot_years['month_factor']
    )

    # Group by the positional lot id (not the non-unique index key) to sum deemed income across all years
    per_lot_deemed_income = lot_years.groupby('lotId')['deemed_income_total'].sum()

    # Realign to the original lots positionally, filling lots without deemed income with 0
    aligned = per_lot_deemed_income.reindex(range(len(lots)), fill_value=0.0)
    return pd.Series(aligned.to_numpy(), index=lots.index)
