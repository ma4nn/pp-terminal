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

from typing import Any, cast

import pandas as pd
import typer

from pp_terminal.commands.simulate_pmt import PmtConfig, split_return_scenario
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.output.strategy import OutputStrategy, Console
from pp_terminal.output.table_decorator import TableOptions, format_value
from pp_terminal.utils.config import Config, command_config
from pp_terminal.utils.helper import footer

app = typer.Typer()
console = Console()


def prepare_taxonomies_df(
        portfolio: Portfolio,
        taxonomy: str | None = None,
        return_scenarios: list[dict[str, float]] | None = None,
) -> pd.DataFrame:
    assignments = portfolio.taxonomy_assignments
    if assignments.empty:
        return pd.DataFrame(columns=['Taxonomy', 'Category', 'Securities', 'Accounts'])

    counts = (assignments.groupby(['taxonomyName', 'categoryName', 'itemType'])
              .size()
              .unstack('itemType', fill_value=0)
              .rename(columns={'security': 'Securities', 'account': 'Accounts'})
              .reset_index()
              .rename(columns={'taxonomyName': 'Taxonomy', 'categoryName': 'Category'}))

    for col in ('Securities', 'Accounts'):
        if col not in counts.columns:
            counts[col] = 0

    result = counts[['Taxonomy', 'Category', 'Securities', 'Accounts']].sort_values(['Taxonomy', 'Category'])

    return _add_return_columns(result, taxonomy, return_scenarios or [])


def _add_return_columns(df: pd.DataFrame, taxonomy: str | None, scenarios: list[dict[str, float]]) -> pd.DataFrame:
    """Attaches the per-category returns configured for `taxonomy`, one column per scenario, blank for other taxonomies."""
    if not taxonomy or not scenarios:
        return df

    in_taxonomy = df['Taxonomy'] == taxonomy
    for index, scenario in enumerate(scenarios, start=1):
        column = 'Expected Return' if len(scenarios) == 1 else f'Expected Return {index}'
        default, overrides = split_return_scenario(scenario)
        rates = df['Category'].map(overrides)
        if default is not None:
            rates = rates.fillna(default)
        df[column] = rates.where(in_taxonomy)

    return df


def _format_counts(value: Any, column_name: str, row: pd.Series) -> str:
    if column_name in ('Securities', 'Accounts'):
        return '' if pd.isna(value) else str(int(value))

    if column_name.startswith('Expected Return'):
        if pd.isna(value) or row.isin(['Total']).any():  # a summed return is meaningless
            return ''
        return f"{float(value):.2f}%"

    return format_value(value, column_name, row)


@app.command(name="taxonomies")
def print_taxonomies(ctx: typer.Context) -> None:
    """Show all taxonomies with their categories, assignment counts, and any configured per-category returns."""
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    output = cast(OutputStrategy, ctx.obj.output)
    config = cast(Config, ctx.obj.config)

    scenarios = [entry for entry in (command_config(config, PmtConfig).returns or []) if isinstance(entry, dict)]
    df = prepare_taxonomies_df(portfolio, config.taxonomy, scenarios)

    console.print(*output.result_table(
        df, TableOptions(title="Taxonomies", caption=f"{len(df)} categories", show_index=False, value_formatter=_format_counts)
    ))

    if any(col.startswith('Expected Return') and df[col].notna().any() for col in df.columns):
        console.print(output.hint(
            f"Expected Return values are the per-category [cyan]returns[/cyan] configured for "
            f"[cyan]simulate pmt[/cyan] on the '{config.taxonomy}' taxonomy; blank cells have no rate configured."
        ))

    console.print(output.text(footer()), style="dim")
