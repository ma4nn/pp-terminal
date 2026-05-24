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

import json
from pathlib import Path

from typer.testing import CliRunner
from _pytest.fixtures import TopRequest

from pp_terminal.main import app


def test_vap_2025_csv_output(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    xml_file = fixtures_dir / 'kommer.ids.xml'
    golden_file = fixtures_dir / 'expected_vap_2025_kommer.csv'

    result = runner.invoke(app, [
        '--file', str(xml_file),
        '--output', 'csv',
        '--no-cache',
        'simulate', 'vap',
        '--year', '2025',
        '--base-rate', '2.53',
        '--tax-rate', '26.375',
        '--exempt-rate', '0'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    expected_output = Path(golden_file).read_text(encoding='utf-8')
    assert result.output == expected_output


def test_share_sell_csv_output(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    golden_file = fixtures_dir / 'expected_share_sell_kommer.csv'

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--config', str(fixtures_dir / 'kommer.toml'),
        '--output', 'csv',
        '--no-cache',
        'simulate', 'share-sell',
        '99b9419f-8c70-422e-8e8e-05eadb4507ec',
        '--account-id', 'dc6fac85-6c6e-47f1-a968-2b5b84d90997',
        '--tax-rate', '26.375'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    expected_output = Path(golden_file).read_text(encoding='utf-8')
    assert result.output == expected_output


def test_view_securities_csv_output(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    xml_file = fixtures_dir / 'kommer.ids.xml'
    golden_file = fixtures_dir / 'expected_view_securities_kommer.csv'

    result = runner.invoke(app, [
        '--file', str(xml_file),
        '--output', 'csv',
        '--no-cache',
        'view', 'securities'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    expected_output = Path(golden_file).read_text(encoding='utf-8')
    assert result.output == expected_output


def test_view_transactions_csv_output(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    xml_file = fixtures_dir / 'kommer.ids.xml'
    golden_file = fixtures_dir / 'expected_view_transactions_kommer.csv'

    result = runner.invoke(app, [
        '--file', str(xml_file),
        '--output', 'csv',
        '--no-cache',
        'view', 'transactions'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    expected_output = Path(golden_file).read_text(encoding='utf-8')
    assert result.output == expected_output


def test_view_accounts_json_output(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    xml_file = fixtures_dir / 'kommer.ids.xml'
    golden_file = fixtures_dir / 'expected_view_accounts_kommer.json'

    result = runner.invoke(app, [
        '--file', str(xml_file),
        '--output', 'json',
        '--no-cache',
        'view', 'accounts'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    actual_rows = json.loads(result.stdout)
    expected_rows = json.loads(Path(golden_file).read_text(encoding='utf-8'))

    assert actual_rows == expected_rows
