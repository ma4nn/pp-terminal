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
from typing import Any

import pandas as pd

from pp_terminal.domain.schemas import Attribute, TransactionType


def retired_row_labels(df: pd.DataFrame) -> set[Any]:
    """Returns the index labels of retired (inactive) rows, empty if not applicable."""
    if 'isRetired' not in df.columns:
        return set()
    return set(df.index[df['isRetired'].eq(True)])


def filter_earlier_than(df: pd.DataFrame, target_date: datetime) -> pd.DataFrame:
    return df[df.index.get_level_values('date') <= target_date]


def filter_later_than(df: pd.DataFrame, target_date: datetime) -> pd.DataFrame:
    return df[df.index.get_level_values('date') >= target_date]


def filter_by_security(df: pd.DataFrame, security_id: str) -> pd.DataFrame:
    return df[df.index.get_level_values('securityId') == security_id]


def filter_by_account(df: pd.DataFrame, account_id: str) -> pd.DataFrame:
    return df[df.index.get_level_values('accountId') == account_id]


def filter_by_account_and_security(df: pd.DataFrame, account_id: str, security_id: str) -> pd.DataFrame:
    return df[(df.index.get_level_values('accountId') == account_id) & (df.index.get_level_values('securityId') == security_id)]


def filter_by_type(df: pd.DataFrame, transaction_types: TransactionType| list[TransactionType]) -> pd.DataFrame:
    if not isinstance(transaction_types, list):
        transaction_types = [transaction_types]

    # we store only the name of the enum to save some space, so we have to convert it here
    cleaned_transaction_types = []
    for transaction_type in transaction_types:
        cleaned_transaction_types.append(transaction_type.name)

    return df[df['type'].isin(cleaned_transaction_types)]


def filter_not_retired(df: pd.DataFrame) -> pd.DataFrame:
    if 'isRetired' not in df.columns:
        return df

    return df[~df['isRetired']]


def drop_empty_values(df: pd.DataFrame | pd.Series, keep_columns: tuple[str, ...] = ()) -> pd.DataFrame:
    if df.empty:
        return df

    # Create mask for NaN values and zero values (only for numeric columns)
    if isinstance(df, pd.DataFrame):
        # For DataFrames, handle numeric and non-numeric columns separately
        numeric_cols = df.select_dtypes(include='number').columns
        mask = df.isna()
        if len(numeric_cols) > 0:
            mask = mask | (df[numeric_cols] == 0).reindex(columns=df.columns, fill_value=False)
        df = df[~mask]
    else:
        # For Series, check if numeric before applying == 0
        if pd.api.types.is_numeric_dtype(df):
            df = df[~(df.isna() | (df == 0))]
        else:
            df = df[~df.isna()]

    df.dropna(how='all', axis=0, inplace=True)
    if isinstance(df, pd.DataFrame):
        empty = [column for column in df.columns if column not in keep_columns and df[column].isna().all()]
        df.drop(columns=empty, inplace=True)

    return df


def clean_for_display(df: pd.DataFrame, attributes: dict[str, Attribute]) -> pd.DataFrame:
    df = df.drop(columns=[col for col in df.columns if col.startswith('_')])
    return df.rename(columns={uuid: attr.column for uuid, attr in attributes.items()})


_TAXONOMY_FULL_WEIGHT = 10000


def pivot_taxonomy_columns(
    df: pd.DataFrame,
    taxonomy_assignments: pd.DataFrame,
    id_column: str,
    item_type: str
) -> pd.DataFrame:
    if taxonomy_assignments.empty:
        return df

    id_in_columns = id_column in df.columns
    id_in_index = id_column == df.index.name or id_column in (df.index.names or [])
    if not id_in_columns and not id_in_index:
        return df

    filtered = taxonomy_assignments[taxonomy_assignments['itemType'] == item_type].copy()
    if filtered.empty:
        return df

    def _format_label(row: pd.Series) -> str:
        if row['weight'] == _TAXONOMY_FULL_WEIGHT:
            return str(row['categoryName'])
        pct = round(row['weight'] / 100)
        return f"{row['categoryName']} ({pct}%)"

    filtered['label'] = filtered.apply(_format_label, axis=1)

    grouped = (filtered.groupby(['itemId', 'taxonomyName'])['label']
               .agg(', '.join)
               .unstack('taxonomyName')
               .rename_axis(index=id_column, columns=None))

    if id_in_columns:
        return df.merge(grouped, left_on=id_column, right_index=True, how='left')
    return df.merge(grouped, left_index=True, right_index=True, how='left')


def unstack_column_by_currency(df: pd.DataFrame, column: str, base_currency: str) -> pd.DataFrame:
    column_unstacked = df[column].unstack(level='currency')
    df_modified = df.drop(columns=column).reset_index(level='currency', drop=True)
    df_modified = df_modified[~df_modified.index.get_level_values('accountId').duplicated()]  # drop duplicates ignoring currency

    df_modified = df_modified.join(column_unstacked, how='outer')

    if base_currency in df_modified:
        df_modified.sort_values(by=base_currency, inplace=True)

    return df_modified
