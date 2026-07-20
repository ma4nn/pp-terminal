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
# pylint: disable=duplicate-code
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pandas as pd
import pytest
import typer
from _pytest.fixtures import TopRequest
from typer.testing import CliRunner, Result

from pp_terminal.commands.validate import run_validations
from pp_terminal.utils.config import load_config
from pp_terminal.domain.portfolio import Portfolio
from pp_terminal.domain.schemas import AccountType, TransactionType
from pp_terminal.main import app

_AMAZON_SECURITY_ID = '3cfa101b-0558-4ec4-a9e5-baab4eeddb2a'  # US0231351067 in kommer.ids.xml
_EUROPE_ETF_SECURITY_ID = 'c770a389-0a84-442c-ad85-2a58c3066924'  # IE00B1YZSC51 in kommer.ids.xml

# deemed income per share matching the base yields calculated from kommer.ids.xml
_MATCHING_TAX_CSV = """isin;year;deemed_income
US0231351067;2023;1.4994
US0231351067;2024;2.4033779
US0231351067;2025;3.9000962
"""


@pytest.fixture(name='sample_portfolio_with_limits')
def provide_sample_portfolio_with_limits() -> Portfolio:
    """Create a portfolio with deposit accounts for testing."""
    accounts = pd.DataFrame([
        ['Checking Account', AccountType.DEPOSIT.value, None, False, 'EUR'],
        ['Savings Account', AccountType.DEPOSIT.value, None, False, 'EUR'],
        ['Multi-Currency Account', AccountType.DEPOSIT.value, None, False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
       index=['account-1', 'account-2', 'account-3'])
    accounts.index.name = 'accountId'

    transactions = pd.DataFrame([
        [datetime(2025, 1, 1), 'account-1', None, 'DEPOSIT', 1050.0, 0.0, AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
        [datetime(2025, 1, 2), 'account-2', None, 'DEPOSIT', 850.0, 0.0, AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
        [datetime(2025, 1, 3), 'account-3', None, 'DEPOSIT', 800.0, 0.0, AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    return Portfolio(
        accounts=accounts,
        transactions=transactions,
        securities=None,
        prices=None
    )


@pytest.fixture(name='sample_portfolio_with_securities')
def provide_sample_portfolio_with_securities() -> Portfolio:
    """Create a portfolio with one securities account and one security without prices."""
    accounts = pd.DataFrame([
        ['Depot', AccountType.SECURITIES.value, None, False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
       index=['acc-1'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Test Security', 'XXX', 'ISIN123', None, False, 'EUR'],
    ], columns=['name', 'wkn', 'isin', 'note', 'isRetired', 'currency'],
       index=['sec-1'])
    securities.index.name = 'securityId'

    transactions = pd.DataFrame([
        [datetime(2020, 1, 15), 'acc-1', 'sec-1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    return Portfolio(
        accounts=accounts,
        transactions=transactions,
        securities=securities,
        prices=None
    )


def _make_ctx(portfolio: Portfolio, config: dict[str, Any]) -> Mock:
    ctx = Mock()
    ctx.obj = SimpleNamespace(portfolio=portfolio, config=load_config(config))
    return ctx


def _with_account_attribute(portfolio: Portfolio, attr_uuid: str, values: dict[str, Any]) -> Portfolio:
    """Return a new portfolio whose deposit accounts carry the given attribute column."""
    accounts = portfolio.deposit_accounts.copy()
    accounts[attr_uuid] = pd.Series(values)

    return Portfolio(accounts=accounts, transactions=portfolio.deposit_account_transactions)


def _write_paid_tax_config(tmp_path: Path, security_id: str, severity: str = 'error') -> Path:
    csv_file = tmp_path / 'paid_taxes.csv'
    csv_file.write_text(_MATCHING_TAX_CSV, encoding='utf-8')

    config_file = tmp_path / 'config.toml'
    config_file.write_text(
        f'[tax]\nfiles = ["{csv_file}"]\n\n'
        '[commands.validate.securities]\n'
        f'rules = [{{type = "paid-tax-validation", severity = "{severity}", tolerance = 0.001, applies-to = ["{security_id}"]}}]\n',
        encoding='utf-8'
    )

    return config_file


def _invoke_paid_tax_validation(request: TopRequest, config_file: Path) -> Result:
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'kommer.ids.xml'

    return runner.invoke(app, ['--file', str(xml_file), '--config', str(config_file), '--no-cache', 'validate', '--rule', 'paid-tax-validation'])


def test_no_validation_config(sample_portfolio_with_limits: Portfolio) -> None:
    """Test that validation completes successfully when no validation config exists."""
    run_validations(_make_ctx(sample_portfolio_with_limits, {}), rule=None)


def test_balance_limit_pass(sample_portfolio_with_limits: Portfolio) -> None:
    """Test balance limit validation when all accounts pass."""
    ctx = _make_ctx(sample_portfolio_with_limits, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'balance-limit', 'value': 2000.0}
    ]}}}})
    run_validations(ctx, rule=None)


def test_balance_limit_fail(sample_portfolio_with_limits: Portfolio) -> None:
    """Test balance limit validation when account exceeds limit."""
    ctx = _make_ctx(sample_portfolio_with_limits, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'balance-limit', 'value': 1000.0}
    ]}}}})

    with pytest.raises(typer.Exit) as exc_info:
        run_validations(ctx, rule=None)

    assert exc_info.value.exit_code == 1


def test_rule_filter_selects_matching_rule(sample_portfolio_with_limits: Portfolio) -> None:
    """Test that a failing rule still fails when selected via --rule."""
    ctx = _make_ctx(sample_portfolio_with_limits, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'balance-limit', 'value': 1000.0}
    ]}}}})

    with pytest.raises(typer.Exit) as exc_info:
        run_validations(ctx, rule=['balance-limit'])

    assert exc_info.value.exit_code == 1


def test_rule_filter_skips_other_rules(sample_portfolio_with_limits: Portfolio, caplog: Any) -> None:
    """Test that a failing rule is skipped when --rule selects a different type, with a warning."""
    caplog.set_level(logging.WARNING)

    ctx = _make_ctx(sample_portfolio_with_limits, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'balance-limit', 'value': 1000.0}
    ]}}}})
    run_validations(ctx, rule=['price-staleness'])

    assert 'price-staleness' in caplog.text


def test_security_rule_violation(sample_portfolio_with_securities: Portfolio, caplog: Any) -> None:
    """Test that securities validation rules are run and reported with the security name."""
    caplog.set_level(logging.ERROR)

    ctx = _make_ctx(sample_portfolio_with_securities, {'commands': {'validate': {'securities': {'rules': [
        {'type': 'price-staleness', 'value': 90}
    ]}}}})

    with pytest.raises(typer.Exit) as exc_info:
        run_validations(ctx, rule=None)

    assert exc_info.value.exit_code == 1
    assert 'Test Security' in caplog.text
    assert 'no price data' in caplog.text


def test_cli_rejects_unknown_rule(request: TopRequest) -> None:
    """Test that the CLI rejects unknown --rule values before running."""
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'empty.ids.xml'

    result = runner.invoke(app, ['--file', str(xml_file), '--no-cache', 'validate', '--rule', 'no-such-rule'])

    assert result.exit_code == 2
    assert 'no-such-rule' in result.output


def test_cli_rule_option_accepted(request: TopRequest) -> None:
    """Test that a valid --rule value is parsed and the command runs."""
    runner = CliRunner()
    xml_file = request.path.parent.parent / 'fixtures' / 'empty.ids.xml'

    result = runner.invoke(app, ['--file', str(xml_file), '--no-cache', 'validate', '--rule', 'balance-limit'])

    assert result.exit_code == 0


def test_entity_specific_rule(sample_portfolio_with_limits: Portfolio) -> None:
    """Test entity-specific rules with applies-to."""
    ctx = _make_ctx(sample_portfolio_with_limits, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'balance-limit', 'value': 900.0, 'applies-to': ['account-1']}
    ]}}}})

    with pytest.raises(typer.Exit) as exc_info:
        run_validations(ctx, rule=None)

    assert exc_info.value.exit_code == 1


def test_attribute_based_rule(sample_portfolio_with_limits: Portfolio) -> None:
    """Test balance-limit-from-attribute rule type."""
    test_attr_uuid = '11111111-1111-1111-1111-111111111111'
    portfolio = _with_account_attribute(sample_portfolio_with_limits, test_attr_uuid, {'account-1': 1000.0})

    ctx = _make_ctx(portfolio, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'balance-limit-from-attribute', 'value': test_attr_uuid}
    ]}}}})

    with pytest.raises(typer.Exit) as exc_info:
        run_validations(ctx, rule=None)

    assert exc_info.value.exit_code == 1


def test_warning_severity(sample_portfolio_with_limits: Portfolio) -> None:
    """Test that warning severity doesn't raise an error."""
    ctx = _make_ctx(sample_portfolio_with_limits, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'balance-limit', 'value': 1000.0, 'severity': 'warning'}
    ]}}}})
    run_validations(ctx, rule=None)


def test_mixed_severities(sample_portfolio_with_limits: Portfolio) -> None:
    """Test mixed warning and error severities."""
    ctx = _make_ctx(sample_portfolio_with_limits, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'balance-limit', 'value': 900.0, 'severity': 'warning', 'applies-to': ['account-2']},
        {'type': 'balance-limit', 'value': 750.0, 'applies-to': ['account-3']}
    ]}}}})

    with pytest.raises(typer.Exit) as exc_info:
        run_validations(ctx, rule=None)

    assert exc_info.value.exit_code == 1


def test_multiple_errors_logged(sample_portfolio_with_limits: Portfolio, caplog: Any) -> None:
    """Test that all errors are logged before exiting."""
    caplog.set_level(logging.ERROR)

    ctx = _make_ctx(sample_portfolio_with_limits, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'balance-limit', 'value': 800.0}
    ]}}}})

    with pytest.raises(typer.Exit) as exc_info:
        run_validations(ctx, rule=None)

    assert exc_info.value.exit_code == 1
    assert len(caplog.records) == 2
    assert 'account-1' in caplog.text
    assert 'account-2' in caplog.text
    assert '1050' in caplog.text
    assert '850' in caplog.text


def test_date_passed_with_friendly_name(sample_portfolio_with_limits: Portfolio, caplog: Any) -> None:
    """Test that date-passed-from-attribute shows friendly attribute name in message."""
    caplog.set_level(logging.ERROR)

    test_attr_uuid = '22222222-2222-2222-2222-222222222222'
    portfolio = _with_account_attribute(sample_portfolio_with_limits, test_attr_uuid, {'account-1': datetime(2020, 1, 1)})

    ctx = _make_ctx(portfolio, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'date-passed-from-attribute', 'value': test_attr_uuid}
    ]}}}})

    with pytest.raises(typer.Exit) as exc_info:
        run_validations(ctx, rule=None)

    assert exc_info.value.exit_code == 1
    assert len(caplog.records) == 1
    assert 'date attribute has passed 2020-01-01' in caplog.text


def test_cli_paid_tax_validation_within_tolerance(request: TopRequest, tmp_path: Path) -> None:
    """Test that matching recorded taxes from the CSV produce no violation."""
    config_file = _write_paid_tax_config(tmp_path, _AMAZON_SECURITY_ID)

    result = _invoke_paid_tax_validation(request, config_file)

    assert result.exit_code == 0, f"Command failed with: {result.output}"


def test_cli_paid_tax_validation_reports_mismatch(request: TopRequest, tmp_path: Path, caplog: Any) -> None:
    """Test that missing CSV entries for a held security are reported as an error."""
    caplog.set_level(logging.ERROR)
    config_file = _write_paid_tax_config(tmp_path, _EUROPE_ETF_SECURITY_ID)

    result = _invoke_paid_tax_validation(request, config_file)

    assert result.exit_code == 1
    assert 'unexpected/missing paid tax values' in caplog.text
    assert 'iShares Core MSCI Europe' in caplog.text


def test_cli_paid_tax_validation_mismatch_with_warning_severity(request: TopRequest, tmp_path: Path, caplog: Any) -> None:
    """Test that a mismatch with warning severity is reported but doesn't fail the command."""
    caplog.set_level(logging.WARNING)
    config_file = _write_paid_tax_config(tmp_path, _EUROPE_ETF_SECURITY_ID, severity='warning')

    result = _invoke_paid_tax_validation(request, config_file)

    assert result.exit_code == 0, f"Command failed with: {result.output}"
    assert 'unexpected/missing paid tax values' in caplog.text


def _provide_portfolio_with_vap_liability(deposit_amount: float) -> tuple[Portfolio, int]:
    """Create a portfolio holding fund shares with a price gain in the relevant VAP year."""
    now = datetime.now()
    vap_year = now.year if now.month == 12 else now.year - 1

    accounts = pd.DataFrame([
        ['Depot', AccountType.SECURITIES.value, 'giro-1', False, 'EUR'],
        ['Girokonto', AccountType.DEPOSIT.value, None, False, 'EUR'],
    ], columns=['name', 'type', 'referenceAccount', 'isRetired', 'currency'],
       index=['depot-1', 'giro-1'])
    accounts.index.name = 'accountId'

    securities = pd.DataFrame([
        ['Test Fund', 'XXX', 'ISIN123', None, False, 'EUR'],
    ], columns=['name', 'wkn', 'isin', 'note', 'isRetired', 'currency'],
       index=['sec-1'])
    securities.index.name = 'securityId'

    transactions = pd.DataFrame([
        [datetime(vap_year - 1, 6, 15), 'depot-1', 'sec-1', TransactionType.BUY.value, -1000.0, 10.0, AccountType.SECURITIES.value, 'EUR', 0.0, 0.0],
        [datetime(vap_year - 1, 1, 10), 'giro-1', None, TransactionType.DEPOSIT.value, deposit_amount, 0.0, AccountType.DEPOSIT.value, 'EUR', 0.0, 0.0],
    ], columns=['date', 'accountId', 'securityId', 'type', 'amount', 'shares', 'accountType', 'currency', 'taxes', 'fees'])
    transactions = transactions.set_index(['date', 'accountId', 'securityId'])

    prices = pd.DataFrame([
        [datetime(vap_year, 1, 1), 'sec-1', 100.0],
        [datetime(vap_year, 12, 30), 'sec-1', 120.0],
    ], columns=['date', 'securityId', 'price']).set_index(['date', 'securityId'])

    return Portfolio(accounts=accounts, transactions=transactions, securities=securities, prices=prices), vap_year


def test_vap_liquidity_insufficient_balance(caplog: Any) -> None:
    """Test that a deposit account balance below the VAP liability is reported as an error."""
    caplog.set_level(logging.ERROR)
    portfolio, vap_year = _provide_portfolio_with_vap_liability(deposit_amount=1.0)

    ctx = _make_ctx(portfolio, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'vap-liquidity'}
    ]}}}})

    with pytest.raises(typer.Exit) as exc_info:
        run_validations(ctx, rule=['vap-liquidity'])

    assert exc_info.value.exit_code == 1
    assert 'insufficient balance' in caplog.text
    assert f'for {vap_year}' in caplog.text


def test_vap_liquidity_sufficient_balance() -> None:
    """Test that a deposit account balance covering the VAP liability passes."""
    portfolio, _ = _provide_portfolio_with_vap_liability(deposit_amount=10000.0)

    ctx = _make_ctx(portfolio, {'commands': {'validate': {'accounts': {'rules': [
        {'type': 'vap-liquidity'}
    ]}}}})

    run_validations(ctx, rule=['vap-liquidity'])
