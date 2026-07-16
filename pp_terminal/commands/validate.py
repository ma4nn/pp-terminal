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

import logging
from datetime import datetime
from typing import Annotated, Callable, cast

import click
import typer

from pp_terminal.domain.portfolio import Portfolio, get_security_by_id
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.utils.config import Config
from pp_terminal.validation.engine import ValidationResult, configured_rule_types, validate_accounts, validate_securities
from pp_terminal.validation.rules import known_rule_types

app = typer.Typer()
log = logging.getLogger(__name__)


def _log_violations(results: dict[str, ValidationResult], describe_entity: Callable[[str], str]) -> bool:
    has_errors = False
    for entity_id, result in results.items():
        for rule, message in result.violations:
            rule.log_violation(f'{describe_entity(entity_id)} {message}')
            if rule.is_error():
                has_errors = True

    return has_errors


@app.command(name="validate")
def run_validations(
        ctx: typer.Context,
        rule: Annotated[list[str] | None, typer.Option('--rule', click_type=click.Choice(sorted(known_rule_types())), help='Run only rules of this type (repeatable); defaults to all configured rules.')] = None,
) -> None:
    """Run configured validation rules on the portfolio data."""
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    config = cast(Config, ctx.obj.config)

    rule_types = set(rule) if rule else None
    if rule_types is not None:
        unmatched = rule_types - configured_rule_types(config)
        if unmatched:
            log.warning('No rules of type(s) %s configured, nothing to validate for them', ', '.join(sorted(unmatched)))

    snapshot = PortfolioSnapshot(portfolio, datetime.now())

    has_errors = _log_violations(
        validate_accounts(portfolio, snapshot, config, rule_types),
        lambda account_id: f'Account "{portfolio.deposit_accounts.loc[account_id, "name"]}" ({account_id})',
    )
    has_errors |= _log_violations(
        validate_securities(portfolio, snapshot, config, rule_types),
        lambda security_id: f'Security "{get_security_by_id(portfolio, security_id).name}" ({security_id})',
    )

    if has_errors:
        raise typer.Exit(1)
