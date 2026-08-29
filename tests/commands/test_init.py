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

import tomllib

from typer.testing import CliRunner

from pp_terminal.commands.init import CONFIG_TEMPLATE
from pp_terminal.main import app
from pp_terminal.utils.config import build_config_model


def _activate(template: str) -> str:
    """Uncomment every commented-out setting (prose stays commented) to get an active config."""
    lines = (line[1:] if line.startswith('#') and not line.startswith('# ') else line for line in template.splitlines())
    return '\n'.join(lines)


def test_should_print_template_without_requiring_a_file() -> None:
    result = CliRunner().invoke(app, ['init'])

    assert result.exit_code == 0
    assert 'pp-terminal configuration' in result.output


def test_should_print_valid_toml() -> None:
    result = CliRunner().invoke(app, ['init'])

    # every setting is commented out, so the emitted document is valid but empty
    assert not tomllib.loads(result.output)


def test_activated_template_should_validate_against_config_model() -> None:
    data = tomllib.loads(_activate(CONFIG_TEMPLATE))

    # guards against drift between the template and the Pydantic config models
    build_config_model().model_validate(data)

    assert data['commands']['simulate']['pmt']['returns'] == [2, 4, 6, {'*': 4.0, 'Eigenkapital': 5.0, 'Fremdkapital': 1.9}]
    assert data['commands']['validate']['securities']['rules'][0]['type'] == 'price-staleness'
