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

import atexit
import locale
from pathlib import Path
from types import SimpleNamespace
import logging
import os
from typing import Optional

from rich import print # pylint: disable=redefined-builtin
from rich.logging import RichHandler
import typer
from typing_extensions import Annotated
from typer_config import use_config

from pp_terminal.output.strategy_factory import create_strategy
from pp_terminal.utils.config import validated_config_callback, get_config
from pp_terminal.exceptions import InputError
from pp_terminal.utils.helper import set_precision
from pp_terminal.output.strategy import OutputFormat
from pp_terminal.utils.plugins import load_command_plugins
from pp_terminal.data.pp_portfolio_builder import PpPortfolioBuilder, CachedPpPortfolioBuilder
from pp_terminal.data.xml_anonymizer import XmlAnonymizer
from pp_terminal.mcp_server import start_mcp
from . import __version__

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
app.add_typer(typer.Typer(no_args_is_help=True), name="simulate", help="Run simulations on the portfolio data, like share sells or German Vorabpauschale.")
app.add_typer(typer.Typer(no_args_is_help=True), name="view", help="View details about portfolio entities like accounts or securities.")
app.add_typer(typer.Typer(no_args_is_help=True), name="import", help="Import transactions, securities, and events into the Portfolio Performance XML file.")

# init default logging (this is e.g. import for errors during command plugin load)
logging.basicConfig(level=logging.WARN, format="%(message)s", datefmt="[%X]", handlers=[RichHandler(rich_tracebacks=False, show_time=False, show_path=False)])
log = logging.getLogger(__name__)

locale.setlocale(category=locale.LC_ALL, locale='')

# Load external plugins dynamically
load_command_plugins(app)

app.command(name="mcp")(start_mcp)


def version_callback(value: bool) -> None:
    if value:
        print(f"[bold]pp-terminal[/bold] version: {__version__}")
        raise typer.Exit()


def _create_anonymized_temp_file(original_file: Path) -> Path:
    """Create a deterministic anonymized version of the XML file next to the original."""
    temp_path = original_file.parent / f".{original_file.stem}.anon{original_file.suffix}"

    # Use deterministic seed based on file path
    seed = hash(str(original_file.resolve())) % (2**31)
    log.debug("Anonymizing data with seed %d", seed)

    anonymizer = XmlAnonymizer(seed=seed, config=get_config().get('anonymize', {}).get('attributes', {}))
    anonymizer.anonymize_file(original_file, temp_path)
    log.debug("Created anonymized file at %s", temp_path)

    # Register cleanup on program exit
    def cleanup() -> None:
        try:
            os.unlink(temp_path)
            log.debug("Removed temporary anonymized file at %s", temp_path)
        except OSError as e:
            log.warning("Failed to remove temporary file %s: %s", temp_path, e)

    atexit.register(cleanup)

    return temp_path


@app.callback(
    invoke_without_command=True,
    epilog="Small insights today, bigger returns tomorrow.",
    help=f"[bold]pp-terminal[/bold] version {__version__} by [link=https://dev-investor.de]dev-investor[/link]\n\nThe Analytic Companion for Portfolio Performance"
)
@use_config(validated_config_callback)
def main(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        ctx: typer.Context,
        file: Annotated[Path, typer.Option(help="Portfolio Performance XML file.", show_default=False, exists=True, file_okay=True, dir_okay=False, readable=True)],
        output: OutputFormat = OutputFormat.TABLE,
        precision: int = 4,
        cache: Annotated[bool, typer.Option('--cache/--no-cache', help='Create cache file for XML.')] = True,
        anonymize: Annotated[bool, typer.Option(help='Anonymize data before processing.')] = False,
        version: Annotated[  # pylint: disable=unused-argument
            Optional[bool],
            typer.Option("--version", callback=version_callback, is_eager=True),  # declared the option name to avoid --no-version
        ] = None,
        verbose: Annotated[Optional[bool], typer.Option('--verbose', help='Enable verbose logging.')] = None,
) -> None:

    if verbose:
        logging.basicConfig(force=True, level=logging.DEBUG, format="%(message)s", datefmt="[%X]", handlers=[RichHandler(rich_tracebacks=True, show_time=False)])

    set_precision(precision)
    should_anonymize = anonymize or 'anonymize' in get_config()
    source_file = _create_anonymized_temp_file(file) if should_anonymize else file

    try:
        builder = CachedPpPortfolioBuilder(config=get_config()) if cache else PpPortfolioBuilder(config=get_config())

        ctx.obj = SimpleNamespace(
            source_file=source_file,
            portfolio=builder.construct(source_file),
            output=create_strategy(output),
            config=get_config(),
            verbose=verbose or False)

    except (RuntimeError, InputError) as e:
        if verbose:
            raise e

        log.critical(e)
        raise typer.Abort()


if __name__ == "__main__":
    app()
