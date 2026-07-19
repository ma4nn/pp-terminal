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
from rich.console import Console

from pp_terminal.output.table_decorator import TableDecorator, TableOptions

DIM = '\x1b[2m'


def _render(table: TableDecorator) -> str:
    console = Console(force_terminal=True, width=200)
    with console.capture() as capture:
        console.print(table)
    return capture.get()


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
