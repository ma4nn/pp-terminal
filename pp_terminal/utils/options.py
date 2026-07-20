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
import typer

from pp_terminal.domain.schemas import Percent, Money


def allowance_callback(ctx: typer.Context, param: typer.CallbackParam, value: Money | None) -> Money:  # pylint: disable=unused-argument
    # 1. If provided via CLI, use it; 2. otherwise fall back to config (or the 1000 EUR default)
    if value is not None:
        return value
    return Money(ctx.obj.config.tax.allowance)


def tax_rate_callback(ctx: typer.Context, param: typer.CallbackParam, value: Percent | None) -> Percent:  # pylint: disable=unused-argument
    # 1. If provided via CLI, use it
    if value is not None:
        return value

    # 2. Use the config value only if the user set it explicitly (a bare default should still prompt)
    tax = ctx.obj.config.tax
    if 'rate' in tax.model_fields_set:
        return Percent(tax.rate)

    # 3. Prompt user with 4. hard-coded default
    return Percent(typer.prompt(
        "Tax Rate (%)",
        type=float,
        default=0.25 * (1 + 0.055) * 100
    ))


def exempt_rate_callback(ctx: typer.Context, param: typer.CallbackParam, value: Percent | None) -> Percent:  # pylint: disable=unused-argument
    # 1. If provided via CLI, use it
    if value is not None:
        return value

    # 2. Use the config value only if the user set it explicitly (a bare default should still prompt)
    tax = ctx.obj.config.tax
    if 'exemption_rate' in tax.model_fields_set:
        return Percent(tax.exemption_rate)

    # 3. Prompt user with 4. hard-coded default
    return Percent(typer.prompt(
        "Default Exemption Rate (%)",
        type=float,
        default=30
    ))
