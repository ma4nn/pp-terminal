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

from datetime import date
from typing import List, Any

import babel.numbers
import pandas as pd
from babel import Locale
from babel.numbers import format_currency

from pp_terminal.domain.schemas import Money

_PRECISION: int = 4


def set_precision(precision: int) -> None:
    global _PRECISION  # pylint: disable=global-statement
    _PRECISION = precision


def currency_exists(currency_code: str, locale: str | None = None) -> bool:
    return currency_code in Locale(str(locale)).currencies


def format_money(value: Money, currency: str | None = None, locale: str | None = babel.numbers.LC_NUMERIC) -> str:
    if pd.isna(value) or not isinstance(value, Money):
        return ''

    try:
        currency = currency if not currency is None and not pd.isna(currency) and currency_exists(currency, locale) else ''

        return format_currency(value, currency, locale=locale)
    except Exception:  # pylint: disable=broad-exception-caught
        # fallback e.g. if system locale is None/not set, or currency does not exist
        return f"{value:.2f}"


def format_shares(value: float) -> str:
    if pd.isna(value) or not isinstance(value, float):
        return ''

    return f"{float(value):.{_PRECISION}f}"


def format_percent(value: float) -> str:
    if pd.isna(value) or not isinstance(value, float):
        return ''

    return f"{float(value) * 100:.2f}%"


def enum_list_to_values(enum_list: List[Any]) -> List[Any]:
    return [item.value for item in enum_list]


def get_last_year() -> str:
    return str(date.today().year - 1)


def footer() -> str:
    return 'All results are non-binding and provided without any guarantee. Actual values may differ.'
