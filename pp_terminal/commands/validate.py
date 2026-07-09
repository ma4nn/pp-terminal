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
from functools import wraps
from typing import Callable, Any, cast

import typer
from typer.models import CommandFunctionType

from pp_terminal.domain.portfolio import Portfolio, get_security_by_id
from pp_terminal.exceptions import ValidationError
from pp_terminal.utils.config import Config
from pp_terminal.utils.helper import run_all_group_cmds
from pp_terminal.output.strategy import Console
from pp_terminal.domain.portfolio_snapshot import PortfolioSnapshot
from pp_terminal.validation.engine import validate_accounts
from pp_terminal.validation.engine import validate_securities

app = typer.Typer()
console = Console()
log = logging.getLogger(__name__)

validate_app = typer.Typer()
app.add_typer(validate_app, name="validate", help='Run configured validation rules on the portfolio data.')

exit_code = 0  # pylint: disable=invalid-name


def catch_errors(func: CommandFunctionType) -> Callable[..., CommandFunctionType]:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        global exit_code  # pylint: disable=global-statement

        try:
            return func(*args, **kwargs)
        except ValidationError:
            exit_code = 1
            ctx = kwargs['ctx'] if 'ctx' in kwargs else args[0] if len(args) > 0 else None

            if ctx and ctx.invoked_subcommand is None:
                raise typer.Exit(exit_code)  # pylint: disable=raise-missing-from

            return None

    return wrapper


@validate_app.command(name="securities")
@catch_errors
def log_validate_securities(ctx: typer.Context) -> None:
    """Validate security prices."""
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    config = cast(Config, ctx.obj.config)

    results = validate_securities(portfolio, PortfolioSnapshot(portfolio, datetime.now()), config)

    if not results:
        log.debug('No security validation rules configured or no securities to validate')
        return

    has_errors = False
    for security_id, result in results.items():
        if not result.violations:
            continue

        security = get_security_by_id(portfolio, security_id)

        for rule, message in result.violations:
            full_message = f'Security "{security.name}" ({security_id}) {message}'
            rule.log_violation(full_message)
            if rule.is_error():
                has_errors = True

    if has_errors:
        raise ValidationError()


@validate_app.command(name="accounts")
@catch_errors
def log_validate_accounts(ctx: typer.Context) -> None:
    """Validate deposit accounts using configured validation rules."""
    portfolio = cast(Portfolio, ctx.obj.portfolio)
    config = cast(Config, ctx.obj.config)

    snapshot = PortfolioSnapshot(portfolio, datetime.now())
    results = validate_accounts(portfolio, snapshot, config)

    if not results:
        log.debug('No account validation rules configured or no accounts to validate')
        return

    has_errors = False
    for account_id, result in results.items():
        if not result.violations:
            continue

        account_name = portfolio.deposit_accounts.loc[account_id, 'name']

        for rule, message in result.violations:
            full_message = f'Account "{account_name}" ({account_id}) {message}'
            rule.log_violation(full_message)
            if rule.is_error():
                has_errors = True

    if has_errors:
        raise ValidationError()


@validate_app.callback(invoke_without_command=True)
@run_all_group_cmds(validate_app)
def validate_all(ctx: typer.Context) -> None:  # pylint: disable=unused-argument
    if ctx.invoked_subcommand is None:
        raise typer.Exit(exit_code)
