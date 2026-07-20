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

import pytest
from _pytest.fixtures import TopRequest
from typer.testing import CliRunner

from pp_terminal import __version__
from pp_terminal.main import app


def test_should_print_version() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ['--version'])

    assert result.exit_code == 0
    assert f'version: {__version__}' in result.output

def test_should_abort_without_traceback_on_malformed_xml(request: TopRequest, caplog: pytest.LogCaptureFixture) -> None:
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'invalid.xml'

    with caplog.at_level(logging.CRITICAL):
        result = runner.invoke(app, ['--file', str(xml_file), '--no-cache', 'view', 'accounts'])

    assert result.exit_code != 0
    assert 'unable to import the Portfolio Performance xml file' in caplog.text
    assert 'Traceback' not in result.output
