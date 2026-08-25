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
from collections.abc import Iterator
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
        '--tax-rate', '26.375'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert rows, "expected at least one lot to be sold"
    assert sum(row['netProceeds'] for row in rows) == pytest.approx(1000.0, abs=1.0)
    assert all('isin' in row for row in rows)
    assert all(row.get('assetClass') for row in rows)  # taxonomy category surfaced on every row


def test_share_sell_preserve_allocation_reports_class_share(request: TopRequest) -> None:
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
    assert rows and all('classShare' in row for row in rows)

    total_gross = sum(row['grossProceeds'] for row in rows)
    class_gross: dict[str, float] = {}
    for row in rows:
        class_gross[row['assetClass']] = class_gross.get(row['assetClass'], 0.0) + row['grossProceeds']

    for row in rows:  # every row carries its own class's share of total gross proceeds
        assert row['classShare'] == pytest.approx(class_gross[row['assetClass']] / total_gross)

    distinct_shares = {row['assetClass']: row['classShare'] for row in rows}
    assert sum(distinct_shares.values()) == pytest.approx(1.0)  # classes partition the whole sale


def test_share_sell_uses_configured_taxonomy_for_preserve_allocation(request: TopRequest, tmp_path: Path) -> None:
    """A target-net run picks up the configured taxonomy, so --preserve-allocation need not be repeated."""
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    config_file = tmp_path / 'config.toml'
    config_file.write_text('taxonomy = "Anlagekategorien"\n', encoding='utf-8')

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--config', str(config_file),
        '--output', 'json',
        '--no-cache',
        'simulate', 'share-sell',
        '--target-net', '1000',
        '--tax-rate', '26.375',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert rows and all(row.get('assetClass') for row in rows)  # allocation preserved via the configured taxonomy
    assert all('classShare' in row for row in rows)


def test_share_sell_configured_taxonomy_ignored_without_target_net(request: TopRequest, tmp_path: Path) -> None:
    """Without a target net the configured taxonomy must not force allocation preservation (plain listing still works)."""
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    config_file = tmp_path / 'config.toml'
    config_file.write_text('taxonomy = "Anlagekategorien"\n', encoding='utf-8')

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--config', str(config_file),
        '--output', 'json',
        '--no-cache',
        'simulate', 'share-sell',
        '--tax-rate', '26.375',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert rows and not any('assetClass' in row for row in rows)  # no preservation, no asset-class enrichment


def test_share_sell_reads_min_amount_from_config(request: TopRequest, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The per-order floor can be configured, matching the behavior of an explicit --min-amount."""
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    config_file = tmp_path / 'config.toml'
    config_file.write_text(
        'taxonomy = "Anlagekategorien"\n'
        '[commands.simulate.share-sell]\n'
        'min-amount = 500\n',
        encoding='utf-8'
    )

    with caplog.at_level(logging.WARNING, logger='pp_terminal.commands.simulate_share_sell'):
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--config', str(config_file),
            '--output', 'json',
            '--no-cache',
            'simulate', 'share-sell',
            '--target-net', '1000',
            '--tax-rate', '26.375',
        ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert sum(row['netProceeds'] for row in rows) == pytest.approx(1000.0, abs=1.0)  # target still met
    assert 'were left unsold' in caplog.text  # configured floor applied, small classes reported


def test_share_sell_configured_min_amount_ignored_without_preserve_allocation(request: TopRequest, tmp_path: Path) -> None:
    """A configured floor without any taxonomy in effect must not force preserve-allocation (no spurious error)."""
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    config_file = tmp_path / 'config.toml'
    config_file.write_text('[commands.simulate.share-sell]\nmin-amount = 500\n', encoding='utf-8')

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--config', str(config_file),
        '--output', 'json',
        '--no-cache',
        'simulate', 'share-sell',
        '--tax-rate', '26.375',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert json.loads(result.output)  # plain listing still works


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
            '--tax-rate', '26.375'
        ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert sum(row['netProceeds'] for row in rows) == pytest.approx(1000.0, abs=1.0)  # target still met
    assert 'were left unsold' in caplog.text  # classes too small for a valid order skipped and reported


def test_share_sell_target_net_is_hit(request: TopRequest) -> None:
    """The min-tax selection sells just enough to land on --target-net (the Sparerpauschbetrag is not applied here)."""
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    def _net(target: str) -> float:
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--config', str(fixtures_dir / 'kommer.toml'),
            '--output', 'json',
            '--no-cache',
            'simulate', 'share-sell',
            '--target-net', target,
            '--tax-rate', '27.375',
        ])
        assert result.exit_code == 0, f"Command failed with: {result.output}"
        return float(sum(r['netProceeds'] for r in json.loads(result.output)))

    assert _net('300') == pytest.approx(300.0, abs=1.0)
    assert _net('10000') == pytest.approx(10000.0, abs=1.0)


def test_share_sell_preserve_allocation_requires_target_net(request: TopRequest, caplog: pytest.LogCaptureFixture) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    with caplog.at_level(logging.CRITICAL):
        result = runner.invoke(app, [
            '--file', str(fixtures_dir / 'kommer.ids.xml'),
            '--config', str(fixtures_dir / 'kommer.toml'),
            '--no-cache',
            'simulate', 'share-sell',
            '--preserve-allocation', 'Anlagekategorien',
            '--tax-rate', '26.375'
        ])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)  # aborted with a message, the traceback needs --verbose
    assert '--preserve-allocation requires --target-net' in caplog.text


def test_pmt_json_output(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--output', 'json',
        '--no-cache',
        'simulate', 'pmt',
        '--return', '5',
        '--end-date', '2056-01-01',
        '--tax-rate', '26.375',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert len(rows) == 1
    row = rows[0]
    assert {'assumedReturn', 'grossPerYear', 'grossRate', 'netPerYear', 'netPerMonth', 'netRate'} <= row.keys()
    assert 0 < row['netPerYear'] <= row['grossPerYear']
    assert row['netPerMonth'] == pytest.approx(row['netPerYear'] / 12)
    assert 0 < row['netRate'] <= row['grossRate']


def test_pmt_multiple_return_rates(request: TopRequest) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--output', 'json',
        '--no-cache',
        'simulate', 'pmt',
        '--return', '2',
        '--return', '5',
        '--end-date', '2056-01-01',
        '--tax-rate', '26.375',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"

    rows = json.loads(result.output)
    assert [row['assumedReturn'] for row in rows] == [2.0, 5.0]
    assert rows[0]['grossPerYear'] < rows[1]['grossPerYear']  # higher assumed return allows a higher withdrawal


def test_pmt_reads_defaults_from_config(request: TopRequest, tmp_path: Path) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    config_file = tmp_path / 'config.toml'
    config_file.write_text('[commands.simulate.pmt]\nreturns = [3, 6]\nend-date = "2051-01-01"\n', encoding='utf-8')

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--config', str(config_file),
        '--output', 'json',
        '--no-cache',
        'simulate', 'pmt',
        '--tax-rate', '26.375',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert [row['assumedReturn'] for row in json.loads(result.output)] == [3.0, 6.0]


def test_pmt_derives_return_from_allocation(request: TopRequest, tmp_path: Path) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    config_file = tmp_path / 'config.toml'
    config_file.write_text(
        'taxonomy = "Anlagekategorien"\n'
        '[commands.simulate.pmt]\n'
        'end-date = "2051-01-01"\n'
        'returns = [{ "Eigenkapital" = 5.0, "Fremdkapital" = 2.0, "Rohstoffe" = 3.0, "Barvermögen" = 0.0 }]\n',
        encoding='utf-8'
    )

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--config', str(config_file),
        '--output', 'json',
        '--no-cache',
        'simulate', 'pmt',
        '--tax-rate', '26.375',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    rows = json.loads(result.output)
    assert len(rows) == 1
    assert 0 < rows[0]['assumedReturn'] < 5.0  # blended from the allocation, diluted by cash and lower-return classes


def test_pmt_mixes_fixed_returns_and_allocation_blend(request: TopRequest, tmp_path: Path) -> None:
    runner = CliRunner()
    fixtures_dir = request.path.parent.parent / 'fixtures'
    config_file = tmp_path / 'config.toml'
    config_file.write_text(
        'taxonomy = "Anlagekategorien"\n'
        '[commands.simulate.pmt]\n'
        'end-date = "2051-01-01"\n'
        'returns = [2, 6, { "Eigenkapital" = 5.0, "Fremdkapital" = 2.0, "Rohstoffe" = 3.0, "Barvermögen" = 0.0 }]\n',
        encoding='utf-8'
    )

    result = runner.invoke(app, [
        '--file', str(fixtures_dir / 'kommer.ids.xml'),
        '--config', str(config_file),
        '--output', 'json',
        '--no-cache',
        'simulate', 'pmt',
        '--tax-rate', '26.375',
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    rows = json.loads(result.output)
    assert [row['assumedReturn'] for row in rows[:2]] == [2.0, 6.0]  # fixed scenarios kept in order
    assert 0 < rows[2]['assumedReturn'] < 5.0  # per-category entry blended into one scenario


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


@pytest.fixture(name='isolated_logging')
def fixture_isolated_logging() -> Iterator[None]:
    """Verbose runs reconfigure the root logger process-wide, which would leak into later tests."""
    root = logging.getLogger()
    level, handlers = root.level, root.handlers[:]
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


@pytest.mark.usefixtures('isolated_logging')
def test_input_files_are_reported_in_verbose_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: TopRequest
) -> None:
    """Neither path was typed by the user here, so both must be traceable from the log."""
    xml_file = request.path.parent.parent / 'fixtures' / 'kommer.ids.xml'
    config_dir = tmp_path / 'pp-terminal'
    config_dir.mkdir()
    (config_dir / 'config.toml').write_text(f'file = "{xml_file}"\n', encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))

    result = CliRunner().invoke(app, ['--no-cache', '--debug', 'view', 'accounts'])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    logged = ''.join(result.output.split())  # rich wraps long paths at terminal width
    assert str(config_dir / 'config.toml') in logged
    assert str(xml_file) in logged


@pytest.mark.usefixtures('isolated_logging')
def test_mistyped_field_aborts_without_a_traceback(request: TopRequest) -> None:
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'kommer.ids.xml'
    args = ['--file', str(xml_file), '--no-cache', 'view', 'securities', '--fields', 'name,nope']

    assert isinstance(runner.invoke(app, args).exception, SystemExit)
    assert isinstance(runner.invoke(app, ['--debug', *args]).exception, InputError)


@pytest.mark.parametrize("flag", ['--verbose', '--debug'])
@pytest.mark.usefixtures('isolated_logging')
def test_verbose_logging_flags_are_synonyms(request: TopRequest, flag: str) -> None:
    """Both let the underlying error surface instead of the bare abort a normal run produces."""
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'invalid.xml'
    args = ['--file', str(xml_file), '--no-cache', 'view', 'accounts']

    assert isinstance(runner.invoke(app, args).exception, SystemExit)
    assert isinstance(runner.invoke(app, [flag, *args]).exception, InputError)


@pytest.mark.parametrize("field", [
    'bef9d57e-0502-44ff-99c7-f26554d1e9a1',  # the attribute uuid, as the error message on a typo suggests
    'IBAN',                                  # its friendly name
    'iban',                                  # names match case-insensitively
])
def test_view_accounts_selects_an_account_attribute_by_uuid_or_name(request: TopRequest, field: str) -> None:
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'account_attribute.ids.xml'

    result = runner.invoke(app, [
        '--file', str(xml_file), '--output', 'csv', '--no-cache', 'view', 'accounts', '--fields', f'name,{field}'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert 'DE00 1234 5678' in result.output


def test_view_accounts_formats_a_percent_attribute_as_percentage(request: TopRequest) -> None:
    """A PercentConverter attribute is stored as a fraction, so the table has to render it as a percentage."""
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'account_attribute.ids.xml'

    result = runner.invoke(app, ['--file', str(xml_file), '--no-cache', 'view', 'accounts', '--fields', 'name,Zinssatz'])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert '3.25%' in result.output
    assert result.output.count('3.25%') == 1, "the total row must not sum up percentages"


def test_requested_column_is_kept_even_when_it_has_no_values(request: TopRequest) -> None:
    """An attribute nobody filled in is still a column the user asked for, so it must not be pruned silently."""
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'account_attribute.ids.xml'

    result = runner.invoke(app, ['--file', str(xml_file), '--no-cache', 'view', 'accounts', '--fields', 'name,Fiktiv'])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert 'Fiktiv' in result.output


def test_columns_from_the_config_file_are_kept_even_when_empty(request: TopRequest, tmp_path: Path) -> None:
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'account_attribute.ids.xml'
    config_file = tmp_path / 'config.toml'
    config_file.write_text(f'file = "{xml_file}"\n\n[commands.view.accounts]\nfields = ["name", "Fiktiv"]\n', encoding='utf-8')

    result = runner.invoke(app, ['--config', str(config_file), '--no-cache', 'view', 'accounts'])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert 'Fiktiv' in result.output


def test_empty_columns_are_still_pruned_from_the_default_view(request: TopRequest) -> None:
    """Pruning keeps the default table readable; only an explicit request overrides it."""
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'account_attribute.ids.xml'

    result = runner.invoke(app, ['--file', str(xml_file), '--no-cache', 'view', 'accounts'])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert 'Fiktiv' not in result.output


def test_view_accounts_currency_column_order_is_stable(request: TopRequest) -> None:
    """Currency columns are derived from the unstacked balance, so their order must not depend on set iteration."""
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'kommer.ids.xml'

    result = runner.invoke(app, [
        '--file', str(xml_file),
        '--output', 'csv',
        '--no-cache',
        'view', 'accounts'
    ])

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert result.output.splitlines()[0] == 'accountId,name,type,EUR,GBP,USD,messages'


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
