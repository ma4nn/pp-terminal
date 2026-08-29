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

import re

import pandas as pd
from rich.console import Console

from pp_terminal.output.table_decorator import TableDecorator, TableOptions

DIM = '\x1b[2m'


def _render(table: TableDecorator) -> str:
    console = Console(force_terminal=True, width=200)
    with console.capture() as capture:
        console.print(table)
    return capture.get()


def _line_with(out: str, needle: str) -> str:
    return next(line for line in out.splitlines() if needle in line)


def test_dimmed_rows_are_grayed_out() -> None:
    df = pd.DataFrame({'name': ['Active One', 'Retired One']}, index=['a', 'r'])
    table = TableDecorator(TableOptions(show_index=False, show_total=False, dimmed_rows={'r'}))
    table.add_df(df)

    out = _render(table)
    active_line = next(line for line in out.splitlines() if 'Active One' in line)
    retired_line = next(line for line in out.splitlines() if 'Retired One' in line)

    assert DIM in retired_line
    assert DIM not in active_line


def test_no_row_is_dimmed_by_default() -> None:
    df = pd.DataFrame({'name': ['One', 'Two']}, index=['a', 'b'])
    table = TableDecorator(TableOptions(show_index=False, show_total=False))
    table.add_df(df)

    assert DIM not in _render(table)


def test_footer_shows_correct_total_for_money_column() -> None:
    df = pd.DataFrame({
        'name': ['Alpha', 'Beta'],
        'balance': [111.0, 222.0],
        'currency': ['EUR', 'EUR'],
    }, index=['a', 'b'])
    table = TableDecorator(TableOptions(show_index=False, show_total=True))
    table.add_df(df)

    out = _render(table)

    assert re.search(r'111[.,]00', out)
    assert re.search(r'222[.,]00', out)
    total_line = _line_with(out, 'Total')
    assert re.search(r'333[.,]00', total_line)


def test_footer_skips_price_column_total() -> None:
    df = pd.DataFrame({
        'name': ['Alpha', 'Beta'],
        'balance': [111.0, 222.0],
        'purchasePrice': [400.0, 500.0],
        'currency': ['EUR', 'EUR'],
    }, index=['a', 'b'])
    table = TableDecorator(TableOptions(show_index=False, show_total=True))
    table.add_df(df)

    total_line = _line_with(_render(table), 'Total')

    assert re.search(r'333[.,]00', total_line)
    assert '900' not in total_line


def test_footer_skips_non_summable_columns() -> None:
    df = pd.DataFrame({
        'name': ['Alpha', 'Beta'],
        'balance': [111.0, 222.0],
        'ter': [0.1, 0.2],
        'currency': ['EUR', 'EUR'],
    }, index=['a', 'b'])
    table = TableDecorator(TableOptions(show_index=False, show_total=True, non_summable_columns=('ter',)))
    table.add_df(df)

    out = _render(table)
    total_line = _line_with(out, 'Total')

    assert re.search(r'333[.,]00', total_line)
    assert '0.3' not in total_line
    assert '0.1' in out  # the column itself is still rendered, only its total is dropped


def test_non_summable_columns_stay_right_justified() -> None:
    """Dropping a column from the total must not turn it into a left-justified text column."""
    df = pd.DataFrame({'name': ['Alpha'], 'ter': [0.1]}, index=['a'])
    table = TableDecorator(TableOptions(show_index=False, show_total=True, non_summable_columns=('ter',)))
    table.add_df(df)

    value_line = _line_with(re.sub(r'\x1b\[[0-9;]*m', '', _render(table)), '0.10')

    assert value_line.endswith('0.10 │')


def test_total_excludes_footer_lines_and_precedes_them() -> None:
    df = pd.DataFrame({
        'name': ['Alpha', 'Beta', 'Later'],
        'balance': [111.0, 222.0, 999.0],
        'currency': ['EUR', 'EUR', 'EUR'],
    }, index=['a', 'b', 'c'])
    table = TableDecorator(TableOptions(show_index=False, show_total=True, footer_lines=1))
    table.add_df(df)

    out = _render(table)

    total_line = _line_with(out, 'Total')
    assert re.search(r'333[.,]00', total_line)
    assert '332' not in out  # 111 + 222 + 999 = 1332 would include the footer line
    assert out.find('Total') < out.find('Later')


def test_show_index_renders_id_column() -> None:
    df = pd.DataFrame({'name': ['Alpha']}, index=['row1'])
    table = TableDecorator(TableOptions(show_index=True, show_total=False))
    table.add_df(df)

    out = _render(table)

    assert 'ID' in out
    assert 'row1' in out


def test_hidden_index_is_not_rendered() -> None:
    df = pd.DataFrame({'name': ['Alpha']}, index=['row1'])
    table = TableDecorator(TableOptions(show_index=False, show_total=False))
    table.add_df(df)

    out = _render(table)

    assert 'ID' not in out
    assert 'row1' not in out
    assert 'Alpha' in out


def test_camel_case_headers_are_title_cased() -> None:
    df = pd.DataFrame({'meanBalance': ['x'], 'accountId': ['y'], 'wkn': ['z']})
    table = TableDecorator(TableOptions(show_index=False, show_total=False))
    table.add_df(df)

    out = _render(table)

    assert 'Mean Balance' in out
    assert 'Account ID' in out
    assert 'WKN' in out
    assert 'meanBalance' not in out
    assert 'accountId' not in out


def test_money_value_renders_with_currency_formatting() -> None:
    df = pd.DataFrame({'name': ['Alpha'], 'balance': [210.5], 'currency': ['EUR']}, index=['a'])
    table = TableDecorator(TableOptions(show_index=False, show_total=False))
    table.add_df(df)

    assert re.search(r'210[.,]50', _render(table))


def test_nan_money_value_renders_blank() -> None:
    df = pd.DataFrame({'name': ['Full', 'Void'], 'balance': [210.5, float('nan')], 'currency': ['EUR', 'EUR']}, index=['a', 'b'])
    table = TableDecorator(TableOptions(show_index=False, show_total=False))
    table.add_df(df)

    out = _render(table)

    assert re.search(r'\d', _line_with(out, 'Full'))
    assert not re.search(r'\d', _line_with(out, 'Void'))


def test_shares_column_renders_with_share_precision() -> None:
    df = pd.DataFrame({'name': ['Alpha'], 'shares': [70.0]}, index=['a'])
    table = TableDecorator(TableOptions(show_index=False, show_total=False))
    table.add_df(df)

    assert '70.0000' in _render(table)


def test_all_zero_column_is_dropped() -> None:
    df = pd.DataFrame({'name': ['Alpha', 'Beta'], 'zeroBalance': [0.0, 0.0]}, index=['a', 'b'])
    table = TableDecorator(TableOptions(show_index=False, show_total=False))
    table.add_df(df)

    out = _render(table)

    assert 'Alpha' in out
    assert 'Name' in out
    assert 'Zero Balance' not in out


def test_dataframe_with_only_empty_values_renders_nothing() -> None:
    df = pd.DataFrame({'zeroBalance': [0.0]}, index=['a'])
    table = TableDecorator(TableOptions())
    table.add_df(df)

    assert _render(table).strip() == ''
