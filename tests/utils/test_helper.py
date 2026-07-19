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

import pytest

from pp_terminal.utils.helper import format_money
from pp_terminal.domain.schemas import Money


@pytest.mark.parametrize('expected, money, currency, locale_value', [
    ('3.20', 3.2, 'EUR', None),
    ('3.20', 3.2, '', None),
    ('€3.20', 3.2, 'EUR', 'en_US'),
    ('3.20', 3.2, '', 'en_US'),
    ('€0.00', 0.0, 'EUR', 'en_US'),
    ('3,20\xa0€', 3.2, 'EUR', 'de_DE'),
    ('3,20\xa0', 3.2, '', 'de_DE'),
    ('100.00', 100.0, 'xx', 'en_US'),  # unknown currency falls back to plain formatting
    ('', None, 'EUR', 'en_US'),
    ('', 9, 'EUR', 'en_US'),  # int is not Money
    ('', float('nan'), 'EUR', None),
])
def test_format_money(expected: str, money: Money, currency: str, locale_value: str) -> None:
    assert format_money(money, currency, locale_value) == expected
