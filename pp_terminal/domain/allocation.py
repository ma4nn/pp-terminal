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

from pp_terminal.exceptions import InputError


def build_category_map(taxonomy_assignments: pd.DataFrame, taxonomy_name: str, item_type: str = 'security') -> tuple[dict[str, str], list[str]]:
    """Map each item of the given type (security or account) to its category in the given taxonomy.

    Items assigned to several categories are mapped to their highest-weight
    (dominant) category; their ids are returned separately so the caller can warn
    about possible allocation drift.
    """
    if taxonomy_assignments.empty:
        raise InputError("Portfolio has no taxonomies to preserve allocation against")

    available = sorted(taxonomy_assignments['taxonomyName'].unique())
    if taxonomy_name not in available:
        raise InputError(f"Unknown taxonomy '{taxonomy_name}'. Available: {', '.join(available)}")

    assignments = taxonomy_assignments[
        (taxonomy_assignments['taxonomyName'] == taxonomy_name)
        & (taxonomy_assignments['itemType'] == item_type)
    ]

    category_by_item: dict[str, str] = {}
    multi_category_items: list[str] = []
    for item_id, group in assignments.groupby('itemId'):
        if len(group) > 1:
            multi_category_items.append(str(item_id))
            dominant = group.loc[group['weight'].idxmax()]
            category_by_item[str(item_id)] = str(dominant['categoryName'])
        else:
            category_by_item[str(item_id)] = str(group.iloc[0]['categoryName'])

    return category_by_item, multi_category_items
