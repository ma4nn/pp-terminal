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

from pp_terminal.exceptions import InputError
from pp_terminal.domain.schemas import Attribute


def normalize_columns(
    requested_columns: list[str],
    available_columns: list[str],
    attributes: dict[str, Attribute] | None = None
) -> list[str]:
    """
    Normalize and validate column names (case-insensitive matching).

    Args:
        requested_columns: List of column names requested by user (UUIDs or regular column names)
        available_columns: List of actual column names in the dataframe
        attributes: Optional mapping of UUID to Attribute (for error messages only)

    Returns:
        List of normalized column names matching the dataframe columns
    """
    normalized = []
    available_lower = {col.lower(): col for col in available_columns}

    if 'currency' in available_columns:
        normalized.append(available_lower['currency'])

    for col in requested_columns:
        col_lower = col.strip().lower()

        if col_lower in available_lower:
            normalized.append(available_lower[col_lower])
        else:
            # Build helpful error message with both UUIDs and friendly names
            uuid_keys = set(attributes.keys()) if attributes else set()
            available_names = sorted([
                col for col in available_columns
                if not col.startswith('_') and col not in uuid_keys
            ])
            # Add attribute columns with their friendly names
            if attributes:
                for uuid, attr in sorted(attributes.items(), key=lambda x: x[1].name):
                    available_names.append(f"{uuid} ({attr.column})")
            raise InputError(f"Column '{col}' not found. Available columns: {', '.join(available_names)}")

    return list(dict.fromkeys(normalized))  # make unique
