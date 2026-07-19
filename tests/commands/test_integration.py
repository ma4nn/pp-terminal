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

import csv
import io
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
        '--exempt-rate', '0',
        '--allowance', '0'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    expected_output = Path(golden_file).read_text(encoding='utf-8')
    assert result.output == expected_output


def test_vap_applies_sparerpauschbetrag(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    def _total_vap(*extra: str) -> float:
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--output', 'csv',
            '--no-cache',
            'simulate', 'vap',
            '--year', '2025',
            '--base-rate', '2.53',
            '--tax-rate', '26.375',
            '--exempt-rate', '0',
            *extra,
        ])
        assert result.exit_code == 0, f"Command failed with: {result.output}"
        rows = list(csv.DictReader(io.StringIO(result.output)))
        cols = [c for c in (rows[0].keys() if rows else []) if c not in ('wkn', 'name', 'currency')]
        return sum(float(r[c]) for r in rows if r.get('name') != 'Related Account Balance'
                   for c in cols if r[c] not in (None, ''))

    raw = _total_vap('--allowance', '0')
    relieved = _total_vap('--allowance', '200')

    assert raw > 0
    assert relieved == pytest.approx(max(0.0, raw - 200 * 26.375 / 100), abs=0.05)  # tax cut by allowance*rate


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
    assert {'shares', 'salePrice', 'grossProceeds', 'capitalGain', 'totalTax', 'netProceeds'} <= plan[0].keys()
    # cost-basis / tax-mechanics columns are left to the per-lot detail view only
    assert not ({'date', 'purchasePrice', 'costBasis', 'fees', 'deemedIncome', 'taxableGain'} & plan[0].keys())
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
        '--tax-rate', '26.375',
        '--allowance', '0'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert rows, "expected at least one lot to be sold"
    assert sum(row['netProceeds'] for row in rows) == pytest.approx(1000.0, abs=1.0)
    assert all('isin' in row for row in rows)
    assert all(row.get('assetClass') for row in rows)  # taxonomy category surfaced on every row


def test_share_sell_preserve_allocation_summary_keeps_asset_class(request: TopRequest) -> None:
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
        '--summary',
        '--tax-rate', '26.375',
        '--allowance', '0',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert rows and all(row.get('assetClass') for row in rows)  # asset class carried into the summary plan
    assert sum(row['netProceeds'] for row in rows) == pytest.approx(1000.0, abs=1.0)  # total still preserved


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
            '--tax-rate', '26.375',
            '--allowance', '0'
        ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert sum(row['netProceeds'] for row in rows) == pytest.approx(1000.0, abs=1.0)  # target still met
    assert 'were left unsold' in caplog.text  # classes too small for a valid order skipped and reported


def test_share_sell_applies_sparerpauschbetrag(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    def _tax_and_net(*extra: str) -> tuple[float, float]:
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--config', str(fixtures_dir / 'kommer.toml'),
            '--output', 'json',
            '--no-cache',
            'simulate', 'share-sell',
            '--tax-rate', '27.375',
            *extra,
        ])
        assert result.exit_code == 0, f"Command failed with: {result.output}"
        rows = json.loads(result.output)
        return sum(r['totalTax'] for r in rows), sum(r['netProceeds'] for r in rows)

    tax_off, net_off = _tax_and_net('--allowance', '0')
    tax_on, net_on = _tax_and_net('--allowance', '1000')

    # the whole portfolio's taxable gain exceeds 1000, so the allowance is fully used: tax drops by 1000 * rate
    assert tax_off - tax_on == pytest.approx(1000 * 27.375 / 100, abs=0.5)
    assert net_on - net_off == pytest.approx(1000 * 27.375 / 100, abs=0.5)


def test_share_sell_target_net_accounts_for_allowance(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    def _run(*extra: str) -> list[dict[str, Any]]:
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--config', str(fixtures_dir / 'kommer.toml'),
            '--output', 'json',
            '--no-cache',
            'simulate', 'share-sell',
            '--target-net', '10000',
            '--tax-rate', '27.375',
            *extra,
        ])
        assert result.exit_code == 0, f"Command failed with: {result.output}"
        rows: list[dict[str, Any]] = json.loads(result.output)
        return rows

    with_allowance = _run('--allowance', '1000')
    without = _run('--allowance', '0')

    # target net is hit either way (the allowance just means selling a little less), and it saves tax
    assert sum(r['netProceeds'] for r in with_allowance) == pytest.approx(10000.0, abs=1.0)
    assert sum(r['netProceeds'] for r in without) == pytest.approx(10000.0, abs=1.0)
    assert sum(r['totalTax'] for r in with_allowance) < sum(r['totalTax'] for r in without)


def test_share_sell_small_target_net_still_hit_when_allowance_exceeds_gain(request: TopRequest) -> None:
    """Regression: for a small --target-net whose taxable gain is below the allowance, the net must still land on target."""
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    def _net(target: str, *extra: str) -> float:
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--config', str(fixtures_dir / 'kommer.toml'),
            '--output', 'json',
            '--no-cache',
            'simulate', 'share-sell',
            '--target-net', target,
            '--tax-rate', '27.375',
            *extra,
        ])
        assert result.exit_code == 0, f"Command failed with: {result.output}"
        return float(sum(r['netProceeds'] for r in json.loads(result.output)))

    # default allowance (1000) dwarfs the realized gain at these targets; before the fix these undershot badly
    assert _net('300') == pytest.approx(300.0, abs=1.0)
    assert _net('1000') == pytest.approx(1000.0, abs=1.0)


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


def test_pmt_json_output(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--output', 'json',
        '--no-cache',
        'simulate', 'pmt',
        '--return', '5',
        '--years', '30',
        '--tax-rate', '26.375',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert len(rows) == 1
    row = rows[0]
    assert {'assumedReturn', 'grossPerYear', 'netPerYear', 'netPerMonth', 'netRate'} <= row.keys()
    assert 0 < row['netPerYear'] <= row['grossPerYear']
    assert row['netPerMonth'] == pytest.approx(row['netPerYear'] / 12)


def test_anonymize_warns_and_keeps_output_clean(request: TopRequest, caplog: pytest.LogCaptureFixture) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    with caplog.at_level(logging.WARNING, logger='pp_terminal.main'):
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--no-cache',
            '--anonymize',
            '--output', 'json',
            'view', 'accounts'
        ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert 'anonymized' in caplog.text  # emitted as a log warning (stderr), not as part of the result
    assert 'anonymized' not in result.stdout
    assert json.loads(result.stdout)  # the warning must not corrupt machine-readable output


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
