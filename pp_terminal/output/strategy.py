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

from abc import abstractmethod, ABC
from enum import Enum
from typing import Any

import pandas as pd
import rich
from rich.console import NewLine

from .table_decorator import TableDecorator, TableOptions, colorize


class OutputFormat(str, Enum):
    TABLE = 'table'
    CSV = 'csv'
    JSON = 'json'
    EXCEL = 'excel'


class Console(rich.console.Console):
    def print(self, *objects: Any, **kwargs: Any) -> None:
        kwargs['end'] = ''  # no newline at the end by default
        kwargs.setdefault('soft_wrap', True)
        super().print(*objects, **kwargs)


class OutputStrategy(ABC):
    @abstractmethod
    def result_table(self, df: pd.DataFrame | None, options: TableOptions) -> Any:
        pass

    def hint(self, message: str) -> str:  # pylint: disable=unused-argument
        return ''

    def warning(self, message: str) -> str:  # pylint: disable=unused-argument
        return ''

    def render_messages(self, messages: list[str], is_error: bool) -> str:  # pylint: disable=unused-argument
        return '; '.join(messages)

    def empty_result(self) -> str:
        return ''

    def text(self, message: str) -> str:  # pylint: disable=unused-argument
        return ''


class RichOutputStrategy(OutputStrategy):
    def result_table(self, df: pd.DataFrame | None, options: TableOptions) -> Any:
        if df is None or df.empty:
            return self.empty_result(), NewLine()

        table = TableDecorator(options)
        table.add_df(df)

        return NewLine(), table, NewLine()

    def hint(self, message: str) -> str:
        return ':bulb: [bold]Hint:[/bold] ' + self.text(message)

    def warning(self, message: str) -> str:
        return ':backhand_index_pointing_right: [bold]Warning:[/bold] ' + self.text(message)

    def render_messages(self, messages: list[str], is_error: bool) -> str:
        if not messages:
            return ''
        icon, color = ('❌', 'red') if is_error else ('⚠️', 'yellow')
        return colorize(f'{icon} {"; ".join(messages)}', color)

    def text(self, message: str) -> str:
        return message + "\n"

    def empty_result(self) -> str:
        return 'Nothing here..:sleeping: '


class CsvOutputStrategy(OutputStrategy):
    def result_table(self, df: pd.DataFrame | None, options: TableOptions) -> Any:
        if df is None:
            return self.empty_result()

        return (df.to_csv(index=options.show_index, float_format='%.2f'), )


class JsonOutputStrategy(OutputStrategy):
    def render_messages(self, messages: list[str], is_error: bool) -> Any:
        if not messages:
            return None
        return {'severity': 'error' if is_error else 'warning', 'text': '; '.join(messages)}

    def result_table(self, df: pd.DataFrame | None, options: TableOptions) -> Any:
        if df is None:
            return self.empty_result()

        if options.show_index:
            df = df.reset_index()
        return (df.to_json(orient='records'), )
