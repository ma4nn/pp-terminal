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
import logging
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from _pytest.fixtures import TopRequest

from pp_terminal.exceptions import InputError
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


def test_share_sell_summary_aggregates_per_security(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    def _run(*extra: str) -> list[dict[str, Any]]:
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--config', str(fixtures_dir / 'kommer.toml'),
            '--output', 'json',
            '--no-cache',
            'simulate', 'share-sell',
            '--tax-rate', '26.375',
            *extra,
        ])
        assert result.exit_code == 0, f"Command failed with: {result.output}"
        rows: list[dict[str, Any]] = json.loads(result.output)
        return rows

    lots = _run()
    plan = _run('--summary')

    assert 'account' in lots[0] and 'account' in plan[0]  # depot surfaced in both views
    assert len(plan) == len({(row['isin'], row['account']) for row in lots})  # one plan row per (security, account)
    assert len(plan) <= len(lots)
    assert 'date' not in plan[0]  # per-lot detail dropped
    assert sum(row['netProceeds'] for row in plan) == pytest.approx(sum(row['netProceeds'] for row in lots))
    assert sum(row['totalTax'] for row in plan) == pytest.approx(sum(row['totalTax'] for row in lots))


def test_share_sell_preserve_allocation(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--config', str(fixtures_dir / 'kommer.toml'),
        '--output', 'json',
        '--no-cache',
        'simulate', 'share-sell',
        '--target-net', '1000',
        '--preserve-allocation', 'Anlagekategorien',
        '--tax-rate', '26.375'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert rows, "expected at least one lot to be sold"
    assert sum(row['netProceeds'] for row in rows) == pytest.approx(1000.0, abs=1.0)
    assert all('isin' in row for row in rows)


def test_share_sell_preserve_allocation_min_amount(request: TopRequest, caplog: pytest.LogCaptureFixture) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    with caplog.at_level(logging.WARNING, logger='pp_terminal.commands.simulate_share_sell'):
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--config', str(fixtures_dir / 'kommer.toml'),
            '--output', 'json',
            '--no-cache',
            'simulate', 'share-sell',
            '--target-net', '1000',
            '--preserve-allocation', 'Anlagekategorien',
            '--min-amount', '500',
            '--tax-rate', '26.375'
        ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert sum(row['netProceeds'] for row in rows) == pytest.approx(1000.0, abs=1.0)  # target still met
    assert 'left unsold to avoid dust trades' in caplog.text  # small classes skipped and reported


def test_share_sell_preserve_allocation_requires_target_net(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--config', str(fixtures_dir / 'kommer.toml'),
        '--no-cache',
        'simulate', 'share-sell',
        '--preserve-allocation', 'Anlagekategorien',
        '--tax-rate', '26.375'
    ])

    assert result.exit_code != 0
    assert isinstance(result.exception, InputError)
    assert '--preserve-allocation requires --target-net' in str(result.exception)


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
