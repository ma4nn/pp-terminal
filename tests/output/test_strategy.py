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

from pp_terminal.output.strategy import RichOutputStrategy, CsvOutputStrategy, JsonOutputStrategy


def test_rich_renders_error_with_red_icon() -> None:
    result = RichOutputStrategy().render_messages(['too high', 'no price'], is_error=True)

    assert result == '[red]❌ too high; no price[/red]'


def test_rich_renders_warning_with_yellow_icon() -> None:
    result = RichOutputStrategy().render_messages(['negative balance'], is_error=False)

    assert result == '[yellow]⚠️ negative balance[/yellow]'


def test_rich_renders_empty_for_no_messages() -> None:
    assert RichOutputStrategy().render_messages([], is_error=True) == ''


def test_csv_omits_icon_markup_and_severity() -> None:
    assert CsvOutputStrategy().render_messages(['too high', 'no price'], is_error=True) == 'too high; no price'
    assert CsvOutputStrategy().render_messages([], is_error=False) == ''


def test_json_carries_structured_severity() -> None:
    assert JsonOutputStrategy().render_messages(['too high', 'no price'], is_error=True) == {'severity': 'error', 'text': 'too high; no price'}
    assert JsonOutputStrategy().render_messages(['stale'], is_error=False) == {'severity': 'warning', 'text': 'stale'}
    assert JsonOutputStrategy().render_messages([], is_error=True) is None
